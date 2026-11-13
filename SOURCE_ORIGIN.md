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

# Source origin and changed-file record

Public orchestrator baseline: https://github.com/i-pp/agent-orchestrator/tree/26de0b0e4919187c7b772723130556dad2bc1b2b

The audited snapshot has 52 exact public matches and 32 differing paths (including tests not distributed here). Required public upstream LICENSE/NOTICE are preserved in notices/agent-orchestrator. This is provenance, not approval to publish local additions.

Distributed changed paths relative to that baseline:
- `runtime/private_codex/config/upstream-policy.json`
- `runtime/private_codex/src/daemon.py`
- `runtime/private_codex/src/codex_app_protocol.py`
- `runtime/private_codex/src/mcp_http_transport.py`
- `runtime/private_codex/src/runner.py`
- `runtime/private_codex/src/desktop_notifications.py`
- `runtime/private_codex/src/development_poc.py`
- `runtime/private_codex/src/mcp_events_service.py`
- `runtime/private_codex/src/transport.py`
- `runtime/private_codex/src/isolation.py`
- `runtime/private_codex/src/os_providers.py`
- `runtime/private_codex/src/codex_pipeline.py`
- `runtime/private_codex/src/local_principal.py`
- `runtime/private_codex/src/notification_runtime.py`
- `runtime/private_codex/src/codex_host.py`
- `runtime/private_codex/src/mcp_adapter.py`
- `runtime/private_codex/src/storage.py`
- `runtime/private_codex/src/upstream_policy.py`
- `runtime/private_codex/src/verification.py`
- `runtime/private_codex/src/pipeline_policy_migration.py`

Locally added runtime paths: `src/oauth_auth.py`, `src/legacy_tool_adapter.py`, `src/experiments.py`, from captured HTTP snapshot 167c32320d78eea5cbee75d06f6d935ca2eb5d08. Legacy adapter is an extraction of the earlier adapter; local runtime-scope/identity changes remain. The six integration modules are local glue. The owner explicitly authorized publishing the project-authored local changes under KOL-v1 to the personal public fork and proposing them to i-pp/laf. Third-party ownership, licenses and notices remain unchanged; this is not an authorization to publish secrets, private state or operational evidence.

Differences include OAuth/HTTP identity, runtime scope, notification composition, bounded NSS resolver, older policy pin and older Codex protocol/pipeline behavior. No claim of newer upstream remote-approval features. Original snapshot tests and operational comparison evidence are not included;
new project-authored offline regression tests are identified below.

LAF source is preserved from the reviewed bundle, including its CHANGES, KOL notices and component-specific SDK MIT notice. The independent MCP component stdio-only notice does not describe the integrated HTTP surface. External Codex, SDK, cryptography, model assets, firmware and C++ Ace-Core implementations are not bundled or relicensed.

R5 archive-only metadata correction: public LAF Git tree 4485a7b records `laf` and `inference/laf_npu_service/build.sh` as 100755. Restore 755 only for those two source paths in the ZIP; all other archived files remain 600. No staged/host file permissions or runtime source bytes are changed.

## Proposed repository integration

Base public integration 114b4b72bcbadefbeca52f96a430e00402a8d0bd remains at top level. runtime/ is the R5 reviewed compatibility snapshot, not a replacement of newer top-level sources. New root launcher/manifests select that subset. Root inference/laf_service/server.py receives only ASYNC-ACK-01: async acknowledgment disconnection must not cancel the admitted job; synchronous disconnect cancellation remains. No other upstream main changes imported. New source-tree builder uses explicit policy paths, no private input artifact. The owner confirmed the KOL-v1 classification of project-authored local additions for this source PR. Existing third-party licenses and copyright notices are preserved. Merge, directory submission and further deployments are separate decisions.


## 2026-11-13: Codex package and version alignment

Set the root plugin and both integrated MCP SERVER_INFO versions to 0.1.0.
Add Codex compatibility/marketplace metadata and existing-owner-host registration
instructions. Correct the portable OpenAI extension nesting. These are explicit
local source changes beyond the pinned compatibility snapshot; guards, schemas,
authentication, runtime behavior and model configuration remain unchanged.
ChatGPT connection listing metadata is separate and not claimed changed here.


