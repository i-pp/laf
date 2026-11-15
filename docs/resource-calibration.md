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

<!-- Modified 2026-11-15: versioned resource-only cloud and pipeline admission. -->

# Resource calibration and controlled acceptance

Added 2026-11-15: a staged procedure for the opt-in resource admission candidate.

The model/provider steps below are prepared and remain unexecuted by this source
implementation task. Bounded passive kernel metadata observations on EVO-X3 are
recorded separately from source fixtures and do not establish model calibration.
No example capacity or quota below is permission to enable resource mode.

## Coordinate the ownership window

The installed-plugin acceptance and old-service cutover owners retain their
worktrees, runtime paths and service ownership. Select an isolated candidate
checkout and private configuration/runtime for this procedure. Keep the accepted
source commit, policy hash, image IDs and device evidence together. Do not reuse
another task's mutable worktree, dispatch token, model instance or receipt.

Inventory the current generation locks, live owners, provider identities and
UNKNOWN tasks first. Read-only inspection may proceed while models are running.
A live owner, conflicting lock, held provider turn or unknown execution outcome
does not authorize stopping anything. Request a separate operator-selected idle
window for model load/unload, provider restart, fault injection and policy
adoption. Do not run those steps while the installation/cutover owner is working.

## Read-only evidence before an idle window

Record the kernel boot and PID-namespace identity, source commit, OS UID,
cgroup-v2 controller availability, ancestor CPU quotas, CPU affinity and memory
limits. Collect paired CPU counters with monotonic timestamps; unknown, stale,
rollback or incomplete readings must refuse admission. Record RAM availability,
swap usage/I/O, memory PSI and filesystem budget without counting swap as RAM.

Pin the actual AMD PCI BDF, vendor/device IDs and driver. Record VRAM counters,
busy readings and per-owned-scope DRM fdinfo. Prove UID/start/cgroup identity for
the sampled clients. Missing permissions, ambiguous device identity, shared
buffer ownership, descendants or over-budget scanning is unknown. Confirm that
the selected scope layout matches the implementation; do not broaden accepted
cgroup paths to make a failed reading pass. Couple every GPU byte to RAM in
this candidate even if a device is described as discrete.

Record the exact existing Core/Ollama image IDs, immutable model manifests,
security configuration and hard CPU/RAM/PID/swap limits. A controller profile
must cover the actual verified ceiling. Source-only configured names, file size,
marketing memory or a successful health response do not establish peak cost.

For cloud turns, obtain the actual provider's current concurrency entitlement
and an expiry/reference for that evidence. Bind it to the exact account read by
the native protocol. Do not infer entitlement from model effort, local RAM,
subscription name or an old observed successful turn. Keep the private account
fingerprint and receipts outside public artifacts; publish only redacted results.

## Measured profiles in the authorized idle window

With the existing owner explicitly drained, capture an idle baseline and verify
the existing physical stop barriers. For each immutable model, measure load
peak, settled residency, worst permitted context/KV growth, output limits and
unload/stop behavior under the unchanged hard cgroup caps. Include compiler,
tokenizer, SDK, mapping/page-cache and transient buffer costs. Retain the maxima
and an explicit safety margin in the approved finite vectors; a crash or ceiling
hit yields an insufficient profile, not a larger automatic limit.

Separate the controller, model residency and incremental execution demand. A
model is charged once per verified resident instance; jobs reference that exact
instance. GPU compute remains an exclusive physical-device demand until genuine
partition isolation is independently verified. No arbitrary LAF worker count is
derived from these observations. Admission uses remaining vector budgets plus
fresh headroom minus safety reserve and unmaterialized promises.

For the implemented Flash source path, prepare its complete immutable
asset/command manifest and separately calibrate loading, settled residency and
incremental execution. Verify both Core and worker hashes, grant-before-load,
exact terminal request identity and owned-process/GPU stop evidence. The source
path has only synthetic adapter tests; no real Flash profile is approved here.

NPU still needs a verified device owner, native execution evidence, peak CPU/RAM
and a positive device release contract. The current source refuses resource mode
for that route. Preserve the refusal until complete integration and hardware
proofs are accepted; a quiet CPU process or an empty ledger is not NPU idleness.

