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

"""Offline security tests: no listener or provider is started."""
import os
import socket
import stat
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from laf_mcp.router import Router, RouterError


def response(body=b'{}', headers=None, status=b'200 OK'):
    headers = headers or b'Content-Length: ' + str(len(body)).encode()
    return b'HTTP/1.0 ' + status + b'\r\n' + headers + b'\r\n\r\n' + body


class FakeSocket:
    def __init__(self, raw=None):
        self.raw = response() if raw is None else raw
        self.sent = []
        self.closed = False
        self.uid = os.getuid()
    def settimeout(self, value):
        assert 0 < value <= 4
    def connect(self, path):
        self.path = path
    def getsockopt(self, *args):
        return struct.pack('3i', 123, self.uid, os.getgid())
    def sendall(self, data):
        self.sent.append(data)
    def recv(self, size):
        result, self.raw = self.raw[:size], self.raw[size:]
        return result
    def close(self):
        self.closed = True


def metadata(path):
    mode = stat.S_IFSOCK | 0o600 if path == '/private/router.sock' else stat.S_IFDIR | (0o700 if path == '/private' else 0o755)
    return SimpleNamespace(st_mode=mode, st_uid=os.getuid(), st_dev=1, st_ino=2)


class RouterTests(unittest.TestCase):
    def run_request(self, raw=None, method='GET', route='/health', body=None, sock=None):
        sock = sock or FakeSocket(raw)
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket', return_value=sock):
            try:
                return Router('/private/router.sock').request(method, route, body), sock
            finally:
                self.assertTrue(sock.closed)
    def test_success(self):
        result, sock = self.run_request(response(b'{"status":"ready"}'))
        self.assertEqual(result, (200, {'status': 'ready'}))
        self.assertIn(b'Host: localhost\r\n', sock.sent[0])
        self.assertNotIn(b'Origin', sock.sent[0])
    def test_post_json(self):
        result, sock = self.run_request(method='POST', route='/v1/jobs', body={'messages': []})
        self.assertIn(b'Content-Length: 15\r\n', sock.sent[0])
    def test_route_allowlist(self):
        for method, route in [('POST','/v1/recover'),('GET','http://evil/'),('GET','/health?x=1'),('DELETE','/v1/jobs/x'),('GET','/health\r\nx: y')]:
            with self.subTest(route=route), self.assertRaises(RouterError) as caught:
                Router('/private/router.sock').request(method, route)
            self.assertFalse(caught.exception.sent)
    def test_malformed_responses(self):
        for raw in [response(b'{"a":1,"a":2}'), response(b'{"a":NaN}'), response(b'{"a":1e999}'), response(b'[]'), response(headers=b'Content-Length: 9'), response(headers=b'Transfer-Encoding: chunked'), response(headers=b'Content-Length: 2\r\nContent-Length: 2'), response(status=b'302 Found'), response(status=b'bad'), response(b'{"a":' + b'['*33 + b'0' + b']'*33 + b'}'), response(headers=b'X-Large: '+b'a'*16384)]:
            with self.subTest(raw=raw[:80]), self.assertRaises(RouterError) as caught:
                self.run_request(raw)
            self.assertTrue(caught.exception.sent)
            self.assertEqual(str(caught.exception), 'LAF router request failed')
    def test_peer_uid(self):
        sock = FakeSocket(); sock.uid += 1
        with self.assertRaises(RouterError) as caught:
            self.run_request(sock=sock)
        self.assertFalse(caught.exception.sent)
        self.assertEqual(sock.sent, [])
    def test_insecure_paths(self):
        for path in ['relative.sock', '/private/../router.sock', '/private/./router.sock']:
            with self.assertRaises(RouterError):
                Router(path).request('GET','/health')
        for mode in [stat.S_IFLNK|0o777, stat.S_IFSOCK|0o666, stat.S_IFREG|0o600]:
            def bad(path):
                value = metadata(path)
                if path.endswith('.sock'): value.st_mode = mode
                return value
            with patch('laf_mcp.router.os.lstat', side_effect=bad), self.assertRaises(RouterError) as caught:
                Router('/private/router.sock').request('GET','/health')
            self.assertFalse(caught.exception.sent)
    def test_identity_pinning(self):
        router = Router('/private/router.sock')
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket', side_effect=lambda *a: FakeSocket()):
            router.request('GET','/health')
        def replacement(path):
            value = metadata(path); value.st_ino = 3; return value
        with patch('laf_mcp.router.os.lstat', side_effect=replacement), self.assertRaises(RouterError) as caught:
            router.request('GET','/health')
        self.assertFalse(caught.exception.sent)
    def test_bounded_body(self):
        with self.assertRaises(RouterError) as caught:
            Router('/private/router.sock').request('POST','/v1/jobs', {'x':'x'*32768})
        self.assertFalse(caught.exception.sent)
    def test_busy_and_delete_independence(self):
        router = Router('/private/router.sock')
        for _ in range(3): self.assertTrue(router._requests.acquire(False))
        with self.assertRaises(RouterError) as caught:
            router.request('GET','/health')
        self.assertFalse(caught.exception.sent)
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket', return_value=FakeSocket()):
            self.assertEqual(router.request('DELETE','/v1/jobs/'+'a'*32), (200, {}))
    def test_send_failure_is_uncertain(self):
        sock = FakeSocket()
        def fail(data):
            raise OSError('secret backend/path/prompt')
        sock.sendall = fail
        with self.assertRaises(RouterError) as caught:
            self.run_request(sock=sock)
        self.assertTrue(caught.exception.sent)
        self.assertEqual(str(caught.exception), 'LAF router request failed')
    def test_connect_failure_not_sent(self):
        sock = FakeSocket()
        def fail(path):
            raise OSError('secret path')
        sock.connect = fail
        with self.assertRaises(RouterError) as caught:
            self.run_request(sock=sock)
        self.assertFalse(caught.exception.sent)
    def test_post_connect_replacement_not_sent(self):
        sock = FakeSocket()
        changed = [False]
        def connect(path):
            changed[0] = True
        sock.connect = connect
        def swapped(path):
            value = metadata(path)
            if changed[0] and path.endswith('.sock'): value.st_ino = 9
            return value
        with patch('laf_mcp.router.os.lstat', side_effect=swapped), patch('laf_mcp.router.socket.socket', return_value=sock), self.assertRaises(RouterError) as caught:
            Router('/private/router.sock').request('POST','/v1/jobs', {})
        self.assertFalse(caught.exception.sent)
        self.assertEqual(sock.sent, [])
        self.assertTrue(sock.closed)
    def test_parent_permissions_and_ancestor_symlink(self):
        for target, mode in [('/private', stat.S_IFDIR | 0o755), ('/private', stat.S_IFLNK | 0o700)]:
            def bad(path):
                value = metadata(path)
                if path == target: value.st_mode = mode
                return value
            with patch('laf_mcp.router.os.lstat', side_effect=bad), self.assertRaises(RouterError):
                Router('/private/router.sock').request('GET','/health')
    def test_deadline_exhaustion_closes_socket(self):
        sock = FakeSocket()
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket', return_value=sock), patch('laf_mcp.router.time.monotonic', side_effect=[0, 1, 2, 5]), self.assertRaises(RouterError) as caught:
            Router('/private/router.sock').request('GET','/health')
        self.assertTrue(caught.exception.sent)
        self.assertTrue(sock.closed)
    def test_response_size_limit(self):
        with self.assertRaises(RouterError):
            self.run_request(response(headers=b'Content-Length: 2097153'))
    def test_read_validation_cannot_use_cancellation_reserve(self):
        router = Router('/private/router.sock')
        for _ in range(3): self.assertTrue(router._requests.acquire(False))
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket') as factory, self.assertRaises(RouterError):
            router.validate_endpoint()
        factory.assert_not_called()
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket', return_value=FakeSocket()):
            self.assertIsInstance(router.validate_endpoint(cancel=True), str)
    def test_cancel_validation_cannot_use_read_reserve(self):
        router = Router('/private/router.sock')
        self.assertTrue(router._deletes.acquire(False))
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket') as factory, self.assertRaises(RouterError):
            router.validate_endpoint(cancel=True)
        factory.assert_not_called()
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket', return_value=FakeSocket()):
            self.assertIsInstance(router.validate_endpoint(), str)
    def test_endpoint_key_requires_authentication(self):
        with self.assertRaises(RouterError):
            _ = Router('/private/router.sock').endpoint_key
    def test_endpoint_key_stable_authenticated(self):
        router = Router('/private/router.sock')
        sockets = []
        def fresh(*args):
            sock = FakeSocket(); sockets.append(sock); return sock
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket', side_effect=fresh):
            key = router.validate_endpoint()
            self.assertEqual(key, router.endpoint_key)
            self.assertEqual(Router('/private/router.sock').validate_endpoint(key), key)
        self.assertNotIn('/private', key)
        self.assertTrue(all(s.closed and not s.sent for s in sockets))
    def test_restart_replacement_rejects_old_receipt(self):
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket', return_value=FakeSocket()):
            key = Router('/private/router.sock').validate_endpoint()
        def replacement(path):
            value = metadata(path)
            if path.endswith('.sock'): value.st_ino = 99
            return value
        sock = FakeSocket()
        with patch('laf_mcp.router.os.lstat', side_effect=replacement), patch('laf_mcp.router.socket.socket', return_value=sock), self.assertRaises(RouterError) as caught:
            Router('/private/router.sock').validate_endpoint(key)
        self.assertFalse(caught.exception.sent)
        self.assertTrue(sock.closed)
        self.assertEqual(sock.sent, [])
    def test_lone_surrogates_rejected(self):
        for body in [b'{"x":"\\ud800"}', b'{"\\ud800":1}']:
            with self.assertRaises(RouterError):
                self.run_request(response(body))
    def test_root_owned_sticky_tmp_allowed(self):
        def temp_metadata(path):
            if path == '/tmp':
                return SimpleNamespace(st_mode=stat.S_IFDIR|0o1777, st_uid=0, st_dev=1, st_ino=11)
            translated = path.replace('/tmp/private', '/private')
            return metadata(translated)
        with patch('laf_mcp.router.os.lstat', side_effect=temp_metadata), patch('laf_mcp.router.socket.socket', return_value=FakeSocket()):
            self.assertIsInstance(Router('/tmp/private/router.sock').validate_endpoint(), str)
    def test_failed_authentication_does_not_expose_key(self):
        router = Router('/private/router.sock')
        sock = FakeSocket(); sock.uid += 1
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket', return_value=sock), self.assertRaises(RouterError):
            router.validate_endpoint()
        with self.assertRaises(RouterError):
            _ = router.endpoint_key
    def test_same_inode_new_peer_rejects_receipt(self):
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket', return_value=FakeSocket()):
            key = Router('/private/router.sock').validate_endpoint()
        sock = FakeSocket()
        sock.getsockopt = lambda *args: struct.pack('3i', 456, os.getuid(), os.getgid())
        with patch('laf_mcp.router.os.lstat', side_effect=metadata), patch('laf_mcp.router.socket.socket', return_value=sock), self.assertRaises(RouterError):
            Router('/private/router.sock').validate_endpoint(key)
        self.assertEqual(sock.sent, [])
    def test_untrusted_ancestors(self):
        for owner, mode in [(os.getuid()+1000, stat.S_IFDIR|0o755), (os.getuid(), stat.S_IFDIR|0o777)]:
            def unsafe(path):
                value = metadata(path)
                if path == '/': value.st_uid, value.st_mode = owner, mode
                return value
            with patch('laf_mcp.router.os.lstat', side_effect=unsafe), self.assertRaises(RouterError):
                Router('/private/router.sock').request('GET','/health')

if __name__ == '__main__': unittest.main()
