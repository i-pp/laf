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

"""LAF source naming and breaking-migration regressions; no real providers."""
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LEGACY_BRAND = 'aceis'
HISTORY_MARKER = b'\n## LAF identifier unification (2026-11-10)\n'
HISTORY_SHA256 = 'a8f872ffc8b83e3fc17fc997135adc549a8ce724165fbfb1e9bcf5a0953d67a0'
NOTICE_SHA256 = '424870b9b1518dee73f42afd450dd711a3766b508e73a8da64dbdb28d267a464'


def source_paths(root):
    """Visit source paths without entering Git metadata or Python bytecode caches."""
    for directory, children, files in os.walk(root):
        children[:] = [name for name in children if name not in {'.git', '__pycache__'}]
        for name in [*children, *files]:
            path = Path(directory) / name
            if path.name != '.git' and path.suffix != '.pyc':
                yield path


class NamingTests(unittest.TestCase):
    def test_no_previous_names_outside_exact_historical_exceptions(self):
        for path in source_paths(ROOT):
            relative = str(path.relative_to(ROOT))
            with self.subTest(path=relative):
                self.assertNotIn(LEGACY_BRAND, relative.lower())
                if not path.is_file():
                    continue
                raw = path.read_bytes()
                if relative == 'NOTICE':
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), NOTICE_SHA256)
                    continue
                if relative == 'CHANGES.md':
                    history, current = raw.split(HISTORY_MARKER, 1)
                    self.assertEqual(hashlib.sha256(history).hexdigest(), HISTORY_SHA256)
                    raw = current
                if relative == 'scripts/test_laf_naming.py':
                    line = ("LEGACY_BRAND = " + repr(LEGACY_BRAND)).encode()
                    self.assertEqual(raw.count(line), 1)
                    raw = raw.replace(line, b'')
                self.assertNotIn(LEGACY_BRAND.encode(), raw.lower())

    def test_scan_prunes_regular_clone_metadata_and_worktree_control_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git/logs').mkdir(parents=True)
            (root / '.git/logs/HEAD').write_text('commit: ' + LEGACY_BRAND + ' rename')
            (root / 'source.py').write_text('')
            self.assertEqual(list(source_paths(root)), [root / 'source.py'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').write_text('gitdir: metadata')
            self.assertEqual(list(source_paths(root)), [])

    def test_canonical_module_paths_load_without_alias_packages(self):
        for name in ('inference.laf_service.server', 'inference.laf_service.core_server',
                     'inference.laf_service.host_supervisor',
                     'inference.laf_npu_service.service', 'inference.laf_npu_service.probe_hardware'):
            with self.subTest(name=name):
                self.assertIsNotNone(importlib.import_module(name))
        for suffix in ('service', 'npu_service'):
            name = 'inference.' + LEGACY_BRAND + '_' + suffix
            with self.subTest(name=name), self.assertRaises(ModuleNotFoundError):
                importlib.import_module(name)

    def test_cli_default_and_explicit_socket_overrides(self):
        from inference.local_platform import cli
        for argv, env, expected in (
            (['models'], {}, '/run/user/' + str(os.getuid()) + '/laf-inference/service.sock'),
            (['models'], {'LOCAL_INFERENCE_SOCKET': '/tmp/operator/service.sock'}, '/tmp/operator/service.sock'),
            (['--socket', '/tmp/explicit/service.sock', 'models'],
             {'LOCAL_INFERENCE_SOCKET': '/tmp/operator/service.sock'}, '/tmp/explicit/service.sock'),
        ):
            with self.subTest(argv=argv, env=env), patch.dict(os.environ, env, clear=True), \
                 patch.object(cli, 'request', return_value={'models': []}) as request, \
                 patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(cli.main(argv), 0)
                request.assert_called_once_with(expected, 'GET', '/v1/models')

    def test_backend_model_aliases_match_default_catalog(self):
        from inference.laf_service import backends, core_backends
        from inference.local_platform.model_management import read_catalog
        expected = {'coder-next': 'laf-coder-next', 'gpt-oss-120b': 'laf-gpt-oss-120b'}
        self.assertEqual(backends.NAMES, expected)
        self.assertEqual(core_backends.NAMES, expected)
        catalog = {row['id']: row for row in read_catalog()}
        for model, name in expected.items():
            self.assertEqual(catalog[model]['reference'], name + ':latest')

    def test_previous_gpu_ownership_label_is_refused_before_mutation(self):
        from inference.laf_service.backends import Backends, IMAGE
        from inference.laf_service.core import ServiceError
        backend = Backends({'state_dir': '/tmp/laf-test'}, '/tmp/config')
        backend.cid = 'fixed-container'
        info = {'Config': {'Labels': {LEGACY_BRAND + '.inference.owner': backend.owner}}, 'Image': IMAGE}
        with patch.object(backend, 'docker', return_value=json.dumps([info])) as docker:
            with self.assertRaises(ServiceError):
                backend.inspect_owned()
            docker.assert_called_once_with('inspect', backend.cid)

    def test_previous_core_ownership_label_is_refused_before_stop(self):
        from inference.laf_service.test_host_supervisor import CoreStopTest
        from inference.laf_service.core import ServiceError
        fixture = CoreStopTest('test_stop_only_exact_container')
        fixture.setUp()
        fixture.info['Config']['Labels'] = {LEGACY_BRAND + '.core.owner': 'test-owner'}
        with self.assertRaises(ServiceError):
            fixture.adapter.stop()
        self.assertFalse(any(call[0] == 'stop' for call in fixture.calls))

    def test_npu_reader_uses_current_prefix_length_and_does_not_accept_previous_prefix(self):
        from inference.laf_npu_service.service import Worker
        worker = Worker.__new__(Worker)
        worker.responses = queue.Queue()
        old_line = LEGACY_BRAND.upper() + '_JSON {"ready":false}\n'
        worker.process = SimpleNamespace(stdout=io.StringIO(old_line + 'LAF_JSON {"ready":true}\n'))
        worker.log = io.StringIO()
        worker._read()
        self.assertEqual(worker.responses.get_nowait(), {'ready': True})
        self.assertIsNone(worker.responses.get_nowait())
        self.assertEqual(worker.log.getvalue(), old_line)

    def test_npu_probe_reads_current_prefix_without_hardware(self):
        from inference.laf_npu_service import probe_hardware
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'probe'
            def fake_run(*args, **kwargs):
                profile = target / 'profile.json'
                profile.write_text(json.dumps([{'cat': 'Node', 'args': {'provider': 'CPUExecutionProvider'}}]))
                for record in ({'profile': str(profile)}, {'logits': [0] * 10, 'class_index': 0}):
                    kwargs['stdout'].write('LAF_JSON ' + json.dumps(record) + '\n')
                return SimpleNamespace(returncode=0)
            with patch.object(probe_hardware.subprocess, 'run', side_effect=fake_run):
                result = probe_hardware.run(Path('/fake/worker'), Path('/fake/sdk'), target, 'CPU')
            self.assertEqual(result['logits'], [0] * 10)
            self.assertTrue(result['execution_verified'])


if __name__ == '__main__':
    unittest.main()
