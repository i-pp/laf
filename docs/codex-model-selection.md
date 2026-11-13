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

<!-- Added 2026-11-13: provider-advertised catalog and immutable reasoning-effort selection. -->

# Codex model and reasoning-effort selection

## Busy-reader summary

With the existing Codex pipeline explicitly enabled, `codex_capabilities` keeps
operator-approved model order and adds fresh provider model/effort metadata.
`codex_models_list` is the new read-only catalog tool. The integrated profile
retains every existing legacy/Codex tool and Router5, adding only this catalog
read. Its configuration name remains `integrated-32` for compatibility; the
fully opted-in catalog now has 33 tools. No approval-response APIs are added.

Inspect and test the source without an account, SDK or running service:

```sh
python3 -B scripts/test_codex_selection_boundary.py
python3 -B scripts/test_codex_catalog_effort.py
python3 -B scripts/run_integrated_source_checks.py
```

These commands exercise fake peers, private temporary ledgers and the pure HTTP
handler. They do not start Codex, bind/connect a socket, generate text or prove
that a provider executed the selected effort. Existing-host installation still
requires the [separate prerequisites](integrated-plugin-install.md); this is not
a clean-host Quick Start.

A bounded application-path trial requested exact `gpt-6.1-sol` with `max` and
completed. The [verification scope](#bounded-solmax-application-path-result)
below separates selection and completion evidence from unverified provider effort.

Updating this checkout does not automatically update an existing daemon or its
live settings. Backend installation/restart and client catalog refresh require
separate authorization and verification; the source checks perform neither.

## Read the provider, not a native-role alias

Use the existing authenticated connection to inspect `codex_capabilities` with
empty arguments, or `codex_models_list` with `{"limit":20}`. An optional cursor
continues the same snapshot. Each request performs fresh bounded discovery:
at most four provider pages of at most 100 entries each. Caller pages contain
at most 20 entries and may be shorter to fit the complete MCP response.

Provider model IDs and supported effort strings are data, not instructions,
permissions, native-agent role aliases or fixed effort enums. Only an
operator-allowed, non-hidden provider model is selectable. Observed discovery is
not entitlement, actual execution, billing-class or model-application proof.
Hidden/absent models are unavailable after successful discovery. An incomplete,
rejected or failed discovery is unknown, with no cached positive availability.
Descriptions and private account/provider error text are not exported.

The legacy `models`, `available`, workspace, bounds and policy-hash fields remain.
Large duplicate policy-row metadata may be explicitly marked truncated and point
to `codex_models_list`. Follow `next_cursor` when present. A changed snapshot
requires a new read, not reuse of its old cursor.

## Omitted effort and explicit intent are different

`codex_thread_start`, `codex_chat_store` and `codex_task_submit` accept optional
`reasoning_effort`. When omitted on a new request, the pipeline reads that model's
freshly advertised default and persists the exact selected string, its source
and selection digest. It does not inherit a thread's previous effort or guess
from a native-agent setting. A failed discovery prevents creation of a new
omitted-effort input.

Explicit input intent can be stored or queued offline without claiming provider
support. Before dispatch, fresh provider support must still allow the exact
stored model/effort. A changed default does not rewrite a queued selection; a
removed model or effort blocks it before `turn/start`. Unsupported thread-start
selection is rejected before context creation. Active-turn steer cannot change
the model or effort.

Request identity records caller intent separately from resolved defaults.
Matching duplicate keys are looked up before discovery, even when discovery is
now unavailable. An omitted-effort retry returns its original record, not a new
default. Changing omission to an explicit value is a request-key conflict even
if the strings would match. Never use a new key to retry an uncertain operation.

Existing queued inputs without recorded effort are blocked without guessed
backfill or replay. Existing unknown/running work retains its held slot. Review
and reconcile that history before creating any separately authorized new work.
The operator-only proven-empty-context replacement keeps its existing proof,
remote-ID and single-attempt constraints; it carries the immutable effort and
refuses legacy selections rather than repairing them silently.

## Ownership of a blocked input

A pre-turn blocker can leave a definitively persisted input even though the MCP
result is an error. For OAuth submissions, the same owner retains get, list,
event-history and cancel access only after the returned input is matched against
the authorized thread, exact caller-intent operation and immutable selection in
the same bounded private ledger. The existing Events authorizer and immutable
origin-conflict check still control binding. The result stays `BLOCKED`; binding
is not a successful dispatch or a grant to another owner.

This works with the existing daemon proxy, without a new RPC or direct access to
a Pipeline object. This new blocked-input exception does not bind unknown/failed/ambiguous or forged
results, mismatched runtime paths, unreadable/locked/oversized proof or foreign
origins. Other result and authorization paths are not broadened.
A temporary proof failure never recreates the input or retries model execution;
an exact-key retry can reconcile the already persisted record when proof is
available. Existing uncertain-execution and no-replay rules remain unchanged.

## Narrow transport and honest evidence

The public argument is `reasoning_effort`; raw `effort` and arbitrary `config`
are not MCP arguments. The protocol uses `effort` on `turn/start` and permits
only `config.model_reasoning_effort` on thread start/resume. No other config
bridge is exposed. Effort tokens are bounded to 64 UTF-8 bytes, with control and
line/paragraph separator characters rejected.

Thread echo may be absent or null. That is recorded as not reported, not matched.
A different echo prevents dispatch/adoption. Acknowledged transport and even a
matching echo do not prove actual model execution; execution and billing
verification remain false. The inspected stable subset includes Codex 0.167.0
alongside the existing 0.165.0 and 0.164.0-alpha.11.1 gates. The versioned fixture
records field names and schema digests, not bundled upstream schema source.
No CLI/schema generation or real provider operation is performed by these tests.

OAuth ownership, shared-tool ACLs, policy pins, ordinary-session limits and
Router lifecycle remain separate and unchanged. Owner-filtered listings scan a
bounded record/page budget with a deadline; short source pages do not lose
owned records. Output budgets include both text and structured content inside
the full JSON-RPC/HTTP envelope, not only raw catalog JSON.

See [testing and exact-commit GitHub evidence](testing.md). Passing offline checks
is not deployment, live account/provider acceptance, a complete plugin-loader
check or a release approval.


## Bounded Sol/max application-path result

One real-provider turn used the public `Pipeline`/`ProtocolClient` application
path from source commit `b531d31b6db9206a5a5c15ba2efb7d406931fc64`, through a
private bounded native carrier (R3), not a normal plugin installation. That
runtime source is unchanged at `df24c5cda624eebd5e07dd2077141002dfe07ef9`.
The exact advertised strings `gpt-6.1-sol` and `max` were selected without alias
conversion; catalog freshness and broader entitlement remain unverified.
See the [public evidence record](https://github.com/i-pp/laf/pull/12);
[source CI at df24c5c](https://github.com/i-pp/laf/actions/runs/43448063414) is a
separate gate, not evidence of provider execution.

- Durable thread/task selection and operation fingerprints, the raw turn request
  model/effort and remote correlation matched. The thread-start provider echo
  matched `max`; the turn was acknowledged and reached `COMPLETED` with exact
  output `LAF_EFFORT_OK`.
- The actual turn's provider-effort echo was unavailable. Both context and turn
  retained `actual_execution_verified=false`. This does not verify internal
  reasoning strength, hidden reasoning or billing.
- Normal existing global instructions/context were used, not synthetic-only
  context. No tool events, server approval requests or interrupt were observed
  in this one trial. That is not a zero-capability or zero-network guarantee.
- Host observations were sampled, not continuous. Normal native exit 0, process
  absence and passing cleanup/host-guard checks were confirmed. Independent read-only
  review matched necessary ledger fields with sanitized evidence and the source
  freeze; raw usage events were not retained.

This result does not accept all models, all 33 tools, normal installation,
clean-host setup or live backend adoption. It does not rerun or extend the
[historical loader archive check](codex-install.md#completed-local-loader-and-discovery-evidence).
