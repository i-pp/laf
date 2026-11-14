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

# Bundled bootstrap policy provenance

The NEW-prefix schema2 selector uses the public `i-pp/ai-orchestration` snapshot
at commit `ddcaf63e50bed722d558d418deeef791f3029a6f`:

https://github.com/i-pp/ai-orchestration/tree/ddcaf63e50bed722d558d418deeef791f3029a6f

The unmodified files are `docs/ai/orchestration.md`, `docs/ai/policies.md`,
`agents/README.md`, `LICENSE` and `NOTICE`. Their original bytes and notices are
retained under `runtime/private_codex/policy-snapshot`. That revision's NOTICE
expressly licenses project-authored code and documentation under KOL-v1; its
complete LICENSE is included. External products and quoted material retain
their own terms. No rights in Codex, its SDK or model implementations are claimed.

`manifest.json` records the original document SHA256 and Git blob identities,
licensed source revision, and exact aggregate policy identity. These are
purposeful policy pins, not an execution/billing attestation. The loader checks
the pinned bytes and separate license/notice identities without Git, network,
a working checkout, or historical text translation. Provenance explicitly says
`source_proof=bundled_snapshot` and `git_checkout_attested=false`.

The old schema1 policy/legacy heading and `aeef6db...` Git-source path remain
unchanged. Their missing historical files are not synthesized, relabeled or
claimed to have the newer license. Schema2 is selected only by explicit NEW
bootstrap configuration. It injects the licensed source text verbatim, together
with the existing English permissions/priority preamble. Policy does not grant
execution authority or override operator, account, cost or safety constraints.

The bootstrap CLI, explicit unconfigured-backend state, schema2 launcher path,
tests and this documentation are new LAF work. Existing adapter/policy files are
modified to support this explicit setup while retaining their original notices.
Private runtime state, credentials, native/SDK binaries, model data and test logs
are not part of the public source distribution.
