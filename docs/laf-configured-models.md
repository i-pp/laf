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

Modified 2026-11-21 to document the explicit CPU-only Router.
Modified 2026-11-27 to distinguish model catalog, registry, engine, and Router observations.
-->

# Configured models and optional Flash

## Use the selected Router catalog

Normal ask/chat/repair/development jobs can use operator-configured small-model
IDs. Each submission first reads and validates the selected Router's catalog
through the same owner-only socket. No model download, provider option, extra
tool or fallback is added.

With an already accepted Router:

```sh
./laf models
./laf status
./laf
./laf ask --model qwen-coder-small --prompt 'Explain a Python KeyError.'
./laf chat --model qwen-coder-small
```

Use an ID actually returned by `models`, not the illustrative name above. Bare
`laf` and `ask` without `--model` use the Router's declared default. The legacy
Router default remains `gpt-oss-120b`. Older four-field catalogs use that legacy
default only when present; otherwise specify a model. There is no automatic
first-model choice or fallback to a larger/cloud model.

Help and quit remain offline:

```sh
printf '/help\n/quit\n' | ./laf
python3 -B scripts/check-laf.py --suite portable
```

The first ordinary chat message resolves the default; the conversation then
keeps that selected ID, including after `/reset`. Later submissions recheck
membership, so a changed default cannot silently send history to another model.
If selection or discovery fails, no job is submitted. Repair and developer
commands retain their previous legacy defaults; use their existing `--model`
option for a configured ID. File capabilities, read-only tests, fixed execution
profiles and bounded attempts/deadlines remain unchanged.

## Find a model without confusing catalog and runtime state

From the LAF checkout, use the command that answers the question you have:

| Question | Command | What it tells you |
| --- | --- | --- |
| Which recent models does a supported publisher list? | `./laf model discover --publisher Qwen` | A bounded set of public publisher metadata, newest first (up to 12 by default). Use `--search` for a named model. Discovery alone does not establish a supported download. |
| Which entries in my reviewed catalog are eligible to install? | `./laf model --catalog "$CATALOG" installable` | Reviewed Ollama entries with complete metadata, including an expected digest and a storage estimate. Does not query an engine, measure size, verify local files, or download. |
| What have I recorded locally? | `./laf model --config "$CONFIG" registry` | Versioned records from the selected owner configuration, including imported and managed assets. Does not imply that an asset is still present or usable. |
| What is installed in this local engine? | `./laf model --config "$CONFIG" inventory` | The explicitly configured engine's current tags and manifest digests, not the Router's model IDs. |
| What has this engine loaded for a catalog entry? | `./laf model --config "$CONFIG" --catalog "$CATALOG" inspect "$MODEL_ID"` | Whether the exact catalog reference is installed or loaded. `digest_matches` compares the installed engine digest with the catalog digest. The separately reported loaded digest may describe a different manifest level and is not checked against the catalog. |
| What can the selected Router accept? | `./laf --socket "$SOCKET" models` | Model IDs advertised by that Router now. This is not a list of installed files or of loaded engine models. |

In these examples, use an existing owner configuration, private reviewed
catalog, catalog model ID, or Router socket as appropriate. To check public
metadata for known Ollama tag mappings, use `./laf model artifacts`. Its
reported digest is from the current registry response, not a historical
approval or a guarantee of compatibility with your Router. The built-in owner
catalog contains setup placeholders: `./laf model installable` can legitimately
return an empty list even when `./laf models` lists usable Router IDs.

A result of `not_queried` means unknown, not absent. Keep the three questions
separate: a recorded model can be missing from the engine, an installed
model may not be selectable through this Router, and a selectable model
need not be loaded. Use an independently verified profile mapping rather
than joining a Router ID and an engine tag by spelling.

## Explicit Core configuration

This is an operator configuration contract, not a clean-host Quick Start. Use
the existing reviewed Core/host-supervisor boundary; do not modify or restart an
active service merely to try it.

Normal mode requires `state_dir`, `runtime_dir`, `lifecycle_runtime_dir`,
`ollama_profiles`, `flash_enabled` and `default_model`. Retain owner-only
regular-file mode 0600 and absolute container-native paths. Optional `npu_socket`
keeps the independent NPU lane.

Illustrative Flash-free configuration. The digest placeholder deliberately fails
validation; replace it with an independently verified installed Ollama manifest:

```json
{
  "state_dir": "/state",
  "runtime_dir": "/runtime",
  "lifecycle_runtime_dir": "/lifecycle",
  "flash_enabled": false,
  "default_model": "qwen-coder-small",
  "ollama_profiles": [
    {
      "id": "qwen-coder-small",
      "reference": "qwen2.5-coder:1.5b-instruct-q4_K_M",
      "digest": "sha256:REPLACE_WITH_64_VERIFIED_LOWERCASE_HEX_DIGITS",
      "options": {
        "num_ctx": 8192,
        "num_thread": 2,
        "num_batch": 32,
        "num_gpu": 32,
        "temperature": 0,
        "seed": 42
      }
    }
  ]
}
```

The option numbers demonstrate the schema, not measured tuning or an 8 GB fit.
This digest is not necessarily a weight-file hash. Acquisition source,
license/authentication steps, storage mapping and image compatibility need
separate verification. Selection does not install or adopt a model into the
persistent model-manager registry.

