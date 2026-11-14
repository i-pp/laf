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

# Connect a local MCP client to LAF

## Busy-reader summary

This local source candidate requires **MCP SDK 1.30.0 exactly**. The user has
reported a successful SDK-only installation. Native SDK 1.30.0 stdio, framing,
cancellation and fake-Router integration checks subsequently passed on Linux /
Python 3.14.4. This does not install or activate the adapter or accept a live
connection. See the [scoped validation record](laf-mcp-validation.md).

Use this adapter when a local MCP client should submit jobs to an already accepted
LAF Router. It adds five tools and keeps inference/resource ownership in the Router.
A job may start or switch configured providers. The adapter has no loaded-only
promise, recovery tool, model installer or shell access.

First inspect the source without contacting a Router:

```sh
python3 -B mcp/launch.py --help
python3 -B mcp/launch.py --version
PYTHONPATH=mcp/src python3 -B -m unittest discover -s mcp/tests -p test_sdk_gate.py -v
```

Help/version and the mocked SDK guard checks are offline; the latter do not
establish actual SDK compatibility. With the existing SDK 1.30.0 interpreter,
run the real stdio/fake-Router checks without installing anything:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=mcp/src:mcp/tests \
  /ABSOLUTE/REVIEWED/VENV/bin/python -B -m unittest \
  test_protocol test_integrated_router -v
