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

# Added 2026-11-15: offline correlated-read, notification-race and release-fence regressions.
"""Pure in-memory peers and private temp ledgers; no child, account or service."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'runtime/private_codex/src')]
import test_codex_catalog_effort as fixtures
from codex_app_protocol import ProtocolClient, ProtocolError, ProtocolUnknown, RpcError, StdioTransport
from codex_host import rpc_failure


def setUpModule():
    global GUARDS
    GUARDS = [patch(name, side_effect=AssertionError('Offline read tests forbid this effect'))
              for name in ('subprocess.Popen', 'socket.socket.bind', 'socket.socket.connect',
                           'threading.Thread.start')]
    for guard in GUARDS:
        guard.start()


def tearDownModule():
    for guard in reversed(GUARDS):
        guard.stop()


class ReadRace(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.GatewayTests('test_metadata_session_closes_and_releases_runner_budget')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.thread = self.f.start()
        self.task = self.f.submit(self.thread)
        self.ident = self.task['id']
        self.remote = self.f.gateway.pipeline._mutate(
            lambda s: s['threads'][self.thread]['remote_id'])
        self.assertEqual(self.task['state'], 'RUNNING')

    def state(self):
        return self.f.gateway.pipeline._mutate(lambda s: copy.deepcopy(s))

    def completion(self, status='completed', remote=None):
        turn = self.f.peer.threads[self.remote]['turns'][-1]
        turn.update(status=status, items=[])
        self.f.gateway.protocol.receive({'method': 'turn/completed', 'params': {
            'threadId': self.remote if remote is None else remote, 'turn': copy.deepcopy(turn)}})

    def reject_once(self, during=None, *, lost=False):
        transport = self.f.transports[-1]
        peer, used = transport.peer, False
        def exchange(request, timeout):
            nonlocal used
            if request['method'] == 'thread/read' and not used:
                used = True
                self.f.peer.calls.append(('thread/read', copy.deepcopy(request['params'])))
                if during is not None:
                    during()
                if lost:
                    raise OSError('Synthetic lost read reply')
                return {'id': request['id'], 'error': {
                    'code': -32601, 'message': 'Synthetic method not found'}}
            return peer(request, timeout)
        transport.peer = exchange

    def assert_one_turn(self):
        self.assertEqual(len(self.state()['tasks']), 1)
        self.assertEqual(self.f.peer.count('turn/start'), 1)

    def test_active_read_rejection_holds_then_reconciles_without_replay(self):
        self.reject_once()
        self.f.gateway.pump()
        self.assertEqual(self.state()['tasks'][self.ident]['state'], 'RUNNING')
        self.assertTrue(self.f.gateway.held)
        self.assertIsNotNone(self.f.gateway.budget)
        self.assertFalse(self.f.gateway.failed)
        self.completion()
        self.f.gateway.pump()
        self.assertTrue(self.state()['threads'][self.thread]['history_verified'])
        self.assertIsNone(self.f.gateway.budget)
        self.assert_one_turn()

    def test_completion_inside_rejected_read_returns_current_task(self):
        self.reject_once(self.completion)
        result = self.f.gateway.pipeline.refresh(self.ident)
        self.assertEqual(result['state'], 'COMPLETED')
        self.assertEqual(result['reconciliation'], 'UNKNOWN')
        self.assertFalse(result['automatic_retry'])
        self.assertIsNone(self.state()['threads'][self.thread]['active_task'])
        self.assert_one_turn()

    def test_interruption_inside_rejected_read_returns_current_task(self):
        self.reject_once(lambda: self.completion('interrupted'))
        result = self.f.gateway.pipeline.refresh(self.ident)
        self.assertEqual(result['state'], 'CANCELLED')
        self.assertEqual(result['reconciliation'], 'UNKNOWN')
        self.assert_one_turn()

    def test_approval_inside_rejected_read_preserves_waiting_barrier(self):
        def approval():
            turn = self.state()['tasks'][self.ident]['remote_turn']
            self.f.gateway.protocol.receive({'id': 'approval-1',
                'method': 'item/commandExecution/requestApproval',
                'params': {'threadId': self.remote, 'turnId': turn}})
        self.reject_once(approval)
        result = self.f.gateway.pipeline.refresh(self.ident)
        self.assertEqual(result['state'], 'WAITING_APPROVAL')
        self.assertEqual(result['reconciliation'], 'UNKNOWN')
        self.assertTrue(self.f.gateway.held)
        self.assert_one_turn()

    def test_completion_inside_lost_read_is_current_but_connection_fenced(self):
        self.reject_once(self.completion, lost=True)
        result = self.f.gateway.pipeline.refresh(self.ident)
        self.assertEqual(result['state'], 'COMPLETED')
        self.assertEqual(result['reconciliation'], 'UNKNOWN')
        self.assertTrue(self.f.gateway.failed)
        self.assertFalse(self.state()['threads'][self.thread]['history_verified'])
        self.assert_one_turn()

    def test_foreign_notification_does_not_complete_owned_task(self):
        self.reject_once(lambda: self.completion(remote='unowned-thread'))
        result = self.f.gateway.pipeline.refresh(self.ident)
        self.assertEqual(result['state'], 'RUNNING')
        self.assertFalse(self.state()['threads'][self.thread]['history_verified'])
        self.assert_one_turn()

    def test_duplicate_submit_after_read_rejection_retains_original_turn(self):
        self.reject_once()
        self.f.gateway.pipeline.refresh(self.ident)
        same = self.f.submit(self.thread)
        self.assertEqual(same['id'], self.ident)
        self.assert_one_turn()

    def test_final_history_rejection_does_not_turn_cleanup_into_readiness(self):
        self.completion()
        self.reject_once()
        self.f.gateway.release_idle()
        thread = self.state()['threads'][self.thread]
        self.assertFalse(thread['history_verified'])
        self.assertEqual(thread['native_context_state'], 'HISTORY_UNKNOWN')
        self.assertTrue(self.f.gateway.failed)
        self.assertIsNone(self.f.gateway.budget)
        self.assertEqual(json.loads((self.f.root / 'codex-pipeline-cleanup.json').read_text())['status'], 'RELEASED')
        self.assert_one_turn()


class ProtocolFrames(unittest.TestCase):
    def client(self, frames):
        transport = StdioTransport()
        transport._proc = SimpleNamespace()
        writes = []
        transport._write = lambda message, deadline: writes.append(copy.deepcopy(message))
        stream = iter([{'id': 1, 'result': fixtures.INITIALIZE}, *frames])
        transport._read = lambda deadline: next(stream, None)
        client = ProtocolClient(transport)
        transport.bind_client(client)
        client.initialize()
        return client, transport, writes

    def test_correlated_error_with_interleaved_events_allows_next_explicit_read(self):
        frames = [{'method': 'thread/status/changed', 'params': {'threadId': 'fixture'}},
                  {'id': 2, 'method': 'item/commandExecution/requestApproval', 'params': {}},
                  {'id': 2, 'error': {'code': -32601, 'message': 'Synthetic method not found'}},
                  {'id': 3, 'result': {'thread': fixtures.THREAD}}]
        client, transport, writes = self.client(frames)
        events, nested = [], []
        def receive(event):
            events.append(event)
            try:
                client.request('thread/read', {'threadId': 'fixture', 'includeTurns': True})
            except ProtocolError:
                nested.append('refused')
        client.event_sink = receive
        with self.assertRaises(RpcError) as error:
            client.request('thread/read', {'threadId': 'fixture', 'includeTurns': True})
        self.assertEqual(error.exception.code, -32601)
        self.assertFalse(transport._broken)
        self.assertEqual(len(events), 2)
        self.assertEqual(nested, ['refused', 'refused'])
        self.assertEqual([w['method'] for w in writes], ['initialize', 'initialized', 'thread/read'])
        self.assertEqual(client.request('thread/read', {'threadId': 'fixture', 'includeTurns': True})['thread']['id'], fixtures.THREAD['id'])
        self.assertEqual(len(writes), 4)

    def test_wrong_response_id_type_is_unknown_and_poisoned(self):
        client, transport, writes = self.client([
            {'id': '2', 'error': {'code': -32601, 'message': 'Synthetic rejection'}}])
        with self.assertRaises(ProtocolUnknown):
            client.request('thread/read', {'threadId': 'fixture', 'includeTurns': True})
        self.assertTrue(transport._broken)
        self.assertEqual(len(writes), 3)

    def test_lost_read_reply_is_unknown_without_automatic_resend(self):
        client, transport, writes = self.client([])
        with self.assertRaises(ProtocolUnknown):
            client.request('thread/read', {'threadId': 'fixture', 'includeTurns': True})
        self.assertTrue(transport._broken)
        self.assertEqual(len(writes), 3)


class Diagnostics(unittest.TestCase):
    def test_code_label_is_fixed_and_does_not_assert_native_cause(self):
        facts = rpc_failure('thread/read', RpcError(-32601, 'Synthetic reason'))
        self.assertEqual(facts['rpc_code_name'], 'METHOD_NOT_FOUND')
        self.assertEqual(facts['reason_categories'], ['UNCLASSIFIED_SERVER_REJECTION'])
        self.assertNotIn('Synthetic reason', json.dumps(facts))

    def test_message_categories_are_finite_deduplicated_and_private(self):
        secret = '/private/user-path TOKEN_FIXTURE'
        facts = rpc_failure('thread/read', RpcError(-32601,
            'Unknown method; method not found; paginated history is not supported. ' + secret))
        self.assertEqual(facts['reason_categories'],
                         ['METHOD_NOT_FOUND', 'PAGINATED_HISTORY', 'UNSUPPORTED_OPERATION'])
        self.assertNotIn(secret, json.dumps(facts))
        self.assertNotIn('TOKEN_FIXTURE', json.dumps(facts))

    def test_unknown_code_and_non_rpc_exception_never_export_body(self):
        facts = rpc_failure('thread/read', RpcError(-31999, 'TOKEN_FIXTURE'))
        self.assertEqual(facts['rpc_code_name'], 'OTHER')
        self.assertEqual(facts['reason_categories'], ['UNCLASSIFIED_SERVER_REJECTION'])
        plain = rpc_failure('thread/read', RuntimeError('TOKEN_FIXTURE'))
        self.assertEqual(plain, {'method': 'thread/read', 'exception_class': 'RuntimeError'})
        self.assertNotIn('TOKEN_FIXTURE', json.dumps([facts, plain]))
