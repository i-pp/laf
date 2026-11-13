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

# Source language and Unicode regression data

Published prose, headings, comments, UI messages and generated instruction
preambles use English. Source uses ASCII spelling and punctuation. Do not leave
an operator's Japanese setup guide in the public distribution or merely relabel
it as a fixture. The SDK guide and its bundled copy must remain synchronized.

`python3 -B scripts/check_english_source.py` checks every Git-tracked source file,
not only changed lines. It rejects non-ASCII prose/code. The strict supplemental
suite includes negative controls for documents, production strings, comments,
identifiers, missing markers, invalid syntax and unlisted test paths.

## Deliberate Unicode test data

The following six test files exercise real multi-byte data, not translated UI:

- `mcp/tests/test_contracts.py`: byte-bounded request IDs and emoji responses.
- `mcp/tests/test_integrated_router.py`: SDK/UDS paths containing Unicode.
- `mcp/tests/test_package.py`: wheel/install paths containing Unicode.
- `mcp/tests/test_sdist.py`: source-distribution paths containing Unicode.
- `scripts/test_sdk_installer.py` and its bundled
  `runtime/public_laf/scripts/test_sdk_installer.py` copy: bundle, prefix and
  empty-venv paths containing both spaces and multi-byte characters.

Only string-literal data on a line immediately after an English
`# Unicode test fixture:` explanation is allowed in those files. This is not a
whole-file exception: non-English comments, identifiers or prose still fail.
Preserve those fixture bytes so the tests continue to exercise UTF-8 paths and
byte lengths, rather than replacing them with ASCII and weakening the oracle.

## Historical policy format

The legacy common-policy parser recognizes an exact historical heading token.
Its Unicode escapes preserve the same UTF-8 token; the accepted format and pins
are not broadened. The generated preamble is English, but an externally supplied
verified policy snapshot is still embedded verbatim. Never translate that body,
change its hash, or relax heading/pin checks to satisfy a language check.

This source policy does not relicense or rewrite external license material.
No current bundled canonical license text needs a language exception.
