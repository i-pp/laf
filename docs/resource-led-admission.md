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
<!-- Modified 2026-11-20: on-demand Flash startup and bounded pre-load queue waits. -->
<!-- Modified 2026-11-20: reconcile unused Flash residency when pre-load waiting ends. -->
<!-- Modified 2026-11-20: document the canonical KFD ownership guard. -->
<!-- Modified 2026-11-20: preserve cancellation semantics during read-only Flash retry checks. -->

# Resource-led queued admission

The optional scheduler `resource_admission` policy retains two protocols and
adds the schema3 feedback extension for pipeline and bounded ordinary execution.
Schema 1 adds an atomic resource gate to legacy slot admission. Schema 2 requires
`admission_mode: "resources"`, forbids an explicit `maximum_sessions`, and replaces
ordinary session slots and the whole-pool memory envelope with per-request atomic
CPU/RAM reservations and fresh headroom. The returned session limit is null in
resource mode. The checked-in scheduler configuration remains unchanged; this
source change does not adopt or restart a deployed service.

Modification notice (2026-11-15): new prefixes may select an explicitly
raw-hash-pinned private scheduler snapshot with configure-resource-policy.
It changes no packaged source and enables no backend. Ordinary/pipeline
normalization leaves the selected raw input unchanged. The scheduler generation
also pins the exact backend binding, including provenance; a running generation
refuses mutation. A missing/malformed bootstrap marker also refuses fresh
selection. Valid older unselected markers retain legacy compatibility; fresh
configuration alone does not attest against unsupported manual record rollback.
Preserve the accepted binding digest and mode-pinned history. Shared-ledger,
controller, quota and calibration admission remain separate.

Modification notice (2026-11-15): a Pipeline ledger pins resource mode separately
from the byte-compatible legacy policy hash. Changing modes refuses the old
ledger instead of silently adopting its tasks; preserve that history and use a
new supported runtime. An absent resource reservation is not terminal-history
evidence. Loaded turns still require matching provider thread/turn history
before history verification, and a pending grant retains its context.

## Common state and ownership

All participating host processes use one owner-private SQLite database under
the OS account's home at `.local/state/laf/resource-admission`. The location comes
from the root-owned account database, independently of HOME, CODEX_HOME and XDG.
An existing ledger at a different HOME requires explicit migration. This is a
same-UID host cohort; a different UID or PID namespace is not silently adopted.

`BEGIN IMMEDIATE` serializes acquisition of all resource dimensions, provider
intents and one-use dispatch identities. The generation pins the resource protocol as well as the policy and cannot
change while any owner or reservation is held. Earlier unpublished schema-2
semantics cannot join the conservative GPU/RAM and physical-release protocol. Owners record boot, PID namespace, PID/start time
and role. Unknown liveness retains ownership. Process death alone cannot release
a provider reservation. Tokens cannot join another runtime/task or replay a
released task. Legacy slot and exclusive-runner leases must all be idle before
the versioned lease marker can change; task/workspace/pipeline locks remain.

Daemon workers reserve before fork and join that grant after handoff. Standalone
execution reserves after workspace exclusion and before the execution claim,
and requires a verified, already-charged coordinator. Direct unbounded
standalone APIs remain refused in resource mode. The CLI execs into a fresh
private systemd scope before Runner creation for direct execution, online
reconciliation, saved-result verification, run-pair and native identity checks.
It validates the actual cgroup ceilings and private READY receipt, pins the pool
generation, and charges its CPU/RAM controller before any native work. An
environment binding alone grants nothing. Controller exit does not free its
still-populated scope; a later known-dead/empty-scope reconciliation releases only
that local controller. No task UNKNOWN or provider reservation is released.
The executing task owns every child resource scope, including a resume probe
that inspects another source task. Scope credit is unique. The daemon controller
is itself charged at its actual verified cgroup ceilings. A confirmed controller
exit can reconcile empty local-only scopes; provider grants remain held.

## Admission and provider identity

Each request must fit both the configured remaining budget and fresh measured
headroom after safety reserves and promised growth not yet materialized.
Materialized usage is credited only to its unique recorded resource scope.
CPU uses millicores and RAM/VRAM use bytes. Cloud requests explicitly use zero
local GPU/VRAM; this is workload classification rather than fabricated GPU data.
The ordinary client profile is the unchanged runner/worker cgroup ceilings.

Linux observations use paired memory samples, a persisted CPU-counter window,
CPU affinity, and remaining ancestor cgroup quota after observed use. Rapid
admission reuses only a still-fresh CPU window while sampling RAM again. An
initial CPU window, counter rollback, stale evidence, missing cgroup data or
unknown required usage blocks new admission. Swap is never free RAM or VRAM.

