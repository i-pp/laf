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

"""Pure negative oracles; simulated results must never be accepted as source evidence."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from run_integrated_source_checks import run_checked
class StrictGate(unittest.TestCase):
 def suite(self):return unittest.TestSuite([unittest.FunctionTestCase(lambda:None)])
 def result(self):return SimpleNamespace(testsRun=1,failures=[],errors=[],skipped=[],expectedFailures=[],unexpectedSuccesses=[],wasSuccessful=lambda:True)
 def reject_result(self,key,value):
  result=self.result();setattr(result,key,value)
  with self.assertRaises(ValueError):run_checked(self.suite(),1,SimpleNamespace(run=lambda suite:result))
 def test_valid(self):self.assertEqual(run_checked(self.suite(),1,SimpleNamespace(run=lambda suite:self.result()))['run'],1)
 def test_empty_no_execution(self):
  runner=Mock()
  with self.assertRaises(ValueError):run_checked(unittest.TestSuite(),1,runner)
  runner.run.assert_not_called()
 def test_duplicate_no_execution(self):
  runner=Mock();test=next(iter(self.suite()))
  with self.assertRaises(ValueError):run_checked(unittest.TestSuite([test,test]),2,runner)
  runner.run.assert_not_called()
 def test_skip(self):self.reject_result('skipped',[('case','reason')])
 def test_xfail(self):self.reject_result('expectedFailures',[('case','reason')])
 def test_incomplete(self):self.reject_result('testsRun',0)
 def test_failure(self):self.reject_result('failures',[('case','reason')])
 def test_error(self):self.reject_result('errors',[('case','reason')])
 def test_unexpected_success(self):self.reject_result('unexpectedSuccesses',['case'])
