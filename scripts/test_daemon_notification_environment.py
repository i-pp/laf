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

# Added 2026-11-15: narrow guarded daemon notification-reference handoff.
"""Fake foreground boundary only; never launch or read a credential/native."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime/private_codex/src'))
from scripts.source_test_isolation import require_isolated
require_isolated()  # Refuse raw resource tests before any fixture or project import.
import daemon
import isolation


class DaemonNotificationEnvironment(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        (self.root / 'logs').mkdir(mode=0o700)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {'HOME': str(self.root),
            'PATH': '/operator/bin:/usr/bin:/bin', 'OPENAI_API_KEY': 'synthetic-not-a-key',
            'ACE' + 'IS_MCP_EVENTS_ENABLED': '1',
            'LAF_UNREVIEWED_OPTION': 'must-not-propagate'}, clear=True))
        for name in ('subprocess.Popen', 'socket.socket.bind', 'socket.socket.connect',
                     'threading.Thread.start'):
            stack.enter_context(patch(name, side_effect=AssertionError('Offline foreground fixture only')))
        self.refs = {'LAF_OAUTH_ISSUER': 'https://issuer.example.invalid',
                     'LAF_OAUTH_AUDIENCE': 'fixture-audience',
                     'LAF_OAUTH_JWKS_FILE': '/fixture/public-jwks.json',
                     'LAF_OAUTH_ALLOWED_SUBJECT': 'fixture-owner',
                     'LAF_OAUTH_ALLOWED_SUBJECTS_JSON': '["fixture-owner", "fixture-reviewer"]',
                     'LAF_OAUTH_REQUIRED_SCOPES': 'mcp:access fixture:read',
                     'CREDENTIALS_DIRECTORY': '/fixture/systemd/credentials'}
        self.binary = '/fixture/bin/codex'

    def environment(self, pipeline=True):
        return daemon._daemon_child_environment(self.binary, allow_codex_pipeline=pipeline)

    def base(self):
        return {'HOME': str(self.root), 'LANG': 'C.UTF-8',
                'PATH': '/fixture/bin:/operator/bin:/usr/bin:/bin'}

    def enable(self):
        os.environ.update(self.refs)
        os.environ['LAF_MCP_EVENTS_ENABLED'] = '1'

    def foreground(self, pipeline, *, released=True):
        captured = {}
        child = Mock()
        child.proc.poll.return_value = 0
        child.close.return_value = {'exit_code': 0,
            'resources': {'confirmed': True, 'exceeded': False, 'released': released}}
        control = Mock(unit='offline-notification-scope')

        def process(args, **kwargs):
            captured.update(args=args, **kwargs)
            return child

        with patch.object(daemon.scheduler, 'configuration', return_value={'daemon': {'fixture': True}}) as settings, \
                patch.object(daemon.development_poc, 'configure') as development, \
                patch.object(isolation, 'prepare', return_value=(self.root, self.binary)) as prepare, \
                patch.object(daemon, 'Control', return_value=control) as resource, \
                patch.object(daemon, 'Process', side_effect=process) as transport, \
                patch.object(daemon.signal, 'signal', return_value=None):
            code = daemon.foreground(self.root, self.binary, allow_codex_pipeline=pipeline)
        prepare.assert_called_once_with(self.root, self.binary)
        development.assert_called_once_with(self.root, False)
        resource.assert_called_once_with(self.root, {'fixture': True}, scheduler_storage=True)
        self.assertIs(captured['resources'], control)
        self.assertTrue(captured['raw_output'])
        self.assertEqual(captured['cwd'], self.root)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(settings.call_args.kwargs, {'codex_pipeline': True} if pipeline else {})
        self.assertEqual(child.close.call_args.kwargs, {'grace': 40})
        return code, captured

    def test_default_pipeline_environment_is_minimal(self):
        self.assertEqual(self.environment(), self.base())

    def test_events_flag_without_pipeline_cannot_forward_any_reference(self):
        self.enable()
        self.assertEqual(self.environment(False), self.base())

    def test_exact_opt_in_forwards_only_required_references_unchanged(self):
        self.enable()
        os.environ['LAF_OAUTH_RESOURCE_URL'] = 'https://resource.example.invalid/mcp'
        os.environ['LAF_OAUTH_AUTHORIZATION_SERVER'] = 'https://issuer.example.invalid'
        os.environ['CODEX_HOME'] = '/fixture/must-not-change-account-reference'
        self.assertEqual(self.environment(), {**self.base(), **self.refs, 'LAF_MCP_EVENTS_ENABLED': '1'})

    def test_disabled_flag_does_not_forward_providers(self):
        self.enable()
        os.environ['LAF_MCP_EVENTS_ENABLED'] = '0'
        self.assertEqual(self.environment(), {**self.base(), 'LAF_MCP_EVENTS_ENABLED': '0'})

    def test_invalid_flags_reach_existing_validator_without_reference_authority(self):
        self.enable()
        for value in ('', 'true', '2', ' 1'):
            with self.subTest(value=value):
                os.environ['LAF_MCP_EVENTS_ENABLED'] = value
                self.assertEqual(self.environment(), {**self.base(), 'LAF_MCP_EVENTS_ENABLED': value})

    def test_missing_references_are_not_filled_or_discovered(self):
        os.environ['LAF_MCP_EVENTS_ENABLED'] = '1'
        os.environ['LAF_OAUTH_ISSUER'] = self.refs['LAF_OAUTH_ISSUER']
        self.assertEqual(self.environment(), {**self.base(), 'LAF_MCP_EVENTS_ENABLED': '1',
                                               'LAF_OAUTH_ISSUER': self.refs['LAF_OAUTH_ISSUER']})

    def test_subject_json_and_scope_text_are_preserved_without_reconstruction(self):
        self.enable()
        result = self.environment()
        self.assertEqual(result['LAF_OAUTH_ALLOWED_SUBJECTS_JSON'], '["fixture-owner", "fixture-reviewer"]')
        self.assertEqual(result['LAF_OAUTH_REQUIRED_SCOPES'], 'mcp:access fixture:read')

    def test_opted_in_foreground_keeps_guard_and_resource_controller(self):
        self.enable()
        code, captured = self.foreground(True)
        self.assertEqual(code, 0)
        self.assertEqual(captured['env'], {**self.base(), **self.refs, 'LAF_MCP_EVENTS_ENABLED': '1'})
        self.assertIn('--allow-codex-pipeline', captured['args'])

    def test_ordinary_foreground_still_drops_all_events_references(self):
        self.enable()
        code, captured = self.foreground(False)
        self.assertEqual(code, 0)
        self.assertEqual(captured['env'], self.base())
        self.assertNotIn('--allow-codex-pipeline', captured['args'])

    def test_isolation_refusal_stops_before_process_or_resource_admission(self):
        self.enable()
        with patch.object(daemon.scheduler, 'configuration', return_value={'daemon': {}}), \
                patch.object(daemon.development_poc, 'configure'), \
                patch.object(isolation, 'prepare', side_effect=ValueError('INJECTED_REFUSAL')), \
                patch.object(daemon, 'Control') as resource, patch.object(daemon, 'Process') as process:
            with self.assertRaises(ValueError):
                daemon.foreground(self.root, self.binary, allow_codex_pipeline=True)
        resource.assert_not_called()
        process.assert_not_called()

    def test_unconfirmed_resource_release_remains_failure(self):
        self.enable()
        code, _ = self.foreground(True, released=False)
        self.assertEqual(code, 1)
        receipt = json.loads((self.root / 'logs/daemon-controller-offline-notification-scope.json').read_bytes())
        self.assertFalse(receipt['resources']['released'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
