# LAF Router

LAF Router is the guarded local inference service for Local AI Fabric (LAF).
Its existing `inference.aceis_service` module path, `aceis-inference.service` unit
and private socket remain compatibility identifiers. See
[naming and compatibility](../local_platform/naming.md). This documentation
does not rename installed units or reconfigure an MCP connector.

This service keeps Strata Flash ready and admits at most one Ollama large model:
Coder Next (48 requested GPU layers) or GPT-OSS 120B (28). It requires separately obtained offline
assets and a pinned Ollama 0.40.15 image. It does not install packages, change
drivers/BIOS, replace the existing Router, or alter AceIS daemon/auth/Tunnel settings.

## Busy-reader summary

Use these commands only after configuring and accepting the service below:

```sh
SOCKET="$XDG_RUNTIME_DIR/aceis-inference/service.sock"
curl --unix-socket "$SOCKET" http://localhost/health
curl --unix-socket "$SOCKET" http://localhost/v1/models
curl --unix-socket "$SOCKET" -H 'Content-Type: application/json' \
  http://localhost/v1/chat/completions -d '{"model":"coder-next","messages":[{"role":"user","content":"Explain integer overflow in C."}],"max_tokens":256}'
systemctl --user stop aceis-inference.service
```

The response includes the job status, content, finish reason and token usage. A
length-capped response is not evidence that the answer is complete or correct.
This API uses the familiar route name but is not a complete OpenAI API emulator.
All requests use a single bounded queue, including Flash. Three-model residency,
parallel Flash/large-model generation, tools, vision, caller-defined options and
remote URLs are intentionally unsupported.

## Machine-specific preparation

This is an existing-assets deployment procedure, not a clean-machine Quick Start.
The example cannot discover or install the model assets. Verify these prerequisites:
Linux user systemd, Docker access, AMD GPU devices, a shared generation lock,
existing **internal** Docker network, pinned image already present, complete GGUF
shards, Strata 0.1.52 HIP executable/pack/source and its working tokenizer Python.
The available disk must cover about 112 GB of Ollama import copies. Never point the
state directory at historical evidence or the source model directory.

1. Create private state/runtime directories with mode 0700. Copy
   `config.example.json` to private state and replace every path/UID with inspected
   local values. Review the `strata_command` example, including `--serve`, against your separately
   obtained Strata build and model assets. Private launch evidence is omitted;
   this example is not a tested default for a new host.
   Config contains no authentication material and should be mode 0600.
2. From this checkout, run the unit/integration tests and monitored model import:

   ```sh
   python3 -m unittest inference.aceis_service.test_service -v
   python3 -B -m inference.aceis_service.server --config /ABSOLUTE/config.json --prepare-models
   ```

   Imports use a new labeled nonprivileged read-only-root container, existing
   internal network, read-only source weights, private writable store, no host
   ports, eight CPUs, 32 GiB RAM and no additional container swap. Import takes
   minutes and retains copies for later restarts. There is no image pull or model
   download. The same RAM/VRAM/swap/PSI guard runs during preparation.
3. Run in the foreground first:

   ```sh
   python3 -B -m inference.aceis_service.server --config /ABSOLUTE/config.json
   ```

   Check `/health`, a short Flash request, Next, 120B, then Next again. Record
   resources and `flash_resident`. Close with SIGTERM and verify GPU reclamation.
4. Replace CHECKOUT/CONFIG in the example unit, install as a **new** user service,
   and run `systemctl --user daemon-reload`, `start`, then `enable` only after
   acceptance. Do not modify existing units. User-service restart is distinct from
   machine reboot: startup after boot requires an active user manager. This work
   does not enable lingering or change login/security settings.

## Safety and model switching

The public endpoint is an owner-only Unix socket, not TCP: private directory 0700,
socket 0600, and Linux peer UID verification. Browser Origins, foreign Host values,
chunked requests, duplicate Content-Length, arbitrary model IDs/URLs/options and
oversized bodies are rejected. No new access credential is created. Docker users
already have host-equivalent privileges; the internal Docker API is not a security
boundary against them. No host port is published.

