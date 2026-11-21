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

With four explicit operator opt-ins, the root plugin exposes 29 tools,
including `codex_models_list` and five owner-scoped Router tools. Local
Core/Router inference needs no OpenAI account. Codex is an optional separately
authorized integration. The portable manifest launches stdio, and the source
retains the HTTP frontend. The LAF host and standalone packages require
Python 3.12 or later.

See [existing-owner-host setup](docs/integrated-plugin-install.md). This is not a
complete clean-host Quick Start. Model/SDK/vendor assets and private account,
service, tunnel and runtime state are not included. The installation guide keeps
the [historical loader evidence](docs/codex-install.md#completed-local-loader-and-discovery-evidence)
scoped to its original source. Later local predecessors were checked separately:
`c2ab056` completed installed stdio Flash and Codex canaries, correlated terminal
history and same-runtime restart/reconciliation; `438b2aa` passed installed
private-policy setup and 33-tool discovery with an expired synthetic policy.
`e42ebd1` later completed installed Flash/Codex canaries, original-key restart
replay, active Codex cancellation and owned local executor cleanup. The model
canaries used compatibility admission with resource mode off. Those observations
do not accept a newer package, production resource policy, provider quota or
the production Tunnel/client path. Use the [installed acceptance procedure](docs/installed-acceptance.md)
for the selected package. Raw account, process and receipt evidence stays private.

For Codex registration and the local marketplace option, see [Codex install](docs/codex-install.md).

Choose a model from the Router's [configured catalog](docs/laf-configured-models.md).
Keep its weights in persistent operator-managed storage outside the installed
plugin. Point the Router at an existing model, or explicitly install the
selected artifact using its [model-management procedure](docs/laf-model-management.md)
after reviewing its source and terms. Plugin installation and updates do not
fetch, install or replace model weights.

The optional [provider-feedback contract](docs/provider-feedback-admission.md)
supports resource-admission schema3 for bounded Codex pipeline and ordinary
Runner execution without inferring a provider concurrency limit. Local resources,
account ownership, durable provider restrictions and UNKNOWN/no-replay guards
still apply. Ordinary pre-turn waiting retains one execution, reservation and
workspace lock; it never requeues a dispatched task. Existing schema1/2 finite
provider quotas retain their enforcement. This staged source path
does not select a calibrated policy or establish live resource-mode acceptance.

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

`runtime/` retains the reviewed compatibility base plus the explicit changes
recorded in [CHANGES.md](CHANGES.md) and [SOURCE_ORIGIN.md](SOURCE_ORIGIN.md).
Top-level `inference/`, `laf` and `mcp/` remain separate development/component
source. The shared resource protocol and selected Core/Router changes are
mirrored in top-level and bundled inference source.
The standalone five-tool `mcp/` is retained as an optional component, not the root
plugin. Changes to root source do not silently change the pinned runtime subset.
The previous public component guide is preserved at [component source guide](docs/public-source-component-guide.md). See [origin and changes](SOURCE_ORIGIN.md). Future consolidation must preserve
the supported tool schemas and runtime guards.

Build source-only from this checkout without a private input ZIP:

```sh
python3 -B scripts/build-integrated-source.py --output /absolute/new/laf-source.zip
```

This creates a new archive only; it installs or starts nothing. Release-time rights,
secret and dependency review is required before distribution. Portal listing
metadata is a later submission concern, not a prerequisite to reviewing this source.

## LAF 0.1.0 release naming

Use the five `laf_router_*` tools for owner-scoped local inference. The fully
opted-in integrated profile exposes 29 tools. The former four local helper
operations and their configuration switches have been removed.

The [LAF-only naming gate](docs/laf-release-names.md) is mandatory for 0.1.0.
Existing resource guards still apply. Allocation-level
[GPU release verification](https://github.com/i-pp/laf/issues/22) and coordinated
[swap policy management](https://github.com/i-pp/laf/issues/13) are follow-up work.
