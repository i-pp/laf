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


# Added 2026-11-15: fenced, read-only-device Router owner reconciliation.

"""Explicit dead-Router reconciliation. Never start, stop or load a provider.

Run in a prebounded private agentorchestrator UUID scope with the approved
host controller ceiling. The command budgets itself from recovery headroom.
"""
from contextlib import ExitStack, closing
import argparse
import fcntl
import json
import os
import shlex
import sys
import sqlite3
import subprocess
from pathlib import Path
import time
import uuid

from .core import GIB, ServiceError, check_resources, snapshot
from .gpu_observation import release_sample
from .host_supervisor import OwnedCoreStop, checked_path, open_owned, read_config
from .lifecycle import validate_sample
from .lifecycle_host import OwnedOllama
from .lifecycle_uds import private_runtime
from .local_resource_admission import LocalAdmission
from .resource_admission_core import (AdmissionWait, Ledger, _scope_path,
    linux_observation, owner_live, policy as validate_policy, resource_only, scope_empty)


def empty_scopes(candidate):
    for row in candidate['grants']:
        if json.loads(row['demand'])['provider'] is not None: continue
        for descriptor in json.loads(row['scopes']):
            group = _scope_path(descriptor)
            if not scope_empty(group):raise AdmissionWait('resource_scope_unknown')


class RouterRecovery:
    def __init__(self, config, config_path, *, core_factory=OwnedCoreStop, provider_factory=OwnedOllama,
                 read_resources=snapshot, read_device=release_sample, clock=time.monotonic, sleep=time.sleep):
        self.config, self.config_path = config, config_path
        self.policy = validate_policy(config['resource_admission'])
        if not resource_only(self.policy) or 'local_inference' not in self.policy:
            raise ValueError('Resource-only local inference policy required')
        self.profiles = self.policy['local_inference']
        self.runtime = checked_path(config['state_dir'])
        self.core_factory, self.provider_factory = core_factory, provider_factory
        self.read_resources, self.read_device, self.clock, self.sleep = read_resources, read_device, clock, sleep
        self.fences = []

    def _stopped(self):
        found = self.core.inspect()
        info = found[1] if found else None
        # Never reuse OwnedOllama's cached CID across the physical barrier.
        provider = self.provider.inspect_current()
        for item in (info, provider):
            if item is None: continue
            state = item['State']
            if (state.get('Running') is not False or state.get('Restarting') is not False
                    or type(state.get('Pid')) is not int or state['Pid'] != 0
                    or item['HostConfig'].get('RestartPolicy', {}).get('Name') not in ('no', '')):
                raise AdmissionWait('resource_scope_unknown')
        return [item['Id'] if item else None for item in (info, provider)]

    def _sample(self):
        sample = validate_sample(self.read_resources())
        device = self.read_device(self.profiles['device'])
        check_resources(sample, sample, self.config.get('swap_limit_mib', 512))
        if (not device['idle'] or device['used'] >= 3 * GIB
                or device['device'] != self.profiles['device']['pci_bdf']
                or sample['vram_total'] != device['total'] or sample['psi_full_avg10'] > 1):
            raise AdmissionWait('resource_reservation_retained')
        return sample, device

    def _fenced(self):
        for path, fd in self.fences:
            before, now = os.fstat(fd), path.lstat()
            if (now.st_dev, now.st_ino) != (before.st_dev, before.st_ino):
                raise AdmissionWait('resource_owner_unknown')

    def _proof(self, candidate, generation):
        self._fenced(); empty_scopes(candidate)
        began = self.clock()
        containers = self._stopped()
        first, device = self._sample()
        self.sleep(.5)
        last, last_device = self._sample()
        if (self.clock() - began < .5 or self._stopped() != containers
                or any(last[key] != first[key] for key in ('swap_used', 'pswpin', 'pswpout'))
                or device['device'] != last_device['device'] or device['total'] != last_device['total']):
            raise AdmissionWait('resource_reservation_retained')
        check_resources(last, first, self.config.get('swap_limit_mib', 512))
        self.proof = {'kind': 'owned_router_exit', 'owner': candidate['owner']['id'], 'generation': generation,
            'providers_stopped': True, 'scopes_empty': True, 'paired_idle': True,
            'device': last_device['device'], 'containers': containers, 'observed_at': began,
            'physical_barrier_seconds': .5,
            'quiet': {key: last[key] for key in ('swap_used', 'pswpin', 'pswpout')}}

    def _verify(self, candidate):
        self._fenced(); empty_scopes(candidate)
        if (owner_live(json.loads(candidate['owner']['identity'])) is not False
                or not 0 <= self.clock() - self.proof['observed_at'] <= self.policy['max_age_seconds']):
            raise AdmissionWait('resource_observation_stale')
        current, device = self._sample()  # Fresh kernel checks only inside SQLite.
        empty_scopes(candidate); self._fenced()
        if any(current[key] != value for key, value in self.proof['quiet'].items()):
            raise AdmissionWait('resource_reservation_retained')
        if device['device'] != self.proof['device']:
            raise AdmissionWait('resource_gpu_topology_unknown')
        # Collection itself can block or cross an ownership/clock boundary.
        # Freshness is measured after all final reads, never only before them.
        self._fenced()
        if (owner_live(json.loads(candidate['owner']['identity'])) is not False
                or not 0 <= self.clock() - self.proof['observed_at'] <= self.policy['max_age_seconds']):
            raise AdmissionWait('resource_observation_stale')
        return dict(self.proof)

    def run(self, owner, *, apply=False):
        """Target release is atomic. The diagnostic's own scope stays charged."""
        with ExitStack() as stack:
            state = private_runtime(self.runtime, os.getuid())
            for path in (checked_path(self.config['generation_lock']), state / 'supervisor.lock'):
                fd = open_owned(path)
                stack.callback(os.close, fd)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fences.append((path, fd))
            stack.callback(self.fences.clear)
            # Verify current hard ceilings before Docker, telemetry or owner writes.
            descriptor = LocalAdmission._host_scope(self)
            cap = self.profiles['controllers']['host']
            if any(cap[key] > self.policy['reserve'][key] for key in ('cpu_millicores', 'ram_bytes')):
                raise AdmissionWait('resource_controller_required')
            with closing(Ledger(self.policy)) as ledger:
                candidate, fingerprint = ledger.router_snapshot(self.runtime, owner)
                for item in ledger.db.execute("SELECT id,identity FROM admission_owners WHERE runtime=? AND role='router_recovery' AND phase='HELD'", (str(self.runtime),)).fetchall():
                    if owner_live(json.loads(item['identity'])) is False: ledger.reconcile_owner(item['id'], apply=True)
                diagnostic_owner = ledger.register_owner(self.runtime, 'router_recovery')
                stack.callback(self._close, diagnostic_owner)
                task, token = str(uuid.uuid4()), str(uuid.uuid4())
                deadline = self.clock() + 1
                for attempt in range(17):
                    try:
                        ledger.reserve(self.runtime, task, token, {'kind': 'reconciliation', 'provider': None, 'resources': cap},
                            lambda rows, previous: linux_observation(rows, previous, host=True),
                            owner=diagnostic_owner, scopes=[descriptor])
                        break
                    except AdmissionWait as error:
                        if error.code != 'resource_cpu_millicores_unknown' or attempt == 16 or self.clock() >= deadline: raise
                        self.sleep(.06)
                self.core = self.core_factory(self.config['core'])
                self.provider = self.provider_factory(self.config['ollama'], self.config_path)
                self._proof(candidate, ledger.generation)
                result = ledger.reconcile_router_owner(self.runtime, owner, fingerprint, self._verify, apply=apply)
                return {**result, 'diagnostic_grant_retained_until_scope_exit': True}

    def _close(self, owner):
        with closing(Ledger(self.policy)) as ledger: ledger.close_owner(owner)


