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

import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from .core import ServiceError
from .host_supervisor import HostSupervisor, OwnedCoreStop, open_owned, read_config
from .lifecycle import LifecycleFault
from .test_lifecycle import sample


class FakeLane:
    """Filesystem lane test double; live AF_UNIX is separately opt-in."""
    def __init__(self, port, runtime, *, control):
        self.path = runtime / ('lifecycle-control.sock' if control else 'lifecycle.sock')
        if os.path.lexists(self.path):
            raise ServiceError('existing endpoint')
        self.path.touch(mode=0o600)
        self.identity = self.path.stat().st_ino
    def close(self):
        if self.path.exists() and self.path.stat().st_ino == self.identity:
            self.path.unlink()
    def serve_forever(self): pass


class SupervisorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('runtime', 'state'):
            (self.root / name).mkdir(mode=0o700)
        self.lock = self.root / 'generation.lock'
        self.lock.write_text('historical content')
        self.lock.chmod(0o600)
        self.config = dict(runtime_dir=str(self.root / 'runtime'), state_dir=str(self.root / 'state'),
                           generation_lock=str(self.lock), lease_seconds=2, core={}, ollama={})
        self.events = []
        self.core = Mock()
        self.core.running.return_value = False
        self.core.stop.side_effect = lambda: self.events.append('core')
        self.provider = Mock()
        self.provider.running.return_value = False
        self.provider.stop.side_effect = lambda: self.events.append('ollama')
        self.now = [100.]
        self.kwargs = dict(core_factory=lambda _: self.core, ollama_factory=lambda *_: self.provider,
                           read_resources=sample, clock=lambda: self.now[0], server_factory=FakeLane)

    def make(self):
        sup = HostSupervisor(self.config, '/unused', **self.kwargs)
        self.addCleanup(sup.close)
        return sup

    @unittest.skipUnless(os.environ.get('LAF_TEST_LIVE_UDS') == '1', 'opt-in real socket test')
    def test_real_socket_lanes(self):
        from .lifecycle_uds import UnixLifecycleServer
        self.kwargs['server_factory'] = UnixLifecycleServer
        sup = self.make()
        self.assertEqual(len(sup.servers), 2)
        for server in sup.servers:
            self.assertEqual(server.path.stat().st_mode & 0o777, 0o600)
        sup.close()
        self.assertFalse((self.root / 'runtime/lifecycle.sock').exists())

    def test_locks_nontruncating_and_contention(self):
        self.make()
        with self.assertRaises(BlockingIOError):
            self.make()
        self.assertEqual(self.lock.read_text(), 'historical content')
        self.provider.stop.assert_not_called()

    def test_symlink_foreign_and_permissions(self):
        link = self.root / 'link'
        link.symlink_to(self.lock)
        with self.assertRaises(ServiceError):
            open_owned(link)
        self.lock.chmod(0o644)
        with self.assertRaises(ServiceError):
            open_owned(self.lock)
        self.lock.chmod(0o600)
        with patch(__package__ + '.host_supervisor.os.getuid', return_value=os.getuid()+1):
            with self.assertRaises(ServiceError):
                open_owned(self.lock)
        with self.assertRaises(ServiceError):
            read_config(link)

    def test_existing_socket_entry_never_removed(self):
        target = self.root / 'runtime/lifecycle-control.sock'
        target.write_text('foreign')
        with self.assertRaises(ServiceError):
            self.make()
        self.assertEqual(target.read_text(), 'foreign')
        self.assertFalse((target.parent / 'lifecycle.sock').exists())
        self.provider.stop.assert_not_called()

    def test_construction_failure_never_stops_active_service(self):
        for active in (self.core, self.provider):
            active.running.return_value = True
            with self.assertRaises(ServiceError):
                self.make()
            self.core.stop.assert_not_called()
            self.provider.stop.assert_not_called()
            active.running.return_value = False

    def test_panic_core_first_and_sticky_after_regular_stop(self):
        sup = self.make()
        generation = sup.port.generation
        self.assertTrue(sup.panic('resource failure'))
        self.assertEqual(self.events, ['core', 'ollama'])
        self.assertGreater(sup.port.generation, generation)
        sup.port.dispatch(dict(operation='stop', epoch=sup.port.epoch, generation=sup.port.generation))
        self.assertEqual(sup.port.safety_fault, 'resource failure')
        with self.assertRaises(LifecycleFault):
            sup.port.dispatch(dict(operation='ensure', epoch=sup.port.epoch, generation=sup.port.generation))

    def test_fixed_panic_callback(self):
        sup = self.make()
        status = sup.port.dispatch({'operation': 'status'})
        self.assertTrue(status['emergency_stop_supported'])
        result = sup.port.dispatch(dict(operation='panic', epoch=status['epoch'], generation=status['generation']))
        self.assertTrue(result['emergency_stop_requested'])
        self.assertEqual(self.events, ['core', 'ollama'])
        self.assertTrue(sup.port.safety_fault)

    def test_unconfirmed_core_does_not_stop_ollama(self):
        sup = self.make()
        self.core.stop.side_effect = ServiceError('uncertain')
        self.assertFalse(sup.panic('guard'))
        self.provider.stop.assert_not_called()
        self.assertTrue(sup.port.stopping)
        self.assertEqual(sup.port.safety_fault, 'guard')

    def test_resource_failure_and_lease_expiry(self):
        sup = self.make()
        sup.port.read_resources = lambda: {**sample(), 'mem_available': 1}
        sup.tick()
        self.assertEqual(self.events, ['core', 'ollama'])
        self.assertTrue(sup.port.safety_fault)

    def test_monitor_retries_transient_core_failure_after_resources_recover(self):
        sup = self.make()
        sup.port.read_resources = Mock(side_effect=[{**sample(), 'mem_available': 1}, sample()])
        attempts = []
        def stop_core():
            attempts.append('core')
            if len(attempts) == 1:
                raise ServiceError('transient Docker timeout; outcome unconfirmed')
            self.events.append('core')
        self.core.stop.side_effect = stop_core
        # Deterministic monitor turns without sleeping or real services. If the
        # loop fails to stop after confirmed cleanup, fail on the third wait.
        with patch.object(sup.closed, 'wait', side_effect=[False, False, AssertionError('extra monitor turn')]) as wait:
            sup._monitor()
        self.assertEqual(wait.call_count, 2)
        self.assertEqual(attempts, ['core', 'core'])
        self.assertEqual(self.events, ['core', 'ollama'])
        self.assertEqual(sup.port.read_resources.call_count, 1)
        self.assertTrue(sup.cleanup_confirmed)
        self.assertEqual(sup.port.safety_fault, 'host resource guard or Core lease failed')

    def test_monitor_retries_unconfirmed_physical_barrier(self):
        sup = self.make()
        self.now[0] = 104
        self.provider.stop.side_effect = [ServiceError('physical barrier uncertain'), None]
        with patch.object(sup.closed, 'wait', side_effect=[False, False, AssertionError('extra monitor turn')]):
            sup._monitor()
        self.assertEqual(self.core.stop.call_count, 2)
        self.assertEqual(self.provider.stop.call_count, 2)
        self.assertTrue(sup.cleanup_confirmed)
        self.assertTrue(sup.port.safety_fault)

    def test_sample_refresh_and_expiry(self):
        sup = self.make()
        with patch(__package__ + '.lifecycle.time.monotonic', return_value=101.):
            sup.port.dispatch({'operation': 'sample'})
        self.now[0] = 102.5
        sup.tick()
        self.assertEqual(self.events, [])
        self.now[0] = 104
        sup.tick()
        self.assertEqual(self.events, ['core', 'ollama'])

    def test_closed_gate_rejects_old_handler(self):
        sup = self.make()
        request = dict(operation='ensure', epoch=sup.port.epoch, generation=sup.port.generation)
        sup.close()
        sup.close()
        with self.assertRaises(LifecycleFault):
            sup.port.dispatch(request)
        self.provider.ensure.assert_not_called()
        self.assertTrue(sup.port.closed)

    def test_delayed_old_panic_after_close_cannot_stop_providers(self):
        sup = self.make()
        admitted, resume = threading.Event(), threading.Event()
        real_lock = threading.Lock()
        class DelayedLock:
            def __enter__(self):
                if threading.current_thread().name == 'old-panic':
                    admitted.set()
                    if not resume.wait(2):
                        raise AssertionError('delayed panic was not resumed')
                real_lock.acquire()
            def __exit__(self, *_):
                real_lock.release()
        sup.panic_lock = DelayedLock()
        errors = []
        def old_panic():
            try:
                sup.panic('old admitted callback')
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=old_panic, name='old-panic', daemon=True)
        worker.start()
        try:
            self.assertTrue(admitted.wait(2))
            # Simulate an active supervisor's confirmed shutdown while the old
            # callback is admitted but has not acquired the panic lock yet.
            sup.started = True
            sup.close()
            stops = self.core.stop.call_count, self.provider.stop.call_count
            self.assertEqual(stops, (1, 1))
        finally:
            resume.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], LifecycleFault)
        self.assertEqual((self.core.stop.call_count, self.provider.stop.call_count), stops)
        with self.assertRaises(LifecycleFault):
            sup.panic('new invocation on old object')
        self.assertEqual((self.core.stop.call_count, self.provider.stop.call_count), stops)

    def test_unconfirmed_shutdown_retains_lock(self):
        sup = self.make()
        sup.started = True
        self.core.stop.side_effect = ServiceError('unconfirmed')
        with self.assertRaises(ServiceError): sup.close()
        self.assertFalse(sup.released)
        with self.assertRaises(BlockingIOError): self.make()
        self.core.stop.side_effect = lambda: self.events.append('core')

    def test_cleanup_preserves_replacement_and_lock_inode(self):
        sup = self.make()
        lock = self.root / 'state/supervisor.lock'
        inode = lock.stat().st_ino
        endpoint = self.root / 'runtime/lifecycle.sock'
        endpoint.rename(endpoint.with_name('old-endpoint'))
        endpoint.write_text('replacement')
        sup.close()
        self.assertEqual(endpoint.read_text(), 'replacement')
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertFalse((endpoint.parent / 'lifecycle-control.sock').exists())
        self.provider.stop.assert_not_called()