Provider records require explicit finite `limit`, `valid_until`, an evidence
identifier and, in schema 2, an `account_sha256` pin. An intent counts against
the provider limit before fork. It becomes a cloud turn only after a fresh native
account read matches that pin. Expiry is checked after telemetry and again at
authorization. Raw account identity and the private pin are not task/MCP output.
The selected native provider is fixed to the existing OpenAI provider, and its
effective response is checked. Account-change notifications invalidate the pin.
Missing plan/identity or expired quota evidence blocks the provider. The source
supplies no host budget, account entitlement or guessed quota.

## Pipeline integration

Resource mode requires the concurrent pipeline protocol and an explicit finite
CPU/RAM `pipeline` profile in the common policy. The built-in gateway verifies
its daemon controller before any native process. Each turn reserves a child
grant under that controller before dispatch, with an immutable task/thread/model
selection binding. All ordinary and pipeline intents share the same account
quota. Fixed pipeline task/thread/concurrency counts are not admission limits in
this mode; per-thread exclusivity, the private history byte bound, fixed sandbox,
provider checks, execution timeouts and process/cgroup limits remain in force.

The shared process stays inside the controller's unchanged hard ceiling. Child
profiles must fit observed parent CPU/RAM headroom and outstanding child promises.
They are conservatively charged against the global budget as well as the parent
ceiling; this can under-admit and requires measured profile calibration. It
cannot make the shared process unlimited. Unsupported custom gateways and
incomplete profiles are refused.

## Release and UNKNOWN reconciliation

Normal ordinary cleanup needs both positive local release facts and exact
provider terminal or never-dispatched evidence. UNKNOWN work never expires.
Pipeline completion needs the pinned account and correlated owned thread/turn
history; terminal labels and notifications alone do not release its grant. Each
reap checks at most one terminal history target with bounded RPC deadlines.
Definitive pre-turn rejection can release a never-used grant. A lost dispatch
receipt or lost token binding remains retained and never authorizes replay.

Read-only recovery probes get a separate bounded CPU/RAM grant from reserved
recovery headroom and request no inference lease. Ordinary UNKNOWN release is
an explicit same-UID paused/idle daemon operation: `resources-reconcile` requires
a fresh stored reconciliation for the exact execution, verified account and
provider terminal, current local process/scope release, and worker cleanup.
`resources-owner-reconcile` handles known-dead owners' empty local-only scopes;
provider and local GPU grants are retained. Generic dead-owner cleanup cannot
substitute empty cgroups for a GPU physical barrier. `resources-owners` is a
read-only private listing.
These actions release reservations without changing UNKNOWN, workspace decisions
or retry permission. No timeout or accepted cancellation is a release receipt.

## Queue ordering

The [eligible FIFO scan](ordinary-session-pool.md) retains its finite rowid
horizon, busy-workspace skipping and no-workspace drain barrier. Ordinary resource
profiles are uniform; a resource/provider failure drains rather than backfilling
newer arrivals indefinitely. Wait reasons are fixed allowlisted codes and do not
contain prompts, paths or raw provider errors.

## Local GPU, UMA and Router integration

The optional `local_inference` policy declares one pinned AMD PCI device,
vendor/device identity, explicit discrete or unified topology evidence with an
expiry, host/Core/Ollama controller vectors and manifest-pinned model profiles.
Each model has separate finite residency and execution vectors. Residency is
reserved once per owned instance; jobs reference that exact instance and add
incremental CPU/RAM/GPU/VRAM. Model release is refused while any job reference is
held. The existing synchronous backend and the supported exclusive GPU allocation
remain serialized provider/device constraints, independent of LAF worker counts.
Unverified GPU partitions are refused.

The ordinary and Router hosts use byte-identical packaged copies of the common
ledger core and the same OS-account state path. The host reserves verified
controller ceilings before Core or Ollama can consume the budget. CPU/RAM scope
credit and GPU scope credit are distinct, and each has one owner. Docker identity,
image, security, namespace and fixed resource checks remain the existing trusted
host operations. No Docker or host ledger authority is added to the Core image.

