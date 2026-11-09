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

# LAF model management: offline-tested first slice

## Busy-reader summary

This increment adds catalog, install, list, remove and storage status to the
existing stdlib CLI. It does not replace the Router or load a model. The bundled
catalog preserves `flash`, `coder-next` and `gpt-oss-120b`, but deliberately leaves
unverified provenance, versions, capacity and hashes empty. Those entries cannot
be installed until a user supplies verified metadata.

Run these safe, local checks from the repository root:

```sh
python3 -B -m inference.local_platform.cli model catalog
python3 -B -m inference.local_platform.cli model list
python3 -B -m inference.local_platform.cli model status
python3 -B -m unittest -v inference.local_platform.test_models
python3 -B scripts/check-laf.py --suite portable
```

The implementation uses the existing module entry point. A packaged `laf`
executable, engine provisioning and complete Docker Compose installation remain
separate work. These commands are not a complete Quick Start for a running LAF.
The existing `status` command still queries the private Router and now includes
an independent local `model_storage` summary. Use `LAF_MODEL_STATE_DIR` to select
the same registry used by model operations; otherwise it reads
`~/.local/state/laf-models`. Catalog, list and local model status do not create directories or contact
Ollama. The opt-in inspect command makes GET-only requests to configured Ollama. Registry entries describe the last verified installation, not a fresh
engine inventory. `loaded_state: not_queried` must not be read as “unloaded.”

## What is implemented

- Strict, bounded catalog metadata: ID, engine, exact tagged reference, source,
  license and license URL, version, capacity estimate, engine manifest SHA-256,
  and a user license/authentication setup requirement.
- Ollama's existing management APIs: `GET /api/tags`, `GET /api/ps`,
  `POST /api/pull` with streaming disabled and `DELETE /api/delete`.
  Bare hexadecimal wire digests are normalized to `sha256:<hex>` in the registry.
- Separate persistent private JSON registry, bounded operation history, atomic
  replacement and fsync. Directory creation is also synced before a mutation.
- Nonblocking operation lock and the existing Router lifetime generation lock.
  An active Router or any loaded model blocks install/remove. Nothing is stopped,
  unloaded, restarted or migrated to make management work.
- Durable write-ahead pending journal. An uncertain engine outcome or digest
  mismatch leaves the pending operation in place and blocks later mutations.
  No pull/delete retry or automatic cleanup is attempted.
- Removal only of models previously installed by this registry, with the same
  engine scope and digest and no loaded models. A matching preexisting unmanaged
  model is refused rather than adopted or later deleted.
- Registry binding to numeric-loopback engine identity, model-store path and
  generation-lock path. Changing that scope does not confer deletion rights on
  another engine. Endpoint identity is not a cryptographic server identity.
- No Docker socket, Docker commands, new listener, credentials, shell commands,
  inference calls or license acceptance in this module.

All three compatibility profile IDs remain available in the Router. This module
only implements an Ollama mutation adapter. Strata/Flash installation remains
blocked until its artifact format and provenance contract are verified. The
existing Flash residency, large-model exclusion and physical resource-release
barriers are unchanged.

## Operator-supplied inputs for a future live acceptance

Do not copy the following placeholders into a production config and claim setup
is complete. No live install/remove was performed for this increment.

Both catalog and configuration must be regular owner-only files (`0600`) inside
an owner-only directory (`0700`), with no symlink or hardlink aliases. The selected
model-store directory must already exist and be owner-only. The state directory's
parent must already exist; the state directory can be created privately on first
mutation. The generation lock must already be the exact existing Router lock;
this code never creates it or changes its mode.

A configuration has exactly these fields:

```json
{
  "state_dir": "/OWNER_CONTROLLED_PERSISTENT_STATE/laf-models",
  "model_store": "/EXISTING_PERSISTENT_OLLAMA_STORE",
  "generation_lock": "/EXISTING_ROUTER_RUNTIME/generation.lock",
  "engine_url": "http://127.0.0.1:11434"
}
```

Only explicit numeric loopback HTTP endpoints and ports are accepted. There is
no proxy, redirect following, DNS hostname, remote transport or default engine
endpoint. The code does not discover the current host Docker container address
or start an engine. This protects existing service boundaries; it also means the
current Router-owned internal Docker endpoint is not automatically usable here.
An independently reviewed managed-engine/container control boundary is still
needed for that integration.

The operator must verify that the selected Ollama server's actual `OLLAMA_MODELS`
store is backed by the named persistent storage, and that registry state is also
on persistent storage. `model_store` is used for a free-space preflight; an HTTP
client cannot prove the server's filesystem mapping. This increment does not
claim verified model-volume persistence or clean-host/container acceptance.
The capacity number is a preflight estimate, not a hard engine download quota.

A private catalog is a JSON array. Each entry has exactly these keys:

