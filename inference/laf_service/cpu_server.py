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

"""Owner-only Router for one explicitly configured CPU Ollama model."""

import argparse
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid

from ..local_platform.model_management import checked_directory, private_lock, read_private_json
from .backends import installed_provider_image, provider_policy
from .core import Controller, GIB, ServiceError, check_cpu_resources, snapshot_cpu
from .core_backends import configured_profiles, manifest_digest
from .server import Handler, Server

LIMIT = 262144
REQUEST_LIMIT = 65536
CID = re.compile(r'[a-f0-9]{64}\Z')
HTTP_WIRE = (
    'set -eu; exec 3<>/dev/tcp/127.0.0.1/11434; '
    '/usr/bin/head -c "$1" >&3; /usr/bin/head -c 262145 <&3'
)
ENDPOINTS = {'/api/version', '/api/tags', '/api/ps'}
ENGINE_ENV = {'HOME': '/tmp', 'OLLAMA_HOST': '127.0.0.1:11434', 'OLLAMA_MODELS': '/models',
              'OLLAMA_NO_CLOUD': '1', 'OLLAMA_NOPRUNE': '1', 'OLLAMA_NUM_PARALLEL': '1',
              'OLLAMA_MAX_LOADED_MODELS': '1', 'NVIDIA_VISIBLE_DEVICES': 'void'}


def load_cpu_config(path):
    config = read_private_json(path)
    required = {'mode', 'runtime_dir', 'model_store', 'generation_lock', 'ollama_provider', 'ollama_profile'}
    if type(config) is not dict or set(config) - {'swap_limit_mib'} != required or config['mode'] != 'cpu-only':
        raise ServiceError('explicit single-model cpu-only configuration required')
    for key in ('runtime_dir', 'model_store', 'generation_lock'):
        if (type(config[key]) is not str or ',' in config[key]
                or not Path(config[key]).is_absolute() or '..' in Path(config[key]).parts):
            raise ServiceError('absolute private CPU Router paths required')
    runtime, store, lock = map(Path, (config['runtime_dir'], config['model_store'], config['generation_lock']))
    checked_directory(runtime)
    checked_directory(store)
    checked_directory(lock.parent)
    if (runtime.is_relative_to(store) or store.is_relative_to(runtime)
            or lock.name != 'generation.lock' or lock.is_relative_to(store)):
        raise ServiceError('separate runtime, model store and generation lock required')
    profiles = configured_profiles([config['ollama_profile']])
    config['ollama_provider'] = provider_policy(config['ollama_provider'])
    options = next(iter(profiles.values()))['options']
    if options['num_gpu'] != 0 or options['num_thread'] > config['ollama_provider']['cpus']:
        raise ServiceError('CPU Router requires num_gpu=0 and threads within its CPU limit')
    limit = config.get('swap_limit_mib', 512)
    if type(limit) is not int or not 512 <= limit <= 2048:
        raise ServiceError('CPU Router swap limit must be 512..2048 MiB')
    return config


class _HTTPBytes:
    def __init__(self, raw):
        self.raw = raw

    def makefile(self, *_args, **_kwargs):
        return io.BytesIO(self.raw)


