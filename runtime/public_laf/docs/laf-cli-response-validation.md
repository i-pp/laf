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

# CLI response validation

The CLI now rejects ambiguous or malformed UTF-8 JSON responses before treating
them as service evidence. This local increment does not change or deploy Router
code, create a transport, submit inference, or retry a request.

- Reject duplicate keys at any level, non-finite numbers including overflowing
  exponents, non-object responses and nesting beyond 64 containers. String
  brackets and escaped quotes do not contribute to nesting depth.
- Convert invalid UTF-8/JSON and excessive decoder recursion to ClientError.
  UTF-16/32 and BOM-prefixed JSON are outside the UTF-8 wire contract.
- Reject malformed GPU/NPU health sections without an uncaught AttributeError.
- Check get responses against the requested job ID, a supported model and known
  state. Completed jobs must contain string content. Keep the known job ID on
  errors so the owner can reconcile it without resubmission.
- Accept cancellation only with cancellation_requested. This acknowledgement
  never proves the job or underlying inference stopped.

From this candidate directory, run the new entirely offline regressions:

```sh
python3 -B -m unittest inference.local_platform.test_cli_responses -v
```

The tests exercise depth boundaries, escaped/Unicode strings and malformed
numeric probes with no live service. Run the standalone portable suite from the
root README for aggregate checks. A passing subset does not establish remote
connectivity, real inference or completion of socket-only tests.

This is not full route-schema validation or a total wall-clock read deadline.
The existing response byte bound and socket idle timeout remain unchanged.
Real PC-B connectivity, build/test E2E, remaining P0 and release gates remain open.
These test commands are not a Quick Start for a running service.