```json
{
  "id": "USER_CHOSEN_PROFILE",
  "engine": "ollama",
  "reference": "OWNER/MODEL:EXACT_TAG",
  "source": "https://OFFICIAL_MODEL_SOURCE",
  "license": "VERIFIED_LICENSE_IDENTIFIER",
  "license_url": "https://OFFICIAL_MODEL_LICENSE",
  "version": "VERIFIED_VERSION",
  "required_bytes": 1,
  "digest": "sha256:REPLACE_WITH_64_VERIFIED_LOWERCASE_HEX_DIGITS",
  "user_setup_required": true
}
```

The placeholders are intentionally invalid and the size is not a model estimate.
Do not invent a digest, license or capacity from the model's marketing name.
`digest` is the Ollama manifest digest, not necessarily the SHA-256 of one weight
file. Distinct IDs cannot alias the same engine reference. Explicit Ollama tags
are required; the expected digest detects content changes behind a mutable tag.

If explicit license acceptance or authentication is necessary, the user must
complete it directly through the provider's official interface. LAF does not
perform or bypass it. `user_setup_required: true` always blocks install. A catalog
entry with `false` is an operator declaration that required user steps are
complete or not required; it is not legal verification by this code.

Only after those inputs and the storage mapping are verified, and no service or
user workload holds the existing generation lock, the relevant command shapes
are:

```sh
python3 -B -m inference.local_platform.cli model --catalog /PRIVATE/catalog.json --config /PRIVATE/config.json install MODEL_ID
python3 -B -m inference.local_platform.cli model --config /PRIVATE/config.json list
python3 -B -m inference.local_platform.cli model --config /PRIVATE/config.json status
python3 -B -m inference.local_platform.cli model --config /PRIVATE/config.json remove MODEL_ID
```

Remove is destructive to the named managed engine model. It never deletes the
source catalog or directly traverses/removes model files. Do not run these live
mutation examples against existing user models merely to test the code.

If an operation reports uncertainty, first inspect engine inventory and the
private pending journal without retrying the mutation. Recovery/reconciliation
needs an explicit follow-up implementation or a carefully reviewed operator
procedure. Deleting the journal to force a replay is not a supported recovery
method.

## Verification and remaining acceptance

The model tests use fake engines, temporary directories, synthetic metadata and
the documented Ollama API response shapes. They cover ownership, stale registry
records, manifest digest normalization, active generation locks, uncertainty,
capacity and operator-supplied metadata requirements. Run the explicit portable
suite above for this standalone tree; it lists all socket-only tests as unexecuted.
Socket-capable executors can run `python3 -B scripts/check-laf.py --suite full`.

API shape references:

- <https://docs.ollama.com/api/tags>
- <https://docs.ollama.com/api/ps>
- <https://docs.ollama.com/api/pull>
- <https://docs.ollama.com/api/delete>

Live engine lifecycle, persistent-volume replacement, crash recovery, Strata
artifact management, clean-host installation and physical second-PC acceptance
remain separate work. Unit/mocked success does not complete those gates.

## V2: non-mutating engine inspection

V2 adds a GET-only observation path, using the same explicit private configuration:

```sh
python3 -B -m inference.local_platform.cli model --catalog /PRIVATE/catalog.json --config /PRIVATE/config.json inspect MODEL_ID
```

This requests only `/api/tags` and `/api/ps`. It does not create registry state,
acquire the generation lock, download, delete, load, unload, generate, log in or
accept legal terms. It can observe an engine while the Router's generation lock
is held. A CLI-level mock test verifies exactly the two GETs while that lock is
held, and verifies that no state directory is created.

`installed` and `loaded` describe the returned engine inventories for the exact
catalog reference. `digest_matches` compares the observed engine manifest with
the catalog's expected manifest digest. It is null if either comparison input is
unavailable. `registry_record_matches` describes saved registry ownership within
the configured engine scope. `managed` additionally requires an observed model
with the saved digest, so changed or missing engine content is not reported as
currently managed. This inspection never creates ownership.

An inventory result does not establish model compatibility, current weight-file
hashes or the actual engine store's volume mapping. The output explicitly labels
those checks as not evaluated, not recomputed and not verified. The two GETs are
separate observations, not an atomic engine snapshot; other authorized clients
can change engine state between them.

## Evidence and installable metadata

Historical private model inventories and host acceptance reports are not included
in this standalone source draft. The regression fixture at
[`provenance-synthetic.json`](../inference/local_platform/fixtures/provenance-synthetic.json)
is explicitly synthetic. It grants no rights and describes no acquired asset.
Tests verify that it cannot be used as an installable catalog and that the built-in
catalog still leaves every unverified provenance field empty. All built-in installs
remain blocked before contacting an engine.

An experiment report hash, runtime image digest, executable hash, model weight
hash and engine manifest digest identify different objects. Do not substitute one
for another, or transfer evidence across similarly named quantizations.

A complete installable entry still needs evidence for the exact chosen artifact,
its license and user-required steps, per-file identity/size, engine manifest,
compatible packaging and persistent store/capacity. No complete built-in entry or
new runtime-compatibility result is supplied by this source draft.
