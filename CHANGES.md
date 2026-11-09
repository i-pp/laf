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

# Source changes

## 2026-11-09: remove repository-wide checksum manifest

Removed the root `MANIFEST.sha256` and its current descriptions from `README.md`
and `LICENSING.md`. Earlier entries below retain their historical references to
the manifest. Runtime asset integrity checks and the test runner's source/fixture
fingerprints remain unchanged. This entry records the changes to those three
files and `CHANGES.md`.

## 2026-11-09: embedded notices and repository-wide first-party scope

Moved the existing KOL notices into HTML comments in all 19 Markdown files and
removed all 25 `.license` sidecars. The approved full header summary and existing
first-party-only scope are preserved in the comments. All six JSON files (five
examples and one synthetic fixture) remain byte-for-byte unchanged, without
comments or added fields.

`README.md`, `LICENSING.md` and `NOTICE` now explain that the top-level LICENSE
covers project-authored material throughout the repository and its subdirectories,
unless a file states different terms. Third-party material keeps its own terms.
The complete license instruments, runtime code and existing code headers are
unchanged. `MANIFEST.sha256` is refreshed. The affected paths, apart from the
removed sidecars, are:

- `AGENTS.md`
- `CHANGES.md`
- `LICENSING.md`
- `MANIFEST.sha256`
- `NOTICE`
- `README.md`
- `THIRD_PARTY_NOTICES.md`
- `docs/laf-candidate-profiles.md`
- `docs/laf-cli-response-validation.md`
- `docs/laf-core-integration.md`
- `docs/laf-host-supervisor.md`
- `docs/laf-model-management.md`
- `inference/aceis_npu_service/DISTRIBUTION.md`
- `inference/aceis_npu_service/README.md`
- `inference/aceis_service/README.md`
- `inference/aceis_service/distribution-notes.md`
- `inference/local_platform/API.md`
- `inference/local_platform/README.md`
- `inference/local_platform/development-scope.md`
- `inference/local_platform/naming.md`
- `inference/local_platform/workspace-client.md`

## 2026-11-09: English development rules

Translated `AGENTS.md` into English while preserving the meaning and order of
all 15 development rules. The translated text uses ASCII characters only.
Existing licensing notices are unchanged. This entry identifies the changes in
`AGENTS.md` and `CHANGES.md`; `MANIFEST.sha256` is refreshed for these files.

## 2026-11-09: initial standalone source-only import

The reviewed component layout was adapted into a standalone tree without private
Git history, historical host acceptance fixtures, model data, SDKs or binaries.
Runtime program bodies are unchanged by the KOL application. Source/configuration
files receive the approved KOL summary in valid comments, while Markdown and
JSON files receive exact-path notices. LICENSE and its LicenseRef copy contain
the complete unchanged KOL v1 text; NOTICE preserves source attribution.

The standalone preparation adds an explicit portable/socket/full Python runner,
synthetic provenance fixtures and runner accounting checks. Historical private
fixture assertions were replaced by synthetic safety-contract assertions; no
real provenance, model license, supported runtime or hardware result is invented.
Documentation and example paths were made standalone and distinguish the legacy
profile from opt-in Core catalogs. Source-only and runtime-image release gates
are separate. The affected standalone-preparation paths are:

- `.gitignore`
- `README.md`
- `docs/laf-candidate-profiles.md`
- `docs/laf-cli-response-validation.md`
- `docs/laf-core-integration.md`
- `docs/laf-host-supervisor.md`
- `docs/laf-model-management.md`
- `inference/aceis_npu_service/DISTRIBUTION.md`
- `inference/aceis_npu_service/README.md`
- `inference/aceis_npu_service/systemd/aceis-npu.service.example`
- `inference/aceis_service/README.md`
- `inference/aceis_service/config.example.json`
- `inference/aceis_service/distribution-notes.md`
- `inference/aceis_service/systemd/aceis-inference.service.example`
- `inference/local_platform/API.md`
- `inference/local_platform/README.md`
- `inference/local_platform/development-scope.md`
- `inference/local_platform/fixtures/provenance-synthetic.json`
- `inference/local_platform/naming.md`
- `inference/local_platform/test_models.py`
- `inference/local_platform/workspace-client.md`
- `scripts/check-laf.py`
- `scripts/test_laf_checks.py`

New licensing records are LICENSE, LICENSES/LicenseRef-KOL-v1.txt, NOTICE,
LICENSING.md, THIRD_PARTY_NOTICES.md, this file and the matching `.license`
notices. MANIFEST.sha256 records final file identities, excluding itself.
Future distributed modifications must identify changed files or components as
required by KOL section 4(c), while preserving applicable original notices.

## Interactive CLI source changes (2026-11-09)

- Add a checkout-local `laf` launcher and `chat` subcommand over existing bounded JSON jobs.
- Retain recent conversation turns in memory, display replies as chat, and mark context/output limits.
- Add an optional explicit-workspace filename-only search with a fixed JSON tool schema and local `/find` command. No file contents, writes, shell, service configuration or model provisioning are added.
- Add offline regressions and a usage guide. Real-model, supported-host and clean-host acceptance remain separate.
- Preserve all existing `aceis-*` compatibility names; no installed unit/socket/connector is renamed.
