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

# LAF integrated plugin: experimental source

This package preserves the existing 27 Codex/orchestration/legacy tools, adds
`codex_models_list`, and retains five owner-scoped Router tools. The owner has authorized publication of the
project-authored source changes under KOL-v1, with third-party terms preserved.
This is an experimental source proposal, not a complete clean-host Quick Start,
a directory publication approval, or a safety-critical product certification.
Private assembly status, credentials and operational evidence are not distributed.

## What is included

- Portable `plugin.json` and `mcp.json`, without a private App binding or endpoint.
- A stdio launcher for the existing integrated production CLI. All six legacy
  capability opt-ins are explicit in the operator config. There is no silent
  five-tool fallback, no task-write flag, and no automatic model fallback.
- The reviewed stdio runtime and HTTP compatibility entrypoint at
  `runtime/integration/launch.py`. The portable manifest starts **stdio only**.
  Existing HTTP authentication must be retained; this package neither configures
  nor exposes an HTTP server and does not copy a live IdP or Tunnel binding.
- Applicable upstream license and notice files. Imported snapshot changes retain
  their specific origin and modification notices; external terms are unchanged.

There are no SDK/model binaries, live runtime files, account tokens, live/installed service
units (example templates are included), captured model outputs, machine-specific tunnel registrations or logs.
Local Core/Router inference itself requires no OpenAI account. Codex operations
use the operator's separately installed and authorized Codex integration; this
package does not create accounts or authenticate them.

## Existing owner-host update flow

Prerequisites must already be real, not inferred from these instructions:

1. A supported Linux owner environment with the existing daemon/runtime,
   prepared policy checkout and capability/grant configuration.
2. An existing MCP SDK **1.30.0** Python interpreter. The production CLI enforces
   the version; the launcher does not download, install or import an SDK to fix it.
3. An existing owner-only Router socket and explicit allowed model IDs.
4. A private receipt directory path reserved for this user's adapter. Preserve
   its durable records across frontend updates; never recreate attempt IDs or
   replay uncertain jobs merely because a connection disappeared.

Create an owner-only configuration file (`0600`, no symlink) at
`~/.config/laf/plugin.json`, below private/trusted owner directories. Select the
following exact fields using the actual existing local paths and model IDs:

```json
{
  "schema": 1,
  "profile": "integrated-32",
  "sdk_python": "/operator/existing-sdk/bin/python",
  "runtime": "/operator/existing-project/.runtime/owner-runtime",
  "runtime_project_root": "/operator/existing-project",
  "router_socket": "/operator/existing-router/service.sock",
  "receipt_directory": "/operator/private-state/receipts",
  "models": ["flash"],
  "capabilities": [
    "local-llm", "local-models", "chat-mailbox", "mailbox-grants",
    "codex-pipeline", "experiments"
  ]
}
```

The profile name `integrated-32` is retained for configuration compatibility;
with Codex catalog discovery enabled, its current complete inventory is 33 tools.
See [Codex model selection](codex-model-selection.md).

Those paths are schema illustrations, **not runnable defaults**. Selecting the
six opt-ins acknowledges their existing configured scope, not a grant of new
workspace/identity/network permissions. Do not use this profile when those
capabilities have not been configured and approved. `flash` is an example model
ID, not an installation instruction or a requirement that Flash be present.

From the assembled package:

```sh
python3 -B launcher.py --check
```

This checks configuration and entrypoint/interpreter metadata only. It does not
prove SDK version, daemon availability, policy acceptance, actual tool discovery,
model execution or authentication. It starts no child. Install/reload the local
plugin only through the target host's supported, separately authorized flow.
Then verify real startup, all 33 expected tools, one harmless status read and the
retained Codex read paths. A local process plugin does not by itself make the
server accessible from ChatGPT web/mobile.

For an existing remote ChatGPT connection, use its existing approved hosting and
authentication. Refresh the **same** app's tool definitions after a backend
update; do not create a second binding, publish the private connection, reset
permissions or enable broader access. The package does not operate systemd or
switch running services.

## Failure behavior and data handling

Malformed/duplicate/unknown config fields, relative/traversal paths, implicit
capabilities and unsafe config permissions fail before execution. Startup errors
go to stderr, not the MCP stdout stream. The launcher does not forward `CONTROL_PLANE_API_KEY`, `OPENAI_API_KEY`,
`OPENAI_ADMIN_KEY`, or Python override environment values to the configured SDK.
This is separate from the existing parent daemon's authorized Codex authentication.
It uses a small non-secret environment allowlist before executing the configured SDK
interpreter; existing owner-runtime and SDK/UDS/receipt guards remain in the
production CLI. A refusal is not bypassed or automatically repaired.

Model outputs are unverified. A returned job handle is not proof of completion;
a cancellation acknowledgment is not proof of physical stop. Read/reconcile the
same receipt after an uncertain result, never submit a new attempt as a retry.

## Completed local loader and discovery check

The actual Codex 0.167.0 local loader, using an existing SDK 1.30.0 and synthetic
private fake-runtime/UDS fixtures, discovered 33 unique tools with the exact
expected schemas for source commit `b531d31b6db9206a5a5c15ba2efb7d406931fc64`.
This is bounded loader/cache/discovery evidence, not backend or model acceptance.
See the [archive identity, evidence and limits](codex-install.md#completed-local-loader-and-discovery-evidence).
Later source archives are not covered by that historical run.

## Remaining product acceptance and directory-release gates

- Recheck source origin, secret exclusion and modification notices for later changes.
  The present owner-authorized source scope does not authorize a directory submission.
- Recheck the actual target-host loader and 33-tool catalog after launcher,
  runtime, manifest or target configuration changes. The historical check above
  does not accept later packages, real backends or ChatGPT client discovery.
- Provide and test a clean-host daemon/policy/isolation/setup flow. Existing-host
  success does not establish second-PC portability, other Python versions or
  8-16 GB VRAM acceptance.
- For a public directory submission, verify publisher identity, website/support/
  privacy/terms URLs, countries, actual commerce behavior, icon, recorded demo,
  review cases and applicable attestations. No URLs, credentials, legal promises,
  country targeting or review approvals are invented in this package.

Packaging unit checks remain source/configuration and synthetic ZIP checks.
Actual SDK startup/discovery has only the historical scope above. No real service,
account, GPU/model, public deployment or live backend adoption is accepted by
that loader check. Earlier runtime evidence retains its own documented scope.

## Source-complete scope, not automatic provisioning

The archive includes public LAF Core/Router/inference services, local CLI, build source, docs and skills under runtime/public_laf, alongside the integrated Codex/MCP frontend. It does not bundle external private C++ Ace-Core, models, SDKs or vendor binaries. Having these sources is not a tested clean-host daemon/model/hardware installation flow. Existing owner-host prerequisites above remain mandatory. The manifest launches stdio; retained HTTP implementation is not a configured or authenticated public endpoint. See SOURCE_ORIGIN.md, CHANGES.md and notices/agent-orchestrator. Component stdio-only notices apply only to their original component.

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
