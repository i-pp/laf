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

"""Deterministic protocol tests: no OS socket or Docker is created here."""
import os
import queue
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from .core import Controller, GIB, ServiceError
from .lifecycle import HostLifecycle, LifecycleFault
from .lifecycle_client import LifecycleClient
from .lifecycle_uds import (FramedExchange, MAX_REQUEST, TransportError, decode, encode,
                            handle_connection, private_runtime, read_frame, verify_peer)
from .test_lifecycle import Provider, sample


class Stream:
    def __init__(self, incoming, outgoing, uid):
        self.incoming, self.outgoing, self.uid = incoming, outgoing, uid
        self.timeout, self.buffer, self.closed = 1, bytearray(), False
    def settimeout(self, timeout): self.timeout = timeout
    def getsockopt(self, *args): return struct.pack('3i', os.getpid(), self.uid, os.getgid())
    def sendall(self, raw):
        if self.closed: raise OSError('closed')
        self.outgoing.put(bytes(raw))
    def recv(self, count):
        if not self.buffer:
            try: item = self.incoming.get(timeout=self.timeout)
            except queue.Empty: raise socket.timeout() from None
            if item is None: return b''
            self.buffer.extend(item)
        out = bytes(self.buffer[:count]); del self.buffer[:count]; return out
    def close(self):
        if not self.closed: self.closed = True; self.outgoing.put(None)


class Loopback:
    """Queue-backed duplex stream, running the actual framing/auth/dispatch code."""
    def __init__(self, port, *, control=False, server_uid=None, client_uid=None, operation_timeout=1):
        self.port, self.control = port, control
        self.server_uid = os.getuid() if server_uid is None else server_uid
        self.client_uid = os.getuid() if client_uid is None else client_uid
        self.threads, self.connections = [], 0
        self.operation_timeout = operation_timeout
    def __call__(self, deadline):
        self.connections += 1
        a, b = queue.Queue(), queue.Queue()
        client, server = Stream(a, b, self.server_uid), Stream(b, a, self.client_uid)
        allowed = {'stop', 'status', 'sample', 'panic'} if self.control else {'ensure'}
        worker = threading.Thread(target=handle_connection,
                                  args=(server, self.port, os.getuid(), .2, self.operation_timeout, allowed), daemon=True)
        worker.start(); self.threads.append(worker); return client
    def exchange(self): return FramedExchange(self)


class ScriptStream:
    def __init__(self, data=b'', uid=None):
        self.data, self.sent, self.reads = bytearray(data), bytearray(), 0
        self.uid = os.getuid() if uid is None else uid
    def settimeout(self, timeout): self.timeout = timeout
    def getsockopt(self, *args): return struct.pack('3i', os.getpid(), self.uid, os.getgid())
    def recv(self, count):
        self.reads += 1; out = bytes(self.data[:count]); del self.data[:count]; return out
    def sendall(self, raw): self.sent.extend(raw)
    def close(self): pass


def framed(value):
    raw = encode(value); return struct.pack('!I', len(raw)) + raw


