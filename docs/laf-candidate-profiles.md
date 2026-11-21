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

# Explicit Ollama-only Core acceptance candidate

## Busy-reader summary

This isolated candidate can exercise named small Ollama models through the actual
Core entrypoint without starting Flash or borrowing a production profile name.
The existing default remains Flash plus `coder-next` and `gpt-oss-120b`.
Nothing here selects or changes an installed service, downloads a model, or gives
Core Docker access.

Run the focused checks from this candidate directory:

```sh
LAF_TEST_LIVE_UDS=1 python3 -B -m unittest -v \
  inference.laf_service.test_acceptance_profiles
LAF_TEST_LIVE_UDS=1 python3 -B -m unittest -v \
  inference.laf_service.test_lifecycle \
  inference.laf_service.test_lifecycle_uds \
  inference.laf_service.test_lifecycle_uds_live \
  inference.laf_service.test_host_supervisor \
  inference.laf_service.test_core_integration \
  inference.laf_service.test_service \
  inference.laf_service.test_acceptance_profiles \
  inference.local_platform.test_models
```

These are offline fake-provider checks, including real private Unix sockets and
owned fake Flash subprocesses in the inherited regression suite. They are not
actual model inference or a complete runtime Quick Start. Container build,
curated download, actual multi-model inference, replacement and persistence
checks require a separately reviewed host acceptance procedure; no such driver or private evidence is included here.

## One explicit opt-in configuration

An owner-only mode-0600 Core configuration may instead contain exactly
`state_dir`, `runtime_dir`, `lifecycle_runtime_dir`, `acceptance_ollama_profiles`,
and optionally `npu_socket`. All paths retain the container-native absolute-path
rule. Strata fields are rejected in this mode, which removes ambiguity about
whether Flash should start. When neither acceptance nor normal configured-model
fields are present, the original required fields and Flash behavior are unchanged.

Each acceptance profile has exactly `id`, `reference`, and `options`. The example
below shows the schema, not verified model installation or deployment settings:

```json
{
  "state_dir": "/state",
  "runtime_dir": "/runtime",
  "lifecycle_runtime_dir": "/lifecycle",
  "acceptance_ollama_profiles": [
    {
      "id": "test-qwen25-05b",
      "reference": "qwen2.5:0.5b-instruct-q4_K_M",
      "options": {
        "num_ctx": 8192,
        "num_thread": 2,
        "num_batch": 32,
        "num_gpu": 32,
        "temperature": 0,
        "seed": 42
      }
    },
    {
      "id": "test-smollm2-135m",
      "reference": "smollm2:135m-instruct-q4_K_M",
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

The catalog permits 1..8 unique `test-` IDs, lowercase letters/digits/hyphens,
and distinct exact tagged references. References cannot contain URLs, ports,
paths outside a single optional model namespace, whitespace, or arbitrary
commands. The tag may use uppercase characters because actual Ollama
quantization tags do. No production ID can be reused.

All option keys are required. `num_ctx` must remain 8192 to match the unchanged
public context-budget validator. Integer bounds are `num_thread` 1..32,
`num_batch` 1..512, `num_gpu` 0..256, and `seed` 0..2147483647. Temperature is a
finite number in 0..2; booleans are not accepted as numbers. Thinking remains
false, streaming false and keep-alive fixed at -1. Only the existing bounded
`max_tokens` request supplies `num_predict`.

Public job requests still allow only `model`, `messages`, `max_tokens` and
`timeout_seconds`. `/v1/models`, `/v1/capabilities` and Controller validation
use the selected instance catalog. The normal CLI now validates this catalog
before submission, so an explicitly selected `test-*` ID can also be used by a
client. This acceptance mode declares no default; specify its ID. Normal
operator profiles with pinned manifests and optional Flash use
[the configured-model contract](laf-configured-models.md).

## Provider readiness and release

Startup still requires the mapped owner UID, a separate PID namespace, the
host's fixed emergency-stop capability, and an authenticated physical stop
barrier. It then starts the fixed host-owned Ollama provider and verifies that
the exact configured references appear in `/api/tags`.

Before accepting requests, Ollama-only mode requires empty `/api/ps`, physical
VRAM below 3 GiB and available RAM at least 8 GiB twice. The resulting host
observations form its empty-engine baseline, using the lower VRAM and higher RAM
values. The baseline is invalidated by any abort and recaptured before a later
load. A concurrent provider stop invalidates baseline publication.

Switching first unloads the exact configured old reference. It requires empty
`/api/ps`, VRAM within 512 MiB of that baseline and below 3 GiB, and available RAM
within 2 GiB of baseline and never below 8 GiB, twice. Missing or unconfirmed
physical release prevents the next model load. After generation, the sole
loaded reference must be the requested configured reference. Default Flash
switching keeps its existing Flash baseline and options.

Candidate readiness does not require Flash. It requires verified startup
catalog/baseline state, no local stop/close, and a fresh authenticated host status
confirming the same host epoch, running provider and no stop or safety fault.
This is provider readiness, not a token-generation probe. No independent
network health request can overwrite the active cancellable inference
connection. Normal abort and close still use the host's physical release stop
barrier; there is no fallback to local telemetry or host PID signaling.

## Boundaries and verification limits

The Core image's existing ten-file source allowlist is unchanged. There are no
new dependencies, lifecycle operations, public listeners, Docker commands,
mount authorities or host socket authorities in Core. The copied v2 local model
manager is unchanged and is not included in the Core image. Its installation
catalog, manifest digest checks and persistent registry are independent of this
Core selection catalog.

Historical host acceptance drivers and their source/evidence manifests are not
included in this standalone source draft. Run the current explicit suite from
[the root README](../README.md); a Python test pass does not establish real model
provenance, runtime acceptance or P0 completion.
