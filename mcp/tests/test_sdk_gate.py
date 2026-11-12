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

# Added 2026-11-11: exact SDK pin/guard regressions; mocked acceptance is not runtime evidence.
"""Offline SDK selection and startup guard checks, with no real Router or SDK run."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.metadata
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from laf_mcp import server

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = '1.30.0'
WHEEL_SHA256 = '666edb5009503e1047c9d60346a756f94b261f05cc2625f23d41c728ffc484d0'
SDIST_SHA256 = '445414625fce5c295faa505bb11bacece661ab6f4028d57c935db57820b7a3e4'


class SDKGateTests(unittest.TestCase):
    def test_exact_sdk_selection_and_archive_metadata(self):
        self.assertEqual(server.SDK_VERSION, EXPECTED)
        self.assertIn('dependencies = ["mcp==1.30.0"]', (ROOT / 'mcp/pyproject.toml').read_text())
        requirement = [line for line in (ROOT / 'mcp/sdk-pin.txt').read_text().splitlines()
                       if line and not line.startswith('#')]
        self.assertEqual(requirement, [f'mcp=={EXPECTED} --hash=sha256:{WHEEL_SHA256} --hash=sha256:{SDIST_SHA256}'])
        review = json.loads((ROOT / 'mcp/dependency-review.json').read_text())
        self.assertEqual(review['sdk']['version'], EXPECTED)
        self.assertEqual(review['sdk']['wheel_sha256'], WHEEL_SHA256)
        self.assertEqual(review['sdk']['sdist_sha256'], SDIST_SHA256)
        self.assertEqual(review['sdk']['requires_python'], '>=3.10')
        pins = json.loads((ROOT / 'scripts/laf-sdk-pins.json').read_text())['packages']
        self.assertEqual(pins['mcp']['version'], EXPECTED)
        self.assertEqual(pins['mcp']['wheel_sha256'], WHEEL_SHA256)
        self.assertEqual(pins['pyjwt']['version'], '2.15.1')

    def assert_rejected_before_state(self, version=None, error=None):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'PRIVATE_STATE_SENTINEL'
            stderr, stdout = io.StringIO(), io.StringIO()
            with patch.object(server.importlib.metadata, 'version', return_value=version,
                              side_effect=error) as metadata, \
                 patch.object(server, 'Receipts') as receipts, \
                 patch.object(server, 'Router') as router, \
                 patch.object(server, 'Application') as application, \
                 redirect_stderr(stderr), redirect_stdout(stdout):
                result = server.main(['--state-dir', str(state), '--allow-model', 'flash'])
            self.assertEqual(result, 2)
            metadata.assert_called_once_with('mcp')
            receipts.assert_not_called()
            router.assert_not_called()
            application.assert_not_called()
            self.assertFalse(state.exists())
            self.assertEqual(stdout.getvalue(), '')
            self.assertEqual(stderr.getvalue(),
                             'laf-mcp: startup or protocol session failed; no automatic retry\n')

    def test_other_sdk_versions_fail_before_state_or_router_access(self):
        for version in ('1.29.0', '1.29.1', '1.30.1', '2.0.0', '1.30.0rc1'):
            with self.subTest(version=version):
                self.assert_rejected_before_state(version=version)

    def test_missing_sdk_fails_before_state_or_router_access(self):
        self.assert_rejected_before_state(error=importlib.metadata.PackageNotFoundError('mcp'))

    def test_metadata_failure_does_not_echo_raw_error(self):
        self.assert_rejected_before_state(error=RuntimeError('PRIVATE_ERROR_SENTINEL'))

    def mocked_startup(self, run_error=None):
        fake_anyio = types.ModuleType('anyio')
        fake_anyio.run = Mock(side_effect=run_error)
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / 'state')
            stderr = io.StringIO()
            with patch.object(server.importlib.metadata, 'version', return_value=EXPECTED), \
                 patch.dict(sys.modules, {'anyio': fake_anyio}), \
                 patch.object(server, 'Receipts') as receipts, \
                 patch.object(server, 'Router') as router, \
                 patch.object(server, 'Application') as application, \
                 patch.object(server.logging, 'getLogger'), redirect_stderr(stderr):
                result = server.main(['--state-dir', state, '--allow-model', 'flash',
                                      '--socket', '/synthetic/socket'])
                receipts.assert_called_once_with(state)
                router.assert_called_once_with('/synthetic/socket')
                application.assert_called_once_with(router.return_value, receipts.return_value, ['flash'])
                fake_anyio.run.assert_called_once_with(server.serve, application.return_value)
                receipts.return_value.close.assert_called_once_with()
            self.assertFalse(Path(state).exists())
            return result, stderr.getvalue()

    def test_mocked_exact_sdk_reaches_session_and_closes_receipts(self):
        # A control-flow test only: metadata and anyio are fake, not SDK acceptance.
        self.assertEqual(self.mocked_startup(), (0, ''))

    def test_mocked_session_failure_closes_receipts_without_raw_error(self):
        self.assertEqual(self.mocked_startup(RuntimeError('PRIVATE_ERROR_SENTINEL')),
                         (2, 'laf-mcp: startup or protocol session failed; no automatic retry\n'))

    def test_help_and_version_do_not_inspect_or_require_sdk(self):
        for arg in ('--help', '--version'):
            with self.subTest(arg=arg), patch.object(server.importlib.metadata, 'version') as metadata, \
                 patch.object(server, 'Receipts') as receipts, redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    server.main([arg])
                self.assertEqual(stopped.exception.code, 0)
                metadata.assert_not_called()
                receipts.assert_not_called()


if __name__ == '__main__':
    unittest.main()