The configured generation lock is held while the service runs. Coordinate any
other local model workload through that same lock. It never stops unrelated containers or
processes. Label, image and privilege/port identity are checked before control.

Switching unloads the old model, waits for empty `/api/ps` **and** physical VRAM
within 512 MiB of the Flash-only baseline, and available RAM within 2 GiB of that
baseline (never below 8 GiB). Two stable readings are required. If release is
uncertain, the next model is not loaded. RAM and VRAM are separately monitored;
UMA/GTT are not added together as extra physical capacity.

Admission reserves 8 GiB OS available RAM and 2 GiB VRAM. The watchdog also latches
on swap growth or swap I/O above 512 MiB from the service baseline, telemetry
failure, or memory full PSI avg10 above 10%. Only owned providers are stopped. The
latch rejects new jobs until an operator checks the cause and requests recovery.
A 3 GiB absolute GPU-idle ceiling blocks new loads after stop if release cannot be
confirmed. Unrelated GPU activity can therefore make this service unavailable,
but is never killed to restore availability.

Input is bounded to 32 messages/24576 UTF-8 bytes, with a conservative byte-based
8192 context reserve and at most 1024 output tokens. This gate can reject text that
would tokenize smaller. Flash additionally checks actual token count. Queue depth
is 8, retained jobs 128 with 10-minute eviction, HTTP handlers 16. Request deadlines
include queue/load/generation and are at most 300s. Requests/completions live in
memory only, are omitted from application logs, and are cleared when evicted.
The existing model engine's stderr and bounded Docker logs may retain engine
metadata; do not send secrets as test prompts.

## Cancel, recovery and shutdown

```sh
# Submit a cancellable asynchronous job, then use its returned ID.
curl --unix-socket "$SOCKET" -H 'Content-Type: application/json' \
  http://localhost/v1/jobs -d '{"model":"flash","messages":[{"role":"user","content":"Explain cache locality."}],"max_tokens":512}'
curl --unix-socket "$SOCKET" http://localhost/v1/jobs/JOB_ID
curl --unix-socket "$SOCKET" -X DELETE http://localhost/v1/jobs/JOB_ID
# Only after fixing a latched resource/telemetry/provider-stop cause:
curl --unix-socket "$SOCKET" -H 'Content-Type: application/json' \
  http://localhost/v1/recover -d '{}'
systemctl --user restart aceis-inference.service
journalctl --user -u aceis-inference.service --no-pager -n 40
```

Cancellation/deadline closes the owned Ollama connection and stops owned providers
rather than assuming HTTP disconnect stopped GPU work. Flash is restored when
idle; at most two automatic Flash recovery attempts in 10 minutes are allowed.
Recovery does not retry the failed user job or reload a large model. The next
explicit job can load one. Stop/start is bounded by provider stop and physical
GPU release checks. After an uncertain stop, readiness remains false.

For full rollback, stop/disable this new unit and remove only that installed unit
(after saving it if needed), then daemon-reload. Leave existing services and all
source models untouched. Private imported copies are retained; deleting them is
an independent, explicitly authorized cleanup operation.

## NPU boundary

An optional `npu_socket` is read with a 0.5s timeout for independent health status.
A missing/not-ready NPU never blocks GPU jobs. NPU inference belongs to
`inference/aceis_npu_service`; it is not silently retried on CPU by this Router.
Existing AceIS callers must opt into this Unix-socket client boundary. No
production daemon/MCP configuration is rewired by installing this service.

## Evidence and limits

Private dated acceptance records and model-availability bundles are omitted
from this source candidate. No historical hardware result is asserted for this
checkout. Hardware-backed tests, unit tests and independent review are separate
evidence. Host reboot, multi-day
soak, multiple users and large-context performance require additional acceptance.

Follow the local [development rules](../../AGENTS.md). Host changes, license
selection, publication and production deployment remain separate from source
checks. No private orchestration repository is required for the offline suite.

The swap-I/O threshold is cumulative from process startup. If that threshold
latched, `/v1/recover` cannot reset it: investigate and restart this new service to
establish a new baseline only after the host is healthy. The future-container
release checklist is in [distribution notes](distribution-notes.md); nothing is published here.
