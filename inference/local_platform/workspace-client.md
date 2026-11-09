# Restricted project tools for the local client

The client can read selected project files, propose a small text edit and run
operator-selected Python tests in a bounded container. This extends beyond the
pure-function grammar. It still cannot run arbitrary shell commands, alter the
original checkout, choose its own permissions or publish/commit a result.

## Start with a reviewed task manifest

Use Linux and the already accepted local inference service. This is an existing
assets procedure, not a clean-host Quick Start. A compatible test image must
already be cached and approved by the operator; there is no image pull or host
Python fallback. Model code never runs in the client host process.

Create an operator manifest naming each file explicitly, with separate read/write
access and a fixed unittest selection:

```json
{
  "version": 1,
  "goal": "Fix the identified cancellation regression without changing tests.",
  "files": [
    {"path":"inference/local_platform/cli.py","access":"write"},
    {"path":"inference/local_platform/__init__.py","access":"read"},
    {"path":"inference/local_platform/test_cli.py","access":"read"},
    {"path":"inference/local_platform/bounded_patch.py","access":"read"},
    {"path":"inference/aceis_service/__init__.py","access":"read"},
    {"path":"inference/aceis_service/npu_bridge.py","access":"read"}
  ],
  "test": {
    "kind":"python-unittest",
    "start_dir":"inference/local_platform",
    "pattern":"test_cli.py"
  }
}
```

This manifest illustrates a bounded repository task. A source snapshot that
already passes the selected tests returns `already_passing` without an LLM request. Supply a real
failing task you have approved, not a goal alone. Include its required source and
read-only test dependencies. Unlisted files are not copied or available to tools.

After obtaining and reviewing a compatible local image, replace the placeholder
with its immutable image ID and confirm the interpreter path inside that image:

```sh
IMAGE=sha256:REPLACE_WITH_REVIEWED_LOCAL_IMAGE_ID
python3 -B -m inference.local_platform.developer \
  --socket "$XDG_RUNTIME_DIR/aceis-inference/service.sock" \
  --source /ABSOLUTE/approved-project --manifest /ABSOLUTE/task.json \
  --workdir /NEW/private-task --model coder-next --max-turns 6 --deadline 300 \
  --test-image "$IMAGE" --reviewed-test-image "$IMAGE" --test-python /usr/bin/python3
```

No test image is included or claimed available. Verify its source/license,
required dependencies and exact ID before adding it to the operator-reviewed
allowlist. The model cannot populate that list. Cached official
Python images use their standard interpreter path instead. No mutable image tag
is accepted for execution.

## File capabilities and tools

The source checkout is read-only input. The task directory must not exist. The
client copies at most 64 named UTF-8 files (eight writable), 64 KiB each/1MiB total
into a new 0700 directory. It refuses symlinks, hardlinks, special files, unsafe
paths and hidden credential/control paths. Read-only copies are 0400, writable
copies 0600, and private controller metadata lives outside the tool-visible tree.
The operator must still exclude secrets; a filename filter cannot recognize all
sensitive content. No repository-wide scan or implicit home-directory access.

The local model returns strict JSON as plain final-answer text with exactly
`tool` and a nested `arguments` object, not a native function-call message. Fenced
JSON, duplicate keys, nonstandard numbers, unknown tools and added permission/
image/command fields are rejected. Available tools:

- list_files: paginated authorized paths/access only.
- read_file: approved text, a bounded line range and the full-file SHA-256.
  A larger model hint is explicitly clipped to 60 lines/2 KiB; it never grants
  access to more files or a larger response.
- replace_text: writable file, expected SHA-256 and one exact old/new match.
- run_tests: the operator's fixed test selection, with no command arguments.
- finish: a summary, accepted only with actual changed files and passing tests
  bound to the same snapshot content and identity.

Every accepted edit automatically runs the fixed test selection. A matching
changed candidate is marked complete only after actual tests and cleanup pass;
the decision to run tests or claim success is not left to the model. Explicit
run_tests/finish records cannot bypass those checks.

Hashes prevent blind stale edits. Tests cannot be edited through these tools.
Canonical controller/snapshot identities, exact file sets and approved-file
hashes are rechecked. A passing result must include nonzero actual test count,
no decrease from baseline, confirmed cleanup and matching tested content. Model
statements of PASS do not substitute for this evidence. The resulting diff still
requires independent review; there is no automatic original-worktree writeback.

## Execution boundary and failure semantics

Project imports/tests run only in a new exact-owned container with network none,
read-only root/snapshot, no added capabilities, no-new-privileges, caller UID/GID,
no GPU/Docker socket, 512 MiB memory/no extra swap, one CPU, 64 PIDs, bounded tmpfs,
output and time, zero core dumps and 128 file descriptors. A separate trusted
PID1 watchdog does not import project code and stops the namespace on its fixed
wall deadline even if the host attach client disappears. The trusted runner checks what is actually mounted before
project imports and keeps test evidence separate from model prose. Cleanup uses
only the exact name/label/image/immutable container ID created for this run.
No broad process or container cleanup is performed.

Missing prerequisites before execution are blocked. Once work starts, test,
transport, timeout or cancellation failures are retained as failed/cancelled/
uncertain rather than relabeled blocked. Unknown inference outcomes stop the loop
without replay. Tool turns and task deadlines are bounded. A missing/zero-test
summary or unconfirmed stop cannot become success.

The private same-user boundary is not protection against a malicious process with
full access to the host account or Docker daemon, and Docker is not a hostile-
kernel security guarantee. Do not mount the original checkout, credentials or
host Docker socket into test containers. Do not weaken a missing isolation layer
or fall back to host execution to make a task pass.

## Evidence and next stages

Private historical acceptance records are omitted. Validate unit/mocked behavior,
real container containment and local-model task execution separately. A deliberate
regression with fixed tests is not an unknown production bug or a general
completion-rate benchmark.

Physical client/server separation, authenticated remote transport/WSL, broader real-project tasks and held-out tests,
multi-client scheduling and container redistribution remain separate gates. Future
NPU task interpretation/delegation/evaluation is not implemented by this client;
a missing suitable NPU model must not stop P0 development. No silent cloud or
CPU/NPU fallback is introduced here.
