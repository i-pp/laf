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

import copy
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from .workspace import Workspace, validate_manifest, relative_path


def manifest():
    return {'version':1,'goal':'Fix arithmetic without modifying tests.',
            'files':[{'path':'src/math.py','access':'write'}, {'path':'tests/test_math.py','access':'read'}],
            'test':{'kind':'python-unittest','start_dir':'tests','pattern':'test_*.py'}}


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.source=self.base/'source';self.source.mkdir()
        (self.source/'src').mkdir();(self.source/'tests').mkdir()
        (self.source/'src/math.py').write_text('def add(a,b):\n    return a-b\n')
        (self.source/'tests/test_math.py').write_text('# operator-selected immutable tests\n')
    def tearDown(self):self.temp.cleanup()
    def new(self):return Workspace(self.source,self.base/'run',manifest())
    def test_snapshot_never_modifies_original_and_freezes_manifest(self):
        value=manifest()
        with Workspace(self.source,self.base/'run',value) as work:
            value['files'][0]['access']='read'
            read=work.read_file('src/math.py')
            work.replace_text('src/math.py',read['sha256'],'a-b','a+b')
            self.assertIn('a-b',(self.source/'src/math.py').read_text())
            self.assertIn('a+b',work.read_file('src/math.py')['content'])
            self.assertEqual(len(work.changes()),1)
            self.assertEqual(work.path.stat().st_mode & 0o777,0o700)
            self.assertEqual((work.root/'capability.json').stat().st_mode & 0o777,0o600)
    def test_denies_paths_and_readonly_tests(self):
        with self.new() as work:
            for path in ['../outside','/etc/passwd','src/../math.py','src\\math.py','.git/config','x']:
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):work.read_file(path)
            read=work.read_file('tests/test_math.py')
            with self.assertRaises(ValueError):work.replace_text('tests/test_math.py',read['sha256'],'#','')
    def test_stale_hash_no_blind_second_write(self):
        with self.new() as work:
            old=work.read_file('src/math.py')['sha256']
            work.replace_text('src/math.py',old,'a-b','a+b')
            with self.assertRaises(ValueError):work.replace_text('src/math.py',old,'a+b','a*b')
    def test_source_symlink_and_hardlink_rejected(self):
        original=self.source/'src/math.py';original.unlink()
        original.symlink_to(self.source/'tests/test_math.py')
        with self.assertRaises(OSError):self.new()
        original.unlink();os.link(self.source/'tests/test_math.py',original)
        with self.assertRaises(ValueError):self.new()
    def test_directory_symlink_cannot_escape(self):
        old=self.source/'src/math.py';old.unlink();(self.source/'src').rmdir()
        (self.source/'src').symlink_to(self.source/'tests',target_is_directory=True)
        with self.assertRaises(OSError):self.new()
    def test_workspace_replacement_with_symlink_is_not_followed(self):
        with self.new() as work:
            path=work.path/'src/math.py';path.unlink();path.symlink_to(self.source/'src/math.py')
            with self.assertRaises((OSError,ValueError)):work.read_file('src/math.py')
    def test_replaced_snapshot_root_is_not_the_held_capability(self):
        with self.new() as work:
            work.path.rename(work.root/'old-workspace')
            work.path.mkdir(mode=0o700)
            with self.assertRaises(ValueError):work.hashes()
            with self.assertRaises(ValueError):work.identity()
    def test_unapproved_extra_file_and_hardlink_rejected(self):
        with self.new() as work:
            extra=work.path/'extra.py';extra.write_text('not in manifest')
            with self.assertRaises(ValueError):work.changes()
            extra.unlink()
            os.link(work.path/'src/math.py',extra)
            with self.assertRaises(ValueError):work.changes()
    def test_replaced_controller_root_rejected(self):
        with self.new() as work:
            work.root.rename(self.base/'old-run');work.root.mkdir(mode=0o700)
            with self.assertRaises(ValueError):work.read_file('src/math.py')
    def test_existing_workdir_never_overwritten(self):
        (self.base/'run').mkdir()
        with self.assertRaises(FileExistsError):self.new()
    def test_nonregular_and_oversize_source_rejected(self):
        path=self.source/'src/math.py';path.unlink();os.mkfifo(path)
        with self.assertRaises(ValueError):self.new()
        path.unlink();path.write_bytes(b'x'*65537)
        with self.assertRaises(ValueError):self.new()
    def test_unchanged_test_hash_is_independently_checked(self):
        with self.new() as work:
            p=work.path/'tests/test_math.py';p.chmod(0o600);p.write_text('# altered outside client')
            with self.assertRaises(ValueError):work.changes()
    def test_bounded_read_and_unique_match(self):
        with self.new() as work:
            with self.assertRaises(ValueError):work.read_file('src/math.py',0)
            read=work.read_file('src/math.py')
            with self.assertRaises(ValueError):work.replace_text('src/math.py',read['sha256'],'notfound','x')
            self.assertEqual(len(work.changes()),0)
    def test_overlapping_match_is_not_unique(self):
        (self.source/'src/math.py').write_text('aaa')
        with self.new() as work:
            old=work.read_file('src/math.py')['sha256']
            with self.assertRaises(ValueError):work.replace_text('src/math.py',old,'aa','b')
    def test_total_snapshot_budget_applies_after_edit(self):
        from unittest.mock import patch
        with self.new() as work:
            read=work.read_file('src/math.py')
            with patch('inference.local_platform.workspace.MAX_TOTAL',1):
                with self.assertRaises(ValueError):work.replace_text('src/math.py',read['sha256'],'a-b','a+b')
    def test_long_single_line_reports_unsupported_read(self):
        (self.source/'src/math.py').write_text('x'*2049)
        with self.new() as work:
            with self.assertRaises(ValueError):work.read_file('src/math.py')
    def test_colocated_application_and_tests_allow_only_application_write(self):
        value=manifest()
        value['files']=[{'path':'package/code.py','access':'write'},
                        {'path':'package/test_code.py','access':'read'}]
        value['test']['start_dir']='package'
        self.assertEqual(validate_manifest(value),value)
        value['files'][1]['access']='write'
        with self.assertRaises(ValueError):validate_manifest(value)
    def test_documented_manifest_validates(self):
        import re
        text=Path(__file__).with_name('workspace-client.md').read_text()
        value=json.loads(re.search(r'```json\n(.*?)\n```',text,re.S).group(1))
        validate_manifest(value)
    def test_manifest_requires_selected_readonly_tests(self):
        for change in ['unknown','writable_test','no_tests','unknown_runner','duplicate']:
            value=manifest()
            if change=='unknown':value['shell']='sh'
            if change=='writable_test':value['files'][1]['access']='write'
            if change=='no_tests':value['files']=value['files'][:1]
            if change=='unknown_runner':value['test']['kind']='shell'
            if change=='duplicate':value['files'].append(value['files'][0])
            with self.subTest(change=change):
                with self.assertRaises(ValueError):validate_manifest(value)
    def test_hidden_secrets_and_ambiguous_paths_rejected(self):
        for path in ['.env','src/.ssh/key','a//b.py','a:b.py','a\x00b','a/./b.py','']:
            with self.assertRaises(ValueError):relative_path(path)

if __name__=='__main__':unittest.main()
