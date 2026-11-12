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

"""Combined catalog/MCP regression tests, not live UDS or hardware acceptance."""
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from laf_mcp.application import Application
from laf_mcp.receipts import Receipts
from integrated_router_fixture import ConfiguredFixture, HandlerBridge, ROOT
from inference.laf_service.core import Controller
from inference.laf_service.test_core_integration import make as legacy_backend

SMALL = 'qwen-coder-small'
NEXT = 'qwen-coder-next-size'
PROMPT = 'SYNTHETIC_INTEGRATION_PROMPT'


def submission(model=SMALL, attempt='1' * 32, **extra):
    return dict(attempt_id=attempt, model=model,
                messages=[{'role': 'user', 'content': PROMPT}], **extra)


class IntegratedApplicationTests(unittest.TestCase):
    def setUp(self):
        self.no_socket = patch.object(socket, 'socket', side_effect=AssertionError(
            'Application integration must never open a socket'))
        self.no_socket.start()
        self.addCleanup(self.no_socket.stop)
        self.fixture = ConfiguredFixture()
        self.addCleanup(self.fixture.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.receipts = Receipts(Path(self.tmp.name) / 'receipts')
        self.addCleanup(lambda: self.receipts.close())
        self.app = Application(self.fixture.router, self.receipts, {SMALL, NEXT})

    def posts(self):
        return [call for call in self.fixture.router.calls if call[0] == 'POST']

    def test_actual_catalog_metadata_and_exact_allowlist(self):
        self.app.allowed = frozenset({NEXT})
        result = self.app.call('laf_router_models', {})
        self.assertEqual(result['outcome'], 'OBSERVED')
        self.assertEqual(result['models'], [NEXT])
        self.assertTrue(result['explicit_model_required'])
        raw = self.fixture.router.observations[-1][3]
        self.assertEqual(raw['default_model'], SMALL)
        self.assertIs(raw['flash_enabled'], False)
        self.assertEqual(set(raw['models']), {SMALL, NEXT})
        # The adapter validates metadata but never turns the default into
        # implicit permission, nor exposes a filtered-out default as selectable.
        self.assertNotIn('default_model', result)
        self.assertNotIn('flash', result['models'])

    def test_no_default_or_flash_fallback(self):
        self.app.allowed = frozenset({NEXT, 'flash', 'absent-model'})
        cases = [submission(SMALL), submission('flash', '2' * 32),
                 submission('absent-model', '3' * 32)]
        missing = submission(attempt='4' * 32)
        missing.pop('model')
        cases.append(missing)
        for args in cases:
            with self.subTest(model=args.get('model')):
                self.assertEqual(self.app.call('laf_router_submit_job', args)['outcome'],
                                 'NOT_SUBMITTED')
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.fixture.api.calls, [])
        self.assertEqual(self.fixture.controller.jobs, {})

    def test_nondefault_job_keeps_exact_model_and_provider_reference(self):
        admitted = self.app.call('laf_router_submit_job', submission(NEXT))
        self.assertEqual(admitted['outcome'], 'ADMITTED')
        self.assertEqual(admitted['status'], 'queued')
        self.assertEqual(admitted['model'], NEXT)
        job = self.fixture.controller.jobs[admitted['job_id']]
        self.assertFalse(job.done.is_set())
        self.fixture.start()
        self.assertTrue(job.done.wait(5))
        result = self.app.call('laf_router_get_job', {'handle': admitted['handle']})
        self.assertEqual(result['outcome'], 'TERMINAL')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['content'], 'synthetic reply')
        self.assertEqual(result['job_id'], job.id)
        self.assertEqual(result['model'], NEXT)
        self.assertTrue(result['unverified_output'])
        self.assertFalse(result['code_executed'])
        calls = [body for path, body in self.fixture.api.calls if path == '/api/chat']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['model'], self.fixture.backend.names[NEXT])
        self.assertEqual(calls[0]['options']['num_gpu'], 32)
        self.assertIs(calls[0]['think'], False)
        self.assertEqual(job.messages, [])

    def test_attempt_resume_canonical_defaults_and_conflict_never_repost(self):
        first = self.app.call('laf_router_submit_job', submission())
        again = self.app.call('laf_router_submit_job',
                              submission(max_tokens=256, timeout_seconds=180))
        self.assertEqual(first['job_id'], again['job_id'])
        self.assertTrue(again['resumed_existing_attempt'])
        self.receipts.close()
        self.receipts = Receipts(Path(self.tmp.name) / 'receipts')
        self.app = Application(self.fixture.router, self.receipts, {SMALL, NEXT})
        reopened = self.app.call('laf_router_submit_job', submission())
        self.assertEqual(reopened['handle'], first['handle'])
        self.assertEqual(reopened['job_id'], first['job_id'])
        conflict = self.app.call('laf_router_submit_job', submission(NEXT))
        self.assertEqual(conflict['error'], 'attempt_conflict')
        self.assertEqual(conflict['model'], SMALL)
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(len(self.fixture.controller.jobs), 1)
        self.assertNotIn('attempt_id', self.posts()[0][2])
        self.assertNotIn('request_hash', self.posts()[0][2])
        record = self.receipts.load(first['handle'])
        public = json.dumps([first, again, reopened, conflict])
        self.assertNotIn(record['request_hash'], public)
        self.assertNotIn(PROMPT, public)
        disk = ''.join(p.read_text() for p in Path(self.tmp.name).rglob('*.json'))
        self.assertNotIn(PROMPT, disk)
        self.assertNotIn('synthetic reply', disk)

    def test_real_readiness_changes_remain_nonatomic_and_unattested(self):
        before = self.app.call('laf_router_status', {})
        self.assertEqual(before['status'], 'not_ready')
        self.fixture.backend.start_ollama()
        self.fixture.backend.verify_models()
        self.fixture.backend.capture_idle_baseline(lambda: None)
        ready = self.app.call('laf_router_status', {})
        self.assertEqual(ready['status'], 'ready')
        self.fixture.controller.trip('SYNTHETIC_PRIVATE_FAULT')
        faulted = self.app.call('laf_router_status', {})
        self.assertEqual(faulted['status'], 'not_ready')
        for result in (before, ready, faulted):
            self.assertEqual(result['outcome'], 'OBSERVED')
            for flag in ('atomic', 'hardware_attested', 'npu_readiness_verified',
                         'npu_configured', 'automatic_retry'):
                self.assertIs(result[flag], False)
            self.assertNotIn('SYNTHETIC_PRIVATE_FAULT', json.dumps(result))

    def test_queued_get_and_cancel_keep_owned_identity_without_physical_claim(self):
        admitted = self.app.call('laf_router_submit_job', submission())
        handle = admitted['handle']
        observed = self.app.call('laf_router_get_job', {'handle': handle})
        self.assertEqual(observed['status'], 'queued')
        self.assertEqual(observed['outcome'], 'ADMITTED')
        self.assertEqual(observed['job_id'], admitted['job_id'])
        cancelled = self.app.call('laf_router_cancel_job', {'handle': handle})
        repeated = self.app.call('laf_router_cancel_job', {'handle': handle})
        self.assertEqual(cancelled['cancel_outcome'], 'REQUESTED')
        self.assertFalse(cancelled['physical_stop_confirmed'])
        self.assertEqual(cancelled, repeated)
        self.assertEqual(cancelled['status'], 'queued')
        calls = [c for c in self.fixture.router.calls if c[0] == 'DELETE']
        self.assertEqual(calls, [('DELETE', '/v1/jobs/' + admitted['job_id'], None)])
        job = self.fixture.controller.jobs[admitted['job_id']]
        self.assertTrue(job.cancel.is_set())
        self.fixture.start()
        self.assertTrue(job.done.wait(5))
        final = self.app.call('laf_router_get_job', {'handle': handle})
        self.assertEqual(final['status'], 'cancelled')
        self.assertEqual(final['outcome'], 'TERMINAL')
        self.assertFalse(any(p == '/api/chat' for p, _ in self.fixture.api.calls))
        self.assertFalse(final['physical_stop_confirmed'])

    def test_foreign_handle_never_reaches_real_job_routes(self):
        self.app.call('laf_router_submit_job', submission())
        count = len(self.fixture.router.calls)
        for name in ('laf_router_get_job', 'laf_router_cancel_job'):
            result = self.app.call(name, {'handle': '9' * 32})
            self.assertEqual(result['outcome'], 'UNAVAILABLE')
        self.assertEqual(len(self.fixture.router.calls), count)

    def test_manifest_drift_fails_admitted_job_without_fallback(self):
        admitted = self.app.call('laf_router_submit_job', submission())
        self.fixture.api.tags[0]['digest'] = 'sha256:' + 'f' * 64
        self.fixture.start()
        job = self.fixture.controller.jobs[admitted['job_id']]
        self.assertTrue(job.done.wait(5))
        result = self.app.call('laf_router_get_job', {'handle': admitted['handle']})
        self.assertEqual(result['outcome'], 'TERMINAL')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['model'], SMALL)
        self.assertFalse(any(p == '/api/chat' for p, _ in self.fixture.api.calls))
        self.assertNotIn('manifest', json.dumps(result))

    def test_mismatched_real_job_model_is_unknown(self):
        admitted = self.app.call('laf_router_submit_job', submission())
        job = self.fixture.controller.jobs[admitted['job_id']]
        job.model = NEXT  # Simulate corrupted Router identity, not a legitimate switch.
        result = self.app.call('laf_router_get_job', {'handle': admitted['handle']})
        self.assertEqual(result['outcome'], 'UNKNOWN')
        self.assertEqual(result['model'], SMALL)
        self.assertEqual(result['job_id'], job.id)

    def test_legacy_catalog_and_absent_optional_metadata_remain_accepted(self):
        backend, lifecycle = legacy_backend()
        controller = Controller(backend, lifecycle.read_resources, start=False)
        self.addCleanup(controller.close)
        bridge = HandlerBridge(controller)
        app = Application(bridge, self.receipts, {'flash', 'coder-next', 'gpt-oss-120b'})
        modern = app.call('laf_router_models', {})
        self.assertEqual(modern['models'], ['coder-next', 'flash', 'gpt-oss-120b'])
        raw = bridge.observations[-1][3]
        self.assertEqual(raw['default_model'], 'gpt-oss-120b')
        self.assertIs(raw['flash_enabled'], True)
        original = bridge.request
        def old_catalog(method, path, body=None):
            code, result = original(method, path, body)
            if path == '/v1/models':
                result.pop('default_model')
                result.pop('flash_enabled')
            return code, result
        bridge.request = old_catalog
        self.assertEqual(app.call('laf_router_models', {}), modern)
        admitted = app.call('laf_router_submit_job', submission('coder-next'))
        self.assertEqual(admitted['outcome'], 'ADMITTED')
        self.assertEqual(controller.jobs[admitted['job_id']].model, 'coder-next')


