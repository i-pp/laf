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

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from .core import GIB, Job, ServiceError
from .core_backends import CoreBackends, current_pid_namespace
from .core_server import load_config
from .lifecycle import HostLifecycle
from .lifecycle_client import LifecycleClient
from .test_lifecycle import Provider, sample
from .test_lifecycle_uds import Loopback


class FakeLifecycle:
    def __init__(self):
        self.calls, self.samples = [], 0
        self.endpoint = '172.31.0.2'
        self.identity = {'epoch': 'a' * 32, 'host_uid': os.getuid(),
                         'host_pid_namespace': 'pid:[111]', 'emergency_stop_supported': True}
        self.resources = sample()
    def status(self): return dict(self.identity)
    def read_resources(self): self.samples += 1; return dict(self.resources)
    def ensure(self, check): check(); self.calls.append('ensure'); return {'endpoint': self.endpoint}
    def stop(self): self.calls.append('stop')
    def panic(self): self.calls.append('panic')


def fake_process(args, **kwargs):
    return subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('fake_core_worker.py'))], **kwargs)


def make(client=None, **kwargs):
    client = client or FakeLifecycle()
    return CoreBackends({'tokenizer_python': sys.executable}, '/test/private/core.json', client,
                        namespace_probe=lambda: 'pid:[222]', **kwargs), client


class CoreBackendTest(unittest.TestCase):
    def test_requires_separate_namespace_mapped_uid_and_emergency_stop(self):
        for key, value in [('host_pid_namespace', current_pid_namespace()),
                           ('host_uid', os.getuid() + 1), ('emergency_stop_supported', False)]:
            client = FakeLifecycle(); client.identity[key] = value
            with self.subTest(key=key), self.assertRaises(ServiceError):
                CoreBackends({}, '/test/config', client)
    def test_namespace_change_never_sends_signal_and_requests_host_panic(self):
        backend, client = make(); backend.namespace_probe = lambda: 'pid:[333]'
        with patch('inference.aceis_service.core_backends.os.killpg') as kill:
            with self.assertRaises(ServiceError): backend.abort()
        kill.assert_not_called(); self.assertEqual(client.calls, ['panic']); self.assertTrue(backend.stopping)
    def test_fixed_private_endpoint_only(self):
        for endpoint in ['127.0.0.1', '169.254.169.254', '8.8.8.8', 'http://host/path', None, '::1']:
            backend, client = make(); client.endpoint = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaises(ServiceError): backend.start_ollama()
            self.assertIsNone(backend.address)
        backend, client = make(); backend.start_ollama(); self.assertEqual(backend.address, '172.31.0.2')
    def test_physical_samples_never_use_local_proc_fallback(self):
        backend, client = make()
        with patch('inference.aceis_service.core.snapshot', side_effect=AssertionError('local snapshot forbidden')), \
             patch('inference.aceis_service.core_backends.time.sleep'):
            backend.wait_idle()
        self.assertEqual(client.samples, 2)
    def test_empty_model_list_does_not_bypass_host_vram_or_ram_release(self):
        for key, value in [('vram_used', 40 * GIB), ('mem_available', 16 * GIB)]:
            backend, client = make(); backend.active_model = 'coder-next'; backend.flash_base = sample()
            client.resources[key] = value; backend.api = lambda *a, **k: {'models': []}
            with patch('inference.aceis_service.core_backends.time.monotonic', side_effect=[0, 0, 1, 31]), \
                 patch('inference.aceis_service.core_backends.time.sleep'):
                with self.assertRaises(ServiceError): backend.unload(lambda: None)
            self.assertEqual(backend.active_model, 'coder-next'); self.assertGreater(client.samples, 0)
    def test_unload_requires_two_stable_host_readings(self):
        backend, client = make(); backend.active_model = 'coder-next'; backend.flash_base = sample()
        backend.api = lambda *a, **k: {'models': []}
        with patch('inference.aceis_service.core_backends.time.sleep'): backend.unload(lambda: None)
        self.assertEqual(client.samples, 2); self.assertIsNone(backend.active_model)
    def test_pid_reuse_does_not_signal_unrelated_group(self):
        backend, client = make(); process = Mock(pid=42); backend.wrapper_start = 1
        backend.engine_pid, backend.engine_start = 43, 2
        reused = {'state': 'S', 'pgid': 42, 'sid': 42, 'start': 999, 'uids': {os.getuid()}}
        backend._process = lambda pid: reused if pid == 42 else None
        with patch('inference.aceis_service.core_backends.Path.glob', return_value=[Path('/proc/42/stat')]), \
             patch('inference.aceis_service.core_backends.os.killpg') as kill:
            with self.assertRaises(ServiceError): backend.stop_group(process)
        kill.assert_not_called()
    def test_unconfirmed_flash_stop_uses_host_panic_not_regular_stop(self):
        backend, client = make(); backend.flash = Mock(pid=42)
        backend.stop_group = Mock(side_effect=ServiceError('unknown ownership'))
        with self.assertRaises(ServiceError): backend.abort()
        self.assertEqual(client.calls, ['panic']); self.assertTrue(backend.stopping)
    def test_abort_invalidates_remote_ensure_without_waiting_for_start_lock(self):
        provider = Provider(); port = HostLifecycle(provider, sample, read_endpoint=lambda: '172.31.0.2', panic_callback=lambda reason: None)
        main, control = Loopback(port), Loopback(port, control=True)
        client = LifecycleClient(main.exchange(), control.exchange(), timeout=1, read_timeout=.2)
        backend, _ = make(client); backend.abort()
        entered, errors = threading.Event(), []
        def waiting(check):
            entered.set()
            while True: check(); time.sleep(.005)
        provider.ensure = waiting
        def start():
            try: backend.start_ollama()
            except Exception as exc: errors.append(exc)
        worker = threading.Thread(target=start); worker.start(); self.assertTrue(entered.wait(1))
        before = time.monotonic(); backend.abort(); worker.join(1)
        self.assertLess(time.monotonic() - before, .5); self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1); self.assertFalse(backend.stopping)


