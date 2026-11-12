#!/usr/bin/env python3
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

# Added 2026-11-12: original portable integrated package preparation.

"""Strict no-service selection: do not confuse this with installed-plugin acceptance."""
import json
from pathlib import Path
import sys
import unittest

def test_ids(suite):
 for test in suite:
  if isinstance(test,unittest.TestSuite):yield from test_ids(test)
  else:yield test.id()

def main():
 sys.path.insert(0,str(Path(__file__).parent))
 suite=unittest.defaultTestLoader.loadTestsFromNames(['test_portable_plugin','test_private_assembly'])
 planned=suite.countTestCases()
 ids=list(test_ids(suite))
 if planned!=34 or len(ids)!=planned:raise SystemExit('Expected exactly 34 packaging tests')
 if len(set(ids))!=planned:raise SystemExit('Duplicate test IDs refused before execution')
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 counts={'planned':planned,'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses)}
 passed=result.wasSuccessful() and result.testsRun==planned and not any(counts[k] for k in ('failures','errors','skipped','expected_failures','unexpected_successes'))
 print(json.dumps({'status':'passed' if passed else 'failed',**counts,'scope':'pure config/source/synthetic ZIP only; service/socket/SDK child/model/publication operations zero'}))
 return 0 if passed else 1
if __name__=='__main__':raise SystemExit(main())
