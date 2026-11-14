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

# Prepare a new private LAF runtime

This source-only bootstrap prepares an independent runtime and the standard
plugin configuration. It does not provision working inference backends. All
33 tool schemas remain available, but unconfigured operations return
`BACKEND_UNCONFIGURED` without dispatch. Prepare, configure and doctor never
start a service, load a model or contact an account. A separate, explicit
`snapshot-codex` command can copy an existing trusted native executable into
the private prefix. No SDK, model or authentication material is installed or copied.

## Run from the downloaded source

Use Linux and Python3.11 or later. First obtain the separately pinned SDK using
[the SDK installation guide](laf-sdk-install.md). The interpreter below must
already exist; the bootstrap checks its file metadata, not its package version.
The frontend independently requires MCP SDK1.30.0 when it is actually launched.
Choose a NEW private prefix whose parent already exists. Do not put runtime
state in the source tree or a Codex plugin cache.

```sh
PREFIX="$HOME/laf-runtime"
SDK_PYTHON="$HOME/.local/share/laf-sdk-1.30.0/venv/bin/python"
BOOT=runtime/integration/laf_bootstrap.py

python3 -B "$BOOT" prepare --prefix "$PREFIX" --sdk-python "$SDK_PYTHON" --dry-run
python3 -B "$BOOT" prepare --prefix "$PREFIX" --sdk-python "$SDK_PYTHON"
python3 -B "$PREFIX/runtime/integration/laf_bootstrap.py" doctor --prefix "$PREFIX"
```

`prepare` refuses an existing prefix. It copies real runtime source and the
tracked resource/scheduler policies, creates0700 directories and0600 files, and
validates the bundled public policy. It never calls Runner/isolation preparation,
reads or copies account credentials, global AGENTS, existing runtime databases,
model data or caches. Failed preparation removes only its own newly created
partial prefix when that directory identity is still confirmed. Existing files
and permissions are never repaired or replaced.

Omitting `--sdk-python` is allowed for offline preparation. Doctor then reports
an unconfigured SDK, and activation is refused. No missing interpreter or backend
is silently substituted. Prepare a fresh prefix with the intended interpreter;
there is no in-place update command in this version.

## Bind existing backends before starting a frontend

Configuration is an explicit local operation, not discovery or provisioning.
Run the commands below only for the backends you intend to use. Leave the
others unconfigured; their schemas remain visible and refuse dispatch. Finish
configuration before starting the daemon or using Router receipts. Existing
bindings cannot be changed or rebound in place. A used runtime, database,
cleanup marker, daemon/runner lock or existing receipt is never adopted, even
if it appears idle. Use a new prefix instead of deleting retained evidence.

### Existing owner Router

Obtain the socket and exact profile IDs from your separately operated Router.
The socket must already be owner-controlled0600 under an owner0700 directory,
with the normal trusted ancestry. The command checks only filesystem metadata:
no connect, peer authentication, catalog request, load or generation occurs.

```sh
BOOT="$PREFIX/runtime/integration/laf_bootstrap.py"
ROUTER_SOCKET="/absolute/owner-private/router/service.sock"

python3 -B "$BOOT" configure-router --prefix "$PREFIX" \
  --socket "$ROUTER_SOCKET" --model raw-profile-id --dry-run
python3 -B "$BOOT" configure-router --prefix "$PREFIX" \
  --socket "$ROUTER_SOCKET" --model raw-profile-id
```

Repeat `--model` for an explicit allowlist. These are raw Router profile IDs,
not legacy helper aliases or a promise that a provider is loaded. Later tool
requests retain the real Router peer, catalog, receipt and UNKNOWN/no-replay
guards. A changed socket filesystem identity is refused, not silently rebound.

### Optional existing Codex

The operator must first confirm the source is the native from an already
installed official Codex package, and supply a trusted expected SHA256. Use
the vendor ELF, not npm's JavaScript or symlink launcher. The bootstrap checks
byte identity/drift, not vendor provenance or software safety. The NEW-prefix
native must fit the512MiB bound. A group-writable npm installation is not eligible for direct execution by the
existing native guard. Do not chmod it or weaken that guard. The optional
user-run copy below creates only NEW `PREFIX/bin/codex` with mode0500 under
mode0700; it refuses an existing `bin` destination. It streams at most512MiB,
checks source identity and SHA256 before/copy/after, verifies the protected
copy, and never executes it. Original bytes and permissions remain unchanged.
Use a trusted expected hash: the ELF header and a matching hash alone do not
prove publisher identity or a supported version. Never put this private binary
or prefix in the source marketplace ZIP.