```

These test classes require actual SDK 1.30.0 and fail on another version. They
use synthetic data, private temporary receipts and subprocess pipes, not real
models. The launcher initialization test exercises the real startup guard without
contacting a Router. Existing LAF checks remain separate:

```sh
python3 -B scripts/check-laf.py --suite portable
python3 -B -m unittest discover -s scripts -p test_client_install.py -v
```

These are source/adapter checks, not a complete fresh-host runtime Quick Start.

## What belongs where

LAF Core and Router implement inference and resource/lifecycle guards. A daemon
is a hosting arrangement. Standard orchestration is a separate logical concern.
This MCP package is an external interface to the public Router API; it does not
absorb the Core, host supervisor or a private orchestration repository.

Local models and this interface need no OpenAI account. Any optional Codex
integration keeps its own authentication outside LAF Core. Local stdio also does
not activate an existing remote ChatGPT MCP catalog or provide web/mobile access.
Remote hosting, credentials, relays and catalog refresh need their own verified
setup and authorization. No remote endpoint is included.

## Prerequisites and explicit operator choices

Use an existing isolated interpreter with MCP SDK **1.30.0** and reviewed
dependencies for the actual protocol checks. The SDK-only installer guide is
[separate](laf-sdk-install.md). Its successful installation does not activate the
adapter or change a client's interpreter selection. Actual connection also needs
an accepted Linux Router and the release gate below. Python >=3.10 is package
metadata only: historical cloud tests used 3.12; the SDK installation and
subsequent native checks used 3.14.4. The selected transitive closure includes rpds-py requiring >=3.11;
it is not a tested Python 3.10 lock. Linux SO_PEERCRED and Unix sockets are required.
No Windows/macOS, GPU/NPU, 8-16 GB fit or clean-host inference claim follows.

The SDK pin and official archive hashes are in `mcp/sdk-pin.txt`; the dependency
review is in `mcp/dependency-review.json`. This is not a full transitive hash lock.
The selected 1.30.0 fixes CVE-2026-59951 in the 1.x line; stdio-only servers were
explicitly outside that advisory's affected scope. This does not approve remote hosting. No packages are
installed or updated automatically. Preserve the repository's Docker-by-default
runtime policy; a containerized adapter must never receive the Docker socket,
privileged mode, provider lifecycle or device authority. Such container deployment
is not included or tested here.

Configure the local MCP host's environment, not tool arguments:

- `LAF_MCP_MODELS`: required comma-separated exact public IDs allowed by the operator.
- `LAF_MCP_STATE_DIR`: required absolute new receipt directory, with an existing
  trusted parent. The adapter creates it with mode 0700 and metadata files 0600.
- `LOCAL_INFERENCE_SOCKET`: optional absolute existing socket; default is
  `/run/user/<uid>/laf-inference/service.sock`. No endpoint is created or repaired.

For example, **after checking those values on the accepted host**, the stdio
process can be started by that host using its reviewed Python interpreter:

```sh
LAF_MCP_MODELS=flash \
LAF_MCP_STATE_DIR=/ABSOLUTE/OWNER/STATE/laf-mcp \
LOCAL_INFERENCE_SOCKET=/ABSOLUTE/PRIVATE/service.sock \
/ABSOLUTE/REVIEWED/VENV/bin/python mcp/launch.py
```

Replace all absolute placeholders; `flash` must actually be both intended and in
the Router catalog. This command waits for MCP protocol messages on stdin and
writes only protocol messages to stdout. It does not print an interactive prompt,
install/start a service or provision a model.

The root `mcp.json` uses `python3` with `${PLUGIN_ROOT}/mcp/launch.py`. Verify which
interpreter the actual MCP host resolves and supply its environment explicitly;
a desktop app may not inherit a terminal's activated virtual environment. Source
packaging does not authorize installing the plugin or editing a host's config.

## Five tools, one bounded job path

- `laf_router_models` lists the catalog intersected with the allowlist. It does
  not treat a listed model as ready or installed.
- `laf_router_status` reports separate non-atomic health/capability observations.
  `not_ready` remains not ready; GPU readiness does not attest NPU execution.
- `laf_router_submit_job` requires `attempt_id`, `model` and 1-32 role/content text messages.
  Optional output/deadline bounds retain Router limits. It makes at most one POST.
- `laf_router_get_job` accepts the returned opaque `handle`, not an arbitrary
  Router ID. Completed output is unverified text, never executed.
- `laf_router_cancel_job` accepts the same handle and makes at most one DELETE
  per owned receipt. Its acknowledgment does not prove physical provider stop.

The adapter checks directory/socket modes, ownership, non-symlink paths, connected
peer UID and consistent endpoint identity. Receipts bind the exact selected model,
socket/parent identity and observed peer PID/UID. The v1 Router has no durable epoch
or cryptographic instance/model attestation. Restart/replacement can make old
receipts unavailable instead of allowing operations on another instance.

## Keep uncertainty visible

The caller must generate and retain a random 32-hex `attempt_id` before submitting.
The adapter uses that value as the opaque receipt handle and fsyncs an UNKNOWN
intent before preflight and again with endpoint identity before its only POST.
Reusing that nonce with the same canonical request returns the existing receipt,
including NOT_SUBMITTED, without issuing another POST. A different request using
the same nonce is a conflict. A fresh nonce means deliberately new work; never
regenerate it automatically after uncertainty. Valid IDs can still collide, so
collision/conflict handling fails closed rather than overwriting a receipt.

The owner-only record contains a SHA256 fingerprint of the canonical complete
request (model, messages and explicit/default bounds) for equality checking.
This derived metadata can allow dictionary guesses of predictable requests; it is
not anonymization or encryption. The digest is never returned or logged. No raw
prompt, completion or backend error is persisted. Admission is
not completion. Failed/malformed I/O after possible transmission does not mean
rejection. There is no automatic POST retry, recovery or model fallback.

Use the returned handle to query an acknowledged job. If the acknowledgment was
lost before a job ID arrived, v1 cannot find that exact submission. A lost adapter
reply does not lose the caller-known attempt handle: use that same handle with
get_job after reconnecting. A repeated submit with the same nonce only returns
the recorded attempt; it never resumes or repeats generation. If the Router ACK
was lost before a job ID arrived, that receipt stays UNKNOWN because Router v1
has no idempotency key or lookup by this nonce. This does not deduplicate a new
nonce for similar work.

The store is bounded to 128 receipts; unresolved intents are never silently pruned.
A full store refuses new submissions. Four retained temporary-write files also
block further updates rather than growing without bound. Maintenance/deletion is
an explicit operator operation outside these tools. Router results themselves are
in-memory, capacity 128 with ten-minute creation-age pruning; 404 does not prove
that generation never occurred.

Input and complete output JSON-RPC frames are limited to 65536 UTF-8 bytes. Output
checks include escaping and any text/structured duplication. If a completion is
too large, the reply preserves its handle, job ID and known status without
silently truncating text. UNKNOWN remains UNKNOWN on oversized/error paths.

Three normal requests and one cancellation call have separate bounded admission.
A session accepts at most 1024 frames; request IDs are unique within it and at most
128 UTF-8 bytes. Invalid/oversized framing closes the session without echoing its
contents. Start a new local session when needed. Transport cancellation does not
cancel Router work: the adapter finishes its bounded operation and records the
outcome; use the explicit job cancellation tool instead.

## Source-only CI is not SDK acceptance

The local Minimum CI integration explicitly selects 74 MCP source/mock cases,
including the plugin manifest, plus 56 SDK-installer regressions without fetching
or installing an SDK. It excludes the 12 actual-SDK protocol/integration cases
and two package-build/install cases. Those exclusions remain separate gates; an
SDK-free CI pass cannot establish framing/cancellation compatibility. The full
Router/Core/client Python command also remains a separate source suite.

## Package checks need a separate build environment

`test_package.py`, `test_sdist.py` and `scripts/test_client_install.py` require
existing packaging tools; the wheel tests also create disposable test environments
and install only first-party wheels there. Do not run those tests with the
SDK-only runtime interpreter when it has no pip/setuptools. Do not add build tools
to the installed SDK venv. A full `unittest discover -s mcp/tests` includes these
build checks as well as the runtime tests, so it is not the SDK-only command.

## Build a wheel without changing the host

With approved existing build tools, from the repository root:

```sh
python3 -m pip --isolated wheel --no-index --no-deps --no-build-isolation \
  --no-cache-dir --wheel-dir /ABSOLUTE/NEW/wheels ./mcp
