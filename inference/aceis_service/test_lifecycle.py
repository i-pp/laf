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

import threading
import unittest
from unittest.mock import Mock, patch
from .core import GIB, ServiceError
from .lifecycle import HostLifecycle, LifecycleFault, StaleLease, validate_sample
from .lifecycle_host import OwnedOllama


def sample():
    return {'mem_available': 32 * GIB, 'swap_used': 0, 'vram_total': 64 * GIB,
            'vram_used': GIB, 'pswpin': 0, 'pswpout': 0, 'psi_full_avg10': 0}


class Provider:
    def __init__(self):
        self.alive = False
        self.calls = []
    def ensure(self, check):
        check(); self.calls.append('ensure'); self.alive = True; check()
    def running(self):
        self.calls.append('status'); return self.alive
    def stop(self):
        self.calls.append('stop'); self.alive = False


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.provider = Provider()
        self.port = HostLifecycle(self.provider, sample)
    def request(self, op, **kw):
        return self.port.dispatch({'operation': op, 'epoch': self.port.epoch,
                                   'generation': self.port.generation, **kw})
    def test_only_fixed_parameterless_capabilities(self):
        for request in [None, [], {'operation': []}, {'operation': 'docker'},
                        {'operation': 'ensure'}, {'operation': 'sample', 'path': '/proc'},
                        {'operation': 'status', 'image': 'arbitrary'},
                        {'operation': 'stop', 'epoch': 'x', 'generation': 0}]:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.port.dispatch(request)
        self.assertEqual(self.provider.calls, [])
    def test_no_policy_or_command_injection(self):
        for key in ['args', 'image', 'mount', 'env', 'container', 'network', 'path', 'model']:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.request('ensure', **{key: '/var/run/docker.sock'})
        self.assertEqual(self.provider.calls, [])
    def test_boolean_generation_rejected(self):
        with self.assertRaises(ValueError): self.request('ensure', generation=True)
    def test_ensure_stop_and_old_generation_rejection(self):
        old = {'operation': 'ensure', 'epoch': self.port.epoch, 'generation': 0}
        self.assertTrue(self.port.dispatch(old)['running'])
        self.assertFalse(self.request('stop')['running'])
        with self.assertRaises(StaleLease): self.port.dispatch(old)
        self.assertEqual(self.provider.calls.count('ensure'), 1)
        self.assertTrue(self.request('ensure')['running'])
    def test_host_restart_invalidates_old_epoch(self):
        request = {'operation': 'ensure', 'epoch': self.port.epoch, 'generation': 0}
        other = HostLifecycle(self.provider, sample)
        with self.assertRaises(StaleLease): other.dispatch(request)
        self.assertEqual(self.provider.calls, [])
    def test_telemetry_always_fresh_and_detached(self):
        calls = []
        self.port.read_resources = lambda: (calls.append(1), sample())[1]
        a = self.port.dispatch({'operation': 'sample'})['resources']; a['mem_available'] = 0
        b = self.port.dispatch({'operation': 'sample'})['resources']
        self.assertEqual(b['mem_available'], 32 * GIB); self.assertEqual(len(calls), 2)
    def test_unknown_telemetry_rejects_start(self):
        self.port.read_resources = lambda: {}
        with self.assertRaises(ServiceError): self.request('ensure')
        self.assertNotIn('ensure', self.provider.calls)
    def test_resource_pressure_rejects_start(self):
        s = sample(); s['mem_available'] = 7 * GIB; self.port.read_resources = lambda: s
        with self.assertRaises(ServiceError): self.request('ensure')
        self.assertNotIn('ensure', self.provider.calls)
    def test_telemetry_failure_does_not_prevent_stop(self):
        self.provider.alive = True; self.port.read_resources = lambda: {}
        self.assertFalse(self.request('stop')['running'])
    def test_ambiguous_ensure_latches_until_confirmed_stop(self):
        def uncertain(check): self.provider.alive = True; raise OSError('sensitive detail')
        self.provider.ensure = uncertain
        with self.assertRaisesRegex(LifecycleFault, 'outcome unconfirmed') as error:
            self.request('ensure')
        self.assertNotIn('sensitive', str(error.exception))
        with self.assertRaises(LifecycleFault): self.request('ensure')
        self.assertFalse(self.request('stop')['running']); self.assertIsNone(self.port.fault)
    def test_stop_error_latches_and_advances_generation(self):
        self.provider.stop = Mock(side_effect=OSError('error'))
        with self.assertRaises(LifecycleFault): self.request('stop')
        self.assertEqual(self.port.generation, 1)
        with self.assertRaises(LifecycleFault): self.request('ensure')
        self.assertFalse(self.port.stopping)
    def test_running_after_stop_cannot_acknowledge(self):
        self.provider.alive = True; self.provider.stop = lambda: None
        with self.assertRaisesRegex(LifecycleFault, 'stop or physical release unconfirmed'):
            self.request('stop')
    def test_success_requires_actual_running_status(self):
        self.provider.ensure = lambda check: check()
        with self.assertRaises(LifecycleFault): self.request('ensure')
    def test_invalid_running_status_is_rejected(self):
        self.provider.running = lambda: 1
        with self.assertRaises(LifecycleFault): self.port.dispatch({'operation': 'status'})
    def test_cancel_invalidates_inflight_ensure_before_lock(self):
        entered, proceed = threading.Event(), threading.Event()
        stopped, errors = threading.Event(), []
        def ensure(check):
            entered.set(); self.assertTrue(proceed.wait(2)); check()
            self.provider.alive = True
        self.provider.ensure = ensure
        def run_ensure():
            try: self.request('ensure')
            except Exception as exc: errors.append(exc)
        def run_stop():
            try: self.request('stop')
            except Exception as exc: errors.append(exc)
            finally: stopped.set()
        worker = threading.Thread(target=run_ensure); worker.start(); self.assertTrue(entered.wait(2))
        stopper = threading.Thread(target=run_stop); stopper.start()
        for _ in range(2000):
            with self.port.state_lock:
                if self.port.stopping: break
            threading.Event().wait(.001)
        self.assertTrue(self.port.stopping); self.assertEqual(self.port.generation, 1)
        self.assertFalse(stopped.is_set())
        with self.assertRaisesRegex(LifecycleFault, 'pending'): self.port._check(self.port.epoch, 1)
        proceed.set(); worker.join(2); stopper.join(2)
        self.assertFalse(worker.is_alive()); self.assertFalse(stopper.is_alive())
        self.assertEqual(len(errors), 1); self.assertIsInstance(errors[0], StaleLease)
        self.assertFalse(self.provider.alive); self.assertFalse(self.port.stopping)
    def test_concurrent_stop_not_duplicated(self):
        entered, release = threading.Event(), threading.Event()
        def stop(): entered.set(); release.wait(2); self.provider.alive = False
        self.provider.stop = stop
        worker = threading.Thread(target=lambda: self.request('stop')); worker.start()
        self.assertTrue(entered.wait(2))
        try:
            with self.assertRaisesRegex(LifecycleFault, 'already pending'): self.request('stop')
            self.assertEqual(self.port.generation, 1)
        finally: release.set(); worker.join(2)
    def test_samples_do_not_wait_for_lifecycle_lock(self):
        self.port.operation_lock.acquire()
        try: self.assertEqual(self.port.dispatch({'operation': 'sample'})['resources'], sample())
        finally: self.port.operation_lock.release()


