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

"""Launch the standalone MCP adapter using one explicit owner-local configuration."""
import os
from pathlib import Path
import stat
import sys

MCP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_ROOT / 'src'))

from laf_mcp.contracts import strict_json, valid_model
from laf_mcp.router import Router, RouterError


CONFIG = '.config/laf/codex-local.json'
MAX_CONFIG_BYTES = 4096


def absolute_path(value):
    return (isinstance(value, str) and value.startswith('/') and '\0' not in value
            and all(part not in ('', '.', '..') for part in value.split('/')[1:]))


def read_config(path):
    if not path.is_absolute() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('The configuration path must be absolute and free of symlinks')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > MAX_CONFIG_BYTES):
            raise ValueError('Expected a bounded owner-only configuration file')
        config = strict_json(stream.read(MAX_CONFIG_BYTES + 1), max_bytes=MAX_CONFIG_BYTES)
    paths = ('sdk_python', 'router_socket', 'state_dir')
    if type(config) is not dict or set(config) != {*paths, 'models'}:
        raise ValueError('Unexpected configuration fields')
    if not all(absolute_path(config[key]) for key in paths):
        raise ValueError('Expected absolute local paths')
    models = config['models']
    if (type(models) is not list or not 1 <= len(models) <= 9
            or not all(valid_model(model) for model in models)
            or len(set(models)) != len(models)):
        raise ValueError('Expected an explicit allowlist of one to nine model IDs')
    return config


def main():
    try:
        config = read_config(Path.home() / CONFIG)
    except (OSError, ValueError, RuntimeError):
        sys.stderr.write('laf-local: configure the owner-only ~/.config/laf/codex-local.json file\n')
        return 2

    try:
        # Authenticate the one explicit local Unix endpoint without sending HTTP.
        Router(config['router_socket']).validate_endpoint()
    except RouterError:
        sys.stderr.write('laf-local: the configured private Router socket is unavailable\n')
        return 2

    python = config['sdk_python']
    argv = [
        python, '-I', '-B', str(MCP_ROOT / 'launch.py'),
        '--socket', config['router_socket'], '--state-dir', config['state_dir'],
    ]
    for model in config['models']:
        argv.extend(('--allow-model', model))
    try:
        # Use the selected installed interpreter. The adapter needs no inherited
        # credentials, proxy, plugin configuration, or endpoint environment.
        os.execve(python, argv, {})
    except OSError:
        sys.stderr.write('laf-local: could not start the configured SDK interpreter\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
