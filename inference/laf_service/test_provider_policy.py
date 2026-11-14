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

# Added 2026-11-11: fixed host-only provider policy regression tests.

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
from . import backends
from .core import GIB, ServiceError

REF = 'docker.io/ollama/ollama@sha256:' + 'a' * 64
ID = 'sha256:' + 'b' * 64


class ProviderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {'state_dir': str(self.root / 'state'), 'weights': str(self.root / 'weights'),
                       'container_name': 'reviewed-provider', 'docker_network': 'private'}
        self.root.joinpath('weights').mkdir()
        self.policy = {'image': REF, 'version': '1.2.3', 'memory_bytes': 8 * GIB, 'cpus': 2, 'pids_limit': 128}

    def make(self, value=None):
        config = copy.deepcopy(self.config)
        if value is not None:
            config['ollama_provider'] = copy.deepcopy(value)
        return backends.Backends(config, '/unused')

    def image(self):
        return {'Id': ID, 'Os': 'linux', 'RepoDigests': [REF], 'Config': {'Volumes': None}}

    def container(self, backend, image=ID):
        state = Path(backend.config['state_dir'])
        return {'Image': image, 'State': {'Running': False},
                'Config': {'Labels': {'laf.inference.owner': backend.owner},
                           'User': f'{os.getuid()}:{os.getgid()}',
                           'Env': ['OLLAMA_HOST=0.0.0.0:30000', 'OLLAMA_MODELS=/store',
                                   'OLLAMA_MAX_LOADED_MODELS=1', 'OLLAMA_NUM_PARALLEL=1', 'OLLAMA_NO_CLOUD=1']},
                'HostConfig': {'Privileged': False, 'PortBindings': {}, 'NetworkMode': 'private',
                               'ReadonlyRootfs': True, 'CapDrop': ['ALL'], 'CapAdd': [],
                               'SecurityOpt': ['no-new-privileges:true'], 'Memory': backend.provider['memory_bytes'],
                               'MemorySwap': backend.provider['memory_bytes'], 'NanoCpus': backend.provider['cpus'] * 10**9,
                               'PidsLimit': backend.provider['pids_limit'], 'IpcMode': 'private', 'PidMode': '',
                               'DeviceRequests': [], 'Devices': [
                                   {'PathOnHost': p, 'PathInContainer': p, 'CgroupPermissions': 'rwm'}
                                   for p in ('/dev/kfd', '/dev/dri/renderD128')]},
                'NetworkSettings': {'Networks': {'private': {'IPAddress': '172.22.0.2'}}},
                'Mounts': [{'Destination': '/weights', 'Source': str(self.root / 'weights'), 'RW': False},
                           {'Destination': '/input', 'Source': str(state / 'input'), 'RW': False},
                           {'Destination': '/store', 'Source': str(state / 'store'), 'RW': True}]}

    def resolve(self, value=None, image=None):
        backend = self.make(self.policy if value is None else value)
        backend.docker = Mock(return_value=json.dumps([self.image() if image is None else image]))
        return backend, backend.resolve_provider_image()

    def test_legacy_defaults_unchanged_without_docker_lookup(self):
        backend = self.make()
        backend.docker = Mock()
        self.assertEqual(backend.provider, {'image': backends.IMAGE, 'version': '0.40.15',
                                           'memory_bytes': 32 * GIB, 'cpus': 8, 'pids_limit': 512})
        self.assertEqual(backend.resolve_provider_image(), backends.IMAGE)
        backend.docker.assert_not_called()

    def test_repository_digest_resolves_only_installed_exact_image(self):
        backend, value = self.resolve()
        self.assertEqual(value, ID)
        backend.docker.assert_called_once_with('image', 'inspect', '--', REF)
        self.assertEqual(backend.resolve_provider_image(), ID)
        self.assertEqual(backend.docker.call_count, 1)

    def test_local_content_id_is_supported_with_exact_lookup(self):
        policy = dict(self.policy, image=ID)
        _, value = self.resolve(policy)
        self.assertEqual(value, ID)

    def test_input_policy_is_detached(self):
        config = dict(self.config, ollama_provider=self.policy)
        backend = backends.Backends(config, '/unused')
        self.policy['memory_bytes'] = 64 * GIB
        self.assertEqual(backend.provider['memory_bytes'], 8 * GIB)

    def test_policy_requires_exact_keys(self):
        for value in (None, [], {}, dict(self.policy, privileged=True)):
            with self.subTest(value=value), self.assertRaises(ServiceError):
                backends.provider_policy(value)
        for key in self.policy:
            value = dict(self.policy);value.pop(key)
            with self.subTest(key=key), self.assertRaises(ServiceError):
                backends.provider_policy(value)

    def test_unpinned_or_malicious_images_rejected(self):
        for image in ('ollama/ollama:latest', 'ollama/ollama:0.40.15', '--privileged', REF + '\n',
                      REF.upper(), 'https://' + REF, 'bad//ollama@sha256:' + 'a' * 64, '../x@sha256:' + 'a' * 64):
            with self.subTest(image=image), self.assertRaises(ServiceError):
                backends.provider_policy(dict(self.policy, image=image))

    def test_integer_limits_and_versions_rejected(self):
        changes = [('memory_bytes', True), ('memory_bytes', GIB - 1), ('memory_bytes', 129 * GIB),
                   ('memory_bytes', 8.0 * GIB), ('cpus', 0), ('cpus', 33), ('cpus', True),
                   ('pids_limit', 63), ('pids_limit', 4097), ('pids_limit', False),
                   ('version', 'latest'), ('version', '1.2.3\n'), ('version', None)]
        for key, value in changes:
            with self.subTest(key=key, value=value), self.assertRaises(ServiceError):
                backends.provider_policy(dict(self.policy, **{key: value}))

    def test_boundary_limits_are_explicit(self):
        for memory, cpus, pids in ((GIB, 1, 64), (128 * GIB, 32, 4096)):
            value = dict(self.policy, memory_bytes=memory, cpus=cpus, pids_limit=pids)
            self.assertEqual(backends.provider_policy(value), value)

    def test_image_identity_os_digest_or_implicit_mount_rejected(self):
        changes = [('Id', backends.IMAGE), ('Os', 'windows'), ('RepoDigests', []),
                   ('RepoDigests', [REF + 'x']), ('Config', {'Volumes': {'/unexpected': {}}}),
                   ('Config', {'Volumes': []})]
        for key, value in changes:
            image = self.image();image[key] = value
            policy = dict(self.policy, image=ID) if key == 'Id' else self.policy
            backend = self.make(policy);backend.docker = Mock(return_value=json.dumps([image]))
            with self.subTest(key=key), self.assertRaises(ServiceError):
                backend.resolve_provider_image()
            self.assertEqual(backend.docker.call_count, 1)

    def test_unexpected_image_response_is_closed_failure(self):
        for response in ('bad', '{}', '[]', '[{},{}]', '[{}]', '[null]'):
            backend = self.make(self.policy);backend.docker = Mock(return_value=response)
            with self.subTest(response=response), self.assertRaises(ServiceError):
                backend.resolve_provider_image()

    def test_missing_image_never_pulls_or_stops(self):
        backend = self.make(self.policy);backend.docker = Mock(side_effect=ServiceError('not installed'))
        with self.assertRaises(ServiceError):
            backend.start_ollama()
        backend.docker.assert_called_once_with('image', 'inspect', '--', REF)

    def test_owned_container_checks_resolved_image_and_every_limit(self):
        backend, _ = self.resolve();backend.cid = 'c' * 64
        good = self.container(backend)
        backend.docker = Mock(return_value=json.dumps([good]))
        self.assertEqual(backend.inspect_owned(), good)
        for key, value in (('Memory', 0), ('MemorySwap', 16 * GIB), ('NanoCpus', 0), ('PidsLimit', -1),
                           ('CapAdd', ['SYS_ADMIN']), ('Devices', []), ('DeviceRequests', [{'Driver': 'nvidia'}])):
            bad = copy.deepcopy(good);bad['HostConfig'][key] = value
            backend.docker.return_value = json.dumps([bad])
            with self.subTest(key=key), self.assertRaises(ServiceError):
                backend.inspect_owned()
        bad = copy.deepcopy(good);bad['Image'] = backends.IMAGE
        backend.docker.return_value = json.dumps([bad])
        with self.assertRaises(ServiceError):
            backend.inspect_owned()

    def test_create_uses_resolved_image_limits_and_preserves_guards(self):
        backend = self.make(self.policy);calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('image', 'inspect'): return json.dumps([self.image()])
            if args[:2] == ('network', 'inspect'): return '[{"Internal":true}]'
            if args[0] == 'ps': return ''
            if args[0] == 'create': return 'c' * 64
            if args[0] == 'inspect': return json.dumps([self.container(backend)])
            return ''
        backend.docker = docker;backend.wait_idle = Mock();backend.wait_ollama = Mock()
        original = os.stat
        def fake_stat(path, *args, **kwargs):
            return SimpleNamespace(st_gid=44) if path == '/dev/kfd' else original(path, *args, **kwargs)
        with patch.object(backends.os, 'stat', side_effect=fake_stat):
            backend.start_ollama()
        create = next(call for call in calls if call[0] == 'create')
        for key, value in (('--memory', str(8 * GIB)), ('--memory-swap', str(8 * GIB)), ('--cpus', '2'), ('--pids-limit', '128')):
            self.assertEqual(create[create.index(key) + 1], value)
        self.assertEqual(create[-2:], (ID, 'serve'))
        for arg in ('--pull=never', '--read-only', 'ALL', 'no-new-privileges:true', '/dev/kfd', '/dev/dri/renderD128'):
            self.assertIn(arg, create)
        start = next(i for i, call in enumerate(calls) if call[0] == 'start')
        self.assertTrue(any(call[0] == 'inspect' for call in calls[:start]))

    def test_invalid_created_container_is_not_started(self):
        backend, _ = self.resolve();backend.wait_idle = Mock();backend.wait_ollama = Mock()
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('network', 'inspect'): return '[{"Internal":true}]'
            if args[0] == 'ps': return ''
            if args[0] == 'create': return 'c' * 64
            if args[0] == 'inspect':
                value = self.container(backend);value['HostConfig']['Memory'] = 0
                return json.dumps([value])
            return ''
        backend.docker = docker
        original = os.stat
        with patch.object(backends.os, 'stat', side_effect=lambda path, *a, **kw: SimpleNamespace(st_gid=44) if path == '/dev/kfd' else original(path, *a, **kw)):
            with self.assertRaises(ServiceError): backend.start_ollama()
        self.assertFalse(any(call[0] == 'start' for call in calls))

    def test_expected_version_is_explicit_no_fallback(self):
        backend = self.make(self.policy);backend.api = Mock(return_value={'version': '1.2.3'})
        backend.wait_ollama(lambda: None)
        backend.api.assert_called_once_with('/api/version', timeout=2)
        backend.api.return_value = {'version': '0.40.15'}
        with patch.object(backends.time, 'monotonic', side_effect=[0, 0, 31]), patch.object(backends.time, 'sleep'):
            with self.assertRaises(ServiceError): backend.wait_ollama(lambda: None)

    def test_existing_foreign_or_mismatched_provider_is_not_mutated(self):
        for change in ('owner', 'image', 'cpu'):
            backend, _ = self.resolve();calls = []
            value = self.container(backend)
            if change == 'owner': value['Config']['Labels']['laf.inference.owner'] = 'someone-else'
            elif change == 'image': value['Image'] = backends.IMAGE
            else: value['HostConfig']['NanoCpus'] = 0
            def docker(*args, **kwargs):
                calls.append(args)
                if args[:2] == ('network', 'inspect'): return '[{"Internal":true}]'
                if args[0] == 'ps': return 'c' * 64
                if args[0] == 'inspect': return json.dumps([value])
                self.fail('unexpected mutation: ' + args[0])
            backend.docker = docker
            with self.subTest(change=change), self.assertRaises(ServiceError):
                backend.start_ollama()
            self.assertTrue(all(call[0] in {'network', 'ps', 'inspect'} for call in calls))

    def test_stopped_custom_provider_discovery_is_read_only(self):
        from .lifecycle_host import OwnedOllama
        provider = OwnedOllama(dict(self.config, ollama_provider=self.policy), '/unused')
        backend = provider.backend;calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            if args[0] == 'ps': return 'c' * 64
            if args[:2] == ('image', 'inspect'): return json.dumps([self.image()])
            if args[0] == 'inspect': return json.dumps([self.container(backend)])
            self.fail('unexpected mutation: ' + args[0])
        backend.docker = docker
        self.assertFalse(provider.running())
        self.assertTrue(all(call[0] in {'ps', 'image', 'inspect'} for call in calls))

    def test_legacy_creation_still_uses_original_image_and_limits(self):
        backend = self.make();calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('network', 'inspect'): return '[{"Internal":true}]'
            if args[0] == 'ps': return ''
            if args[0] == 'create': return 'c' * 64
            if args[0] == 'inspect': return json.dumps([self.container(backend, backends.IMAGE)])
            return ''
        backend.docker = docker;backend.wait_idle = Mock();backend.wait_ollama = Mock()
        original = os.stat
        with patch.object(backends.os, 'stat', side_effect=lambda path, *a, **kw: SimpleNamespace(st_gid=44) if path == '/dev/kfd' else original(path, *a, **kw)):
            backend.start_ollama()
        create = next(call for call in calls if call[0] == 'create')
        self.assertEqual(create[-2:], (backends.IMAGE, 'serve'))
        for key, value in (('--memory', str(32 * GIB)), ('--memory-swap', str(32 * GIB)), ('--cpus', '8'), ('--pids-limit', '512')):
            self.assertEqual(create[create.index(key) + 1], value)
        self.assertFalse(any(call[0] == 'image' for call in calls))
        self.assertEqual(backend.inspect_owned()['Image'], backends.IMAGE)

    def test_falsey_non_dictionary_volume_metadata_is_rejected(self):
        for volumes in ('', False, 0, []):
            value = self.image();value['Config']['Volumes'] = volumes
            with self.subTest(volumes=volumes), self.assertRaises(ServiceError):
                self.resolve(image=value)


if __name__ == '__main__':
    unittest.main()
