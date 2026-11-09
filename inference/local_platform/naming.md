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

# Local AI Fabric naming and compatibility

## Names

The agreed product names are:

| Scope | Display name |
| --- | --- |
| Independent local AI development platform | Local AI Fabric (LAF) |
| Guarded inference service | LAF Router |
| MCP integration | LAF MCP |

Use these English names in new product documentation. They do not rename the
separate AceIS implementation or change historical evidence. Preserve original
names in dated acceptance records, logs, upstream references and quoted output.

## Compatibility boundary

This source candidate preserves compatibility identifiers. It does not deploy
a renamed service, update an installed MCP App, or create new transports or
access. Example units retain their filenames and Python module entry points,
but require explicit local paths before installation.

Keep these identifiers unchanged:

| Identifier | Existing value or scope |
| --- | --- |
| Client modules | `inference.local_platform.*` |
| Router modules | `inference.aceis_service.*` |
| GPU user unit | `aceis-inference.service` |
| GPU socket | `$XDG_RUNTIME_DIR/aceis-inference/service.sock` |
| NPU unit and socket | `aceis-npu.service`; `$XDG_RUNTIME_DIR/aceis-npu/service.sock` |
| Client socket override | `LOCAL_INFERENCE_SOCKET` and `--socket` |
| HTTP routes and JSON fields | Existing v1 API in [API.md](API.md) |
| Model/profile IDs | Existing IDs; display naming does not add models |
| MCP tool names | Existing names, including `aceis_local_*` and `codex_*` |
| MCP server identity | Existing `serverInfo.name` and protocol metadata |
| App/plugin identity | Existing App/plugin IDs and package name |
| State and ownership | Existing paths, locks, labels and owned-container checks |

Do not replace `aceis` strings globally. Some belong to the separate AceIS
implementation; others bind clients, allowlists, state, resource ownership or
rollback to the running deployment. A new brand is not an API migration.

## Any future display rollout

1. Review and accept this documentation and example-description change.
2. Update remaining CLI help or other user-visible labels in the owning source,
   with focused regression tests and the same ASCII-only code convention.
3. Rename an existing MCP App presentation to `LAF MCP`
   through its supported owning App workflow. Preserve App/plugin identity,
   audience, connection, authentication, tool names and permissions. A generated
   local plugin-cache manifest is not the editable source of truth.
4. If changing an installed systemd description, inspect and back up that unit,
   keep its unit name and command unchanged, and arrange any required reload or
   restart separately. Do not interrupt active inference merely for branding.
5. Read the displayed names back in the intended host. A saved source label is
   not proof that an installed App or running process has adopted it.

An MCP integration and the private Router service are separate
integration boundaries. Renaming both does not assert that MCP has been rewired
to the new Router. Validate any such rewiring as an independent implementation
change.

## Verification and later distribution

For the documentation-only patch, check patch applicability, links, valid
command examples and compatibility identifiers. Runtime tests are required for
later source or deployment changes; documentation checks alone cannot pass them.

Before introducing new package, executable, image, repository or service names,
choose the exact names and a compatibility/rollback policy. Docker Hub release,
public networking, credentials, persistent access and permission changes remain
separate decisions. The existing deployment procedure is not a complete fresh-host
Quick Start. Continue using [the distribution checklist](../aceis_service/distribution-notes.md)
before proposing a portable release.
