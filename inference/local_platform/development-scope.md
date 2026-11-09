# Extending from pure functions to real development tasks

The current repair client completes small pure-function tasks against declarative
cases. Its grammar permits useful candidate changes but cannot repair imports,
project configuration, tests, multiple files or a real application. A tiny fixed-case
sample is not evidence of general development capability.

The next acceptance should use actual small repository bugs with held-out tests,
not merely prompts or successful model-server requests. Report task completion,
regressions, attempts, time, resource peaks, human interventions and failure causes.
Compare model/role choices on the same task set rather than presuming a coding
model or a 120B model is always better. No paid API should be a hidden fallback.

Before enabling broader edits/tools:

1. Use a new worktree/private task workspace, preserve dirty user work, and record
   an explicit target revision and file-write scope. Reject absolute/traversal/
   symlink/hardlink escapes, cap file/diff sizes and produce a reviewable patch.
   Never automatically overwrite the original checkout or merge.
2. Prefer structured read/search/patch/test operations. Do not execute shell text
   returned by a model. Test commands and dependency/build steps must be selected
   from operator-approved task configuration, with an auditable outcome.
3. Establish a real bounded execution environment before accepting arbitrary
   project code: nonprivileged, no host networking, no Docker socket, no secrets,
   read-only base, narrowly writable task files, process/memory/CPU/time/output
   limits, cancellation and owned-descendant cleanup. Do not weaken an unavailable
   namespace/security boundary or relabel a failed sandbox as success. The current
   pure-function grammar/resource limits are not this arbitrary-code environment.
4. Make attempts/deadlines/resource budgets explicit. Separate uncertain transport
   or process outcomes from known test failures. Only known, permitted failures
   may enter a bounded diagnose/patch/test loop. Surface stop uncertainty and do
   not retry consequential work silently.
5. Version the structured tools/results and verify multi-turn/model-role replay.
   Keep GPU large-model switching in the existing server lane. NPU work has its
   own bounded lane and truthfully reports mixed execution. Classification/model
   accuracy and preprocessing need a suitable application model before using NPU
   results to guide real task decisions.
6. Validate logical client/server separation first, then a second WSL machine with
   authenticated encrypted transport, host verification and least-privilege
   identity. No unauthenticated LAN listener, credential persistence or broad
   remote shell access is implied by the current API.
7. For distributable containers, verify each component's redistribution rights,
   pinned images/build inputs, SDK/driver ABI, model/cache volume policy and
   clean-machine instructions. Free use and redistribution are separate license
   questions. Publication requires a distinct release decision.

Codex, Claude Code and OpenCode adapters can be optional clients after the local
API, execution bounds and actual task-completion evaluation work. They are not a
substitute for these requirements and are not a P0 dependency.
