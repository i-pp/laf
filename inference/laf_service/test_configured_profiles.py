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

# Added 2026-11-11: normal configured catalogs and explicit Flash policy.

import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from . import core_backends
from .core import Controller, GIB, Job, ServiceError, validate_request
from .core_server import load_config, run_core
from .server import Handler
from .test_acceptance_profiles import CandidateLifecycle, profiles as acceptance, candidate as old_candidate
from .test_core_integration import make


def profiles():
    options = {'num_ctx': 8192, 'num_thread': 2, 'num_batch': 32,
               'num_gpu': 32, 'temperature': 0, 'seed': 42}
    return [{'id': 'qwen-coder-small', 'reference': 'qwen2.5-coder:1.5b-instruct-q4_K_M',
             'digest': 'sha256:' + 'a' * 64, 'options': dict(options)},
            {'id': 'qwen-coder-next-size', 'reference': 'qwen2.5-coder:7b-instruct-q4_K_M',
             'digest': 'sha256:' + 'b' * 64, 'options': dict(options)}]


def selection(flash=False):
    return {'ollama_profiles': profiles(), 'flash_enabled': flash, 'default_model': 'qwen-coder-small'}


class ConfiguredAPI:
    def __init__(self):
        self.calls, self.loaded = [], None
        self.tags = [{'name': p['reference'], 'digest': p['digest']} for p in profiles()]
    def __call__(self, path, body=None, **kwargs):
        self.calls.append((path, copy.deepcopy(body)))
        if path == '/api/tags': return {'models': copy.deepcopy(self.tags)}
        if path == '/api/ps': return {'models': [] if self.loaded is None else [copy.deepcopy(self.loaded)]}
        if path == '/api/generate': self.loaded = None;return {'done': True}
        if path == '/api/chat':
            self.loaded = next(copy.deepcopy(p) for p in self.tags if p['name'] == body['model'])
            return {'done': True, 'done_reason': 'stop', 'message': {'content': 'synthetic reply'}}
        raise AssertionError(path)


def configured(flash=False):
    client, api = CandidateLifecycle(), ConfiguredAPI()
    backend = core_backends.CoreBackends(selection(flash), '/config', client,
                namespace_probe=lambda: 'pid:[222]', process_factory=Mock(side_effect=AssertionError('unexpected Flash')))
    backend.api = api
    return backend, client, api


def job(model='qwen-coder-small'):
    return Job(model, [{'role': 'user', 'content': 'hello'}], 8, time.monotonic() + 5)


