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

# Added 2026-11-15: offline receipt/interruption/candidate-drift acceptance oracles.
"""Synthetic peers only. These tests neither install a plugin nor run inference."""
import asyncio
import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from contextlib import asynccontextmanager, contextmanager, ExitStack
from types import ModuleType, SimpleNamespace

import laf_acceptance as a
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime/integration'), str(ROOT / 'runtime/private_codex/src'),
               str(ROOT / 'runtime/public_laf/mcp/src')]
import laf_bootstrap as boot
from codex_pipeline import PipelineError


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.base.chmod(0o700)

    def journal(self, name='journal.jsonl'):
        result = a.Journal(self.base / name)
        self.addCleanup(result.close)
        return result

    def run_peer(self, peer, *, journal=None):
        return a.Run(journal or self.journal(), {'candidate': 'synthetic'}, peer,
                     wait=lambda _: asyncio.sleep(0))

    def package(self):
        root = self.base / 'installed'
        root.mkdir()
        values = {'launcher.py': b'# synthetic no execution\n',
                  '.codex-plugin/plugin.json': b'{"name":"laf","version":"0.1.0"}',
                  '.mcp.json': a.packed({'mcpServers': {'laf': {'command': 'python3',
                    'args': ['-B', './launcher.py'], 'cwd': '.'}}}).encode()}
        archive = self.base / 'candidate.zip'
        with zipfile.ZipFile(archive, 'w') as target:
            for name, raw in values.items():
                path = root / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(raw)
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                target.writestr(info, raw)
        return archive, root

    def test_exact_installed_package_and_manifest(self):
        archive, root = self.package()
        result = a.verify_package(archive, root)
        self.assertEqual(result['source_members'], 3)
        self.assertEqual(result['archive_sha256'], a.digest(archive.read_bytes()))

    def test_installed_byte_drift_blocks(self):
        archive, root = self.package()
        (root / 'launcher.py').write_bytes(b'# changed\n')
        with self.assertRaisesRegex(a.Refused, 'installed_package_drift'):
            a.verify_package(archive, root)

    def test_source_symlink_blocks(self):
        archive, root = self.package()
        (root / 'launcher.py').unlink()
        (root / 'launcher.py').symlink_to(root / '.mcp.json')
        with self.assertRaisesRegex(a.Refused, 'symlink'):
            a.verify_package(archive, root)

    def test_unexpected_installed_code_is_not_ignored(self):
        archive, root = self.package()
        (root / 'unexpected.py').write_bytes(b'raise RuntimeError()\n')
        with self.assertRaisesRegex(a.Refused, 'unexpected_installed_files'):
            a.verify_package(archive, root)

    def test_archive_duplicate_and_traversal_block(self):
        archive, root = self.package()
        for name in ('launcher.py', '../outside'):
            with self.subTest(name=name):
                new = self.base / ('bad-' + str(len(name)) + '.zip')
                new.write_bytes(archive.read_bytes())
                with zipfile.ZipFile(new, 'a') as target:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = (stat.S_IFREG | 0o600) << 16
                    target.writestr(info, b'bad')
                with self.assertRaises(a.Refused):
                    a.verify_package(new, root)

    def test_journal_partial_tail_fences_without_modification(self):
        path = self.base / 'partial'
        path.write_bytes(b'{"kind":"intent"')
        path.chmod(0o600)
        with self.assertRaisesRegex(a.Refused, 'incomplete_journal'):
            a.Journal(path)
        self.assertEqual(path.read_bytes(), b'{"kind":"intent"')

    def test_duplicate_json_and_nonfinite_journal_refused(self):
        for index, raw in enumerate((b'{"kind":1,"kind":2}\n', b'{"value":NaN}\n')):
            path = self.base / str(index)
            path.write_bytes(raw)
            path.chmod(0o600)
            with self.assertRaises(a.Refused):
                a.Journal(path)
            self.assertEqual(path.read_bytes(), raw)

    def test_journal_replacement_preserves_replacement(self):
        journal = self.journal()
        journal.append({'kind': 'test'})
        journal.path.rename(self.base / 'retained-original')
        journal.path.write_bytes(b'foreign')
        with self.assertRaisesRegex(a.Refused, 'journal_identity_changed'):
            journal.append({'kind': 'later'})
        self.assertEqual(journal.path.read_bytes(), b'foreign')

    def test_journal_concurrent_owner_and_unsafe_mode_block(self):
        first = self.journal()
        with self.assertRaises(BlockingIOError):
            a.Journal(first.path)
        bad = self.base / 'unsafe'
        bad.write_bytes(b'')
        bad.chmod(0o644)
        with self.assertRaises(a.Refused):
            a.Journal(bad)
        self.assertEqual(stat.S_IMODE(bad.stat().st_mode), 0o644)

    def test_journal_hardlink_and_capacity_block(self):
        path = self.base / 'linked'
        path.write_bytes(b'')
        path.chmod(0o600)
        os.link(path, self.base / 'other')
        with self.assertRaises(a.Refused):
            a.Journal(path)
        journal = self.journal()
        with patch.object(a, 'LIMIT', 8):
            with self.assertRaisesRegex(a.Refused, 'journal_capacity'):
                journal.append({'long': 'data'})
        self.assertEqual(journal.path.read_bytes(), b'')

    def test_configuration_or_candidate_change_fences_resume(self):
        journal = self.journal()
        async def peer(*args):
            self.fail('No peer after pin drift')
        a.Run(journal, {'candidate': 'one'}, peer)
        with self.assertRaisesRegex(a.Refused, 'candidate_or_configuration_changed'):
            a.Run(journal, {'candidate': 'two'}, peer)

    def test_live_config_and_binding_changes_are_both_fenced(self):
        config, binding = self.base / 'config', self.base / 'bindings'
        config.write_bytes(b'original config')
        binding.write_bytes(b'original bindings')
        pins = {'config_sha256': a.digest(config.read_bytes()),
                'binding_sha256': a.digest(binding.read_bytes())}
        a.check_configuration(config, binding, pins)
        config.write_bytes(b'changed config')
        with self.assertRaisesRegex(a.Refused, 'configuration_changed'):
            a.check_configuration(config, binding, pins)
        config.write_bytes(b'original config')
        binding.write_bytes(b'changed binding')
        with self.assertRaisesRegex(a.Refused, 'backend_binding_changed'):
            a.check_configuration(config, binding, pins)

    def test_exact_catalog_gate_rejects_count_duplicate_schema_version_and_cursor(self):
        expected = [{'name': 'tool-' + str(i), 'description': 'synthetic',
                     'inputSchema': {'type': 'object'}, 'annotations': {}} for i in range(33)]
        a.check_catalog(copy.deepcopy(expected), expected, '0.1.0')
        changed = copy.deepcopy(expected)
        changed[0]['inputSchema'] = {'type': 'string'}
        cases = [(expected[:-1], '0.1.0', None), (expected[:-1] + [expected[0]], '0.1.0', None),
                 (changed, '0.1.0', None), (expected, '0.2.0', None), (expected, '0.1.0', 'more')]
        for found, version, cursor in cases:
            with self.subTest(version=version, cursor=cursor, count=len(found)):
                with self.assertRaises(a.Refused):
                    a.check_catalog(found, expected, version, cursor)

    def test_mcp_result_shapes_and_error_envelopes(self):
        from types import SimpleNamespace
        for body in ({'data': {'status': 'READY'}, 'call_id': 'private'},
                     {'status': 'READY'}):
            self.assertEqual(a.tool_data(SimpleNamespace(structuredContent=body, isError=False)),
                             {'status': 'READY'})
        for body, error in ((None, False), ({'data': []}, False), ({'state': 'COMPLETED'}, True)):
            with self.assertRaises(a.Refused):
                a.tool_data(SimpleNamespace(structuredContent=body, isError=error))
        captured = a.tool_data(SimpleNamespace(isError=True,
            structuredContent={'data': {'id': 'known', 'state': 'UNKNOWN'}}), capture_error=True)
        self.assertEqual(captured, {'id': 'known', 'state': 'UNKNOWN', '_mcp_is_error': True})

    def test_fsync_failure_prevents_dispatch_and_keeps_uncertain_intent(self):
        calls = []
        async def peer(*args):
            calls.append(args)
        run = self.run_peer(peer)
        with patch.object(a.os, 'fsync', side_effect=OSError('synthetic fsync failure')):
            with self.assertRaises(OSError):
                asyncio.run(run.once('local', 'laf_router_submit_job', {'attempt_id': 'a' * 32}))
        self.assertEqual(calls, [])
        self.assertIn(b'"kind":"intent"', run.journal.path.read_bytes())

    def test_local_completion_uses_one_submission_and_duplicate_run_only_polls(self):
        calls = []
        async def peer(name, args):
            calls.append((name, copy.deepcopy(args)))
            if name == 'laf_router_status':
                return {'status': 'ready'}
            if name == 'laf_router_models':
                return {'models': ['flash']}
            return {'handle': run.plan['keys']['local'], 'receipt_durable': True,
                    'outcome': 'TERMINAL', 'status': 'completed', 'job_id': 'b' * 32,
                    'model': 'flash', 'content': a.EXPECTED_REPLY}
        run = self.run_peer(peer)
        self.assertEqual(asyncio.run(run.local('flash'))['status'], 'passed')
        self.assertEqual(asyncio.run(run.local('flash'))['status'], 'passed')
        self.assertEqual(sum(n == 'laf_router_submit_job' for n, _ in calls), 1)
        sent = next(args for name, args in calls if name == 'laf_router_submit_job')
        self.assertEqual((sent['max_tokens'], sent['timeout_seconds']), (16, 30))

    def test_lost_local_ack_only_reads_original_handle(self):
        calls = []
        async def peer(name, args):
            calls.append((name, args))
            if name == 'laf_router_submit_job':
                raise TimeoutError()
            return {'handle': run.plan['keys']['local'], 'receipt_durable': True}
        run = self.run_peer(peer)
        key = run.plan['keys']['local']
        args = {'attempt_id': key}
        with self.assertRaises(TimeoutError):
            asyncio.run(run.once('local', 'laf_router_submit_job', args,
                        reconcile=('laf_router_get_job', {'handle': key})))
        asyncio.run(run.once('local', 'laf_router_submit_job', args,
                    reconcile=('laf_router_get_job', {'handle': key})))
        self.assertEqual([name for name, _ in calls],
                         ['laf_router_submit_job', 'laf_router_get_job'])
        self.assertEqual(calls[-1][1]['handle'], key)

    def test_local_loop_exhaustion_does_not_cancel_or_resubmit(self):
        calls = []
        async def peer(name, args):
            calls.append(name)
            if name == 'laf_router_status':
                return {'status': 'ready'}
            if name == 'laf_router_models':
                return {'models': ['flash']}
            return {'handle': run.plan['keys']['local'], 'model': 'flash',
                    'job_id': 'b' * 32, 'receipt_durable': True,
                    'status': 'running', 'outcome': 'ADMITTED'}
        run = self.run_peer(peer)
        with self.assertRaisesRegex(a.Refused, 'local_wait_bound'):
            asyncio.run(run.local('flash'))
        self.assertEqual(calls.count('laf_router_submit_job'), 1)
        self.assertEqual(calls.count('laf_router_get_job'), 35)
        self.assertNotIn('laf_router_cancel_job', calls)

    def test_conflicting_input_blocks_without_another_call(self):
        calls = []
        async def peer(*args):
            calls.append(args)
            return {}
        run = self.run_peer(peer)
        asyncio.run(run.once('local', 'laf_router_submit_job', {'model': 'flash'}))
        with self.assertRaisesRegex(a.Refused, 'conflicting_operation_input'):
            asyncio.run(run.once('local', 'laf_router_submit_job', {'model': 'other'}))
        self.assertEqual(len(calls), 1)

    def test_unknown_receipt_never_replays_generation(self):
        async def peer(name, args):
            if name == 'laf_router_status':
                return {'status': 'ready'}
            if name == 'laf_router_models':
                return {'models': ['flash']}
            return {'handle': run.plan['keys']['local'], 'receipt_durable': True,
                    'outcome': 'UNKNOWN', 'error': 'no_acknowledged_job'}
        run = self.run_peer(peer)
        with self.assertRaisesRegex(a.Refused, 'local_outcome_unknown'):
            asyncio.run(run.local('flash'))
        self.assertEqual(sum(r.get('label') == 'local' and r['kind'] == 'intent'
                             for r in run.journal.rows), 1)

    def test_no_ready_or_wrong_model_never_submits(self):
        calls = []
        async def peer(name, args):
            calls.append(name)
            return {'status': 'not_ready'} if name.endswith('status') else {'models': ['flash']}
        run = self.run_peer(peer)
        with self.assertRaises(a.Refused):
            asyncio.run(run.local('other'))
        self.assertNotIn('laf_router_submit_job', calls)

    def test_codex_same_key_reconciles_lost_submit_without_new_input(self):
        calls = []
        async def peer(name, args):
            calls.append((name, copy.deepcopy(args)))
            if len(calls) == 1:
                raise TimeoutError()
            return {'id': 'known-task'}
        run = self.run_peer(peer)
        args = {'request_key': run.plan['keys']['task'], 'prompt': a.PROMPT}
        with self.assertRaises(TimeoutError):
            asyncio.run(run.once('task', 'codex_task_submit', args))
        result = asyncio.run(run.once('task', 'codex_task_submit', args))
        self.assertEqual(result['id'], 'known-task')
        self.assertEqual(calls[0], calls[1])

    def codex_peer(self, calls, *, effort='max', state='COMPLETED', answer=a.EXPECTED_REPLY,
                   capacity=None, overrides=None):
        async def peer(name, args):
            calls.append((name, copy.deepcopy(args)))
            if name == 'codex_models_list':
                return {'models': [{'model': 'gpt-6.1-sol', 'selectable': True,
                                    'supported_reasoning_efforts': ['medium', 'max']}]}
            if name == 'codex_threads_list':
                return {'capacity': capacity or {'active_threads': 0, 'pending_inputs': 0, 'held_tasks': 0,
                        'executor_release_confirmed': True, 'executor_reconciliation_required': False}}
            if name == 'codex_thread_start':
                return {'status': 'READY', 'thread_id': 'thread-one'}
            if name == 'codex_task_submit':
                return {'id': 'task-one'}
            if name == 'codex_task_get':
                return {'id': 'task-one', 'thread_id': 'thread-one', 'model': 'gpt-6.1-sol',
                        'reasoning_effort': effort, 'answer': answer, 'state': state,
                        'prompt_sha256': a.digest(a.packed(a.PROMPT).encode()),
                        'context_reasoning_effort_evidence': {'method': 'thread/start',
                            'parameter': 'config.model_reasoning_effort', 'requested': 'max',
                            'transport': 'acknowledged', 'provider_echo': None},
                        'reasoning_effort_evidence': {'turn_parameter': 'effort',
                            'turn_transport': 'acknowledged'}, **(overrides or {})}
            self.fail('Unexpected tool')
        return peer

    def test_codex_completion_forwards_exact_model_and_effort_once(self):
        calls = []
        run = self.run_peer(self.codex_peer(calls))
        result = asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))
        self.assertEqual(result['task_id'], 'task-one')
        self.assertFalse(result['provider_internal_effort_verified'])
        self.assertFalse(result['billing_verified'])
        for name in ('codex_thread_start', 'codex_task_submit'):
            sent = next(args for method, args in calls if method == name)
            self.assertEqual((sent['model'], sent['reasoning_effort']), ('gpt-6.1-sol', 'max'))
        asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))
        self.assertEqual(sum(name == 'codex_task_submit' for name, _ in calls), 1)

    def test_codex_own_empty_context_hold_allows_first_input_then_requires_release(self):
        calls = []
        original = self.codex_peer(calls)
        async def peer(name, args):
            methods = [method for method, _ in calls]
            if (name == 'codex_threads_list' and 'codex_thread_start' in methods
                    and 'codex_task_submit' not in methods):
                return {'capacity': {'active_threads': 0, 'pending_inputs': 0, 'held_tasks': 0,
                    'executor_release_confirmed': False, 'executor_reconciliation_required': False}}
            return await original(name, args)
        run = self.run_peer(peer)
        self.assertEqual(asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))['status'], 'passed')
        self.assertEqual(sum(method == 'codex_task_submit' for method, _ in calls), 1)

    def test_codex_busy_fences_new_dispatch(self):
        calls = []
        run = self.run_peer(self.codex_peer(calls, capacity={
            'active_threads': 1, 'pending_inputs': 0, 'held_tasks': 1}))
        with self.assertRaisesRegex(a.Refused, 'codex_not_idle'):
            asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))
        self.assertNotIn('codex_thread_start', [name for name, _ in calls])

    def test_codex_known_running_task_can_be_reconciled_when_not_idle(self):
        calls = []
        run = self.run_peer(self.codex_peer(calls))
        asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))
        original = self.codex_peer(calls)
        seen = []
        async def peer(name, args):
            if name == 'codex_task_get':
                seen.append(name)
            if name == 'codex_threads_list' and not seen:
                return {'capacity': {'active_threads': 1, 'pending_inputs': 0, 'held_tasks': 1}}
            return await original(name, args)
        run.call = peer
        asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))
        self.assertEqual(sum(name == 'codex_task_submit' for name, _ in calls), 1)

    def test_codex_unknown_or_approval_wait_never_passes_or_cancels_implicitly(self):
        for state in ('UNKNOWN', 'WAITING_APPROVAL'):
            with self.subTest(state=state):
                calls = []
                run = self.run_peer(self.codex_peer(calls, state=state),
                                    journal=self.journal(state))
                with self.assertRaisesRegex(a.Refused, 'codex_unresolved'):
                    asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))
                self.assertNotIn('codex_task_cancel', [name for name, _ in calls])

    def test_codex_failed_or_wrong_answer_or_effort_never_passes(self):
        for index, values in enumerate(({'state': 'FAILED'}, {'answer': 'wrong'}, {'effort': 'medium'})):
            with self.subTest(values=values):
                run = self.run_peer(self.codex_peer([], **values),
                                    journal=self.journal('failed-' + str(index)))
                with self.assertRaises(a.Refused):
                    asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))

    def test_codex_full_lost_ack_recovers_owned_task_without_catalog_or_resubmit(self):
        calls, lost, physical_admissions = [], [False], []
        original = self.codex_peer(calls)
        async def peer(name, args):
            if name == 'codex_task_submit':
                calls.append((name, copy.deepcopy(args)))
                if not lost[0]:
                    physical_admissions.append(args['request_key'])
                    lost[0] = True
                    raise TimeoutError('synthetic lost acknowledgment after durable admission')
                self.assertEqual(args['request_key'], physical_admissions[0])
                return {'id': 'task-one', 'duplicate': True}
            if name == 'codex_models_list' and lost[0]:
                self.fail('A resumed known task does not depend on fresh catalog')
            if name == 'codex_threads_list' and lost[0] and not any(n == 'codex_task_get' for n, _ in calls):
                return {'tasks': [{'id': 'task-one', 'thread_id': 'thread-one',
                    'model': 'gpt-6.1-sol', 'reasoning_effort': 'max',
                    'prompt_sha256': a.digest(a.packed(a.PROMPT).encode()), 'state': 'RUNNING'}],
                    'capacity': {'active_threads': 1, 'pending_inputs': 0, 'held_tasks': 1}}
            return await original(name, args)
        journal = self.journal()
        run = self.run_peer(peer, journal=journal)
        with self.assertRaises(TimeoutError):
            asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))
        resumed = self.run_peer(peer, journal=journal)
        self.assertEqual(asyncio.run(resumed.codex('default', 'gpt-6.1-sol', 'max'))['task_id'], 'task-one')
        submits = [args for name, args in calls if name == 'codex_task_submit']
        self.assertEqual(len(physical_admissions), 1)
        self.assertEqual(submits[0], submits[1])

    def test_codex_terminal_identity_and_forwarding_negative_oracles(self):
        cases = [{'id': 'wrong-task'}, {'thread_id': 'wrong-thread'}, {'prompt_sha256': 'bad'},
                 {'context_reasoning_effort_evidence': {}}, {'reasoning_effort_evidence': {}},
                 {'context_reasoning_effort_evidence': {'method': 'thread/start',
                    'parameter': 'config.model_reasoning_effort', 'requested': 'medium',
                    'transport': 'acknowledged'}},
                 {'reasoning_effort_evidence': {'turn_parameter': 'effort', 'turn_transport': 'not_sent'}}]
        for index, override in enumerate(cases):
            with self.subTest(override=override):
                run = self.run_peer(self.codex_peer([], overrides=override),
                    journal=self.journal('negative-' + str(index)))
                with self.assertRaises(a.Refused):
                    asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))

    def test_codex_final_cleanup_fence(self):
        calls = []
        original = self.codex_peer(calls)
        async def peer(name, args):
            if name == 'codex_threads_list' and any(n == 'codex_task_get' for n, _ in calls):
                return {'capacity': {'active_threads': 0, 'held_tasks': 0, 'pending_inputs': 0,
                    'executor_release_confirmed': False, 'executor_reconciliation_required': True}}
            return await original(name, args)
        run = self.run_peer(peer)
        with self.assertRaisesRegex(a.Refused, 'codex_not_idle'):
            asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))

    def test_codex_error_receipt_keeps_exact_task_for_cancellation(self):
        calls = []
        original = self.codex_peer(calls, state='UNKNOWN')
        async def peer(name, args):
            if name == 'codex_task_submit':
                calls.append((name, copy.deepcopy(args)))
                return {'id': 'task-one', 'thread_id': 'thread-one',
                        'state': 'UNKNOWN', '_mcp_is_error': True}
            if name == 'codex_task_cancel':
                calls.append((name, copy.deepcopy(args)))
                return {'status': 'STOP_REQUESTED'}
            return await original(name, args)
        run = self.run_peer(peer)
        with self.assertRaises(a.Refused):
            asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))
        self.assertEqual(run.known_task()['id'], 'task-one')
        result = asyncio.run(run.cancel())
        self.assertFalse(result['physical_stop_confirmed'])
        self.assertEqual(next(args['task_id'] for name, args in calls
                              if name == 'codex_task_cancel'), 'task-one')

    def test_codex_lost_cancel_ack_reuses_key_and_never_claims_stop(self):
        calls, interrupts = [], {}
        async def peer(name, args):
            calls.append((name, copy.deepcopy(args)))
            if name == 'codex_task_cancel':
                key = args['request_key']
                if key not in interrupts:
                    interrupts[key] = copy.deepcopy(args)
                    raise TimeoutError('synthetic lost cancel acknowledgment')
                self.assertEqual(interrupts[key], args)
                return {'status': 'STOP_REQUESTED', 'duplicate': True}
            return {'id': 'task-one', 'state': 'RUNNING'}
        run = self.run_peer(peer)
        run.journal.append({'kind': 'result', 'label': 'task', 'result': {'id': 'task-one'}})
        with self.assertRaises(TimeoutError):
            asyncio.run(run.cancel())
        result = asyncio.run(run.cancel())
        self.assertFalse(result['physical_stop_confirmed'])
        asyncio.run(run.cancel())
        self.assertEqual(len(interrupts), 1)
        self.assertEqual(sum(name == 'codex_task_cancel' for name, _ in calls), 2)

    def test_codex_poll_exhaustion_preserves_one_task_without_implicit_cancel(self):
        calls = []
        run = self.run_peer(self.codex_peer(calls, state='RUNNING'))
        with self.assertRaisesRegex(a.Refused, 'codex_wait_bound'):
            asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))
        self.assertEqual(sum(name == 'codex_task_submit' for name, _ in calls), 1)
        self.assertEqual(sum(name == 'codex_task_get' for name, _ in calls), 125)
        self.assertNotIn('codex_task_cancel', [name for name, _ in calls])

    def test_unknown_codex_cancel_cannot_adopt_same_thread_same_content_foreign_key(self):
        calls = []
        original = self.codex_peer(calls)
        async def peer(name, args):
            if name == 'codex_threads_list':
                return {'capacity': {'active_threads': 1, 'pending_inputs': 0, 'held_tasks': 1},
                        'tasks': [{'id': 'foreign', 'thread_id': 'thread-one', 'model': 'gpt-6.1-sol',
                            'reasoning_effort': 'max', 'prompt_sha256': a.digest(a.packed(a.PROMPT).encode())}]}
            return await original(name, args)
        run = self.run_peer(peer)
        for label, arguments in (
                ('thread', {'workspace': 'default', 'model': 'gpt-6.1-sol',
                    'reasoning_effort': 'max', 'request_key': run.plan['keys']['thread']}),
                ('task', {'thread_id': 'thread-one', 'model': 'gpt-6.1-sol', 'reasoning_effort': 'max',
                    'prompt': a.PROMPT, 'request_key': run.plan['keys']['task']})):
            run.journal.append({'kind': 'intent', 'label': label,
                'tool': 'codex_thread_start' if label == 'thread' else 'codex_task_submit',
                'arguments': arguments})
        run.journal.append({'kind': 'result', 'label': 'thread',
                            'result': {'status': 'READY', 'thread_id': 'thread-one'}})
        with self.assertRaisesRegex(a.Refused, 'task_receipt_unresolved'):
            asyncio.run(run.cancel())
        self.assertEqual(calls, [])

    def test_local_terminal_identity_negative_oracles(self):
        cases = [{'handle': 'wrong'}, {'model': 'other'}, {'job_id': 'c' * 32},
                 {'receipt_durable': False}, {'outcome': 'ADMITTED'}]
        for index, override in enumerate(cases):
            with self.subTest(override=override):
                async def peer(name, args):
                    if name == 'laf_router_status':
                        return {'status': 'ready'}
                    if name == 'laf_router_models':
                        return {'models': ['flash']}
                    return {'handle': run.plan['keys']['local'], 'model': 'flash',
                        'receipt_durable': True, 'job_id': 'b' * 32, 'status': 'completed',
                        'outcome': 'TERMINAL', 'content': a.EXPECTED_REPLY,
                        **(override if name == 'laf_router_get_job' else {})}
                run = self.run_peer(peer, journal=self.journal('receipt-' + str(index)))
                with self.assertRaises(a.Refused):
                    asyncio.run(run.local('flash'))

    def test_ready_wrong_model_and_unready_allowed_model_are_independent(self):
        for index, (status, models) in enumerate((('ready', ['other']), ('not_ready', ['flash']))):
            calls = []
            async def peer(name, args):
                calls.append(name)
                return {'status': status} if name.endswith('status') else {'models': models}
            run = self.run_peer(peer, journal=self.journal('gates-' + str(index)))
            with self.assertRaises(a.Refused):
                asyncio.run(run.local('flash'))
            self.assertNotIn('laf_router_submit_job', calls)

    def test_catalog_unsupported_effort_never_starts_thread(self):
        calls = []
        async def peer(name, args):
            calls.append(name)
            return {'models': [{'model': 'gpt-6.1-sol', 'selectable': True,
                                'supported_reasoning_efforts': ['medium']}]}
        run = self.run_peer(peer)
        with self.assertRaisesRegex(a.Refused, 'not_advertised'):
            asyncio.run(run.codex('default', 'gpt-6.1-sol', 'max'))
        self.assertEqual(calls, ['codex_models_list'])

    def test_cancel_ack_does_not_claim_physical_stop(self):
        calls = []
        async def peer(name, args):
            calls.append(name)
            return {'cancel_outcome': 'REQUESTED', 'status': 'running'}
        run = self.run_peer(peer)
        key = run.plan['keys']['local']
        run.journal.append({'kind': 'intent', 'label': 'local', 'tool': 'laf_router_submit_job',
                            'arguments': {'attempt_id': key}})
        result = asyncio.run(run.cancel())
        self.assertFalse(result['physical_stop_confirmed'])
        asyncio.run(run.cancel())
        self.assertEqual(calls.count('laf_router_cancel_job'), 1)

    def test_lost_router_cancel_ack_reconciles_without_second_cancel(self):
        calls = []
        async def peer(name, args):
            calls.append(name)
            if name == 'laf_router_cancel_job':
                raise TimeoutError()
            return {'status': 'running', 'cancel_outcome': 'UNKNOWN'}
        run = self.run_peer(peer)
        run.journal.append({'kind': 'intent', 'label': 'local', 'tool': 'laf_router_submit_job',
                            'arguments': {'attempt_id': run.plan['keys']['local']}})
        with self.assertRaises(TimeoutError):
            asyncio.run(run.cancel())
        self.assertFalse(asyncio.run(run.cancel())['physical_stop_confirmed'])
        self.assertEqual(calls.count('laf_router_cancel_job'), 1)

    def test_interruption_preserves_original_keys_on_reopen(self):
        calls = []
        async def peer(name, args):
            calls.append((name, args))
            if name == 'laf_router_submit_job':
                raise KeyboardInterrupt()
            return {'handle': args['handle']}
        journal = a.Journal(self.base / 'interrupted')
        run = self.run_peer(peer, journal=journal)
        key = run.plan['keys']['local']
        with self.assertRaises(KeyboardInterrupt):
            asyncio.run(run.once('local', 'laf_router_submit_job', {'attempt_id': key},
                                reconcile=('laf_router_get_job', {'handle': key})))
        journal.close()
        again = self.run_peer(peer, journal=self.journal('interrupted'))
        asyncio.run(again.once('local', 'laf_router_submit_job', {'attempt_id': key},
                              reconcile=('laf_router_get_job', {'handle': key})))
        self.assertEqual(again.plan['keys']['local'], key)
        self.assertEqual([name for name, _ in calls], ['laf_router_submit_job', 'laf_router_get_job'])

    def test_copied_runtime_drift_is_checked_before_import(self):
        source = self.base / 'runtime'
        copied = source / 'integration/laf_stdio_cli.py'
        copied.parent.mkdir(parents=True)
        copied.write_bytes(b'# changed source\n')
        class Loader:
            def exec_module(self, module):
                module.source_snapshot = lambda: {'integration/laf_stdio_cli.py': b'# expected\n'}
        from types import SimpleNamespace
        with patch('importlib.util.spec_from_file_location', return_value=SimpleNamespace(loader=Loader())), \
                patch('importlib.util.module_from_spec', return_value=SimpleNamespace()):
            with self.assertRaisesRegex(a.Refused, 'copied_runtime_drift'):
                a.verify_runtime(self.base, {'schema': 2, 'runtime_source_root': str(source)})

    @contextmanager
    def sdk_peer(self, *, mutate=False, lost=False, hang=False):
        """Faithful SDK-shaped in-process session; creating children is forbidden."""
        from legacy_tool_adapter import tool_definitions, SERVER_INFO
        from laf_shared_tools import CompositeSharedTools
        expected = CompositeSharedTools(SimpleNamespace(
            tools=tool_definitions(local_llm=True, local_models=True, chat_mailbox=True,
                codex_pipeline=True, experiments=True), call_tool=lambda *args: None, audit=None),
            SimpleNamespace(call=lambda *args: None), server_info=SERVER_INFO).tools
        home = self.base / 'sdk-home'
        config = home / '.config/laf/plugin.json'
        config.parent.mkdir(parents=True)
        config.write_text(a.packed({'schema': 2, 'runtime_source_root': str(ROOT / 'runtime'),
                                    'runtime': str(self.base / 'runtime')}))
        calls, flags = [], {}
        class Session:
            def __init__(self, *args, **kwargs):
                flags['timeout_seconds'] = kwargs['read_timeout_seconds'].total_seconds()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                flags['session_closed'] = True
            async def initialize(self):
                return SimpleNamespace(serverInfo=SimpleNamespace(version='0.1.0'))
            async def list_tools(self):
                return SimpleNamespace(tools=[SimpleNamespace(
                    model_dump=lambda row=row, **kw: copy.deepcopy(row)) for row in expected],
                    nextCursor=None)
            async def call_tool(self, name, arguments):
                calls.append((name, copy.deepcopy(arguments)))
                if mutate and name == 'codex_capabilities':
                    config.write_bytes(b'changed during reply')
                if lost and name == 'laf_router_submit_job':
                    raise TimeoutError('synthetic lost reply')
                if hang and name == 'laf_router_submit_job':
                    await asyncio.Event().wait()
                body = ({'status': 'ready'} if name == 'laf_router_status' else
                        {'models': ['flash']} if name == 'laf_router_models' else {'status': 'OBSERVED'})
                return SimpleNamespace(isError=False, structuredContent=body)
        @asynccontextmanager
        async def stdio(params):
            flags['parameters'] = params
            try:
                yield None, None
            finally:
                flags['transport_closed'] = True
        mcp = ModuleType('mcp')
        mcp.ClientSession = Session
        mcp.StdioServerParameters = lambda **kw: kw
        transport = ModuleType('mcp.client.stdio')
        transport.stdio_client = stdio
        args = SimpleNamespace(home=home, installed_root=self.base / 'installed',
            archive=self.base / 'archive.zip', journal=self.base / 'sdk-journal', action='inspect',
            model='flash', workspace='default', effort='max')
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {'mcp': mcp,
                'mcp.client': ModuleType('mcp.client'), 'mcp.client.stdio': transport}))
            stack.enter_context(patch.object(a.importlib.metadata, 'version', return_value='1.30.0'))
            stack.enter_context(patch.object(a, 'verify_package', return_value={'archive_sha256': 'synthetic'}))
            stack.enter_context(patch.object(a, 'verify_runtime', return_value=ROOT / 'runtime'))
            stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('No SDK child')))
            yield args, calls, flags

    def test_exercise_sdk_protocol_path_closes_both_contexts(self):
        with self.sdk_peer() as (args, calls, flags):
            result = asyncio.run(a.exercise(args))
        self.assertFalse(result['execution_accepted'])
        self.assertEqual([name for name, _ in calls], ['laf_router_status', 'codex_capabilities'])
        self.assertTrue(flags['session_closed'] and flags['transport_closed'])
        self.assertEqual(flags['parameters']['args'], ['-B', './launcher.py'])
        self.assertEqual(flags['parameters']['env']['HOME'], str(args.home))
        self.assertEqual(flags['timeout_seconds'], 120)

    def test_exercise_mutation_during_final_reply_fails_and_closes(self):
        with self.sdk_peer(mutate=True) as (args, calls, flags):
            with self.assertRaisesRegex(a.Refused, 'configuration_changed'):
                asyncio.run(a.exercise(args))
        self.assertTrue(flags['session_closed'] and flags['transport_closed'])

    def test_exercise_lost_submit_keeps_journal_and_reaps_client_contexts(self):
        with self.sdk_peer(lost=True) as (args, calls, flags):
            args.action = 'local'
            with self.assertRaises(TimeoutError):
                asyncio.run(a.exercise(args))
        self.assertTrue(flags['session_closed'] and flags['transport_closed'])
        rows = [a.decode(row) for row in args.journal.read_bytes().splitlines()]
        self.assertEqual(sum(row.get('kind') == 'intent' and row.get('label') == 'local'
                             for row in rows), 1)
        self.assertNotIn('laf_router_cancel_job', [name for name, _ in calls])

    def test_exercise_deadline_keeps_intent_and_closes_sdk_contexts(self):
        real_timeout = asyncio.timeout
        with self.sdk_peer(hang=True) as (args, calls, flags):
            args.action = 'local'
            with patch.object(a.asyncio, 'timeout', side_effect=lambda _: real_timeout(.01)):
                with self.assertRaises(TimeoutError):
                    asyncio.run(a.exercise(args))
        self.assertTrue(flags['session_closed'] and flags['transport_closed'])
        rows = [a.decode(row) for row in args.journal.read_bytes().splitlines()]
        self.assertEqual(sum(row.get('kind') == 'intent' and row.get('label') == 'local'
                             for row in rows), 1)
        self.assertNotIn('laf_router_cancel_job', [name for name, _ in calls])

    def test_readonly_admission_reports_normal_host_refusal_without_execution(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(boot, 'safe_path', return_value=self.base))
            stack.enter_context(patch.object(boot, 'doctor', return_value={'bindings': {'router': 'unconfigured'}}))
            stack.enter_context(patch('backend_state.load_state', return_value=({'codex': {}}, b'')))
            stack.enter_context(patch('isolation_guard.environment', return_value={'state': 'SANDBOXED'}))
            stack.enter_context(patch('codex_host.HostGateway.require_host',
                side_effect=PipelineError('NORMAL_HOST_CONTEXT_NOT_CONFIRMED')))
            stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('No process')))
            stack.enter_context(patch('socket.socket', side_effect=AssertionError('No service')))
            result = boot.admission(self.base)
        self.assertEqual(result['codex']['code'], 'NORMAL_HOST_CONTEXT_NOT_CONFIRMED')
        self.assertFalse(result['codex']['dispatch_attempted'])
        self.assertFalse(result['execution_ready'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