## 2026-11-13: English public source and explicit Unicode test fixtures

Translate the SDK installation guide and its bundled copy, chat examples and
generated policy preamble to English; normalize English typography to ASCII.
Retain intentional Unicode byte/path regression data with English explanations
and a literal-only allowlist. Preserve the historical policy heading bytes using
Unicode escapes; the verified body, hash/pin checks and parsing contract are
unchanged. Add CI enforcement and negative controls. These documented language
changes supersede the prior byte-frozen snapshot only in the affected source and
documentation paths; no deployment, SDK install, auth or model change occurs.


## 2026-11-13: deterministic header-rejection test transport

A hosted Python 3.12 run exposed a pre-existing test-client race: the NPU fake
HTTP server can return 415 after headers before http.client sends an unnecessary
body. Keep production service/guards unchanged; send only declared headers for
415/413 cases and assert no worker dispatch. Preserve the real body for the
400 input-validation case. Close the test connection even if sending fails,
and propagate unexpected send failures. Two RED-to-GREEN fixture regressions
cover payload omission and cleanup without suppressing errors. Synchronize the
bundled source-test copy. The original failed hosted run remains recorded.


## 2026-11-13: feature-only Codex catalog and reasoning-effort port

Adapt the project-authored provider catalog and immutable effort behavior from
[agent-orchestrator](https://github.com/i-pp/agent-orchestrator), not its full
later source snapshot. Modified runtime source is limited to
`codex_app_protocol.py`, `codex_host.py`, `codex_pipeline.py`, `mcp_adapter.py`
and `notification_outbox.py` under `runtime/private_codex/src/`. Both MCP facades
reuse the shared pipeline schema/validation; legacy dispatch is not replaced.
Preserve LAF's OAuthPrincipal ownership, shared-tool ACL and notify-only approval
surface. Do not import the canonical approval-response APIs, LocalPrincipal
per-user model, native-role aliases, auth configuration or policy pins.

The existing operator-only proven-empty-context replacement gains only immutable
selection checking, fixed effort transport and echo evidence. Its proof,
remote-ID, history and single-attempt constraints remain; legacy input is not
backfilled. Ordinary-session pool code/config, Router lifecycle and package
version 0.1.0 remain unchanged. The integrated-32 configuration key is retained;
the fully opted-in schema adds only the catalog read, yielding 33 tools.

New selected canonical and LAF-specific fake regressions are in
`scripts/test_codex_catalog_effort.py` and `scripts/test_codex_selection_boundary.py`.
`scripts/fixtures/codex-0.167-contract.json` records the inspected stable method
and reasoning subset only; unrelated approval schemas are omitted. These are
project-authored tests/metadata under the repository KOL scope. No Codex/SDK
implementation or external schema corpus is bundled or relicensed. Fixture
construction uses isolated temporary anchors, not private runtime records.
See the model-selection, testing and installation guides for scope and limits.


## 2026-11-13: preserve OAuth access to proven persisted blockers

Independent adapted-source review found that a newly persisted pre-turn BLOCKED
input was treated only as a failed dispatch, skipping its OAuth origin binding.
The source correction in `mcp_adapter.py` and `notification_outbox.py` under
`runtime/private_codex/src/` adds only an exact, bounded read-only creation-proof
path. It checks the same runtime ledger, caller-intent operation, task/thread and
immutable selection before using the existing Events authorizer and immutable
origin transaction. It does not treat UNKNOWN/FAILED/foreign results as ownership
proof or add a daemon RPC, identity source or permission surface.

Composed OAuth regressions in `scripts/test_codex_catalog_effort.py` cover direct
and proxy/daemon/HostGateway paths with temporary databases and fake transport,
including the review reproduction and negative uncertainty/forgery/lock/size
controls. Documentation and the strict selection guard describe this addition.
This correction requires independent re-review and exact-commit CI before use;
no prior source-review result approves it automatically.
