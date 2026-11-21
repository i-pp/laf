---
name: laf-model-setup
description: Guide the host owner through choosing and preparing a local model for Local AI Fabric.
---

<!--
KISS Open License Version 1.0 (KOL-v1)
SPDX-FileCopyrightText: 2026 I-plusplus Inc.
SPDX-License-Identifier: LicenseRef-KOL-v1
Original copyright and attribution notices must be preserved. Use is governed by
the full LICENSE included with this package.
-->

# Set up a model for Local AI Fabric

Run these commands from the verified LAF source directory on the user's Linux
host. In ChatGPT, give the commands to the host owner when the host terminal
is unavailable. The same plugin handles Router inference after setup.

1. Show public names and update dates with
   `python3 -B -m inference.local_platform.cli model discover --publisher HuggingFaceTB`
   or `--publisher Qwen`. These names are from the publisher's public model
   registry. Only entries mapped to a reviewed artifact can be downloaded by
   this workflow. Show the publisher model card separately from the Ollama
   format, quantization, size, license and full manifest digest. Use
   `python3 -B -m inference.local_platform.cli model artifact MODEL_ID`
   for that metadata.
2. Ask the owner to choose an artifact or an existing model. To prepare a new
   private model config, use
   `python3 -B -m inference.local_platform.cli model --config CONFIG setup`
   with the owner's numeric-loopback `--engine-url` and exact
   `--generation-lock`. The default store is `~/.local/share/laf/models`.
   Pass `--model-store ABSOLUTE_PATH` for a different persistent store and
   `--state-dir` for an independent management registry if required. Verify
   that the selected Ollama process actually uses that store. Existing
   configuration is preserved.
3. After the user explicitly chooses a download, use
   `python3 -B -m inference.local_platform.cli model --config CONFIG download MODEL_ID --digest FULL_DIGEST`.
   Use the exact digest the user selected. Preserve an uncertain result and
   inspect it before another attempt. License or login requirements are
   completed by the owner at the official source.
4. To use an existing store, run `model --config CONFIG inventory` and
   `model --config CONFIG use-installed --reference TAG --digest FULL_DIGEST --profile ID`.
   To select a downloaded GGUF file, use
   `model use-file ABSOLUTE_FILE --digest FILE_SHA256 --modelfile NEW_PRIVATE_PATH`.
   This verifies the file and prepares the Ollama Modelfile. The owner imports
   it locally with the official Ollama CLI, then selects its installed tag and
   manifest digest from `inventory`.
5. Configure the selected identity in the owner's Router profile and explicit
   allowlist. Check `laf_router_models` after the owner's Router setup. A
   verified installation by itself does not establish Router readiness.

Plugin installation and plugin updates do not acquire weights. No separate
model-management MCP registration is needed. Keep the model store outside
the plugin installation and cache. Start downloads only on explicit selection.