class TelemetryTest(unittest.TestCase):
    def test_malformed_samples_rejected(self):
        bad = []
        for key, value in [('mem_available', True), ('vram_used', 65 * GIB),
                           ('pswpin', -1), ('psi_full_avg10', float('nan')),
                           ('psi_full_avg10', float('inf')), ('psi_full_avg10', True)]:
            s = sample(); s[key] = value; bad.append(s)
        bad += [{}, {**sample(), 'extra': 1}, []]
        for s in bad:
            with self.subTest(s=s), self.assertRaises(LifecycleFault): validate_sample(s)


class HostAdapterTest(unittest.TestCase):
    def adapter(self):
        with patch('inference.aceis_service.lifecycle_host.Backends') as ctor:
            backend = ctor.return_value; backend.cid = 'a' * 64
            return OwnedOllama({}, 'host-only-config'), backend
    def test_stop_verifies_ownership_before_and_state_after(self):
        adapter, backend = self.adapter()
        backend.inspect_owned.side_effect = [{'State': {'Running': True}}, {'State': {'Running': False}}]
        adapter.stop()
        backend.docker.assert_called_once_with('stop', '-t', '3', 'a' * 64, timeout=10)
        self.assertEqual(backend.inspect_owned.call_count, 2); backend.wait_idle.assert_called_once_with()
    def test_unconfirmed_stop_does_not_pass_physical_barrier(self):
        adapter, backend = self.adapter(); backend.inspect_owned.return_value = {'State': {'Running': True}}
        with self.assertRaises(ServiceError): adapter.stop()
        backend.wait_idle.assert_not_called()
    def test_foreign_container_not_stopped(self):
        adapter, backend = self.adapter(); backend.inspect_owned.side_effect = ServiceError('ownership changed')
        with self.assertRaises(ServiceError): adapter.stop()
        backend.docker.assert_not_called(); backend.wait_idle.assert_not_called()
    def test_idle_failure_propagates(self):
        adapter, backend = self.adapter(); backend.inspect_owned.return_value = {'State': {'Running': False}}
        backend.wait_idle.side_effect = ServiceError('physical idle unconfirmed')
        with self.assertRaises(ServiceError): adapter.stop()
    def test_reconcile_existing_container_after_restart(self):
        adapter, backend = self.adapter(); backend.cid = None
        backend.config = {'container_name': 'fixed-owned-name'}; backend.docker.return_value = 'b' * 12
        backend.inspect_owned.return_value = {'State': {'Running': True}}
        self.assertTrue(adapter.running()); self.assertEqual(backend.cid, 'b' * 12)
        backend.docker.assert_called_once_with('ps', '-aq', '--filter', 'name=^/fixed-owned-name$')
    def test_ambiguous_discovery_not_controlled(self):
        adapter, backend = self.adapter(); backend.cid = None
        backend.config = {'container_name': 'fixed-owned-name'}; backend.docker.return_value = 'a' * 12 + '\n' + 'b' * 12
        with self.assertRaises(ServiceError): adapter.running()
        backend.inspect_owned.assert_not_called()


if __name__ == '__main__': unittest.main()
