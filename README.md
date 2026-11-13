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

<!-- Modified 2026-11-09: add the checkout-local interactive chat guide. -->

<!-- Modified 2026-11-10: redesign the landing page, document the default chat entrypoint, and move source-preview and test details into docs. -->

<!-- Modified 2026-11-10: link client-only packaging while retaining the separate runtime-provisioning boundary. -->

# Local AI Fabric integrated plugin (experimental source)

The root plugin selects the existing 27 legacy/Codex tools, one new Codex catalog read and five Router tools,
with six explicit operator opt-ins. Local Core/Router inference needs no OpenAI
account; Codex is an optional separately authorized integration. HTTP-compatible
source is retained; the portable manifest itself launches stdio, not an HTTP endpoint.

See [existing-owner-host setup](docs/integrated-plugin-install.md). This is not a
complete clean-host Quick Start. Model/SDK/vendor assets and private account,
service, tunnel and runtime state are not included. Actual Codex 0.167.0 loader,
cache and SDK-backed MCP discovery passed for source commit `b531d31` only; see the
[scoped evidence](docs/codex-install.md#completed-local-loader-and-discovery-evidence).
Clean-host provisioning, general backend functionality and Chat acceptance remain separate gates.

For Codex registration and the local marketplace option, see [Codex install](docs/codex-install.md).

For a NEW independent private prefix, use the [prepare and configure guide](docs/bootstrap.md).
It connects explicit existing Router/Codex prerequisites without copying another
runtime's state. Unconfigured backends remain denied; this is not complete
clean-host provisioning or acceptance of all backend actions.

For provider-advertised Codex models and immutable reasoning effort, see
[model selection](docs/codex-model-selection.md). One bounded `gpt-6.1-sol`/`max`
application-path turn completed; its [verification scope](docs/codex-model-selection.md#bounded-solmax-application-path-result)
does not verify provider effort or normal installation. The existing configuration
profile name `integrated-32` is retained for compatibility.

## Source layout and package build

`runtime/` is the fixed reviewed compatibility implementation for the root launcher.
Top-level `inference/`, `laf`, `mcp/`, docs and scripts remain the public development
source from the PR11 integration base, not overwritten by that older snapshot.
The standalone five-tool `mcp/` is retained as an optional component, not the root
plugin. Changes to root source do not silently change the pinned runtime subset.
The previous public component guide is preserved at [component source guide](docs/public-source-component-guide.md). See [origin and changes](SOURCE_ORIGIN.md). Future consolidation must preserve
33-tool schemas and runtime guards.

Build source-only from this checkout without a private input ZIP:

```sh
python3 -B scripts/build-integrated-source.py --output /absolute/new/laf-source.zip
```

This creates a new archive only; it installs or starts nothing. Release-time rights,
secret and dependency review is required before distribution. Portal listing
metadata is a later submission concern, not a prerequisite to reviewing this source.

## Legacy helper compatibility boundary

The integrated 33-tool catalog and startup/read checks do not establish that all
33 tools work against the modern Router. Prefer the five `laf_router_*` tools for
the modern owner-scoped Router interface. The full profile also requires an
existing local-LLM helper directory and configured runtime, mailbox and grant
state. The compatibility tools `aceis_local_llm_generate`,
`aceis_local_model_status`, `aceis_local_model_generate` and
`aceis_local_model_prepare` depend on their separate legacy backend. That helper
route currently has an unresolved transport failure; its functionality is not
accepted by the modern Router checks. Do not silently translate helper calls to
Router load/unload operations, fabricate helper configuration, or weaken guards.
This known limitation does not prevent source Alpha review, but this is neither
an all-33-functional claim nor a complete clean-host setup.