class FlashProcessTest(unittest.TestCase):
    def test_fake_flash_process_identity_generation_and_stop_barriers(self):
        backend, client = make(process_factory=fake_process)
        backend.wait_idle = lambda check=lambda: None: check()
        try:
            backend.start_flash(); self.assertTrue(backend.flash_alive())
            process, engine = backend.flash, backend.engine_pid
            result = backend.generate(Job('flash', [{'role': 'user', 'content': 'x'}], 8, time.monotonic() + 2), lambda: None)
            self.assertEqual(result['content'], 'fake Flash')
            def stop():
                client.calls.append('stop')
                self.assertFalse(backend._owned_group_alive(process))
                info = backend._process(engine); self.assertTrue(info is None or info['state'] == 'Z')
            client.stop = stop; backend.abort()
            self.assertIsNone(backend.flash); self.assertFalse(backend.flash_alive())
        finally:
            if backend.flash is not None: backend.abort()
    def test_live_engine_is_stopped_when_wrapper_has_already_exited(self):
        def exited(args, **kwargs):
            kwargs['env'] = {**kwargs['env'], 'LAF_TEST_WRAPPER_EXIT': '1'}
            return fake_process(args, **kwargs)
        backend, client = make(process_factory=exited); backend.wait_idle = lambda check=lambda: None: check()
        try:
            try: backend.start_flash()
            except ServiceError: pass
            process, engine = backend.flash, backend.engine_pid
            self.assertIsNotNone(engine); process.wait(timeout=2)
            backend.abort()
            info = backend._process(engine); self.assertTrue(info is None or info['state'] == 'Z')
            self.assertEqual(client.calls[-1], 'stop')
        finally:
            if backend.flash is not None: backend.abort()


