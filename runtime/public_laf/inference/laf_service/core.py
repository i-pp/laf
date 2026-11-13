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

"""Bounded single-lane scheduling and fail-closed resource admission."""
from __future__ import annotations
import dataclasses
import queue
import threading
import time
import uuid
from pathlib import Path

GIB = 1 << 30
MODELS = {"flash", "coder-next", "gpt-oss-120b"}

class ServiceError(Exception):
    pass


def snapshot():
    mem = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        k, v = line.split(':', 1)
        mem[k] = int(v.split()[0]) * 1024
    vm = dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
    devices = list(Path('/sys/class/drm').glob('card*/device/mem_info_vram_total'))
    if len(devices) != 1:
        raise ServiceError('exactly one GPU telemetry source required')
    gpu = devices[0].parent
    full = next(l for l in Path('/proc/pressure/memory').read_text().splitlines() if l.startswith('full '))
    psi = dict(x.split('=') for x in full.split()[1:])
    return {'mem_available': mem['MemAvailable'], 'swap_used': mem['SwapTotal'] - mem['SwapFree'],
            'vram_total': int((gpu / 'mem_info_vram_total').read_text()),
            'vram_used': int((gpu / 'mem_info_vram_used').read_text()),
            'pswpin': int(vm['pswpin']), 'pswpout': int(vm['pswpout']), 'psi_full_avg10': float(psi['avg10'])}


def check_resources(s, baseline, swap_limit_mib=512):
    if type(swap_limit_mib) is not int or not 512 <= swap_limit_mib <= 2048:
        raise ValueError("swap_limit_mib must be 512..2048")
    if s['mem_available'] < 8 * GIB:
        raise ServiceError('RAM reserve below 8 GiB')
    if s['vram_total'] - s['vram_used'] < 2 * GIB:
        raise ServiceError('VRAM reserve below 2 GiB')
    if s['swap_used'] - baseline['swap_used'] > swap_limit_mib * 1024**2:
        raise ServiceError(f'swap growth exceeds {swap_limit_mib} MiB')
    page = __import__('os').sysconf('SC_PAGE_SIZE')
    if any((s[key] - baseline[key]) * page > swap_limit_mib * 1024**2 for key in ['pswpin', 'pswpout']):
        raise ServiceError(f'swap I/O exceeds {swap_limit_mib} MiB since resource baseline')
    if s['psi_full_avg10'] > 10:
        raise ServiceError('memory PSI full avg10 above 10 percent')


def validate_request(body, models=MODELS):
    if not isinstance(body, dict) or set(body) - {'model', 'messages', 'max_tokens', 'timeout_seconds'}:
        raise ValueError('unknown request fields')
    model = body.get('model')
    if not isinstance(model, str) or model not in models:
        raise ValueError('unknown model')
    messages = body.get('messages')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise ValueError('1..32 messages required')
    if any(not isinstance(m, dict) or set(m) != {'role', 'content'} or not isinstance(m['role'], str) or m['role'] not in {'user', 'assistant', 'system'}
           or not isinstance(m['content'], str) for m in messages):
        raise ValueError('text-only chat messages required')
    size = sum(len(m['content'].encode()) for m in messages)
    if not 1 <= size <= 24576:
        raise ValueError('message text must fit 24576 UTF-8 bytes')
    n = body.get('max_tokens', 256)
    timeout = body.get('timeout_seconds', 180)
    if type(n) is not int or not 1 <= n <= 1024:
        raise ValueError('max_tokens must be 1..1024')
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError('timeout_seconds must be 1..300')
    # Worst-case UTF-8 byte tokenization reserve, rather than silently truncating.
    if size + len(messages) * 64 + n + 256 > 8192:
        raise ValueError('conservative 8192-token context budget exceeded')
    return model, messages, n, timeout


@dataclasses.dataclass
class Job:
    model: str
    messages: list
    max_tokens: int
    deadline: float
    id: str = dataclasses.field(default_factory=lambda: uuid.uuid4().hex)
    created: float = dataclasses.field(default_factory=time.monotonic)
    status: str = 'queued'
    result: dict | None = None
    error: str | None = None
    cancel: threading.Event = dataclasses.field(default_factory=threading.Event)
    done: threading.Event = dataclasses.field(default_factory=threading.Event)
    abort_requested: bool = False

    def public(self):
        return {'id': self.id, 'model': self.model, 'status': self.status,
                'result': self.result, 'error': self.error}