class FramingTest(unittest.TestCase):
    def test_duplicate_nonfinite_nested_and_nonobject_json_rejected(self):
        for raw in [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}', b'[]', b'\xff', b'[' * 1500 + b']' * 1500]:
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError): decode(raw)
    def test_oversize_header_does_not_read_body(self):
        stream = ScriptStream(struct.pack('!I', MAX_REQUEST + 1) + b'x' * 32)
        with self.assertRaises(ValueError): read_frame(stream, MAX_REQUEST, time.monotonic() + 1)
        self.assertEqual(stream.reads, 1)
    def test_partial_header_and_body_rejected(self):
        for data in [b'\0\0', struct.pack('!I', 4) + b'xx']:
            with self.assertRaises(TransportError): read_frame(ScriptStream(data), 10, time.monotonic() + 1)
    def test_peer_rejected_before_any_payload(self):
        stream = ScriptStream(framed({'operation': 'sample'}), uid=os.getuid() + 1)
        port = Mock(); handle_connection(stream, port, os.getuid())
        port.dispatch.assert_not_called(); self.assertEqual(stream.reads, 0); self.assertFalse(stream.sent)
    def test_peer_uid_authentication_does_not_assume_shared_pid_namespace(self):
        stream = Mock(); stream.getsockopt.return_value = struct.pack('3i', 0, os.getuid(), os.getgid())
        verify_peer(stream, os.getuid())
        stream.getsockopt.return_value = struct.pack('3i', 0, os.getuid() + 1, os.getgid())
        with self.assertRaises(TransportError): verify_peer(stream, os.getuid())

    def test_client_rejects_peer_before_send(self):
        stream = ScriptStream(uid=os.getuid() + 1)
        with self.assertRaises(TransportError): FramedExchange(lambda deadline: stream)({'operation': 'status'}, .1)
        self.assertFalse(stream.sent); self.assertEqual(stream.reads, 0)
    def test_no_retry_after_timeout(self):
        stream = Mock(); stream.getsockopt.return_value = struct.pack('3i', 1, os.getuid(), 1)
        stream.recv.side_effect = socket.timeout(); connector = Mock(return_value=stream)
        with self.assertRaises(TransportError): FramedExchange(connector)({'operation': 'ensure'}, .05)
        connector.assert_called_once(); self.assertEqual(stream.sendall.call_count, 1)
    def test_response_schema_rejects_extra_fields_and_nonboolean_ok(self):
        for value in [{'ok': 1}, {'ok': True, 'result': {}, 'extra': 1}, {'ok': False, 'error': 'private detail'}, {'ok': False, 'error': []}]:
            with self.subTest(value=value), self.assertRaises(TransportError):
                FramedExchange(lambda deadline: ScriptStream(framed(value)))({'operation': 'status'}, .1)
    def test_host_errors_are_sanitized(self):
        port = Mock(); port.dispatch.side_effect = OSError('secret /host/private/path')
        stream = ScriptStream(framed({'operation': 'sample'})); handle_connection(stream, port, os.getuid())
        self.assertNotIn(b'secret', stream.sent); self.assertIn(b'"internal"', stream.sent)
    def test_control_lane_rejects_ensure_without_dispatch(self):
        port = Mock(); stream = ScriptStream(framed({'operation': 'ensure'}))
        handle_connection(stream, port, os.getuid(), allowed_operations={'stop', 'status', 'sample'})
        port.dispatch.assert_not_called(); self.assertIn(b'"invalid"', stream.sent)
    def test_timeout_is_absolute_across_partial_frame(self):
        clock = [0.0]
        stream = ScriptStream(framed({'operation': 'status'}))
        original = stream.recv
        def slow(count): clock[0] += .06; return original(min(count, 2))
        stream.recv = slow
        with patch('inference.aceis_service.lifecycle_uds.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError): read_frame(stream, MAX_REQUEST, .1)
        self.assertEqual(stream.reads, 2)
    def test_private_directory_rejects_public_and_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root); os.chmod(path, 0o755)
            with self.assertRaises(TransportError): private_runtime(path, os.getuid())
            os.chmod(path, 0o700); link = path / 'alias'; link.symlink_to(path, target_is_directory=True)
            with self.assertRaises(TransportError): private_runtime(link, os.getuid())
    def test_lifecycle_lock_wait_has_deadline(self):
        port = HostLifecycle(Provider(), sample); port.operation_lock.acquire()
        start = time.monotonic()
        try:
            with self.assertRaises(LifecycleFault): port.dispatch({'operation': 'status'}, deadline=start + .02)
        finally: port.operation_lock.release()
        self.assertLess(time.monotonic() - start, .2)


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.provider = Provider(); self.port = HostLifecycle(self.provider, sample)
        self.main = Loopback(self.port); self.control = Loopback(self.port, control=True)
        self.client = LifecycleClient(self.main.exchange(), self.control.exchange(), timeout=.5, read_timeout=.2)
    def test_bootstrap_requires_explicit_physical_stop(self):
        self.client.reconcile()
        with self.assertRaises(ServiceError): self.client.ensure()
        self.client.stop(); self.client.ensure(); self.assertTrue(self.provider.alive)
    def test_fresh_host_telemetry(self):
        self.assertEqual(self.client.read_resources(), sample())
        changed = sample(); changed['mem_available'] = 21 * GIB; self.port.read_resources = lambda: changed
        self.assertEqual(self.client.read_resources()['mem_available'], 21 * GIB)
    def test_lost_stop_ack_reconciles_without_duplicate_stop(self):
        self.client.stop(); base = self.provider.calls.count('stop')
        real = self.client.control_exchange
        def drop(request, timeout):
            result = real(request, timeout)
            if request['operation'] == 'stop': raise TransportError('lost ACK')
            return result
        self.client.control_exchange = drop
        with self.assertRaises(TransportError): self.client.stop()
        self.assertTrue(self.client.uncertain)
        self.client.control_exchange = real; self.client.reconcile()
        self.assertFalse(self.client.uncertain); self.assertEqual(self.provider.calls.count('stop'), base + 1)
    def test_uncertain_ensure_never_automatically_replayed(self):
        self.client.stop(); real = self.client.exchange
        def drop(request, timeout): real(request, timeout); raise TransportError('lost ACK')
        self.client.exchange = drop
        with self.assertRaises(TransportError): self.client.ensure()
        self.client.reconcile(); self.assertTrue(self.client.uncertain)
        with self.assertRaises(ServiceError): self.client.ensure()
        self.assertEqual(self.provider.calls.count('ensure'), 1)
        self.client.stop(); self.assertFalse(self.provider.alive)
    def test_cancel_does_not_wait_for_status_lock(self):
        self.client.stop(); entered, release = threading.Event(), threading.Event(); errors = []
        def ensure(check):
            entered.set(); release.wait(1); self.provider.alive = True; check()
        self.provider.ensure = ensure
        def start():
            try: self.client.ensure()
            except Exception as exc: errors.append(exc)
        worker = threading.Thread(target=start); worker.start(); self.assertTrue(entered.wait(1))
        stopper = threading.Thread(target=self.client.stop); stopper.start()
        limit = time.monotonic() + .2
        while not self.port.stopping and time.monotonic() < limit: time.sleep(.001)
        self.assertTrue(self.port.stopping); release.set(); worker.join(1); stopper.join(1)
        self.assertFalse(worker.is_alive()); self.assertFalse(stopper.is_alive())
        self.assertEqual(len(errors), 1); self.assertFalse(self.client.uncertain); self.assertFalse(self.provider.alive)
    def test_client_io_timeout_is_bounded_and_latches(self):
        self.client.stop(); entered = threading.Event()
        def slow(check): entered.set(); time.sleep(.15); self.provider.alive = True
        self.provider.ensure = slow; self.client.timeout = .03
        start = time.monotonic()
        with self.assertRaises(TransportError): self.client.ensure()
        self.assertLess(time.monotonic() - start, .12); self.assertTrue(self.client.uncertain)
        self.assertEqual(self.main.connections, 1)
        time.sleep(.17); self.client.timeout = .5; self.client.stop()
    def test_reconcile_cannot_clear_latch_with_pre_ensure_stop_receipt(self):
        self.client.stop(); captured, release = threading.Event(), threading.Event(); errors = []
        real_control, real_main = self.client.control_exchange, self.client.exchange
        def delayed_status(request, timeout):
            result = real_control(request, timeout)
            if request['operation'] == 'status': captured.set(); release.wait(1)
            return result
        self.client.control_exchange = delayed_status
        def reconcile():
            try: self.client.reconcile()
            except Exception as exc: errors.append(exc)
        worker = threading.Thread(target=reconcile); worker.start(); self.assertTrue(captured.wait(1))
        def lost_ensure_ack(request, timeout):
            real_main(request, timeout); raise TransportError('lost ACK')
        self.client.exchange = lost_ensure_ack
        with self.assertRaises(TransportError): self.client.ensure()
        self.assertTrue(self.client.uncertain)
        release.set(); worker.join(1)
        self.assertEqual(len(errors), 1); self.assertTrue(self.client.uncertain)
        with self.assertRaises(ServiceError): self.client.ensure()
        self.assertEqual(self.provider.calls.count('ensure'), 1)

    def test_reconcile_and_second_ensure_rejected_while_mutation_inflight(self):
        self.client.stop(); entered, release = threading.Event(), threading.Event(); errors = []
        real = self.client.exchange
        def delayed_ensure(request, timeout):
            entered.set(); release.wait(1); real(request, timeout); raise TransportError('lost ACK')
        self.client.exchange = delayed_ensure
        def start():
            try: self.client.ensure()
            except Exception as exc: errors.append(exc)
        worker = threading.Thread(target=start); worker.start(); self.assertTrue(entered.wait(1))
        with self.assertRaises(ServiceError): self.client.reconcile()
        with self.assertRaises(ServiceError): self.client.ensure()
        self.assertEqual(self.provider.calls.count('ensure'), 0)
        release.set(); worker.join(1)
        self.assertEqual(len(errors), 1); self.assertTrue(self.client.uncertain)
        self.client.reconcile(); self.assertTrue(self.client.uncertain)
        with self.assertRaises(ServiceError): self.client.ensure()
        self.assertEqual(self.provider.calls.count('ensure'), 1)

    def test_host_telemetry_epoch_change_latches_instead_of_rebaselining(self):
        self.client.stop(); old_epoch = self.client.epoch
        self.port.epoch = 'b' * 32 if old_epoch != 'b' * 32 else 'c' * 32
        with self.assertRaisesRegex(ServiceError, 'epoch changed'): self.client.read_resources()
        self.assertTrue(self.client.uncertain); self.assertTrue(self.client.need_reconcile)
        with self.assertRaises(ServiceError): self.client.ensure()
        self.assertEqual(self.client.epoch, old_epoch)
        self.client.stop(); self.assertEqual(self.client.epoch, self.port.epoch)

    def test_unknown_ensure_requires_new_stop_even_if_host_dispatch_is_delayed(self):
        self.client.stop(); queued, release = [], threading.Event()
        def delayed(request, timeout):
            queued.append(dict(request)); raise TransportError('request outcome unknown')
        self.client.exchange = delayed
        with self.assertRaises(TransportError): self.client.ensure()
        self.client.reconcile(); self.assertTrue(self.client.uncertain)
        with self.assertRaises(ServiceError): self.client.ensure()
        # The old receipt cannot clear the latch while this request can arrive.
        self.port.dispatch(queued[0]); self.assertTrue(self.provider.alive)
        self.assertTrue(self.client.uncertain)
        self.client.stop(); self.assertFalse(self.provider.alive)

    def test_new_stop_invalidates_not_yet_dispatched_unknown_ensure(self):
        self.client.stop(); queued = []
        def delayed(request, timeout): queued.append(dict(request)); raise TransportError('unknown')
        self.client.exchange = delayed
        with self.assertRaises(TransportError): self.client.ensure()
        self.client.stop()
        with self.assertRaises(ServiceError): self.port.dispatch(queued[0])
        self.assertFalse(self.provider.alive); self.assertFalse(self.client.uncertain)

    def test_invalid_telemetry_never_falls_back_to_core_proc(self):
        self.port.read_resources = lambda: {}
        with self.assertRaises(ServiceError): self.client.read_resources()
    def test_stopped_status_without_barrier_receipt_is_not_safe(self):
        self.client.reconcile(); self.assertTrue(self.client.uncertain)
        self.assertIsNone(self.port.stopped_generation)


