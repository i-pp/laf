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

"""Bounded, owner-only HTTP/1.0 transport for the public LAF API.

No retries: a failure after send begins has an uncertain remote outcome.
Endpoint keys bind observed filesystem and peer identity, not a Router epoch: the
public API offers no epoch attestation; inode and process IDs can eventually recur.
"""
import hashlib
import json
import math
import os
import re
import socket
import stat
import struct
import threading
import time

from .contracts import strict_json

_MAX_BODY = 32768
_MAX_RESPONSE = 2 * 1024 * 1024
_MAX_HEADERS = 16384
_JOB = re.compile(r'/v1/jobs/[0-9a-f]{32}\Z')


class RouterError(Exception):
    def __init__(self, *, sent=False):
        super().__init__('LAF router request failed')
        self.sent = bool(sent)


def _depth(value, level=0):
    if level > 32:
        raise ValueError()
    if isinstance(value, dict):
        for item in value.values():
            _depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, level + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError()



class Router:
    def __init__(self, socket_path):
        self._path = socket_path
        self._identity = None
        self._identity_lock = threading.Lock()
        self._requests = threading.BoundedSemaphore(3)
        self._deletes = threading.BoundedSemaphore(1)

    @property
    def endpoint_key(self):
        with self._identity_lock:
            if self._identity is None:
                raise RouterError()
            raw = json.dumps([self._path, self._identity], separators=(',', ':')).encode('utf-8')
            return hashlib.sha256(raw).hexdigest()

    def _check_identity(self):
        path = self._path
        if not isinstance(path, str) or not path.startswith('/') or '\x00' in path:
            raise ValueError()
        parts = path.split('/')[1:]
        if not parts or any(part in ('', '.', '..') for part in parts):
            raise ValueError()
        uid = os.getuid()
        current = ''
        parent = os.lstat('/')
        self._check_ancestor('/', parent, uid)
        for index, part in enumerate(parts):
            current += '/' + part
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError()
            if index < len(parts) - 1:
                self._check_ancestor(current, info, uid)
                parent = info
        if parent.st_uid != uid or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError()
        identity = (info.st_dev, info.st_ino, info.st_uid, parent.st_dev, parent.st_ino, parent.st_uid)
        with self._identity_lock:
            if self._identity is not None and self._identity[0] != identity:
                raise ValueError()
        return identity

    @staticmethod
    def _check_ancestor(path, info, uid):
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid):
            raise ValueError()
        writable = stat.S_IMODE(info.st_mode) & 0o022
        trusted_tmp = path == '/tmp' and info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if writable and not trusted_tmp:
            raise ValueError()

    def _connect(self, deadline):
        identity = self._check_identity()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self._remaining(deadline))
            sock.connect(self._path)
            peer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
            pid, uid, _gid = struct.unpack('3i', peer)
            if uid != os.getuid() or pid <= 0:
                raise ValueError()
            if self._check_identity() != identity:
                raise ValueError()
            observed = (identity, pid, uid)
            with self._identity_lock:
                if self._identity is None:
                    self._identity = observed
                elif self._identity != observed:
                    raise ValueError()
            return sock
        except BaseException:
            sock.close()
            raise

    def validate_endpoint(self, expected_key=None, *, cancel=False):
        """Authenticate without HTTP; reject stale receipt identity before job I/O.

        Cancellation validation alone uses the independent cancellation lane.
        Read validation cannot consume that reserve. No HTTP bytes are sent.
        """
        slot = self._deletes if cancel else self._requests
        if not slot.acquire(blocking=False):
            raise RouterError()
        sock = None
        deadline = time.monotonic() + 4.0
        try:
            sock = self._connect(deadline)
            key = self.endpoint_key
            if expected_key is not None and expected_key != key:
                raise ValueError()
            self._remaining(deadline)
            return key
        except Exception:
            raise RouterError() from None
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            slot.release()

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        return remaining

    def _response(self, sock, deadline):
        data = bytearray()
        def receive(size):
            sock.settimeout(self._remaining(deadline))
            chunk = sock.recv(size)
            if not chunk:
                raise ValueError()
            return chunk
        while b'\r\n\r\n' not in data:
            if len(data) >= _MAX_HEADERS:
                raise ValueError()
            data.extend(receive(min(4096, _MAX_HEADERS - len(data))))
        head, body = bytes(data).split(b'\r\n\r\n', 1)
        if len(head) + 4 > _MAX_HEADERS:
            raise ValueError()
        lines = head.split(b'\r\n')
        match = re.fullmatch(rb'HTTP/1\.[01] ([1-5][0-9]{2}) [\x20-\x7e]*', lines[0])
        if not match:
            raise ValueError()
        status = int(match.group(1))
        if status < 200 or 300 <= status < 400:
            raise ValueError()
        headers = {}
        for line in lines[1:]:
            if b':' not in line:
                raise ValueError()
            key, value = line.split(b':', 1)
            if not re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key):
                raise ValueError()
            if any(byte < 32 and byte != 9 or byte == 127 for byte in value):
                raise ValueError()
            key = key.lower()
            if key in headers:
                raise ValueError()
            headers[key] = value.strip()
        if b'transfer-encoding' in headers or b'content-length' not in headers:
            raise ValueError()
        length_raw = headers[b'content-length']
        if not re.fullmatch(rb'[0-9]{1,10}', length_raw):
            raise ValueError()
        length = int(length_raw)
        if length > _MAX_RESPONSE or len(body) > length:
            raise ValueError()
        body = bytearray(body)
        while len(body) < length:
            body.extend(receive(min(65536, length - len(body))))
        result = strict_json(bytes(body), max_bytes=_MAX_RESPONSE)
        self._remaining(deadline)
        return status, result

    def request(self, method, route, body=None):
        sent = False
        sock = None
        slot = self._deletes if method == 'DELETE' else self._requests
        if not slot.acquire(blocking=False):
            raise RouterError()
        deadline = time.monotonic() + 4.0
        try:
            allowed = (method == 'GET' and (route in ('/health', '/v1/capabilities', '/v1/models') or _JOB.fullmatch(route))) or (method == 'POST' and route == '/v1/jobs') or (method == 'DELETE' and _JOB.fullmatch(route))
            if not allowed or (method != 'POST' and body is not None):
                raise ValueError()
            raw = b''
            if method == 'POST':
                if not isinstance(body, dict):
                    raise ValueError()
                _depth(body)
                raw = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
                if len(raw) > _MAX_BODY:
                    raise ValueError()
            request = f'{method} {route} HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n'.encode('ascii')
            if method == 'POST':
                request += f'Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n'.encode('ascii')
            request += b'\r\n' + raw
            sock = self._connect(deadline)
            sock.settimeout(self._remaining(deadline))
            sent = True
            sock.sendall(request)
            return self._response(sock, deadline)
        except Exception:
            raise RouterError(sent=sent) from None
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            slot.release()
