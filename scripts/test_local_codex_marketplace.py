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

"""Offline checks for the exact local Codex marketplace and launcher boundary."""
import ast
from contextlib import redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import build_local_codex_marketplace as marketplace


ROOT = Path(__file__).resolve().parents[1]


class MarketplaceTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.export = self.scratch / 'out'
        self.plugin = self.export / 'plugins/laf-local'

    def fixture_source(self):
        source = self.scratch / 'input'
        for name in (*marketplace.COPY, marketplace.README_SOURCE):
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        return source

    def test_export_has_one_stdio_plugin_and_only_allowlisted_files(self):
        marketplace.build(ROOT, self.export)
        files = {p.relative_to(self.export).as_posix() for p in self.export.rglob('*') if p.is_file()}
        plugin_files = {
            '.codex-plugin/plugin.json', '.mcp.json', 'LICENSE', 'assets/laf-neural-fabric.png',
            'mcp.json', 'plugin.json', 'mcp/NOTICE', 'mcp/THIRD_PARTY_NOTICES.md',
            'mcp/codex_local.py', 'mcp/dependency-review.json', 'mcp/launch.py',
            'mcp/licenses/MCP-SDK-LICENSE.txt', 'mcp/sdk-pin.txt',
            'mcp/src/laf_mcp/__init__.py', 'mcp/src/laf_mcp/application.py',
            'mcp/src/laf_mcp/contracts.py', 'mcp/src/laf_mcp/receipts.py',
            'mcp/src/laf_mcp/router.py', 'mcp/src/laf_mcp/server.py',
        }
        self.assertEqual(files, {'.agents/plugins/marketplace.json', 'README.md'}
                         | {f'plugins/laf-local/{name}' for name in plugin_files})
        catalog = json.loads((self.export / '.agents/plugins/marketplace.json').read_bytes())
        self.assertEqual(catalog['name'], 'laf-local')
        self.assertEqual([x['name'] for x in catalog['plugins']], ['laf-local'])
        self.assertEqual(catalog['plugins'][0]['source'], {
            'source': 'local', 'path': './plugins/laf-local',
        })

        def load(name):
            return json.loads((self.plugin / name).read_bytes())

        portable, legacy = load('plugin.json'), load('.codex-plugin/plugin.json')
        self.assertEqual(portable['name'], legacy['name'])
        self.assertEqual(portable['version'], legacy['version'])
        self.assertEqual(portable['extensions'], {'com.openai': {'interface': legacy['interface']}})
        self.assertEqual(legacy['mcpServers'], './.mcp.json')
        self.assertNotIn('apps', legacy)
        for name, cwd, launcher in (
            ('mcp.json', marketplace.PLUGIN_ROOT, marketplace.PLUGIN_ROOT + '/mcp/codex_local.py'),
            ('.mcp.json', '.', './mcp/codex_local.py'),
        ):
            self.assertEqual(load(name)['mcpServers'], {'laf-local': {
                'type': 'stdio', 'command': 'python3', 'args': ['-I', '-B', '-S', launcher],
                'cwd': cwd,
            }})
        tools_path = self.plugin / 'mcp/src/laf_mcp/server.py'
        self.assertEqual(tools_path.read_bytes(), (ROOT / 'mcp/src/laf_mcp/server.py').read_bytes())
        tool_table, = (node.value for node in ast.parse(tools_path.read_bytes()).body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'TOOLS'
                               for target in node.targets))
        self.assertEqual(set(ast.literal_eval(tool_table)), {
            'laf_router_models', 'laf_router_status', 'laf_router_submit_job',
            'laf_router_get_job', 'laf_router_cancel_job',
        })

    def test_private_source_files_do_not_enter_export(self):
        source = self.fixture_source()
        (source / '.app.json').write_text('synthetic private binding')
        (source / 'runtime').mkdir()
        (source / 'runtime' / 'remote.py').write_text('synthetic remote runtime')
        marketplace.build(source, self.export)
        marketplace.verify(self.export, marketplace.payload(ROOT))

    def test_export_check_refuses_extra_or_modified_entries(self):
        marketplace.build(ROOT, self.export)
        files = marketplace.payload(ROOT)
        unexpected = self.export / '.app.json'
        unexpected.write_text('{}')
        with self.assertRaises(ValueError):
            marketplace.verify(self.export, files)
        unexpected.unlink()
        (self.plugin / 'mcp.json').write_text('{}')
        with self.assertRaises(ValueError):
            marketplace.verify(self.export, files)
        with self.assertRaises(FileExistsError):
            marketplace.build(ROOT, self.export)
        self.assertEqual((self.plugin / 'mcp.json').read_text(), '{}')

    def test_runtime_allowlist_rejects_network_and_private_material(self):
        source = self.fixture_source()
        target = source / 'mcp/src/laf_mcp/router.py'
        real = target.read_bytes()
        for extra in (
            b'\naddress = "https://example.invalid/mcp"\n',
            b'\nimport urllib.request\n',
            b'\nsock = socket.socket()\n',
            b'\napp_id = "asdk_app_' + b'a' * 28 + b'"\n',
        ):
            with self.subTest(extra=extra):
                target.write_bytes(real + extra)
                with self.assertRaises(ValueError):
                    marketplace.build(source, self.export)
                self.assertFalse(self.export.exists())

    def test_source_symlink_cannot_supply_allowlisted_file(self):
        source = self.fixture_source()
        target = source / marketplace.LAUNCHER
        target.unlink()
        target.symlink_to(ROOT / marketplace.LAUNCHER)
        with self.assertRaises(ValueError):
            marketplace.build(source, self.export)
        self.assertFalse(self.export.exists())

    def test_unreviewed_service_reference_in_documentation_is_rejected(self):
        source = self.fixture_source()
        with (source / marketplace.README_SOURCE).open('a') as readme:
            readme.write('\nUnexpected address https://example.invalid/mcp\n')
        with self.assertRaisesRegex(ValueError, 'Unreviewed reference'):
            marketplace.build(source, self.export)
        self.assertFalse(self.export.exists())


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scratch = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        export = scratch / 'out'
        marketplace.build(ROOT, export)
        cls.plugin = export / 'plugins/laf-local'
        spec = importlib.util.spec_from_file_location('codex_local_launcher', cls.plugin / marketplace.LAUNCHER)
        cls.launcher = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', sys.path.copy()):
            spec.loader.exec_module(cls.launcher)

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='laf-local-')))
        self.config = self.home / self.launcher.CONFIG
        self.config.parent.mkdir(parents=True, mode=0o700)
        self.router_dir = self.home / 'router'
        self.router_dir.mkdir(mode=0o700)
        self.socket_path = self.router_dir / 'service.sock'
        self.settings = {
            'sdk_python': str(Path(sys.executable)),
            'router_socket': str(self.socket_path),
            'state_dir': str(self.home / 'receipts'),
            'models': ['test-model', 'another-model'],
        }
        self.legacy_environment = {
            'LOCAL_INFERENCE_SOCKET': str(self.home / 'any-other.sock'),
            'LAF_MCP_STATE_DIR': str(self.home / 'other-receipts'),
            'LAF_MCP_MODELS': 'different-model',
        }

    def write_config(self, settings=None):
        self.config.write_text(json.dumps(self.settings if settings is None else settings))
        self.config.chmod(0o600)

    def assert_stops_without_connection_or_exec(self, diagnostic):
        with (patch.object(Path, 'home', return_value=self.home),
              patch.dict(os.environ, self.legacy_environment),
              patch('socket.socket') as make_socket, patch('socket.getaddrinfo') as resolve,
              patch('os.execve') as execute, redirect_stderr(io.StringIO()) as stderr):
            self.assertEqual(self.launcher.main(), 2)
        self.assertIn(diagnostic, stderr.getvalue())
        make_socket.assert_not_called()
        resolve.assert_not_called()
        execute.assert_not_called()
        self.assertFalse((self.home / 'receipts').exists())

    def test_missing_config_ignores_inherited_integrated_settings(self):
        self.assert_stops_without_connection_or_exec('codex-local.json')

    def test_configuration_rejects_remote_or_ambiguous_inputs(self):
        for changes in (
            {'router_socket': 'https://example.invalid/mcp'},
            {'router_socket': str(self.router_dir) + '/../router/service.sock'},
            {'url': 'https://example.invalid/mcp'},
            {'models': ['valid-model', 'valid-model']},
            {'models': [None]},
        ):
            with self.subTest(changes=changes):
                self.write_config(self.settings | changes)
                self.assert_stops_without_connection_or_exec('codex-local.json')
        self.config.chmod(0o644)
        self.assert_stops_without_connection_or_exec('codex-local.json')
        self.config.rename(self.config.with_name('real.json'))
        self.config.symlink_to('real.json')
        self.assert_stops_without_connection_or_exec('codex-local.json')

    def test_missing_socket_refuses_before_opening_any_connection(self):
        self.write_config()
        self.assert_stops_without_connection_or_exec('private Router socket is unavailable')

    def test_stale_socket_attempts_only_the_selected_unix_socket(self):
        self.write_config()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(self.socket_path))
            self.socket_path.chmod(0o600)
        with (patch.object(Path, 'home', return_value=self.home),
              patch.dict(os.environ, self.legacy_environment),
              patch('socket.socket', wraps=socket.socket) as make_socket,
              patch('socket.getaddrinfo') as resolve, patch('os.execve') as execute,
              redirect_stderr(io.StringIO()) as stderr):
            self.assertEqual(self.launcher.main(), 2)
        self.assertIn('private Router socket is unavailable', stderr.getvalue())
        make_socket.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
        resolve.assert_not_called()
        execute.assert_not_called()

    def test_private_unix_peer_leads_only_to_packaged_adapter_with_fixed_args(self):
        self.write_config()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(self.socket_path))
            self.socket_path.chmod(0o600)
            listener.listen(1)
            listener.settimeout(2)
            with (patch.object(Path, 'home', return_value=self.home),
                  patch.dict(os.environ, self.legacy_environment),
                  patch('socket.socket', wraps=socket.socket) as make_socket,
                  patch('socket.getaddrinfo') as resolve,
                  patch('os.execve', side_effect=OSError) as execute,
                  redirect_stderr(io.StringIO()) as stderr):
                self.assertEqual(self.launcher.main(), 2)
            request, _address = listener.accept()
            with request:
                request.settimeout(2)
                self.assertEqual(request.recv(1), b'')
        make_socket.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
        resolve.assert_not_called()
        self.assertIn('could not start', stderr.getvalue())
        sdk_python = self.settings['sdk_python']
        execute.assert_called_once_with(sdk_python, [
            sdk_python, '-I', '-B', str(self.plugin / 'mcp/launch.py'), '--socket',
            str(self.socket_path), '--state-dir', self.settings['state_dir'],
            '--allow-model', 'test-model', '--allow-model', 'another-model',
        ], {})

    def test_exported_stdio_command_emits_only_stderr_for_missing_requirements(self):
        command = json.loads((self.plugin / '.mcp.json').read_bytes())['mcpServers']['laf-local']
        for config_exists, text in (
            (False, b'codex-local.json'), (True, b'private Router socket is unavailable'),
        ):
            with self.subTest(config_exists=config_exists):
                if config_exists:
                    self.write_config()
                result = subprocess.run(
                    [sys.executable, *command['args']], cwd=self.plugin,
                    env={'HOME': str(self.home), **self.legacy_environment},
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertIn(text, result.stderr)
