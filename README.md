# Local AI Fabric

Local AI Fabric (LAF) provides a guarded local inference service, an opt-in Core
and host-supervisor boundary, a scoped development client and model management.
The `inference.aceis_service.*` module path and existing API/ownership names remain
compatibility identifiers. The separate AceIS implementation is not included.

## Busy-reader summary

This repository contains a source-only LAF preview. It includes no model weights,
SDK binaries, compiled runtime, private host acceptance harness or private
repository history. Its identified first-party source is licensed under
[KISS Open License Version 1.0](LICENSE); see [the licensing scope](LICENSING.md)
and [third-party boundaries](THIRD_PARTY_NOTICES.md). This is not a supported
runtime or image release.

For offline development, use an existing Linux Python 3.10 or later with standard
library support for `fcntl`, `resource` and Unix processes. No package installation,
Docker, model download, running Router or vendor SDK is needed for these commands.
From the extracted source root:

```sh
python3 -B scripts/check-laf.py --suite portable
python3 -B -m inference.local_platform.cli --help
python3 -B -m inference.local_platform.cli model --help
python3 -B -m inference.local_platform.cli model catalog
```

The portable runner executes unit/mocked checks, temporary-file operations and
owned fake subprocesses. It omits exactly 39 enumerated private Unix-socket tests,
lists each unexecuted ID, and cannot report a full regression pass. Exit 0 means
the selected group passed with complete accounting and no skips; exit 1 means
test failure/error, and exit 2 means invalid execution or test inventory.

On an authorized executor where private Unix sockets and peer-UID queries work:

```sh
python3 -B scripts/check-laf.py --suite sockets
python3 -B scripts/check-laf.py --suite full
```

The socket group uses fake providers, not Docker or real models. The full group
combines both groups. A denied socket is an error, never an automatically skipped
or passing test. Native C++/ORT, real GPU/NPU, clean-host runtime and second-PC
checks are separate and are not run by this Python suite.

## Code map

- [Router and existing-assets operation](inference/aceis_service/README.md)
- [Opt-in Core and image source boundary](docs/laf-core-integration.md)
- [Fixed host supervisor](docs/laf-host-supervisor.md)
- [Model catalog, management and provenance](docs/laf-model-management.md)
- [Scoped development client](inference/local_platform/README.md)
- [Optional fixed-shape NPU service](inference/aceis_npu_service/README.md)
- [NPU runtime rights and distribution gates](inference/aceis_npu_service/DISTRIBUTION.md)

## Runtime prerequisites and current limits

The test commands above are a software-check path, not a runtime Quick Start.
Runtime operation needs separately obtained and verified engines, assets and
host configuration. The legacy backend expects a fixed local Ollama image ID;
that value is not a downloadable registry reference. Flash depends on separately
provisioned Strata executable/source/tokenizer assets. Optional NPU operation
needs a matching vendor SDK, ORT/XRT/compiler stack, pinned model and compatible
host driver/device access. These dependencies are neither installed nor licensed
by this checkout. The Core image builder packages ten named source files only.

Configuration and systemd examples are templates. Replace every placeholder
path, UID, container name and network with inspected local values. JSON does not
expand `~`, `$HOME` or environment variables. A generation lock must refer to the
same existing owner-controlled lock used by other local model workloads. Keep
compatibility labels and ownership checks intact. No example authorizes replacing
an existing service, changing device permissions or exposing a network port.

Default model catalog entries intentionally lack verified acquisition, license,
version, manifest and capacity metadata, so installation is blocked. Synthetic
regression fixtures are not real model provenance. Model management does not
accept legal terms, authenticate to providers or obtain redistribution rights.

## Evidence and release boundary

Historical private host reports remain outside this source draft. Their previous
test counts and binary hashes are not acceptance of this tree. The new runner
records its exact source/fixture fingerprints and current result scope. Preserve
failed and unexecuted stages when reproducing it on another executor.

The Owner approved this bounded first-party source import and KOL v1 licensing.
Files covered by KOL are identified by their headers or exact-path `.license`
notices. External assets retain their separate terms; this approval does not
license or authorize their redistribution. The source review found no obvious
credentials or private host paths in the selected tree, but does not establish
exhaustive security, patent or legal clearance. KOL is a custom license, not a
claim of OSI approval. Preserve applicable notices and identify distributed
changes as required by the full license.

`MANIFEST.sha256` identifies all distributed files except itself. The Python
runner separately records its source/fixture fingerprints and selected test
scope; its fixed `p0_complete`, `release_authorized` and `deployed` flags remain
false because tests do not grant runtime-release or deployment approval.

A runtime or image release additionally needs the exact external dependency
inventory/SBOM, redistribution assessment, clean-host behavior and real
runtime/hardware acceptance for its supported configuration. These runtime gates
do not block a separately approved source-only snapshot that contains none of
those external assets. Source publication, registry upload, merge and deployment
are separate actions.

For an initial infra integration, prefer a README reference to the verified LAF
repository and tested immutable release/commit once those exist. Do not make this
source depend on private infra history or its host directories. A commit-pinned
submodule is an optional later integration choice.
