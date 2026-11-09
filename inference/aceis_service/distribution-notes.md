# Local AI Fabric container distribution (planned, not published)

Local AI Fabric (LAF) is the agreed name for the independent local-LLM-plus-NPU
development platform intended for future free Docker Hub distribution. The
agreed service and MCP display names are LAF Router and LAF MCP; see
[naming and compatibility](../local_platform/naming.md). This is not publication
of AceIS itself. The current
AceIS implementation continues separately. This change neither
publishes an image nor grants release approval. Separate GPU and NPU services
remain the intended boundary, with a private Router client contract and no public
API by default. The user-service template requires a preprovisioned machine; it is not a portable
container release. Strata, Ollama, SDKs and models must be obtained separately.

Before publication:

- Check licenses and redistribution rights for Strata binaries/source, tokenizer,
  Flash pack/GGUF files, Ollama image and model manifests. Keep multi-GB weights
  out of images unless redistribution is explicitly permitted and intended.
- Pin base images and all source/dependency versions/digests, record build inputs,
  build reproducibly, produce a software bill of materials and scan it. The
  current pinned existing Ollama digest is not a new redistributed image.
- Supply minimal GPU device mappings and verified groups without privileged mode,
  host networking, Docker socket mounts or security disabling. Router must not
  receive a host Docker socket in a distributable container by default; replace
  host lifecycle control with a narrow supervisor boundary.
- NPU needs independent XRT/firmware/ORT EP and hardware compatibility checks.
  Clearly expose actual NPU versus mixed CPU/NPU work, with no silent CPU fallback.
  Do not assume hardware pass-through, driver ABI or licenses match GPU images.
- Separate model/cache/state volumes, offline operation, owner/UID mapping,
  health, readiness, resource budgets, shutdown/restart and optional service
  dependencies. No credential or machine-specific path belongs in an image.
- Reproduce on another clean host and document a complete installation path,
  supported hardware, exact pull/run examples, opt-in network endpoints and
  recovery. Current local paths/SDKs are not such a reproducibility claim.
- Confirm repository/registry visibility, image name, account, content and
  publication authority before any Docker Hub push. PR merge is separate.
