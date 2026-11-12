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

# MCP dependency boundary

The adapter is independently authored first-party LAF source under KOL-v1.
The external MCP Python SDK 1.30.0 is MIT licensed. Its unchanged MIT license is
included at `licenses/MCP-SDK-LICENSE.txt` for reference. The SDK and its dependencies
are installed separately and are not bundled or relicensed by this source package.

`dependency-review.json` records official SDK artifact hashes, release-specific
Python metadata, separately labeled historical cloud inventory and user-reported
SDK-only installation checks. `sdk-pin.txt` pins the
SDK archives only, not every transitive dependency. Check the complete dependency
lock, licenses and current advisories before a distributable runtime release.

CVE-2026-59951 is fixed in SDK 1.30.0 for the 1.x line. This adapter exposes only
stdio and does not initialize HTTP/SSE servers. Its bounded local selection does
not approve remote hosting or establish a complete security review.

## Dependency release gate

The adapter now requires **MCP SDK 1.30.0 exactly**. The previous frozen
candidate's SDK 1.29.0 / Python 3.12 protocol results remain historical evidence;
they do not verify this new SDK selection. The user reported a successful SDK-only
installation on Ubuntu 26.04.1 x86_64 / Python 3.14.4, including `pip check`,
low-level Server/stdio imports, all 29 selected versions and prefix isolation.
That installation alone is not adapter acceptance. Subsequent native SDK 1.30.0
framing/cancellation and synthetic-provider integration checks passed. Live MCP
client, real-model and deployment acceptance remain open. See
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
