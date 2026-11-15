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

# Select backends for an existing-host installation

Added 2026-11-15. A visible schema is not an operational backend. Configure only
real prerequisites and authority selected by the operator; keep unsupported or
unused groups explicitly denied. All configuration is for a new private prefix
before daemon, database or receipt use. Existing bindings are immutable.

| Group | Role in 0.1.0 | Configuration boundary |
| --- | --- | --- |
| Router | Local model jobs | Explicit owner socket and exact model allowlist; local Core/Router requires no OpenAI account. |
| Codex pipeline | Optional account-backed tasks | Protected existing native, operator workspace/model allowlist and normal authorized account path; fresh model/effort advertisements precede new work. |
| Orchestrator | Ordinary-daemon observability | Explicit configure-orchestrator binding enables the portable profile's status/list/get metadata tools. No task-write policy is created. |
| Local helper | Four legacy model operations | Requires a separate compatible backend. Current Router jobs cannot enforce the original fixed-profile, atomic loaded-only/no-autoload and 32768-context contract. Keep denied; do not substitute Flash. |
| Mailbox | Non-secret questions and answers | Requires a real operator-approved task/request/hash slot. It does not activate ChatGPT or prove Chat identity. Do not invent slots just to enable startup. |
| Mailbox grants | Bounded mailbox workstreams | Requires mailbox plus a separately authorized finite grant, disjoint task namespace, purpose, expiry, request and retention limits. No automatic grant is created. |
| Experiments | Optional benchmark evidence reader | Requires a selected owner-controlled manifest and SHA256, pinned sources and individually approved release projections. No benchmark execution or general accuracy claim follows. |

The new-prefix supported binding commands are configure-router,
configure-codex and configure-orchestrator. The other groups remain denied in
this bootstrap unless a future separately reviewed setup path supplies their
actual prerequisites. Existing owner-runtime configurations retain their old
contracts. Never remove denial fields or copy an existing runtime database to
make a new prefix appear configured.

For daemon metadata, use the [bootstrap commands](bootstrap.md) and review the
printed daemon-command. The ordinary metadata mode requires neither an enabled
Codex pipeline nor a native/account probe. Start it only through the supported
normal operator-host path; ownership, resource and uncertainty guards remain.
The portable integrated profile still advertises 33 tools, including only the
three read-only orchestrator tools. Independently enabling task writes requires
the separate existing operator-fixed flags and task policy; this binding does
not provide those permissions.

The ordinary scheduler ledger and Codex pipeline ledger are different. Test
status/list against the actual daemon. Test a known ordinary task only when one
exists; a Codex task ID is not an ordinary task ID. Unknown-ID refusal and an
empty list do not establish positive task lookup or task execution.

A core acceptance run must distinguish source/offline fixtures, native loader
discovery, installed-interface use, real local-model completion and real Codex
completion. Keep receipts and immutable request keys. For active cancellation,
separate acknowledgment, observed terminal interruption, verified history and
executor/resource release. Terminal-record cancellation is not an active-stop
trial. A Flash cancellation can affect the provider lifecycle; coordinate that
window with its service owner before testing it. Never restart an existing
provider or disable a guard to complete acceptance.
