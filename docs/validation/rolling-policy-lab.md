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

# Rolling-policy validation scope

The optional split-host rolling policy was exercised in an isolated Linux lab.
This record is not acceptance of a complete inference deployment.

## Observed

- The production HostSupervisor implementation served real owner-only Unix
  sockets. A separate Docker PID namespace ran the LifecycleClient protocol path.
- The client observed the same versioned policy, completed a new-generation stop,
  and started a separately owned stand-in provider container.
- Injecting low-free-swap telemetry into the lab authority stopped the two lab
  containers through the existing ownership-checked stop barrier.
- Restoring real telemetry and completing a real quiet recovery interval produced
  a new epoch. Neither container restarted automatically.
- Only containers and an internal network created for this trial were removed,
  after identity/ownership checks. Their removal was checked again. The existing
  inference and MCP services retained their process identities and active state.

The source was copied through the Core image build-context allowlist and mounted
read-only into an existing immutable local image. This was not a new production
image build. Preparation found and corrected the missing `swap_window.py` entry
in the Dockerfile and build-context allowlist.

## What this does not establish

The Core-side program exercised LifecycleClient, not the complete `run_core`
application. The provider was a bounded sleep-process container, not Ollama or a
model. The pressure violation was synthetic; the trial did not create real swap
pressure or calibrate model throughput. No model/GPU operation, existing service
switch, authentication change, `swapoff`, cache dropping, or kernel tuning was
performed. The prior frontend installation record retains its original source
scope and is not a whole-package rerun for this successor.

Pure and threaded regressions cover policy/epoch agreement, admission and provider
checks, invalid telemetry, ownership cleanup, interrupted recovery, cooldown,
quiet duration, topology changes, and concurrent emergency fences. Their outcomes
and the complete source checks belong in GitHub CI, not a prescribed test total.

## Next controlled trial

1. Capture passive RAM/VRAM/free-swap, both paging directions and memory PSI traces
   under the intended workload. Passive idle observations alone cannot select
   production rate/window thresholds.
2. Agree on a dedicated model/service test window before exercising real load;
   retain the existing runtime and rollback configuration. Do not run a second
   large model alongside the resident model merely to force swapping.
3. Start with one short bounded development request. Use explicit finite window,
   per-direction I/O, free-swap, quiet and cooldown values derived from the traces.
   Some occupied swap is acceptable; low reserves or sustained pressure are not.
4. Confirm the same policy at Host/Core, monitor the complete request and owned
   cleanup, and stop rather than retry on unknown outcomes or persistent pressure.
   Keep an explicit owner recovery and a separate decision to restart.

Large swap capacity is not a throughput guarantee. Actual model acceptance,
representative load calibration and long-duration operation remain open in
[Issue 13](https://github.com/i-pp/laf/issues/13).
