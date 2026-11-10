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

# Added 2026-11-10: isolated offline client-install acceptance.

"""Offline packaging gate; never install into the executor's host environment."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LICENSE_FILES = ('LICENSE', 'LICENSES/LicenseRef-KOL-v1.txt', 'NOTICE',
                 'LICENSING.md', 'THIRD_PARTY_NOTICES.md', 'CHANGES.md',
                 'inference/laf_npu_service/DISTRIBUTION.md')


class InstalledClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='laf-client-install-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        source = cls.root / 'source'
        source.mkdir()
        for name in ('pyproject.toml', *LICENSE_FILES):
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, source / name)
        # Copy only first-party Python inputs, never caches or external assets.
        for name in ('local_platform', 'laf_service'):
            target = source / 'inference' / name
            target.mkdir(parents=True)
            for module in (ROOT / 'inference' / name).glob('*.py'):
                shutil.copy2(module, target / module.name)
        cls.env = {'PATH': os.defpath, 'HOME': str(cls.root), 'LANG': 'C.UTF-8',
                   'PYTHONDONTWRITEBYTECODE': '1', 'PIP_CONFIG_FILE': os.devnull}
        wheels = cls.root / 'wheels'
        cls.checked([sys.executable, '-m', 'pip', '--isolated', 'wheel',
                     '--no-index', '--no-deps', '--no-build-isolation',
                     '--no-cache-dir', '--wheel-dir', str(wheels), str(source)])
        candidates = list(wheels.glob('*.whl'))
        if len(candidates) != 1:
            raise AssertionError('expected exactly one client wheel')
        cls.wheel = candidates[0]
        cls.venv = cls.root / 'venv'
        venv.EnvBuilder(with_pip=True).create(cls.venv)
        cls.python = str(cls.venv / 'bin' / 'python')
        cls.laf = str(cls.venv / 'bin' / 'laf')
        cls.checked([cls.python, '-m', 'pip', '--isolated', 'install',
                     '--no-index', '--no-deps', str(cls.wheel)])
        # All acceptance runs happen after the build copy is unavailable.
        shutil.rmtree(source)
        cls.outside = cls.root / 'outside'
        cls.outside.mkdir()

    @classmethod
    def checked(cls, command, *, input=None, expected=0, env=None):
        result = subprocess.run(command, input=input, text=True,
                                cwd=getattr(cls, 'outside', cls.root),
                                env=cls.env if env is None else env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=120)
        if result.returncode != expected:
            raise AssertionError('command failed: ' + repr(command) + '\n' +
                                 result.stdout + result.stderr)
        return result

    def run_laf(self, *args, input=None, expected=0, env=None):
        return self.checked([self.laf, *args], input=input, expected=expected, env=env)

    def test_help_outside_checkout(self):
        result = self.run_laf('--help')
        self.assertIn('chat', result.stdout)
        self.assertIn('--socket', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_bare_command_opens_and_exits_without_router(self):
        result = self.run_laf(input='/help\n/quit\n')
        self.assertIn('You>', result.stdout)
        self.assertIn('/find GLOB', result.stdout)
        self.assertNotIn('Traceback', result.stderr)

    def test_environment_and_explicit_socket_do_not_require_router_on_exit(self):
        env = dict(self.env, LOCAL_INFERENCE_SOCKET='/missing/env/service.sock')
        for args in ((), ('--socket', '/missing/explicit/service.sock')):
            with self.subTest(args=args):
                result = self.run_laf(*args, input='/quit\n', env=env)
                self.assertIn('You>', result.stdout)

    def test_explicit_chat_and_subcommand_help(self):
        for command in ('chat', 'status', 'models', 'capabilities', 'classify',
                        'repair', 'get', 'cancel', 'ask', 'model'):
            with self.subTest(command=command):
                self.run_laf(command, '--help')
        self.run_laf('chat', '--model', 'flash', input='/quit\n')

    def test_invalid_command_stays_an_error(self):
        result = self.run_laf('not-a-command', expected=2)
        self.assertIn('invalid choice', result.stderr)
        self.assertNotIn('You>', result.stdout)

    def test_catalog_remains_three_unprovisioned_profiles(self):
        result = self.run_laf('model', 'catalog')
        catalog = json.loads(result.stdout)['catalog']
        self.assertEqual({entry['id'] for entry in catalog},
                         {'flash', 'coder-next', 'gpt-oss-120b'})
        self.assertTrue(all(entry['user_setup_required'] for entry in catalog))
        self.assertTrue(all(entry['digest'] is None for entry in catalog))
        self.assertFalse((self.root / '.local').exists())

    def test_missing_socket_fails_closed(self):
        result = self.run_laf('--socket', str(self.root / 'absent.sock'),
                              'status', expected=1)
        report = json.loads(result.stderr)
        self.assertEqual(report['status'], 'error_or_uncertain')
        self.assertFalse(report['automatic_retry'])
        self.assertFalse((self.root / 'absent.sock').exists())

    def test_installed_filename_search_does_not_need_a_router(self):
        workspace = self.root / 'workspace'
        workspace.mkdir(mode=0o700)
        (workspace / 'example.txt').write_text('not read by filename search')
        (workspace / '.hidden.txt').write_text('hidden')
        result = self.run_laf('chat', '--workspace', str(workspace),
                              input='/find *.txt\n/quit\n')
        self.assertIn('Files [local]>', result.stdout)
        self.assertIn('example.txt', result.stdout)
        self.assertNotIn('.hidden.txt', result.stdout)
        self.assertNotIn('not read by filename search', result.stdout)

    def test_installed_repair_runner_for_already_passing_pure_function(self):
        source = self.root / 'pure-function.py'
        source.write_text('def add(a, b):\n    return a + b\n')
        cases = self.root / 'pure-cases.json'
        cases.write_text(json.dumps([{'function': 'add', 'args': [2, 3], 'expected': 5}]))
        before = source.read_bytes()
        result = self.run_laf('repair', '--source', str(source), '--cases', str(cases),
                              '--workdir', str(self.root / 'repair-output'))
        report = json.loads(result.stdout)
        self.assertTrue(report['tests_passed'])
        self.assertEqual(report['attempts'], [])
        self.assertEqual(report['before']['report']['passed'], 1)
        self.assertEqual(source.read_bytes(), before)

    def test_installed_ask_validation_and_missing_socket_fail_closed(self):
        result = self.run_laf('--socket', str(self.root / 'absent.sock'), 'ask',
                              '--model', 'flash', '--prompt', 'Hello', expected=1)
        report = json.loads(result.stderr)
        self.assertEqual(report['status'], 'error_or_uncertain')
        self.assertFalse(report['automatic_retry'])
        self.assertIsNone(report['job_id'])
        self.assertFalse((self.root / 'absent.sock').exists())

    def test_installed_imports_and_entrypoint_have_no_checkout_fallback(self):
        code = (
            'import importlib.metadata as m,json,pathlib; '
            'from inference.local_platform import cli,repair; '
            'from inference.laf_service import core,npu_bridge; '
            'd=m.distribution("local-ai-fabric-client"); '
            'print(json.dumps({"cli":cli.__file__,"core":core.__file__, '
            '"runner":pathlib.Path(repair.__file__).with_name("test_runner.py").is_file(), '
            '"dependencies":d.requires or [],"entrypoints":'
            '[(e.name,e.value) for e in d.entry_points]}))'
        )
        report = json.loads(self.checked([self.python, '-I', '-c', code]).stdout)
        for name in ('cli', 'core'):
            self.assertTrue(Path(report[name]).is_relative_to(self.venv))
        self.assertTrue(report['runner'])
        self.assertEqual(report['dependencies'], [])
        self.assertIn(['laf', 'inference.local_platform.cli:main'], report['entrypoints'])

    def test_wheel_retains_license_and_only_python_support_modules(self):
        with zipfile.ZipFile(self.wheel) as archive:
            names = archive.namelist()
            for name in LICENSE_FILES:
                matching = [p for p in names if p.endswith('.dist-info/licenses/' + name)]
                self.assertEqual(len(matching), 1, name)
                self.assertEqual(archive.read(matching[0]), (ROOT / name).read_bytes())
            for name in names:
                self.assertTrue('.dist-info/' in name or
                                (name.startswith(('inference/local_platform/',
                                                  'inference/laf_service/')) and
                                 name.endswith('.py')), name)
            metadata = archive.read(next(n for n in names if n.endswith('/METADATA'))).decode()
            self.assertIn('License-Expression: LicenseRef-KOL-v1', metadata)
            self.assertIn('Requires-Python: >=3.10', metadata)
            self.assertNotIn('Requires-Dist:', metadata)

    def test_shell_local_path_invocation(self):
        env = dict(self.env, PATH=str(self.venv / 'bin') + os.pathsep + os.defpath)
        result = self.checked(['laf'], input='/quit\n', env=env)
        self.assertIn('You>', result.stdout)


if __name__ == '__main__':
    unittest.main()
