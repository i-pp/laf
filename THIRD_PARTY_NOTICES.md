# Third-party and external-asset boundaries

This source snapshot includes the selected first-party Python adapters, C++
worker/parser fixture, build templates, tests and documentation. No vendored
third-party implementation, SDK header/library, model weight, firmware,
compiled runtime, container image or private hardware trace is included.
No third-party notices were removed from the selected source files when KOL
headers and accompanying notices were added. Genuine upstream notices must be
preserved if third-party material is included in a later distribution.

## Referenced external components

- Python 3 and its standard library are installed separately. Their own license
  applies; KOL does not relicense Python.
- The legacy Ollama backend refers to a local image and model assets; it does
  not include or obtain those bytes. Confirm the exact upstream and model terms
  before acquiring or redistributing them.
- The Flash adapters load separately configured Strata source/tokenizer modules
  and executable/model assets. Those assets are not part of this repository and
  are not licensed by its first-party notices.
- The optional NPU worker includes the external ONNX Runtime C API by name at
  build time. Its build/launch path requires separately obtained Ryzen AI SDK,
  ORT, Peano, XRT/XDNA and model assets. The repository includes none of their
  headers, libraries, wheels or models. The retained [distribution assessment](inference/aceis_npu_service/DISTRIBUTION.md)
  explains unresolved exact-version license and redistribution questions.
- Docker, a chosen pinned base image, host drivers and hardware are separate
  prerequisites. This source snapshot includes a Dockerfile/build script,
  not a published image or a grant to redistribute its eventual contents.

This is a source-boundary inventory, not a completed runtime SBOM or a complete
license clearance of external dependencies. No AMD EULA or other external asset
terms are accepted by cloning the first-party source or running its portable
unit tests. Obtain assets through authorized routes and comply with their
applicable terms. Do not infer their license from model names, SDK wheel metadata
or a first-party wrapper's license.
