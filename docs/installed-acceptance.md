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

# Verify the installed LAF package against existing backends

Added 2026-11-15. This procedure exercises an existing owner-host installation.
It does not install dependencies, provision models, start/restart services or
replace a complete clean-host Quick Start.

Use the [new-prefix configuration](bootstrap.md) or the
[existing-owner configuration](integrated-plugin-install.md) first. Select the
existing SDK 1.30.0 and the real operator-owned Router. Optional Codex uses the
normal guarded daemon and the operator's existing authorized account. Run the
bootstrap admission check before attempting a new daemon. Retain ownership,
isolation, resource and uncertainty guards.

Build a new source-only archive and use the supported
[Codex marketplace installation](codex-install.md#codex-local-marketplace-option)
with a new isolated HOME/CODEX_HOME and plugin cache. Activate the new private
HOME/.config/laf/plugin.json there. Preserve global configuration and existing
runtime state. Record the installed cache path, archive hash and native version.
Check the actual loader's plugin/installed and mcpServerStatus/list results.
Installation or 33 schemas alone is not backend acceptance.

The runner below uses the installed .mcp.json command through an actual MCP
SDK client. It checks all archive/cache bytes, copied runtime source, version
0.1.0 and all 33 input schemas/annotations before calling a tool. This SDK
client test is separate from the Codex loader check; it is not a model-driven
outer Codex turn. Keep both pieces of evidence.

The paths are explicit operator selections, not portable defaults:

    SDK_PYTHON="/absolute/existing/sdk/bin/python"
    ACCEPT_HOME="/absolute/new/acceptance-home"
    INSTALLED="/absolute/isolated/plugin-cache/laf"
    ARCHIVE="/absolute/new/laf-0.1.0-source.zip"
    EVIDENCE="/absolute/private/acceptance-evidence"
    RUNNER="/absolute/reviewed/source/scripts/laf_acceptance.py"

    "$SDK_PYTHON" -B "$RUNNER" inspect --archive "$ARCHIVE" \
      --installed-root "$INSTALLED" --home "$ACCEPT_HOME" \
      --journal "$EVIDENCE/inspect.jsonl"

The evidence directory must already be owner-only 0700. Journals are private
0600 append-only files with an exclusive process lock. Redirect stdout/stderr
to new private files and retain exit statuses. These can contain model outputs
and local locators; do not commit or publish them. A zero inspection exit means
observations were recorded, not that backend execution passed.

Before inference, coordinate with the service owner and verify readiness, an
idle queue, no active job, ownership and a quiet resource window. The runner
checks Router readiness and the allowlist again. It never recovers a latch,
restarts a service or repairs a refusal. Router submission may start/switch
providers: select only the exact model authorized for this window.

    "$SDK_PYTHON" -B "$RUNNER" local --model exact-approved-router-id \
      --archive "$ARCHIVE" --installed-root "$INSTALLED" --home "$ACCEPT_HOME" \
      --journal "$EVIDENCE/local.jsonl"

    "$SDK_PYTHON" -B "$RUNNER" codex --workspace exact-approved-workspace \
      --model gpt-6.1-sol --effort max \
      --archive "$ARCHIVE" --installed-root "$INSTALLED" --home "$ACCEPT_HOME" \
      --journal "$EVIDENCE/codex.jsonl"

The local canary requests 16 output tokens, a 30-second Router deadline and a
45-second client wait. Codex requests one harmless response with a 160-second
client wait; its configured daemon deadline remains authoritative. Complete
startup plus one action is bounded by 200 seconds. Before new work, the runner
fetches the provider catalog and refuses an unavailable model/effort. Here max
is the exact advertised string, not an alias or entitlement claim. Stored
selection and acknowledged context/turn forwarding are verified separately.
Provider internal effort, billing and quota are not verified.

Each journal pins one candidate/configuration and reserves random identifiers
before dispatch. Reuse the same journal and command to reconcile interrupted
work. A lost Router admission response is followed only by GET of the original
handle. A lost Codex task response is reconciled by the server's original
immutable request key and exact arguments, before fresh catalog discovery. If
the first request never committed, the same original operation can be admitted;
the server never dispatches a duplicate key twice. No new key is generated.
The runner never infers task ownership from matching content or a thread list.
Unknown receipts and conflicting arguments stop the run. Do not create another
journal to retry uncertain work.

Cancellation is a separate action using the same journal:

    "$SDK_PYTHON" -B "$RUNNER" cancel --archive "$ARCHIVE" \
      --installed-root "$INSTALLED" --home "$ACCEPT_HOME" \
      --journal "$EVIDENCE/local.jsonl"

The runner records acknowledgment and a subsequent observation. It leaves
physical_stop_confirmed false: acknowledgment is not physical stop or resource
release. MCP disconnection also does not cancel a job. Codex completion requires
the daemon's executor-release confirmation and a clear reconciliation state.
The owned thread, ledger, cache, prefix and journal are retained. Confirm
terminal state and physical release through normal operator procedures before
any cleanup; the runner performs no destructive cleanup.

When a Codex submission response has no known task ID, cancellation refuses
rather than submitting work just to obtain a target. Explicitly reconcile the
original operation first. An authoritative task ID returned in an error reply
is retained for observation/cancellation, while the error still prevents a
successful acceptance claim.

Offline CI uses synthetic peers and temporary files to test this logic. Those
tests do not prove installation, native loading, an account or a model. Keep
source/offline, actual loader, real-account and real-model results distinct.
The four legacy tools retain their separate compatibility boundary; a Router
canary does not establish that all 33 tools work.
