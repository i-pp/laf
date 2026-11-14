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
The package retains the 27 Codex/orchestration/legacy tools plus Router5 and adds
the read-only `codex_models_list` tool, for a fully opted-in catalog of 33 tools.
First satisfy the [existing owner-host prerequisites](integrated-plugin-install.md):
SDK 1.30.0, daemon/policy/grant state, legacy helper configuration, an owner-only
Router and private receipt directory. Do not copy another person's credentials
or runtime. Local Core/Router needs no OpenAI account; optional Codex operations
use that operator's separately authorized installation.

For a NEW independent prefix instead of reusing existing runtime state, see the
[source prepare/configure flow](bootstrap.md). It provides explicit backend
bindings and optional protected native copying, not automatic SDK/model/service
provisioning or a complete clean-host Quick Start.

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
Codex session after registration and verify discovery of 33 tools and harmless
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
The completed check below used the actual Codex loader and MCP SDK, not only
metadata inspection. Neither that discovery result nor installation alone
establishes that all 33 tools' backend actions work.

See [provider catalog and reasoning-effort selection](codex-model-selection.md)
for the added read-only tool and immutable input settings.

## Completed local loader and discovery evidence

This historical acceptance belongs to source commit
[`b531d31b6db9206a5a5c15ba2efb7d406931fc64`](https://github.com/y-ooya-ipp/laf/commit/b531d31b6db9206a5a5c15ba2efb7d406931fc64).
Its source-only ZIP contained 363 members, with SHA-256
`027aa71249d722df9af8af678e64cc6f177a56d0fd624a94e2731229dbc01122`.
Every source/cache file matched byte-for-byte. No later all-source ZIP, including
one containing these documentation edits, is covered by that run. The
[public PR record](https://github.com/i-pp/laf/pull/12) records the evidence;
[source CI for the same commit](https://github.com/i-pp/laf/actions/runs/43419504551)
is separate from loader acceptance.

- Official Codex CLI 0.167.0 used a new isolated HOME/CODEX_HOME and plugin cache,
  synthetic private fake-runtime/UDS fixtures, and an existing SDK 1.30.0.
- From a separate empty working directory, the actual loader resolved the
  relative `.mcp.json` working directory and launcher. SDK-backed MCP discovery
  reported `laf@laf-experimental`, 33 unique tools with the exact expected
  schemas, `serverInfo.version` 0.1.0 and `toolsError` null.
- The only outer RPCs were `initialize`, `initialized`, `plugin/installed` and
  `mcpServerStatus/list`. Fixture contacts, audited tool calls and receipts were
  all zero. All nine tracked processes, including observed descendants, were
  confirmed absent after cleanup; directly owned children were reaped.
- Resource observations were sampled, not continuous measurements or an OS
  sandbox. Independent read-only evidence review passed.

This establishes loader/cache/discovery only, not clean-host setup, all 33 tool
operations, real-account/provider model or reasoning-effort execution, billing,
zero native TCP activity, 8-16 GB VRAM suitability, live backend adoption or
public-directory release. Existing daemon, policy, grants, helper, Router/SDK and
private configuration prerequisites still apply.

A separate [bounded Sol/max application-path trial](codex-model-selection.md#bounded-solmax-application-path-result)
completed through a private carrier, not a normal plugin installation. It does
not extend this historical loader/archive scope or verify provider effort.

## Versions and limits

The source plugin and integrated MCP `serverInfo.version` are 0.1.0. The standalone
Router5 component already has its own 0.1.0 version. JSON Schema URLs containing
1.0.0 are format versions, not LAF releases. An existing remote ChatGPT App's
1.0.0 listing is separate stored connection metadata: changing this checkout or
refreshing tools alone does not establish that the listing has changed.

No npm/uvx wrapper is published here. Those wrappers would not remove the daemon,
policy, auth, model and owner-socket prerequisites. Clean-host/second-PC setup,
8-16 GB VRAM hardware acceptance and all 33 tools' backend functionality remain open.
See the legacy-helper transport limitation in the prerequisite guide.

The commands were checked against the installed CLI's `--help`; the completed
loader/discovery check has the exact historical scope above. These examples are
not an instruction to alter an existing host automatically. No private tool output,
credentials, model output or SDK binary is included in this source package.