class ConfigPackagingTest(unittest.TestCase):
    def config(self, root):
        return {'state_dir': str(root / 'state'), 'runtime_dir': str(root / 'runtime'),
                'lifecycle_runtime_dir': str(root / 'host'), 'strata_source': '/assets/strata',
                'tokenizer_python': '/usr/bin/python3', 'strata_command': ['/assets/strata/bin', '--serve']}
    def test_explicit_private_config_rejects_host_authority_fields(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); path = root / 'core.json'; config = self.config(root)
            path.write_text(json.dumps(config)); path.chmod(0o600); self.assertEqual(load_config(path), config)
            for key in ['generation_lock', 'docker_network', 'weights', 'container_name']:
                path.write_text(json.dumps({**config, key: '/host/path'}))
                with self.subTest(key=key), self.assertRaises(ServiceError): load_config(path)
            path.write_text(json.dumps(config)); path.chmod(0o644)
            with self.assertRaises(ServiceError): load_config(path)
    def test_core_allowlist_imports_without_host_docker_modules(self):
        import shutil
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / 'inference/core_container/Dockerfile').read_text()
        copied = [line.split()[1] for line in dockerfile.splitlines() if line.startswith('COPY ')]
        self.assertNotIn('inference/aceis_service/backends.py', copied)
        self.assertNotIn('inference/aceis_service/lifecycle_host.py', copied)
        self.assertNotIn('inference/aceis_service/host_supervisor.py', copied)
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)
            for item in copied:
                (dest / item).parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(root / item, dest / item)
            code = "import sys; sys.path.insert(0, '.'); import inference.aceis_service.core_server; assert 'inference.aceis_service.backends' not in sys.modules"
            subprocess.run([sys.executable, '-I', '-B', '-c', code], cwd=dest, check=True, capture_output=True, text=True)
    def test_build_context_contains_only_reviewed_core_sources(self):
        from inference.core_container.build import CORE_FILES, prepare_context
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as d:
            prepare_context(root, d)
            observed = {str(path.relative_to(d)) for path in Path(d).rglob('*') if path.is_file()}
            expected = {'inference/aceis_service/' + name for name in CORE_FILES}
            expected.add('inference/core_container/Dockerfile')
            self.assertEqual(observed, expected)
            self.assertFalse(any('host_supervisor' in name or 'backends.py' == Path(name).name for name in observed))

    def test_panic_fences_identity_before_delayed_callback(self):
        entered, release = threading.Event(), threading.Event()
        def slow(reason): entered.set(); release.wait(1)
        port = HostLifecycle(Provider(), sample, panic_callback=slow)
        request = {'operation': 'panic', 'epoch': port.epoch, 'generation': 0}
        worker = threading.Thread(target=lambda: port.dispatch(request)); worker.start()
        self.assertTrue(entered.wait(1))
        try:
            self.assertTrue(port.safety_fault); self.assertTrue(port.stopping)
            self.assertEqual(port.generation, 1)
            with self.assertRaises(ServiceError):
                port.dispatch({'operation': 'stop', 'epoch': port.epoch, 'generation': 0})
            with self.assertRaises(ServiceError): port.dispatch({'operation': 'sample'})
        finally: release.set(); worker.join(1)

    def test_base_reference_must_be_immutable(self):
        from inference.core_container.build import validate_base
        with self.assertRaises(ValueError): validate_base('python:latest')
        self.assertEqual(validate_base('python@sha256:' + 'a' * 64), 'python@sha256:' + 'a' * 64)


@unittest.skipUnless(os.environ.get('LAF_TEST_LIVE_UDS') == '1', 'requires permitted real UDS executor')
class CoreEntryLiveTest(unittest.TestCase):
    def test_opt_in_core_entry_uses_controller_and_preserves_http_api(self):
        from .core_server import run_core
        from .server import Server, UnixHTTPConnection
        events, lifecycle = [], FakeLifecycle()
        class Backend:
            def __init__(self, *args): self.resident = False
            def abort(self): events.append('abort'); self.resident = False
            def start_ollama(self, check): check(); events.append('ollama')
            def verify_models(self): events.append('models')
            def start_flash(self, check): check(); events.append('flash'); self.resident = True
            def health(self): return {'flash_resident': self.resident}
            def generate(self, job, check): check(); return {'content': 'fake Core entry'}
            def close(self): self.abort()
        class TestServer(Server):
            def serve_forever(inner, poll_interval=.2):
                worker = threading.Thread(target=lambda: super(TestServer, inner).serve_forever(poll_interval=.01), daemon=True)
                worker.start()
                try:
                    conn = UnixHTTPConnection(inner.server_address)
                    request = {'model': 'flash', 'messages': [{'role': 'user', 'content': 'fake'}]}
                    conn.request('POST', '/v1/chat/completions', json.dumps(request), {'Content-Type': 'application/json'})
                    response = conn.getresponse(); result = json.loads(response.read()); conn.close()
                    self.assertEqual(response.status, 200); self.assertEqual(result['result']['content'], 'fake Core entry')
                finally: inner.shutdown(); worker.join(2)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); config = ConfigPackagingTest().config(root); path = root / 'core.json'
            path.write_text(json.dumps(config)); path.chmod(0o600)
            run_core(path, backend_factory=Backend, client_factory=lambda config: lifecycle,
                     server_factory=TestServer, install_signals=False)
            self.assertFalse((root / 'runtime/service.sock').exists())
            self.assertEqual(events[:4], ['abort', 'ollama', 'models', 'flash'])
            self.assertEqual(events[-1], 'abort')


if __name__ == '__main__': unittest.main()