class CoreStopTest(unittest.TestCase):
    def setUp(self):
        self.policy = dict(container_name='laf-core', image='sha256:'+'ab'*32, owner='test-owner',
                           uid=os.getuid(), gid=os.getgid(), network='laf-private', mounts=[], devices=[],
                           memory=8 * 1024**3, memory_swap=8 * 1024**3, nano_cpus=2_000_000_000, pids_limit=128)
        self.info = dict(Name='/laf-core', Image=self.policy['image'],
                         Config={'Labels': {'laf.core.owner': 'test-owner'}, 'User': f'{os.getuid()}:{os.getgid()}'},
                         HostConfig=dict(Privileged=False, PortBindings={}, ReadonlyRootfs=True,
                                         CapDrop=['ALL'], SecurityOpt=['no-new-privileges:true'],
                                         PidMode='', IpcMode='private', NetworkMode='laf-private', Devices=[],
                                         RestartPolicy={'Name': 'no'}, Memory=self.policy['memory'],
                                         MemorySwap=self.policy['memory_swap'], NanoCpus=self.policy['nano_cpus'],
                                         PidsLimit=self.policy['pids_limit']),
                         NetworkSettings={'Networks': {'laf-private': {}}, 'Ports': {}},
                         Mounts=[], State={'Running': True})
        self.calls = []
        def cli(*args, **kwargs):
            self.calls.append(args)
            if args[0] == 'ps': return 'a'*64
            if args[0] == 'inspect': return json.dumps([self.info])
            if args[:2] == ('network', 'inspect'): return json.dumps([{'Internal': True, 'Driver': 'bridge'}])
            if args[0] == 'stop': self.info['State']['Running'] = False; return ''
            raise AssertionError(args)
        self.adapter = OwnedCoreStop(self.policy, cli=cli)

    def test_stop_only_exact_container(self):
        self.adapter.stop()
        self.assertEqual([c for c in self.calls if c[0] == 'stop'], [('stop', '-t', '3', 'a'*64)])
        self.assertFalse(self.adapter.running())

    def test_each_security_mismatch_never_stops(self):
        original = copy.deepcopy(self.info)
        mutations = [('Image', 'wrong'), ('Name', '/other'),
                     ('HostConfig.Privileged', True), ('HostConfig.PortBindings', {'80/tcp': [{}]}),
                     ('HostConfig.PidMode', 'host'), ('HostConfig.IpcMode', 'host'),
                     ('HostConfig.ReadonlyRootfs', False), ('HostConfig.CapDrop', []),
                     ('HostConfig.CapAdd', ['SYS_ADMIN']), ('HostConfig.SecurityOpt', []),
                     ('HostConfig.Devices', [{}]), ('HostConfig.NetworkMode', 'host'),
                     ('HostConfig.RestartPolicy', {'Name': 'always'}),
                     ('Config.User', '999:999'), ('Config.Labels', {}),
                     ('Mounts', [{'Source': '/run', 'Destination': '/bad', 'RW': True}])]
        for key, value in mutations:
            with self.subTest(key=key):
                self.info = copy.deepcopy(original)
                target = self.info
                parts = key.split('.')
                for part in parts[:-1]: target = target[part]
                target[parts[-1]] = value
                with self.assertRaises(ServiceError): self.adapter.stop()
                self.assertFalse(any(c[0] == 'stop' for c in self.calls))

    def test_resource_policy_requires_explicit_positive_finite_integer_caps(self):
        for key in ('memory', 'memory_swap', 'nano_cpus', 'pids_limit'):
            policy = dict(self.policy)
            del policy[key]
            with self.subTest(key=key, value='missing'), self.assertRaises(ServiceError):
                OwnedCoreStop(policy)
            for value in (0, -1, True, False, None, 1.5, '1024', float('inf'), 2**63):
                with self.subTest(key=key, value=value), self.assertRaises(ServiceError):
                    OwnedCoreStop({**self.policy, key: value})
        with self.assertRaises(ServiceError):
            OwnedCoreStop({**self.policy, 'memory_swap': self.policy['memory'] + 1})

    def test_observed_resource_caps_must_match_exactly_without_stopping(self):
        original = copy.deepcopy(self.info)
        for key in ('Memory', 'MemorySwap', 'NanoCpus', 'PidsLimit'):
            for value in (None, 0, -1, True, float(original['HostConfig'][key]), original['HostConfig'][key] + 1):
                self.info = copy.deepcopy(original)
                if value is None:
                    del self.info['HostConfig'][key]
                else:
                    self.info['HostConfig'][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ServiceError):
                    self.adapter.stop()
                self.assertFalse(any(call[0] == 'stop' for call in self.calls))

    def test_socket_ancestor_and_placeholder_rejected(self):
        for source in ('/', '/run', str(Path.home())):
            with self.subTest(source=source), self.assertRaises(ServiceError):
                OwnedCoreStop({**self.policy, 'mounts': [{'Source': source, 'Destination': '/bad', 'RW': False}]})
        with self.assertRaises(ServiceError):
            OwnedCoreStop({**self.policy, 'image': 'sha256:'+'0'*64})


if __name__ == '__main__':
    unittest.main()
