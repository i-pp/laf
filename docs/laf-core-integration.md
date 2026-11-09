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

# LAF P0: opt-in Core integration and fixed host supervision

## Component boundary

This source adds a separate Core backend and entrypoint, a host-only supervisor,
and an explicit Core image source allowlist. The legacy host Router remains the
default. Selecting the separate Core entrypoint does not itself change or restart
an existing service. GPU/NPU and clean-host runtime acceptance remain separate.

Core has no Docker CLI/API integration. The host helper controls only its
configured owned Ollama container and, for emergency cleanup, its configured
owned Core container. It never accepts client commands, paths, mounts, images,
container names, arbitrary URLs or host PIDs.

The following are focused checks, not the full regression suite. Run from the
source root with Python 3.10 or later:

```sh
python3 -B -m unittest \
  inference.aceis_service.test_lifecycle \
  inference.aceis_service.test_lifecycle_uds \
  inference.aceis_service.test_host_supervisor \
  inference.aceis_service.test_core_integration -v

# On an executor allowed to create private Unix sockets, include both opt-in
# supervisor/Core-entry tests, the kernel-credential suite and legacy Router:
LAF_TEST_LIVE_UDS=1 python3 -B -m unittest \
  inference.aceis_service.test_lifecycle \
  inference.aceis_service.test_lifecycle_uds \
  inference.aceis_service.test_lifecycle_uds_live \
  inference.aceis_service.test_host_supervisor \
  inference.aceis_service.test_core_integration \
  inference.aceis_service.test_service -v
```

These are stdlib fake-provider tests. They do not run Docker or load model assets.
They include real owned Flash-like subprocess groups, but token generation and
host physical-resource observations are fake. The first command does not enable
the opt-in live socket tests. Use [the root README](../README.md) for the complete
portable/socket/full software-test groups and result accounting. These checks
are not a runtime Quick Start or evidence of container/model acceptance.

## Selection and compatibility

`inference.aceis_service.core_server` is the new explicit entrypoint. The existing
`inference.aceis_service.server` still selects its legacy host backend. Its only
integration includes moving that backend import into its host-only `main`, so the
Core image can reuse the public HTTP handler without shipping direct Docker code.
The candidate also adds explicit model catalogs and optional Flash behavior to
Controller, and health/model advertisement to the shared server. Default legacy
profiles retain Flash plus the two production large-model IDs. Existing job,
cancellation, recovery and NPU route contracts remain covered by regression
tests. This is more than an import-only change and needs the combined suite.

Core configuration is an owner-only mode-0600 file with container-native paths.
Host authority fields such as generation lock, Docker network, image, source
weights and container identity are rejected in this Core configuration. Paths to
Strata/tokenizer/model assets still need a separately reviewed runtime layout.
The image source does not install or redistribute those runtime/model bytes.

`run_core` is the production startup path behind the CLI; trusted Python factory
injection makes that path testable without changing CLI/config authority. It
starts the existing resource watchdog during startup, takes a new physical stop
barrier before loading, verifies model preparation, starts Flash and then exposes
the owner-only public Unix socket. Existing socket entries are refused, not
replaced. Shutdown retains ownership until provider barriers finish.

## Host resources and process namespaces

Every Core physical snapshot used for admission, idle detection, Flash baseline
and model unload comes from `LifecycleClient.read_resources`. There is no local
`/proc/meminfo` fallback. The unchanged Controller uses that reader too. Samples
are authenticated and bound to the host epoch; an epoch change latches rather
than silently resetting a baseline.

Before starting or signaling Flash, Core checks that its UID maps to the host
owner and its PID namespace differs from the host supervisor. Its `/proc` view
must match its own PID. The host must advertise a fixed Core emergency-stop path.
A missing capability, shared PID namespace or changed process namespace fails
before normal provider execution. No host PID is sent across the protocol.

Flash runs in a new Core-owned session/process group. The Core worker announces
its engine PID before model readiness. Core binds wrapper/engine identity to UID,
process start time, session and process group, rather than wrapper liveness alone.
It can stop a live engine after its wrapper exits, but never signals a reused or
unconfirmed group. A disappearance between scan and identity probe is rechecked.
Pipe-reader shutdown is part of cleanup. An unconfirmed Flash stop asks the host
for fixed Core-container termination; it does not guess a host PID.

Model switching retains empty `/api/ps` plus physical VRAM within 512 MiB of the
Flash-only baseline and available RAM within 2 GiB, never below 8 GiB, twice.
Regular abort first stops the owned Flash group, then requests host Ollama stop
and the inherited two-reading absolute GPU-idle barrier below 3 GiB. An HTTP
response or container liveness is never substituted for physical release.

## Host supervisor and emergency cleanup

See [fixed host policy](laf-host-supervisor.md) for schema and fail-closed example.
Startup holds the existing historical generation lock without truncating it and
an owner-only singleton lock. It refuses already-running unadopted Core/Ollama
containers. It creates the two private authenticated UDS lanes only after these
checks. No installed unit or existing service is modified by this source work.

The Core stop adapter requires exact configured name, image digest, owner label,
UID/GID, mounts, devices, internal network and positive memory/no-extra-swap,
CPU and PID caps. It rejects privileged mode, host PID/IPC, published ports,
extra capabilities, writable root, restart policies and Docker socket exposure.
It can inspect/stop this fixed identity, never create/start a caller-selected
container. A mismatch is an error, not permission to stop an unrelated target.

Host resource monitoring and expiration of the Core sample lease trigger a
sticky safety fence before cleanup waits. Emergency cleanup stops and verifies
Core first, so Flash dies within its own container namespace, then stops Ollama
and confirms the physical barrier. Unknown cleanup is safely re-inspected and
retried while admission stays fenced. A successful ordinary client stop cannot
clear this safety fault. Fresh samples are rejected after the fault.

Panic admission checks identity and establishes its fence atomically. Shutdown
also fences and drains old handlers before releasing ownership locks; stale
callbacks cannot act after a replacement supervisor takes over. Lock files
persist to prevent split-inode lock domains. No wire reset of host safety exists.

Client I/O and queued operation waits have absolute deadlines. Arbitrary hung
Python/kernel/provider calls do not acquire hard real-time guarantees from this
code; independent process supervision remains required for that fault model.

## Image boundary

`inference/core_container/Dockerfile` copies ten named Core source files only.
The offline build wrapper accepts an already-local immutable base reference and
creates a temporary allowlisted build context. The full repository, private
assets, host adapter, direct-Docker backend, tests and host configuration are not
sent to the daemon. The wrapper explicitly targets the local host daemon.

No base/runtime image was selected as a distributable LAF release. A disposable
network-none test container with no GPU or Docker socket can validate process
namespace/socket behavior without loading models; this is narrower than running
this production source image with its real runtime assets. Inspect exact evidence
before treating either as completed. Build, registry upload, deployment and
service replacement are separate actions and are not authorized by a test pass.

## Remaining acceptance

Real Strata/tokenizer dependencies, GPU devices, pinned complete runtime image,
model-switch physical release, NPU independence, resource pressure under real
inference, Core/supervisor/Docker failure cases, reboot and clean-host acceptance
remain to be executed before production selection. This work establishes no
safety certification or redistribution rights. Rollback is to leave the new
entrypoint unselected; no deployed service needs restoration.