class Controller:
    def __init__(self, backend, read_resources=snapshot, start=True, swap_limit_mib=512):
        self.backend = backend
        self.swap_limit_mib = swap_limit_mib
        self.models = frozenset(getattr(backend, 'models', MODELS))
        self.requires_flash = getattr(backend, 'requires_flash', True)
        self.read_resources = read_resources
        self.baseline = read_resources()
        check_resources(self.baseline, self.baseline, self.swap_limit_mib)
        self.queue = queue.Queue(maxsize=8)
        self.jobs = {}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.fault = None
        self.active = None
        self.latest = self.baseline
        self.restarts = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.monitor = threading.Thread(target=self.watch, daemon=True)
        if start:
            self.thread.start()
            self.monitor.start()

    def prune(self):
        now = time.monotonic()
        for key, job in list(self.jobs.items()):
            if job.done.is_set() and now - job.created > 600:
                del self.jobs[key]

    def submit(self, body):
        model, messages, n, timeout = validate_request(body, self.models)
        with self.lock:
            self.prune()
            if self.stop.is_set() or self.fault:
                raise ServiceError(self.fault or 'service is stopping')
            if len(self.jobs) >= 128:
                raise ServiceError('job retention capacity reached; retry later')
            job = Job(model, messages, n, time.monotonic() + timeout)
            try:
                self.queue.put_nowait(job)
            except queue.Full:
                raise ServiceError('queue full') from None
            self.jobs[job.id] = job
            return job

    def safety(self):
        # Backend lifecycle callbacks call this while holding their own lock.
        # Never acquire Controller.lock here (it would invert lock ordering).
        s = self.read_resources()
        self.latest = s
        check_resources(s, self.baseline, self.swap_limit_mib)
        return s

    def watch(self):
        while not self.stop.wait(0.5):
            try:
                self.safety()
            except Exception:
                with self.lock:
                    if not self.fault:
                        # Recovery may have renewed the baseline while the
                        # observation waited. Only resource errors are retried.
                        try:
                            self.safety()
                        except Exception as current_error:
                            self.trip(str(current_error))
                continue
            with self.lock:
                job = self.active
                abort = job and not job.abort_requested and (job.cancel.is_set() or time.monotonic() >= job.deadline)
                if abort:
                    job.abort_requested = True
                    try:
                        self.backend.abort()
                    except Exception:
                        # A successful resource reading never proves a failed
                        # provider stop safe. Preserve the fail-closed latch.
                        self.trip('provider stop unconfirmed')

    def trip(self, reason):
        with self.lock:
            self.fault = reason
            if self.active:
                self.active.cancel.set()
            # Keep the latch/active identity stable through the stop barrier.
            try:
                self.backend.abort()
            except Exception:
                self.fault += '; provider stop unconfirmed'

    def cancelled(self, job):
        if self.stop.is_set() or job.cancel.is_set() or time.monotonic() >= job.deadline or self.fault:
            raise ServiceError('cancelled, expired, or resource safety latch set')
        self.safety()

    def run(self):
        while not self.stop.is_set():
            try:
                job = self.queue.get(timeout=.2)
            except queue.Empty:
                if self.requires_flash and not self.fault and not self.stop.is_set() and not self.backend.health()['flash_resident']:
                    now = time.monotonic()
                    self.restarts = [t for t in self.restarts if now - t < 600]
                    if len(self.restarts) >= 2:
                        self.trip('Flash recovery budget exhausted; explicit recovery required')
                        continue
                    self.restarts.append(now)
                    try:
                        self.backend.start_flash(lambda: self.cancelled_idle())
                    except Exception as exc:
                        self.trip('Flash recovery failed: ' + str(exc))
                continue
            try:
                with self.lock:
                    self.active = job
                    job.status = 'running'
                self.cancelled(job)
                job.result = self.backend.generate(job, lambda: self.cancelled(job))
                self.cancelled(job)
                job.status = 'completed'
            except Exception as exc:
                job.status = 'cancelled' if job.cancel.is_set() or self.stop.is_set() else 'failed'
                job.error = str(exc)[:240]
                try:
                    self.backend.abort()
                except Exception:
                    self.fault = 'provider stop unconfirmed'
            finally:
                job.messages = []
                job.done.set()
                with self.lock:
                    self.active = None
                self.queue.task_done()

    def cancelled_idle(self):
        if self.stop.is_set() or self.fault:
            raise ServiceError('service stopped or latched')
        return self.safety()

    def recover(self):
        """Rebase only explicit, idle recovery after five seconds of quiet telemetry."""
        with self.lock:
            if self.stop.is_set() or self.active or any(not job.done.is_set() for job in self.jobs.values()):
                raise ServiceError('wait for all requests to stop')
            self.fault = self.fault or "explicit recovery pending"
            # A failed stop barrier must never clear the existing fault.
            self.backend.abort()
            first = self.read_resources()
            for _ in range(10):
                time.sleep(0.5)
                current = self.read_resources()
                # Absolute reserves and PSI protections remain in force. The
                # old cumulative I/O counter is deliberately not a new budget.
                check_resources(current, current, self.swap_limit_mib)
                if current['psi_full_avg10'] > 1 or any(
                    current[key] != first[key] for key in ('swap_used', 'pswpin', 'pswpout')
                ):
                    raise ServiceError('resource recovery requires five quiet seconds')
            if self.stop.is_set():
                raise ServiceError("service stopping")
            self.baseline = current
            self.latest = current
            self.fault = None
            self.restarts = []

    def close(self):
        self.stop.set()
        with self.lock:
            for job in self.jobs.values():
                if not job.done.is_set():
                    job.cancel.set()
                    job.status = 'cancelled'
                    job.messages = []
                    job.done.set()
        self.backend.abort()
        if self.thread.is_alive():
            self.thread.join(15)
        if self.monitor.is_alive():
            self.monitor.join(3)
        self.backend.close()
