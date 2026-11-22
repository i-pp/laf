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

"""Build or verify a standalone Git marketplace tree for the local Codex adapter."""
import argparse
import ast
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
    for node in ast.walk(ast.parse(data, filename=name)):
        if isinstance(node, ast.Import):
            modules = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = {node.module}
        else:
            modules = set()
        if not modules <= RUNTIME_IMPORTS:
            raise ValueError(f'Unreviewed runtime import in {name}')
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == 'socket' and node.attr not in SOCKET_OPERATIONS):
            raise ValueError(f'Unreviewed socket operation in {name}')
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'socket'
                and node.func.attr == 'socket'):
            family = node.args[0] if node.args else None
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

    for name, data in files.items():
        check_member(name, data)
        if PRIVATE_VALUE.search(data):
            raise ValueError(f'Unexpected private material in {name}')
        if name.endswith('.py'):
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
