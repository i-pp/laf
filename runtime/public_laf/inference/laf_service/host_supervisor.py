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

"""Opt-in host-only supervisor. Never import this module into the Core image.

No service installation, container creation for Core, or wire-configured policy.
Python/provider hangs are not given hard execution deadlines by this monitor.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import threading
import time
import uuid
import sys
from .core import ServiceError, snapshot, check_resources
from .lifecycle import HostLifecycle, LifecycleFault, validate_sample
from .lifecycle_host import OwnedOllama
from .lifecycle_uds import UnixLifecycleServer, private_runtime


def checked_path(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ServiceError('absolute normalized host path required')
    for component in reversed((path, *path.parents)):
        try:
            if stat.S_ISLNK(component.lstat().st_mode):
                raise ServiceError('symlink host path rejected')
        except FileNotFoundError:
            if component != path:
                raise
    return path


def open_owned(path, *, create=False):
    path = checked_path(path)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC |
                 (os.O_CREAT if create else 0), 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
            raise ServiceError('owner-only regular host file required')
        now = path.lstat()
        if (now.st_dev, now.st_ino) != (info.st_dev, info.st_ino):
            raise ServiceError('host file changed during open')
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_config(path):
    fd = open_owned(path)
    with os.fdopen(fd) as stream:
        config = json.load(stream)
    if type(config) is not dict:
        raise ServiceError('host configuration object required')
    return config


def docker(*args, timeout=10):
    result = subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *args],
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ServiceError('fixed Core Docker operation failed')
    return result.stdout.strip()


class OwnedCoreStop:
    """Only inspect and stop one preconfigured container, never create/start."""
    def __init__(self, policy, *, cli=docker):
        self.policy, self.cli = policy, cli
        required = {'container_name', 'image', 'owner', 'uid', 'gid', 'network', 'mounts', 'devices',
                    'memory', 'memory_swap', 'nano_cpus', 'pids_limit'}
        if set(policy) != required:
            raise ServiceError('exact fixed Core policy required')
        # Docker represents these resource constraints as signed 64-bit values.
        # Zero/-1 mean absent or unlimited for some fields, never acceptable.
        self.limits = {'Memory': policy['memory'], 'MemorySwap': policy['memory_swap'],
                       'NanoCpus': policy['nano_cpus'], 'PidsLimit': policy['pids_limit']}
        if (any(type(value) is not int or not 0 < value <= 2**63 - 1
                for value in self.limits.values())
                or policy['memory_swap'] != policy['memory']):
            raise ServiceError('positive bounded Core limits with no extra swap required')
        for key in ('container_name', 'network'):
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', policy[key]):
                raise ServiceError('invalid fixed Core identity')
        if policy['network'] in {'host', 'bridge', 'none'}:
            raise ServiceError('dedicated private network required')
        if (not re.fullmatch(r'sha256:[0-9a-f]{64}', policy['image'])
                or len(set(policy['image'][7:])) == 1 or not policy['owner']
                or type(policy['uid']) is not int or policy['uid'] != os.getuid()
                or type(policy['gid']) is not int or policy['gid'] != os.getgid()):
            raise ServiceError('pinned image and mapped owner required')
        if type(policy['mounts']) is not list or type(policy['devices']) is not list:
            raise ServiceError('exact mounts and devices required')
        self.mounts = []
        sockets = [Path('/var/run/docker.sock'), Path('/run/docker.sock'),
                   Path(f'/run/user/{os.getuid()}/docker.sock'), Path.home() / '.docker/run/docker.sock']
        for mount in policy['mounts']:
            if set(mount) != {'Source', 'Destination', 'RW'} or type(mount['RW']) is not bool:
                raise ServiceError('invalid fixed mount')
            source = checked_path(mount['Source'])
            if not source.exists() or stat.S_ISSOCK(source.stat().st_mode):
                raise ServiceError('existing non-socket mount required')
            if any(source == sock or source in sock.parents for sock in sockets):
                raise ServiceError('Docker socket exposure rejected')
            dest = Path(mount['Destination'])
            if not dest.is_absolute() or '..' in dest.parts:
                raise ServiceError('invalid mount destination')
            self.mounts.append((str(source), str(dest), mount['RW']))
        if len({m[1] for m in self.mounts}) != len(self.mounts):
            raise ServiceError('duplicate mount destination')
        for device in policy['devices']:
            if (set(device) != {'PathOnHost', 'PathInContainer', 'CgroupPermissions'}
                    or device['PathOnHost'] not in {'/dev/kfd', '/dev/dri/renderD128'}
                    or device['PathInContainer'] != device['PathOnHost']
                    or device['CgroupPermissions'] != 'rwm'):
                raise ServiceError('fixed GPU devices only')

    def inspect(self):
        p = self.policy
        cid = self.cli('ps', '-aq', '--filter', 'name=^/' + p['container_name'] + '$')
        if not cid:
            return None
        if not re.fullmatch('[0-9a-f]{12,64}', cid):
            raise ServiceError('ambiguous fixed Core identity')
        info = json.loads(self.cli('inspect', cid))[0]
        self.validate(info)
        return cid, info

    def validate(self, info):
        p = self.policy
        host, conf = info['HostConfig'], info['Config']
        net = json.loads(self.cli('network', 'inspect', p['network']))[0]
        observed = [(m['Source'], m['Destination'], m['RW']) for m in info['Mounts']]
        if (any(type(host.get(key)) is not int or host[key] != value
                for key, value in self.limits.items())
                or info['Name'] != '/' + p['container_name'] or info['Image'] != p['image']
                or conf.get('Labels', {}).get('laf.core.owner') != p['owner']
                or conf.get('User') != f"{p['uid']}:{p['gid']}"
                or host.get('Privileged') is not False or host.get('PortBindings')
                or any(info['NetworkSettings'].get('Ports', {}).values())
                or host.get('ReadonlyRootfs') is not True or set(host.get('CapDrop') or []) != {'ALL'}
                or host.get('RestartPolicy', {}).get('Name') not in {'no', ''}
                or host.get('CapAdd') or host.get('DeviceRequests') or host.get('Devices', []) != p['devices']
                or 'no-new-privileges:true' not in (host.get('SecurityOpt') or [])
                or host.get('PidMode', '') != '' or host.get('IpcMode') != 'private'
                or host.get('NetworkMode') != p['network']
                or set(info['NetworkSettings']['Networks']) != {p['network']}
                or net.get('Internal') is not True or net.get('Driver') != 'bridge'
                or sorted(observed) != sorted(self.mounts)
                or type(info['State']['Running']) is not bool):
            raise ServiceError('fixed Core ownership/security mismatch')

    def running(self):
        found = self.inspect()
        return False if found is None else found[1]['State']['Running']

    def stop(self):
        found = self.inspect()
        if found is None:
            return
        cid, info = found
        if info['State']['Running']:
            self.cli('stop', '-t', '3', cid, timeout=10)
        # Inspect the same immutable identity, not a replacement with the name.
        info = json.loads(self.cli('inspect', cid))[0]
        self.validate(info)
        if info['State']['Running'] is not False:
            raise ServiceError('Core stop unconfirmed')


class HostSupervisor:
    def __init__(self, config, config_path, *, ollama_factory=OwnedOllama,
                 core_factory=OwnedCoreStop, read_resources=snapshot,
                 server_factory=UnixLifecycleServer, clock=time.monotonic, pressure_reader=None):
        self.locks, self.servers, self.threads = [], [], []
        self.closed = threading.Event()
        self.clock, self.port = clock, None
        self.resource_authority = None
        if 'rolling_swap_policy' in config:
            from .swap_window import HostPressureAuthority
            self.resource_authority = HostPressureAuthority(
                config['rolling_swap_policy'], pressure_reader or (lambda: snapshot(swap_capacity=True)), clock)
        self.panic_lock = threading.RLock()
        self.panic_serial = 0
        self.awaiting_core = self.resource_authority is not None
        self.started = False
        self.released = False
        self.cleanup_confirmed = False
        self.lease_seconds = config['lease_seconds']
        if (type(self.lease_seconds) not in {int, float} or not math.isfinite(self.lease_seconds)
                or not 1 <= self.lease_seconds <= 30):
            raise ServiceError('bounded 1..30 second lease required')
        try:
            runtime = private_runtime(checked_path(config['runtime_dir']), os.getuid())
            state = private_runtime(checked_path(config['state_dir']), os.getuid())
            for path, create in [(config['generation_lock'], False), (state / 'supervisor.lock', True)]:
                fd = open_owned(path, create=create)
                self.locks.append(fd)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.core = core_factory(config['core'])
            # Never adopt or stop an unadopted running service on construction.
            if self.core.running():
                raise ServiceError('active Core must be stopped explicitly before supervisor startup')
            provider = ollama_factory(config['ollama'], config_path)
            if provider.running():
                raise ServiceError('active Ollama must be stopped explicitly before supervisor startup')
            self.port = HostLifecycle(provider, read_resources,
                                      read_endpoint=lambda: provider.backend.address, panic_callback=self.panic,
                                      swap_limit_mib=config.get('swap_limit_mib', 512),
                                      resource_authority=self.resource_authority)
            for control in (False, True):
                self.servers.append(server_factory(self.port, runtime, control=control))
            self.started_at = clock()
        except BaseException:
            self._release()
            raise

    def start(self):
        if self.started or self.closed.is_set():
            raise ServiceError('supervisor cannot restart')
        self.started = True
        self.started_at = self.clock()
        for target in [s.serve_forever for s in self.servers] + [self._monitor]:
            thread = threading.Thread(target=target, daemon=True)
            self.threads.append(thread)
            thread.start()

    def tick(self):
        with self.panic_lock:
            with self.port.state_lock:
                fault = self.port.safety_fault
                confirmed = self.cleanup_confirmed
            if fault:
                # Safety recovery must not depend on another resource violation or
                # an expired lease: a prior panic may have had an uncertain outcome.
                if not confirmed:
                    self.panic(fault)
                return
            try:
                if self.resource_authority is None:
                    check_resources(validate_sample(self.port.read_resources()), self.port.baseline, self.port.swap_limit_mib)
                else:
                    decision = self.resource_authority.observe()
                    if decision['action'] not in {'admit', 'warmup'}:
                        raise ServiceError('rolling host pressure guard')
                    if decision['action'] == 'warmup' and self.core.running() is False:
                        if self.port.provider.running() is not False:
                            raise ServiceError('provider running during host warmup')
                        return
                if self.awaiting_core:
                    if self.core.running() is False:
                        if self.port.provider.running() is not False:
                            raise ServiceError('provider running before explicit Core start')
                        return
                    self.awaiting_core = False
                    self.started_at = self.clock()
                with self.port.state_lock:
                    last = self.port.last_sample
                if self.clock() - (self.started_at if last is None else last) > self.lease_seconds:
                    raise ServiceError('Core sample lease expired')
            except Exception:
                self.panic('host resource guard or Core lease failed')

    def observe_pressure(self, window, now, sample):
        """Host-local experimental pressure hook; never exposed over Core RPC.

        Admission/drain coordination is not wired yet. Conservatively use the
        existing panic barrier on pressure instead of assuming the Core is idle.
        This supplements, and does not replace, the existing resource guard.
        A returned advisory is never an execution permit; provider operations
        must still validate their current epoch/generation and safety state.
        """
        with self.panic_lock:
            with self.port.state_lock:
                if self.port.closed or self.closed.is_set():
                    raise ServiceError('host supervisor stopping')
                if self.port.safety_fault:
                    return {'action': 'stop', 'reason': 'existing host safety latch'}
                identity = (self.port.epoch, self.port.generation, self.panic_serial)
            try:
                decision = window.observe(now, sample, idle=False)
            except Exception:
                decision = window.stop('pressure observation failed')
            with self.port.state_lock:
                if (self.port.closed or self.closed.is_set() or self.port.safety_fault
                        or identity != (self.port.epoch, self.port.generation, self.panic_serial)):
                    return {'action': 'stop', 'reason': 'pressure observation superseded'}
            if decision['action'] in {'stop', 'drain', 'unload_owned'}:
                # panic fences generations before stopping only verified owned
                # Core/Ollama processes. Unknown cleanup keeps the latch.
                confirmed = self.panic('rolling pressure guard requested stop')
                return {'action': 'stopped' if confirmed else 'stop_unconfirmed',
                        'reason': decision['reason']}
            return decision

    def _monitor(self):
        while not self.closed.wait(.5):
            self.tick()

    def panic(self, reason):
        port = self.port
        # Invalidate admitted starts before waiting for any provider or lock.
        with port.state_lock:
            self.panic_serial += 1
            if port.closed:
                raise LifecycleFault('host supervisor closed')
            if not port.safety_fault:
                port.generation += 1
            port.safety_fault = port.safety_fault or reason
            port.stopping = True
        with self.panic_lock:
            with port.state_lock:
                if port.closed:
                    raise LifecycleFault('host supervisor closed')
                # A queued emergency may outlive an owner recovery. Re-establish
                # its fence rather than performing an unfenced late cleanup.
                if not port.safety_fault:
                    self.panic_serial += 1
                    port.generation += 1
                    port.safety_fault = reason
                    port.stopping = True
                self.cleanup_confirmed = False
            try:
                self.core.stop()
                if self.core.running() is not False:
                    raise ServiceError('Core stop unconfirmed')
                # Cooperative in-flight ensure must observe the generation fence.
                with port._operation(None):
                    port._confirm_stop(None)
                with port.state_lock:
                    self.cleanup_confirmed = True
                return True
            except Exception:
                # Remain fenced; client stop never clears this safety fault.
                return False

    def recover(self, *, cancelled=lambda: False):
        """Host-owner-only recovery. Never exposed as a Core lifecycle operation.

        Core stays stopped; the owner must explicitly restart it after success.
        A lost result is reconciled with the new host epoch, never replayed.
        """
        if self.closed.is_set() or cancelled():
            raise ServiceError('host supervisor stopping')
        with self.port.state_lock:
            if not self.port.safety_fault:
                raise ServiceError('host recovery requires an existing safety latch')
            prior_serial = self.panic_serial
        if not self.panic('operator resource recovery requested'):
            raise ServiceError('recovery provider cleanup unconfirmed')
        with self.panic_lock, self.port._operation(None):
            with self.port.state_lock:
                serial = prior_serial + 1
                if self.panic_serial != serial:
                    raise ServiceError('host recovery superseded by another panic')
                epoch, generation = self.port.epoch, self.port.generation
            authority = self.resource_authority
            read_quiet = (authority.recovery_sample if authority is not None
                          else lambda: validate_sample(self.port.read_resources()))
            # Rolling cooldown starts after confirmed cleanup. Waiting the full
            # duration here is conservative even if the original pause is older.
            duration = (max(5.0, authority.policy.quiet_seconds, authority.policy.cooldown_seconds)
                        if authority is not None else 5.0)
            started = previous_time = self.clock()
            first = read_quiet()
            for _ in range(math.ceil(duration / .5)):
                if self.closed.is_set() or cancelled():
                    raise ServiceError('host supervisor stopping')
                time.sleep(0.5)
                current = read_quiet()
                check_resources(current, current, self.port.swap_limit_mib)
                if authority is not None:
                    now = self.clock()
                    if now <= previous_time or now - previous_time > authority.policy.max_gap_seconds:
                        raise ServiceError('rolling recovery telemetry interval invalid')
                    previous_time = now
                quiet_fields = ('swap_used', 'pswpin', 'pswpout')
                if authority is not None:
                    quiet_fields += ('swap_total', 'swap_free')
                if current['psi_full_avg10'] > 1 or any(
                    current[key] != first[key] for key in quiet_fields
                ):
                    raise ServiceError('host recovery requires continuous quiet telemetry')
            if authority is not None and self.clock() - started < duration:
                raise ServiceError('rolling recovery quiet duration incomplete')
            if self.core.running() is not False or self.port.provider.running() is not False:
                raise ServiceError('recovery providers no longer stopped')
            with self.port.state_lock:
                if (self.closed.is_set() or cancelled() or self.port.closed
                        or self.panic_serial != serial or self.port.epoch != epoch
                        or self.port.generation != generation or not self.cleanup_confirmed):
                    raise ServiceError('host recovery invalidated')
                if self.resource_authority is not None:
                    self.resource_authority.reset_after_owner_recovery()
                self.port.baseline = current
                self.port.epoch = uuid.uuid4().hex
                self.port.generation = 0
                self.port.stopped_generation = 0
                self.port.stopping = False
                self.port.fault = self.port.safety_fault = None
                self.port.last_sample = None
                self.awaiting_core = True
                self.started_at = self.clock()
                return {'epoch': self.port.epoch, 'core_restart_required': True}

    def _release(self):
        # Panic can act on Core outside operation_lock. Drain it too and retain
        # both fences until endpoint/ownership release is complete.
        with self.panic_lock:
            if self.released:
                return
            if self.port is not None:
                with self.port._operation(None):
                    with self.port.state_lock:
                        self.port.closed = True
                        self.port.generation += 1
                    self._release_files()
            else:
                self._release_files()

    def _release_files(self):
        self.closed.set()
        for server in self.servers:
            server.close()
        for fd in reversed(self.locks):
            os.close(fd)
        self.locks.clear()
        self.released = True
        # Lock files deliberately persist: unlinking permits split lock domains.

    def close(self):
        if self.released:
            return
        self.closed.set()
        if self.started:
            if not self.panic('host supervisor shutdown'):
                raise ServiceError('shutdown unconfirmed; retaining ownership locks')
        self._release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = read_config(args.config)
    supervisor = HostSupervisor(config, args.config)
    stopping = threading.Event()
    recovery = threading.Event()
    recovering = threading.Event()
    def request_recovery(*_):
        # Coalesce pending requests and discard duplicates during the transaction.
        if not recovering.is_set():
            recovery.set()
    signal.signal(signal.SIGUSR1, request_recovery)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    supervisor.start()
    try:
        while not stopping.wait(.5):
            if recovery.is_set():
                recovering.set()
                recovery.clear()
                try:
                    supervisor.recover(cancelled=stopping.is_set)
                    print('Host recovery complete; explicitly restart Core.', flush=True)
                except Exception:
                    print('Host recovery failed; safety latch retained.', file=sys.stderr, flush=True)
                finally:
                    recovery.clear()
                    recovering.clear()
    finally:
        # Keep process ownership on an uncertain shutdown; never implicitly
        # release flock by exiting while the owned providers remain unconfirmed.
        while True:
            try:
                supervisor.close()
                break
            except ServiceError:
                time.sleep(.5)


if __name__ == '__main__':
    main()
