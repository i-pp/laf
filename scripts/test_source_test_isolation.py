#!/usr/bin/env python3
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

# Added 2026-11-15: physically isolated source tests and fail-closed fixture access.

"""Offline namespace proofs and negative ledger-access controls, never host state."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import stat
import sys
import tempfile
import unittest
import io
from contextlib import redirect_stderr
from unittest.mock import patch
from types import SimpleNamespace
import source_test_isolation as isolation

isolation.require_isolated()
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime/private_codex/src'),str(ROOT/'scripts')]
import resource_admission as admission
import test_resource_only as fixtures


class IsolationBoundaries(unittest.TestCase):
 def test_kernel_proof_precedes_fixture_imports(self):
  proof=isolation.require_isolated();self.assertEqual(proof['uid'],os.getuid())
  self.assertEqual(os.listdir(proof['guard']),[])
 def test_env_hint_without_private_namespace_proof_refuses(self):
  receipt=json.loads(os.environ[isolation.RECEIPT]);receipt['namespaces']=isolation.namespace_ids()
  with patch.dict(os.environ,{isolation.RECEIPT:json.dumps(receipt)}):
   with self.assertRaises(isolation.IsolationError):isolation.require_isolated()
 def test_missing_receipt_refuses_before_default_directory_stat(self):
  with patch.dict(os.environ,{},clear=True),patch.object(os,'stat',side_effect=AssertionError('No default path access')):
   with self.assertRaises(isolation.IsolationError):isolation.require_isolated()
 def test_longest_mount_and_escape_decoding(self):
  table=isolation.mounts('1 0 0:1 / / rw - ext4 x rw\n2 1 0:2 / /private\\040space ro - tmpfs x ro,size=1k')
  name,mount=isolation.covering(table,'/private space/file');self.assertEqual(name,'/private space');self.assertIn('ro',mount['options'])
 def test_missing_bwrap_never_runs_an_unisolated_fallback(self):
  with patch.object(isolation.shutil,'which',return_value=None),patch.object(isolation.subprocess,'run',side_effect=AssertionError('No fallback')):
   with self.assertRaises(isolation.IsolationError):isolation.launch('integrated')
 def test_caught_unexpected_guard_error_still_fails_an_otherwise_green_suite(self):
  with patch.object(isolation,'COUNTS',{'blocked':1,'expected':0}),patch.object(isolation,'UNRAISABLE',[]),redirect_stderr(io.StringIO()):
   self.assertEqual(isolation.finish(0),2)
 def test_unraisable_resource_warning_cannot_be_a_green_suite(self):
  with patch.object(isolation,'COUNTS',{'blocked':0,'expected':0}),patch.object(isolation,'UNRAISABLE',['ResourceWarning']),redirect_stderr(io.StringIO()):
   self.assertEqual(isolation.finish(0),2)
 def test_output_budget_kills_a_noisy_disposable_python_child(self):
  command=['/usr/bin/python3','-I','-B','-c',"import sys;sys.stdout.buffer.write(b'x'*(9*1024**2))"]
  with self.assertRaisesRegex(isolation.IsolationError,'output exceeds bound'):isolation.bounded_capture(command,(),5)
 def test_timeout_reaps_a_sleeping_disposable_python_child(self):
  command=['/usr/bin/python3','-I','-B','-c','import time;time.sleep(10)']
  with self.assertRaises(subprocess.TimeoutExpired):isolation.bounded_capture(command,(),1)
 def test_explicit_private_sqlite_fixture_still_works(self):
  with tempfile.TemporaryDirectory() as temporary:
   with sqlite3.connect(Path(temporary)/'fixture.sqlite') as db:db.execute('CREATE TABLE fixture(x)')
   db.close()
 def test_review_harness_outside_fixture_paths_refuses_before_payload_open(self):
  real=os.open;seen=[]
  def opening(path,*args,**kw):seen.append(str(path));return real(path,*args,**kw)
  forbidden=Path(isolation.require_isolated()['guard'])/'reservations.sqlite'
  with patch.object(os,'open',side_effect=opening):
   with self.assertRaises(isolation.IsolationError):isolation.launch('harness',harness_file=forbidden,harness_sha='0'*64)
  self.assertNotIn(str(forbidden),seen)
 def test_missing_or_unbounded_test_timeout_refuses_before_scratch(self):
  with patch.object(tempfile,'TemporaryDirectory',side_effect=AssertionError('No scratch for invalid timeout')):
   for value in (0,601,None,True,float('inf')):
    with self.assertRaises(isolation.IsolationError):isolation.launch('integrated',timeout=value)
 def test_parent_scratch_ignores_inherited_temp_directory_overrides(self):
  def temporary(*args,**kwargs):
   self.assertEqual(kwargs.get('dir'),'/tmp');raise isolation.IsolationError('fixture sentinel')
  with patch.dict(os.environ,{'TMPDIR':isolation.require_isolated()['guard']}),patch.object(tempfile,'TemporaryDirectory',side_effect=temporary):
   with self.assertRaisesRegex(isolation.IsolationError,'fixture sentinel'):isolation.launch('integrated')
 def test_default_sqlite_open_is_refused_before_driver_access(self):
  path=Path(isolation.require_isolated()['guard'])/'reservations.sqlite'
  isolation.expect_refusal(lambda:sqlite3.connect(path))
 def test_default_open_mkdir_and_uri_aliases_are_refused(self):
  path=Path(isolation.require_isolated()['guard'])/'reservations.sqlite'
  for operation in (lambda:os.open(path,os.O_RDONLY),lambda:path.parent.mkdir(exist_ok=True),lambda:sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True),lambda:sqlite3.connect('file:'+str(path).replace('/','%2f')+'?mode=ro',uri=True)):
   isolation.expect_refusal(operation)
 def test_sqlite_proc_descriptor_aliases_are_refused(self):
  for value in ('/proc/self/fd/1','file:/proc/self/root/tmp/fixture?mode=ro','file://localhost/tmp/fixture?mode=ro'):
   isolation.expect_refusal(lambda value=value:sqlite3.connect(value,uri=value.startswith('file:')))
  with tempfile.TemporaryDirectory() as temporary:
   path=Path(temporary)/'fixture.sqlite';path.write_bytes(b'');fd=os.open(path,os.O_RDWR)
   try:
    for value in ('file:////proc/self/fd/'+str(fd),'file:../../proc/self/fd/'+str(fd)):
     isolation.expect_refusal(lambda value=value:sqlite3.connect(value,uri=True))
   finally:os.close(fd)
 def test_dirfd_relative_default_open_and_mkdir_are_refused_before_kernel_access(self):
  proof=isolation.require_isolated();fd=os.open(proof['home'],os.O_PATH|os.O_DIRECTORY)
  try:
   target='.local/state/laf/resource-admission'
   isolation.expect_refusal(lambda:os.open(target+'/never.sqlite',os.O_WRONLY|os.O_CREAT,0o600,dir_fd=fd))
   isolation.expect_refusal(lambda:os.mkdir(target+'/never',dir_fd=fd))
  finally:os.close(fd)
 def test_fresh_exec_entrypoint_bootstraps_audit_and_rejects_swallowed_default_access(self):
  code='''import os,pwd,sqlite3,sys
sys.path.insert(0,'/source/scripts')
import source_test_isolation as isolated
def main():
 assert isolated.INSTALLED
 try:sqlite3.connect(pwd.getpwuid(os.getuid()).pw_dir+'/.local/state/laf/resource-admission/never.sqlite')
 except isolated.FixtureAccessError:pass
 return 0
raise SystemExit(isolated.entrypoint('integrated',main))
'''
  result=subprocess.run(['/usr/bin/python3','-I','-B','-c',code],stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=10)
  self.assertEqual(result.returncode,2,result.stderr);self.assertIn('SOURCE_TEST_BOUNDARY_FAILURE',result.stderr)
 def test_late_global_sqlite_warning_is_a_fatal_child_exit(self):
  code='''import sqlite3,sys
sys.path.insert(0,'/source/scripts')
import source_test_isolation as isolated
def main():
 global retained_connection
 retained_connection=sqlite3.connect('/tmp/retained-global.sqlite')
 return 0
raise SystemExit(isolated.entrypoint('integrated',main))
'''
  result=subprocess.run(['/usr/bin/python3','-I','-B','-c',code],stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=10)
  self.assertEqual(result.returncode,2,result.stderr);self.assertIn('SOURCE_TEST_UNRAISABLE_FATAL',result.stderr)
 def test_symlink_to_default_directory_is_refused(self):
  with tempfile.TemporaryDirectory() as temporary:
   alias=Path(temporary)/'alias';alias.symlink_to(isolation.require_isolated()['guard'],target_is_directory=True)
   isolation.expect_refusal(lambda:sqlite3.connect(alias/'reservations.sqlite'))
 def test_all_three_copied_ledger_classes_refuse_without_directory_override(self):
  cores=[admission]
  for n,name in enumerate(('inference/laf_service/resource_admission_core.py','runtime/public_laf/inference/laf_service/resource_admission_core.py')):
   spec=importlib.util.spec_from_file_location('guarded_copy_'+str(n),ROOT/name);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);cores.append(module)
  passwd=SimpleNamespace(stat=lambda:SimpleNamespace(st_mode=stat.S_IFREG|0o644,st_uid=0,st_size=100000),
    is_symlink=lambda:False,read_text=lambda:Path('/etc/passwd').read_text())
  for module in cores:
   with patch.object(module,'DIRECTORY',None),patch.object(module,'PASSWD',passwd):
    isolation.expect_refusal(lambda module=module:module.Ledger(fixtures.policy()))
 def test_readonly_source_cannot_be_a_sqlite_fixture(self):
  isolation.expect_refusal(lambda:sqlite3.connect(ROOT/'forbidden.sqlite'))
 def test_supported_command_hides_host_home_and_every_tempfs_is_bounded(self):
  with tempfile.TemporaryDirectory() as temporary:
   directory=Path(temporary);fd=os.open(directory,os.O_PATH|os.O_DIRECTORY)
   try:command=isolation.build_command('/usr/bin/bwrap',directory,fd,'integrated',[])
   finally:os.close(fd)
  self.assertEqual(command.count('--size'),4);self.assertEqual(command.count('--tmpfs'),4)
  for flag in ('--unshare-all','--new-session','--clearenv','--die-with-parent','--ro-bind-fd'):self.assertIn(flag,command)
  self.assertIn(['--proc','/proc'],[command[n:n+2] for n in range(len(command)-1)])
  self.assertIn(['--dev','/dev'],[command[n:n+2] for n in range(len(command)-1)])


if __name__=='__main__':unittest.main()