class CPUOllama:
    requires_flash = False

    def __init__(self, config, docker_config):
        self.default_model = config['ollama_profile']['id']
        self.models = frozenset({self.default_model})
        self.profile = config['ollama_profile']
        self.policy = config['ollama_provider']
        self.runtime = Path(config['runtime_dir'])
        self.store = str(Path(config['model_store']).resolve())
        self.name = 'laf-cpu-' + hashlib.sha256(str(self.runtime.resolve()).encode()).hexdigest()[:20]
        self.owner = uuid.uuid4().hex
        self.uid = f'{os.getuid()}:{os.getgid()}'
        self.argv = ('/usr/bin/docker', '--config', str(docker_config), '--host=unix:///var/run/docker.sock')
        self.environment = {'HOME': str(self.runtime), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
        self.lifecycle = threading.RLock()
        self.image = None
        self.cid = None
        self.creation_attempted = False
        self.generation = 0
        self.ready = False
        self.blocked = False
        self.closing = False

    def docker(self, *args, timeout=8):
        try:
            result = subprocess.run([*self.argv, *args], env=self.environment, timeout=timeout,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, check=True)
        except (OSError, subprocess.SubprocessError):
            raise ServiceError('owned CPU Ollama Docker operation unconfirmed') from None
        return result.stdout.decode().strip()

    def inspect(self):
        fields = json.loads(self.docker('container', 'inspect', self.cid))
        if len(fields) != 1 or not self.cid or fields[0].get('Id') != self.cid:
            raise ServiceError('owned CPU provider identity unavailable')
        info = fields[0]
        settings = info['Config']
        host = info['HostConfig']
        attached = info['NetworkSettings']['Networks']
        devices = ('Devices', 'DeviceRequests', 'DeviceCgroupRules', 'GroupAdd', 'CapAdd')
        if (info['Name'] != '/' + self.name or info['Image'] != self.image
                or settings['Labels'].get('laf.cpu.owner') != self.owner or settings['User'] != self.uid
                or settings['Entrypoint'] != ['/bin/ollama'] or settings['Cmd'] != ['serve']
                or host['Runtime'] != 'runc' or host['NetworkMode'] != 'none'
                or not isinstance(attached, dict) or set(attached) - {'none'}
                or host['Privileged'] or host['PortBindings'] or host['PublishAllPorts']
                or any(host[key] not in (None, []) for key in devices)
                or host['PidMode'] != '' or host['UTSMode'] != '' or host['IpcMode'] != 'private'
                or host['Init'] is not True
                or host['CapDrop'] != ['ALL'] or not host['ReadonlyRootfs']
                or 'no-new-privileges:true' not in host['SecurityOpt']
                or host['RestartPolicy']['Name'] != 'no'
                or host['Memory'] != self.policy['memory_bytes'] or host['MemorySwap'] != host['Memory']
                or host['NanoCpus'] != self.policy['cpus'] * 10**9 or host['PidsLimit'] != self.policy['pids_limit']):
            raise ServiceError('owned CPU provider isolation changed')
        mounts = [(mount['Type'], mount['Source'], mount['Destination'], mount['RW']) for mount in info['Mounts']]
        if mounts != [('bind', self.store, '/models', False)]:
            raise ServiceError('owned CPU provider model mount changed')
        env = dict(item.split('=', 1) for item in settings['Env'])
        if any(env.get(name) != value for name, value in ENGINE_ENV.items()):
            raise ServiceError('owned CPU provider environment changed')
        state = info['State']
        if (type(state['Running']) is not bool or type(state['Pid']) is not int
                or state['Running'] != (state['Pid'] > 0) or state['Paused'] or state['Restarting']):
            raise ServiceError('owned CPU provider process state unconfirmed')
        return state

    def create(self):
        self.image = installed_provider_image(self.policy['image'], self.docker)
        if self.docker('ps', '-aq', '--filter', f'name=^/{self.name}$'):
            raise ServiceError('existing CPU provider requires owner reconciliation')
        self.creation_attempted = True
        memory = str(self.policy['memory_bytes'])
        flags = ['create', '--pull=never', '--name', self.name, '--label', 'laf.cpu.owner=' + self.owner,
                 '--network', 'none', '--runtime', 'runc', '--user', self.uid, '--ipc', 'private',
                 '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--read-only', '--init',
                 '--restart', 'no', '--memory', memory, '--memory-swap', memory,
                 '--cpus', str(self.policy['cpus']), '--pids-limit', str(self.policy['pids_limit']),
                 '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1',
                 '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777',
                 '--mount', f'type=bind,source={self.store},target=/models,readonly',
                 '--entrypoint', '/bin/ollama']
        for key, value in ENGINE_ENV.items():
            flags.extend(('--env', key + '=' + value))
        cid = self.docker(*flags, self.image, 'serve')
        if not CID.fullmatch(cid):
            raise ServiceError('created CPU provider identity unconfirmed')
        self.cid = cid

    def api(self, path, body=None, *, check=lambda: None, timeout=5):
        if not (path in ENDPOINTS and body is None or path == '/api/chat' and body is not None):
            raise ValueError('fixed local Ollama routes required')
        request = f'{"GET" if body is None else "POST"} {path} HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n'.encode()
        raw = b'' if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        if body is not None:
            request += f'Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n'.encode()
        request += b'\r\n' + raw
        if len(request) > REQUEST_LIMIT:
            raise ServiceError('CPU Ollama request exceeds wire bound')
        with self.lifecycle:
            check()
            if self.closing or self.blocked or not self.inspect()['Running']:
                raise ServiceError('owned CPU provider unavailable')
            checked_cid = self.cid
            generation = self.generation
            child = subprocess.Popen([*self.argv, 'exec', '-i', checked_cid, '/bin/bash', '-c',
                                      HTTP_WIRE, 'laf-cpu-http', str(len(request))],
                                     env=self.environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        with child:
            try:
                wire, _ = child.communicate(request, timeout=timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
                raise ServiceError('CPU Ollama response deadline exceeded') from None
        if child.returncode or len(wire) > LIMIT:
            raise ServiceError('CPU Ollama exchange unconfirmed')
        with self.lifecycle:
            check()
            if (self.blocked or self.closing or self.cid != checked_cid or self.generation != generation
                    or not self.inspect()['Running']):
                raise ServiceError('CPU Ollama exchange invalidated')
        try:
            response = http.client.HTTPResponse(_HTTPBytes(wire))
            response.begin()
            value = json.loads(response.read())
            if response.status != 200 or not isinstance(value, dict) or 'error' in value:
                raise ValueError()
            return value
        except (ValueError, http.client.HTTPException):
            raise ServiceError('CPU Ollama response rejected') from None

    def verify_model(self, path, check):
        rows = self.api(path, check=check).get('models')
        if (type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict
                or rows[0].get('name') != self.profile['reference']
                or manifest_digest(rows[0].get('digest')) != self.profile['digest']):
            raise ServiceError('single configured CPU model manifest unconfirmed')
        if path == '/api/ps' and (type(rows[0].get('size_vram')) is not int or rows[0]['size_vram'] != 0):
            raise ServiceError('loaded CPU provider reported GPU memory')

    def ensure(self, check):
        with self.lifecycle:
            check()
            if self.closing or self.blocked:
                raise ServiceError('CPU provider needs owner reconciliation')
            if self.cid is None:
                self.create()
            if not self.inspect()['Running']:
                check()
                if snapshot_cpu()['mem_available'] < 8 * GIB + self.policy['memory_bytes']:
                    raise ServiceError('RAM reserve cannot cover CPU provider memory limit')
                self.docker('start', self.cid)
            generation = self.generation
        end = time.monotonic() + 10
        while True:
            check()
            try:
                version = self.api('/api/version', check=check, timeout=1).get('version')
            except ServiceError:
                if time.monotonic() >= end:
                    raise ServiceError('owned CPU Ollama did not start') from None
                time.sleep(.2)
            else:
                break
        if version != self.policy['version']:
            raise ServiceError('CPU Ollama version changed')
        self.verify_model('/api/tags', check)
        with self.lifecycle:
            check()
            if self.closing or self.blocked or self.generation != generation or not self.inspect()['Running']:
                raise ServiceError('CPU provider start invalidated')
            self.ready = True

    def health(self):
        with self.lifecycle:
            try:
                state = self.inspect() if self.ready and not self.blocked and not self.closing else None
            except (ValueError, KeyError, TypeError, ServiceError, OSError):
                state = None
        return {'ready': state is not None, 'engine_running': bool(state and state['Running']),
                'flash_resident': False, 'provider_mode': 'cpu-only'}

    def generate(self, job, check):
        if job.model != self.default_model:
            raise ServiceError('unknown CPU model')
        self.ensure(check)
        options = dict(self.profile['options'], num_predict=job.max_tokens)
        result = self.api('/api/chat',
                          {'model': self.profile['reference'], 'messages': job.messages, 'stream': False,
                           'think': False, 'keep_alive': -1, 'options': options},
                          check=check, timeout=max(.1, job.deadline - time.monotonic()))
        count, message = result.get('eval_count'), result.get('message')
        if (result.get('done') is not True or result.get('model') != self.profile['reference']
                or result.get('done_reason') not in {'stop', 'length'} or type(count) is not int
                or not 0 <= count <= job.max_tokens or not isinstance(message, dict)
                or not isinstance(message.get('content'), str)):
            raise ServiceError('terminal CPU Ollama response unconfirmed')
        self.verify_model('/api/ps', check)
        return {'content': message['content'], 'thinking': '', 'finish_reason': result['done_reason'],
                'prompt_tokens': result.get('prompt_eval_count'), 'completion_tokens': count}

    def abort(self):
        with self.lifecycle:
            self.generation += 1
            self.blocked = True
            if self.cid is None:
                if self.creation_attempted:
                    raise ServiceError('CPU provider creation outcome requires owner reconciliation')
            else:
                if self.inspect()['Running']:
                    self.docker('stop', '--time', '3', self.cid)
                state = self.inspect()
                if state['Running'] or state['Pid'] != 0:
                    raise ServiceError('owned CPU provider stop unconfirmed')
            self.blocked = False

    def close(self):
        with self.lifecycle:
            self.closing = True
            self.abort()
            if self.cid is not None:
                self.docker('rm', self.cid)
                self.cid = None


def serve_cpu(config):
    runtime = Path(config['runtime_dir'])
    address = runtime / 'service.sock'
    lock = Path(config['generation_lock'])
    with private_lock(lock.parent, lock.name), tempfile.TemporaryDirectory(prefix='.cpu-docker-', dir=runtime) as docker_config:
        if os.path.lexists(address):
            raise ServiceError('existing CPU Router socket preserved')
        engine = CPUOllama(config, docker_config)
        ctl = Controller(engine, read_resources=snapshot_cpu, start=False,
                         swap_limit_mib=config.get('swap_limit_mib', 512), check_limits=check_cpu_resources)
        old_umask = os.umask(0o177)
        try:
            server = Server(str(address), Handler)
        finally:
            os.umask(old_umask)
        info = address.lstat()
        handlers = {}

        def stop(_signum, _frame):
            ctl.stop.set()

        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.signal(signum, stop)
            server.config, server.controller = config, ctl
            engine.ensure(ctl.cancelled_idle)
            ctl.thread.start()
            ctl.monitor.start()
            # handle_request() with a finite accept timeout handles signals before
            # and during serving without a blocking shutdown thread.
            server.timeout = .2
            while not ctl.stop.is_set():
                server.handle_request()
        finally:
            server.server_close()
            try:
                ctl.close()
            finally:
                if address.exists():
                    current = address.lstat()
                    if stat.S_ISSOCK(current.st_mode) and (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                        address.unlink()
                for signum, previous in handlers.items():
                    signal.signal(signum, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    serve_cpu(load_cpu_config(args.config))


if __name__ == '__main__':
    main()
