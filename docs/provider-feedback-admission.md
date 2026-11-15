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

# External provider acceptance and local resource admission

Added 2026-11-15. Resource admission schema3 introduces an explicit account-bound
provider feedback contract for Codex pipeline only. It does not manufacture concurrency entitlement from
plan names, usage percentages, reset times, a successful canary or LAF slot
settings. Existing schema1/2 finite provider quotas keep their enforcement.

In feedback mode the provider decides whether to accept a submission. External
capacity remains UNKNOWN, including when usage metadata is unavailable. This is
an explicit operator-selected submission policy, not a positive capacity or
billing attestation. Fresh authenticated account binding, durable local CPU/RAM
reservation, owner/generation checks and physical headroom remain mandatory.
Local inference still needs its unchanged CPU/RAM/GPU/VRAM/model/controller and
UMA contracts. Unknown physical resources never become safe through this mode.

The provider entry selects `mode: feedback`, the protected native auth
`account_sha256`, and finite `backoff_seconds`. It contains no inferred integer
`limit` or fabricated entitlement expiry. An optional `usage_account_sha256`
pins the exact provider-returned usage accountId hash. Email plus plan cannot
identify a workspace; do not derive that usage pin from the auth fingerprint.
Without this usage pin, a subsequent restriction cannot recover automatically.
Select complete private scheduler bytes through `configure-resource-policy` on
a NEW unused prefix, using the existing expected raw SHA256 and provenance
checks. Packaged defaults, live bindings and existing ledgers are not edited.

`account/rateLimits/read` is exposed only with empty parameters. Automatic
reserve fallback, paid credit capabilities and account changes are not enabled.
The narrow client discards the raw usage accountId and retains a private digest
plus bounded restriction facts. Model/tool counts do not change.

Local accounting and provider feedback are separate. A shared, durable,
per-account restriction revision is sampled before bounded native metadata reads.
Those reads run outside SQLite write transactions. A later full response can
recover a restriction only when that revision still matches atomically, the
authenticated identity is fresh and unchanged, the usage account pin matches,
the backend explicitly allows ordinary included usage, every previously blocked
bucket/window is covered by favorable full evidence, and the finite backoff has
elapsed. Sparse updates only restrict or invalidate the read epoch. Missing/null
buckets, contradictory spending signals, percentages alone and reset timestamps
cannot recover a restriction. Unknown or malformed feedback cannot clear one.

Structured correlated429 or native rate/usage-limit errors stop future
submissions through durable shared feedback. Server message text is not parsed.
An `error` notification with `willRetry: true` leaves its active task and resource
grant held. A correlated turn/start RPC rejection retains the existing FAILED,
no-replay semantics. Lost/malformed acknowledgements retain UNKNOWN and charged
resources. Metadata rejection is never a reason to replay a turn. Fresh inputs
and request keys remain subject to ordinary FIFO, workspace and resource checks.

Feedback never releases a reservation or authorizes a retry. Terminal history,
owned local process/cgroup cleanup, physical GPU proof and uncertain recovery
retain their separate existing requirements. A valid old unselected bootstrap
record retains documented legacy compatibility; unreadable selector metadata
refuses instead of silently choosing fixed legacy slots.

This is a source contract, not measured operational policy. The current Codex
canary is a bounded sample, not maximum turn/context growth calibration. The
existing Router's CPU/swap settings and missing Flash peak/ownership evidence
remain separate adoption conditions. NPU and OAuth are outside the0.1.0
Codex-plus-Flash acceptance scope. No quotas, billing settings, production
limits, services, models or Tunnel bindings are changed by configuration.

The authoritative auth source is a validated native account/read on the owned
connection, not the operator's subscription label. The usage identity comes from
the separate full provider accountId, pinned as a private digest. The durable
restriction retains its original usage pin and resource generation; changing a
later policy/workspace cannot relabel an older block. A restriction recorded
without a usage pin does not gain recovery authority from a later-added pin.

Freshness uses the existing finite max_age_seconds budget (at most five seconds)
over the metadata-read bundle and a final fresh authenticated account read.
Account/rate notifications advance the shared SQLite revision. Revision and
owner/generation checks commit atomically in the canonical same-UID ledger.
Restrictions have no automatic expiry: not_before is only a cooperative backoff,
not a lease or release proof. A positive full read is a one-time causal recovery
receipt, never stored future capacity. Known schema1/2 quota evidence keeps its
original finite valid_until; feedback mode asserts no entitlement expiry.

Backoff is pinned to both wall and same-boot monotonic deadlines. A forward wall
clock change cannot skip it; a new boot starts a new finite waiting interval.
Elapsed time still cannot clear a restriction without the full causal receipt.