class RoutedBackend:
    def __init__(self, client):
        self.client, self.calls, self.resident = client, [], True
    def generate(self, job, check):
        self.calls.append(job.model); self.client.ensure(check)
        for _ in range(10): check(); time.sleep(.005)
        return {'content': 'fake local generation'}
    def abort(self): self.client.stop(); self.resident = False
    def close(self): self.abort()
    def start_flash(self, check): check(); self.resident = True
    def health(self): return {'flash_resident': self.resident}


class ControllerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.provider = Provider(); self.resources = sample()
        self.port = HostLifecycle(self.provider, lambda: dict(self.resources))
        main, control = Loopback(self.port), Loopback(self.port, control=True)
        self.client = LifecycleClient(main.exchange(), control.exchange(), timeout=1, read_timeout=.2)
        self.client.stop(); self.backend = RoutedBackend(self.client)
        self.controller = Controller(self.backend, self.client.read_resources)
    def tearDown(self): self.controller.close()
    def submit(self, model='coder-next'):
        return self.controller.submit({'model': model, 'messages': [{'role': 'user', 'content': 'fake input'}]})
    def test_controller_completes_using_framed_host_lifecycle_and_telemetry(self):
        job = self.submit(); self.assertTrue(job.done.wait(2)); self.assertEqual(job.status, 'completed')
        self.assertEqual(self.controller.latest['mem_available'], self.resources['mem_available'])
        self.assertEqual(self.provider.calls.count('ensure'), 1)
    def test_physical_stop_ack_holds_next_queued_job(self):
        entered, release = threading.Event(), threading.Event()
        def stop(): entered.set(); release.wait(2); self.provider.alive = False
        self.provider.stop = stop
        first = self.submit()
        while not self.backend.calls: time.sleep(.001)
        second = self.submit('gpt-oss-120b'); first.cancel.set()
        self.assertTrue(entered.wait(1)); self.assertNotIn('gpt-oss-120b', self.backend.calls)
        release.set(); self.assertTrue(second.done.wait(2)); self.assertEqual(second.status, 'completed')
        self.assertEqual(first.status, 'cancelled')
    def test_host_resource_pressure_trips_controller_and_stops_provider(self):
        self.resources['mem_available'] = 7 * GIB
        until = time.monotonic() + 2
        while not self.controller.fault and time.monotonic() < until: time.sleep(.01)
        self.assertIsNotNone(self.controller.fault)
        with self.assertRaises(ServiceError): self.submit()
        self.assertFalse(self.provider.alive)
