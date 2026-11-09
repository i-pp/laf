# NPU runtime provenance and distribution gates

This source-only preview does not include a vendor SDK, model, compiled worker
or runtime image. KOL covers only the identified first-party source; it grants
no rights to the external assets discussed here. Container NPU acceptance and
a clean-host installation procedure remain outstanding. No image publication
is approved by this source release.

## What the source builds

[build.sh](build.sh) compiles `worker.cpp` as C++17 against SDK ONNX Runtime C
headers and dynamically links its ORT library. It uses `-O2 -Wall -Wextra -Werror`,
a literal `$ORIGIN` RUNPATH, and refuses to replace an existing output. The Python
adapter uses the standard library and does not import the vendor Python wheel.
This builds the application, not ORT, the VitisAI EP/compiler, Peano or XRT.

The expected integration baseline is Ryzen AI SDK 1.8.0, ORT 1.27.0, matching SDK
Peano and local XRT 2.25.37 with its XDNA user-space plugin. Acquire these and the
pinned quicktest model separately through authorized vendor routes. Version
labels alone do not establish ABI compatibility, provenance or redistribution
rights. SDK headers/libraries must match the layout required by the source.

Private inventories, binary hashes and hardware traces are omitted. The original
outer SDK download URL/receipt, exact historical build/install commands, complete
outer-to-inner correspondence and reproducible source recipes for all binary
components have not been established here. Do not invent or replace them with
generic `cmake` commands. A hash identifies bytes, not a vendor signature, proof
of acquisition or proof of a clean-host source build.

## Rebuild and verify the application

1. Obtain the SDK/XRT/model under applicable terms; record origins, versions,
   hashes and license documents. Preserve the original inputs.
2. Check ELF `NEEDED`, SONAME, symbol versions and RPATH/RUNPATH with `readelf`.
   Resolve the complete dependency graph, including later `dlopen` of compiler,
   execution-provider and plugin libraries. Linking the worker alone is insufficient.
3. Follow [the build and probe procedure](README.md) on a compatible provisioned
   host. Record compiler identity and output hash; no `pip install` is required
   for the worker path.
4. Check actual accelerator submissions, internal CPU partitions and numerical
   comparison. Revalidate for every new host/runtime baseline. Keep logs private.

## License findings and release gate

Read-only inspection of retained wheels found:

- `onnxruntime_vitisai-1.27.0-...whl` and `voe-1.8.0-...whl` metadata say `License-Expression: MIT`.
- Their actual `.dist-info/licenses/LICENSE.txt`, and that of `vitis_aie_essentials-1.8.0-...whl`, instead say use agrees to the [AMD EULA](https://account.amd.com/content/dam/account/en/licenses/download/amd-end-user-license-agreement.pdf) and [Ryzen AI third-party notices](https://account.amd.com/content/dam/account/en/licenses/download/ryzen-ai-1.7.1-linux-tpn-license.pdf).
- This inconsistency means **metadata's MIT label is not sufficient redistribution clearance**. The referenced third-party filename is 1.7.1 while the retained wheel is 1.8.0; the applicable complete license set needs confirmation.
- The SDK's bundled compiler, Peano, XRT binaries/plugin, firmware, and quicktest model each need the applicable license/source/notice/redistribution assessment. No separate model-redistribution grant was located in the bounded quicktest inspection. General ONNX Runtime upstream MIT terms do not automatically license every AMD bundled component or model.

The linked agreements have not been accepted anew or fully legally analyzed by this task. Before any public image: confirm exact redistributable subset, required notices, source-offer obligations if any, export/use restrictions if applicable, and the quicktest model's permitted redistribution. Obtain explicit publication approval. If unclear, distribute only our source/adapter image and require each user to obtain their own authorized SDK/runtime/model locally.

## Host and container requirements

A container still depends on a compatible host kernel, amdxdna driver, firmware,
physical NPU and ordinary-user access to `/dev/accel/accel0`. Resolve the SDK's
ORT/compiler/Peano libraries and XRT core/coreutil/XDNA plugin together with host
libc/libgcc/libuuid/libzstd/libncurses/libtinfo and SDK-selected libstdc++/zlib.
Compile-only success is not NPU execution. Matching SONAMEs are not ABI acceptance.

Before proposing a public image:

- Establish rights for our source and compiled worker as well as dependencies.
  No base image or portable userspace is claimed tested by this source candidate.
- Mount user-obtained SDK/model/XRT read-only; keep caches, temporary files and run
  directories outside that tree. Never bake private paths, secrets or traces into images.
- Pass only the required accelerator device and verified group mapping to a
  non-root container. No privileged mode, Docker socket, host PID namespace,
  security-profile disabling or automatic device-permission change.
- Connect Router and NPU through an owner-only Unix-socket directory. Keep NPU
  readiness independent of GPU readiness and publish no host port by default.
- Measure cold/steady memory and concurrent GPU load before setting budgets.
  Model file size is not a compiler or runtime memory bound.
- Repeat source build, library-selection checks, CPU/NPU comparison, ioctl proof,
  owned-child cleanup, NPU-outage/GPU-serving checks and restart recovery inside
  the actual container configuration.

Open gates include runtime/source-build provenance, redistribution permissions,
pinned compatible userspace, device access, measured resource budgets and real
container/hardware acceptance. An image is not a portable supported service until
those gates pass.
