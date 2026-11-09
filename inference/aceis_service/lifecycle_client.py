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

"""Core-side lifecycle client: no Docker, host configuration or PID authority."""
from __future__ import annotations
import re
import math
import time
import threading
from .core import ServiceError
from .lifecycle import validate_sample


class LifecycleClient:
    def __init__(self, exchange, control_exchange=None, *, timeout=45, read_timeout=2):
        for value in (timeout, read_timeout):
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= 60:
                raise ValueError('positive bounded timeouts required')
        self.exchange = exchange
        self.control_exchange = control_exchange or exchange
        self.timeout, self.read_timeout = timeout, read_timeout
        self.lock = threading.RLock()
        self.stop_lock = threading.Lock()
        self.epoch, self.generation = None, None
        self.uncertain = True
        self.need_reconcile = True
        self.stopping = False
        self.ticket = 0
        self.pending_ensure = None
        self.expected_stop = None

    @staticmethod
    def _identity(value):
        if (type(value.get('epoch')) is not str or not re.fullmatch(r'[0-9a-f]{32}', value['epoch'])
                or type(value.get('generation')) is not int or value['generation'] < 0):
            raise ServiceError('invalid host lifecycle identity')
        return value['epoch'], value['generation']

    def status(self, timeout=None):
        result = self.control_exchange({'operation': 'status'}, self.read_timeout if timeout is None else timeout)
        self._identity(result)
        if (set(result) != {'epoch', 'generation', 'running', 'stopping', 'fault', 'stopped_generation',
                                   'host_pid_namespace', 'host_uid', 'host_boot_id', 'emergency_stop_supported'}
                or type(result['running']) is not bool or type(result['stopping']) is not bool
                or result['fault'] is not None and type(result['fault']) is not str
                or result['stopped_generation'] is not None and type(result['stopped_generation']) is not int
                or type(result['emergency_stop_supported']) is not bool
                or type(result['host_uid']) is not int or result['host_uid'] < 0
                or type(result['host_pid_namespace']) is not str
                or not re.fullmatch(r'pid:\[\d+\]', result['host_pid_namespace'])
                or type(result['host_boot_id']) is not str
                or not re.fullmatch(r'[0-9a-f-]{36}', result['host_boot_id'])):
            raise ServiceError('invalid host lifecycle status')
        return result

    def reconcile(self):
        with self.lock:
            if self.stopping or self.pending_ensure is not None:
                raise ServiceError('host lifecycle mutation pending')
            ticket = self.ticket
        result = self.status()
        with self.lock:
            if ticket != self.ticket or self.stopping or self.pending_ensure is not None:
                raise ServiceError('reconciliation invalidated by mutation')
            self.epoch, self.generation = self._identity(result)
            # Liveness is not a physical stop receipt. A lost ACK is reconciled
            # only against the same epoch/generation's confirmed stop barrier.
            self.uncertain = not (self.expected_stop == (result['epoch'], result['generation'])
                                  and not result['running'] and not result['stopping']
                                  and result['fault'] is None
                                  and result['stopped_generation'] == result['generation'])
            self.need_reconcile = False
        return result

    def read_resources(self):
        result = self.control_exchange({'operation': 'sample'}, self.read_timeout)
        if (set(result) != {'epoch', 'resources'} or type(result['epoch']) is not str
                or not re.fullmatch(r'[0-9a-f]{32}', result['epoch'])):
            raise ServiceError('invalid host telemetry response')
        with self.lock:
            if self.epoch is not None and result['epoch'] != self.epoch:
                self.ticket += 1
                self.uncertain = True
                self.need_reconcile = True
                self.expected_stop = None
                raise ServiceError('host telemetry epoch changed; explicit reconciliation required')
        return validate_sample(result['resources'])

    def ensure(self, check=lambda: None):
        check()
        with self.lock:
            if self.uncertain or self.stopping or self.pending_ensure is not None or self.epoch is None:
                raise ServiceError('explicit confirmed host stop required before start')
            # Fence any reconciliation whose status observation predates this mutation.
            self.ticket += 1
            epoch, generation, ticket = self.epoch, self.generation, self.ticket
            self.pending_ensure = ticket
            self.uncertain = True
            # A pre-existing stop receipt cannot disprove a late queued start.
            # Unknown ensure outcomes always require a NEW invalidating stop.
            self.expected_stop = None
        try:
            result = self.exchange({'operation': 'ensure', 'epoch': epoch,
                                    'generation': generation}, self.timeout)
            if set(result) != {'epoch', 'generation', 'running', 'endpoint'} or result['running'] is not True:
                raise ServiceError('invalid host ensure response')
            if self._identity(result) != (epoch, generation):
                raise ServiceError('host lifecycle identity changed')
            check()
            with self.lock:
                if ticket != self.ticket or self.stopping:
                    raise ServiceError('start invalidated by cancellation')
                self.uncertain = False
            return result
        except Exception:
            with self.lock:
                # An older start cannot re-latch a newer, confirmed stop.
                if ticket == self.ticket:
                    self.uncertain = True
            raise
        finally:
            with self.lock:
                if self.pending_ensure == ticket:
                    self.pending_ensure = None

    def stop(self):
        deadline = time.monotonic() + self.timeout
        if not self.stop_lock.acquire(timeout=self.timeout):
            raise ServiceError('host stop already pending; outcome unconfirmed')
        with self.lock:
            self.ticket += 1
            self.stopping = True
            self.uncertain = True
            epoch, generation = self.epoch, self.generation
            reconcile = self.need_reconcile or epoch is None
        try:
            if reconcile:
                status = self.status(min(self.read_timeout, self._remaining(deadline)))
                if status['stopping']:
                    raise ServiceError('host stop pending; reconcile before retry')
                epoch, generation = self._identity(status)
            # Cached known identity permits cancellation to invalidate an
            # in-flight ensure immediately, before any status lock wait.
            with self.lock:
                self.expected_stop = (epoch, generation + 1)
            result = self.control_exchange({'operation': 'stop', 'epoch': epoch,
                                            'generation': generation}, self._remaining(deadline))
            if (set(result) != {'epoch', 'generation', 'running', 'stopped_generation'}
                    or result['running'] is not False
                    or self._identity(result) != (epoch, generation + 1)
                    or type(result['stopped_generation']) is not int
                    or result['stopped_generation'] != generation + 1):
                raise ServiceError('host physical stop acknowledgement invalid')
            with self.lock:
                self.epoch, self.generation = epoch, generation + 1
                self.uncertain = False
                self.need_reconcile = False
                self.pending_ensure = None
        except Exception:
            with self.lock:
                self.need_reconcile = True
            raise
        finally:
            with self.lock:
                self.stopping = False
            self.stop_lock.release()

    def panic(self):
        # No caller-supplied process/container/command or reason crosses the port.
        with self.lock:
            epoch, generation = self.epoch, self.generation
        if epoch is None:
            epoch, generation = self._identity(self.status())
        with self.lock:
            self.ticket += 1
            self.uncertain = True
            self.expected_stop = None
            self.need_reconcile = True
        return self.control_exchange({'operation': 'panic', 'epoch': epoch,
                                      'generation': generation}, self.timeout)

    @staticmethod
    def _remaining(deadline):
        value = deadline - time.monotonic()
        if value <= 0:
            raise ServiceError('host stop deadline exceeded; outcome unconfirmed')
        return value
