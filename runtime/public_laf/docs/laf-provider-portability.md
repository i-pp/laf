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

# Explicit host provider policy and smaller-VRAM acceptance

## What changes now

A host configuration can select an already-installed immutable Ollama image and
explicit bounded resources without editing Python source. The legacy defaults
remain the same image ID, version 0.40.15, 32 GiB RAM cap, eight CPUs and 512 PIDs.
This opt-in policy removes the compiled-in image/resource choice. It is not a
clean-host installer or an assertion that a different image is API-compatible.

Run the new deterministic tests and the aggregate portable checks:

```sh
python3 -B -m unittest inference.laf_service.test_provider_policy -v
python3 -B scripts/check-laf.py --suite portable
```

The tests use injected Docker responses. They neither contact a Docker daemon
nor load a model. The full group additionally needs a socket-capable executor;
portable results explicitly leave the 39 private-socket tests unexecuted.

## Host-only configuration

Add `ollama_provider` inside the supervisor's existing `ollama` object. The
legacy host backend can also receive this key in its trusted host configuration.
It is not accepted as a Core/public request field or a lifecycle wire parameter.

Illustrative fragment only. The placeholder image deliberately fails validation:

```json
{
  "ollama_provider": {
    "image": "ollama/ollama@sha256:REPLACE_WITH_VERIFIED_REPOSITORY_DIGEST",
    "version": "0.40.15",
    "memory_bytes": 8589934592,
    "cpus": 2,
    "pids_limit": 128
  }
}
```

All five fields are required; extra keys are rejected. `image` is either a full
`sha256:` local image ID or a repository reference pinned with `@sha256:`. A
mutable tag alone is rejected. `version` is the exact numeric three-part version
expected from the engine, not an inferred version from a tag. Use the separately
reviewed image's actual version rather than copying this example.

`memory_bytes` is the **container RAM cap**, not GPU memory allocation or an
assertion of model fit. It must be an integer between 1 and 128 GiB. The same cap
is applied to memory plus swap, so no extra swap is allowed. CPUs must be an
integer in 1..32 and PIDs in 64..4096. Booleans are rejected. These bounds constrain
configuration; their endpoints are not recommended operating profiles.

For an explicit override, LAF performs only local `docker image inspect` before
any provider mutation. It requires exactly one Linux image, an exact content ID,
the exact repository digest when that form was configured, and no image-declared
anonymous volumes. It never pulls, builds, chooses a tag, silently substitutes
the legacy image, or adopts a mismatching container. Record the image's source,
platform, digest, license and expected API compatibility separately.

Creation uses the resolved content ID and the existing `--pull=never`, read-only
root, private network/IPC/PID namespace, fixed mounts/devices, non-root UID/GID,
cap-drop ALL and no-new-privileges. The stopped container is inspected before
start. CPU/PID/RAM limits, lack of added capabilities/device requests and the
two exact AMD device mappings are checked along with existing ownership,
network and mount checks. A mismatch refuses start or stop rather than weakening
the policy. A refused new container may remain stopped for operator inspection;
LAF does not automatically delete it.

An installed image with the requested bytes still needs real startup, inference,
cancellation, unload, fault and shutdown acceptance. Matching a version string
alone is not proof of API compatibility. Model acquisition, reviewed licenses,
model manifest identities and persistent storage remain separate prerequisites.

## 8-16 GB VRAM: the next bounded path

Use the existing
[Ollama-only Core acceptance catalog](laf-candidate-profiles.md) first. It already
omits Flash without disabling host telemetry, stop barriers or ownership checks.
It retains named `test-*` smoke profiles. The
[normal configured-model path](laf-configured-models.md) supports a pinned
operator catalog, an explicit default and optional Flash through ordinary CLI
jobs. Installing a model alone still does not configure selection: the Core
profile and client catalog/job validation must agree.

Candidate sequence, not a hardware-fit claim:

1. Retain the existing 0.5B/135M lifecycle smoke profiles for empty-state,
   switching and release tests.
2. Inspect exact provenance for
   [Qwen2.5-Coder 1.5B Q4_K_M](https://ollama.com/library/qwen2.5-coder:1.5b-instruct-q4_K_M),
   then evaluate it as the first coding/test-authoring candidate. The registry
   page lists 986 MB and Apache 2.0; verify the complete installed artifact and
   applicable terms before acquisition or distribution.
3. Evaluate
   [Qwen2.5-Coder 7B Q4_K_M](https://ollama.com/library/qwen2.5-coder:7b-instruct-q4_K_M)
   only after measured headroom permits it. Its listed 4.7 GB size is a download
   artifact size, not peak VRAM use. Do not infer that the whole model family
   shares one license: the
   [3B tagged page](https://ollama.com/library/qwen2.5-coder:3b-instruct-q4_K_M)
   displays different research terms.

The physical guards still require at least 8 GiB **available system RAM** and
2 GiB free VRAM, plus unchanged swap-growth/I/O and PSI bounds. A nominal 8 GB
GPU therefore does not provide an 8 GB model budget. Measure other GPU usage,
weights, context/KV allocations and peak transient use during cold load and
generation. The current context contract remains 8192. Reducing it needs matching
request validation and capabilities, not only a provider option change.

This increment retains Linux `/proc`/PSI, exactly one AMD-style DRM telemetry
source, `/dev/kfd` and `/dev/dri/renderD128`. It does not add NVIDIA, macOS,
multiple-GPU or GPU-less support. `num_gpu=0` does not remove these prerequisites.

## Acceptance without disturbing the existing runtime

Use only a separately authorized stopped test deployment, isolated private
state/model/runtime paths and a distinct owned container. Do not adopt existing
production container names, stores or sockets. Keep experiment-local state and
supervisor locks separate, but use the approved shared generation lock when the
GPU is shared with other model workloads. Never create a second generation-lock
domain to bypass a held lock. If the existing owner holds that lock, defer the
experiment until a coordinated window rather than modifying the active service.
Shared GPU capacity must be coordinated before any load or stop-injection test.
Nothing in this document starts or changes an installed service.

Record the hardware/driver and telemetry identity, image ID/digest/version,
model manifests, persistent-store mapping and exact source/configuration.
Test cold startup and A-to-B-to-A generation, peak resources, exact loaded model,
physical release, cancellation/deadlines, fault cleanup, restart and persistence.
Use the existing authenticated host stop boundary; do not replace physical
evidence with empty `/api/ps` alone or bypass a safety latch.

For the primary AI test-quality metric, freeze specifications first. Generate
test drafts in a separate evaluation artifact, independently verify their
expected values, then grade them against a correct implementation and hidden
non-equivalent defects in the bounded runner. Record nonzero test execution,
false positives, defect detection, bypasses/skips, initial attempts and retries.
Keep existing development-workspace tests read-only. A successful repair against
those tests does not measure the model's ability to author correct strict tests.
Measure speed only after correctness and defect detection; it is secondary.

No actual image, model, 8-16 GB GPU, clean-host deployment or runtime failure
acceptance has been performed for this policy change. Software mock results do
not establish hardware support, certification or safety-critical readiness.
