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

"""Explicit Core backend: container-owned Flash, host-owned Ollama lifecycle.

No Docker module, CLI, socket path, host PID or host mount authority is imported.
All physical admission/release samples come from the authenticated host client.
"""
from __future__ import annotations
import http.client
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time
from .core import GIB, MODELS, ServiceError

NAMES = {'coder-next': 'laf-coder-next', 'gpt-oss-120b': 'laf-gpt-oss-120b'}


# Changed 2026-11-11: share bounded options across isolated and normal owner catalogs.
def _profiles(value, *, normal):
    if type(value) is not list or not 1 <= len(value) <= 8:
        raise ServiceError('1..8 explicit Ollama profiles required')
    profiles, references = {}, set()
    bounds = {'num_ctx': (512, 8192), 'num_thread': (1, 32), 'num_batch': (1, 512),
              'num_gpu': (0, 256), 'seed': (0, 2147483647)}
    for entry in value:
        fields = {'id', 'reference', 'options'} | ({'digest'} if normal else set())
        if type(entry) is not dict or set(entry) != fields:
            raise ServiceError('fixed profile fields required')
        key, reference, options = entry['id'], entry['reference'], entry['options']
        pattern = r'[a-z][a-z0-9-]{0,63}' if normal else r'test-[a-z0-9][a-z0-9-]{0,58}'
        if (type(key) is not str or not re.fullmatch(pattern, key) or key in profiles
                or normal and (key in MODELS or key.startswith('test-'))):
            raise ServiceError('distinct nonreserved configured model IDs required')
        if (type(reference) is not str or len(reference) > 200
                or not re.fullmatch(r'[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)?:[A-Za-z0-9][A-Za-z0-9._-]*', reference)
                or reference in references):
            raise ServiceError('distinct exact tagged local Ollama references required')
        if type(options) is not dict or set(options) != set(bounds) | {'temperature'}:
            raise ServiceError('fixed bounded profile options required')
        for name, (lower, upper) in bounds.items():
            if type(options[name]) is not int or not lower <= options[name] <= upper:
                raise ServiceError('profile option outside permitted bounds')
        if type(options['temperature']) not in (int, float) or not 0 <= options['temperature'] <= 2:
            raise ServiceError('profile temperature outside permitted bounds')
        # Keep the public validator's existing conservative context budget true.
        if options['num_ctx'] != 8192:
            raise ServiceError('profile context must preserve 8192-token request budget')
        profiles[key] = {'reference': reference, 'options': dict(options)}
        if normal:
            digest = entry['digest']
            if type(digest) is not str or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                raise ServiceError('exact configured model manifest digest required')
            profiles[key]['digest'] = digest
        references.add(reference)
    return profiles


def acceptance_profiles(value):
    return _profiles(value, normal=False)


def configured_profiles(value):
    return _profiles(value, normal=True)


def configured_selection(config):
    if ('acceptance_ollama_profiles' in config
            or not {'ollama_profiles', 'flash_enabled', 'default_model'} <= set(config)
            or type(config['flash_enabled']) is not bool):
        raise ServiceError('distinct explicit normal catalog and Flash policy required')
    profiles = configured_profiles(config['ollama_profiles'])
    models = set(profiles) | ({'flash'} if config['flash_enabled'] else set())
    default = config['default_model']
    if type(default) is not str or default not in models:
        raise ServiceError('explicit default must belong to selected model catalog')
    return profiles, config['flash_enabled'], default


def manifest_digest(value):
    if type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value):
        value = 'sha256:' + value
    if type(value) is not str or not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        raise ServiceError('engine manifest digest invalid')
    return value


def current_pid_namespace():
    namespace = os.readlink('/proc/self/ns/pid')
    if not re.fullmatch(r'pid:\[\d+\]', namespace):
        raise ServiceError('Core PID namespace unavailable')
    # Reject a host /proc view, rather than signaling IDs from another namespace.
    if int(Path('/proc/self/stat').read_text().split(' ', 1)[0]) != os.getpid():
        raise ServiceError('Core proc view does not match process namespace')
    return namespace


