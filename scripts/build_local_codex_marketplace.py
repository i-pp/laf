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

# Modified 2026-11-23: pin reviewed standalone runtime source digests.

"""Build or verify a standalone Git marketplace tree for the local Codex adapter."""
import argparse
import ast
from hashlib import sha256
import json
from pathlib import Path
import re
import stat

from check_release_names import MAX_FILE, check_member, check_name

ROOT = Path(__file__).resolve().parents[1]
NAME = 'laf-local'
VERSION = '0.1.0'
SCHEMA = 'https://agent-plugins.org/schemas/1.0.0'
CATEGORY = 'Developer Tools'
LAUNCHER = 'mcp/codex_local.py'
MARKETPLACE = '.agents/plugins/marketplace.json'
PLUGIN_PATH = 'plugins/laf-local'
ICON = 'assets/laf-neural-fabric.png'

# Output member and reviewed source path, with identical paths unless mapped here.
COPY = (
    'LICENSE', ICON,
    'mcp/NOTICE', 'mcp/THIRD_PARTY_NOTICES.md', 'mcp/dependency-review.json',
    'mcp/licenses/MCP-SDK-LICENSE.txt', 'mcp/sdk-pin.txt', LAUNCHER, 'mcp/launch.py',
    'mcp/src/laf_mcp/__init__.py', 'mcp/src/laf_mcp/application.py',
    'mcp/src/laf_mcp/contracts.py', 'mcp/src/laf_mcp/receipts.py',
    'mcp/src/laf_mcp/router.py', 'mcp/src/laf_mcp/server.py',
)
# Pins bind the standalone export to reviewed source bytes. Review any runtime
# change before deliberately updating its pin.
RUNTIME_SHA256 = {
    'mcp/codex_local.py': 'b0b81a4acc81448c93224684b6f470d354d93156a605ff68877ab60ef00b37a6',
    'mcp/launch.py': 'ab5f05bf826e71f544f6d065f5bff12a234a89865dbd4f854907ef21fe6d16d1',
    'mcp/src/laf_mcp/__init__.py': 'c9d7c6794e0a3d470973bc88d9008ccdb1d71956ba48949dda956cd81446eec9',
    'mcp/src/laf_mcp/application.py': '19515bce7397cbf2664446ebdb9b8849feb0283f108fb18a694b25ef73f3789f',
    'mcp/src/laf_mcp/contracts.py': '13ec37eaa082a8222d7cd9f6127ed8c0ee1bd6bcc873de25ce586db3f4f97042',
    'mcp/src/laf_mcp/receipts.py': 'dfd4cad82ff8687d6c3e5d275f574c532e33f71cfb925a72ad8580cda0494103',
    'mcp/src/laf_mcp/router.py': '10d3162df972bd051d4a87a0801ac8ff578e3a3c061a73a49c064b21bbf42af6',
    'mcp/src/laf_mcp/server.py': 'f2c173542a1b7311bd5ff52cde79cf231072d5621f2dd2012415dd62f2151dec',
}
README_SOURCE = 'docs/laf-codex-local-marketplace.md'
PLUGIN_ROOT = '${PLUGIN_ROOT}'
PRIVATE_VALUE = re.compile(
    rb'\b(?:asdk_app|plugin_asdk_app|templated_apps|tunnel|connector|tunnel_client)_[a-z0-9_-]{16,}\b'
    rb'|\b(?:sk|ghp|gho|github_pat)[_-][a-z0-9_-]{16,}\b'
    rb'|-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----', re.IGNORECASE,
)
REMOTE_ADDRESS = re.compile(rb'(?:https?|wss?|ssh|tcp)://', re.IGNORECASE)
REFERENCE = re.compile(rb"(?:https?|wss?|ssh|tcp)://[^\s'\")>]+", re.IGNORECASE)
PUBLIC_REFERENCES = (
    b'https://agent-plugins.org/schemas/1.0.0/',
    b'https://developers.openai.com/plugins/build/plugins',
    b'https://github.com/i-pp/laf/blob/main/docs/',
    b'https://github.com/modelcontextprotocol/python-sdk/',
    b'https://pypi.org/project/mcp/',
    b'https://pypi.org/pypi/mcp/',
)
RUNTIME_IMPORTS = {
    'anyio', 'argparse', 'contextlib', 'fcntl', 'hashlib', 'importlib.metadata',
    'json', 'laf_mcp.contracts', 'laf_mcp.router', 'laf_mcp.server', 'logging',
    'math', 'mcp', 'mcp.server.lowlevel', 'mcp.shared.message', 'os', 'pathlib',
    're', 'socket', 'stat', 'struct', 'sys', 'threading', 'time', 'uuid',
}
SOCKET_OPERATIONS = {'AF_UNIX', 'SOCK_STREAM', 'SOL_SOCKET', 'SO_PEERCRED', 'socket'}


