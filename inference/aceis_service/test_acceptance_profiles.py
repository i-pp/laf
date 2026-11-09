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

"""Offline candidate catalog regressions; no Docker, models, or real inference."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from .core import Controller, GIB, Job, MODELS, ServiceError, validate_request
from .core_backends import CoreBackends, NAMES, acceptance_profiles
from .core_server import load_config, run_core
from . import test_core_integration
from .test_core_integration import FakeLifecycle, make
from .test_service import body


def profiles():
    options = {'num_ctx': 8192, 'num_thread': 2, 'num_batch': 32,
               'num_gpu': 32, 'temperature': 0, 'seed': 42}
    return [{'id': 'test-qwen-small-1', 'reference': 'qwen2.5:0.5b', 'options': dict(options)},
            {'id': 'test-qwen-small-2', 'reference': 'qwen2.5:1.5b', 'options': dict(options)}]


def config(root):
    return {'state_dir': str(root / 'state'), 'runtime_dir': str(root / 'runtime'),
            'lifecycle_runtime_dir': str(root / 'host'), 'acceptance_ollama_profiles': profiles()}


class CandidateLifecycle(FakeLifecycle):
    def __init__(self):
        super().__init__()
        self.identity.update(running=False, stopping=False, fault=None)
    def ensure(self, check):
        result = super().ensure(check)
        self.identity['running'] = True
        return result
    def stop(self):
        super().stop(); self.identity['running'] = False


class FakeAPI:
    def __init__(self): self.calls, self.loaded = [], None
    def __call__(self, path, body=None, **kwargs):
        self.calls.append((path, copy.deepcopy(body)))
        if path == '/api/tags': return {'models': [{'name': p['reference']} for p in profiles()]}
        if path == '/api/ps': return {'models': [] if self.loaded is None else [{'name': self.loaded}]}
        if path == '/api/generate': self.loaded = None; return {'done': True}
        if path == '/api/chat':
            self.loaded = body['model']
            return {'done': True, 'done_reason': 'stop', 'message': {'content': 'fake candidate'}, 'eval_count': 3}
        raise AssertionError(path)


def candidate():
    client, api = CandidateLifecycle(), FakeAPI()
    backend = CoreBackends({'acceptance_ollama_profiles': profiles()}, '/candidate.json', client,
                           namespace_probe=lambda: 'pid:[222]', process_factory=Mock(side_effect=AssertionError('no Flash')))
    backend.api = api
    return backend, client, api


def job(model='test-qwen-small-1'):
    return Job(model, [{'role': 'user', 'content': 'hello'}], 8, time.monotonic() + 5)


class CatalogTest(unittest.TestCase):
    def test_default_constants_validation_and_profiles_unchanged(self):
        self.assertEqual(MODELS, {'flash', 'coder-next', 'gpt-oss-120b'})
        self.assertEqual(NAMES, {'coder-next': 'aceis-coder-next', 'gpt-oss-120b': 'aceis-gpt-oss-120b'})
        backend, _ = make()
        self.assertTrue(backend.requires_flash); self.assertEqual(backend.models, MODELS)
        for key in MODELS: validate_request(body(key))
        with self.assertRaises(ValueError): validate_request(body('test-qwen-small-1'))
        self.assertNotIn('ready', backend.health())

    def test_explicit_profiles_are_copied_and_do_not_modify_defaults(self):
        source = profiles(); result = acceptance_profiles(source)
        source[0]['options']['num_gpu'] = 0
        self.assertEqual(result['test-qwen-small-1']['options']['num_gpu'], 32)
        self.assertEqual(set(result), {p['id'] for p in profiles()})
        self.assertNotIn('test-qwen-small-1', MODELS)

    def test_catalog_rejects_invalid_shape_ids_references_and_aliases(self):
        invalid = [None, {}, [], [profiles()[0]] * 9]
        for field, values in {
            'id': ['flash', 'coder-next', 'gpt-oss-120b', 'test-', 'test-X', 'test-' + 'x' * 60, 1],
            'reference': ['qwen', 'https://host/model:tag', 'host:30000/model:tag', '../qwen:tag', 'qwen:bad tag', 1],
            'options': [{}, None, {'command': 'sh'}],
        }.items():
            for value in values:
                entries = profiles(); entries[0][field] = value; invalid.append(entries)
        entries = profiles(); entries[0]['engine_url'] = 'http://host'; invalid.append(entries)
        entries = profiles(); entries[1]['id'] = entries[0]['id']; invalid.append(entries)
        entries = profiles(); entries[1]['reference'] = entries[0]['reference']; invalid.append(entries)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ServiceError): acceptance_profiles(value)

    def test_catalog_rejects_option_type_and_bound_escapes(self):
        for key, values in {'num_ctx': [True, 4096, 8193], 'num_thread': [0, 33, 2.0],
                            'num_batch': [0, 513], 'num_gpu': [-1, 257, True],
                            'seed': [-1, 2147483648], 'temperature': [True, -1, 3, math.nan, math.inf]}.items():
            for value in values:
                entries = profiles(); entries[0]['options'][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ServiceError): acceptance_profiles(entries)
        entries = profiles(); entries[0]['options']['num_predict'] = 100
        with self.assertRaises(ServiceError): acceptance_profiles(entries)

    def test_private_candidate_config_excludes_flash_and_host_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'core.json'; value = config(root)
            path.write_text(json.dumps(value)); path.chmod(0o600)
            self.assertEqual(load_config(path), value)
            for key in ['strata_source', 'strata_command', 'tokenizer_python', 'generation_lock',
                        'engine_url', 'docker_network', 'container_name', 'weights']:
                path.write_text(json.dumps({**value, key: '/authority'}))
                with self.subTest(key=key), self.assertRaises(ServiceError): load_config(path)
            path.write_text(json.dumps(value)); path.chmod(0o644)
            with self.assertRaises(ServiceError): load_config(path)

    def test_default_config_still_requires_all_strata_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'core.json'
            value = test_core_integration.ConfigPackagingTest().config(root)
            path.write_text(json.dumps(value)); path.chmod(0o600)
            self.assertEqual(load_config(path), value)
            for key in ('strata_source', 'strata_command', 'tokenizer_python'):
                bad = dict(value); del bad[key]; path.write_text(json.dumps(bad))
                with self.subTest(key=key), self.assertRaises(ServiceError): load_config(path)

    def test_controller_accepts_only_its_catalog_and_no_public_options(self):
        backend, client, _ = candidate(); ctl = Controller(backend, client.read_resources, start=False)
        for key in backend.models: self.assertEqual(ctl.submit(body(key)).model, key)
        for key in list(MODELS) + ['qwen2.5:0.5b', 'test-unknown']:
            with self.subTest(key=key), self.assertRaises(ValueError): ctl.submit(body(key))
        for key in ['options', 'reference', 'engine_url', 'acceptance_ollama_profiles', 'keep_alive', 'think']:
            with self.subTest(key=key), self.assertRaises(ValueError): ctl.submit(body('test-qwen-small-1', **{key: {}}))


class OllamaOnlyTest(unittest.TestCase):
    def start(self, backend):
        backend.abort(); backend.start_ollama(); backend.verify_models()
        with patch('inference.aceis_service.core_backends.time.sleep'): backend.capture_idle_baseline(lambda: None)

    def test_start_switch_and_shutdown_never_launch_flash(self):
        backend, client, api = candidate(); self.start(backend)
        self.assertEqual(client.calls, ['stop', 'ensure'])
        self.assertIsNone(backend.flash_base); self.assertIsNotNone(backend.idle_base)
        self.assertTrue(backend.health()['ready']); self.assertFalse(backend.health()['flash_resident'])
        self.assertEqual(backend.generate(job(), lambda: None)['content'], 'fake candidate')
        before = client.samples
        with patch('inference.aceis_service.core_backends.time.sleep'):
            backend.generate(job('test-qwen-small-2'), lambda: None)
        self.assertEqual(client.samples - before, 2)
        requests = [body for path, body in api.calls if path == '/api/chat']
        self.assertEqual([r['model'] for r in requests], [p['reference'] for p in profiles()])
        self.assertEqual(requests[0]['options'], {**profiles()[0]['options'], 'num_predict': 8})
        self.assertFalse(requests[0]['think'])
        self.assertEqual([body for path, body in api.calls if path == '/api/generate'],
                         [{'model': 'qwen2.5:0.5b', 'keep_alive': 0}])
        backend.close(); self.assertEqual(client.calls[-1], 'stop')
        self.assertIsNone(backend.idle_base); self.assertFalse(backend.health()['ready'])
        backend.process_factory.assert_not_called()

    def test_start_flash_fails_closed_in_candidate_mode(self):
        backend, _, _ = candidate()
        with self.assertRaises(ServiceError): backend.start_flash()
        backend.process_factory.assert_not_called()

    def test_unknown_model_has_no_provider_side_effect(self):
        backend, client, api = candidate()
        with self.assertRaises(ServiceError): backend.generate(job('flash'), lambda: None)
        self.assertEqual(client.calls, []); self.assertEqual(api.calls, [])

    def test_missing_configured_reference_prevents_readiness(self):
        backend, _, _ = candidate(); backend.start_ollama()
        backend.api = lambda *args, **kwargs: {'models': [{'name': 'aceis-coder-next:latest'}]}
        with self.assertRaises(ServiceError): backend.verify_models()
        self.assertFalse(backend.health()['ready'])

    def test_readiness_rechecks_host_running_fault_and_epoch(self):
        for key, value in [('running', False), ('stopping', True), ('fault', 'safety fence'), ('epoch', 'b' * 32)]:
            backend, client, _ = candidate(); self.start(backend); client.identity[key] = value
            with self.subTest(key=key): self.assertFalse(backend.health()['ready'])

    def test_baseline_requires_empty_engine_and_absolute_host_idle(self):
        for case in ('loaded', 'vram', 'ram'):
            backend, client, api = candidate(); backend.start_ollama()
            if case == 'loaded': api.loaded = 'unowned:tag'
            if case == 'vram': client.resources['vram_used'] = 4 * GIB
            if case == 'ram': client.resources['mem_available'] = 7 * GIB
            with self.subTest(case=case), patch('inference.aceis_service.core_backends.time.monotonic', side_effect=[0, 0, 1, 31]), \
                 patch('inference.aceis_service.core_backends.time.sleep'):
                with self.assertRaises(ServiceError): backend.capture_idle_baseline(lambda: None)
            self.assertIsNone(backend.idle_base)

    def test_switch_rejects_empty_ps_without_physical_release(self):
        for key, value in [('vram_used', 4 * GIB), ('mem_available', 16 * GIB)]:
            backend, client, api = candidate(); self.start(backend)
            backend.active_model = 'test-qwen-small-1'; api.loaded = 'qwen2.5:0.5b'
            client.resources[key] = value
            with self.subTest(key=key), patch('inference.aceis_service.core_backends.time.monotonic', side_effect=[0, 0, 1, 31]), \
                 patch('inference.aceis_service.core_backends.time.sleep'):
                with self.assertRaises(ServiceError): backend.unload(lambda: None)
            self.assertEqual(backend.active_model, 'test-qwen-small-1')
            self.assertFalse(any(path == '/api/chat' for path, _ in api.calls))

    def test_missing_release_baseline_fails_before_unload_or_load(self):
        backend, _, api = candidate(); backend.active_model = 'test-qwen-small-1'
        with self.assertRaises(ServiceError): backend.unload(lambda: None)
        self.assertEqual(api.calls, [])

    def test_abort_then_new_generation_recaptures_idle_baseline(self):
        backend, client, api = candidate(); self.start(backend); backend.abort()
        before = client.samples
        with patch('inference.aceis_service.core_backends.time.sleep'): backend.generate(job(), lambda: None)
        self.assertEqual(client.samples - before, 2); self.assertTrue(backend.health()['ready'])
        backend.close()

    def test_baseline_capture_invalidated_by_abort_cannot_publish_ready(self):
        backend, client, api = candidate(); backend.start_ollama(); calls = []
        def check():
            calls.append(1)
            if len(calls) == 3: backend.abort()
        with patch('inference.aceis_service.core_backends.time.sleep'):
            with self.assertRaises(ServiceError): backend.capture_idle_baseline(check)
        self.assertIsNone(backend.idle_base); self.assertFalse(backend.health()['ready'])

    def test_wrong_single_loaded_reference_is_rejected(self):
        backend, client, api = candidate(); self.start(backend)
        def wrong(path, body=None, **kwargs):
            result = api(path, body, **kwargs)
            if path == '/api/ps': result = {'models': [{'name': 'wrong:tag'}]}
            return result
        backend.api = wrong
        with self.assertRaises(ServiceError): backend.generate(job(), lambda: None)

    def test_idle_controller_never_recovers_flash(self):
        backend, client, _ = candidate()
        backend.start_flash = Mock(side_effect=AssertionError('Flash recovery forbidden'))
        ctl = Controller(backend, client.read_resources)
        try:
            time.sleep(.45); self.assertIsNone(ctl.fault); self.assertEqual(ctl.restarts, [])
        finally: ctl.close()
        backend.start_flash.assert_not_called()


@unittest.skipUnless(os.environ.get('LAF_TEST_LIVE_UDS') == '1', 'requires permitted real UDS executor')
class CandidateEntryTest(unittest.TestCase):
    def test_real_entry_http_catalog_readiness_job_switch_and_shutdown(self):
        from .server import Server, UnixHTTPConnection
        client, api = CandidateLifecycle(), FakeAPI()
        created = []
        def factory(config, path, lifecycle):
            backend = CoreBackends(config, path, lifecycle, namespace_probe=lambda: 'pid:[222]',
                                   process_factory=Mock(side_effect=AssertionError('Flash forbidden')))
            backend.api = api; created.append(backend); return backend
        case = self
        class TestServer(Server):
            def serve_forever(self, poll_interval=.2):
                thread = threading.Thread(target=lambda: super(TestServer, self).serve_forever(poll_interval=.01), daemon=True)
                thread.start()
                def request(method, route, body=None):
                    conn = UnixHTTPConnection(self.server_address)
                    conn.request(method, route, None if body is None else json.dumps(body), {'Content-Type': 'application/json'})
                    response = conn.getresponse(); result = json.loads(response.read()); conn.close()
                    return response.status, result
                try:
                    status, health = request('GET', '/health')
                    case.assertEqual(status, 200); case.assertEqual(health['status'], 'ready')
                    case.assertFalse(health['gpu']['flash_resident'])
                    for route in ('/v1/models', '/v1/capabilities'):
                        status, result = request('GET', route)
                        observed = result['gpu']['models'] if route.endswith('capabilities') else result['models']
                        case.assertEqual(observed, [p['id'] for p in profiles()])
                    for key in ('flash', 'coder-next', 'gpt-oss-120b'):
                        case.assertEqual(request('POST', '/v1/jobs', body(key))[0], 400)
                    for entry in profiles():
                        status, result = request('POST', '/v1/chat/completions', body(entry['id']))
                        case.assertEqual(status, 200); case.assertEqual(result['model'], entry['id'])
                        case.assertEqual(result['result']['content'], 'fake candidate')
                    client.identity['running'] = False
                    case.assertEqual(request('GET', '/health')[0], 503)
                finally: self.shutdown(); thread.join(2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'core.json'
            path.write_text(json.dumps(config(root))); path.chmod(0o600)
            run_core(path, backend_factory=factory, client_factory=lambda config: client,
                     server_factory=TestServer, install_signals=False)
            self.assertFalse((root / 'runtime/service.sock').exists())
        self.assertEqual(client.calls[0], 'stop'); self.assertEqual(client.calls[-1], 'stop')
        self.assertIsNone(created[0].flash); created[0].process_factory.assert_not_called()


if __name__ == '__main__': unittest.main()
