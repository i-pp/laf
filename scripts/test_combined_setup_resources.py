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
# This header is a summary, not a separate license or an amendment to KOL-v1.
# All terms and conditions of KOL-v1 apply. See the accompanying LICENSE file for the complete license text.

# Added 2026-11-15: setup/resource integration and terminal-history proof fences.
"""In-memory peers/private SQLite only; never open the shared resource ledger."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'runtime/private_codex/src'),
               str(ROOT / 'runtime/integration')]
from codex_host import HostGateway
from codex_pipeline import Pipeline, PipelineError, digest
from scripts.source_test_isolation import require_isolated
require_isolated()  # Refuse raw resource tests before any fixture or project import.
import pipeline_resource_admission
import resource_admission
import test_notification_fresh_prefix as event_fixtures
import test_bootstrap_production_policy as policy_fixtures
import test_resource_only as resource_fixtures


def setUpModule():
    global GUARDS
    GUARDS = [patch(name, side_effect=AssertionError('No native, network or shared resource state in joint fixtures'))
        for name in ('subprocess.Popen', 'socket.socket.bind', 'socket.socket.connect',
                     'threading.Thread.start', 'resource_admission.Ledger',
                     'pipeline_resource_admission.Ledger')]
    for guard in GUARDS:
        guard.start()


def tearDownModule():
    for guard in reversed(GUARDS):
        guard.stop()


class HistoryProof(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = {'threads': {'thread': {'id': 'thread', 'remote_id': 'native-thread',
            'history_verified': False, 'archived': False, 'closing': False}},
            'tasks': {'task': {'id': 'task', 'thread_id': 'thread',
                              'state': 'COMPLETED', 'remote_turn': 'native-turn'}}}
        self.gateway = HostGateway.__new__(HostGateway)
        self.gateway.root = self.root
        self.gateway.pipeline = SimpleNamespace(_mutate=lambda function: function(self.state),
            loaded={'thread'}, policy={'schema': 2, 'timeout_seconds': 10}, transport=None)
        self.gateway.resource_gate = SimpleNamespace(reap=Mock(), pending=Mock(return_value=False))
        self.gateway.protocol = object()
        self.gateway.transport = SimpleNamespace(close=Mock(), cleanup_confirmed=True)
        self.gateway.budget = None
        self.gateway.failed = False
        self.gateway.context_deadlines = {}
        self.gateway.marker = Mock()
        self.gateway.rpc = Mock(return_value={'thread': {'id': 'native-thread',
            'turns': [{'id': 'native-turn', 'status': 'completed'}]}})

    def test_missing_grant_does_not_verify_legacy_history_after_read_refusal(self):
        self.gateway.rpc.side_effect = ValueError('Synthetic read rejection')
        self.gateway.release_idle()
        self.gateway.rpc.assert_called_once_with('thread/read',
            {'threadId': 'native-thread', 'includeTurns': True}, timeout=1)
        self.assertFalse(self.state['threads']['thread']['history_verified'])
        self.assertTrue(self.gateway.failed)
        self.assertEqual(self.state['threads']['thread']['native_context_state'], 'HISTORY_UNKNOWN')

    def test_missing_grant_still_requires_matching_remote_thread(self):
        self.gateway.rpc.return_value['thread']['id'] = 'other-thread'
        self.gateway.release_idle()
        self.assertFalse(self.state['threads']['thread']['history_verified'])
        self.assertTrue(self.gateway.failed)

    def test_correlated_complete_history_can_verify_an_uncharged_legacy_turn(self):
        self.gateway.release_idle()
        self.gateway.rpc.assert_called_once_with('thread/read',
            {'threadId': 'native-thread', 'includeTurns': True}, timeout=1)
        self.assertTrue(self.state['threads']['thread']['history_verified'])
        self.assertFalse(self.gateway.failed)
        self.assertEqual(self.state['tasks']['task']['state'], 'COMPLETED')

    def test_pending_resource_grant_retains_context_without_claiming_history(self):
        self.gateway.resource_gate.pending.return_value = True
        self.gateway.release_idle()
        self.gateway.rpc.assert_not_called()
        self.gateway.transport.close.assert_not_called()
        self.assertFalse(self.state['threads']['thread']['history_verified'])

    def test_one_matching_turn_does_not_verify_a_different_unproven_turn(self):
        self.state['tasks']['other'] = {'id': 'other', 'thread_id': 'thread',
            'state': 'CANCELLED', 'remote_turn': 'unproven-native-turn'}
        self.gateway.release_idle()
        self.assertFalse(self.state['threads']['thread']['history_verified'])
        self.assertTrue(self.gateway.failed)

    def test_nonresource_history_proof_is_retained(self):
        self.gateway.resource_gate = None
        self.gateway.release_idle()
        self.gateway.rpc.assert_called_once_with('thread/read',
            {'threadId': 'native-thread', 'includeTurns': True})
        self.assertTrue(self.state['threads']['thread']['history_verified'])


class NotificationComposition(unittest.TestCase):
    def fixture(self):
        fixture = event_fixtures.FreshPrefixEvents(
            'test_missing_first_ledger_activates_same_runtime_after_real_pipeline_creation')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def settings(self):
        # Complete, validated synthetic policy for the real admission constructor.
        # No reserve/ready call, measured profile or real controller is claimed.
        policy = resource_fixtures.policy()
        policy['pipeline'] = resource_fixtures.fixtures.v(100, 100, 0, 0)
        return {'resource_admission': resource_admission.policy(policy)}

    def test_resource_schema_refusal_precedes_notification_storage_activation(self):
        fixture = self.fixture()
        runtime = fixture.components()
        with self.assertRaises(PipelineError) as error:
            fixture.gateway(runtime, resource_settings=self.settings(),
                            resource_controller=lambda: ('owner', 'token'))
        self.assertEqual(error.exception.code, 'RESOURCE_MODE_REQUIRES_CONCURRENT_POLICY')
        self.assertFalse(runtime.enabled)
        self.assertIsNone(runtime.outbox)
        self.assertFalse(runtime.outbox_path.exists())
        self.assertEqual(fixture.effects, [])

    def test_schema2_passes_the_same_resource_gate_pipeline_and_events_runtime(self):
        fixture = self.fixture()
        policy = policy_fixtures.ProductionPolicyImport(
            'test_exact_schema2_values_and_raw_provenance_are_snapshotted')
        policy.setUp()
        self.addCleanup(policy.doCleanups)
        policy.configure()
        fixture.f, fixture.runtime = policy.f, policy.runtime
        runtime = fixture.components()
        settings = self.settings()
        controller = lambda: ('owner', 'token')
        gateway = fixture.gateway(runtime, resource_settings=settings,
                                  resource_controller=controller)
        gate = gateway.resource_gate
        self.assertIsInstance(gate, pipeline_resource_admission.PipelineAdmission)
        self.assertIs(gate.gateway, gateway)
        self.assertIs(gate.settings, settings)
        self.assertIs(gate.controller, controller)
        self.assertIs(gateway.pipeline.resource_gate, gate)
        self.assertTrue(runtime.enabled)
        self.assertIs(gateway.notification_worker_factory.__self__, runtime)
        self.assertEqual(runtime.outbox.source_path, gateway.pipeline.db)
        self.assertEqual(gateway.pipeline._mutate(lambda state: len(state['tasks'])), 0)
        self.assertEqual(fixture.provider_calls, 1)
        self.assertEqual(fixture.effects, [])


class ModePin(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        workspace = self.root / 'workspace'
        workspace.mkdir(mode=0o700)
        self.policy = {'schema': 2, 'workspaces': {'fixture': str(workspace)},
            'models': ['fixture-model'], 'max_threads': 4, 'max_tasks': 8,
            'concurrency': 2, 'timeout_seconds': 10}
        self.common = '0' * 64
        self.client = SimpleNamespace(event_sink=None, request=Mock(
            side_effect=AssertionError('No provider call in mode migration fixture')))

    def create(self, resource=False):
        return Pipeline(self.root, self.policy, self.client,
            instructions='Synthetic mode-pinning fixture only', policy_sha256=self.common,
            resource_gate=object() if resource else None)

    def state(self):
        import sqlite3
        with closing(sqlite3.connect('file:' + str(self.root / 'codex-pipeline.sqlite3') + '?mode=ro', uri=True)) as connection:
            return json.loads(connection.execute('SELECT body FROM ledger WHERE id=1').fetchone()[0])

    def test_legacy_policy_hash_remains_byte_compatible(self):
        pipeline = self.create()
        self.assertEqual(pipeline.policy_hash, digest({'policy': self.policy, 'common': self.common}))

    def test_legacy_to_resource_refuses_without_mutating_retained_history(self):
        pipeline = self.create()
        pipeline._mutate(lambda state: state['tasks'].update({'legacy': {
            'id': 'legacy', 'thread_id': 'thread', 'remote_turn': 'old-turn', 'state': 'COMPLETED'}}))
        before = self.state()
        with self.assertRaises(PipelineError) as error:
            self.create(resource=True)
        self.assertEqual(error.exception.code, 'PIPELINE_POLICY_CHANGED')
        self.assertEqual(self.state(), before)
        self.client.request.assert_not_called()

    def test_resource_to_legacy_also_refuses_without_rewriting(self):
        self.create(resource=True)
        before = self.state()
        with self.assertRaises(PipelineError) as error:
            self.create()
        self.assertEqual(error.exception.code, 'PIPELINE_POLICY_CHANGED')
        self.assertEqual(self.state(), before)

    def test_same_resource_mode_reopens_without_fabricating_a_new_lineage(self):
        first = self.create(resource=True)
        before = self.state()
        second = self.create(resource=True)
        self.assertEqual(first.policy_hash, second.policy_hash)
        self.assertNotEqual(first.policy_hash, digest({'policy': self.policy, 'common': self.common}))
        self.assertEqual(self.state(), before)
        self.client.request.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
