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

<!-- Added 2026-11-15: staged resource admission contract; not a live policy. -->

# Staged resource-led admission

[Current ordinary dispatcher](ordinary-session-pool.md)

## Scope and current boundary

The target is queued dispatch controlled by incremental resource reservations and
fresh observed headroom. Worker count is an outcome of those constraints. Actual
provider quotas, workspace exclusion, ownership, process limits, storage budgets
and uncertain-execution recovery remain independent mandatory gates.

This document defines the next implementation contract. The queue correction
retains the existing `maximum_sessions` leases, policy generation and full-pool
`MemAvailable` check. Removing them before every execution path shares the new
ledger would permit unaccounted launches. No live configuration, daemon, service,
model, runner or host setting is changed by this document.

## Request and observation contract

Each execution declares validated, finite, nonnegative local requirements before
reservation: CPU allocation in millicores, host RAM bytes, a fixed GPU device or
partition and supported compute allocation, VRAM bytes, and the existing process
and storage requirements. Bounds come from an approved execution profile and
calibration evidence, not arbitrary caller values. Unknown requirements wait for
configuration or measurement; they never become zero. A task that cannot fit the
approved total budget reports an explicit configuration blocker.

Cloud API work reserves only its local client, runner, verification and subprocess
costs plus its separate provider quota. Remote model weights and remote GPU use
are not charged to the local GPU. Local inference reserves model residency once
per owned model instance, then incremental per-execution KV cache, buffers,
compute and host costs. Shared residency is reference-counted and cannot be
released while a task, provider or uncertain owner can still use it. Unified
memory needs one reconciled accounting model so the same physical allocation is
not treated as two independent free RAM/VRAM pools. Swap does not count as free
VRAM or replace host safety reserves.

Observations identify host boot, cgroup/device, monotonic sample time, sampling
window and policy generation. They cover host and ancestor-cgroup RAM headroom,
CPU pressure and sustained allocatable CPU, device compute availability, and
VRAM availability after provider/model residency. Stale, missing, malformed,
mismatched or unavailable required telemetry blocks new work for that resource.
CPU time budgets remain separate lifetime controls. Device telemetry and enforced
compute partition limits must be verified for the selected backend before that
backend can share a GPU; unsupported sharing remains exclusive.

## Atomic admission and release

1. Use one bounded admission mutex and a durable ledger shared by daemon workers,
   standalone runners and other local execution owners that consume the same
   host budget. Preserve their existing runtime/workspace and pipeline exclusion
   locks. Pin the approved resource policy generation before reservation.
2. Under a single SQLite `BEGIN IMMEDIATE` transaction, validate the task's queued
   state, execution/request identity, current policy and fresh observations;
   inspect every unreleased reservation and the authoritative provider gate.
3. For each resource dimension, require the request to fit both the approved
   remaining budget and observed headroom after safety reserves and outstanding
   allocations not yet reflected in the sample. Track materialized usage versus
   promised growth explicitly: already-observed resident usage must not be
   subtracted twice, and unmaterialized reservations must never be ignored.
4. Commit the entire CPU/RAM/GPU/VRAM request and the applicable provider lease
   together with the one-use dispatch token before launch. Any failed dimension
   leaves the task queued, with no partial resource grant. If a provider quota
   uses a separate store, obtain a durable fenced quota lease before committing;
   reconcile ambiguous acquisition and never launch without both receipts.
5. Launch only the matching generation, task, token and owned resource scope.
   Refresh resource evidence at handoff without minting a second reservation.
   A lost launch receipt becomes retained uncertain work, never a new attempt.
6. Release only after confirmed process/cgroup and provider cleanup or explicit
   evidence-backed reconciliation. Timeout, cancellation request, dead daemon,
   expired sample and terminal database labels are not release evidence. Keep
   reservations and model references for UNKNOWN execution until reconciled.

Existing cgroup CPU, memory, swap and process ceilings remain enforcement layers.
A provider-account quota is verified for the selected account/backend, with
rate/token windows and reset evidence where applicable; a source constant or
local worker count is never treated as entitlement. Missing quota evidence blocks
that provider. Exhausting one provider/resource class need not block unrelated
eligible work with distinct budgets. No provider or billing settings are changed
by admission.

## Queue fairness and migration gates

Keep oldest-eligible ordering and the no-workspace exclusive barrier implemented
by the queue correction. Resource backfill must have an explicit aging or drain
rule so a large older request cannot be perpetually displaced by small arrivals.
Report fixed reason codes identifying the limiting dimension, provider gate or
recovery fence. Diagnostics must not include prompts, credentials, host paths or
raw provider errors and must never authorize a dispatch themselves.

Before replacing local slot admission, verify atomic contention across daemon
and standalone paths, mixed cloud/local workloads, pending launches, process
exit races, crash recovery, stale telemetry, pressure changes, exhausted provider
quotas, shared model release and unified-memory accounting. Prove that uncertain
work retains its whole reservation, new capacity cannot overcommit any dimension,
and draining releases capacity for older requests. Exercise actual supported
cgroup/device telemetry separately from fake-source regressions.

Adopt the new ledger only through an explicitly authorized idle transition with
all legacy leases, active scopes, model owners and provider requests reconciled.
The fixed local worker ceiling and full-pool envelope can then be replaced as one
versioned protocol change. Live migration, deployment and operational inference
acceptance remain separate from this local queue fix.
