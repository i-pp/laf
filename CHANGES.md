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

## Unreleased: tunnel-only operator transaction

Added `ops/frontend_cutover.py`, `ops/test_frontend_cutover.py` and
`docs/frontend-only-transition.md`. The original operator code imports an existing
owned recipe; it does not bundle that recipe, private machine configuration,
credentials or runtime data. Added a strict28-unique fake-only selection in
`.github/workflows/minimum-ci.yml` and classified the three new files in
`.github/license-policy.json`. This `CHANGES.md` entry records the affected paths.
The change does not install, enable, deploy or publish a service when checked out.



## 2026-11-11: exact MCP SDK 1.30.0 source selection

Changed the adapter startup guard and package dependency from 1.29.0 to 1.30.0,
and recorded the official SDK wheel/sdist identities. Added mocked wrong/missing
SDK startup regressions and real launcher initialization coverage; real protocol
and integration tests now reject any SDK other than 1.30.0. Updated operator,
license-boundary and SDK-only installer descriptions. Historical 1.29.0 evidence
is retained separately from user-reported 1.30.0 installation checks. No Router,
receipt, lifecycle, provider, resource, authority, dependency-installer behavior,
or model code changed. All 29 installer pins remain unchanged. No installed
environment, host configuration, service or catalog was modified. The initial
source freeze does not claim native 1.30.0 protocol or live-runtime acceptance.

## 2026-11-11: combined catalog and MCP source checks

Combined the reviewed configured-catalog/provider-policy source and standalone
MCP source without changing their production modules. Added
`mcp/tests/integrated_router_fixture.py` and `test_integrated_router.py` to check
real Handler/Controller/CoreBackends behavior with synthetic lifecycle/provider
inputs. Checks cover exact configured IDs, Flash-off catalogs, legacy metadata,
caller-owned attempt recovery, readiness, completion and cancellation, including
an actual SDK stdio session. The bridge replaces HTTP/socket transport and peer
authentication. These are local source checks, not live UDS, model, hardware,
clean-host or install-ready acceptance. Added `mcp/MANIFEST.in` and an offline
sdist-content regression so the source package retains SDK pin/audit metadata,
launcher, notices and test helpers. Integration and full package tests still
require the complete LAF source tree. Existing source-change notices remain.

## 2026-11-11: normal configured model catalogs and optional Flash

Added a strict normal Core catalog with pinned Ollama manifest identities, an
explicit selected default and `flash_enabled`. Flash-free mode omits Strata
paths/process recovery; mixed mode retains the existing Flash lifecycle while
using configured small-model options. Legacy and isolated acceptance catalogs
remain separate. Core inventory/loaded-manifest checks reject identity drift;
empty-engine evidence now requires an actual list, and cancellation/provider
generation is checked before chat. Model/capabilities responses include default
and Flash metadata.

The CLI discovers and validates the catalog before each job, supports selected
IDs consistently across chat/ask/get/status/repair/development, and separates
known no-submit failures/interrupts from uncertain submissions. A conversation
resolves its default lazily and keeps its selected model. Added focused Core and
client regressions and updated existing fixtures, aggregate registration, API,
README and operator/client guides. No model acquisition, runtime deployment,
hardware suitability or public-source publication is implied.

## 2026-11-11: explicit host Ollama provider policy

Added an opt-in host-only `ollama_provider` policy in `backends.py` for an already-
installed immutable image, explicit version and bounded RAM/CPU/PID limits.
Repository digests are resolved by local image inspection; no image pull/build,
mutable tag fallback or new client/lifecycle authority is added. Legacy defaults
and physical safety guards remain. Container inspection now checks CPU/PID limits,
exact existing AMD devices and no added capabilities/device requests, and runs
before a newly created container starts. Added deterministic provider tests and
runner registration, a portability/smaller-VRAM acceptance guide, and updates to
the existing supervisor/source-preview guides. No installed configuration,
service, model or hardware acceptance changed.

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

## 2026-11-11: independent public Router MCP source candidate

Added root plugin manifests, the separate `mcp/` Python adapter package and tests,
`skills/laf-router/SKILL.md`, `docs/laf-mcp.md` and source/package checks. The five
local stdio tools use only the public Router API and owned receipts. No private
orchestrator source was imported. Existing Router, Core, host lifecycle, CLI and
resource limits are unchanged. SDK/dependency licenses remain separate from KOL.
This local candidate does not authorize installation, live switching or publication.

## 2026-11-12: native SDK 1.30.0 evidence and general installer guide

Record the recovered native SDK 1.30.0 stdio/fake-provider checks and remaining
live-client, hardware, distribution and deployment gates. Preserve the missing
full-suite command and package-log limitations without rerunning completed work.
Replace operator-specific SDK retry paths and review hash with the generic
inspect/prepare/printed-command workflow. Runtime source, tests, dependency pins,
SDK installation and services are unchanged. No public release is made.

## 2026-11-12: align the local candidate with Minimum CI

Integrate public main 963133b's source/license CI and upstream oversized-request
test race fix without changing production runtime. Classify the added candidate
paths explicitly, preserve the external MIT SDK license, and separate generated
dependency metadata from first-party licensing. Record rebased-tree static checks
without presenting the earlier native 513/86 results as a rerun of this tree.
SDK installation, services, models, catalog and public branches are unchanged.

## 2026-11-12: explicit SDK-free CI coverage

Integrate main03ddf8b's reviewed shared license Action and test-only early-Origin
rejection race fix. Add explicit Minimum CI steps for 74 MCP source/mock/manifest
cases and 56 installer regressions without SDK/dependency acquisition or install.
Keep 12 actual SDK cases and two package-build cases as separate gates. Check
the added commands locally and preserve scoped results without claiming native
SDK, live client, hardware, deployment or public-release acceptance.

The SDK-free CI steps also reject empty/duplicate selections, incomplete run
counts, skips, expected failures and unexpected successes. Actual 74/56 runs
passed; synthetic pass/skip/xfail/error/early-stop witnesses checked fail-closed
result accounting without installing SDK packages or starting real providers.

2026-11-12 proposed integrated source layout: root manifests/launcher select 27 legacy/Codex plus Router5; runtime/ is the reviewed compatibility snapshot. Root Core/CLI sources remain current public baseline. See SOURCE_ORIGIN.md. No deployment or rights grant implied.


## Owner-authorized experimental integrated source proposal

Publish the reviewed local compatibility/integration source and operator tooling
in one personal-fork PR, preserving public PR11 ancestry and third-party notices.
Update the root plugin description, README, install guides and origin records
to distinguish authorized source publication from still-unverified clean-host,
plugin-loader, complete 32-tool functionality and directory-release acceptance.
No credential, runtime state, private Git history, model, SDK, service unit binding
or operational evidence is included. This source change does not install, switch
or merge anything.


## 2026-11-13: Codex package and version alignment

Set the root plugin and both integrated MCP SERVER_INFO versions to 0.1.0.
Add Codex compatibility/marketplace metadata and existing-owner-host registration
instructions. Correct the portable OpenAI extension nesting. These are explicit
local source changes beyond the pinned compatibility snapshot; guards, schemas,
authentication, runtime behavior and model configuration remain unchanged.
ChatGPT connection listing metadata is separate and not claimed changed here.

Codex local marketplace documentation uses source-only archive extraction, not
a Git checkout: the CLI otherwise copies a .git worktree pointer into its cache.
The source builder already excludes Git metadata; no guard is weakened.
