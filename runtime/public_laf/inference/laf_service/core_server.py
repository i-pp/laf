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

"""Explicit Core-only entrypoint. The legacy host entrypoint remains unchanged."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import threading
from .core import Controller, ServiceError
from .core_backends import CoreBackends, acceptance_profiles, configured_selection
from .lifecycle_client import LifecycleClient
from .lifecycle_uds import FramedExchange, UnixConnector, decode
from .server import Handler, Server, private_dir

REQUIRED = {'state_dir', 'runtime_dir', 'lifecycle_runtime_dir', 'strata_source',
            'tokenizer_python', 'strata_command'}


def load_config(path):
    path = Path(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 32768):
        raise ServiceError('owned mode-0600 Core configuration required')
    config = decode(path.read_bytes())
    # Changed 2026-11-11: normal owner-selected catalogs explicitly include/exclude Flash.
    candidate = type(config) is dict and 'acceptance_ollama_profiles' in config
    normal = type(config) is dict and any(key in config for key in ('ollama_profiles', 'flash_enabled', 'default_model'))
    if normal:
        _, flash, _ = configured_selection(config)
        required = {'state_dir', 'runtime_dir', 'lifecycle_runtime_dir', 'ollama_profiles', 'flash_enabled', 'default_model'}
        if flash:
            required |= {'strata_source', 'tokenizer_python', 'strata_command'}
    else:
        required = {'state_dir', 'runtime_dir', 'lifecycle_runtime_dir', 'acceptance_ollama_profiles'} if candidate else REQUIRED
    if type(config) is not dict or not required <= set(config) or set(config) - required - {'npu_socket', 'swap_limit_mib', 'rolling_swap_policy'}:
        raise ServiceError('explicit Core configuration fields required')
    if 'rolling_swap_policy' in config:
        from .swap_window import WindowPolicy
        if type(config['rolling_swap_policy']) is not dict:
            raise ServiceError('explicit rolling policy required')
        WindowPolicy(**config['rolling_swap_policy'])
    if 'swap_limit_mib' in config and (type(config['swap_limit_mib']) is not int or not 512 <= config['swap_limit_mib'] <= 2048):
        raise ServiceError('swap_limit_mib must be an integer from 512 through 2048')
    if candidate:
        acceptance_profiles(config['acceptance_ollama_profiles'])
    for name in required - {'strata_command', 'acceptance_ollama_profiles', 'ollama_profiles', 'flash_enabled', 'default_model'}:
        if type(config[name]) is not str or not Path(config[name]).is_absolute():
            raise ServiceError('absolute container-native Core paths required')
    if 'strata_command' in required:
        command = config['strata_command']
        if (type(command) is not list or not command or len(command) > 128
                or any(type(x) is not str or not x or len(x) > 4096 for x in command)
                or not Path(command[0]).is_absolute()):
            raise ServiceError('fixed local Strata command required')
    if config.get('npu_socket') is not None and (type(config['npu_socket']) is not str or not Path(config['npu_socket']).is_absolute()):
        raise ServiceError('absolute private NPU socket required')
    return config


def lifecycle_client(config):
    path = config['lifecycle_runtime_dir']
    return LifecycleClient(FramedExchange(UnixConnector(path)),
                           FramedExchange(UnixConnector(path, control=True)))


def run_core(path, *, backend_factory=CoreBackends, client_factory=lifecycle_client,
             server_factory=Server, install_signals=True):
    path = Path(path)
    config = load_config(path)
    client = client_factory(config)
    policy = client.resource_policy()
    if policy['swap_limit_mib'] != config.get('swap_limit_mib', 512):
        raise ServiceError('Core and host swap policy mismatch')
    if policy.get('rolling_swap_policy') != config.get('rolling_swap_policy'):
        raise ServiceError('Core and host rolling policy mismatch')
    if 'rolling_swap_policy' in config and policy.get('policy_version') != 2:
        raise ServiceError('rolling host policy version mismatch')
    # Namespace/UID refusal precedes any Core runtime-directory creation.
    backend = backend_factory(config, path, client)
    state, runtime = private_dir(config['state_dir']), private_dir(config['runtime_dir'])
    lock_fd = os.open(state / 'core.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    controller, server, socket_identity = None, None, None
    address = runtime / 'service.sock'
    try:
        lock_info = os.fstat(lock_fd)
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid()
                or lock_info.st_nlink != 1 or stat.S_IMODE(lock_info.st_mode) != 0o600):
            raise ServiceError('Core singleton lock identity invalid')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        controller = Controller(backend, client.read_resources, start=False, swap_limit_mib=config.get('swap_limit_mib', 512),
                                host_admission='rolling_swap_policy' in config)
        # Refresh the host lease and enforce host resource limits during startup.
        controller.monitor.start()
        def shutdown(*_):
            controller.stop.set()
            if server:
                threading.Thread(target=server.shutdown, daemon=True).start()
        if install_signals:
            signal.signal(signal.SIGTERM, shutdown)
            signal.signal(signal.SIGINT, shutdown)
        backend.abort()  # Explicit new-generation physical barrier before startup.
        backend.start_ollama(controller.cancelled_idle)
        backend.verify_models()
        if getattr(backend, 'requires_flash', True):
            backend.start_flash(controller.cancelled_idle)
        else:
            backend.capture_idle_baseline(controller.cancelled_idle)
        if os.path.lexists(address):
            raise ServiceError('Core public socket already exists; not replaced')
        old_umask = os.umask(0o177)
        try:
            server = server_factory(str(address), Handler)
        finally:
            os.umask(old_umask)
        info = address.lstat(); socket_identity = info.st_dev, info.st_ino
        server.controller, server.config = controller, config
        controller.thread.start()
        def stop_server():
            controller.stop.wait(); server.shutdown()
        threading.Thread(target=stop_server, daemon=True).start()
        server.serve_forever(poll_interval=.2)
    finally:
        if server:
            server.server_close()
        try:
            if controller:
                controller.close()
        finally:
            if socket_identity is not None:
                try:
                    info = address.lstat()
                    if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == socket_identity:
                        address.unlink()
                except FileNotFoundError:
                    pass
            os.close(lock_fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    run_core(args.config)


if __name__ == '__main__': main()
