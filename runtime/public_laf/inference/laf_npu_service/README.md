<!--
KISS Open License Version 1.0 (KOL-v1)

SPDX-FileCopyrightText: 2026 I-plusplus Inc.
SPDX-License-Identifier: LicenseRef-KOL-v1

Copyright remains with its respective holders; original copyright and attribution notices must be preserved.
Contributors may add copyright notices for their own original modifications.
Distributed modifications must be identified as required by Section 4(c) of KOL-v1.

Use, modification, distribution, and incorporation into commercial or proprietary software are permitted
without license fees or source disclosure, subject to all terms of the complete KOL-v1 license.

Contributors grant the patent permissions stated in KOL-v1. Outputs are not covered merely because a
KOL-licensed tool produced them; embedded licensed material remains subject to applicable license terms.

THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OR LIABILITY TO THE EXTENT PERMITTED BY APPLICABLE LAW.

This header is a summary, not a separate license or an amendment to KOL-v1.
All terms and conditions of KOL-v1 apply. See the accompanying LICENSE file for the complete license text.

Scope: This notice applies only to the project-authored metadata or documentation in this exact file. External material it describes retains its separate terms.
-->

# Optional RyzenAI classification service

## Busy-reader summary

This is a private Unix-socket service with one persistent ONNX Runtime C API session. It is independent of the GPU Router's process and readiness. The pinned SDK quicktest classifier takes **3072 float32 values**, shaped `[1,3,32,32]`, and returns ten logits and their argmax. It is a **plumbing validation classifier**: label meanings, training provenance, accuracy, and application preprocessing have not been established. Do not use its class index for a real-world decision.

The runtime can perform **mixed NPU and CPU** execution. Disabling ONNX Runtime
CPU EP fallback does not disable VitisAI's internal CPU partitions. Inspect the
profile and compiler partitions, then run the hardware probe on your own host.
Private hardware acceptance records are omitted from this source candidate;
no hardware or deployment result is claimed for this checkout.

```bash
python3 -B -m unittest discover -s inference/laf_npu_service/tests -v
```

