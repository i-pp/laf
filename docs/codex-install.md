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

# Install the experimental LAF plugin in Codex

LAF 0.1.0 is experimental source, not a self-provisioning inference stack.
The package retains the 27 Codex/orchestration/legacy tools plus Router5.
First satisfy the [existing owner-host prerequisites](integrated-plugin-install.md):
SDK 1.30.0, daemon/policy/grant state, legacy helper configuration, an owner-only
Router and private receipt directory. Do not copy another person's credentials
or runtime. Local Core/Router needs no OpenAI account; optional Codex operations
use that operator's separately authorized installation.

## Existing owner host: simplest supported path

Review the checkout, create your private `~/.config/laf/plugin.json` as described
in the prerequisite guide, then use an absolute checkout path:

```sh
python3 -B /absolute/path/to/laf/launcher.py --check
codex mcp add laf -- python3 -B /absolute/path/to/laf/launcher.py
codex mcp list
```

These are operator-run commands. `--check` validates metadata only; it does not
accept the SDK, daemon, authentication or models. `mcp add` changes Codex's local
MCP configuration. First check `codex mcp list` and do not overwrite an existing
server named `laf`. No extra API listener or cloud binding is needed. Reopen the
Codex session after registration and verify discovery of 32 tools and harmless
status reads before attempting actions. Do not run a model merely as discovery.

## Codex local marketplace option

The checkout also includes portable `plugin.json`/`mcp.json`, a matching
`.codex-plugin/plugin.json` compatibility overlay, Codex `.mcp.json`, and
`.agents/plugins/marketplace.json`. Codex CLI 0.167.0 exposes these commands:

Use a source-only ZIP built by `scripts/build-integrated-source.py`, extracted
under a new private directory, rather than registering a Git checkout. The
Codex installer copies the source directory, including a checkout's `.git`
metadata if present. The source builder excludes all Git/runtime evidence.

```sh
python3 -B /absolute/path/to/laf/scripts/build-integrated-source.py --output /absolute/new/laf-0.1.0-source.zip
(umask 077; mkdir /absolute/new/laf-0.1.0 && python3 -m zipfile -e /absolute/new/laf-0.1.0-source.zip /absolute/new/laf-0.1.0)
codex plugin marketplace add /absolute/new/laf-0.1.0
codex plugin add laf@laf-experimental
```

All example destinations must be new; preserve existing directories and records.
Choose this OR direct MCP registration, not both, to avoid duplicate tools.
The marketplace is local source and has no account/app binding. Installation
copies source into Codex's plugin cache; it neither provisions the daemon nor
installs SDKs/models. Never edit the cache as the development source.
Actual loader, startup and tool-call acceptance are recorded separately from
metadata checks. A successful install alone is not all-32-functional acceptance.

## Versions and limits

The source plugin and integrated MCP `serverInfo.version` are 0.1.0. The standalone
Router5 component already has its own 0.1.0 version. JSON Schema URLs containing
1.0.0 are format versions, not LAF releases. An existing remote ChatGPT App's
1.0.0 listing is separate stored connection metadata: changing this checkout or
refreshing tools alone does not establish that the listing has changed.

No npm/uvx wrapper is published here. Those wrappers would not remove the daemon,
policy, auth, model and owner-socket prerequisites. Clean-host/second-PC setup,
8–16 GB hardware acceptance and all 32 tools' backend functionality remain open.
See the legacy-helper transport limitation in the prerequisite guide.

The commands were checked against the installed CLI's `--help`. Local marketplace
registration, install and list also succeeded in an isolated HOME/CODEX_HOME;
this is metadata/cache acceptance, not MCP startup or model acceptance. These
examples are not an instruction to alter an existing host automatically. No tool output,
credentials, model output or SDK binary is included in this source package.