class IntegratedSDKTests(unittest.TestCase):
    # Modified 2026-11-11: distinguish real selected-SDK evidence from historical tests.
    def setUp(self):
        self.assertEqual(importlib.metadata.version('mcp'), '1.30.0',
                         'Real integration checks require actual MCP SDK 1.30.0')

    def run_session(self, queued):
        import anyio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def session_run(tmp):
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'mcp/src'),
                       PYTHONDONTWRITEBYTECODE='1', LAF_MCP_STATE_DIR=str(tmp / 'receipts'),
                       LAF_TEST_QUEUED='1' if queued else '0')
            params = StdioServerParameters(command=sys.executable,
                args=['-B', str(ROOT / 'mcp/tests/integrated_router_fixture.py')],
                env=env, cwd=str(tmp))
            with (tmp / 'stderr').open('w+') as errors:
                with anyio.fail_after(15):
                    async with stdio_client(params, errlog=errors) as (read, write):
                        async with ClientSession(read, write) as client:
                            await client.initialize()
                            listed = await client.list_tools()
                            self.assertEqual({t.name for t in listed.tools}, {
                                'laf_router_models', 'laf_router_status', 'laf_router_submit_job',
                                'laf_router_get_job', 'laf_router_cancel_job'})
                            async def call(name, args):
                                result = await client.call_tool(name, args)
                                self.assertFalse(result.isError, result.structuredContent)
                                return result.structuredContent
                            models = await call('laf_router_models', {})
                            self.assertEqual(models['models'], sorted([SMALL, NEXT]))
                            self.assertTrue(models['explicit_model_required'])
                            status = await call('laf_router_status', {})
                            self.assertEqual(status['status'], 'not_ready')
                            self.assertFalse(status['atomic'])
                            self.assertFalse(status['hardware_attested'])
                            admitted = await call('laf_router_submit_job', submission(NEXT))
                            self.assertEqual(admitted['outcome'], 'ADMITTED')
                            self.assertEqual(admitted['model'], NEXT)
                            again = await call('laf_router_submit_job', submission(NEXT))
                            self.assertEqual(again['job_id'], admitted['job_id'])
                            self.assertTrue(again['resumed_existing_attempt'])
                            if queued:
                                observed = await call('laf_router_get_job', {'handle': admitted['handle']})
                                self.assertEqual(observed['status'], 'queued')
                            else:
                                for _ in range(150):
                                    observed = await call('laf_router_get_job', {'handle': admitted['handle']})
                                    if observed['outcome'] == 'TERMINAL':
                                        break
                                    await anyio.sleep(.02)
                                self.assertEqual(observed['status'], 'completed')
                                self.assertEqual(observed['content'], 'synthetic reply')
                                self.assertTrue(observed['unverified_output'])
                                self.assertFalse(observed['code_executed'])
                            self.assertEqual(observed['job_id'], admitted['job_id'])
                            self.assertEqual(observed['model'], NEXT)
                            cancelled = await call('laf_router_cancel_job', {'handle': admitted['handle']})
                            self.assertEqual(cancelled['cancel_outcome'], 'REQUESTED')
                            self.assertFalse(cancelled['physical_stop_confirmed'])
                            self.assertEqual(cancelled,
                                await call('laf_router_cancel_job', {'handle': admitted['handle']}))
                errors.seek(0)
                self.assertNotIn(PROMPT, errors.read())
            disk = ''.join(p.read_text() for p in tmp.rglob('*.json'))
            self.assertNotIn(PROMPT, disk)
            self.assertNotIn('synthetic reply', disk)

        with tempfile.TemporaryDirectory(prefix='laf integrated 雪 ') as tmp:
            anyio.run(session_run, Path(tmp))

    def test_actual_sdk_stdio_real_controller_configured_completion(self):
        self.run_session(queued=False)

    def test_actual_sdk_stdio_real_controller_queued_cancellation(self):
        self.run_session(queued=True)


if __name__ == '__main__':
    unittest.main()