This regression command needs permission to create Unix-domain sockets. For
software checks that omit the explicitly enumerated socket tests, use the
[portable suite](../../README.md#busy-reader-summary) from the source root.
To build or launch the service, follow [the preprovisioned-host procedure](#launch-on-a-preprovisioned-host),
which defines `SDK` and `PRIVATE_BUILD` and lists the required external assets.
The native parser fixture below uses those same variables and prerequisites.
These checks and launch steps are not a clean-machine Quick Start.

## Local validation commands

The Python regression suite covers shutdown of a TERM-ignoring descendant after its leader exits, deadline handling when the worker never reads stdin, private HTTP boundaries, explicit CPU identity, and rejection of misleading hardware traces.

The production C++ input parser also has a device-free boundary fixture:

```bash
c++ -std=c++17 -O2 -Wall -Wextra -Werror -I"$SDK/cpp-test/include" \
  inference/laf_npu_service/tests/test_parser.cpp \
  -L"$SDK/cpp-test/sdk/onnxruntime/lib" -lonnxruntime -o "$PRIVATE_BUILD/test-parser"
LD_LIBRARY_PATH="$SDK/cpp-test/sdk/onnxruntime/lib" "$PRIVATE_BUILD/test-parser"
```

This fixture checks the smallest positive float32 subnormal and maximum finite float32 through the same decimal parser as the production worker. JSON input is rounded once to float32 before serialization. A boundary fixture is not a claim that extreme-valued model inputs always yield finite logits; nonfinite model outputs deliberately fail closed.

## Contract

- `GET /health`: 200 when the owned worker is alive and the warmup ORT profile proves the requested EP executed. Otherwise 503. It reports `execution_target`, `execution_scope`, `verification_kind`, internal CPU/DPU partition counts, model hash, ORT version, and profile hash.
- `POST /v1/classify`: JSON `{"model":"ryzenai-quicktest-10class","input":[...3072 finite float32 values...]}`. Response adds `logits`, `class_index`, and `latency_ms` for the worker's inference call. No image upload or automatic normalization is provided.
- HTTP 400 invalid input; 403 browser Origin; 413 over 100,000 bytes; 415 wrong content type; 429 another inference is active; 503 owned worker failure or its ten-second deadline. No inference queue, per-request retry, autoload, or implicit CPU fallback; process-level bounded recovery is described below.
- `--provider CPU` is an explicit diagnostic mode. It reports `execution_target=cpu`, `execution_scope=cpu_only`; it is never reported as NPU.
- The socket parent must be owned by the current user and mode 0700; the socket is mode 0600. No TCP listener, new credential, external service, or tunnel is created. A preexisting socket is never deleted on startup.
- At most eight HTTP handler threads, one inference, 5-second connection read timeout. Health remains independent of inference serialization. Inputs and logits are not request-logged.

`execution_verified` specifically means **the warmup ran on the declared ORT execution provider**, not hardware counters, pure-NPU execution, or accuracy validation. The hardware probe below is separate evidence. `execution_scope=mixed_npu_cpu` makes internal CPU work explicit.

## Launch on a preprovisioned host

Obtain and provision these prerequisites separately: Ryzen AI SDK 1.8.0, its ONNX Runtime 1.27.0 C headers/libraries, its matching Peano, local XRT 2.25.37, an accessible `/dev/accel/accel0`, and a compiler. The commands below do not install or validate that stack. Do not substitute another XRT version merely because its SONAME matches.

From the repository root, with no conflicting NPU workload. If adopting this source over an older installation, first inspect the previously named unit and follow [naming and migration](../local_platform/naming.md); a renamed checkout does not stop it. Replace the SDK placeholder with your authorized local SDK path. Stop only your own existing `laf-npu.service`, if installed, before a foreground experiment; do not start two copies:

```bash
SDK="/ABSOLUTE/authorized-ryzen-ai-1.8.0"
PRIVATE_BUILD="$HOME/.cache/laf-npu-build"
install -d -m 700 "$PRIVATE_BUILD"
bash inference/laf_npu_service/build.sh "$SDK" "$PRIVATE_BUILD/worker"
# worker must be a NEW filename. Rebuild with a different filename; existing output is preserved.
RUN_PARENT="$HOME/.cache/laf-npu-runs"
install -d -m 700 "$RUN_PARENT"
RUN="$RUN_PARENT/run-$(date -u +%Y%m%dT%H%M%SZ)-$$"
# RUN must not exist; the service creates it with mode 0700.
python3 -m inference.laf_npu_service.service \
  --worker "$PRIVATE_BUILD/worker" --sdk "$SDK" \
  --xrt "$SDK/xrt-2.25.37/opt/xilinx/xrt" \
  --model "$SDK/cpp-test/samples/quicktest/test_model.onnx" \
  --run-dir "$RUN" --socket "$XDG_RUNTIME_DIR/laf-npu/service.sock"
```

Leave this foreground process in an owned terminal, or use the optional user-unit template after reviewing its paths. Ctrl-C or SIGTERM to **this service PID** stops its worker process group and removes its own socket. Do not use broad `pkill` commands. Startup has one warmup and a 180-second timeout. Startup failure closes the owned worker and exits instead of switching to CPU. A dead worker after startup returns not-ready; the process exits nonzero so an external supervisor can apply a bounded restart policy.

The worker gets a minimal, explicit environment: private HOME, cache, tmp, and working directory; local XRT then SDK ORT then matching Peano library order. It does not modify the retained SDK. `run-dir` must be a fresh directory. Binaries, SDKs, model, raw profiles, compiler caches, and private logs belong outside Git.

Example request:

```bash
python3 - <<'PY' > "$RUN/input.json"
import json
print(json.dumps({"model":"ryzenai-quicktest-10class", "input":[float(i%251)/250 for i in range(3072)]}))
PY
curl --max-time 12 --unix-socket "$XDG_RUNTIME_DIR/laf-npu/service.sock" \
  -H 'Content-Type: application/json' --data-binary "@$RUN/input.json" \
  http://localhost/v1/classify
```

Router integration: configure only its optional NPU socket path. The GPU Router must not load the NPU model, supervise the NPU worker, or make GPU readiness depend on NPU readiness. NPU downtime is local to classification. GPU and NPU share system memory, so any co-load acceptance must record memory headroom and active workloads. The source template itself does not install a unit or change the Router.

## Confirm actual hardware execution

EP registration alone is insufficient. The probe below uses the built worker and
the SDK-relative model/XRT layout shown above. It is a hardware check on an
already provisioned host, not an installation procedure:

```bash
python3 -m inference.laf_npu_service.probe_hardware \
  --worker "$PRIVATE_BUILD/worker" --sdk "$SDK" \
  --output "$HOME/.cache/laf-npu-probe-$(date -u +%Y%m%dT%H%M%SZ)-$$"
```

It needs an already installed `strace`, direct NPU access, and a fresh output directory. It runs once on CPU and once on NPU, with one warmup and one request per mode. Its acceptance threshold is exact equality for this synthetic probe, not a general model tolerance. No retry is performed.

1. Run one warmup and one synthetic inference under `strace -f -yy -e trace=ioctl -o PRIVATE/ioctl.trace`, with a 180-second timeout. Trace only a newly owned worker, not unrelated processes. The trace contains only ioctl metadata for this synthetic test. Do not commit raw traces.
2. Inspect the fresh ORT profile with `profile_evidence`: it must contain executed `VitisAIExecutionProvider` node events and no ORT CPU-provider events. Check compiler partitions separately; CPU work internal to VitisAI does not appear as CPU EP events.
3. Run `python3 -m inference.laf_npu_service.verify_trace PRIVATE/ioctl.trace`. It requires successful `DRM_IOCTL_AMDXDNA_EXEC_CMD` and successful `DRM_IOCTL_SYNCOBJ_TIMELINE_WAIT` on `/dev/accel/accel0`.
4. Compare all ten finite output logits to an explicit CPU-mode run with the same serialized input and model hash. Use an application-justified tolerance for other models/inputs. One matching synthetic sample is not model accuracy validation.

Do not bypass missing device access, relax kernel tracing/security settings, install a different driver, or reset the NPU to make the probe pass. Report the blocked stage instead. An open accelerator file descriptor alone is not execution proof.

## Model identity and verification limits

The service pins SDK `test_model.onnx` to SHA-256
`7a78c7e85bac3a0681e3d2f77e69093a761e1c8f62f7fc65ff5ce3e1b82c5ba3`.
Vendor model/binaries are not included. Model redistribution permission, labels,
training provenance, accuracy and application preprocessing remain unresolved.

Private deployment records, raw traces and historical performance samples are
omitted. Run unit checks separately from hardware acceptance. A passing synthetic
probe would not establish application accuracy, cold-start memory peaks,
container support, concurrent GPU/NPU capacity or long-running reliability.
See [distribution requirements](DISTRIBUTION.md) for runtime and rights gates.

## Optional supervision

The [user-unit template](systemd/laf-npu.service.example) requires replacement
of all placeholder paths and a separately built worker. It is not installed by
this source tree. Its bounded restart policy allows at most two starts in five
minutes; investigate a failure rather than repeatedly resetting the limit.
A manual stop does not request automatic restart. User-manager availability after
boot and physical reboot recovery require separate validation on your host.