Pipeline metadata preparation has a charged CPU/RAM intent with no provider turn.
This permits a full recovery read after a prior rejected job has been reaped;
it does not bypass provider authorization before turn/start. The original
bundle-start timestamp is rechecked inside the
final SQLite transaction after identity/state/kernel reads; a cached freshness
boolean cannot authorize a delayed recovery commit. Nested bucket identities
must be valid and agree with their map keys before any recovery receipt exists.

Ordinary tasks and standalone online controllers/probes do not support the new
feedback policy. They refuse with resource_provider_feedback_unsupported before
claim, local metadata reservation, worker scope or native child creation; a
daemon keeps the original task QUEUED with that diagnostic. Enabling pipeline
on the same daemon does not authorize ordinary dispatch. Use the existing
pipeline task path for feedback mode. Existing schema1/2 finite-quota ordinary
execution remains supported. Supporting deferred ordinary turns needs a separate
review of immutable task/grant/dispatch lineage; a pre-turn wait must not be
implemented as terminal failure, task replay or reuse of a released grant.


Recovery is driven by a later queued pipeline task's bounded metadata read under
its existing local grant. The refused original turn is never requeued. A full
negative response continues the shared block; a favorable response before the
backoff deadline leaves the task queued. After both deadlines, only the causal
full receipt described above may clear the shared restriction, followed by the
ordinary fresh-account and local-headroom checks immediately before turn/start.
Method-not-found metadata leaves initial capacity UNKNOWN and cannot clear any
persisted restriction. Other read errors create or retain UNKNOWN feedback.
There is no timer-only recovery, operator "force clear", ledger deletion or
payment/reserve fallback. A block with no original usage pin, lost bucket
coverage, damaged state or unresolved ownership stays blocked for investigation.

The private scheduler bytes and their SHA256/provenance in the immutable bootstrap
record are the policy authority. The resource generation is the shared core's
hash of the complete validated policy plus its protocol revision. It is not the
per-account feedback revision. The latter lives in provider_feedback keyed by
provider and auth-account digest in reservations.sqlite under the OS account's
canonical .local/state/laf/resource-admission directory. HOME/XDG overrides are
not alternate authorities. All participating owners must share that ledger and
generation; active or uncertain old generations prevent adoption. No policy is
made operational merely by passing import or source tests.

The supported selection command on a newly prepared, unused prefix is:

```sh
python3 -B <NEW_PREFIX>/runtime/integration/laf_bootstrap.py configure-resource-policy \
  --prefix <NEW_PREFIX> --policy-file <PROTECTED_SCHEDULER_JSON> \
  --policy-sha256 <EXACT_RAW_BYTES_SHA256>
```

The document must contain measured/enforced local resource and ownership
contracts; this provider mode supplies none of them. Auth and usage digests must
come from the pinned owned native connection, not copied fixture values. Import
does not start an executor, load a model, attest admission readiness, change an
existing service or authorize a live cutover. Fresh process/physical observations
and any remaining calibration are still required before adoption.


Terminal restriction delivery (modified2026-11-15) uses a compact private
Pipeline outbox in the same transaction that adopts the exact acknowledged
terminal turn. History refresh and direct reap of terminal tasks both collect
matching evidence. Terminal ACKs and notifications use the same path. Pre-ACK
evidence is retained by bounded native turn identity, with a monotone restriction
bit; unrelated terminals cannot overwrite it. Unmatched identities publish no
restriction. Overflow without the matching ACK evidence retains uncertainty
until exact complete history resolves it.

Before any onward provider authorization or grant release, pending negatives
are flushed from a closed Pipeline snapshot. The shared ledger atomically
commits each restriction and its exact deterministic receipt in the existing
reconciliation audit table. Receipts bind runtime, task, grant, generation,
selection binding and native thread/turn identities. Local acknowledgement is a
separate exact-record transaction. A failed publication or acknowledgement keeps
pending evidence and grants, marks uncertainty and never replays a turn. A crash
after shared commit is idempotent: an already delivered terminal fact does not
reblock an account after valid later recovery. Live retrying error observations
remain new restrictions and are not suppressed as terminal duplicates.

Schema3 protocol11 fences earlier unpublished participants lacking this delivery
contract. Schema1/2 finite quotas retain their previous generation and enforcement.
No existing ledger is repaired, migrated or adopted by these source tests.

Matching pre-ACK retrying errors keep a monotone per-turn bit and enter a
separate receipted outbox after
the exact ACK. Their receipt cannot suppress a later terminal restriction.
Acknowledging the early error does not infer terminal execution state or clear
another uncertainty. Early-error overflow is separate from terminal overflow:
terminal null error cannot disprove an earlier lost retrying error. It retains
UNKNOWN unless an exact matching negative can be delivered. Malformed or
unsupported terminal error shapes cannot resolve uncertainty or release grants;
bounded native error metadata is checked without parsing message text.
