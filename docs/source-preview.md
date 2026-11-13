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

<!-- Modified 2026-11-13: link the ordinary-pool source policy without authorizing live adoption. -->

<!-- Modified 2026-11-10: relocate the root README's source-preview, host and release restrictions without changing their scope. -->

# Source preview and release boundary

[Back to Local AI Fabric](../README.md)

This repository contains a source-only LAF preview. It includes no model weights,
SDK binaries, compiled runtime, private host acceptance harness or private
repository history. Its identified first-party source is licensed under
[KISS Open License Version 1.0](../LICENSE); see [the licensing scope](../LICENSING.md)
and [third-party boundaries](../THIRD_PARTY_NOTICES.md). This is not a supported
runtime or image release.

The [offline checks](testing.md) validate software behavior without provisioning
engines or model assets. The [Router guide](../inference/laf_service/README.md)
contains existing-assets operation, not a fresh-host installation procedure.
The [Core guide](laf-core-integration.md) and
[NPU distribution requirements](../inference/laf_npu_service/DISTRIBUTION.md)
retain their additional component-specific restrictions.

## Runtime prerequisites and current limits

The offline test commands are a software-check path, not a runtime Quick Start.
Runtime operation needs separately obtained and verified engines, assets and
host configuration. The legacy backend defaults to a fixed local Ollama image ID;
that value is not a downloadable registry reference. An opt-in
[host provider policy](laf-provider-portability.md) can select another explicitly
installed immutable image/version and bounded resources without editing source;
it does not provision an image or establish compatibility. Flash depends on separately
provisioned Strata executable/source/tokenizer assets. Optional NPU operation
needs a matching vendor SDK, ORT/XRT/compiler stack, pinned model and compatible
host driver/device access. These dependencies are neither installed nor licensed
by this checkout. The Core image builder packages ten named source files only.

Configuration and systemd examples are templates. Replace every placeholder
path, UID, container name and network with inspected local values. JSON does not
expand `~`, `$HOME` or environment variables. A generation lock must refer to the
same existing owner-controlled lock used by other local model workloads. Keep
the LAF ownership labels and ownership checks intact. No example authorizes replacing
an existing service, changing device permissions or exposing a network port.

Default model catalog entries intentionally lack verified acquisition, license,
version, manifest and capacity metadata, so installation is blocked. Synthetic
regression fixtures are not real model provenance. Model management does not
accept legal terms, authenticate to providers or obtain redistribution rights.

The ordinary worker pool has a [selectable local session limit](ordinary-session-pool.md).
Its source candidate selects 8, with generation and memory admission guards.
That configured limit is not live availability, provider entitlement or evidence
of real parallel model execution; adoption of an existing runtime is separate.

## Evidence and release boundary

Historical private host reports remain outside this source draft. Their previous
test counts and binary hashes are not acceptance of this tree. The Python runner
reports the selected tests, failures and unexecuted groups. Preserve failed and
unexecuted stages when reproducing it on another executor.

The Owner approved this bounded first-party source import and KOL v1 licensing.
The top-level LICENSE covers the project's first-party material throughout
this repository and its subdirectories; see [LICENSING.md](../LICENSING.md).
Markdown carries embedded license comments, while JSON examples and fixtures
remain unmodified. External assets retain their separate terms; this approval
does not license or authorize their redistribution. The source review found no obvious
credentials or private host paths in the selected tree, but does not establish
exhaustive security, patent or legal clearance. KOL is a custom license, not a
claim of OSI approval. Preserve applicable notices and identify distributed
changes as required by the full license.

A passing Python suite does not establish hardware support, a complete runtime
release or deployment readiness.

A runtime or image release additionally needs the exact external dependency
inventory/SBOM, redistribution assessment, clean-host behavior and real
runtime/hardware acceptance for its supported configuration. These runtime gates
do not block a separately approved source-only snapshot that contains none of
those external assets. Source publication, registry upload, merge and deployment
are separate actions.

For an initial infra integration, prefer a README reference to the verified LAF
repository and tested immutable release/commit once those exist. Do not make this
source depend on private infra history or its host directories. A commit-pinned
submodule is an optional later integration choice.
