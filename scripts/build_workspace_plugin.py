#!/usr/bin/env python3
# KISS Open License Version 1.0 (KOL-v1)
#
# SPDX-FileCopyrightText: 2026 I-plusplus Inc.
# SPDX-License-Identifier: LicenseRef-KOL-v1
#
# The complete license terms and attribution requirements are in LICENSE.

"""Build the private LAF workspace plugin from an operator-supplied App binding."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import zipfile

from check_release_names import MAX_FILE, check_member, check_name

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.0.1'
APP_PATH = './.app.json'
APP_ID = re.compile(r'(?:asdk_app_|connector_|templated_apps_)[A-Za-z0-9][A-Za-z0-9_-]{0,127}')
PORTABLE = 'plugin.json'
COMPAT = '.codex-plugin/plugin.json'
PACKAGED = (
    'LICENSE', 'LICENSING.md', 'NOTICE', 'SOURCE_ORIGIN.md',
    'assets/laf-neural-fabric.png',
    'skills/laf-model-setup/SKILL.md', 'skills/laf-router/SKILL.md',
)


def private_path(path, root):
    if (not path.is_absolute() or '..' in path.parts
            or any(parent.is_symlink() for parent in (path, *path.parents))):
        raise ValueError('a private absolute path without symlinks is required')
    parent = path.parent.resolve(strict=True)
    info = parent.stat()
    if (parent.is_relative_to(root) or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('use an owner-only directory outside the source tree')
    return parent / path.name


def unique_keys(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError('duplicate manifest or binding key')
    return value


def app_mapping(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > 4096):
            raise ValueError('use a private regular App binding file')
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError('App binding exceeds size limit')
    mapping = json.loads(raw, object_pairs_hook=unique_keys)
    apps = mapping.get('apps') if type(mapping) is dict and set(mapping) == {'apps'} else None
    app = apps.get('laf') if type(apps) is dict and set(apps) == {'laf'} else None
    if (type(app) is not dict or set(app) != {'id', 'required'} or app['required'] is not True
            or not isinstance(app['id'], str) or APP_ID.fullmatch(app['id']) is None):
        raise ValueError('exactly one required LAF App binding is needed')
    return mapping


def source_member(root, name):
    path = root / name
    if any(item.is_symlink() for item in (path, *path.parents)) or not path.is_file():
        raise ValueError('workspace plugin source is incomplete')
    with path.open('rb') as stream:
        data = stream.read(MAX_FILE + 1)
    check_member(name, data)
    return data


def build(root, binding, output):
    root = root.resolve(strict=True)
    binding, output = (private_path(path, root) for path in (binding, output))
    check_name(output.name)
    if output.suffix != '.zip' or output.exists():
        raise ValueError('choose a new ZIP output')

    mapping = app_mapping(binding)
    manifests = [json.loads(source_member(root, name), object_pairs_hook=unique_keys)
                 for name in (PORTABLE, COMPAT)]
    portable, compat = manifests
    extension = portable['extensions']['com.openai']
    if (portable['name'] != 'laf' or any(portable[key] != compat[key] for key in ('name', 'version', 'author'))
            or extension['interface'] != compat['interface']
            or extension['onboardingSkill'] != './skills/laf-model-setup/SKILL.md'
            or compat['onboardingSkill'] != extension['onboardingSkill']):
        raise ValueError('LAF source manifests disagree')
    description = 'Workspace plugin for a Local AI Fabric Router and daemon over a private Tunnel.'
    for manifest in manifests:
        manifest.update(version=VERSION, description=description)
    extension['apps'] = APP_PATH
    compat['apps'] = APP_PATH
    del compat['mcpServers']

    payload = {name: source_member(root, name) for name in PACKAGED}
    for name, item in ((PORTABLE, portable), (COMPAT, compat), ('.app.json', mapping)):
        raw = (json.dumps(item, indent=2) + '\n').encode()
        check_member(name, raw)
        payload[name] = raw
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream, zipfile.ZipFile(stream, 'w') as archive:
        for name, raw in sorted(payload.items()):
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(entry, raw, compress_type=zipfile.ZIP_DEFLATED)
    return {'version': VERSION, 'files': len(payload), 'app_references': 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-binding', type=Path, required=True, help='Private JSON file with the verified App reference')
    parser.add_argument('--output', type=Path, required=True, help='New ZIP path in a private directory')
    args = parser.parse_args()
    try:
        print(json.dumps(build(ROOT, args.app_binding, args.output)))
    except (OSError, ValueError, TypeError, KeyError):
        parser.exit(1, 'Workspace plugin build refused. Check the source, private paths and one App binding.\n')


if __name__ == '__main__':
    main()
