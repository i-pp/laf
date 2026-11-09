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

# Licensing

The complete KISS Open License Version 1.0 is in [LICENSE](LICENSE).
The custom identifier `LicenseRef-KOL-v1` maps to the identical text in
[LICENSES/LicenseRef-KOL-v1.txt](LICENSES/LicenseRef-KOL-v1.txt). It is not an
SPDX License List entry or a claim of OSI approval.

## First-party scope

The top-level LICENSE applies to project-authored source, configuration, tests,
fixtures and documentation at the repository root and throughout its
subdirectories, unless a file states different terms. This first-party
material is attributed to I-plusplus Inc. A separate notice in every file is
not required to bring that material within this repository-wide scope.
The Owner approved this source-only initial import and license application.
A root LICENSE does not change the terms of external assets, separately licensed
material or future contributions. Each contributor must have authority for the
material they offer.

The initial import identifies 84 reviewed component/source files and three new
project licensing/change documents. Files with comment syntax carry the approved
full source-header summary, with HTML comments used for Markdown. JSON files
include configuration and task examples, sample case data and a synthetic
provenance test fixture. They retain valid JSON without comments, added license
fields or individual sidecar notices; the first-party scope above still applies.
NOTICE carries its own SPDX attribution. The two copies of the license
instrument are reproduced unchanged.

## External material

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for what is referenced but
not bundled. No vendor SDK, runtime, firmware, model, tokenizer payload or base
image becomes KOL-licensed through these source notices. A JSON field describing
an external model's license describes that model, not this metadata wrapper.
The [NPU assessment](inference/aceis_npu_service/DISTRIBUTION.md) preserves the
open SDK, runtime and model redistribution questions. Source licensing does not
resolve them or authorize publication of a runtime image.

Keep original copyright, attribution and license notices with any third-party
material later included, and comply with its full terms. Never apply a first-party
KOL notice over an upstream license or assume interface use transfers ownership.

## Notices and changes

Preserve [NOTICE](NOTICE) where applicable, as required by section 4(d).
Identify distributed changes under section 4(c), for example in a change record
that identifies affected files. [CHANGES.md](CHANGES.md) records this initial
source-only adaptation. Retain interpreter shebangs first and use valid comment
syntax. Do not create `.license` sidecars or insert comments into strict JSON.
The repository-wide first-party scope above covers commentless formats.

Credit in an About screen or manual is optional. Using a KOL-licensed tool does
not alone license its output or require attribution for that output; copied or
embedded material retains its applicable terms. The full LICENSE controls over
any summary. This source release makes no claim of legal enforceability,
patent clearance, third-party license compatibility, runtime support or hardware
certification.
