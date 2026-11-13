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

# Install the MCP SDK on the host

## Before you begin

This CLI installs **only 29 pinned packages, including MCP SDK 1.30.0**, into a
persistent, user-owned venv on the host. It does not change system Python, PATH,
the MCP client, Router, models, or services. **If already installed, no
reinstallation is needed.** Installing the SDK alone does not enable the LAF adapter.

For the first installation, run the following from the repository root.
`inspect` is read-only.

```sh
python3 -I -B scripts/laf-sdk.py inspect
```

Check for `ready_for_explicit_action` and an empty `preflight_issues` before
fetching artifacts. This operation saves official PyPI metadata and wheels to a
new bundle; it does not install them.

```sh
python3 -I -B scripts/laf-sdk.py prepare
```

Review the licenses, advisories, and target environment in `review.json` at the
reported `review_path`. Then use **`install_dry_run_command`, followed by
`install_command`**, from that output. Do not copy a SHA256 from another
environment or article. The generated commands contain the review hash and
installation destination for this bundle. The dry run does not install anything.

These instructions install only the SDK. [Adapter configuration and testing](laf-mcp.md)
are separate steps. This is not a complete LAF Quick Start; it does not
automatically install LAF or start services.

## Destinations and prerequisites

Without arguments, the CLI uses these destinations:

- Downloaded artifacts: `$HOME/laf-sdk-1.30.0-bundle`
- Persistent installation prefix: `$HOME/.local/share/laf-sdk-1.30.0`
- Installed Python: `$HOME/.local/share/laf-sdk-1.30.0/venv/bin/python`

Each destination's parent directory must already exist, be owned by you, and
not be writable by other users. `prepare` requires a new bundle directory;
`install` requires a new prefix. Existing directories are neither overwritten
nor deleted. If an incomplete directory remains, inspect the cause and select
a different, new destination. A verified bundle can be reused to resume in the
same environment. For example, when a new bundle is needed, use existing, safe
parent directories:

```sh
python3 -I -B scripts/laf-sdk.py prepare \
  --bundle /ABSOLUTE/NEW/bundle --prefix /ABSOLUTE/NEW/prefix
```

The supported target is Linux x86_64 with an existing CPython 3.12 or 3.14.
`venv` and pip 22.3 or later are required; this CLI does not add missing host
components. Installation uses the existing pip's metadata parser and `--python`,
and creates the venv with `--without-pip`. It does not work around missing
prerequisites with `apt`, `get-pip`, `sudo`, or `--break-system-packages`.
On Ubuntu with a PEP 668-managed system Python, the dedicated venv remains the
only installation target. Do not add pip or build tools to the SDK venv.

## What the CLI verifies

`prepare` uses the running Python's compatible wheel tags and dependency markers.
It does not reuse a Python 3.12 wheel as though it were compatible with a 3.14
host. The pinned set contains 29 packages, including the cryptography, cffi, and
pycparser dependencies of `pyjwt[crypto]`.

- Fetches only official release metadata from `pypi.org` and binary wheels from
  `files.pythonhosted.org`. It does not use redirects, environment-configured
  proxies, alternate indexes, sdists, or additional MCP SDK extras.
- Checks package names, versions, filenames, sizes, SHA256, METADATA, and WHEEL
  tags. It also checks the SDK wheel against the previously verified upstream SHA256.
- Validates Python requirements, dependency constraints, and extras reachability.
  Rejects direct-URL dependencies.
- Records PyPI vulnerability information and wheel license declarations and text.
  Stops on any advisory, missing information, unknown declaration, or missing
  compatible wheel.
- Stops rather than automatically updating if the pinned SDK is no longer the
  official latest stable 1.x release.

These checks reflect information available at acquisition time. Empty PyPI
vulnerability information does not prove the absence of vulnerabilities or
comprehensive safety. Saving license evidence is not legal approval.
The CLI stops if network access is not authorized. If a proxy is configured,
it stops before acquisition rather than switching to a direct connection.
There is no automatic fallback to another route.

`install` operates offline, using only verified local wheels and a complete
hash lock. It creates the venv at its final destination, verifies Python's
prefix and site-packages, and installs with `--no-index --no-deps --require-hashes`.
It then checks `pip check`, SDK import, and exact package versions.
It writes `installed.json` only after all checks pass. It does not relocate the venv.

