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

"""Transport-independent, fixed-capability lifecycle protocol prototype.

This module is suitable for a future socket-only Core image: no subprocess,
Docker API, host path, mount, device, environment or image input is accepted.
It is not a network server or a replacement for the production Router.
"""
from __future__ import annotations
import math
import os
from pathlib import Path
import threading
import time
from contextlib import contextmanager
import uuid
from .core import ServiceError, check_resources


class StaleLease(ServiceError):
    pass


class LifecycleFault(ServiceError):
    pass


FIELDS = {'mem_available', 'swap_used', 'vram_total', 'vram_used',
          'pswpin', 'pswpout', 'psi_full_avg10'}


def validate_sample(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise LifecycleFault('host telemetry fields unavailable')
    for key in FIELDS - {'psi_full_avg10'}:
        if type(value[key]) is not int or value[key] < 0:
            raise LifecycleFault('invalid host telemetry')
    psi = value['psi_full_avg10']
    if type(psi) not in {int, float} or not math.isfinite(psi) or not 0 <= psi <= 100:
        raise LifecycleFault('invalid host telemetry')
    if value['vram_used'] > value['vram_total']:
        raise LifecycleFault('invalid host telemetry')
    return dict(value)


class HostLifecycle:
    """One host-owned provider; only ensure/stop/status/sample are wire capabilities.

    ``provider`` is trusted host code, never deserialized from a client.
    ``stop`` must verify container termination and the physical idle barrier.
    Transport authentication, watchdog and crash reconciliation are deliberately
    not implemented here. Do not deploy this prototype as a security boundary.
    """
    def __init__(self, provider, read_resources, *, read_endpoint=lambda: None, panic_callback=None, swap_limit_mib=512):
        self.swap_limit_mib = swap_limit_mib
        self.provider = provider
        self.read_endpoint = read_endpoint
        self.panic_callback = panic_callback
        self.host_pid_namespace = os.readlink('/proc/self/ns/pid')
        self.host_uid = os.getuid()
        self.host_boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.read_resources = read_resources
        self.baseline = validate_sample(read_resources())
        check_resources(self.baseline, self.baseline, self.swap_limit_mib)
        self.epoch = uuid.uuid4().hex
        self.generation = 0
        self.fault = None
        self.closed = False
        self.safety_fault = None
        self.last_sample = None
        self.stopping = False
        self.stopped_generation = None
        self.state_lock = threading.RLock()
        self.operation_lock = threading.Lock()

    def _identity(self):
        return {'epoch': self.epoch, 'generation': self.generation}

    @contextmanager
    def _operation(self, deadline):
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        acquired = self.operation_lock.acquire() if remaining is None else self.operation_lock.acquire(timeout=remaining)
        if not acquired:
            raise LifecycleFault('lifecycle operation deadline exceeded')
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise LifecycleFault('lifecycle operation deadline exceeded')
            yield
        finally:
            self.operation_lock.release()

    def _check(self, epoch, generation, deadline=None):
        if deadline is not None and time.monotonic() >= deadline:
            raise LifecycleFault('lifecycle operation deadline exceeded')
        with self.state_lock:
            if epoch != self.epoch or generation != self.generation:
                raise StaleLease('lifecycle lease invalidated; do not replay')
            if self.stopping:
                raise LifecycleFault('provider stop pending')
            if self.closed:
                raise LifecycleFault('host lifecycle closed')
            if self.safety_fault or self.fault:
                raise LifecycleFault(self.safety_fault or self.fault)
        check_resources(validate_sample(self.read_resources()), self.baseline, self.swap_limit_mib)

    def dispatch(self, request, *, deadline=None):
        with self.state_lock:
            if self.closed:
                raise LifecycleFault('host lifecycle closed')
        if not isinstance(request, dict) or type(request.get('operation')) is not str:
            raise ValueError('fixed lifecycle operation required')
        op = request['operation']
        if op in {'status', 'sample', 'resource_policy'}:
            if set(request) != {'operation'}:
                raise ValueError('operation accepts no parameters')
            if op == 'resource_policy':
                with self.state_lock:
                    return {'epoch': self.epoch, 'swap_limit_mib': self.swap_limit_mib}
            if op == 'sample':
                with self.state_lock:
                    if self.safety_fault:
                        raise LifecycleFault(self.safety_fault)
                # No cached samples: Core's watchdog must use host observations.
                resources = validate_sample(self.read_resources())
                with self.state_lock:
                    self.last_sample = time.monotonic()
                return {'epoch': self.epoch, 'resources': resources}
            with self._operation(deadline):
                running = self.provider.running()
                if type(running) is not bool:
                    raise LifecycleFault('provider status unconfirmed')
                with self.state_lock:
                    return {**self._identity(), 'running': running, 'stopping': self.stopping, 'fault': self.safety_fault or self.fault,
                            'stopped_generation': self.stopped_generation,
                            'host_pid_namespace': self.host_pid_namespace,
                            'host_uid': self.host_uid, 'host_boot_id': self.host_boot_id,
                            'emergency_stop_supported': self.panic_callback is not None}
        if op not in {'ensure', 'stop', 'panic'} or set(request) != {'operation', 'epoch', 'generation'}:
            raise ValueError('unknown lifecycle operation or fields')
        epoch, generation = request['epoch'], request['generation']
        if type(epoch) is not str or len(epoch) != 32 or type(generation) is not int or generation < 0:
            raise ValueError('invalid lifecycle identity')
        if op == 'panic':
            with self.state_lock:
                if epoch != self.epoch or generation != self.generation:
                    raise StaleLease('stale emergency stop request')
                if self.panic_callback is None:
                    raise LifecycleFault('fixed Core emergency stop unavailable')
                # Establish the safety fence atomically with identity admission.
                self.generation += 1
                self.safety_fault = 'Core provider stop unconfirmed'
                self.stopping = True
            self.panic_callback('Core provider stop unconfirmed')
            return {**self._identity(), 'emergency_stop_requested': True}
        if op == 'ensure':
            return self._ensure(epoch, generation, deadline)
        return self._stop(epoch, generation, deadline)

    def _ensure(self, epoch, generation, deadline):
        with self._operation(deadline):
            self._check(epoch, generation, deadline)
            with self.state_lock:
                self.stopped_generation = None
            try:
                self.provider.ensure(lambda: self._check(epoch, generation, deadline))
                self._check(epoch, generation, deadline)
                if self.provider.running() is not True:
                    raise LifecycleFault('provider readiness unconfirmed')
                with self.state_lock:
                    # Atomic final identity check and success observation.
                    self._check(epoch, generation, deadline)
                    return {**self._identity(), 'running': True, 'endpoint': self.read_endpoint()}
            except StaleLease:
                # A stop invalidated this operation before taking operation_lock.
                raise
            except Exception:
                with self.state_lock:
                    self.fault = 'provider ensure outcome unconfirmed; explicit stop required'
                raise LifecycleFault(self.fault) from None

    def _stop(self, epoch, generation, deadline):
        # Cancellation wins before waiting for a concurrent start. No stale start
        # can commit afterward, even if its request was already dispatched.
        with self.state_lock:
            if epoch != self.epoch or generation != self.generation:
                raise StaleLease('lifecycle lease invalidated; reconcile status first')
            if self.stopping:
                raise LifecycleFault('provider stop already pending')
            self.stopping = True
            self.generation += 1
        try:
            with self._operation(deadline):
                return self._confirm_stop(deadline)
        except Exception:
            with self.state_lock:
                self.stopping = False
                self.fault = 'provider stop or physical release unconfirmed'
            raise LifecycleFault(self.fault) from None

    def _confirm_stop(self, deadline):
        with self.state_lock:
            if self.closed:
                raise LifecycleFault('host lifecycle closed')
        self.provider.stop()
        if self.provider.running() is not False:
            raise LifecycleFault('provider stop unconfirmed')
        with self.state_lock:
            # A completed physical barrier is retained even if its ACK is lost.
            self.fault = None
            self.stopping = False
            self.stopped_generation = self.generation
            return {**self._identity(), 'running': False,
                    'stopped_generation': self.stopped_generation}
