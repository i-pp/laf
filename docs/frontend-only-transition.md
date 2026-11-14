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

# Updating an existing integrated frontend without restarting inference

This operator utility updates one already owned transient stdio tunnel. It is
not a LAF installer, a complete Quick Start, a production-readiness claim, or a
replacement for the existing service manager. It imports an operator-selected
existing recipe rather than distributing that recipe or private machine data.

## Scope and prerequisites

The current contract requires an existing `Services` recipe with owner/runtime
validation, preflight checks, a shared operation lock, invocation ownership,
atomic state replacement and a tunnel-only `launch` method. The operator plan
pins that recipe's Git commit and clean tracked bytes, the reviewed source ZIP,
a private staged source tree, an existing SDK interpreter, existing capability
flags and exact observed tunnel properties. Runtime plans/journals do not belong
in Git. No credentials are read: the old recipe retains its credential-file
reference, environment filtering, registration, resource caps and dependencies.

Run only through an authorized normal owner execution environment. An owner
ancestry, permission, socket or bus refusal is not an invitation to alter guards,
replace bus environment variables or retry through an undocumented route.

## Operator sequence

1. Review the concrete plan, source/provenance and interruption scope. Preserve
   results if any service that holds volatile data is actually in scope.
2. Run `python3 -B ops/frontend_cutover.py inspect --plan /absolute/private/plan.json`.
3. After cutover authorization, run `apply` with the same plan and an explicit
   new `--journal /absolute/private/transaction-directory`.
4. Verify the actual connected client's existing read-only Codex operations and
   refreshed tool catalog. Local transport readiness is not client acceptance.

`apply` backs up the ownership state and properties, stops only the recorded
owned tunnel, launches the replacement with the old recipe's unchanged limits,
and updates only its invocation in the existing state. The daemon and inference
providers are not stopped, and jobs are never replayed. A missing fresh health
locator is a bounded startup condition; permission failures are not retried.
The 20-second stop plus 75-second readiness decision budget is not an outage SLA
or a hard-real-time guarantee. Host scheduling, I/O and rollback can take longer.

## Failure and recovery

A failed/uncertain operation is not automatically retried or rolled back.
Inspect the journal and current manager generation first. `reconcile` may only
mark the exact recorded replacement ready after matching owner/invocation,
unchanged daemon and current transport health. It never adopts an unknown unit
or performs another service operation. `rollback` stops only the known
replacement and recreates the exact recorded old tunnel command; it does not
stop the daemon, inference, model providers or unrelated HTTP service.

Journal updates flush a new private file, commit it atomically and fsync the
parent directory. If directory fsync fails after replacement, visibility and
crash durability differ: inspect both state and journal, do not assume either
that nothing happened or that the old record remained. This is not a power-loss
or distributed exactly-once proof. The two records are not one atomic transaction.

A transient unit can disappear on stop; bare `systemctl start` is not an adequate
rollback recipe. The older manager's general `start`/`stop` may manage a whole
service pair and later recreate its original frontend. Keep the recorded
transaction recipe authoritative for this transition and revalidate before
future operations.

## Cached tool definitions

A backend restart does not prove a client's tool catalog changed. Open the
existing app's details page and refresh that app to pull new tool definitions;
[the official custom-MCP guide](https://developers.openai.com/api/docs/guides/custom-mcp-server#how-to-use)
describes this operation. Do not create another app, expand authentication,
publish a private binding, or reset permissions just to force a refresh.

## Verification

`python3 -B ops/test_frontend_cutover.py` runs 28 fake-only cases. They cover
ownership drift, exact capability inheritance, one-shot failures, state and
journal uncertainty, rollback, atomic record preservation, bounded missing
locator handling and reconciliation without restart. They create only private
temporary test files. They do not invoke a real manager, socket, SDK child or
model. An authorized normal owner environment is required by the real filesystem
guards. Keep separately measured operational evidence separate from these tests.
