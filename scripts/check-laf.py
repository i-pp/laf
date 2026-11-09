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

"""Run standalone LAF Python regressions without models, Docker or vendor SDKs.

Portable omits exactly the enumerated socket tests. Sockets selects only those
tests. Full runs both groups. No mode establishes runtime or release readiness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

from laf_socket_test_ids import PRIVATE_SOCKET_TESTS

ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    'inference.aceis_npu_service.tests.test_service',
    'inference.aceis_service.test_acceptance_profiles',
    'inference.aceis_service.test_core_integration',
    'inference.aceis_service.test_host_supervisor',
    'inference.aceis_service.test_lifecycle',
    'inference.aceis_service.test_lifecycle_uds',
    'inference.aceis_service.test_lifecycle_uds_live',
    'inference.aceis_service.test_npu_bridge',
    'inference.aceis_service.test_service',
    'inference.core_container.test_build',
    'inference.local_platform.test_cli',
    'inference.local_platform.test_cli_responses',
    'inference.local_platform.test_developer',
    'inference.local_platform.test_models',
    'inference.local_platform.test_project_runner',
    'inference.local_platform.test_repair',
    'inference.local_platform.test_workspace',
    'test_laf_checks',
)
# This file is a resource-limited child executable used by workspace tests,
# not a unittest module. Its behavior is exercised by those parent tests.
CHILD_HELPERS = frozenset({'inference.local_platform.test_runner'})


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def check_module_inventory(root=ROOT, modules=MODULES):
    actual = {
        '.'.join(path.relative_to(root).with_suffix('').parts)
        for path in (root / 'inference').rglob('test_*.py')
    }
    actual.update(path.stem for path in (root / 'scripts').glob('test_*.py'))
    if len(modules) != len(set(modules)) or actual != set(modules) | CHILD_HELPERS:
        raise ValueError('explicit test module inventory is stale')


def select_tests(suite, mode, socket_ids=PRIVATE_SOCKET_TESTS):
    if mode not in {'full', 'portable', 'sockets'}:
        raise ValueError('unknown suite')
    tests = list(flatten(suite))
    ids = [test.id() for test in tests]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('empty or duplicate test inventory')
    if any(isinstance(test, unittest.loader._FailedTest) for test in tests):
        raise ValueError('test module import failed')
    if set(socket_ids) - set(ids):
        raise ValueError('socket test inventory is stale')
    if mode == 'portable':
        omitted = set(socket_ids)
    elif mode == 'sockets':
        omitted = set(ids) - set(socket_ids)
    else:
        omitted = set()
    selected = [test for test in tests if test.id() not in omitted]
    if not selected:
        raise ValueError('empty selected suite')
    return unittest.TestSuite(selected), len(tests), sorted(omitted)


def summary(result, planned, selected, unexecuted, mode):
    passed = (result.wasSuccessful() and result.testsRun == selected
              and not result.skipped and not result.expectedFailures
              and not result.unexpectedSuccesses
              and planned == selected + len(set(unexecuted))
              and len(unexecuted) == len(set(unexecuted)))
    reason = ('explicit portable group: private Unix socket required'
              if mode == 'portable' else 'explicit socket-only group: use portable for this test')
    return {
        'planned': planned, 'selected': selected, 'tests_run': result.testsRun,
        'failure_events': len(result.failures), 'error_events': len(result.errors),
        'skipped': len(result.skipped), 'expected_failures': len(result.expectedFailures),
        'unexpected_successes': len(result.unexpectedSuccesses),
        'selected_suite_passed': passed,
        'full_python_regression_passed': passed and mode == 'full' and not unexecuted,
        'unexecuted': [{'test': name, 'reason': reason} for name in unexecuted],
    }


def source_identity(root=ROOT):
    paths = []
    for directory in ('inference', 'scripts', 'docs'):
        paths.extend(path for path in (root / directory).rglob('*')
                     if '__pycache__' not in path.parts and path.suffix != '.pyc'
                     and (path.is_file() or path.is_symlink()))
    paths.extend(root / name for name in ('README.md', 'AGENTS.md', '.gitignore'))
    result = {}
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise ValueError('source fingerprint requires regular non-symlink files')
        if path.stat().st_size > 2 * 1024**2:
            raise ValueError('unexpected large input in source fingerprint')
        result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('full', 'portable', 'sockets'), required=True)
    args = parser.parse_args(argv)
    sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
    os.environ['LAF_TEST_LIVE_UDS'] = '1'
    try:
        check_module_inventory()
        suite = unittest.TestLoader().loadTestsFromNames(MODULES)
        suite, planned, unexecuted = select_tests(suite, args.suite)
        selected = suite.countTestCases()
        result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
        report = summary(result, planned, selected, unexecuted, args.suite)
        report.update(
            schema_version=1, suite=args.suite, python_version=sys.version.split()[0],
            source_sha256=source_identity(), p0_complete=False,
            release_authorized=False, deployed=False,
            scope='Standalone source Python unit/mocked checks only. No Docker, model, vendor runtime, native NPU, clean-host runtime, second-PC, legal or publication acceptance.',
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report['selected_suite_passed'] else 1
    except (ImportError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({'error': 'check_execution_invalid', 'error_type': type(exc).__name__,
                          'p0_complete': False, 'release_authorized': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