class CoreBackends:
    def __init__(self, config, config_path, lifecycle, *, namespace_probe=current_pid_namespace,
                 process_factory=subprocess.Popen):
        self.config, self.config_path, self.client = config, str(config_path), lifecycle
        self.normal_catalog = any(key in config for key in ('ollama_profiles', 'flash_enabled', 'default_model'))
        if self.normal_catalog:
            self.profiles, self.requires_flash, self.default_model = configured_selection(config)
        elif 'acceptance_ollama_profiles' in config:
            self.profiles = acceptance_profiles(config['acceptance_ollama_profiles'])
            self.requires_flash, self.default_model = False, None
        else:
            self.profiles, self.requires_flash, self.default_model = None, True, 'gpt-oss-120b'
        self.names = dict(NAMES) if self.profiles is None else {key: item['reference'] for key, item in self.profiles.items()}
        self.models = frozenset(set(self.names) | ({'flash'} if self.requires_flash else set()))
        self.idle_base, self.models_verified = None, False
        self.read_resources = lifecycle.read_resources
        self.namespace_probe, self.process_factory = namespace_probe, process_factory
        self.namespace = namespace_probe()
        identity = lifecycle.status()
        if (identity['host_uid'] != os.getuid() or identity['host_pid_namespace'] == self.namespace
                or identity.get('emergency_stop_supported') is not True):
            raise ServiceError('Core requires mapped owner UID and a separate PID namespace')
        self.uid = os.getuid()
        self.host_epoch = identity['epoch']
        self.flash = None
        self.flash_drain = None
        self.engine_pid = None
        self.wrapper_start = None
        self.engine_start = None
        self.flash_lines = queue.Queue(maxsize=64)
        self.lock = threading.RLock()
        self.lifecycle_lock = threading.RLock()
        self.abort_lock = threading.RLock()
        self.active_model, self.flash_base, self.connection = None, None, None
        self.address = None
        self.closing, self.stopping, self.generation = False, False, 0

    def _namespace(self):
        if self.namespace_probe() != self.namespace:
            raise ServiceError('Core process namespace changed; no process signaling')

    def _guard(self, generation, check):
        self._namespace()
        with self.lock:
            if self.closing or self.stopping or generation != self.generation:
                raise ServiceError('Core lifecycle stopping or invalidated')
        check()

    def start_ollama(self, check=lambda: None):
        with self.lock:
            generation = self.generation
        self._guard(generation, check)
        # Do not hold the Flash lifecycle lock across a remote ensure: abort must
        # invalidate that ensure through the independent control connection.
        result = self.client.ensure(lambda: self._guard(generation, check))
        self._guard(generation, check)
        endpoint = result['endpoint']
        try:
            if type(endpoint) is not str:
                raise ValueError()
            address = ipaddress.ip_address(endpoint)
            if (not address.is_private or address.is_loopback or address.is_link_local
                    or address.is_multicast or address.is_unspecified):
                raise ValueError()
        except ValueError:
            raise ServiceError('fixed host-owned private Ollama endpoint unavailable') from None
        with self.lock:
            if generation != self.generation or self.stopping:
                raise ServiceError('Core start invalidated')
            self.address = str(address)

    def start_flash(self, check=lambda: None):
        if not self.requires_flash:
            raise ServiceError('Flash disabled by explicit catalog')
        with self.lifecycle_lock:
            with self.lock:
                generation = self.generation
            guard = lambda: self._guard(generation, check)
            guard()
            if self.flash_alive():
                return
            if self.flash is not None:
                raise ServiceError('stale Flash requires explicit provider stop')
            self.wait_idle(guard)
            guard()
            self.flash_lines = queue.Queue(maxsize=64)
            env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'OMP_NUM_THREADS': '8'}
            self.flash = self.process_factory([self.config['tokenizer_python'], '-B',
                str(Path(__file__).with_name('core_flash_worker.py')), self.config_path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
                start_new_session=True, env=env)
            process, records = self.flash, self.flash_lines
            wrapper = self._process(process.pid)
            if wrapper is None or wrapper['pgid'] != process.pid or wrapper['sid'] != process.pid or wrapper['uids'] != {self.uid}:
                raise ServiceError('Flash wrapper identity unconfirmed')
            self.wrapper_start = wrapper['start']
            def drain():
                try:
                    for line in process.stdout:
                        if len(line) > 2 * 1024**2:
                            break
                        records.put(line, timeout=1)
                except (queue.Full, OSError, ValueError):
                    pass
                finally:
                    try: records.put(None, timeout=1)
                    except queue.Full: pass
            self.flash_drain = threading.Thread(target=drain, daemon=True)
            self.flash_drain.start()
            deadline = time.monotonic() + 90
            launch = self.read_flash(deadline, guard)
            if (type(launch) is not dict or set(launch) != {'starting', 'engine_pid'}
                    or launch['starting'] is not True or type(launch['engine_pid']) is not int or launch['engine_pid'] <= 0):
                raise ServiceError('Flash launch identity protocol mismatch')
            self.engine_pid = launch['engine_pid']
            engine = self._process(self.engine_pid)
            if (engine is None or engine['pgid'] != process.pid or engine['sid'] != process.pid
                    or engine['uids'] != {self.uid}):
                raise ServiceError('Flash engine identity unconfirmed')
            self.engine_start = engine['start']
            ready = self.read_flash(deadline, guard)
            if (type(ready) is not dict or ready.get('ready') is not True
                    or ready.get('engine_pid') != self.engine_pid or not self.flash_alive()):
                raise ServiceError('Flash readiness or owned engine identity unavailable')
            self.flash_base = self.read_resources()

    def _process(self, pid):
        self._namespace()
        try:
            fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            uid_line = next(x for x in Path(f'/proc/{pid}/status').read_text().splitlines() if x.startswith('Uid:'))
            return {'state': fields[0], 'pgid': int(fields[2]), 'sid': int(fields[3]),
                    'start': int(fields[19]), 'uids': set(map(int, uid_line.split()[1:]))}
        except (OSError, ValueError, IndexError, StopIteration):
            return None

    def _owned_group_alive(self, process):
        self._namespace()
        group = []
        for path in Path('/proc').glob('[0-9]*/stat'):
            info = self._process(int(path.parent.name))
            if info and info['state'] != 'Z' and info['pgid'] == process.pid and info['sid'] == process.pid:
                group.append(info)
        if not group:
            return False
        if any(info['uids'] != {self.uid} for info in group):
            raise ServiceError('Flash process group owner changed; no signal sent')
        known = False
        for pid, start in [(process.pid, self.wrapper_start), (self.engine_pid, self.engine_start)]:
            if pid is not None and start is not None:
                info = self._process(pid)
                known |= bool(info and info['state'] != 'Z' and info['start'] == start
                              and info['pgid'] == process.pid and info['sid'] == process.pid
                              and info['uids'] == {self.uid})
        if not known:
            # A signaled engine can disappear between the group scan and the
            # identity probe. Reconfirm disappearance before declaring failure.
            remaining = []
            for path in Path('/proc').glob('[0-9]*/stat'):
                info = self._process(int(path.parent.name))
                if info and info['state'] != 'Z' and info['pgid'] == process.pid and info['sid'] == process.pid:
                    remaining.append(info)
            if not remaining:
                return False
            raise ServiceError('Flash group identity unconfirmed; no signal sent')
        return True

    def stop_group(self, process):
        for sig in [signal.SIGTERM, signal.SIGKILL]:
            if not self._owned_group_alive(process):
                break
            try: os.killpg(process.pid, sig)
            except ProcessLookupError: pass
            until = time.monotonic() + 5
            while self._owned_group_alive(process) and time.monotonic() < until:
                process.poll(); time.sleep(.05)
        if self._owned_group_alive(process):
            raise ServiceError('owned Flash process group stop unconfirmed')
        process.wait(timeout=2)
        if self.flash_drain is not None:
            self.flash_drain.join(3)
            if self.flash_drain.is_alive():
                raise ServiceError('Flash pipe drain stop unconfirmed')
        for stream in [process.stdin, process.stdout]:
            if stream is not None:
                try: stream.close()
                except (OSError, ValueError): pass

    def flash_alive(self):
        if self.flash is None or self.flash.poll() is not None or self.engine_pid is None:
            return False
        engine = self._process(self.engine_pid)
        return bool(engine and engine['state'] != 'Z' and engine['start'] == self.engine_start
                    and engine['pgid'] == self.flash.pid and engine['sid'] == self.flash.pid
                    and engine['uids'] == {self.uid})

    def abort(self):
        with self.abort_lock:
            with self.lock:
                self.generation += 1
                self.stopping = True
                conn = self.connection
            if conn:
                if conn.sock:
                    try: conn.sock.shutdown(2)
                    except OSError: pass
                conn.close()
            # Core PID namespace only. Never ask the host to signal a Core PID.
            try:
                with self.lifecycle_lock:
                    self._namespace()
                    process = self.flash
                    if process is not None:
                        self.stop_group(process)
                        self.flash = None
                        self.engine_pid = self.engine_start = self.wrapper_start = None
            except Exception:
                # No guessed PID signals. Only the host's fixed owned Core
                # container boundary can resolve uncertain Flash ownership.
                self.client.panic()
                raise ServiceError('Flash stop unconfirmed; host emergency stop requested') from None
            # Only after Flash's process barrier can the host's physical absolute
            # GPU-idle barrier acknowledge an Ollama stop.
            self.client.stop()
            with self.lock:
                self.active_model = self.flash_base = self.idle_base = self.address = None
                self.models_verified = False
                self.stopping = False

    def close(self):
        with self.lock:
            self.closing = True
        self.abort()

    def health(self):
        try:
            resident = self.flash_alive()
        except ServiceError:
            resident = False
        health = {'flash_resident': resident, 'ollama_selected': self.active_model,
                  'provider_mode': 'host-lifecycle', 'stopping': self.stopping}
        if self.normal_catalog or not self.requires_flash:
            baseline_ready = resident if self.requires_flash else self.idle_base is not None
            ready = bool(self.address and baseline_ready and self.models_verified and not self.stopping and not self.closing)
            if ready:
                try:
                    status = self.client.status()
                    ready = (status['epoch'] == self.host_epoch and status['running'] is True
                             and not status['stopping'] and status['fault'] is None)
                except Exception:
                    ready = False
            health.update(ready=ready, flash_enabled=self.requires_flash,
                          acceptance_ollama_only=not self.normal_catalog)
        return health

    def wait_idle(self, check=lambda: None):
        until = time.monotonic() + 30
        stable = 0
        while time.monotonic() < until:
            check()
            stable = stable + 1 if self.read_resources()['vram_used'] < 3 * GIB else 0
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
        self.models_verified = False
        value = self.api('/api/tags')
        if self.normal_catalog:
            if type(value) is not dict or type(value.get('models')) is not list or len(value['models']) > 1024:
                raise ServiceError('bounded engine inventory required')
            tags = {}
            for entry in value['models']:
                if (type(entry) is not dict or type(entry.get('name')) is not str
                        or entry['name'] in tags):
                    raise ServiceError('ambiguous engine inventory')
                tags[entry['name']] = entry
            for profile in self.profiles.values():
                actual = tags.get(profile['reference'])
                if actual is None or manifest_digest(actual.get('digest')) != profile['digest']:
                    raise ServiceError('configured model manifest identity changed or absent')
        else:
            tags = {m['name'] for m in value['models']}
        required = ({name + ':latest' for name in self.names.values()} if self.profiles is None else set(self.names.values()))
        if not required <= set(tags):
            raise ServiceError('models not prepared; run --prepare-models first' if self.requires_flash
                               else 'configured models not prepared')
        self.models_verified = True

    def capture_idle_baseline(self, check):
        if self.requires_flash:
            raise ServiceError('idle baseline only applies to explicit Flash-free mode')
        # Capture only after the host stop barrier, empty engine inventory, and
        # two authenticated physical idle observations. No local /proc fallback.
        with self.lock:
            generation = self.generation
        guard = lambda: self._guard(generation, check)
        until, stable, samples = time.monotonic() + 30, 0, []
        while time.monotonic() < until:
            guard()
            sample = self.read_resources()
            empty = not self.loaded_models()
            if empty and sample['vram_used'] < 3 * GIB and sample['mem_available'] >= 8 * GIB:
                stable += 1; samples.append(sample)
            else:
                stable, samples = 0, []
            if stable >= 2:
                guard()
                with self.lock:
                    if generation != self.generation or self.stopping or self.closing:
                        raise ServiceError('physical idle baseline invalidated by provider stop')
                    self.idle_base = {**samples[-1], 'vram_used': min(s['vram_used'] for s in samples),
                                      'mem_available': max(s['mem_available'] for s in samples)}
                return
            time.sleep(.5)
        raise ServiceError('physical idle baseline unconfirmed')

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

    def loaded_models(self):
        value = self.api('/api/ps')
        if (type(value) is not dict or type(value.get('models')) is not list
                or len(value['models']) > 1024 or any(type(entry) is not dict for entry in value['models'])):
            self.models_verified = False
            raise ServiceError('valid bounded loaded-model inventory required')
        return value['models']

    def unload(self, check):
        if not self.active_model:
            return
        baseline = self.flash_base if self.requires_flash else self.idle_base
        if baseline is None:
            raise ServiceError('physical release baseline unavailable; next model not loaded')
        self.api('/api/generate', {'model': self.names[self.active_model], 'keep_alive': 0}, timeout=10)
        # api/ps alone is not a release barrier. Compare physical GPU use to
        # the configured Flash-only or empty-engine baseline twice.
        until = time.monotonic() + 30
        stable = 0
        while time.monotonic() < until:
            check()
            s = self.read_resources()
            empty = not self.loaded_models()
            released = (s['vram_used'] <= baseline['vram_used'] + 512 * 1024**2
                        and s['mem_available'] >= max(8 * GIB, baseline['mem_available'] - 2 * GIB)
                        and (self.requires_flash or s['vram_used'] < 3 * GIB))
            stable = stable + 1 if empty and released else 0
            if stable >= 2:
                self.active_model = None
                return
            time.sleep(.5)
        raise ServiceError('old model physical release unconfirmed; next model not loaded')

    def generate(self, job, check):
        if job.model not in self.models:
            raise ServiceError('unknown configured model')
        with self.lock:
            generation = self.generation
        guard = lambda: self._guard(generation, check)
        guard()
        if self.normal_catalog:
            self.start_ollama(guard)
            self.verify_models()
            guard()
        if self.requires_flash:
            self.start_flash(guard)
        if job.model == 'flash':
            self.flash.stdin.write(json.dumps({'messages': job.messages, 'max_tokens': job.max_tokens}) + '\n')
            self.flash.stdin.flush()
            return self.read_flash(job.deadline, guard)
        if not self.normal_catalog:
            self.start_ollama(guard)
        if not self.requires_flash and self.idle_base is None:
            if not self.normal_catalog:
                self.verify_models()
            self.capture_idle_baseline(guard)
        if self.active_model != job.model:
            self.unload(guard)
            guard()
            self.active_model = job.model
        key = job.model
        options = ({'num_ctx': 8192, 'num_thread': 8, 'num_batch': 256,
                    'num_gpu': 48 if key == 'coder-next' else 28, 'temperature': 0, 'seed': 42}
                   if self.profiles is None else dict(self.profiles[key]['options']))
        options['num_predict'] = job.max_tokens
        guard()  # Inventory/unload can finish after cancellation or a provider-generation change.
        result = self.api('/api/chat', {'model': self.names[key], 'messages': job.messages, 'stream': False,
                            'think': 'low' if key == 'gpt-oss-120b' else False, 'keep_alive': -1,
                            'options': options},
                          timeout=max(.1, job.deadline - time.monotonic()))
        guard()
        if result.get('done') is not True or result.get('done_reason') not in {'stop', 'length'}:
            raise ServiceError('Ollama nonterminal response')
        loaded = self.loaded_models()
        if (type(loaded) is not list or len(loaded) != 1 or type(loaded[0]) is not dict
                or self.profiles is not None and loaded[0].get('name') != self.names[key]):
            self.models_verified = False
            raise ServiceError('Ollama single-model invariant violated')
        if self.normal_catalog:
            try:
                if manifest_digest(loaded[0].get('digest')) != self.profiles[key]['digest']:
                    raise ServiceError('loaded model manifest identity mismatch')
            except ServiceError:
                self.models_verified = False
                raise
        return {'content': result['message'].get('content', ''),
                'thinking': result['message'].get('thinking', ''), 'finish_reason': result['done_reason'],
                'prompt_tokens': result.get('prompt_eval_count'), 'completion_tokens': result.get('eval_count'),
                'load_seconds': result.get('load_duration', 0) / 1e9}
