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


# Added 2026-11-15: bounded resource-only standalone coordinators.

"""Exec into a verified private scope before constructing a standalone Runner.

The environment record is only a recursion/binding hint. Kernel limits, the
scope receipt, the pinned pool generation and the ledger owner are verified
again in the child. There is no unbounded fallback or time-based reclamation.
"""
from contextlib import ExitStack, closing, contextmanager
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid

from concurrency import lease, pool_generation
from resources import Control
import scheduler

MARKER = 'LAF_STANDALONE_CONTROLLER'


def required(args):
    return (args.command in ('check', 'run-pair')
            or args.command == 'run' and args.dispatch_token is None
            or args.command == 'reconcile' and not args.offline
            or args.command == 'resolve' and args.kind == 'verify')


def _private_root(root):
    root = Path(root)
    for path in (root, *root.parents):
        if path.is_symlink(): raise ValueError('Unsafe standalone runtime ancestry')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Standalone runtime must be owner-only')


def _verified(root, settings, raw):
    from resource_admission import AdmissionWait, _scope_path
    try:
        record = json.loads(raw)
        if (set(record) != {'unit', 'receipt', 'generation'}
                or record['generation'] != settings['pool_generation']
                or not re.fullmatch(r'agentorchestrator-[0-9a-f-]{36}\.scope', record['unit'])):
            raise ValueError('Invalid standalone binding')
        descriptor = scheduler.controller_descriptor()
        if descriptor['unit'] != record['unit']: raise ValueError('Different actual scope')
        _scope_path(descriptor)
        receipt = Path(root) / 'resource-events' / (record['unit'] + '.json')
        if str(receipt) != record['receipt']: raise ValueError('Different receipt')
        info = receipt.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 32768):
            raise ValueError('Unsafe scope receipt')
        fd = os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd) as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('Scope receipt changed')
            data = json.load(stream)
        if (data['state'] != 'READY' or data['unit'] != record['unit']
                or data['cgroup'] != descriptor['expected_cgroup'] or data['limits'] != settings['daemon']):
            raise ValueError('Unconfirmed standalone scope')
        from daemon import verify_self_limits
        verify_self_limits(allow_codex_pipeline=False)
        return {**descriptor, 'receipt': str(receipt), 'limits': settings['daemon']}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        raise AdmissionWait('resource_controller_required') from error


@contextmanager
def controller(root, args, *, argv=None):
    """Resource-only CLI boundary; direct unbounded Runner APIs still refuse."""
    if not required(args):
        yield None
        return
    settings = scheduler.configuration()
    if not scheduler.resource_only(settings):
        yield None
        return
    scheduler.require_ordinary_provider(settings)
    _private_root(root)
    marker = os.environ.get(MARKER)
    if marker is None:
        control = Control(root, settings['daemon'])
        command, env = control.wrap([sys.executable, '-B', str(Path(__file__).with_name('runner.py')),
                                     *(sys.argv[1:] if argv is None else argv)], dict(os.environ))
        env[MARKER] = json.dumps({'unit': control.unit, 'receipt': str(control.receipt),
                                 'generation': settings['pool_generation']})
        # Preserve the launcher PID. resource_entry verifies this process is
        # still its ancestor. No Python coordinator remains outside the scope.
        os.execve(command[0], command, env)
        raise RuntimeError('Standalone bounded exec returned')
    descriptor = _verified(root, settings, marker)
    from resource_admission import AdmissionWait, Ledger, linux_observation
    with ExitStack() as stack:
        stack.enter_context(lease(Path(root) / 'daemon.lock', shared=True))
        stack.enter_context(pool_generation(root, settings))
        scheduler.require_pool_generation(settings)
        scheduler.reconcile_stopped_controllers(root, settings)
        owner = scheduler.register_resource_owner(root, settings, 'standalone_controller')
        stack.callback(scheduler.close_resource_owner, settings, owner)
        task, token = str(uuid.uuid4()), str(uuid.uuid4())
        limits = settings['daemon']
        demand = {'kind': 'local_control', 'provider': None, 'resources':
                  {'cpu_millicores': limits['cpu_percent'] * 10, 'ram_bytes': limits['memory_bytes'],
                   'gpu_milliunits': 0, 'vram_bytes': 0}}
        with closing(Ledger(settings['resource_admission'])) as ledger:
            deadline = time.monotonic() + 1
            for attempt in range(17):
                scheduler.require_pool_generation(settings)
                try:
                    ledger.reserve(root, task, token, demand, linux_observation, owner=owner, scopes=[descriptor])
                    break
                except AdmissionWait as error:
                    if error.code != 'resource_cpu_millicores_unknown' or attempt == 16 or time.monotonic() >= deadline:
                        raise
                    time.sleep(.06)
        scheduler.require_charged_controller(root, settings)
        # The controller is still executing inside its own populated scope at
        # exit. Keep the grant until a later known-dead + empty-scope proof.
        yield settings
