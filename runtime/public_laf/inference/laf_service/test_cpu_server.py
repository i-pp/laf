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

# Modified 2026-11-21: cover CPU readiness before and after an owned restart.

import copy
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from . import cpu_server
from .core import GIB, Job, ServiceError, check_cpu_resources, check_resources


def configuration(base):
    return {
        'mode': 'cpu-only',
        'runtime_dir': str(base / 'runtime'),
        'model_store': str(base / 'store'),
        'generation_lock': str(base / 'generation.lock'),
        'ollama_provider': {'image': 'sha256:' + 'a' * 64, 'version': '0.40.15',
                            'memory_bytes': GIB, 'cpus': 1, 'pids_limit': 96},
        'ollama_profile': {'id': 'small-cpu', 'reference': 'small:instruct-q4_K_M',
                           'digest': 'sha256:' + 'b' * 64,
                           'options': {'num_ctx': 8192, 'num_thread': 1, 'num_batch': 32,
                                       'num_gpu': 0, 'temperature': 0, 'seed': 42}},
    }


class CPUConfigTests(unittest.TestCase):
    def test_only_explicit_owner_configuration_and_cpu_profile_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = configuration(base)
            (base / 'runtime').mkdir(mode=0o700)
            (base / 'store').mkdir(mode=0o700)
            path = base / 'config.json'
            path.touch(mode=0o600)
            path.write_text(json.dumps(config))
            self.assertEqual(cpu_server.load_cpu_config(path), config)

            for key, value in [('mode', 'normal'), ('npu_socket', '/other'), ('flash_enabled', False),
                               ('generation_lock', str(base / 'wrong.lock')),
                               ('model_store', config['runtime_dir']), ('swap_limit_mib', 4096)]:
                with self.subTest(key=key):
                    path.write_text(json.dumps(config | {key: value}))
                    with self.assertRaises(ServiceError):
                        cpu_server.load_cpu_config(path)

            for key, value in [('num_gpu', 1), ('num_gpu', False), ('num_thread', 2)]:
                with self.subTest(option=key):
                    bad = copy.deepcopy(config)
                    bad['ollama_profile']['options'][key] = value
                    path.write_text(json.dumps(bad))
                    with self.assertRaises(ServiceError):
                        cpu_server.load_cpu_config(path)

    def test_cpu_retains_host_ram_swap_io_memory_psi_and_observation_checks(self):
        idle = {'mem_available': 10 * GIB, 'swap_used': 0, 'pswpin': 1, 'pswpout': 1, 'psi_full_avg10': 0}
        check_cpu_resources(idle, idle)
        excess_io = (512 * 1024**2) // os.sysconf('SC_PAGE_SIZE') + 2
        for bad in ({'mem_available': 8 * GIB - 1}, {'swap_used': GIB}, {'pswpin': excess_io},
                    {'pswpout': excess_io}, {'pswpout': 0}, {'psi_full_avg10': 10.1},
                    {'psi_full_avg10': float('nan')}):
            with self.subTest(sample=bad), self.assertRaises(ServiceError):
                check_cpu_resources(idle | bad, idle)
        gpu = idle | {'vram_total': 8 * GIB, 'vram_used': 7 * GIB}
        with self.assertRaisesRegex(ServiceError, 'VRAM reserve'):
            check_resources(gpu, gpu)