Each profile contains exactly `id`, `reference`, `digest` and `options`. Select
1..8 distinct ASCII IDs and distinct exact tagged local references. IDs begin
with a lowercase letter and use only lowercase letters, digits and hyphens, at
most 64 characters. Do not reuse `flash`, `coder-next`, `gpt-oss-120b` or the
isolated `test-*` namespace. The explicit default must belong to the catalog.

All option keys shown are required. Context remains 8192. Integer ranges are
threads 1..32, batch 1..512, GPU layers 0..256 and seed 0..2147483647.
Temperature is finite in 0..2; booleans are not numbers. Streaming and thinking
remain disabled for these Ollama profiles. Keep-alive is fixed; only bounded
public `max_tokens` supplies output length.

With `flash_enabled:false`, omit Strata fields: no Flash process is started or
recovered. Core uses the authenticated empty-engine physical baseline instead.
With `flash_enabled:true`, also provide all three existing `strata_source`,
`tokenizer_python` and `strata_command` fields. Flash becomes a resident catalog
member and may be its default; small profiles still use their configured options.

Configurations without either normal or acceptance fields preserve the original
Flash plus Coder Next/120B behavior. The separate
[acceptance-only catalog](laf-candidate-profiles.md) keeps `test-*` IDs and no
implicit default. Do not combine acceptance and normal configuration fields.

## Identity and failure behavior

Before normal generation, every configured Ollama reference must have its pinned
manifest in `/api/tags`. Before returning an Ollama result, `/api/ps` must report exactly
the requested reference and matching manifest. Missing, duplicate, malformed or
changed identity fails closed and clears readiness. Idle/unload evidence requires
an actual model list, not a falsey malformed value. Cancellation and the provider
generation fence are rechecked before chat. The Controller's existing error path
invokes the physical provider stop barrier.

These comparisons validate observed engine identity, not a full weight-file
rehash, signature, legal clearance or independent runtime attestation. Existing
installed-image verification and real runtime acceptance remain necessary.

Discovery failure or interruption is a known no-submit outcome. A failure after
POST can still be uncertain: retain any received job ID and reconcile rather
than replay. `get` validates job ID/model against the catalog. Cancellation keeps
its existing one-request/no-stop-confirmation semantics.

The 8192 context budget, output/deadline bounds, single GPU lane, at least 8 GiB
available system RAM, 2 GiB free VRAM, swap/PSI limits, ownership checks and
physical unload/stop barriers remain. Disabling Flash does not remove Linux/AMD
telemetry/device prerequisites or establish model fit. See
[provider portability and smaller-VRAM acceptance](laf-provider-portability.md)
for the remaining measured-hardware gates and independent AI test-quality work.

Software tests use synthetic providers. No new deployment, actual small-model
generation, 8-16 GB GPU or clean-host acceptance is claimed.

## Explicit CPU-only Router

On a Linux host with operator-authorized local Docker access, the separate CPU
entry point serves one preinstalled Ollama profile using the ordinary
owner-only Router socket. Prepare the model store through
[model setup](laf-model-management.md). Use a dedicated store containing one
exact tag, a locally installed digest-pinned Ollama image, and a new owner-only
runtime directory. Keep the existing owner-only `generation.lock` in a private
model state directory outside the model store. Configure model setup and the
CPU Router to use that same lock.
Keep the store outside directories replaced by plugin updates.

Save this example with mode 0600 under a directory owned by your user with mode
0700. Set the three paths and both digest placeholders to verified values.
The profile accepts `num_gpu: 0` and a thread count within the provider's CPU
limit.

```json
{
  "mode": "cpu-only",
  "runtime_dir": "/PRIVATE_RUNTIME",
  "model_store": "/PRIVATE_MODEL_STORE",
  "generation_lock": "/PRIVATE_MODEL_STATE/generation.lock",
  "ollama_provider": {
    "image": "ollama/ollama@sha256:IMAGE_REPOSITORY_DIGEST",
    "version": "0.40.15",
    "memory_bytes": 1073741824,
    "cpus": 1,
    "pids_limit": 96
  },
  "ollama_profile": {
    "id": "smollm2-135m",
    "reference": "smollm2:135m-instruct-q4_K_M",
    "digest": "sha256:OLLAMA_MANIFEST_DIGEST",
    "options": {
      "num_ctx": 8192,
      "num_thread": 1,
      "num_batch": 32,
      "num_gpu": 0,
      "temperature": 0,
      "seed": 42
    }
  }
}
```

Run `python3 -B -m inference.laf_service.cpu_server --config /PRIVATE_CONFIG/router.json`
from an extracted source tree. The runtime directory must be short enough for
its `service.sock` Unix socket. Bind the plugin to that socket and the selected
profile ID with the existing [`configure-router` command](bootstrap.md#bind-existing-backends-before-starting-a-frontend).

The provider runs in its own loopback-only container without host GPU devices
or published ports. The model store is mounted read-only and image pulling is
disabled. Cold startup checks the 8 GiB host RAM reserve plus the configured
container memory limit. The running Router monitors RAM, swap growth and I/O,
and memory pressure. Docker enforces the configured CPU, RAM and process limits
with no container swap allowance. Completion requires the pinned manifest and
zero VRAM reported for the loaded model. The one-job lane reuses the existing
deadlines and cancellation handling. A normal shutdown stops and removes the
verified owned CPU provider. Unknown ownership or stop status blocks cleanup.
