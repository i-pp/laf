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

# Local AI Fabric for local Codex

This marketplace offers one plugin, **Local AI Fabric Local**. Codex starts its
stdio adapter on your machine. The adapter uses one owner-only Unix socket to reach
your local LAF Router and its locally configured models. It offers the model catalog,
Router status, and three tools to submit, inspect, or cancel an owned job. The
Router still enforces its own resource policy.

## Prepare the local runtime

Use Linux with Python 3.12 or later available as `python3` to Codex. Prepare
your own model and Router first, using the
[local model and CPU Router guide](https://github.com/i-pp/laf/blob/main/docs/laf-model-management.md).
Use a Router that serves local models on this machine. Select its actual socket
path and public model ID. The socket must be owned by you with mode `0600`
inside an owner-only `0700` directory. The adapter authenticates the local
peer before starting stdio.

Install the [reviewed MCP SDK 1.30.0](https://github.com/i-pp/laf/blob/main/docs/laf-sdk-install.md)
separately and locate its virtual environment's Python executable. The SDK install
guide covers CPython 3.12 and 3.14 on Linux x86_64. The plugin starts no Router or
provider and performs no dependency installation or model download at startup.

Save the following shape as `~/.config/laf/codex-local.json` with mode `0600`.
Use your own absolute paths and model IDs in place of the examples. Give the
new receipt directory an existing trusted parent. Keep configuration, receipts,
the SDK and model storage outside the marketplace and installed plugin cache.

```json
{
  "sdk_python": "/absolute/local/sdk/venv/bin/python",
  "router_socket": "/absolute/local/router/service.sock",
  "state_dir": "/absolute/local/state/laf-local-receipts",
  "models": ["my-local-model"]
}
```

When the configuration or socket is unavailable, the adapter exits and writes a
diagnostic to stderr. Configure the Router for on-machine inference. A local
socket that forwards to another host is outside this setup.

## Export and check the Git marketplace

From the reviewed LAF source checkout, pass a new absolute output path outside the
checkout. The export has its own catalog and LAF plugin manifests.

```sh
python3 -B scripts/build_local_codex_marketplace.py --output /absolute/new/laf-local-marketplace
python3 -B scripts/build_local_codex_marketplace.py --check /absolute/new/laf-local-marketplace
```

Use that directory as the root of the dedicated distribution Git ref or repository.
Its catalog points to `plugins/laf-local`. This keeps Git checkout metadata
outside the directory that Codex installs. The integrated LAF source checkout
has a different catalog. Only the dedicated export is suitable for this marketplace.

## Verify Git installation and updates

Use a fresh OS test account or VM and a fresh Codex profile, with no existing LAF
entry or shared Router. Prepare that machine's local runtime and configuration as
above. In the commands below, set `LAF_MARKETPLACE_SOURCE` to the Git address of
the dedicated export and `LAF_MARKETPLACE_REF` to its update branch.

```sh
codex plugin marketplace add "${LAF_MARKETPLACE_SOURCE:?Set the export Git address}" \
    --ref "${LAF_MARKETPLACE_REF:?Set the export branch}"
codex plugin marketplace list
codex plugin add laf-local@laf-local
```

Start a fresh Codex session. Check `/mcp` for the five `laf_router_*` tools and ask
Codex to run only `laf_router_status`. Verify `ready` on the test Router.
Start a fresh session with the test configuration absent, then with a missing socket,
and confirm the adapter reports a startup failure in each case.

For the update check, advance only the dedicated test ref with a reviewed export
whose plugin version was bumped in the builder. Refresh the marketplace, install
the advertised version if required by the client, then start a fresh session.

```sh
codex plugin marketplace upgrade laf-local
codex plugin add laf-local@laf-local
```

Check the installed version, one plugin entry, five tools, and a read-only Router
status again. The owner-local settings and receipts live outside the plugin cache.
See the [Codex marketplace commands](https://developers.openai.com/plugins/build/plugins#add-a-marketplace-from-the-cli)
for client support. Git retrieval and update fetch the reviewed distribution source.
The running LAF adapter communicates with the configured local socket.

First-party files use KISS Open License v1.0. The plugin directory contains the
full license text in `LICENSE` and the separate MCP SDK license and notices.
