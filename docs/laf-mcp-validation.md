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

# SDK 1.30.0 adapter validation record

## Result and scope

The independent public Router MCP candidate passed native **MCP SDK 1.30.0 /
Python 3.14.4** stdio and synthetic-provider checks on Linux on 2026-11-11.
The SDK was installed by the operator. Validation did not install packages into
that runtime or change an existing Router, service, model, device or MCP catalog.
This is not live deployment acceptance or a safety-critical certification.

| Check | Recovered result | Boundary |
| --- | --- | --- |
| MCP runtime | 86 passed, zero failure/error/skip | Includes 12 actual-SDK protocol/integration cases; excludes two package-build cases. |
| Router/Core/client Python suite | 513 passed, zero failure/error/skip | Unit/mocked source checks; excludes MCP and package-build checks. |
| Additional launcher/Router transport harness | 4 passed, zero failure/error/skip | Three actual-launcher/private-UDS synthetic-provider cases and one missing-SDK startup refusal. |

Focused socket (39) and partial portable (472 of 474 selected) checks were
also recovered. The initial portable run deferred two owned fake-Flash-child
cases; those two passed after their read-only ownership/cleanup scope was
confirmed. The later full 513-case pass includes them. These focused checks
overlap that full suite: **do not add them to the 513 count**. Passing test counts are not a coverage percentage or assurance level.

The launcher check used the unmodified `mcp/launch.py` startup/version guard,
five-tool catalog, a path containing spaces/Unicode, isolated Python startup,
EOF shutdown and empty stderr. The extra transport harness covered small normal
model identifiers with Flash disabled, canonical-request nonce reuse/conflict,
receipt privacy, queued cancellation semantics and crash/restart without reposting.
Cancellation acknowledgment was not presented as proof of physical provider stop.
No real model or hardware was used.

## Evidence integrity and limitations

Counts in this dated record identify historical observations, not a fixed test
specification or acceptance of future revisions. Current source commands and
results are available in the [pull request checks](https://github.com/i-pp/laf/pull/12/checks)
and [CI history](https://github.com/i-pp/laf/actions/workflows/minimum-ci.yml).
Inspect the tested commit and verification scope rather than comparing totals.

The source export tested on the host is retained separately from raw evidence.
On 2026-11-12 the existing JSON/log artifacts were recovered and checked without
rerunning the tests. Save times were normalized from the host's +09:00 offset:
the full-suite log was saved at **2026-11-11 15:10:33 UTC**. A save time is not an
independently verified execution start/end time.

- The successful 513-case command was not preserved in those artifacts. A prior
  direct `python -I scripts/check-laf.py` invocation failed importing its sibling
  `laf_socket_test_ids` helper. Preserve that failure; do not count it as a pass
  or invent the later successful invocation. The normal documented runner command
  below does not use `-I`.
- Two additional adapter packaging cases were reported passing, but their raw-log
  location was not recovered. They are excluded from the independently recovered
  result table. No duplicate package test was run just to fill this evidence gap.
- Four installed-SDK file content/size/mtime samples matched saved before/after
  observations. This is a sample check, not proof that every prefix file is unchanged.
- The known test temporary directory was empty, the four recorded launcher PIDs
  were gone, and the transport harness recorded fixture removal/thread shutdown.
  This checks owned test cleanup, not all host processes.
- Dependency advisory/license records are dated observations, not exhaustive
  security clearance or legal approval. Recheck support before future release.

The first follow-up revision only updated documentation and dependency-evidence
metadata, keeping runtime, tests, dependency pins and executable modes unchanged.
The subsequent 2026-11-12 integration incorporates public main `03ddf8b`: its
Minimum CI/license-evidence files and upstream test-only fixes that check oversized-request and empty-Origin
early rejection without racing a rejected body write.
Production runtime and dependency pins remain unchanged; that existing test's
source is no longer byte-identical to the native-tested export. The 513/86 result
table is historical evidence of the preserved native candidate, not a claim that
the rebased tree was rerun. Source syntax, plugin-manifest and declared-license
checks passed on the rebased 145-file tree. Raw logs and private machine paths are
not bundled into distributable source; no source-file hash inventory is added.

## Source-only CI follow-up

The 2026-11-12 local CI integration, based on public main `03ddf8b`, additionally
passed **74 MCP source/mock checks** and **56 SDK-installer regressions** using an
existing cloud Python. The exact selected MCP command includes the plugin-manifest
case but deliberately excludes the 12 actual-SDK protocol/integration cases and
two package-build/install cases. These new source-only results are not native SDK
acceptance and must not be added to the earlier 86-case runtime count.

Installer regressions use synthetic wheels, fake HTTP/install runners and four
empty private venv creation tests. They do not fetch/install the SDK. Source
syntax, YAML and the added shell commands were checked; no installed SDK prefix,
existing Router/service, real model or device was changed. The updated Minimum CI
runs these selected checks explicitly rather than silently claiming that the Core
Python suite covers the adapter or installer. Actual SDK and packaging acceptance
remain separately recorded gates. The inherited license job uses the reviewed
immutable shared Action, which selects the latest released checker; local
checker-v0.1.0 evidence is dated and not proof of that future CI resolution.

## Repeating checks when a change requires it

Do not reinstall the SDK or modify its venv to run tests. The source commands
remain:

```sh
python3 -B scripts/check-laf.py --suite full
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=mcp/src:mcp/tests \
  /ABSOLUTE/REVIEWED/VENV/bin/python -B -m unittest \
  test_protocol test_integrated_router -v
```

The first command uses fake subprocesses and private Unix sockets, with unchanged
ownership/peer guards. The second requires the reviewed SDK 1.30.0 interpreter.
These are repeatable commands for their stated checks, not a reconstruction of
the missing historical full-suite invocation. See [the MCP guide](laf-mcp.md)
for package-build prerequisites and runtime configuration. Actual client/plugin
installation must use the selected SDK interpreter explicitly; activating a
terminal venv does not establish a desktop application's interpreter selection.

## Remaining acceptance gates

- Review one integrated candidate before public push/PR; publication is not authorized here.
- Explicitly select and test a local MCP client/plugin configuration and catalog.
- Review a separate deployment plan before any service or live MCP cutover.
- Measure actual accepted models on intended 8-16 GB VRAM hosts; no fit/latency or
  GPU/NPU acceptance follows from synthetic profiles.
- Complete clean-host/second-PC runtime and external-asset redistribution review.
- Preserve independent test/evidence review. Automotive, aviation or aerospace
  rigor is a development goal, not assessed compliance or certification.

Standard orchestration and optional Codex authentication remain separate from
this account-free local Core/Router interface. An unrelated private orchestrator
runtime update must not be confused with installing this five-tool adapter.