def plan_command(config, config_path, owner, *, apply=False):
    """Print the exact bounded command; never acquire a ledger or provider."""
    if not isinstance(owner, str) or str(uuid.UUID(owner)) != owner:
        raise ValueError('Canonical owner required')
    value = validate_policy(config['resource_admission'])
    if not resource_only(value) or 'local_inference' not in value:
        raise ValueError('Resource-only local policy required')
    cap = value['local_inference']['controllers']['host']
    if any(cap[key] > value['reserve'][key] for key in ('cpu_millicores', 'ram_bytes')):
        raise AdmissionWait('resource_controller_required')
    path = checked_path(config_path)
    unit = 'agentorchestrator-' + str(uuid.uuid4()) + '.scope'
    cpu = str(cap['cpu_millicores'] // 10) + ('.' + str(cap['cpu_millicores'] % 10) if cap['cpu_millicores'] % 10 else '')
    command = ['/usr/bin/systemd-run', '--user', '--scope', '--quiet', '--collect', '--no-ask-password',
        '--expand-environment=no', '--slice=agentorchestrator.slice', '--unit=' + unit,
        '-pCPUQuota=' + cpu + '%', '-pCPUQuotaPeriodSec=100ms', '-pMemoryMax=' + str(cap['ram_bytes']),
        '-pMemorySwapMax=0', '-pTasksMax=64', sys.executable, '-B', '-m',
        'inference.laf_service.resource_recovery', '--config', str(path), '--owner', owner]
    if apply: command.append('--apply')
    return {'executes': False, 'argv': command, 'command': shlex.join(command),
            'working_directory': str(Path(__file__).resolve().parents[2]),
            'target_changes': 'apply_verified_local_release' if apply else 'target_grants_unchanged'}


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--plan', action='store_true', help='Print a bounded command without executing it')
    args = parser.parse_args(argv)
    try:
        config = read_config(args.config)
        result = (plan_command(config, args.config, args.owner, apply=args.apply) if args.plan else
                  RouterRecovery(config, args.config).run(args.owner, apply=args.apply))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, ServiceError, AdmissionWait, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({'error': getattr(error, 'code', 'resource_reconciliation_refused'), 'automatic_retry': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
