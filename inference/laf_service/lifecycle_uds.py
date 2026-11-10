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

"""Owner-authenticated, bounded local transport for the fixed lifecycle port.

No TCP listener, Docker API forwarding, automatic retries or socket replacement.
Socket I/O and queued-operation waits have absolute deadlines. A provider that
ignores its cooperative deadline still needs separate process supervision.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import socket
import stat
import struct
import threading
import time
from .core import ServiceError
from .lifecycle import LifecycleFault, StaleLease

MAX_REQUEST = 4096
MAX_RESPONSE = 8192
SOCKET_NAME = 'lifecycle.sock'
CONTROL_SOCKET_NAME = 'lifecycle-control.sock'


class TransportError(ServiceError):
    pass


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError('lifecycle transport deadline exceeded')
    return value


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON field')
        value[key] = item
    return value


def decode(raw):
    def reject(_):
        raise ValueError('non-finite JSON number')
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('non-finite JSON number')
        return number
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                            parse_constant=reject, parse_float=finite_float)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('invalid lifecycle JSON') from exc
    if type(result) is not dict:
        raise ValueError('lifecycle object required')
    return result


def encode(value):
    return json.dumps(value, allow_nan=False, separators=(',', ':')).encode('utf-8')


def read_frame(stream, maximum, deadline):
    def read(count):
        out = bytearray()
        while len(out) < count:
            stream.settimeout(remaining(deadline))
            chunk = stream.recv(count - len(out))
            if not chunk:
                raise TransportError('incomplete lifecycle frame')
            out.extend(chunk)
        return bytes(out)
    length = struct.unpack('!I', read(4))[0]
    if not 1 <= length <= maximum:
        raise ValueError('lifecycle frame exceeds bound')
    return read(length)


def write_frame(stream, raw, maximum, deadline):
    if not 1 <= len(raw) <= maximum:
        raise ValueError('lifecycle frame exceeds bound')
    stream.settimeout(remaining(deadline))
    stream.sendall(struct.pack('!I', len(raw)) + raw)


def verify_peer(stream, expected_uid):
    # Authentication happens before receiving or sending any protocol payload.
    if not hasattr(socket, 'SO_PEERCRED'):
        raise TransportError('Linux peer credentials required')
    raw = stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
    pid, uid, gid = struct.unpack('3i', raw)
    # A peer outside a container PID namespace can legitimately appear as PID
    # zero. Authenticate the UID; never use peer PID as host process authority.
    if uid != expected_uid:
        raise TransportError('lifecycle peer identity rejected')


def private_runtime(path, expected_uid):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute private runtime directory required')
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != expected_uid
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise TransportError('owned mode-0700 runtime directory required')
    return path


class UnixConnector:
    def __init__(self, runtime_dir, expected_uid=None, *, control=False):
        self.socket_name = CONTROL_SOCKET_NAME if control else SOCKET_NAME
        self.expected_uid = os.getuid() if expected_uid is None else expected_uid
        self.runtime_dir = Path(runtime_dir)

    def __call__(self, deadline):
        path = private_runtime(self.runtime_dir, self.expected_uid) / self.socket_name
        info = path.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != self.expected_uid
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise TransportError('owned mode-0600 lifecycle socket required')
        stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            stream.settimeout(remaining(deadline))
            stream.connect(str(path))
            return stream
        except BaseException:
            stream.close()
            raise


class FramedExchange:
    """One new authenticated connection per call; never retries a mutation."""
    def __init__(self, connector, expected_uid=None):
        self.connector = connector
        self.expected_uid = os.getuid() if expected_uid is None else expected_uid

    def __call__(self, request, timeout):
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError('timeout must be positive and at most 60 seconds')
        deadline = time.monotonic() + timeout
        stream = None
        try:
            raw = encode(request)
            if len(raw) > MAX_REQUEST:
                raise ValueError('lifecycle request exceeds bound')
            stream = self.connector(deadline)
            verify_peer(stream, self.expected_uid)
            write_frame(stream, raw, MAX_REQUEST, deadline)
            response = decode(read_frame(stream, MAX_RESPONSE, deadline))
            if type(response.get('ok')) is not bool:
                raise TransportError('invalid lifecycle response')
            if response['ok']:
                if set(response) != {'ok', 'result'} or type(response['result']) is not dict:
                    raise TransportError('invalid lifecycle response')
                return response['result']
            if (set(response) != {'ok', 'error'} or type(response['error']) is not str
                    or response['error'] not in {'invalid', 'stale', 'fault', 'internal'}):
                raise TransportError('invalid lifecycle response')
            error = response['error']
            if error == 'stale':
                raise StaleLease('lifecycle lease invalidated; reconcile before continuing')
            if error == 'invalid':
                raise ValueError('lifecycle request rejected')
            raise LifecycleFault('host lifecycle outcome unconfirmed')
        except (OSError, EOFError, struct.error) as exc:
            raise TransportError('lifecycle transport failed; mutation outcome unknown') from exc
        finally:
            if stream is not None:
                stream.close()


def handle_connection(stream, port, expected_uid, io_timeout=2, operation_timeout=45, allowed_operations=None):
    """Serve one frame only. No logging or raw exception details cross the port."""
    try:
        verify_peer(stream, expected_uid)
        request = decode(read_frame(stream, MAX_REQUEST, time.monotonic() + io_timeout))
        try:
            if allowed_operations is not None and (type(request.get('operation')) is not str
                    or request['operation'] not in allowed_operations):
                raise ValueError('operation not permitted on this lane')
            # The client cannot set host execution deadlines or resource policy.
            result = port.dispatch(request, deadline=time.monotonic() + operation_timeout)
            response = {'ok': True, 'result': result}
        except StaleLease:
            response = {'ok': False, 'error': 'stale'}
        except LifecycleFault:
            response = {'ok': False, 'error': 'fault'}
        except ValueError:
            response = {'ok': False, 'error': 'invalid'}
        except Exception:
            response = {'ok': False, 'error': 'internal'}
        write_frame(stream, encode(response), MAX_RESPONSE, time.monotonic() + io_timeout)
    except (OSError, ValueError, ServiceError, struct.error):
        # Authentication/framing failure closes the connection without execution.
        pass
    finally:
        stream.close()


class UnixLifecycleServer:
    """Explicitly constructed local test server; never installs or starts a unit."""
    def __init__(self, port, runtime_dir, *, workers=8, io_timeout=2, operation_timeout=45, control=False):
        if type(workers) is not int or not 2 <= workers <= 16:
            raise ValueError('2..16 workers required')
        for value in (io_timeout, operation_timeout):
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= 60:
                raise ValueError('positive bounded timeouts required')
        self.uid = os.getuid()
        self.path = private_runtime(runtime_dir, self.uid) / (CONTROL_SOCKET_NAME if control else SOCKET_NAME)
        self.allowed_operations = {'stop', 'status', 'sample', 'panic'} if control else {'ensure'}
        if os.path.lexists(self.path):
            raise TransportError('lifecycle socket already exists; not replaced')
        self.port = port
        self.io_timeout, self.operation_timeout = io_timeout, operation_timeout
        self.slots = threading.BoundedSemaphore(workers)
        self.closed = threading.Event()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.identity = None
        try:
            self.listener.bind(str(self.path))
            info = self.path.lstat()
            self.identity = info.st_dev, info.st_ino
            os.chmod(self.path, 0o600)
            self.listener.listen(workers)
            self.listener.settimeout(.1)
        except BaseException:
            self.close()
            raise

    def serve_forever(self):
        while not self.closed.is_set():
            try:
                stream, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self.closed.is_set():
                    return
                raise
            if not self.slots.acquire(blocking=False):
                stream.close()
                continue
            threading.Thread(target=self._worker, args=(stream,), daemon=True).start()

    def _worker(self, stream):
        try:
            handle_connection(stream, self.port, self.uid, self.io_timeout, self.operation_timeout, self.allowed_operations)
        finally:
            self.slots.release()

    def close(self):
        self.closed.set()
        self.listener.close()
        # Never unlink a replacement or pre-existing endpoint.
        if self.identity is not None:
            try:
                info = self.path.lstat()
                if (info.st_dev, info.st_ino) == self.identity and stat.S_ISSOCK(info.st_mode):
                    self.path.unlink()
            except FileNotFoundError:
                pass
