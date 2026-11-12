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

# LAF MCP source package

This Python package provides five tools for an existing owner-only LAF Router.
It selects MCP SDK 1.30.0 exactly. Native Linux / Python 3.14.4
stdio and fake-Router checks passed with that SDK. Live client/Router installation
and deployment acceptance remain separate gates. It does not start a Router, download models,
control containers, expose a listener or require a cloud account.

From the LAF repository root, inspect `docs/laf-mcp.md` before configuration.
The operator must provide `LAF_MCP_STATE_DIR` (a new absolute owner-only receipt
directory with an existing parent) and `LAF_MCP_MODELS` (comma-separated exact IDs).
`LOCAL_INFERENCE_SOCKET` optionally selects the accepted private Router socket.
Startup uses the selected host interpreter with the separately installed reviewed
SDK. There is no automatic dependency installation or provider fallback.

Python metadata requires 3.10+; historical cloud source checks used 3.12.
The SDK installation and native adapter checks used 3.14.4, not a measured
Python 3.10 pass.
The Unix transport requires Linux SO_PEERCRED. Windows/macOS and hardware support
are not established. This is adapter packaging, not a full runtime Quick Start.

First-party source: KOL-v1, see LICENSE. Dependencies retain their own licenses,
see THIRD_PARTY_NOTICES.md and dependency-review.json in the source distribution.

## Dependency release gate

The adapter now requires **MCP SDK 1.30.0 exactly**. The previous frozen
candidate's SDK 1.29.0 / Python 3.12 protocol results remain historical evidence;
they do not verify this new SDK selection. The user reported a successful SDK-only
installation on Ubuntu 26.04.1 x86_64 / Python 3.14.4, including `pip check`,
low-level Server/stdio imports, all 29 selected versions and prefix isolation.
That installation alone is not adapter acceptance. Subsequent native checks
passed 86 MCP runtime cases, including actual SDK framing/cancellation/integration,
and a four-case launcher harness: three private-UDS fake-provider cases and
one missing-SDK startup refusal. The full
repository's independent Router/Core/client suite also passed 513 cases. These
are scoped checks, not hardware or live deployment acceptance. See
`dependency-review.json` for evidence scope and `docs/laf-mcp-validation.md` in
the full repository for limitations; `docs/laf-mcp.md` gives test commands.

The official [SDK support policy](https://github.com/modelcontextprotocol/python-sdk/security)
supports only the newest 1.x maintenance release. Recheck support, the complete
platform-specific dependency closure and advisories before future installation
or release. The SDK-only installer selects PyJWT 2.15.1; its archive/review records
remain separate from this adapter source. No SDK or transitive package is bundled.

The [official advisory](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-84m7-p3x7-pcfv)
affects 1.8.0–1.29.1 and 2.0.0–2.1.1 stateful Streamable HTTP; 1.30.0 is the fixed
1.x release. It explicitly excludes stdio-only servers. This is not exhaustive
security clearance or approval for remote hosting.
