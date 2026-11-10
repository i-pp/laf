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

# Modified 2026-11-09: require the new CLI launcher in source fingerprints.

"""Runner accounting regressions, using synthetic test objects only."""
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('laf_check_runner', Path(__file__).with_name('check-laf.py'))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class CheckTests(unittest.TestCase):
    def cases(self):
        class Sample(unittest.TestCase):
            def test_portable(self):
                pass
            def test_socket(self):
                pass
        cases = list(runner.flatten(unittest.defaultTestLoader.loadTestsFromTestCase(Sample)))
        sockets = {case.id() for case in cases if case.id().endswith('test_socket')}
        return cases, sockets

    def test_full_selects_each_case_once(self):
        cases, sockets = self.cases()
        suite, planned, omitted = runner.select_tests(unittest.TestSuite(cases), 'full', sockets)
        self.assertEqual((planned, suite.countTestCases(), omitted), (2, 2, []))

    def test_portable_and_sockets_are_disjoint_and_complete(self):
        cases, sockets = self.cases()
        portable, planned, omitted = runner.select_tests(unittest.TestSuite(cases), 'portable', sockets)
        real, _, other = runner.select_tests(unittest.TestSuite(cases), 'sockets', sockets)
        first = {case.id() for case in runner.flatten(portable)}
        second = {case.id() for case in runner.flatten(real)}
        self.assertFalse(first & second)
        self.assertEqual(len(first | second), planned)
        self.assertEqual(set(omitted), second)
        self.assertEqual(set(other), first)

    def test_new_non_socket_case_is_not_implicitly_omitted(self):
        cases, sockets = self.cases()
        portable, _, _ = runner.select_tests(unittest.TestSuite(cases), 'portable', sockets)
        self.assertEqual(portable.countTestCases(), 1)

    def test_duplicate_empty_unknown_or_stale_selection_refused(self):
        cases, sockets = self.cases()
        for suite, mode, ids in [
            (unittest.TestSuite(), 'full', set()),
            (unittest.TestSuite(cases + cases), 'full', sockets),
            (unittest.TestSuite(cases), 'unknown', sockets),
            (unittest.TestSuite(cases), 'full', {'not.a.test'}),
            (unittest.TestSuite(cases), 'sockets', set()),
        ]:
            with self.assertRaises(ValueError):
                runner.select_tests(suite, mode, ids)

    def test_import_failure_refused(self):
        suite = unittest.TestLoader().loadTestsFromName('laf_nonexistent_module')
        with self.assertRaises(ValueError):
            runner.select_tests(suite, 'full', set())

    def test_portable_pass_cannot_claim_full_pass(self):
        result = unittest.TestResult(); result.testsRun = 1
        report = runner.summary(result, 2, 1, ['socket.case'], 'portable')
        self.assertTrue(report['selected_suite_passed'])
        self.assertFalse(report['full_python_regression_passed'])

    def test_missing_duplicate_or_miscounted_results_refuse_pass(self):
        for planned, selected, omitted, run in [(2, 2, [], 1), (3, 1, ['x', 'x'], 1), (3, 1, ['x'], 1)]:
            result = unittest.TestResult(); result.testsRun = run
            self.assertFalse(runner.summary(result, planned, selected, omitted, 'full')['selected_suite_passed'])

    def test_skips_and_expected_failures_are_not_accepted(self):
        for field in ('failures', 'errors', 'skipped', 'expectedFailures', 'unexpectedSuccesses'):
            result = unittest.TestResult(); result.testsRun = 1
            getattr(result, field).append('synthetic')
            self.assertFalse(runner.summary(result, 1, 1, [], 'full')['selected_suite_passed'])

    def test_exact_module_inventory_and_new_file_guard(self):
        runner.check_module_inventory()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'inference').mkdir(); (root / 'scripts').mkdir()
            (root / 'inference/test_unlisted.py').write_text('')
            with self.assertRaises(ValueError):
                runner.check_module_inventory(root, ())

    def test_source_manifest_contains_runner_and_synthetic_fixture(self):
        values = runner.source_identity()
        for name in ('scripts/check-laf.py', 'scripts/test_laf_checks.py',
                     'scripts/laf_socket_test_ids.py',
                     'inference/local_platform/fixtures/provenance-synthetic.json',
                     'inference/core_container/Dockerfile',
                     'inference/laf_npu_service/worker.cpp',
                     'inference/laf_npu_service/build.sh', 'README.md', 'laf'):
            self.assertRegex(values[name], r'^[0-9a-f]{64}$')

    def test_source_has_no_private_home_or_original_repository_reference(self):
        tokens = ('/' + 'home/', '/' + 'Users/', 'EVO' + '-X3',
                  'y-' + 'ooya', 'project-' + 'kiss-infra')
        for path in runner.ROOT.rglob('*'):
            if path.is_file() and path.suffix in {'.py', '.md', '.json', '.example'}:
                content = path.read_text()
                for token in tokens:
                    with self.subTest(path=str(path.relative_to(runner.ROOT)), token=token):
                        self.assertNotIn(token, content)

    def test_relative_document_links_resolve_in_standalone_tree(self):
        for path in runner.ROOT.rglob('*.md'):
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', path.read_text()):
                target = target.split('#', 1)[0]
                if target and not re.match(r'[A-Za-z]+:', target):
                    with self.subTest(path=str(path.relative_to(runner.ROOT)), target=target):
                        self.assertTrue((path.parent / target).exists())


if __name__ == '__main__':
    unittest.main()