The AMD observer checks the configured device and driver, paired VRAM free/used
and idle observations, and bounded per-client DRM fdinfo under verified scopes.
It deduplicates client IDs and credits only private resident bytes; shared buffers
never become duplicate materialization credit. Release uses a conservative
resident upper bound instead. Process identity changes, permissions, unsupported
scope layouts, missing counters, work-budget exhaustion and expired topology
evidence block admission. CPU and RAM remain measured independently. The kernel
[DRM usage ABI](https://docs.kernel.org/gpu/drm-usage-stats.html) and
[AMDGPU sysfs ABI](https://docs.kernel.org/gpu/amdgpu/driver-misc.html) describe the
read-only counter formats; these sources do not establish this host's calibration.

Every configured GPU profile charges GPU memory against both the RAM budget
and the GPU-memory budget, and limits GPU-memory headroom by observed RAM.
Topology is still operator evidence: these native counters do not attest that a
device is discrete. The conservative coupling applies even to a discrete label
until a verified discrete path exists, so misclassification grants no extra
memory pool.
Controller ceilings and child/model vectors can overlap conservatively; measured
profile calibration is required before live adoption. No hardware capacity,
topology, model size or throughput is guessed from a source constant.

The split Router lifecycle advertises the common policy generation. Core requires
an identical generation and manifest pins before backend/runtime creation. Fixed
owner-authenticated resource operations grant model residency and then job
execution before native chat. A correlated pre-dispatch refusal leaves the
oldest job queued with a fixed reason; newer arrivals wait behind it. A lost
receipt or uncertain post-execution release never authorizes replay. Provider
stop releases local model/job grants only after the existing physical barrier
and independently empty recorded scopes. Cloud reservations are not released by
local provider cleanup.

This local route supports the explicit normal Ollama catalog and separately
pinned Core-owned Flash. Flash requires the loading/residency/execution contract
below. NPU resource ownership, GPU partitions and legacy monolithic provider
execution are not integrated; resource-only configuration refuses those routes.
A bounded read-only NPU evidence collector supports calibration preparation but
always reports global ownership unverified and never admits/releases a grant.
Existing legacy configuration and Flash/NPU service behavior are unchanged.
The new policy is not installed or enabled by this source change.

## Core-owned Flash admission

The `flash` local model profile uses engine `core` and distinct finite
`residency`, `loading` and `execution` vectors. Both loading and generation
reserve the exclusive physical GPU plus their incremental CPU/RAM/VRAM peak;
residency remains one model-instance grant. A missing loading profile cannot
borrow the generation profile or assume a zero-cost load. The existing Core
controller and all conservative RAM/GPU coupling remain charged.

In this mode Core loads Flash on demand. Startup and idle polling leave it
unloaded until a queued request needs it. Core still requires the normal startup
ownership barrier and host safety checks. When either of the two pre-load idle
samples reaches the existing VRAM occupancy limit, the request waits at the head
of the queue. The host also checks the pinned loading profile and fresh
headroom before granting the load. Cancellation or expiry of a confirmed
pre-dispatch wait ends the request without stopping the provider. A missing or
mismatched owner, profile or acknowledgement still fails closed.

When the host definitively refuses a loading reservation, cancellation or expiry
triggers one release check for that unused residency instance. The host verifies
the owner and generation, absence of held references, and paired device-idle
readings with zero owned GPU residency. Only a confirmed release clears Core's
instance record. Missing physical proof leaves the grant held. An uncertain reply
requires reconciliation through the existing lifecycle fence. If a later attempt
has already loaded Flash, the job's cancellation leaves that residency in place.

The worker and watchdog distinguish read-only Flash preparation on a retry.
Cancellation or expiry during the paired idle samples or artifact verification
ends the wait through the same limited residency reconciliation. The watchdog
continues checking hard faults in this phase. Its ordinary stop barrier applies
once another reservation may have been sent or native loading has begun.

The host matches the canonical AMD KFD class at `/sys/class/kfd/kfd` to the
character device `/dev/kfd` by device number. Mismatched or incomplete identity
refuses the GPU proof. An owned KFD descriptor leaves attribution and release
unknown because DRM counters do not account for its allocations. The observer
keeps the procfs FD enumerator open during the bounded scan so it can also inspect
the host observer's own scope. This check preserves the reservation when KFD
ownership is opaque. Hardware acceptance still requires physical release
evidence for the actual workload and separately verified controller limits.

Both pre-load samples still require total device VRAM usage below 3 GiB. A game
or another process at or above that occupancy can keep the job queued until its
deadline even when the incremental resource profile would otherwise fit.

For this mode, `/health` can report `ready` with `gpu.flash_on_demand: true`
and `gpu.flash_resident: false`. Readiness means Core can accept a request into
its queue. Actual admission is decided when the request reaches the load gate.

Core's explicit resource configuration adds `resource_flash_assets`, an exact
list of normalized absolute `path`, `bytes` and `sha256` entries. Its canonical
model digest includes the fixed Strata source/interpreter/command and sorted
asset entries. The manifest must cover the complete source/pack trees and every
absolute command/interpreter asset; symlinks, hardlinks, missing/extra inputs,
changed content, duplicate fields and bounded-inventory/deadline violations
refuse. `resource_model_sha256.flash` and the host model profile must match that
same digest. Approved assets/mounts must stay read-only and unchanged through
the calibrated execution window; this hash check is not filesystem verity or a
defence against a trusted host operator replacing assets after verification.

Core checks the manifest before reservation/native load. Its worker checks it
again before vendor Python imports and engine startup against the parent-passed
digest, so changing the configuration file cannot substitute another workload.
The loading grant precedes native creation. Matching owned engine readiness and
host-observed idle/settled residency release only that loading grant. Each
generation acquires a new job grant, sends its one-use request identity through
the native adapter and releases it only after an exact bounded terminal reply
and host physical evidence. Public results omit the private adapter binding.
Lost acquisition ACKs, mismatched readiness/terminal IDs, deadlines and unknown
cleanup retain reservations and do not retry an execution.

Normal abort retains the existing owned Core process-group barrier, followed by
the fixed Ollama stop/physical barrier. The new fixed `core_models_stopped`
operation independently requires zero owned Core GPU residency and paired
device idle evidence before releasing Core model/loading/job grants. It accepts
no PID or command and keeps the live Core controller charged. Uncertain stop
still uses the existing fixed Core emergency-stop boundary. Generic dead-owner
CPU cleanup retains loading grants just like inference/model GPU grants.
The explicit dead-Router proof path understands their exact instance/profile
references and never turns a released resource into a replayable job.

## Adoption and remaining evidence

Adoption requires a separately authorized idle transition: drain and reconcile
legacy leases, providers, model owners and UNKNOWN work; select complete measured
profiles and current provider/topology evidence; verify all participating owners
use the same generation; then exercise real cgroups, device counters and model
cleanup. Different UIDs/PID namespaces, uncharged standalone parents and unsupported
providers cannot silently join the cohort. Expired provider evidence blocks new
cloud turns; refreshing a pinned generation requires the existing idle transition.

Native GPU/UMA operation, calibrated model loading/peak/KV budgets, actual
account entitlements, real parallel inference and live mixed-provider adoption
have not been verified by the offline source tests. No service, model, runner,
security, billing or production configuration is changed. NPU resource ownership,
GPU partitions and real scope/device/profile acceptance remain unfinished.
The standalone wrapper and explicit dead-Router reconciliation below are source
implementations verified with synthetic peers; neither was exercised on live
providers or devices in this change.

[Resource calibration and controlled acceptance](resource-calibration.md) describes
the separate hardware procedure and required evidence without enabling a policy.

## Explicit dead-Router reconciliation

`python -m inference.laf_service.resource_recovery --config HOST_CONFIG --owner OWNER`
checks one known-dead registered Router owner. It never starts or stops Core,
Ollama, a model, a driver or a device. Execute it from a separately authorized,
private agentorchestrator UUID scope with the approved host controller limits;
an unbounded invocation refuses before Docker inspection or owner writes. The
CPU/RAM profile must fit the configured recovery reserve. The command charges
its own scope, including a dry run, and leaves that diagnostic grant held until
its process and scope are positively gone.

Both existing generation/supervisor locks must be available and stay held.
Exact fixed Docker ownership/security inspection must show both providers
stopped, PID zero, no restart in progress and no automatic restart. Every stored
local scope must be empty or absent. Paired fresh readings of the pinned AMD
device must show no compute activity and satisfy the existing physical idle,
RAM, VRAM, swap and quiet PSI barriers. Expired calibration is never admission
authority; fresh pinned kernel identity may still prove release of an old grant.

The command fingerprints the exact owner, grants, model instances and references.
It checks fresh kernel/fence/scope evidence again inside one bounded SQLite
transaction. `--apply` atomically releases those local grants and records a
per-grant audit proof only if the candidate is unchanged. Provider grants remain
held, UNKNOWN tasks are unchanged, and no replay becomes eligible. A refused or
lost operation leaves uncertain reservations charged. A dry run leaves all
target grants unchanged. Different ownership, namespace, device, scope, stale
telemetry, conflicting locks or ongoing provider work requires explicit operator
investigation; there is no forced recovery switch.

## Offline validation

Run `scripts/run_integrated_source_checks.py` for the strict source suite and
the focused `test_resource_admission.py`, `test_resource_only.py` and
`test_resource_pipeline.py`, `test_resource_review.py` and
`test_local_resource_admission.py`, `test_resource_completion.py` and
`test_flash_resource_admission.py` and `test_npu_resource_evidence.py` modules for resource boundaries. Fixtures use private
temporary SQLite/leases, synthetic telemetry and a fake native peer; no model,
service or external account is invoked. Test cardinality lives in the executable
refusal guard, not in this specification. Passing source fixtures is not a claim
of real parallel model execution, profile calibration or live adoption.

Modification notice (2026-11-15): [provider feedback](provider-feedback-admission.md)
adds an explicit schema3 submission contract when no actual provider concurrency
entitlement is exposed. It retains local physical admission and known quotas.
