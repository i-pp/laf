# KISS Open License Version 1.0 (KOL-v1)
#
# SPDX-FileCopyrightText: 2026 I-plusplus Inc.
# SPDX-License-Identifier: LicenseRef-KOL-v1
#
# Copyright remains with its respective holders; original copyright and attribution notices must be preserved.
# Contributors may add copyright notices for their own original modifications.
# Distributed modifications must be identified as required by Section 4(c) of KOL-v1.
#
# Use, modification, distribution, and incorporation into commercial or proprietary software are permitted
# without license fees or source disclosure, subject to all terms of the complete KOL-v1 license.
#
# Contributors grant the patent permissions stated in KOL-v1. Outputs are not covered merely because a
# KOL-licensed tool produced them; embedded licensed material remains subject to applicable license terms.
#
# THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OR LIABILITY TO THE EXTENT PERMITTED BY APPLICABLE LAW.
#
# This header is a summary, not a separate license or an amendment to KOL-v1.
# All terms and conditions of KOL-v1 apply. See the accompanying LICENSE file for the complete license text.

"""Child-only resource-limited runner for canonical pure functions and JSON cases."""
import importlib.util
import json
from pathlib import Path
import resource
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024**2, 256 * 1024**2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024**2, 1024**2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    root = Path(sys.argv[1])
    spec = importlib.util.spec_from_file_location('candidate', root / 'candidate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = json.loads((root / 'cases.json').read_text())
    passed = 0
    failures = []
    for index, case in enumerate(cases):
        try:
            actual = getattr(module, case['function'])(*case['args'])
            equal = actual == case['expected']
            if type(equal) is not bool or not equal:
                failures.append({'case': index, 'error': 'expected value mismatch'})
            else:
                passed += 1
        except Exception as exc:
            failures.append({'case': index, 'error': type(exc).__name__})
    print(json.dumps({'total': len(cases), 'passed': passed, 'failures': failures}))
    return 0 if passed == len(cases) else 1

if __name__ == '__main__':
    raise SystemExit(main())