def check_runtime(name, data):
    if REMOTE_ADDRESS.search(data):
        raise ValueError(f'Unexpected network address in {name}')
    tree = ast.parse(data, filename=name)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = {alias.name for alias in node.names}
            if any((alias.asname is not None and alias.name == 'socket')
                   or alias.asname == 'socket' for alias in node.names):
                raise ValueError(f'Expected canonical socket import in {name}')
        elif isinstance(node, ast.ImportFrom):
            modules = {node.module} if node.level == 0 else set()
            if ((node.module or '').split('.')[0] == 'socket'
                    or any(alias.name == 'socket' or alias.asname == 'socket'
                           for alias in node.names)):
                raise ValueError(f'Expected canonical socket import in {name}')
        else:
            modules = set()
        if not modules <= RUNTIME_IMPORTS:
            raise ValueError(f'Unreviewed runtime import in {name}')
        if isinstance(node, ast.Name) and node.id == 'socket':
            access = parents.get(node)
            if (not isinstance(access, ast.Attribute) or access.value is not node
                    or not isinstance(access.ctx, ast.Load)
                    or access.attr not in SOCKET_OPERATIONS):
                raise ValueError(f'Unreviewed socket operation in {name}')
            if access.attr != 'socket':
                continue
            call = parents.get(access)
            if not isinstance(call, ast.Call) or call.func is not access:
                raise ValueError(f'Expected a direct Unix socket call in {name}')
            family = call.args[0] if call.args else None
            if not isinstance(family, ast.Attribute) or ast.unparse(family) != 'socket.AF_UNIX':
                raise ValueError(f'Expected a Unix socket in {name}')


def manifests():
    interface = {
        'displayName': 'Local AI Fabric Local', 'shortDescription': 'Local Router job tools',
        'developerName': 'I-plusplus Inc.', 'category': CATEGORY,
        'logo': f'./{ICON}', 'composerIcon': f'./{ICON}',
    }
    common = {
        'name': NAME, 'version': VERSION, 'author': {'name': interface['developerName']},
        'description': 'Five stdio tools for an owner-local LAF Router.',
    }

    def servers(prefix, cwd):
        return {'mcpServers': {NAME: {
            'type': 'stdio', 'command': 'python3', 'args': ['-I', '-B', '-S', prefix + LAUNCHER],
            'cwd': cwd,
        }}}

    return {
        MARKETPLACE: {
            'name': NAME, 'interface': {'displayName': interface['displayName']},
            'plugins': [{
                'name': NAME, 'source': {'source': 'local', 'path': f'./{PLUGIN_PATH}'},
                'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_USE'},
                'category': CATEGORY,
            }],
        },
        'plugin.json': {
            '$schema': f'{SCHEMA}/plugin.schema.json',
            **common, 'extensions': {'com.openai': {'interface': interface}},
        },
        '.codex-plugin/plugin.json': {
            **common, 'interface': interface, 'mcpServers': './.mcp.json',
        },
        'mcp.json': {
            '$schema': f'{SCHEMA}/mcp.schema.json', **servers(f'{PLUGIN_ROOT}/', PLUGIN_ROOT),
        },
        '.mcp.json': servers('./', '.'),
    }


def payload(root):
    files = {name: (json.dumps(value, indent=2) + '\n').encode()
             for name, value in manifests().items()}
    sources = {'README.md': README_SOURCE, **{name: name for name in COPY}}
    for name, source in sources.items():
        path = root / source
        if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
            raise ValueError(f'Expected a regular source file at {source}')
        with path.open('rb') as stream:
            files[name] = stream.read(MAX_FILE + 1)

    if {name for name in files if name.endswith('.py')} != RUNTIME_SHA256.keys():
        raise ValueError('Runtime source inventory differs from reviewed digest pins')

    for name, data in files.items():
        check_member(name, data)
        if PRIVATE_VALUE.search(data):
            raise ValueError(f'Unexpected private material in {name}')
        if name in RUNTIME_SHA256:
            if sha256(data).hexdigest() != RUNTIME_SHA256[name]:
                raise ValueError(f'Runtime source digest differs from reviewed pin in {name}')
            check_runtime(name, data)
        elif name != ICON and any(not url.startswith(PUBLIC_REFERENCES) for url in REFERENCE.findall(data)):
            raise ValueError(f'Unreviewed reference in {name}')
    # Codex copies the marketplace entry's source directory on install. Keep
    # that directory below the Git root so the checkout itself is never copied.
    return {name if name in (MARKETPLACE, 'README.md') else f'{PLUGIN_PATH}/{name}': data
            for name, data in files.items()}


def verify(output, files):
    if output.is_symlink() or not output.is_dir():
        raise ValueError('Expected a real marketplace directory')
    directories = {parent.as_posix() for name in files for parent in Path(name).parents} - {'.'}
    observed = set()
    for path in output.rglob('*'):
        name = path.relative_to(output).as_posix()
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode) and name in directories:
            continue
        if (name not in files or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1 or info.st_size != len(files[name])):
            raise ValueError(f'Unexpected marketplace entry at {name}')
        if path.read_bytes() != files[name]:
            raise ValueError(f'Marketplace contents differ at {name}')
        observed.add(name)
    if observed != files.keys():
        raise ValueError('Marketplace is missing allowlisted files')


def build(root, output):
    if (not output.is_absolute() or '..' in output.parts or
            any(part.is_symlink() for part in (output, *output.parents))):
        raise ValueError('Choose a new absolute output without symlinks')
    output = output.parent.resolve(strict=True) / output.name
    if output.is_relative_to(root.resolve()):
        raise ValueError('Output must be outside the source checkout')
    check_name(output.name)
    files = payload(root)
    output.mkdir(mode=0o700)
    for name, data in sorted(files.items()):
        path = output / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
    verify(output, files)
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output', type=Path, help='New marketplace directory outside the source checkout')
    mode.add_argument('--check', type=Path, help='Verify an exported tree against the source allowlist')
    args = parser.parse_args()
    try:
        if args.check is not None:
            files = payload(ROOT)
            verify(args.check, files)
            count = len(files)
        else:
            count = build(ROOT, args.output)
    except (OSError, ValueError, SyntaxError) as exc:
        parser.exit(1, f'Local marketplace rejected: {exc}\n')
    print(json.dumps({'marketplace': NAME, 'version': VERSION, 'files': count, 'transport': 'stdio'}))


if __name__ == '__main__':
    main()
