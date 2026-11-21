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

# Changed 2026-11-13: bounded swap policy and explicit quiet resource recovery.

"""HTTP over an owner-only Unix socket. No TCP listener or new credentials."""
from __future__ import annotations
import argparse
import fcntl
import http.client
import http.server
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import stat
import struct
import threading
import time
from .core import Controller, MODELS, ServiceError
from .npu_bridge import classify as npu_classify, NPUUnavailable, NPUBusy, MAX_BODY as NPU_MAX_BODY, MODEL as NPU_MODEL


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=2):
        super().__init__('localhost', timeout=timeout)
        self.path = str(path)
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def npu_health(path):
    if not path:
        return {'status': 'not_configured'}
    try:
        c = UnixHTTPConnection(path, timeout=.5)
        c.request('GET', '/health')
        r = c.getresponse()
        data = r.read(8193)
        if len(data) > 8192:
            raise ValueError()
        result = json.loads(data)
        if not isinstance(result,dict):
            raise ValueError('invalid NPU health')
        return {k: result.get(k) for k in ['status', 'backend', 'model', 'execution_target', 'execution_verified', 'provider', 'execution_scope', 'verification_kind']}
    except (OSError, ValueError, RecursionError, http.client.HTTPException):
        return {'status': 'unavailable'}
    finally:
        if 'c' in locals():
            c.close()


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16
    slots = threading.BoundedSemaphore(16)
    def __init__(self, *args, **kwargs):
        self.npu_slot = threading.BoundedSemaphore(1)
        super().__init__(*args, **kwargs)
    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise
    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()
    def verify_request(self, request, address):
        pid, uid, gid = struct.unpack('3i', request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        return uid == os.getuid()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    def log_message(self, *args):
        pass  # Do not retain request paths, IDs, prompts, or completions.
    def setup(self):
        super().setup()
        self.connection.settimeout(5)
    def response(self, code, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)
    def guard(self):
        if self.headers.get('Origin') is not None or self.headers.get('Transfer-Encoding') is not None:
            raise ValueError('browser origin and transfer encoding are not accepted')
        if self.headers.get('Host', 'localhost') not in {'localhost', '127.0.0.1'}:
            raise ValueError('invalid host')
    def body(self, maximum=32768):
        if len(self.headers.get_all('Content-Length', [])) != 1:
            raise ValueError('one Content-Length required')
        size = int(self.headers['Content-Length'])
        if not 0 < size <= maximum:
            raise ValueError('body exceeds route bound')
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('application/json required')
        data = self.rfile.read(size)
        if len(data) != size:
            raise ValueError('incomplete body')
        try:
            return json.loads(data)
        except RecursionError:
            raise ValueError('JSON nesting exceeds bound') from None
    def do_GET(self):
        try:
            self.guard()
            ctl = self.server.controller
            if self.path == '/health':
                health = ctl.backend.health()
                with ctl.lock:
                    active_id = ctl.active.id if ctl.active else None
                ready = not ctl.stop.is_set() and not ctl.fault and health.get('ready', health.get('flash_resident', False))
                self.response(200 if ready else 503,
                              {'status': 'ready' if ready else 'not_ready',
                               'fault': ctl.fault, 'resources': ctl.latest, 'gpu': health,
                               'queue_depth': ctl.queue.qsize(), 'active': active_id,
                               'npu': npu_health(self.server.config.get('npu_socket'))})
            elif self.path == '/v1/capabilities':
                self.response(200, {'api_version':1, 'transport':'owner-only Unix socket',
                    'gpu':{'models':sorted(ctl.models),'max_concurrent_generation':1,
                           'default_model':getattr(ctl.backend, 'default_model', 'gpt-oss-120b'),
                           'flash_enabled':ctl.requires_flash,
                           'context_limit':8192,'max_output_tokens':1024},
                    'npu':{'configured':bool(self.server.config.get('npu_socket')),
                           'model':NPU_MODEL,'input_shape':[1,3,32,32],'input_elements':3072,
                           'input_dtype':'float32','output_classes':10,'max_concurrent_classification':1,
                           'max_request_bytes':NPU_MAX_BODY,'application_accuracy_verified':False}})
            elif self.path == '/v1/models':
                self.response(200, {'models': sorted(ctl.models), 'context_limit': 8192,
                                    'default_model':getattr(ctl.backend, 'default_model', 'gpt-oss-120b'),
                                    'flash_enabled':ctl.requires_flash,
                                    'max_output_tokens': 1024, 'max_large_models_loaded': 1})
            elif self.path.startswith('/v1/jobs/'):
                with ctl.lock:
                    job = ctl.jobs.get(self.path.removeprefix('/v1/jobs/'))
                    result = None if job is None else job.public()
                self.response(404 if job is None else 200, result or {'error': 'unknown job'})
            else:
                self.response(404, {'error': 'unknown route'})
        except (ValueError, OSError) as exc:
            self.response(400, {'error': type(exc).__name__})
    def do_POST(self):
        try:
            self.guard()
            body = self.body(NPU_MAX_BODY if self.path == '/v1/classify' else 32768)
            ctl = self.server.controller
            if self.path == '/v1/classify':
                if ctl.stop.is_set():
                    raise NPUUnavailable('service is stopping')
                if not self.server.npu_slot.acquire(blocking=False):
                    raise NPUBusy('NPU classifier busy')
                try:
                    result = npu_classify(self.server.config.get('npu_socket'),body)
                finally:
                    self.server.npu_slot.release()
                self.response(200,result)
                return
            if self.path == '/v1/recover':
                if body != {}:
                    raise ValueError('empty object required')
                ctl.recover()
                self.response(200, {'status': 'recovery_requested'})
                return
            if self.path not in {'/v1/jobs', '/v1/chat/completions'}:
                self.response(404, {'error': 'unknown route'})
                return
            job = ctl.submit(body)
            if self.path == '/v1/jobs':
                self.response(202, {'id': job.id, 'status': job.status})
                return
            while not job.done.wait(.2):
                # Detect disconnected synchronous clients without consuming input.
                readable, _, _ = __import__('select').select([self.connection], [], [], 0)
                if readable and not self.connection.recv(1, socket.MSG_PEEK):
                    job.cancel.set()
                    return
            self.response(200 if job.status == 'completed' else 503, job.public())
        except ValueError as exc:
            self.response(400, {'error': str(exc)[:160]})
        except NPUBusy:
            self.response(429, {'error':'NPU classifier busy'})
        except NPUUnavailable:
            self.response(503, {'error':'NPU unavailable or outcome unknown; not retried'})
        except ServiceError as exc:
            self.response(503, {'error': str(exc)[:160]})
        except (OSError, http.client.HTTPException):
            # ASYNC-ACK-01: losing async ACK must not cancel admitted work.
            if self.path == '/v1/chat/completions' and 'job' in locals():
                job.cancel.set()
    def do_DELETE(self):
        try:
            self.guard()
            ctl = self.server.controller
            key = self.path.removeprefix('/v1/jobs/') if self.path.startswith('/v1/jobs/') else ''
            with ctl.lock:
                job = ctl.jobs.get(key)
                if job and not job.done.is_set():
                    job.cancel.set()
            self.response(202 if job else 404, {'status': 'cancellation_requested' if job else 'unknown_job'})
        except (ValueError, OSError):
            self.response(400, {'error': 'invalid request'})


def private_dir(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise ServiceError('private directory must be owned by current user with mode 0700')
    return path


def main():
    # Host-only legacy backend is not imported by the Core image entrypoint.
    from .backends import Backends
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--prepare-models', action='store_true')
    args = p.parse_args()
    config_path = Path(args.config).resolve(strict=True)
    config = json.loads(config_path.read_text())
    state = private_dir(config['state_dir'])
    runtime = private_dir(config['runtime_dir'])
    # Coordinate with all historical GPU experiments, without changing their lock.
    with open(config['generation_lock'], 'a+') as legacy_lock, open(state / 'service.lock', 'a+') as service_lock:
        fcntl.flock(legacy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backend = Backends(config, config_path)
        if args.prepare_models:
            prepare = Controller(backend, start=False, swap_limit_mib=config.get('swap_limit_mib', 512))
            prepare.monitor.start()
            def interrupt_prepare(*_):
                raise KeyboardInterrupt()
            signal.signal(signal.SIGTERM, interrupt_prepare)
            try:
                backend.import_models()
                if prepare.fault:
                    raise ServiceError(prepare.fault)
            finally:
                prepare.close()
            return
        controller = Controller(backend, start=False, swap_limit_mib=config.get('swap_limit_mib', 512))
        address = runtime / 'service.sock'
        server = None
        def shutdown(*_):
            controller.stop.set()
            if server:
                threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        try:
            backend.start_ollama(controller.cancelled_idle)
            backend.verify_models()
            controller.safety()
            backend.start_flash(controller.cancelled_idle)
            controller.thread.start()
            controller.monitor.start()
            if address.exists():
                # A held lock proves no live owner, but never overwrite foreign files.
                st = address.lstat()
                if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
                    raise ServiceError('foreign socket path')
                address.unlink()
            old_umask = os.umask(0o177)
            try:
                server = Server(str(address), Handler)
            finally:
                os.umask(old_umask)
            server.controller, server.config = controller, config
            if controller.stop.is_set():
                return
            def stop_server():
                controller.stop.wait()
                server.shutdown()
            threading.Thread(target=stop_server, daemon=True).start()
            server.serve_forever(poll_interval=.2)
        finally:
            if server:
                server.server_close()
            controller.close()
            if address.exists() and stat.S_ISSOCK(address.lstat().st_mode):
                address.unlink()

if __name__ == '__main__':
    main()