The dependency-integrity hash lock and the fingerprint used to match identical
MCP job requests serve different purposes. Neither creates a per-source-file
hash inventory in the repository.

## If an operation fails

The newly created directory is retained in its incomplete state. Partially
installed packages may exist inside the new prefix, but no completion marker
is written. Existing environments, commands, and services are untouched.
The CLI does not automatically delete, overwrite, or reuse the directory; the
error reports its retained location. Do not treat that state as a completed
installation. After inspecting its contents, the user decides whether to
remove unneeded data.

The creation-permissions bug fixed on 2026-11-11 caused a venv inheriting the
caller's `umask=0002` to create `pyvenv.cfg` with mode `0664`, which the CLI's own
strict check rejected. The fix uses `subprocess.run(..., umask=0o077)` to make
only child-process file creation private. It does not change the caller's
`umask` or existing files, and does not relax ownership or write-permission
checks. There is no need to work around the failure with `chmod` or by deleting
the old prefix. The [CPython 3.14.4 venv implementation](https://github.com/python/cpython/blob/v3.14.4/Lib/venv/__init__.py)
was also checked: it creates this file with a normal writable `open` call.

## Why PyJWT was updated

Official PyPI metadata observed on 2026-11-11 listed advisories for the previous
candidate, 2.13.0, while 2.15.1 had an empty `vulnerabilities` list. Version
2.15.1 is MIT-licensed, requires Python >=3.9, and satisfies MCP SDK 1.30.0's
`pyjwt[crypto]>=2.10.1` requirement. Its cryptography dependency constraint and
crypto extra are unchanged from the previous candidate.

- [Official 2.15.1 release metadata](https://pypi.org/pypi/pyjwt/2.15.1/json)
- [Advisory concerning updates to decode options](https://github.com/advisories/GHSA-gvp8-978c-rx2q)
- [Recursion advisory fixed in 2.15.0](https://github.com/advisories/GHSA-42vr-xj54-vc7v)
- [OKP key advisory fixed in 2.15.0](https://github.com/advisories/GHSA-x33g-cr3x-6449)

The decode-options advisory did not specify a fixed version, but the 2.15.1
upstream source was also confirmed to include the
[fix that copies options](https://github.com/jpadilla/pyjwt/commit/0c87c8c8b1a74cac99ad8115f3050efcb7fbed35).
Version 2.14.0 was not selected because other advisories still apply to it.
Acquisition checks advisories for all 29 packages and stops if any are reported.
No exception to ignore warnings has been added.

## Measured validation scope

The corrected installer passed 56 tests: 52 cases using synthetic wheels, fake
HTTP, fake installation runners, and similar fixtures, plus four real tests
creating empty venvs with the existing CPython 3.12.14. The real tests used
`venv --without-pip` in temporary directories and did not install the SDK.

- Original behavior reproduced: with `umask=0002`, `pyvenv.cfg` had mode `0664`
  and the strict check rejected it.
- After the fix: with caller masks `0002`, `0022`, and `0077`, the file had mode
  `0600`, the venv directory had mode `0700`, and the same check passed.
- The caller's `umask` remained unchanged, system site-packages were disabled,
  and pip was not installed.

A successful installation performed by the user on Ubuntu 26.04.1 x86_64 /
CPython 3.14.4 was also confirmed. SDK 1.30.0, PyJWT 2.15.1, all 29 pinned
versions, `pip check`, SDK import, and prefix isolation passed verification.
Adapter stdio and fake-provider tests using that existing SDK subsequently
passed as well. The [validation record](laf-mcp-validation.md) distinguishes
SDK installation from connection to real services. This evidence does not
validate GPU/NPU operation, 8-16 GB VRAM configurations, real models, another PC,
real MCP-client configuration, or deployment.

## Licensing and changes

The first-party CLI, pin candidates, and documentation are licensed under
KOL-v1. See the repository's `LICENSE` and `NOTICE`. SDK and dependency wheels
are not bundled; their individual licenses apply when downloaded.

2026-11-11: Added the SDK-only CLI, pinned candidates, tests, and installation
guide. Updated PyJWT to 2.15.1 and set the child-process-only creation mask to
`0077`. Preserved the requirement not to overwrite existing environments.

2026-11-12: Replaced personalized resumption commands and review hashes with
general installation instructions and measured results. No changes to CLI
behavior, pinned versions, or permission checks.
