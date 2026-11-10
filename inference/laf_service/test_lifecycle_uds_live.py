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

"""Real Linux AF_UNIX/peer-UID tests; fake providers only, no Docker or GPU."""
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from .core import Controller, ServiceError
from .lifecycle import HostLifecycle
from .lifecycle_client import LifecycleClient
from .lifecycle_uds import (CONTROL_SOCKET_NAME, SOCKET_NAME, FramedExchange,
                            TransportError, UnixConnector, UnixLifecycleServer)
from .test_lifecycle import Provider, sample
from .test_lifecycle_uds import RoutedBackend


class RealUnixTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='laf-uds-')
        self.root = Path(self.directory.name); os.chmod(self.root, 0o700)
        self.provider = Provider(); self.port = HostLifecycle(self.provider, sample)
        self.servers, self.threads = [], []
        try:
            for control in [False, True]:
                server = UnixLifecycleServer(self.port, self.root, workers=2,
                                             io_timeout=.3, operation_timeout=1, control=control)
                self.servers.append(server)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start(); self.threads.append(thread)
        except BaseException:
            self.cleanup(); raise
        main = FramedExchange(UnixConnector(self.root))
        control = FramedExchange(UnixConnector(self.root, control=True))
        self.client = LifecycleClient(main, control, timeout=.7, read_timeout=.3)
    def cleanup(self):
        for server in self.servers: server.close()
        for thread in self.threads: thread.join(1)
        self.directory.cleanup()
    def tearDown(self): self.cleanup()
    def test_real_peer_authenticated_controller_and_host_telemetry(self):
        self.client.stop(); backend = RoutedBackend(self.client)
        controller = Controller(backend, self.client.read_resources)
        try:
            job = controller.submit({'model': 'coder-next', 'messages': [{'role': 'user', 'content': 'fake input'}]})
            self.assertTrue(job.done.wait(2)); self.assertEqual(job.status, 'completed')
            self.assertEqual(controller.latest, sample())
        finally: controller.close()
    def test_socket_modes_and_cleanup_identity(self):
        for name in [SOCKET_NAME, CONTROL_SOCKET_NAME]:
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        path = self.servers[0].path; path.unlink(); path.write_text('replacement')
        self.servers[0].close(); self.assertEqual(path.read_text(), 'replacement')
    def test_preexisting_endpoint_is_never_replaced(self):
        path = self.root / 'unused'; path.mkdir(mode=0o700); endpoint = path / SOCKET_NAME
        endpoint.write_text('keep')
        with self.assertRaises(TransportError): UnixLifecycleServer(self.port, path)
        self.assertEqual(endpoint.read_text(), 'keep')
    def test_client_wrong_expected_uid_rejected_before_mutation(self):
        exchange = FramedExchange(UnixConnector(self.root), expected_uid=os.getuid() + 1)
        with self.assertRaises(TransportError): exchange({'operation': 'ensure'}, .2)
        self.assertEqual(self.provider.calls, [])
    def test_server_rejects_wrong_peer_uid_before_dispatch(self):
        self.servers[0].uid = os.getuid() + 1
        exchange = FramedExchange(UnixConnector(self.root))
        with self.assertRaises(ServiceError): exchange({'operation': 'ensure'}, .2)
        self.assertEqual(self.provider.calls, [])
    def test_cancel_lane_survives_saturated_start_workers(self):
        self.client.stop(); entered, release = threading.Event(), threading.Event(); errors = []
        def ensure(check): entered.set(); release.wait(1); self.provider.alive = True; check()
        self.provider.ensure = ensure
        def start():
            try: self.client.ensure()
            except Exception as exc: errors.append(exc)
        old_identity = {'epoch': self.client.epoch, 'generation': self.client.generation}
        def second_start():
            try:
                FramedExchange(UnixConnector(self.root))({'operation': 'ensure', **old_identity}, .7)
            except Exception as exc: errors.append(exc)
        a, b = threading.Thread(target=start), threading.Thread(target=second_start)
        a.start(); self.assertTrue(entered.wait(1)); b.start()
        stopper = threading.Thread(target=self.client.stop); stopper.start()
        until = time.monotonic() + .3
        while not self.port.stopping and time.monotonic() < until: time.sleep(.001)
        self.assertTrue(self.port.stopping)
        release.set()
        for worker in [a, b, stopper]: worker.join(2); self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 2); self.assertFalse(self.client.uncertain)
        self.assertFalse(self.provider.alive)
    def test_io_timeout_is_unknown_not_automatic_retry(self):
        self.client.stop()
        def slow(check): time.sleep(.15); self.provider.alive = True
        self.provider.ensure = slow; self.client.timeout = .03
        start = time.monotonic()
        with self.assertRaises(TransportError): self.client.ensure()
        self.assertLess(time.monotonic() - start, .12); self.assertTrue(self.client.uncertain)
        with self.assertRaises(ServiceError): self.client.ensure()
        time.sleep(.17); self.client.timeout = .7; self.client.stop()


if __name__ == '__main__': unittest.main()
