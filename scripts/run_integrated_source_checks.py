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

"""Strict supplemental source checks with independent result/inventory refusal guards."""
import json,sys,unittest
from pathlib import Path

def flatten(suite):
 for test in suite:
  if isinstance(test,unittest.TestSuite):yield from flatten(test)
  else:yield test

def run_checked(suite,expected,runner):
 ids=[test.id() for test in flatten(suite)]
 if type(expected) is not int or expected<=0 or len(ids)!=expected or suite.countTestCases()!=expected or len(set(ids))!=expected:raise ValueError('Empty, incomplete or duplicate selection; no execution')
 result=runner.run(suite)
 counts={key:len(getattr(result,key)) for key in ('failures','errors','skipped','expectedFailures','unexpectedSuccesses')}
 if result.testsRun!=expected or any(counts.values()) or not result.wasSuccessful():raise ValueError('Incomplete or non-green execution')
 return {'planned':expected,'run':result.testsRun,**counts}

def main():
 root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root),str(root/'scripts')]
 # When invoked as a script, tests must exercise this exact module instance.
 sys.modules.setdefault('run_integrated_source_checks',sys.modules[__name__])
 runner=unittest.TextTestRunner(verbosity=2)
 try:
  gates=run_checked(unittest.defaultTestLoader.loadTestsFromName('test_strict_source_gate'),9,runner)
  # Cardinality is a machine refusal guard, not a coverage or product specification.
  source=run_checked(unittest.defaultTestLoader.loadTestsFromNames(['test_async_job_ack_disconnect','test_integrated_source','test_codex_package','test_common_policy_language','test_english_source','test_ordinary_session_pool','test_session_pool_boundary']),76,runner)
  codex_selection=run_checked(unittest.defaultTestLoader.loadTestsFromNames(['test_codex_selection_boundary','test_codex_catalog_effort']),77,runner)
  bootstrap=run_checked(unittest.defaultTestLoader.loadTestsFromName('test_bootstrap'),28,runner)
  bindings=run_checked(unittest.defaultTestLoader.loadTestsFromName('test_bootstrap_bindings'),30,runner)
 except ValueError as exc:print(json.dumps({'status':'failed','reason':str(exc)}));return 1
 print(json.dumps({'status':'passed','gate_oracles':gates,'source_cases':source,'codex_selection_cases':codex_selection,'bootstrap_cases':bootstrap,'binding_cases':bindings,'bootstrap_scope':'source/tempfiles/fake native and peer; bounded stdlib copied doctor/config CLI children; no SDK/native/socket/service/model','scope':'pure gates/source/fake Codex peer and owner HTTP handler/fake controller; no SDK child or service'}));return 0
if __name__=='__main__':raise SystemExit(main())