```sh
INSTALLED_NATIVE="/absolute/path/to/trusted/native/codex"
NATIVE_SHA256="replace-with-trusted-64-lowercase-hex-digest"

python3 -B "$BOOT" snapshot-codex --prefix "$PREFIX" \
  --source "$INSTALLED_NATIVE" --sha256 "$NATIVE_SHA256" --dry-run
python3 -B "$BOOT" snapshot-codex --prefix "$PREFIX" \
  --source "$INSTALLED_NATIVE" --sha256 "$NATIVE_SHA256"
NATIVE="$PREFIX/bin/codex"

python3 -B "$BOOT" configure-codex --prefix "$PREFIX" \
  --native "$NATIVE" --sha256 "$NATIVE_SHA256" \
  --workspace "$PREFIX/workspace" --model raw-provider-model-id --dry-run
python3 -B "$BOOT" configure-codex --prefix "$PREFIX" \
  --native "$NATIVE" --sha256 "$NATIVE_SHA256" \
  --workspace "$PREFIX/workspace" --model raw-provider-model-id
```

Skip the copy when an existing protected native path already passes the guard;
set `NATIVE` to that path. Configuration never executes a version/account probe,
reads or copies authentication, global AGENTS, model data or existing databases.
Before the first LAF binding, use your already signed-in official Codex CLI:
open a normal interactive session, enter `/model`, and inspect the choices
shown for that account. The [official command guide](https://learn.chatgpt.com/docs/developer-commands#built-in-slash-commands)
documents the picker and `/status`. If you select a candidate, `/status` shows
the active model; record its exact model ID, not only a display label, then exit
without submitting a prompt. This uses the normal account/cache path and does
not require LAF, a new login, an API key, or a model generation. It can change
the interactive session's selected model. No fixed example ID is guaranteed
for every account. If the UI does not expose a raw ID, use the official
app-server `model/list` metadata interface described in the
[app-server reference](https://learn.chatgpt.com/docs/app-server); do not guess
an ID from a role alias or run a diagnostic generation to discover one.

Pass that raw ID to `configure-codex --model`; repeat the flag for more.
After the guarded daemon and frontend are connected, call LAF
`codex_models_list` before starting a thread or task. This freshly revalidates
the operator allowlist against provider model/effort advertisements. It does
not reuse the picker observation as entitlement, execution, billing or quota
proof; unknown or unavailable entries remain unselectable. With this initial
immutable binding flow, an incorrect allowlist needs a new prefix, not JSON
editing or reuse of an uncertain ledger. Configuration is operator intent,
not entitlement or execution proof; the connected `codex_models_list` still
fetches fresh provider advertisements and effort options and retains unknown/
unavailable states. Per-call efforts are exact advertised strings, not a new
bootstrap enum or alias map.

The dedicated `PREFIX/workspace` is the only initial workspace (`default`).
The existing pipeline enforces read-only sandboxing and on-request approvals.
Its finite schema1 HostGateway policy has concurrency1, four thread records,
sixteen task records and a120s turn budget. This does not change ordinary
`maximum_sessions=8`, claim eight Codex turns, or establish provider quota.

### Atomic configuration and explicit daemon launch

The private runtime's `unconfigured-backends.json` becomes one schema2 record
containing both bindings and their pinned snapshots. Publication uses one
atomic replacement. The copied plugin configuration stays an immutable anchor,
including when activation happened before configuration. New frontends read
one consistent record; existing sessions refuse changed snapshots. Do not
manually repair JSON, remove deny-all state, or retry a possibly published
change after an error. Read doctor output first; no rollback or automatic
replay is attempted after publication uncertainty.

```sh
python3 -B "$BOOT" doctor --prefix "$PREFIX"
python3 -B "$BOOT" daemon-command --prefix "$PREFIX"
```

`daemon-command` only prints the existing guarded user-run command. Review its
paths, then run it explicitly if Codex execution is intended:

```sh
"$SDK_PYTHON" -B "$PREFIX/runtime/private_codex/src/runner.py" \
  --runtime "$PREFIX/runtime/private_codex/.runtime/main" \
  --codex "$NATIVE" --allow-codex-pipeline daemon
```

This is not an automatic start or service installation. The existing normal-host,
namespace tools, user bus, cgroup, resource, ownership and cleanup admission must
pass. No permission denial is repaired or bypassed. Ordinary daemon preparation
can create its own new runtime state and inherit legitimate normal user
instructions; the Codex gateway uses the normal existing account/cache and
legitimate refresh path. Configuration does not promise synthetic-only context
or change account settings. Other optional backends stay denied.

## Connect the standard plugin lookup

The Codex marketplace/cache copy contains source only. The ordinary launcher
reads `~/.config/laf/plugin.json`; the following explicit command creates that
NEW0600 file and any missing0700 parent directories. It refuses an existing file
or symlink. It does not register a plugin or modify Codex settings.

```sh
python3 -B "$PREFIX/runtime/integration/laf_bootstrap.py" activate-config \
  --prefix "$PREFIX" --dry-run
python3 -B "$PREFIX/runtime/integration/laf_bootstrap.py" activate-config \
  --prefix "$PREFIX"
python3 -B launcher.py --check
```

The schema2 configuration points to the copied runtime source outside the cache.
Its historical profile label remains `integrated-32`; it is not the current tool
count. Existing schema1 configurations and their explicit endpoint/model guards
remain supported unchanged. `--check` is static: it does not establish SDK,
account, daemon or model readiness.

For a separate manual MCP registration, an explicit command is also available:

```sh
python3 -B "$PREFIX/launcher.py" --config "$PREFIX/plugin.json"
```

That last command starts the stdio frontend when run by an MCP client, not by
prepare or doctor. The client supplies MCP initialization and tools requests.
Use the normal source-plugin installation instructions for Codex registration;
never include this private prefix/configuration in a plugin upload or archive.

## What is ready, and what is not

Doctor reads the prepared files without opening databases or contacting services.
It validates the private runtime anchor and pinned policy and reports static
interpreter/tool presence. SDK version, account availability, Linux namespace/
cgroup admission and service readiness remain unprobed. `execution_ready=false`
is intentional, not an assertion that the services have failed.

The initial operator file `unconfigured-backends.json` explicitly denies the
ordinary orchestrator, local helper, mailbox, mailbox grants, Codex pipeline,
experiments and Router. This avoids invented mailbox slots, finite grants,
helper implementations, evidence or model selections just to make startup pass.
A missing optional configuration is not treated as an implicit fallback.

The binding commands above connect configuration, not service acceptance. Do not
infer working backend execution from catalog discovery or a metadata check,
translate legacy model prepare into Router load/job requests, or assume native
aliases/effort tokens. Core/Router and the OpenAI account remain separate
prerequisites. The existing daemon, owner, resource and uncertainty rules still
govern execution. The offline tests use fake native bytes and peers, not a real
daemon start, account, model or clean-host backend provisioning. Existing
empirical loader/model evidence belongs to its recorded source cohort; it is
not silently generalized to this new bootstrap.

The self-contained policy is an exact licensed public snapshot, not a fabricated
legacy policy or local Git checkout. See [policy provenance](bootstrap-policy-provenance.md).


## Observed frontend installation

Source commit [`ad0b133`](https://github.com/y-ooya-ipp/laf/tree/ad0b13316f2fabcad751fb834bdb0c32a2f38bb3)
was prepared into a new private prefix and activated in a new isolated Codex
HOME. Official Codex 0.167.0 installed the plugin, and the actual loader used the
existing SDK 1.30.0 to advertise the exact 33 schemas with version 0.1.0. Source,
cache and copied-runtime checks passed; owned cleanup completed normally.
See the [curated validation record](validation/bootstrap-frontend-20261113.json)
and [pull request checks](https://github.com/i-pp/laf/pull/12/checks).

All seven backends remained explicitly unconfigured. This accepts the new-prefix
frontend installation path, not dependency installation on a clean operating
system, configured backend actions, account/model readiness or live deployment.
Resource observations were sampled, not proof of zero pressure or attribution
to a particular process. Later documentation-only additions are not a rerun of
their whole-source ZIP.
