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

<!-- Added 2026-11-10: isolated client installation, separate from runtime provisioning. -->

# Install the terminal client

## Busy-reader summary

Install this checkout's Python client into a dedicated virtual environment to get
an executable `laf`. The client has no third-party runtime Python dependencies.
It connects to an existing private LAF Router; it does not install or start a
Router, engine or model. These are client installation commands, not a fresh-host
runtime Quick Start.

Use an existing Linux Python 3.10 or later with `venv`, `pip`, `fcntl` and
`resource`. From the reviewed repository checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/laf --help
printf '/help\n/quit\n' | .venv/bin/laf
. .venv/bin/activate
laf
```

Choose a new virtual-environment directory. Do not replace an existing environment
or use `sudo pip`. Activating it changes PATH only in the current shell. In a new
shell, activate it again or run its absolute `bin/laf` path. No shell startup file
or system service is changed by these commands. To leave the environment, run
`deactivate`.

`pip install .` may obtain the `setuptools>=77` build dependency from the user's
configured Python package index. Do not enter credentials into LAF. If the
required build tools are already installed in a separate build environment,
the offline route below avoids package-index access. This preview's distribution
name is `local-ai-fabric-client`, with development version `0.0.0.dev0`; do not
substitute an unrelated package from a public index.

## Offline wheel and isolated install

The build environment needs existing `pip`, `setuptools>=77` and wheel-build
support. Do not install or update host packages just to run this check. From the
reviewed checkout, using that build environment's `python`:

```sh
python -m pip --isolated wheel --no-index --no-deps --no-build-isolation \
  --no-cache-dir --wheel-dir /ABSOLUTE/NEW/wheels .
python3 -m venv /ABSOLUTE/NEW/client-venv
/ABSOLUTE/NEW/client-venv/bin/python -m pip --isolated install \
  --no-index --no-deps /ABSOLUTE/NEW/wheels/local_ai_fabric_client-0.0.0.dev0-py3-none-any.whl
/ABSOLUTE/NEW/client-venv/bin/laf --help
printf '/help\n/quit\n' | /ABSOLUTE/NEW/client-venv/bin/laf
```

Replace the absolute placeholders with new directories owned by you. Build only
a reviewed checkout: Python package build configuration can execute code. If an
existing build prerequisite is missing, stop and obtain approval before changing
the host's tools. The offline flags do not fetch missing build dependencies.

The wheel contains project Python client/support modules, license notices, the
change record and the referenced NPU redistribution assessment.
It does not contain Docker images, model weights, vendor SDKs, native NPU code,
systemd units or runtime configuration. The existing `laf` checkout launcher and
`python3 -m inference.local_platform.cli` remain available without installation.

## Connect to an accepted Router

Help, `/help`, `/quit` and a manual `/find` do not need a model or Router.
Ordinary conversation does. Once the Router and models are separately accepted,
activate the client environment and use:

```sh
laf status
laf
laf chat --model flash --workspace "$PWD"
# Only when the accepted Router uses a different private socket:
laf --socket /ABSOLUTE/PRIVATE/service.sock
```

The default remains `/run/user/<uid>/laf-inference/service.sock`.
`LOCAL_INFERENCE_SOCKET` and an explicit `--socket` retain their precedence and
strict ownership, non-symlink and directory0700/socket0600 checks. Installation
does not create a socket, change access controls or migrate an old service.
Read the [chat guide](laf-chat-cli.md) for behavior and model-text limitations.

The three existing model profiles remain `flash`, `coder-next` and
`gpt-oss-120b`. Packaging does not make any of them fit a smaller machine or
supply their acquisition/license metadata. No 8-16GB inference configuration,
Windows/macOS runtime, fresh-host setup, GPU/NPU support or model download is
established by installing this client. Keep the [runtime prerequisites and
release gates](source-preview.md) separate from client acceptance.

## Check the package without modifying the host

On an authorized Linux build executor with the existing prerequisites above:

```sh
python3 -B -m unittest discover -s scripts -p test_client_install.py -v
python3 -B scripts/check-laf.py --suite portable
```

The package test builds offline in a temporary copy, installs into a new temporary
venv, and invokes the installed command outside the checkout with `PYTHONPATH`
removed. It checks the entrypoint, license metadata, local chat/help/filename
search, unchanged model catalog and fail-closed missing-socket behavior. It sends
no real model requests and changes no permanent PATH, service or user config.
Missing prerequisites are errors, not passing/skipped installation tests.
The package gate is separate from the standard-library-only source suite and
its explicit Unix-socket group. Neither gate proves fresh-host inference.
