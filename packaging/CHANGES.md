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

# Portable packaging changes (2026-11-12)

New generic owner-config launcher, strict source assembler, portable manifests, source/config tests and installation-scope guide. No imported runtime source modified. Environment allowlist removes account/API credentials; integrated-32 requires six explicit capabilities.

R3 adds public LAF Core/Router/inference/CLI/docs/skills source rather than shipping only the MCP frontend. Preserves upstream orchestrator notices and records snapshot changes separately. No daemon installation, start, hardware provisioning, SDK install, live binding or authentication change. Source-only completeness is not clean-host provisioning acceptance.

R5 archive-only metadata correction: public LAF Git tree 4485a7b records `laf` and `inference/laf_npu_service/build.sh` as 100755. Restore 755 only for those two source paths in the ZIP; all other archived files remain 600. No staged/host file permissions or runtime source bytes are changed.
