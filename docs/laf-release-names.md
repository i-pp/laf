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

<!-- Added 2026-11-20: require LAF-only names and document migration of the integrated runtime. -->

# LAF-only naming gate for 0.1.0

Release 0.1.0 requires LAF names across the candidate source, distribution
archives and fresh installation. Inspect MCP tool names and descriptions,
package and UI labels, file and socket paths, configuration keys, environment
variables, and enabled systemd units. The check is case-insensitive. A hidden
tool, compatibility alias, or old active registration still fails the gate.
The existing functional, resource, persistence, and Tunnel acceptance gates
also apply. Keep legally required notices and archived history. The scanner
allows only the exact preserved notice and history bytes to carry the retired
name in the candidate.

## Check the deliverables

From the candidate's repository root, using a new output path outside the tree:

```sh
python3 -I -B scripts/check_release_names.py --checkout .
python3 -B scripts/build-integrated-source.py --output /absolute/new/laf-source-0.1.0.zip
python3 -I -B scripts/check_release_names.py /absolute/new/laf-source-0.1.0.zip
```

The source builder checks paths and content before writing its ZIP. Run the
checker on the exact artifact to be shipped, including any separately built
review package. For a non-ZIP distribution, check the artifact's basename and
an expanded private staging copy. The checker does not unpack nested archives.
Record the candidate commit, artifact SHA-256, and check results.

Check a new private installation prefix and supply the observed active names
from registration, environment-key, unit and socket inventories:

```sh
python3 -I -B scripts/check_release_names.py --names-only /absolute/fresh/laf-prefix \
  --name laf-inference.service --name LAF_MCP_EVENTS_ENABLED --name laf_router_status
```

Use the actual inventory for acceptance. Names-only mode reads file and
directory names and symlink targets without reading credentials or runtime
records. Statically lint the shipped unit and configuration templates. Record
the actual local Codex loader's 29-tool catalog and the visible LAF name of any
separately deployed App or remote entry. Source CI cannot inspect those live
surfaces. Unknown or stale registrations need resolution before release.

## Existing installations

1. Inventory the original service owners, registrations and config files.
   Keep restorations outside the candidate prefix. Preserve the exact package,
   units, bindings and owner-controlled state in a private backup. Identify
   active jobs, unresolved attempts, leases, and pending notification delivery.
   Pause any later switch until owners can reconcile them through their original
   runtime. Keep UNKNOWN reservations and receipts intact.
2. Stage an independent prefix with the new package and the supported bootstrap
   commands in the [installation guide](integrated-plugin-install.md). The
   former helper capabilities and four operations are absent; use the supported
   `laf_router_*` contract. Generate the new four-capability configuration
   through setup. Review the `laf-inference.service.example` and Router
   configuration for the staged owner. Keep owner binding, explicit model
   allowlists, resource limits, and shared-ledger generation checks in force.
   Start with read-only discovery in an isolated session.
3. Configure notifications only if supported and needed. The new environment
   keys start with `LAF_MCP_EVENTS_` and `LAF_OAUTH_`. Keep the reviewed
   issuer, audience, scopes, principal and key sources. The new outbox uses a
   separate file and stamped namespace for LAF event, owner-hash and encryption
   identifiers. Retain older outboxes, sidecars and keys for their original
   owner. Renaming or copying an old outbox cannot migrate its identity domain.
   Complete authorized delivery or keep the old records retained for review.
4. In a separately authorized maintenance window, verify quiescence and stop
   only the recorded owners through the supported stop barriers. Start the
   staged LAF owners with verified limits and the approved resource policy.
   Check the actual tool inventory, installed readiness and a bounded canary.
   Adopt the registration or remote binding through its normal installer or
   [Tunnel procedure](installed-acceptance.md). Retire old active registrations,
   units and displays only after acceptance. Preserve unrelated services and
   other MCP entries.

If a switch fails, stop new submissions and reconcile the staged tasks, receipts
and resources first. When the new owners are stopped and release is confirmed,
restore just the backed-up registration and configuration entries, comparing
their current values to the staged changes to preserve intervening user edits.
Restore the original unit references and pinned prefix and check readiness.
If ownership or release is UNKNOWN, retain both records and the reservation and
escalate to the owner before re-enabling execution. Never copy an active ledger,
force a generation change, or replay a task to roll back.

Modification notice (2026-11-20): removal of the retired helper from the
integrated profile and both adapters, LAF names for OAuth/notifications with a
new outbox namespace, release-name checking for repository, ZIP and install
paths, and related setup, acceptance, and packaging updates.
