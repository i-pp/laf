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

<!-- Modified 2026-11-10: distinguish client observations, generated text and truncated turns. -->

# Talk to an existing local Router

## Busy-reader summary

Run `laf` without a subcommand to open a small terminal conversation with an
already configured LAF Router. It uses the existing JSON job API and owner-only
Unix socket. The same conversation is available explicitly as `laf chat`.

From a checkout, use `./laf`. To put `laf` on a dedicated virtual environment's
PATH, follow the [client installation guide](client-install.md). Starting the
prompt does not install or start a Router, engine or model.

This command exercises only local help and exit, with no model request or running
Router needed:

```sh
printf '/help\n/quit\n' | ./laf
python3 -B scripts/check-laf.py --suite portable
```

With an accepted Router at the default private socket, no socket option is needed:

```sh
./laf status
./laf
# Optional: choose a model and permit filename search in an explicit workspace.
./laf chat --model flash --workspace "$PWD"
```

The default is `/run/user/<uid>/laf-inference/service.sock`, using the client's
current user ID. `LOCAL_INFERENCE_SOCKET` can select another existing private
socket. An explicit `--socket` takes priority over that environment variable:

```sh
SOCKET=/ABSOLUTE/PRIVATE/service.sock  # Replace with the accepted existing socket.
./laf --socket "$SOCKET"
./laf --socket "$SOCKET" chat --model flash
```

The implicit conversation uses the selected Router's declared default, resolved
on the first ordinary message. The legacy default remains `gpt-oss-120b`.
It keeps at most 512 output tokens and a 180-second timeout. Use the explicit
`chat` subcommand for its model, output, timeout and workspace options. `--help`
and existing subcommands keep their previous behavior. Every request still uses
the same absolute-path, ownership, non-symlink and private-permission checks;
there is no fallback to another socket or TCP.

Each submission validates the current model catalog. Once selected, the model
stays fixed for this conversation, including after `/reset`; a changed default
does not silently switch it. See [configured models](laf-configured-models.md)
for ordinary small-model IDs and optional Flash. Help/quit remains offline.

