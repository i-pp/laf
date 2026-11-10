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

# Opt-in host supervisor integration

This is a local P0 implementation, not an installed service or a verified deployment. It does not migrate, adopt, or stop an already-running legacy service during construction. No Docker commands in its tests reach a real daemon.

## Safe verification

From this checkout, run the deterministic tests:

```sh
PYTHONPATH=inference python3 -B -m unittest laf_service.test_host_supervisor -v
```

On an authorized Linux test host that permits temporary AF_UNIX sockets, the same fake-provider suite can additionally exercise actual owner-only endpoint creation and cleanup:

```sh
LAF_TEST_LIVE_UDS=1 PYTHONPATH=inference python3 -B -m unittest laf_service.test_host_supervisor -v
```

The deterministic checks use injected lane doubles. Actual socket tests require an executor that permits private AF_UNIX sockets and Linux peer-UID queries. Use the explicit portable/socket/full groups in [the root README](../README.md) for aggregate results; these focused checks do not establish production readiness, GPU performance or behavior of a deployed Docker daemon.

## Host configuration contract

The explicit entrypoint is `python3 -B -m laf_service.host_supervisor --config /absolute/host-config.json`, with `inference` on `PYTHONPATH`. This is only an entrypoint, **not a complete Quick Start**. Before using it, the operator must separately establish the pinned images, model files, historical lock, private runtime/state directories, existing internal bridge network, GPU/device mappings, and an authorized stopped Core container. No provisioning or service installation is provided here.

The config must be an owner-only regular file with no symlink path components. The generation lock must already exist as an owner-only regular file; it is locked without truncation. Runtime and state directories must already exist, owner-only mode 0700. The private `supervisor.lock` is created if absent and deliberately retained after release to prevent split lock domains. Existing socket entries are never replaced.

Illustrative structure only; placeholders deliberately fail validation and must not be copied into active configuration:

```json
{
  "state_dir": "/REPLACE/private-state",
  "runtime_dir": "/REPLACE/private-runtime",
  "generation_lock": "/REPLACE/existing-generation.lock",
  "lease_seconds": 5,
  "ollama": {
    "state_dir": "/REPLACE/ollama-state",
    "weights": "/REPLACE/weights",
    "container_name": "REPLACE-ollama",
    "docker_network": "REPLACE-private-network"
  },
  "core": {
    "container_name": "REPLACE-core",
    "image": "REPLACE_WITH_VERIFIED_SHA256_IMAGE_ID",
    "owner": "REPLACE-with-explicit-owner-label-value",
    "uid": -1,
    "gid": -1,
    "network": "REPLACE-private-network",
    "memory": 0,
    "memory_swap": 0,
    "nano_cpus": 0,
    "pids_limit": 0,
    "mounts": [],
    "devices": []
  }
}
```

The nested `ollama` configuration is the legacy fixed backend policy, not client input. Its image remains the backend's fixed reviewed digest. Core's owner label key is `laf.core.owner`; its UID/GID must match the supervisor's current host UID/GID. Mounts must enumerate every observed mount as `{"Source":"/absolute/existing/source","Destination":"/container/path","RW":false}`. Devices must enumerate exact Docker inspect objects with `PathOnHost`, `PathInContainer`, and `CgroupPermissions`; only `/dev/kfd` and `/dev/dri/renderD128` with matching container paths and `rwm` are permitted. No Docker socket, known Docker-socket ancestor directory, or symlink mount source is permitted. An empty mounts/devices list is valid only when inspect also reports no mounts/devices; it is not a working GPU Core configuration.

Core resource policy must explicitly provide positive integer `memory` and `memory_swap` in bytes, `nano_cpus` in billionths of a CPU, and `pids_limit`. Each must fit Docker's signed 64-bit representation. `memory_swap` must equal `memory`, allowing no extra swap. Docker inspect must report exactly those values in `Memory`, `MemorySwap`, `NanoCpus`, and `PidsLimit`; missing values, zero/unlimited values, booleans, and mismatches are rejected before any stop. The example zeros deliberately fail closed. Actual limits must be chosen for the approved workload and host capacity; this module supplies no hardware-tuning defaults.

Core must have a private PID namespace and IPC, read-only root filesystem, no capabilities added, cap-drop ALL, no-new-privileges, no published ports, no restart policy, no extra networks, and the exact expected mounts/devices. The preexisting named network must be an internal bridge. The adapter can inspect and stop only that fixed container; it never starts or creates Core. Both Core and Ollama must be stopped before supervisor startup. Starting the stopped Core after supervisor readiness is a separate operator action, outside this module's authority.

## Safety behavior and limits

Two mode-0600 UDS lanes use the existing UID-authenticated transport. Starts are isolated from control operations. Authenticated samples refresh a 1–30 second lease; the monitor checks host telemetry and lease every 0.5 seconds. The configured lease includes startup time before the first sample. The Core Controller watchdog must continue sampling while idle and busy.

A resource fault, expired lease, or fixed emergency-stop request first invalidates admitted starts and latches a sticky host safety fault. It then stops the fixed owned Core container, verifies termination, and only afterward stops Ollama through the existing physical GPU-idle barrier. An unconfirmed Core stop prevents the Ollama stop. The monitor keeps retrying fixed-target cleanup, with ownership/security reconciliation on every attempt, until Core termination and the Ollama physical barrier are both confirmed. Recovery of the resource readings does not cancel this cleanup obligation. Regular client stop cannot clear the host safety fault. There is no wire reset operation.

Confirmed shutdown drains in-flight panic callbacks and host operations, closes the old port admission gate, advances its generation, removes only endpoints it created, and releases locks. On an unconfirmed shutdown, the CLI retains its locks and retries cleanup. Operator termination or host failure can still end the process; there is no durable cross-process safety ledger or automatic recovery claim.

The monitor and provider operations are Python code with cooperative cancellation. Docker subprocess timeouts are bounded, but arbitrary Python hangs, blocked telemetry, or an unresponsive physical idle barrier do **not** have hard execution deadlines. This prototype does not replace independent host containment or final operational review.
