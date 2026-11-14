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

"""Only service-owned Strata processes and labeled Docker containers are controlled."""
from __future__ import annotations
import http.client
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time
from .core import GIB, ServiceError, snapshot

IMAGE = 'sha256:d4ae7c574945c7432c8c02ae63fa81ad555854d9fcbc29d02542a4dd69833a85'
NAMES = {'coder-next': 'laf-coder-next', 'gpt-oss-120b': 'laf-gpt-oss-120b'}

# Changed 2026-11-11: optional fixed host policy; no client-supplied image or limits.
_IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}\Z')
_IMAGE_REF = re.compile(r'[a-z0-9][a-z0-9._-]*(?::[0-9]{1,5})?(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*){0,2}@sha256:[0-9a-f]{64}\Z')


def provider_policy(value):
    fields = {'image', 'version', 'memory_bytes', 'cpus', 'pids_limit'}
    if type(value) is not dict or set(value) != fields:
        raise ServiceError('exact fixed Ollama provider policy required')
    image, version = value['image'], value['version']
    if (type(image) is not str or len(image) > 512
            or not (_IMAGE_ID.fullmatch(image) or _IMAGE_REF.fullmatch(image))):
        raise ServiceError('installed immutable Ollama image identity required')
    if type(version) is not str or not re.fullmatch(r'[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}', version):
        raise ServiceError('explicit numeric Ollama version required')
    for key, minimum, maximum in (('memory_bytes', GIB, 128 * GIB), ('cpus', 1, 32), ('pids_limit', 64, 4096)):
        if type(value[key]) is not int or not minimum <= value[key] <= maximum:
            raise ServiceError('bounded integer Ollama resource limits required')
    return dict(value)