The [NPU evidence collector](../inference/laf_npu_service/README.md#read-only-resource-calibration-evidence)
is implemented as a separate bounded, read-only preparatory step. In an approved
reader scope it can observe the existing owned NPU service without restarting,
loading or stopping it. Pin the actual PCI/firmware/evidence and reader ceilings
in its private policy; use the actual owner's flat cgroup. Missing permissions
or ambiguous metadata remain unknown. Preserve its output privately with the
source/configuration hashes. Its allocated-byte and engine-counter observations
are not resource credits or global device capacity. Its result always refuses
inference admission, even when quiet or empty. NPU common-ledger ownership,
Router binding and positive UNKNOWN/device recovery still need implementation
and proof; do not enable the resource-only NPU route from this evidence alone.

## Bounded standalone and stopped-Router checks

On the isolated candidate, exercise the CLI standalone wrapper with the new
policy only after an idle generation adoption. Verify the command's actual UUID
scope, CPU/RAM/PID ceilings, zero swap allowance, private READY receipt and
controller grant before a native operation starts. Confirm that direct unbounded
Runner use, a forged marker, stale generation, unavailable systemd or changed
limits refuse without an unbounded fallback. Confirm that completed child/task
grants release only with exact terminal and local cleanup evidence and that the
coordinator remains charged until its own scope exits.

For an already-dead Router owner, prepare its exact read-only command from the
candidate root:

```sh
python3 -B -m inference.laf_service.resource_recovery --config /ABSOLUTE/PRIVATE/HOST_CONFIG --owner OWNER_UUID --plan
```

Replace the path/owner placeholders with the verified private values. `--plan`
only prints the proposed bounded argv and working directory. It does not open
the ledger, inspect Docker, claim locks or execute the command. Review the exact
source/configuration and keep the printed unit unique. The printed target-mode
field states whether `--apply` would be included.

During the separately selected idle window, run the printed dry-run argv with
the existing owner locks free and both providers already stopped. The tool must
refuse a live/unknown owner, running/restarting provider, populated/ambiguous
scope, different device, busy GPU, stale or moving swap/PSI evidence. It never
stops providers to obtain a passing result. A dry run preserves the target
grants, while charging its diagnostic process from reserved CPU/RAM headroom.

Inspect the unchanged target grants and redacted proof, then prepare a separate
`--plan --apply` command for explicit local release if authorized. Compare the
exact owner/generation/candidate again. Confirm atomic local release and retained
provider UNKNOWN grants. No task state or automatic retry changes. Preserve
audit evidence even on failure. The diagnostic's own grant is reclaimed only
after its known process and cgroup have exited.

## Acceptance and recovery boundaries

Exercise ordinary cloud work and local inference together using finite approved
profiles and real quotas. Show that eligible queue entries progress, competing
resource vectors wait with fixed reasons, FIFO barriers remain, and workspace
or UNKNOWN ownership still blocks unsafe overlap. Check rejection at each
dimension boundary and changed/expired provider or device evidence. A lost ACK,
timeout, owner crash or missing kernel observation must retain grants and never
replay a turn. Any injected crash is limited to newly authorized disposable
owners; do not kill, stop or alter a running user model for this procedure.

Only after hardware/profile/provider/ownership checks pass may the integration
owner propose a new resource-only generation without `maximum_sessions`. Keep
the existing legacy configuration until that decision. Rollback requires the
same idle transition and retained evidence; never drop the common ledger, edit
HELD rows, erase UNKNOWN tasks, enlarge caps automatically or disable a guard.
Record failed, unexecuted and blocked cases alongside successful evidence.

### Counterless accelerator descriptors

A descriptor for the pinned accelerator can exist without any DRM usage fields.
The NPU observer checks the sysfs device number and descriptor identity, and
refuses such a sample with `resource_npu_counters_unknown`. Zero visible owned
clients is never a global-idle or release receipt. On the inspected EVO-X3
`amdxdna`/firmware `1.1.2.65` interface, the existing native worker had a matching
accelerator descriptor with only generic fdinfo fields. That interface does not
establish the required NPU usage or physical recovery contract. A bounded
read-only observation does not validate native loading, model peaks, shared NPU
admission or positive UNKNOWN recovery.

### GPU handle visibility

The GPU collector also binds counters to actual pinned sysfs DRM device numbers
and rejects owned DRM descriptors without matching counters. It refuses KFD
handles because per-device KFD allocation and physical release are not proven
by this implementation. This may prevent resource-mode Flash/ROCm admission or
release even when model code reports completion. Retain those grants and the
existing legacy service until a separately verified KFD contract is implemented;
never discard a handle, claim zero use, or relax the guard to force acceptance.