class ConfiguredProfileTests(unittest.TestCase):
    def test_normal_profiles_pin_reference_manifest_and_options(self):
        source = profiles();parsed = core_backends.configured_profiles(source)
        source[0]['options']['num_gpu'] = 0
        self.assertEqual(parsed['qwen-coder-small']['digest'], 'sha256:' + 'a' * 64)
        self.assertEqual(parsed['qwen-coder-small']['options']['num_gpu'], 32)

    def test_normal_profiles_reject_legacy_acceptance_ids_and_unpinned_manifests(self):
        for key in ('flash', 'coder-next', 'gpt-oss-120b', 'test-small', '../small', 'Small', ''):
            value = profiles();value[0]['id'] = key
            with self.subTest(key=key), self.assertRaises(ServiceError):
                core_backends.configured_profiles(value)
        for digest in (None, 'a' * 64, 'sha256:' + 'A' * 64, 'sha256:' + 'a' * 64 + '\n'):
            value = profiles();value[0]['digest'] = digest
            with self.subTest(digest=digest), self.assertRaises(ServiceError):
                core_backends.configured_profiles(value)

    def test_explicit_flash_policy_catalog_and_default(self):
        for flash in (False, True):
            backend, _, _ = configured(flash)
            expected = {'qwen-coder-small', 'qwen-coder-next-size'} | ({'flash'} if flash else set())
            with self.subTest(flash=flash):
                self.assertEqual(backend.models, expected)
                self.assertEqual(backend.requires_flash, flash)
                self.assertEqual(backend.default_model, 'qwen-coder-small')
                self.assertEqual(backend.names['qwen-coder-small'], profiles()[0]['reference'])
                for key in expected:
                    validate_request({'model': key, 'messages': [{'role': 'user', 'content': 'hello'}]}, backend.models)

    def test_legacy_and_acceptance_modes_remain_separate(self):
        backend, _ = make();self.assertEqual(backend.models, {'flash', 'coder-next', 'gpt-oss-120b'})
        self.assertTrue(backend.requires_flash);self.assertEqual(backend.default_model, 'gpt-oss-120b')
        candidate, _, _ = old_candidate();self.assertFalse(candidate.requires_flash)
        self.assertEqual(candidate.models, {p['id'] for p in acceptance()})
        self.assertIsNone(candidate.default_model)

    def test_invalid_selection_rejected_before_host_observation(self):
        bad = []
        for key, value in [('flash_enabled', 0), ('flash_enabled', None), ('default_model', 'flash'),
                           ('default_model', 'unknown'), ('default_model', ['qwen-coder-small'])]:
            config = selection();config[key] = value;bad.append(config)
        config = selection();config['acceptance_ollama_profiles'] = acceptance();bad.append(config)
        for key in ('flash_enabled', 'default_model'):
            config = selection();config.pop(key);bad.append(config)
        for config in bad:
            client = Mock()
            with self.subTest(config=config), self.assertRaises(ServiceError):
                core_backends.CoreBackends(config, '/unused', client)
            client.status.assert_not_called()

    def test_flash_disabled_config_needs_no_strata_paths_and_rejects_mixed_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);path = root / 'config.json'
            value = dict(selection(), state_dir=str(root / 'state'), runtime_dir=str(root / 'runtime'),
                         lifecycle_runtime_dir=str(root / 'lifecycle'))
            path.write_text(json.dumps(value));path.chmod(0o600)
            self.assertEqual(load_config(path), value)
            for key, extra in [('strata_source', '/unexpected'), ('acceptance_ollama_profiles', acceptance())]:
                invalid = dict(value, **{key: extra});path.write_text(json.dumps(invalid))
                with self.subTest(key=key), self.assertRaises(ServiceError):load_config(path)
            value['flash_enabled'] = True;path.write_text(json.dumps(value))
            with self.assertRaises(ServiceError):load_config(path)
            value.update(strata_source='/strata', tokenizer_python='/venv/bin/python', strata_command=['/strata/bin/strata'])
            path.write_text(json.dumps(value));self.assertEqual(load_config(path), value)

    def test_pinned_inventory_rejects_missing_wrong_or_duplicate_manifest(self):
        for case in ('missing', 'wrong', 'duplicate', 'malformed'):
            backend, _, api = configured();backend.start_ollama()
            if case == 'missing':api.tags.pop()
            elif case == 'wrong':api.tags[0]['digest'] = 'sha256:' + 'c' * 64
            elif case == 'duplicate':api.tags.append(dict(api.tags[0]))
            else:api.tags[0]['digest'] = None
            with self.subTest(case=case), self.assertRaises(ServiceError):backend.verify_models()
            self.assertFalse(backend.models_verified)
            self.assertFalse(any(path == '/api/chat' for path, _ in api.calls))

    def start(self, backend):
        backend.start_ollama();backend.verify_models()
        with patch.object(core_backends.time, 'sleep'):backend.capture_idle_baseline(lambda: None)

    def test_flash_free_generation_switching_and_readiness(self):
        backend, client, api = configured();self.start(backend)
        self.assertTrue(backend.health()['ready'])
        with patch.object(core_backends.time, 'sleep'):
            for key in ('qwen-coder-small', 'qwen-coder-next-size', 'qwen-coder-small'):
                result = backend.generate(job(key), lambda: None)
                self.assertEqual(result['content'], 'synthetic reply')
        backend.process_factory.assert_not_called()
        self.assertFalse(backend.health()['flash_resident'])
        calls = [body for path, body in api.calls if path == '/api/chat']
        self.assertEqual([body['model'] for body in calls], [profiles()[0]['reference'], profiles()[1]['reference'], profiles()[0]['reference']])
        self.assertTrue(all(body['options']['num_ctx'] == 8192 and body['think'] is False for body in calls))
        self.assertEqual(client.calls.count('ensure'), 4)

    def test_manifest_changed_before_generation_never_loads(self):
        backend, _, api = configured();self.start(backend)
        api.tags[0]['digest'] = 'sha256:' + 'c' * 64
        with self.assertRaises(ServiceError):backend.generate(job(), lambda: None)
        self.assertFalse(any(path == '/api/chat' for path, _ in api.calls))
        self.assertFalse(backend.health()['ready'])

    def test_loaded_manifest_mismatch_rejects_result_and_readiness(self):
        backend, _, api = configured();self.start(backend)
        original = backend.api
        def wrong_loaded(path, body=None, **kwargs):
            value = original(path, body, **kwargs)
            if path == '/api/ps' and value['models']:value['models'][0]['digest'] = 'sha256:' + 'c' * 64
            return value
        backend.api = wrong_loaded
        with self.assertRaises(ServiceError):backend.generate(job(), lambda: None)
        self.assertFalse(backend.models_verified)

    def test_optional_flash_uses_configured_small_options_not_legacy_options(self):
        backend, _, api = configured(True)
        backend.start_flash = Mock();backend.flash_alive = lambda: True
        backend.flash_base = dict(backend.read_resources())
        backend.start_ollama();backend.verify_models()
        backend.generate(job(), lambda: None)
        body = next(body for path, body in api.calls if path == '/api/chat')
        self.assertEqual(body['options']['num_gpu'], 32)
        self.assertEqual(body['model'], profiles()[0]['reference'])
        backend.start_flash.assert_called_once()

    def test_disabled_flash_and_unknown_models_fail_before_calls(self):
        backend, client, api = configured()
        with self.assertRaises(ServiceError):backend.generate(job('flash'), lambda: None)
        with self.assertRaises(ServiceError):backend.start_flash()
        self.assertEqual(api.calls, []);self.assertEqual(client.calls, [])

    def test_router_catalog_publishes_selected_default_and_flash_policy(self):
        backend, client, _ = configured()
        ctl = Controller(backend, client.read_resources, start=False)
        handler = object.__new__(Handler)
        handler.guard = lambda: None
        handler.server = type('Server', (), {'controller': ctl, 'config': {}})()
        results = [];handler.response = lambda code, value: results.append((code, value))
        for path in ('/v1/models', '/v1/capabilities'):
            handler.path = path;handler.do_GET()
        catalog = results[0][1];caps = results[1][1]['gpu']
        self.assertEqual(catalog['default_model'], 'qwen-coder-small')
        self.assertFalse(catalog['flash_enabled'])
        self.assertEqual(catalog['models'], sorted(backend.models))
        self.assertEqual(caps['default_model'], catalog['default_model'])
        self.assertFalse(caps['flash_enabled'])

    def test_malformed_empty_inventory_cannot_publish_idle_baseline_or_switch(self):
        for value in (None, {}, False, '', {'not_models': []}, {'models': None},
                      {'models': {}}, {'models': False}, {'models': ''}, {'models': [None]}):
            backend, _, api = configured();backend.start_ollama();backend.verify_models()
            original = backend.api
            backend.api = lambda path, body=None, **kw: value if path == '/api/ps' else original(path, body, **kw)
            with self.subTest(value=value), patch.object(core_backends.time, 'sleep'):
                with self.assertRaises(ServiceError):backend.capture_idle_baseline(lambda: None)
                self.assertIsNone(backend.idle_base)
                backend.active_model = 'qwen-coder-small'
                backend.idle_base = dict(backend.read_resources())
                with self.assertRaises(ServiceError):backend.unload(lambda: None)
                self.assertEqual(backend.active_model, 'qwen-coder-small')
                self.assertFalse(any(path == '/api/chat' for path, _ in api.calls))

    def test_same_model_cancelled_during_inventory_never_sends_generation(self):
        backend, _, api = configured();self.start(backend)
        backend.active_model = 'qwen-coder-small'
        api.loaded = dict(api.tags[0]);cancelled = [False]
        original = backend.api
        def inventory_cancels(path, body=None, **kwargs):
            value = original(path, body, **kwargs)
            if path == '/api/tags':cancelled[0] = True
            return value
        def check():
            if cancelled[0]:raise ServiceError('cancelled fixture')
        backend.api = inventory_cancels
        with self.assertRaises(ServiceError):backend.generate(job(), check)
        self.assertFalse(any(path == '/api/chat' for path, _ in api.calls))

    def test_generation_fence_changed_during_inventory_never_sends_generation(self):
        backend, _, api = configured();self.start(backend)
        backend.active_model = 'qwen-coder-small';api.loaded = dict(api.tags[0])
        original = backend.api
        def changed_generation(path, body=None, **kwargs):
            value = original(path, body, **kwargs)
            if path == '/api/tags':backend.generation += 1
            return value
        backend.api = changed_generation
        with self.assertRaises(ServiceError):backend.generate(job(), lambda: None)
        self.assertFalse(any(path == '/api/chat' for path, _ in api.calls))

    def test_normal_profile_options_and_shape_do_not_expand_authority(self):
        cases = [None, {}, [], profiles() * 5]
        for key, value in [('engine_url', 'http://unapproved'), ('command', ['sh'])]:
            entries = profiles();entries[0][key] = value;cases.append(entries)
        for key, value in [('num_ctx', 4096), ('num_thread', True), ('num_gpu', -1),
                           ('num_predict', 5), ('temperature', float('nan')), ('temperature', float('inf'))]:
            entries = profiles();entries[0]['options'][key] = value;cases.append(entries)
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(ServiceError):core_backends.configured_profiles(entries)
        entries = profiles();entries[1]['reference'] = entries[0]['reference']
        with self.assertRaises(ServiceError):core_backends.configured_profiles(entries)
        value = selection(True);value['default_model'] = 'flash'
        backend = core_backends.CoreBackends(value, '/unused', CandidateLifecycle(), namespace_probe=lambda: 'pid:[222]')
        self.assertEqual(backend.default_model, 'flash')

    def test_raw_engine_digest_normalizes_without_changing_configured_identity(self):
        backend, _, api = configured()
        for entry in api.tags:entry['digest'] = entry['digest'].removeprefix('sha256:')
        self.start(backend)
        self.assertEqual(backend.generate(job(), lambda: None)['content'], 'synthetic reply')

    def test_mixed_mode_after_abort_flash_request_restores_catalog_readiness(self):
        backend, client, api = configured(True)
        backend.start_ollama();backend.verify_models();backend.abort()
        client.calls.clear();api.calls.clear()
        backend.start_flash = Mock();backend.flash_alive = lambda: True
        backend.flash = type('Flash', (), {'stdin': Mock()})()
        backend.read_flash = Mock(return_value={'content': 'synthetic Flash'})
        self.assertEqual(backend.generate(job('flash'), lambda: None)['content'], 'synthetic Flash')
        self.assertTrue(backend.health()['ready'])
        self.assertEqual(client.calls, ['ensure'])
        self.assertEqual([path for path, _ in api.calls], ['/api/tags'])
        backend.flash.stdin.write.assert_called_once()

    def test_mixed_flash_input_is_blocked_by_manifest_failure_or_cancel(self):
        for case in ('manifest', 'cancel'):
            backend, _, api = configured(True)
            backend.start_flash = Mock();backend.flash_alive = lambda: True
            backend.flash = type('Flash', (), {'stdin': Mock()})()
            backend.read_flash = Mock(return_value={'content': 'must not return'})
            cancelled = [False];original = backend.api
            if case == 'manifest':api.tags[0]['digest'] = 'sha256:' + 'c' * 64
            def lookup(path, body=None, **kwargs):
                value = original(path, body, **kwargs)
                if case == 'cancel' and path == '/api/tags':cancelled[0] = True
                return value
            def check():
                if cancelled[0]:raise ServiceError('cancelled fixture')
            backend.api = lookup
            with self.subTest(case=case), self.assertRaises(ServiceError):backend.generate(job('flash'), check)
            backend.start_flash.assert_not_called();backend.flash.stdin.write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
