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

<!-- Added 2026-11-13: configurable ordinary-pool policy and source-only verification boundaries. -->

# Choose ordinary worker parallelism

[Back to source-preview limits](source-preview.md)

## In brief

- Set `maximum_sessions` to an integer from 1 through 8 in
  [`scheduler-config.json`](../runtime/private_codex/scheduler-config.json).
  This source candidate explicitly selects 8. An older valid configuration
  without the field retains 2; a missing or invalid file does not.
- `maximum_sessions` in daemon status is the pinned generation's configured
  limit, not the number of slots currently available. Memory pressure, held
  leases, unresolved work and operator/provider restrictions can reduce admission.
- A configuration edit does not deploy or resize a running daemon. New dispatch
  blocks until the policy matches again or an authorized idle adoption occurs.
  Existing workers are not killed to make a lower limit fit.

To check the source behavior without starting a service or model:

```sh
python3 -B -W error::ResourceWarning scripts/test_ordinary_session_pool.py
python3 -B scripts/test_session_pool_boundary.py
```

These are verification commands, not a runtime Quick Start or host setup.

## One policy for status and admission

The local safety ceiling of 8 is not a provider-account quota, billing entitlement
or proof that eight model turns can execute successfully. The ordinary pool is
also separate from the Codex pipeline's execution policy and native collaboration.

Booleans, fractional values, strings, out-of-range limits, duplicate configuration
fields and unreadable policy files are rejected. There is no fallback to 8.
A fingerprint of the validated scheduler and runner resource policy, including
the effective pipeline mode and daemon budget, pins each participating daemon
and worker to one generation. Status, FIFO queue-page size, child admission and
worker handoff use that generation's limit and budget. A missing, malformed or
stale worker mode is rejected rather than replaced with the ordinary budget.

An edited policy leaves status at the pinned limit and reports
`session_pool_policy_current: false` with `session_pool_policy_changed` in the
raw daemon's `blocked` reasons. No new ordinary work is dispatched under a mixture
of old and new settings. Restoring the same validated policy permits admission
again without replacing the generation.

A new generation requires exclusive runner ownership and every physical slot to
be free. A live legacy runner therefore blocks rebinding. A standalone operation
retains shared daemon admission, and cannot silently adopt an idle legacy daemon.
Workers join a daemon only through its current-generation marker and matching
handoff. This is not an automatic rolling-upgrade protocol: drain and inspect old
participants before adopting new source, rather than restarting an old binary
alongside new workers.

## Held work still counts

Admission is serialized and inspects physical slots 0 through 7, including slots
above a newly requested lower limit. Its mutex uses nonblocking `flock` attempts
with a 50 ms retry budget, separate from the task execution timeout. Exhausted
admission refuses new work without reclaiming a foreign lease; it never waits
indefinitely inside a blocking lock call. For example, an upper held slot and one
new worker already fill a two-session limit. Upper locks cannot be ignored to
admit two more workers.

Per-task and per-workspace exclusions, exclusive legacy execution and unresolved
pipeline exclusion remain in force. A terminal database label does not release a
lease. UNKNOWN or timeout/cleanup uncertainty must still be reconciled using the
existing ownership and resource evidence. Admission never deletes another
worker's lock files or interrupts it to reclaim capacity.

## Memory can block an otherwise valid limit

Before ordinary execution, admission samples Linux `MemAvailable` and requires:

```text
daemon memory cap + maximum_sessions * (runner memory cap + worker memory cap)
```

With the current unchanged caps, the configured eight-session envelope is
18.5 GiB for the ordinary daemon, or 24 GiB with its pipeline-enabled daemon
budget. The worker handoff rechecks the same effective mode-specific envelope
before authorization, rather than falling back to the smaller ordinary budget.
These are conservative admission envelopes, not memory reservations or load
measurements. The full-pool check can count already-running workers again
and block earlier than necessary. It does not reduce the configured limit or kill
existing workers.

Unknown, malformed or insufficient available-memory evidence blocks new dispatch;
the raw daemon reports `session_pool_memory_headroom`. Passing this sample does
not establish future RAM availability, aggregate cgroup headroom, GPU capacity or
an external provider quota. Existing `CPUQuota`, `MemoryMax`, `MemorySwapMax=0`
and `TasksMax` controls remain unchanged.

## Verify source first, adopt live separately

The focused checks use fake daemon/process handoffs, in-memory SQLite and owned
temporary-file `flock` leases. They cover limit validation, one/two/eight-session
bounds, refusal of extra admission, concurrent contenders, upper held slots,
legacy participants, oversized invalid policy edits, mode-specific memory
uncertainty, bounded lock contention and constructor/lease cleanup. They
also preserve task/workspace/pipeline exclusion. The adapter boundary checks
require observed valid limits to survive projection and missing/malformed limits
to remain unknown, while retaining resource redaction and read-only status.

No focused result establishes real parallel model execution, actual unit limits,
a supported host installation or a complete Python regression pass. Use
[the testing guide](testing.md) and
[the pull request checks](https://github.com/i-pp/laf/pull/12/checks) for commands,
results and the exact tested revision, rather than maintaining a fixed test total.

Before live adoption, inspect the actual daemon and worker units, fresh memory,
swap and pressure, held jobs and volatile results. Preserve or collect existing
results first, verify ownership and external account constraints, and arrange an
authorized idle transition. A source configuration, a live configuration change,
a daemon restart and successful eight-worker execution are separate outcomes.
