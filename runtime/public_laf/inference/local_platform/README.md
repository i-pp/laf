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

# Local AI Fabric (LAF) client

A standard-library-only client for LAF Router, the existing guarded local
inference service. It does not need Codex CLI, Claude Code, a paid API, an external
daemon or MCP. See [naming and migration](naming.md) for the LAF module paths,
service names and the source migration boundary.

## Busy-reader summary

- Select the active Router's configured model IDs without bypassing resource/switch guards.
- Submit once, poll, inspect or cancel a job, including after an uncertain outcome.
- Repair small pure Python functions against declarative cases, without executing
  arbitrary shell, model-proposed imports or executable test files.

## Run with an already accepted local service

This client procedure assumes the [GPU service](../laf_service/README.md) is
already configured and healthy. It is not a fresh-host installation guide. Run
from this repository root, with Python 3.11+ on Linux:

```sh
SOCKET="$XDG_RUNTIME_DIR/laf-inference/service.sock"
python3 -B -m inference.local_platform.cli --socket "$SOCKET" status
python3 -B -m inference.local_platform.cli --socket "$SOCKET" models
python3 -B -m inference.local_platform.cli --socket "$SOCKET" capabilities
python3 -B -m inference.local_platform.cli --socket "$SOCKET" ask \
  --prompt 'Explain one cause of a Python KeyError.' \
  --max-tokens 256 --timeout 180 --output /NEW/answer.json
python3 -B -m inference.local_platform.cli --socket "$SOCKET" get JOB_ID
python3 -B -m inference.local_platform.cli --socket "$SOCKET" cancel JOB_ID
```

The omitted model uses the Router's declared default. For explicit selection,
use `--model` with an ID returned by `models`; the legacy catalog includes
`gpt-oss-120b`. See [configured models and optional Flash](../../docs/laf-configured-models.md).
Choose a genuinely new output path, or omit `--output` for JSON on stdout. Code
sent by `ask` is advice only, not applied or executed. Length-capped output may be
incomplete. Generation is never silently retried. Ctrl-C requests cancellation
once and reports `stop_confirmed:false`; use `get` to check the terminal state.
A lost submit reply may leave an accepted job with no client-known ID. Do not
blindly repeat that request. The server's deadline still bounds it.

## A bounded repair loop

The input source is never overwritten. The work directory must not exist:

```sh
python3 -B -m inference.local_platform.cli --socket "$SOCKET" repair \
  --source inference/local_platform/examples/calculator.py \
  --cases inference/local_platform/examples/cases.json \
  --workdir /NEW/private-repair-run --model gpt-oss-120b --attempts 2
```

The client runs a baseline, asks the selected local model for a complete candidate,
checks a small AST grammar, writes canonical UTF-8 from the validated AST, and runs
cases. On a test failure it sends the diagnosis into the next attempt, at most
three attempts. Transport uncertainty stops the loop rather than resubmitting.
`result.json` records actual test exit status and the accepted candidate is
`candidate.py`. Passing the supplied cases is evidence for those cases only.

This is useful for arithmetic/list/dictionary pure-function bugs. It is **not a
general repository agent** and does not fix arbitrary Python packages. Accepted
source contains only functions with unchanged positional signatures, arithmetic,
comparisons, local assignment, if/return, collection literals/subscripts and a
small set of pure builtin calls. No imports, attributes, decorators, private names,
loops, arbitrary calls, file/network/process APIs or executable tests are allowed.
The correct expression is not hard-coded into the validator. JSON cases use
`function`, positional `args` and `expected`; values are bounded primitives.

AST validation is not advertised as an arbitrary-code sandbox. Only this narrow
language runs in a child with CPU 3s, address space 256MiB, output file 1MiB and a
10s wall deadline. Canonicalization strips encoding cookies and comments before
execution. The trusted runner uses Python isolated mode and a minimal environment.
Tests, logs and candidates stay in the new private directory. Do not put secrets
in source or test data. There is no automatic original-file writeback or commit.

## API and remote boundary

The product-independent contract is [API.md](API.md). It uses HTTP/JSON over the
operator's owner-only Unix socket, with one GPU queue for the selected catalog. A
missing NPU is reported separately and does not stop GPU requests. NPU inference
uses an independent classification lane through the same private API, while the
separate NPU service owns its worker and recovery:

```sh
WORKDIR=$(mktemp -d)
python3 - <<'PYGEN' > "$WORKDIR/classify.json"
import json
print(json.dumps({'model':'ryzenai-quicktest-10class',
                  'input':[float(i%251)/250 for i in range(3072)]}))
PYGEN
python3 -B -m inference.local_platform.cli --socket "$SOCKET" classify \
  --input "$WORKDIR/classify.json"
```

This synthetic input tests plumbing, not label accuracy. The client validates
VitisAI/NPU identity and explicitly preserves mixed CPU/NPU scope. The model has
no validated application labels/preprocessing. Classification never queues behind
GPU generation or silently falls back to CPU. Busy returns 429, unavailable or an
uncertain outcome 503, and no request is replayed. Ctrl-C ends waiting without
claiming NPU stop. Private dated NPU acceptance records are omitted.

This source candidate makes no hardware acceptance claim. Two physical PCs,
WSL, LAN authentication and a remote transport require separate validation. This
change opens no TCP port, creates no credentials, changes no SSH or firewall
settings and adds no cloud control plane. Do not point the CLI directly at
Ollama: doing so bypasses admission, layer limits, cancellation and release guards.
Codex/Claude Code/OpenCode compatibility is optional follow-up work, not P0.

## Tests

```sh
python3 -B -m unittest inference.local_platform.test_cli \
  inference.local_platform.test_repair -v
```

The Unix-socket unit test needs a host execution context that permits a private
local socket. The pure validator and repair tests use no model or network.
Private dated repair acceptance records are omitted. See
[development scope](development-scope.md) for requirements before broader
repository/tool access.

## Selected project modules

For manifest-scoped project files and mandatory containerized post-edit tests,
see [restricted project tools](workspace-client.md). This is a separate opt-in
client module, not a new server listener, production writeback or arbitrary-shell
agent. Private historical model-comparison and regression acceptance records
are omitted; run and review your own task checks.

Correct test-design ability remains unmeasured by the fixed-test repair demo.
A separate evaluation should use independent expected values, coverage and
mutation detection. The historical test-quality design is omitted from this
source candidate and does not expand current client permissions.
