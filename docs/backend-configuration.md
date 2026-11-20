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
| Codex pipeline | Optional account-backed tasks | Protected existing native, explicit canary scope or hash-pinned operator schema2 policy, and normal authorized account path; fresh model/effort advertisements precede new work. |
| Orchestrator | Ordinary-daemon observability | Explicit configure-orchestrator binding enables the portable profile's status/list/get metadata tools. No task-write policy is created. |
| Mailbox | Non-secret questions and answers | Requires a real operator-approved task/request/hash slot. It does not activate ChatGPT or prove Chat identity. Do not invent slots just to enable startup. |
| Mailbox grants | Bounded mailbox workstreams | Requires mailbox plus a separately authorized finite grant, disjoint task namespace, purpose, expiry, request and retention limits. No automatic grant is created. |
| Experiments | Optional benchmark evidence reader | Requires a selected owner-controlled manifest and SHA256, pinned sources and individually approved release projections. No benchmark execution or general accuracy claim follows. |

The new-prefix supported binding commands are configure-router,
configure-codex and configure-orchestrator. The other groups remain denied in
this bootstrap unless a future separately reviewed setup path supplies their
actual prerequisites. Existing owner-runtime configurations retain their old
contracts. Never remove denial fields or copy an existing runtime database to
make a new prefix appear configured.

Modification notice (2026-11-15): configure-codex can import an explicitly
selected non-secret schema2 policy snapshot. It preserves the existing validated
values; it does not infer workspace paths, provider limits or resource-led
admission from a capability projection. The canary mode remains distinct.

The optional resource-mode source is not activated by importing that pipeline
policy. Its scheduler policy, measured vectors, fresh provider/account evidence
and shared-ledger generation need separate review. The explicit
configure-resource-policy command snapshots a complete protected private
scheduler document before use without editing packaged source. It supplies no
measurements, quota evidence or backend authorization. The checked-in scheduler
configuration remains legacy. Never infer resource capacity from its count
ceiling, an idle model or a new prefix; shared ownership/UNKNOWN fences still
apply. Resource-mode adoption uses a fresh mode-pinned pipeline ledger and
preserves old history. Missing reservations do not verify past provider turns.

The optional nested resource-admission schema3 adds the explicit
[provider-feedback contract](provider-feedback-admission.md) for the Codex
pipeline and bounded ordinary Runner. It does not change the separate schema2
pipeline-policy format. Ordinary execution still requires a verified charged
controller and native client scopes; metadata/status configuration alone enables
no task execution. Schema1/2 finite-quota behavior is retained; no protected
policy selection attests external capacity.

For daemon metadata, use the [bootstrap commands](bootstrap.md) and review the
printed daemon-command. The ordinary metadata mode requires neither an enabled
Codex pipeline nor a native/account probe. Start it only through the supported
normal operator-host path; ownership, resource and uncertainty guards remain.
The portable integrated profile advertises 29 tools, including only the
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

## Remote HTTP, OAuth and MCP Events

Modification notice (2026-11-15): fresh-ledger initialization now retains the
validated notification composition until HostGateway creates its source ledger.
It does not reload providers, create a placeholder ledger, start a delivery
thread or contact a receiver during activation. Only a genuinely absent source
under the same private runtime directory may use this one-time deferral. Other
disabled states and failed storage stay disabled.

The standard plugin launcher is stdio-only. The copied prefix has a separate
HTTP entrypoint with this frontend invocation shape:

    "$SDK_PYTHON" -B "$PREFIX/runtime/integration/launch.py" \
      --runtime "$PREFIX/runtime/private_codex/.runtime/main" \
      --transport http --http-port "$OPERATOR_HTTP_PORT" \
      --allow-codex-pipeline

This binds the existing loopback HTTP transport; it is not a complete remote
Quick Start. Start the owner daemon through the supported bootstrap
daemon-command and existing namespace/cgroup/resource admission. With both its
Codex pipeline opt-in and `LAF_MCP_EVENTS_ENABLED=1`, the foreground controller
now retains only the existing OAuth issuer, audience, JWKS path, subject
selection, scopes and `CREDENTIALS_DIRECTORY` references for its child.
Defaults remain minimal. Invalid Events flags reach the existing validator
without forwarding provider references. No account override, token, broad
environment inheritance or internal daemon entrypoint is needed.

The frontend separately retains its existing OAuth resource URL. Do not print
or copy configuration/credential values into the package or a new prefix.
The Events opt-in is
`LAF_MCP_EVENTS_ENABLED=1`; the providers require the existing systemd
`mcp-events-aes-key` and `mcp-events-owner-hmac-key` credentials. The command above
alone does not supply them. Do not add `--runtime-project-root` or select local
single-user HTTP mode to make a remote OAuth failure pass.

For Router tools, add the following flags to that HTTP command:

    --allow-existing-tools --allow-laf-router --laf-bootstrap-bindings

This loads one explicit bootstrap binding snapshot, preserves its Router
allowlist/socket identity and fences later tool/Events calls if the record
changes. A denied Router remains visible but opens no endpoint or receipt store.
Do not combine this mode with raw Router socket/state/model arguments. Raw mode
is retained only for existing runtimes without a bootstrap state marker.
Existing shared-tool HTTP authorization still requires exactly one configured
OAuth principal. Keep that scope when updating the frontend.

An existing stdio Tunnel response or an older local HTTP adapter's discovery
response does not establish this candidate's OAuth or Events delivery. Verify
the exact server version, approved endpoint and authentication behavior. An
active encrypted credential reference does not prove that issuer/JWKS/subject
configuration, an authorized client or a callback receiver is available.

Before a remote acceptance run, the service owner must select the exact package,
unused prefix and schema2 policy, preserve the old runtime/history for rollback,
and confirm ownership, idle resources, an existing authorized OAuth client and
an explicitly approved HTTPS callback receiver. Subscribing sends a verification
challenge immediately. Do not choose a receiver, create credentials or subscribe
without those inputs. Keep operational evidence private.

1. Verify the installed candidate, normal daemon admission and exact model/effort
   advertisements. Exercise the configured Tunnel and authenticated HTTP path;
   also verify the expected unauthenticated and wrong-principal refusals.
2. Start one OAuth-owned thread and require `access_binding:BOUND` before storing
   input. Use `codex_chat_store` for one harmless immutable input without queueing,
   and require its `access_binding:BOUND` before subscribing. Do not adopt old
   local-UID tasks as OAuth-owned work.
3. Subscribe through `events/subscribe` to the approved receiver, complete its
   challenge and retain the subscription ID. Queue that same stored task only
   after verification; HTTP does not expose manual pipeline ticking.
4. Reconcile the same task after a lost response. Check the signed delivery and
   its stable event ID separately from durable `codex_events` history, then
   unsubscribe and archive the idle thread. A cancellation acknowledgment does
   not prove physical provider stop; require terminal history and executor
   release before cleanup. Do not repeat generation to repair missing evidence.

These are operator acceptance prerequisites and steps, not a record that a
remote account, Tunnel, token or callback delivery has been tested.