class CPUProviderTests(unittest.TestCase):
    def setUp(self):
        config = configuration(Path('/operator-private'))
        self.engine = cpu_server.CPUOllama(config, '/private-docker-config')
        engine = self.engine
        engine.image = config['ollama_provider']['image']
        engine.cid = 'c' * 64
        self.info = {
            'Id': engine.cid, 'Image': engine.image, 'Name': '/' + engine.name,
            'Config': {'Labels': {'laf.cpu.owner': engine.owner}, 'User': engine.uid,
                       'Entrypoint': ['/bin/ollama'], 'Cmd': ['serve'],
                       'Env': [key + '=' + value for key, value in cpu_server.ENGINE_ENV.items()]},
            'HostConfig': {'Runtime': 'runc', 'NetworkMode': 'none', 'Privileged': False,
                           'PortBindings': {}, 'PublishAllPorts': False, 'Devices': [], 'DeviceRequests': None,
                           'DeviceCgroupRules': None, 'GroupAdd': None, 'CapAdd': None, 'PidMode': '',
                           'IpcMode': 'private', 'UTSMode': '', 'Init': True, 'CapDrop': ['ALL'],
                           'ReadonlyRootfs': True, 'SecurityOpt': ['no-new-privileges:true'],
                           'RestartPolicy': {'Name': 'no'}, 'Memory': GIB, 'MemorySwap': GIB,
                           'NanoCpus': 10**9, 'PidsLimit': 96},
            'NetworkSettings': {'Networks': {'none': {}}},
            'Mounts': [{'Type': 'bind', 'Source': engine.store, 'Destination': '/models', 'RW': False}],
            'State': {'Running': True, 'Pid': 50, 'Paused': False, 'Restarting': False},
        }
        engine.docker = Mock(side_effect=self.docker)

    def docker(self, *args, **_kwargs):
        if args == ('container', 'inspect', self.engine.cid):
            return json.dumps([self.info])
        raise AssertionError(args)

    def test_owned_container_refuses_devices_network_and_weakened_limits(self):
        self.assertTrue(self.engine.inspect()['Running'])
        for key, value in [('Runtime', 'nvidia'), ('Devices', ['/dev/kfd']),
                           ('DeviceRequests', [{'Capabilities': [['gpu']]}]), ('DeviceCgroupRules', ['c 226:* rw']),
                           ('NetworkMode', 'bridge'), ('PortBindings', {'11434/tcp': [{}]}),
                           ('PublishAllPorts', True), ('PidMode', 'host'), ('IpcMode', 'host'),
                           ('CapAdd', ['SYS_ADMIN']), ('Privileged', True), ('MemorySwap', 2 * GIB),
                           ('SecurityOpt', []), ('NanoCpus', 0)]:
            with self.subTest(host_config=key):
                with patch.dict(self.info['HostConfig'], {key: value}):
                    with self.assertRaises(ServiceError):
                        self.engine.inspect()
        self.info['Mounts'][0]['RW'] = True
        with self.assertRaisesRegex(ServiceError, 'mount changed'):
            self.engine.inspect()

    def test_unconfirmed_owner_or_stop_preserves_block_and_never_removes(self):
        self.info['Config']['Labels']['laf.cpu.owner'] = 'other'
        with self.assertRaises(ServiceError):
            self.engine.close()
        self.engine.docker.assert_called_once_with('container', 'inspect', self.engine.cid, timeout=8)
        self.assertTrue(self.engine.blocked)

        self.info['Config']['Labels']['laf.cpu.owner'] = self.engine.owner
        calls = []

        def stop_but_running(*args, **kwargs):
            calls.append(args[0])
            return self.docker(*args, **kwargs) if args[0] == 'container' else ''

        self.engine.docker.side_effect = stop_but_running
        with self.assertRaisesRegex(ServiceError, 'stop unconfirmed'):
            self.engine.close()
        self.assertEqual(calls, ['container', 'stop', 'container'])
        self.assertTrue(self.engine.blocked)

    def test_close_removes_only_after_confirmed_owned_stop(self):
        self.engine.ready = True

        def docker(*args, **kwargs):
            if args[0] == 'stop':
                self.info['State'].update(Running=False, Pid=0)
            elif args[0] == 'rm':
                self.assertFalse(self.info['State']['Running'])
                self.assertEqual(args[1], self.engine.cid)
            else:
                return self.docker(*args, **kwargs)
            return ''

        self.engine.docker.side_effect = docker
        self.engine.close()
        self.assertEqual([call.args[0] for call in self.engine.docker.call_args_list],
                         ['container', 'stop', 'container', 'rm'])
        self.assertIsNone(self.engine.cid)
        self.assertFalse(self.engine.ready)

    def test_uncertain_create_fences_further_create_and_cleanup(self):
        engine = self.engine
        engine.cid = None
        engine.creation_attempted = True
        with self.assertRaises(ServiceError):
            engine.abort()
        with self.assertRaises(ServiceError):
            engine.ensure(lambda: None)
        engine.docker.assert_not_called()

    def test_cold_start_requires_host_reserve_plus_container_memory_limit(self):
        self.info['State'].update(Running=False, Pid=0)
        with patch.object(cpu_server, 'snapshot_cpu', return_value={'mem_available': 9 * GIB - 1}):
            with self.assertRaisesRegex(ServiceError, 'RAM reserve'):
                self.engine.ensure(lambda: None)
        self.engine.docker.assert_called_once_with('container', 'inspect', self.engine.cid, timeout=8)

    def test_http_health_tracks_stopped_provider_and_verified_restart(self):
        engine = self.engine
        resources = {'mem_available': 10 * GIB, 'swap_used': 0, 'pswpin': 0, 'pswpout': 0, 'psi_full_avg10': 0}
        controller = cpu_server.Controller(engine, read_resources=lambda: resources, start=False,
                                           check_limits=check_cpu_resources)
        handler = cpu_server.Handler.__new__(cpu_server.Handler)
        handler.path = '/health'
        handler.headers = {}
        handler.server = SimpleNamespace(config={}, controller=controller)
        handler.response = Mock()
        engine.ready = True
        engine.api = Mock()

        def check_health(code, status, *, inspect=True):
            engine.docker.reset_mock()
            api_calls = engine.api.call_count
            handler.do_GET()
            health_code, body = handler.response.call_args.args
            self.assertEqual((health_code, body['status']), (code, status))
            self.assertIs(body['gpu']['engine_running'], code == 200)
            self.assertEqual(engine.api.call_count, api_calls)
            if inspect:
                engine.docker.assert_called_once_with('container', 'inspect', engine.cid, timeout=1)
            else:
                engine.docker.assert_not_called()

        check_health(200, 'ready')
        with patch.object(engine.docker, 'side_effect', ServiceError('inspection unavailable')):
            check_health(503, 'not_ready')
        self.info['State'].update(Running=False, Pid=0)
        check_health(503, 'not_ready')

        def docker(*args, **kwargs):
            if args == ('start', engine.cid):
                self.info['State'].update(Running=True, Pid=51)
                return ''
            return self.docker(*args, **kwargs)

        model = {'name': engine.profile['reference'], 'digest': engine.profile['digest']}
        replies = {'/api/version': {'version': engine.policy['version']}, '/api/tags': {'models': [model]}}

        def api(path, **_kwargs):
            check_health(503, 'not_ready', inspect=False)
            return replies[path]

        engine.docker.side_effect = docker
        engine.api.side_effect = api
        with patch.object(cpu_server, 'snapshot_cpu', return_value=resources):
            engine.ensure(lambda: None)
        self.assertEqual([call.args[0] for call in engine.api.call_args_list], list(replies))
        check_health(200, 'ready')

    def test_one_chat_and_exact_cpu_model_evidence(self):
        engine = self.engine
        engine.ensure = Mock()
        profile = engine.profile
        job = Job(profile['id'], [{'role': 'user', 'content': 'say hello'}], 8, time.monotonic() + 20)
        reply = {'done': True, 'model': profile['reference'], 'message': {'content': 'hello'},
                 'done_reason': 'stop', 'eval_count': 1}
        row = {'name': profile['reference'], 'digest': profile['digest'], 'size_vram': 0}
        engine.api = Mock(side_effect=[reply, {'models': [row]}])
        self.assertEqual(engine.generate(job, lambda: None)['content'], 'hello')
        self.assertEqual([call.args[0] for call in engine.api.call_args_list], ['/api/chat', '/api/ps'])
        body = engine.api.call_args_list[0].args[1]
        self.assertEqual(body['model'], profile['reference'])
        self.assertEqual(body['messages'], job.messages)
        self.assertEqual(body['options']['num_gpu'], 0)
        self.assertEqual(body['options']['num_predict'], 8)
        for bad in ({'size_vram': 1}, {'size_vram': False}, {'digest': 'a' * 64}):
            with self.subTest(bad=bad):
                engine.api.reset_mock(side_effect=True)
                engine.api.side_effect = [reply, {'models': [row | bad]}]
                with self.assertRaises(ServiceError):
                    engine.generate(job, lambda: None)
                self.assertEqual(engine.api.call_count, 2)

    def test_pipe_transmits_private_text_on_stdin_and_refuses_invalidated_exchange(self):
        engine = self.engine
        private_text = 'caf\u00e9'
        reply = b'HTTP/1.0 200 OK\r\nContent-Length: 11\r\n\r\n{"ok":true}'
        child = MagicMock(returncode=0)
        child.communicate.return_value = (reply, None)
        with patch.object(cpu_server.subprocess, 'Popen', return_value=child) as popen:
            self.assertEqual(engine.api('/api/chat', {'input': private_text}), {'ok': True})
            command = popen.call_args.args[0]
            request = child.communicate.call_args.args[0]
            self.assertNotIn(private_text, ' '.join(command))
            self.assertIn(private_text.encode(), request)
            self.assertEqual(int(command[-1]), len(request))
            self.assertNotIn('DOCKER_HOST', popen.call_args.kwargs['env'])

            def invalidate(*_args, **_kwargs):
                engine.generation += 1
                return reply, None

            child.communicate.side_effect = invalidate
            with self.assertRaisesRegex(ServiceError, 'exchange invalidated'):
                engine.api('/api/ps')


if __name__ == '__main__':
    unittest.main()
