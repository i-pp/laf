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

# Local AI Fabric container distribution (planned, not published)

Local AI Fabric (LAF) is the agreed name for the independent local-LLM-plus-NPU
development platform intended for future free Docker Hub distribution. The
agreed service and MCP display names are LAF Router and LAF MCP; see
[naming and compatibility](../local_platform/naming.md). This is not publication
of AceIS itself. The current
AceIS implementation continues separately. This change neither
publishes an image nor grants release approval. Separate GPU and NPU services
remain the intended boundary, with a private Router client contract and no public
API by default. The user-service template requires a preprovisioned machine; it is not a portable
container release. Strata, Ollama, SDKs and models must be obtained separately.

Before publication:

- Check licenses and redistribution rights for Strata binaries/source, tokenizer,
  Flash pack/GGUF files, Ollama image and model manifests. Keep multi-GB weights
  out of images unless redistribution is explicitly permitted and intended.
- Pin base images and all source/dependency versions/digests, record build inputs,
  build reproducibly, produce a software bill of materials and scan it. The
  current pinned existing Ollama digest is not a new redistributed image.
- Supply minimal GPU device mappings and verified groups without privileged mode,
  host networking, Docker socket mounts or security disabling. Router must not
  receive a host Docker socket in a distributable container by default; replace
  host lifecycle control with a narrow supervisor boundary.
- NPU needs independent XRT/firmware/ORT EP and hardware compatibility checks.
  Clearly expose actual NPU versus mixed CPU/NPU work, with no silent CPU fallback.
  Do not assume hardware pass-through, driver ABI or licenses match GPU images.
- Separate model/cache/state volumes, offline operation, owner/UID mapping,
  health, readiness, resource budgets, shutdown/restart and optional service
  dependencies. No credential or machine-specific path belongs in an image.
- Reproduce on another clean host and document a complete installation path,
  supported hardware, exact pull/run examples, opt-in network endpoints and
  recovery. Current local paths/SDKs are not such a reproducibility claim.
- Confirm repository/registry visibility, image name, account, content and
  publication authority before any Docker Hub push. PR merge is separate.
