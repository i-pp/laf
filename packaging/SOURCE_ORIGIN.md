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

Locally added runtime paths: `src/oauth_auth.py`, `src/legacy_tool_adapter.py`, `src/experiments.py`, from captured HTTP snapshot 167c32320d78eea5cbee75d06f6d935ca2eb5d08. Legacy adapter is an extraction of the earlier adapter; local runtime-scope/identity changes remain. The six integration modules are local glue. Public-distribution authority for these changes remains pending.

Differences include OAuth/HTTP identity, runtime scope, notification composition, bounded NSS resolver, older policy pin and older Codex protocol/pipeline behavior. No claim of newer upstream remote-approval features. Tests and operational comparison evidence are not included.

LAF source is preserved from the reviewed bundle, including its CHANGES, KOL notices and component-specific SDK MIT notice. The independent MCP component stdio-only notice does not describe the integrated HTTP surface. External Codex, SDK, cryptography, model assets, firmware and C++ Ace-Core implementations are not bundled or relicensed.

R5 archive-only metadata correction: public LAF Git tree 4485a7b records `laf` and `inference/laf_npu_service/build.sh` as 100755. Restore 755 only for those two source paths in the ZIP; all other archived files remain 600. No staged/host file permissions or runtime source bytes are changed.
