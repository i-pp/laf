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

# Local AI Fabric naming and migration

## Names used by this source version

- Product: Local AI Fabric (LAF).
- Guarded inference service: LAF Router.
- MCP presentation name: LAF MCP. Its implementation and installed configuration
  are outside this source tree; a repository rename does not update that connector.

The active source, examples and documentation use the following identifiers:

| Scope | Canonical identifier |
| --- | --- |
| Client modules | `inference.local_platform.*` |
| Router modules | `inference.laf_service.*` |
| NPU modules | `inference.laf_npu_service.*` |
| GPU example unit | `laf-inference.service` |
| GPU default socket | `$XDG_RUNTIME_DIR/laf-inference/service.sock` |
| NPU example unit and socket | `laf-npu.service`; `$XDG_RUNTIME_DIR/laf-npu/service.sock` |
| GPU container ownership label | `laf.inference.owner` |
| Core ownership label | `laf.core.owner` |
| Backend model aliases | `laf-coder-next`, `laf-gpt-oss-120b` |
| NPU worker output prefix | `LAF_JSON ` |

No aliases, dual label acceptance, fallback sockets or automatic resource
migration are provided for the previous names. Keep HTTP v1 routes and JSON field
semantics, public model IDs, `LOCAL_INFERENCE_SOCKET`, `--socket`, owner-only
permissions and lifecycle guards unchanged. The label rename must not weaken an
ownership check: a container carrying only a previous label is not owned by this
version and must be refused.

## Check the checkout without touching a running service

From the repository root:

```sh
python3 -B scripts/check-laf.py --suite full
printf '/help\n/quit\n' | ./laf chat --model flash
```

These checks use synthetic data and fake-provider sockets. They do not install
units, build a runtime image, load real models or prove a host migration. The
existing deployment examples require a provisioned host and are not a complete
fresh-host Quick Start.

## Adopting the new source names

This is a breaking source and configuration change. An older installed unit,
container, model alias, configuration or NPU worker must not be assumed to work
with it. Plan the deployment separately before replacing an installation:

1. Inspect and save the installed units, configuration and resource identities.
   Record an authorized maintenance window and rollback version. Preserve model
   files and the existing shared generation lock used by other local workloads.
2. Prepare a matched Router, optional NPU worker and source image using the new
   module paths, worker protocol and build allowlist. Rebuild the NPU worker;
   this source version does not accept its previous output prefix.
3. Inspect exact local paths, UID/GID, container names and owner labels. Recreate
   only resources explicitly authorized for this version. Never relabel a
   running or foreign container to bypass ownership validation, and never run
   parallel old/new model services against shared GPU resources.
4. Under separate authorization, stop the intended old service, install reviewed
   LAF units/configuration, prepare required model aliases, and update the client
   and optional NPU socket destinations. Retain the same private permissions,
   resource limits, exact ownership checks and no-retry behavior.
5. Validate startup, readiness, a real generation, optional NPU execution,
   cancellation, stop/restart and rollback on the supported host before declaring
   migration complete. An explicit `--socket` can select an already inspected
   endpoint, but does not migrate units, labels, models or the worker protocol.

No deployment, installed MCP identity or permission change is performed by this
source patch. MCP tool/server/App identifiers must be changed in their owning
implementation if a separate rename is requested; this repository contains no
such implementation. Do not infer that it has been rewired to the Router.

## Historical and attribution records

Earlier entries in `CHANGES.md` retain their original paths and scope statements.
`NOTICE` retains original source attribution. These are historical records, not
accepted runtime identifiers. Do not rewrite them as if the new names had been
used in the original source or acceptance evidence. Referenced third-party SDK,
library, model and runtime names keep their own identities and terms.

A public runtime release still requires the [distribution checklist](../laf_service/distribution-notes.md),
model/license/provenance checks, complete clean-host instructions and separate
publication approval. A passing source test suite does not satisfy those gates.