These are client commands, not a clean-host runtime Quick Start. They do not
start, replace or reconfigure a service. See the [Router prerequisites](../inference/laf_service/README.md#machine-specific-preparation).
If the executable bit was lost during transfer, use `python3 -B laf` with the same
arguments. The existing `python3 -B -m inference.local_platform.cli` form and its
JSON-oriented `ask`, `get` and `cancel` commands remain available.

## A short session

Start in English with a simple greeting. These examples illustrate the interface;
they are not recorded model replies or guaranteed response strings.

```text
You> Hello
LAF [unverified]> Hello!
```

With workspace access enabled, ask in ordinary language, for example
`Find Python files in this workspace.` The client gives the model a single
filename-search tool. When the model returns the exact supported JSON request,
the client searches locally and displays actual relative names:

```text
You> Find Python files in this workspace.
Files [local]> *.py
  src/example.py
```

Whether a particular model emits the supported tool JSON must still be checked
on the accepted host. If it answers in prose instead of calling the tool, no
search takes place. `Files [local]>` marks actual client search results. Every
line of ordinary model text has `LAF [unverified]>`; in-session notices use
`Client>`. These labels distinguish the source, not the truth of generated text.
Model prose can still make incorrect claims or invent filenames. A search turn
displays the tool result directly, without a second inference, arbitrary tools
or an agent loop.

Ordinary conversation turns use JSON messages with `user` and `assistant` roles.
The terminal shows the assistant text rather than the job envelope. Replies are
printed after completion, not streamed token by token. All replies are generated
advice, not executed code or verified facts.

For example, a greeting such as "Hello" might receive "Hello!". This is only
illustrative; the exact reply depends on the selected model.

## Commands and privacy

- `/help`: show commands and the capability boundary.
- `/reset`: clear the client's in-memory conversation. This does not erase retained
  jobs from the running Router.
- `/quit` or `/exit`: leave the session. EOF also exits.
- `/find *.py`: perform a local filename-only search without calling a model.
  Manual `/find` results are displayed only and do not join conversation history.
  The model cannot answer a follow-up about them unless you supply that context.

Without `--workspace`, the client has no search capability. With it, searches stay
inside one explicitly chosen, owned directory. Only regular, owned, single-link
files on that directory's device are listed. Hidden or restricted path components,
symlinks, other owners and mounted devices are excluded. A directory descriptor
pins the workspace, no-follow opens and identity checks guard traversal, and the
client never opens file data. Restricted credential/configuration roots cannot
be selected as the workspace. This is a deliberately narrow capability, not a
complete filesystem or secret scanner.

Patterns are relative globs of at most 128 UTF-8 bytes. `*` can span directories;
`src/*.py` restricts the prefix. Absolute paths, `..`, backslashes, hidden components
and control characters are rejected. Searches stop at 4,096 directory entries,
16 directory levels, 50 matches or 2,048 serialized path bytes. Limits and
unreadable directories produce an explicit incomplete result. Results are sorted
only within the visited subset; an incomplete search is not proof of absence.

Model-requested search results join the in-memory conversation as clearly marked
user-role observations, separate from the assistant's JSON request. The Router
accepts text roles rather than generic tool messages. Names are untrusted data,
not user instructions, and may be sent to the selected local model on a
subsequent message. Only relative names are included,
not source contents or the workspace's absolute path. Prompts and conversation
history are not saved by this client. The existing Router and engine retention
rules still apply. Do not use secrets in prompts or choose a sensitive workspace.

The tool accepts only a whole JSON object with exactly these two fields:

```json
{"tool":"find_files","pattern":"*.py"}
```

Unknown tools, extra fields and unsafe patterns do not execute. Markdown-fenced,
malformed or ambiguous JSON is displayed as text and does not execute. Model
output, file names and search results are untrusted data. They are never passed
to a shell, Python interpreter or OS command, and cannot create new capabilities.
Terminal controls, bidirectional overrides and Unicode line/paragraph separators
are escaped before display. Every generated line receives the model label, so a
multiline reply cannot create an unlabeled client-result line.

## Limits and failures

The session retains recent completed turns within the Router's conservative
8,192-token-equivalent byte budget and 32-message cap. Whole oldest turns, including their client observations, are
dropped together when necessary, with a visible notice; the new user message is never
silently shortened. Input, output tokens and timeout keep the existing bounds.
Use `--max-tokens 256` or `--timeout 120` if appropriate. A length-capped reply is
marked as possibly incomplete, cannot execute a tool, and its entire turn is
omitted from future context. It is never automatically continued. `/reset` is
useful when changing topics.

Every ordinary turn submits at most one job and only polls its returned ID.
Submission or polling failures stop the chat and never automatically resend it.
A received job ID is shown for reconciliation with the existing `get` or `cancel`
commands. Ctrl-C during a job makes one best-effort cancellation; it does not
claim that provider execution has stopped. Ctrl-C at the prompt exits without a
job. File-search errors and unknown local commands cannot execute model or shell
commands.

```sh
./laf --socket "$SOCKET" get JOB_ID
./laf --socket "$SOCKET" cancel JOB_ID
```

An unsupported or unsafe model-generated tool request stops the chat without
executing it. An invalid manual `/find` pattern only displays an error and leaves
the session running.

There is no arbitrary file read/write, shell, tool chaining, background autonomy,
remote-machine access, session persistence, image input, generic OpenAI tool-call
compatibility, installed packaging, model download or new runtime setup in this
change. Models must belong to the selected Router catalog, including configured
IDs and optional Flash. There is no client-selected provider or arbitrary
backend option. NPU is optional and is not used by this chat.

## Verification and next steps

Offline checks cover conversation history, JSON job compatibility, fixed tool
schemas, local versus model-requested search, symlink and directory-swap refusal,
limits, all-line provenance labels, truncated-tool refusal, complete-turn pruning,
terminal display, interrupted/uncertain requests and existing CLI behavior.
They use fake model replies and do not establish real-model search compliance or
runtime readiness. Socket checks require an executor that permits private Unix
sockets. No denied socket test is treated as passed or automatically skipped.

Before release, accept an actual multi-turn model conversation, a Japanese and an
English filename request, a rejected tool request, cancellation and an unavailable
Router on the supported host. Then complete the existing model acquisition,
license/provenance, packaging and clean-host runtime gates. Web UI can build on
the same API later.

This source version uses `laf-inference` and `laf-npu` names without aliases
for older installations. Follow [naming and migration](../inference/local_platform/naming.md)
before adopting it. A checkout update does not rename an installed unit, socket
directory, running process or NPU connection; those operational changes require
separate authorization and supported-host validation.
