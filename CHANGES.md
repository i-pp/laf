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

## 2026-11-10: isolated terminal-client package

Added `pyproject.toml` with a standard `laf` console entrypoint and no third-party
runtime Python dependencies. Added an offline wheel/temporary-venv regression
gate in `scripts/test_client_install.py` and a bounded client installation guide
in `docs/client-install.md`, linked from `docs/laf-chat-cli.md` and `README.md`.
The README distinguishes client packaging from pending runtime provisioning.
Updated build output ignores. Runtime source, services, model profiles and socket checks are
unchanged. This is not a complete fresh-host runtime installer.

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

## Chat provenance and history follow-up (2026-11-10)

- Label every generated line as unverified, label actual file observations as local, and distinguish client notices. Generated prose remains capable of incorrect claims.
- Keep client filename observations out of assistant-generated history and prune each search turn as one complete group.
- Do not dispatch tools from length-capped output, retain truncated turns, or automatically continue them.
- Add regressions for result-line impersonation, observation roles, complete-turn pruning, reset and truncation. No new file, shell, model, service or network capability is added.

## LAF identifier unification (2026-11-10)

- Rename the Router and NPU source directories to `inference/laf_service` and
  `inference/laf_npu_service`, and update every active import, test inventory,
  Docker COPY/entrypoint and document link to those paths.
- Use `laf-inference` / `laf-npu` unit, socket, state/cache and example container
  names; `laf.inference.owner` / `laf.core.owner` ownership labels; `laf-*`
  backend model aliases; and the `LAF_JSON ` NPU worker protocol prefix.
- Update the CLI defaults, model catalog references, configuration examples,
  source/build tests and operational documentation as one breaking source change.
  No previous-name aliases, automatic migration or dual ownership acceptance
  are provided. Add regressions for canonical names and bounded historical exceptions.
- Keep the HTTP v1 API, public model IDs, explicit socket override, existing
  generation lock, permission checks and lifecycle/resource guards unchanged.
  No installed unit, process, model, container, connector or permission is changed.
- Preserve all earlier entries above and the original `NOTICE` attribution
  verbatim. Their old paths and prior compatibility decisions are historical;
  the naming policy in this source version supersedes those earlier decisions.

Affected current components: `README.md`, `LICENSING.md`, `THIRD_PARTY_NOTICES.md`,
`docs/laf-candidate-profiles.md`, `docs/laf-chat-cli.md`,
`docs/laf-core-integration.md`, `docs/laf-host-supervisor.md`,
`inference/laf_service/`, `inference/laf_npu_service/`, `inference/core_container/`,
`inference/local_platform/{API.md,README.md,cli.py,model_management.py,naming.md,
workspace-client.md,test_chat.py,examples/cli-regression-task.json}`,
`scripts/{check-laf.py,laf_socket_test_ids.py,test_laf_checks.py,test_laf_naming.py}`
and this change record. Unchanged third-party references retain their own terms.

## Remove development-only source metadata checks (2026-11-10)

Remove the two repository-metadata test modules under `scripts/` and their runner
registrations. The runner no longer scans filenames or emits source-file SHA
lists and internal release/deployment flags. Update the current README to describe
its test-result summary instead.

Keep the functional and safety suites, private-socket grouping and accurate
failure/skip accounting. Move the hardware-free NPU probe decoding regression
into `inference/laf_npu_service/tests/test_service.py`; it tests actual output
handling rather than product spelling. No production runtime source, service,
model, connection, license, original attribution or earlier change record changes.

## Default conversation entry (2026-11-10)

Open the existing interactive chat when no subcommand is supplied, including an
explicit global socket override. Keep the canonical private socket, environment
override, chat defaults, explicit subcommands, help and all socket safety checks.
No installed command, PATH entry, service, model or permission is changed.
