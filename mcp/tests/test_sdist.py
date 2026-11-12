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

"""Offline sdist completeness, not standalone monorepo test acceptance."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SourceDistributionTests(unittest.TestCase):
    def test_offline_sdist_preserves_modules_audit_metadata_and_test_helpers(self):
        with tempfile.TemporaryDirectory(prefix='laf sdist 雪 ') as tmp:
            tmp = Path(tmp)
            source = tmp / 'source'
            shutil.copytree(ROOT / 'mcp', source, ignore=shutil.ignore_patterns(
                'build', '*.egg-info', '__pycache__', '.venv', '.git'))
            dist = tmp / 'dist'
            dist.mkdir()
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            env.pop('PYTHONPATH', None)
            # Existing local backend only: no network, dependency resolution,
            # pip installation or host-environment modification.
            subprocess.run([sys.executable, '-B', '-c',
                'import setuptools.build_meta as b, sys; b.build_sdist(sys.argv[1])',
                str(dist)], cwd=source, env=env, check=True, capture_output=True)
            archives = list(dist.glob('*.tar.gz'))
            self.assertEqual(len(archives), 1)
            with tarfile.open(archives[0]) as archive:
                members = archive.getmembers()
                self.assertTrue(all(m.isfile() or m.isdir() for m in members))
                self.assertTrue(all(not m.name.startswith('/') and '..' not in Path(m.name).parts
                                    for m in members))
                prefixes = {Path(m.name).parts[0] for m in members}
                self.assertEqual(len(prefixes), 1)
                files = {'/'.join(Path(m.name).parts[1:]): archive.extractfile(m).read()
                         for m in members if m.isfile()}
            required = {'MANIFEST.in', 'pyproject.toml', 'README.md', 'LICENSE', 'NOTICE',
                        'THIRD_PARTY_NOTICES.md', 'sdk-pin.txt', 'dependency-review.json',
                        'launch.py'}
            for subdir in ('src/laf_mcp', 'tests', 'licenses'):
                required.update(p.relative_to(ROOT / 'mcp').as_posix()
                                for p in (ROOT / 'mcp' / subdir).rglob('*')
                                if p.is_file() and p.suffix in {'.py', '.txt'})
            self.assertTrue({'tests/fake_stdio.py', 'tests/integrated_router_fixture.py',
                             'tests/test_integrated_router.py', 'tests/test_sdist.py'} <= required)
            for name in sorted(required):
                with self.subTest(name=name):
                    self.assertIn(name, files)
                    self.assertEqual(files[name], (ROOT / 'mcp' / name).read_bytes())
            self.assertFalse(any(any(part in {'.git', '.venv', '__pycache__', 'build'}
                                     for part in Path(name).parts) for name in files))
            self.assertFalse(any(name.endswith(('.pyc', '.whl', '.so')) for name in files))
            # These included tests still import the enclosing LAF repository.
            # An sdist audit pass is not a standalone full-suite execution claim.


if __name__ == '__main__':
    unittest.main()
