# Source changes

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
