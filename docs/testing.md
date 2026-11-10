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

<!-- Modified 2026-11-10: relocate the root README's software-test commands and result-accounting contract. -->

# Testing the source preview

[Back to Local AI Fabric](../README.md)

These checks exercise source behavior without provisioning a runtime. See
[source-preview and release gates](source-preview.md) for the separate hardware,
external-asset and distribution requirements.

## Portable and socket groups

For offline development, use an existing Linux Python 3.10 or later with standard
library support for `fcntl`, `resource` and Unix processes. No package installation,
Docker, model download, running Router or vendor SDK is needed for these commands.
From the repository root:

```sh
python3 -B scripts/check-laf.py --suite portable
python3 -B -m inference.local_platform.cli --help
python3 -B -m inference.local_platform.cli model --help
python3 -B -m inference.local_platform.cli model catalog
```

The portable runner executes unit/mocked checks, temporary-file operations and
owned fake subprocesses. It omits exactly 39 enumerated private Unix-socket tests,
lists each unexecuted ID, and cannot report a full regression pass. Exit 0 means
the selected group passed with complete accounting and no skips; exit 1 means
test failure/error, and exit 2 means invalid execution or test inventory.

On an authorized executor where private Unix sockets and peer-UID queries work:

```sh
python3 -B scripts/check-laf.py --suite sockets
python3 -B scripts/check-laf.py --suite full
```

The socket group uses fake providers, not Docker or real models. The full group
combines both groups. A denied socket is an error, never an automatically skipped
or passing test. Native C++/ORT, real GPU/NPU, clean-host runtime and second-PC
checks are separate and are not run by this Python suite.

## Interpreting the result

The runner writes individual test results to stderr and a JSON summary to stdout.
Check `selected_suite_passed`, `full_python_regression_passed`, `tests_run`,
`failure_events`, `error_events`, `skipped` and the explicit `unexecuted` list.
A portable or socket-only pass never means the full Python suite passed.
See [the runner source](../scripts/check-laf.py) and
[the socket-test inventory](../scripts/laf_socket_test_ids.py).

A passing Python suite does not establish hardware support, a complete runtime
release, deployment readiness, security certification or redistribution rights.
Private historical counts and binary hashes are not acceptance of this tree.

Some component guides show focused `unittest` commands. Those select narrower
checks and do not replace the aggregate groups above. Do not omit failures or
unexecuted stages when recording results from another executor.