```

This builds only the adapter wheel. It does not install the external SDK. In a
separately approved isolated runtime, install the reviewed wheel/dependencies and
verify the `laf-mcp` entrypoint. Do not use `sudo pip`, alter host packages, mount
Docker authority or rename an arbitrary public-index package to LAF. Source and
wheel checks cannot replace real local-client installation acceptance.

## Dependency release gate

The adapter now requires **MCP SDK 1.30.0 exactly**. The previous frozen
candidate's SDK 1.29.0 / Python 3.12 protocol results remain historical evidence;
they do not verify this new SDK selection. The user reported a successful SDK-only
installation on Ubuntu 26.04.1 x86_64 / Python 3.14.4, including `pip check`,
low-level Server/stdio imports, all 29 selected versions and prefix isolation.
That installation alone is not adapter acceptance. Subsequent native checks of
the actual SDK 1.30.0 passed: 86 MCP runtime cases, 513 Router/Core/client Python
regressions and a four-case launcher harness (three private-UDS fake-provider
cases and one missing-SDK startup refusal).
These groups are not additive coverage totals. The source suite does not include
MCP or package-build tests. See the [validation record](laf-mcp-validation.md) for
evidence limits and `../mcp/dependency-review.json` for dependency scope. Actual
local-client configuration, live Router and deployment acceptance remain open.

The official [SDK support policy](https://github.com/modelcontextprotocol/python-sdk/security)
supports only the newest 1.x maintenance release. Recheck support, the complete
platform-specific dependency closure and advisories before future installation
or release. The SDK-only installer selects PyJWT 2.15.1; its archive/review records
remain separate from this adapter source. No SDK or transitive package is bundled.

The [official advisory](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-84m7-p3x7-pcfv)
affects 1.8.0-1.29.1 and 2.0.0-2.1.1 stateful Streamable HTTP; 1.30.0 is the fixed
1.x release. It explicitly excludes stdio-only servers. This is not exhaustive
security clearance or approval for remote hosting.