class Backends:
    def __init__(self, config, config_path):
        self.config, self.config_path = config, str(config_path)
        self.custom_provider = 'ollama_provider' in config
        self.provider = provider_policy(config['ollama_provider']) if self.custom_provider else {
            'image': IMAGE, 'version': '0.40.15', 'memory_bytes': 32 * GIB, 'cpus': 8, 'pids_limit': 512}
        self.provider_image_id = None if self.custom_provider else IMAGE
        self.owner = str(Path(config['state_dir']).resolve())
        self.cid = None
        self.address = None
        self.flash = None
        self.engine_pid = None
        self.flash_lines = queue.Queue(maxsize=64)
        self.lock = threading.RLock()
        self.active_model = None
        self.flash_base = None
        self.connection = None
        self.closing = False
        self.lifecycle_lock = threading.RLock()

    def resolve_provider_image(self):
        if self.provider_image_id is not None:
            return self.provider_image_id
        # GET-only local image inspection. Never pull, build, infer a tag or stop a provider.
        reference = self.provider['image']
        try:
            objects = json.loads(self.docker('image', 'inspect', '--', reference))
            if type(objects) is not list or len(objects) != 1 or type(objects[0]) is not dict:
                raise ValueError()
            image = objects[0]
            identifier = image['Id']
            if (type(identifier) is not str or not _IMAGE_ID.fullmatch(identifier)
                    or image.get('Os') != 'linux' or type(image.get('Config')) is not dict):
                raise ValueError()
            volumes = image['Config'].get('Volumes')
            if volumes is not None and (type(volumes) is not dict or volumes):
                raise ValueError()
            if _IMAGE_ID.fullmatch(reference):
                if identifier != reference:
                    raise ValueError()
            else:
                digests = image.get('RepoDigests')
                if type(digests) is not list or not all(type(item) is str for item in digests) or reference not in digests:
                    raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ServiceError('installed Ollama image identity or implicit mounts rejected') from None
        self.provider_image_id = identifier
        return identifier

    def docker(self, *args, timeout=30):
        p = subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *args],
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode:
            # No command arguments, logs, or user data enter public errors.
            raise ServiceError('owned Docker operation failed: ' + args[0])
        return p.stdout.strip()

    def inspect_owned(self):
        if not self.cid:
            return None
        info = json.loads(self.docker('inspect', self.cid))[0]
        if info['Config'].get('Labels', {}).get('laf.inference.owner') != self.owner or info['Image'] != self.resolve_provider_image():
            raise ServiceError('container ownership or image identity changed')
        host = info['HostConfig']
        expected_network = self.config['docker_network']
        if (host['Privileged'] or host['PortBindings'] or host.get('NetworkMode') != expected_network
                or set(info['NetworkSettings']['Networks']) != {expected_network}
                or not host.get('ReadonlyRootfs') or set(host.get('CapDrop') or []) != {'ALL'}
                or 'no-new-privileges:true' not in (host.get('SecurityOpt') or [])
                or info['Config'].get('User') != f'{os.getuid()}:{os.getgid()}'
                or host.get('Memory') != self.provider['memory_bytes'] or host.get('MemorySwap') != self.provider['memory_bytes']
                or host.get('NanoCpus') != self.provider['cpus'] * 10**9 or host.get('PidsLimit') != self.provider['pids_limit']
                or host.get('CapAdd') or host.get('DeviceRequests')
                or host.get('IpcMode') != 'private' or host.get('PidMode', '') != ''):
            raise ServiceError('owned container security configuration changed')
        devices = [{'PathOnHost': path, 'PathInContainer': path, 'CgroupPermissions': 'rwm'}
                   for path in ('/dev/kfd', '/dev/dri/renderD128')]
        if host.get('Devices') != devices:
            raise ServiceError('owned container device configuration changed')
        expected_mounts = {
            '/weights': (str(Path(self.config['weights']).resolve()), False),
            '/input': (str(Path(self.config['state_dir']).resolve() / 'input'), False),
            '/store': (str(Path(self.config['state_dir']).resolve() / 'store'), True)}
        observed = {m['Destination']: (m['Source'], m['RW']) for m in info['Mounts']}
        if observed != expected_mounts:
            raise ServiceError('owned container mounts changed')
        expected_env = {'OLLAMA_HOST=0.0.0.0:30000', 'OLLAMA_MODELS=/store',
                        'OLLAMA_MAX_LOADED_MODELS=1', 'OLLAMA_NUM_PARALLEL=1', 'OLLAMA_NO_CLOUD=1'}
        if not expected_env <= set(info['Config'].get('Env') or []):
            raise ServiceError('owned container environment changed')
        return info

    def start_ollama(self, check=lambda: None):
        with self.lifecycle_lock:
            check()
            return self._start_ollama(check)

    def _start_ollama(self, check):
        if self.closing:
            raise ServiceError('service closing')
        image = self.resolve_provider_image()
        if self.cid:
            info = self.inspect_owned()
            if not info['State']['Running']:
                check()
                self.docker('start', self.cid)
                info = self.inspect_owned()
            self.address = info['NetworkSettings']['Networks'][self.config['docker_network']]['IPAddress']
            return self.wait_ollama(check)
        network = self.config['docker_network']
        net = json.loads(self.docker('network', 'inspect', network))[0]
        if not net.get('Internal'):
            raise ServiceError('existing internal Docker network required')
        name = self.config['container_name']
        existing = self.docker('ps', '-aq', '--filter', 'name=^/' + name + '$')
        if existing:
            self.cid = existing
            info = self.inspect_owned()
            self.docker('stop', '-t', '3', self.cid)
            self.active_model = None
        else:
            state = Path(self.config['state_dir'])
            for sub in ['store', 'input']:
                (state / sub).mkdir(mode=0o700, parents=True, exist_ok=True)
            files = {'coder-next': 'qwen-coder-next/Qwen3-Coder-Next-Q4_K_M-*.gguf',
                     'gpt-oss-120b': 'gpt-oss-120b/gpt-oss-120b-MXFP4.gguf'}
            for model, path in files.items():
                (state / 'input' / (model + '.Modelfile')).write_text('FROM /weights/' + path + '\n')
            args = ['create', '--pull=never', '--name', name, '--label', 'laf.inference.owner=' + self.owner,
                    '--memory', str(self.provider['memory_bytes']), '--memory-swap', str(self.provider['memory_bytes']),
                    '--cpus', str(self.provider['cpus']), '--pids-limit', str(self.provider['pids_limit']),
                    '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=512m', '--ipc', 'private',
                    '--network', network, '--user', f'{os.getuid()}:{os.getgid()}', '--device', '/dev/kfd',
                    '--device', '/dev/dri/renderD128', '--group-add', str(os.stat('/dev/kfd').st_gid),
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--init', '--restart', 'no',
                    '--log-opt', 'max-size=10m', '--log-opt', 'max-file=2', '--entrypoint', '/bin/ollama',
                    '--mount', f'type=bind,source={self.config["weights"]},target=/weights,readonly',
                    '--mount', f'type=bind,source={state / "input"},target=/input,readonly',
                    '--mount', f'type=bind,source={state / "store"},target=/store']
            env = {'HOME': '/tmp', 'OLLAMA_HOST': '0.0.0.0:30000', 'OLLAMA_MODELS': '/store',
                   'OLLAMA_MAX_LOADED_MODELS': '1', 'OLLAMA_NUM_PARALLEL': '1',
                   'OLLAMA_CONTEXT_LENGTH': '8192', 'OLLAMA_FLASH_ATTENTION': '1',
                   'OLLAMA_KEEP_ALIVE': '-1', 'OLLAMA_NO_CLOUD': '1', 'OLLAMA_NOPRUNE': '1', 'OLLAMA_NOHISTORY': '1'}
            for key, value in env.items():
                args += ['--env', key + '=' + value]
            self.cid = self.docker(*args, image, 'serve')
        # Reconcile the stopped container before it can execute a new image.
        self.inspect_owned()
        self.wait_idle(check)
        check()
        self.docker('start', self.cid)
        info = self.inspect_owned()
        self.address = info['NetworkSettings']['Networks'][network]['IPAddress']
        return self.wait_ollama(check)

    def wait_ollama(self, check):
        until = time.monotonic() + 30
        while time.monotonic() < until:
            check()
            try:
                if self.api('/api/version', timeout=2).get('version') == self.provider['version']:
                    return
            except (OSError, ServiceError, ValueError):
                time.sleep(.3)
        raise ServiceError('pinned Ollama not ready')

    def wait_idle(self, check=lambda: None):
        until = time.monotonic() + 30
        stable = 0
        while time.monotonic() < until:
            check()
            stable = stable + 1 if snapshot()['vram_used'] < 3 * GIB else 0
            if stable >= 2:
                return
            time.sleep(.5)
        raise ServiceError('GPU idle recovery unconfirmed; no new load')

    def api(self, path, body=None, timeout=10):
        if not self.address:
            raise ServiceError('Ollama not initialized')
        conn = http.client.HTTPConnection(self.address, 30000, timeout=timeout)
        with self.lock:
            self.connection = conn
        try:
            conn.request('GET' if body is None else 'POST', path,
                         None if body is None else json.dumps(body).encode(), {'Content-Type': 'application/json'})
            response = conn.getresponse()
            raw = response.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2 or response.status != 200:
                raise ServiceError('Ollama API failed or response exceeded bound')
            result = json.loads(raw)
            if 'error' in result:
                raise ServiceError('Ollama rejected request')
            return result
        finally:
            conn.close()
            with self.lock:
                if self.connection is conn:
                    self.connection = None

    def verify_models(self):
        tags = {m['name'] for m in self.api('/api/tags')['models']}
        if not {name + ':latest' for name in NAMES.values()} <= tags:
            raise ServiceError('models not prepared; run --prepare-models first')

    def import_models(self):
        self.start_ollama()
        tags = {m['name'] for m in self.api('/api/tags')['models']}
        for key, name in NAMES.items():
            if name + ':latest' not in tags:
                self.docker('exec', '--env', 'OLLAMA_HOST=http://127.0.0.1:30000', self.cid,
                            '/bin/ollama', 'create', name, '-f', '/input/' + key + '.Modelfile', timeout=900)

    def start_flash(self, check=lambda: None):
        with self.lifecycle_lock:
            check()
            return self._start_flash(check)

    def _start_flash(self, check):
        if self.closing:
            raise ServiceError('service closing')
        if self.flash_alive():
            return
        if self.flash is not None:
            self._abort()
        check()
        self.wait_idle(check)
        check()
        self.flash_lines = queue.Queue(maxsize=64)
        env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'OMP_NUM_THREADS': '8'}
        self.flash = subprocess.Popen([self.config['tokenizer_python'], '-B',
                                      str(Path(__file__).with_name('flash_worker.py')), self.config_path],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, bufsize=1, start_new_session=True, env=env)
        process, lines = self.flash, self.flash_lines
        def drain():
            try:
                for line in process.stdout:
                    if len(line) > 2 * 1024**2:
                        break
                    lines.put(line, timeout=1)
            finally:
                try:
                    lines.put(None, timeout=1)
                except queue.Full:
                    pass
        threading.Thread(target=drain, daemon=True).start()
        ready = self.read_flash(time.monotonic() + 90, check)
        if (not isinstance(ready, dict) or ready.get('ready') is not True
                or type(ready.get('engine_pid')) is not int or ready['engine_pid'] <= 0):
            raise ServiceError('Flash readiness protocol mismatch')
        self.engine_pid = ready['engine_pid']
        if not self.flash_alive():
            raise ServiceError('Flash engine identity unavailable')
        self.flash_base = snapshot()

    def read_flash(self, deadline, check):
        while time.monotonic() < deadline:
            check()
            try:
                line = self.flash_lines.get(timeout=.2)
            except queue.Empty:
                continue
            if line is None:
                raise ServiceError('Flash worker stopped')
            obj = json.loads(line)
            if 'error' in obj:
                raise ServiceError('Flash request rejected')
            return obj
        raise ServiceError('Flash deadline exceeded')

    def unload(self, check):
        if not self.active_model:
            return
        self.api('/api/generate', {'model': NAMES[self.active_model], 'keep_alive': 0}, timeout=10)
        # api/ps alone is not a release barrier. Compare physical GPU use to
        # the Flash-only observation, and require stable readings twice.
        until = time.monotonic() + 30
        stable = 0
        while time.monotonic() < until:
            check()
            s = snapshot()
            empty = not self.api('/api/ps')['models']
            released = (s['vram_used'] <= self.flash_base['vram_used'] + 512 * 1024**2
                        and s['mem_available'] >= max(8 * GIB, self.flash_base['mem_available'] - 2 * GIB))
            stable = stable + 1 if empty and released else 0
            if stable >= 2:
                self.active_model = None
                return
            time.sleep(.5)
        raise ServiceError('old model physical release unconfirmed; next model not loaded')

    def generate(self, job, check):
        self.start_flash(check)
        if job.model == 'flash':
            self.flash.stdin.write(json.dumps({'messages': job.messages, 'max_tokens': job.max_tokens}) + '\n')
            self.flash.stdin.flush()
            return self.read_flash(job.deadline, check)
        self.start_ollama(check)
        if self.active_model != job.model:
            self.unload(check)
            check()
            self.active_model = job.model
        key = job.model
        result = self.api('/api/chat', {'model': NAMES[key], 'messages': job.messages, 'stream': False,
                            'think': 'low' if key == 'gpt-oss-120b' else False, 'keep_alive': -1,
                            'options': {'num_ctx': 8192, 'num_predict': job.max_tokens, 'num_thread': 8,
                                        'num_batch': 256, 'num_gpu': 48 if key == 'coder-next' else 28,
                                        'temperature': 0, 'seed': 42}},
                          timeout=max(.1, job.deadline - time.monotonic()))
        check()
        if result.get('done') is not True or result.get('done_reason') not in {'stop', 'length'}:
            raise ServiceError('Ollama nonterminal response')
        if len(self.api('/api/ps')['models']) != 1:
            raise ServiceError('Ollama single-model invariant violated')
        return {'content': result['message'].get('content', ''),
                'thinking': result['message'].get('thinking', ''), 'finish_reason': result['done_reason'],
                'prompt_tokens': result.get('prompt_eval_count'), 'completion_tokens': result.get('eval_count'),
                'load_seconds': result.get('load_duration', 0) / 1e9}

    @staticmethod
    def group_alive(pgid):
        # Zombies do not execute or retain GPU allocations. Verify the entire
        # owned session/group, not only the tokenizer wrapper's exit status.
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields = path.read_text().rsplit(')', 1)[1].split()
                if int(fields[2]) == pgid and int(fields[3]) == pgid and fields[0] != 'Z':
                    return True
            except (OSError, ValueError, IndexError):
                continue
        return False

    @classmethod
    def stop_group(cls, process):
        pgid = process.pid
        if cls.group_alive(pgid):
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        until = time.monotonic() + 5
        while cls.group_alive(pgid) and time.monotonic() < until:
            process.poll()
            time.sleep(.05)
        if cls.group_alive(pgid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        until = time.monotonic() + 5
        while cls.group_alive(pgid) and time.monotonic() < until:
            process.poll()
            time.sleep(.05)
        if cls.group_alive(pgid):
            raise ServiceError('owned Strata process group stop unconfirmed')
        process.wait(timeout=2)

    def abort(self):
        with self.lifecycle_lock:
            self._abort()

    def _abort(self):
        with self.lock:
            conn, process = self.connection, self.flash
        if conn:
            if conn.sock:
                try:
                    conn.sock.shutdown(2)
                except OSError:
                    pass
            conn.close()
        if process:
            self.stop_group(process)
            with self.lock:
                if self.flash is process:
                    self.flash = None
                    self.engine_pid = None
        if self.cid:
            try:
                info = self.inspect_owned()
                if info['State']['Running']:
                    self.docker('stop', '-t', '3', self.cid, timeout=10)
            except Exception:
                # Caller must not advertise successful cleanup on uncertain stop.
                raise ServiceError('owned container stop could not be confirmed') from None
        self.active_model = None
        self.flash_base = None
        self.wait_idle()

    def close(self):
        self.closing = True
        self.abort()

    def flash_alive(self):
        if self.flash is None or self.flash.poll() is not None or self.engine_pid is None:
            return False
        try:
            fields = Path(f'/proc/{self.engine_pid}/stat').read_text().rsplit(')', 1)[1].split()
            return fields[0] != 'Z' and int(fields[2]) == self.flash.pid and int(fields[3]) == self.flash.pid
        except (OSError, ValueError, IndexError):
            return False

    def health(self):
        return {'flash_resident': self.flash_alive(),
                'ollama_selected': self.active_model, 'container_id': self.cid}
