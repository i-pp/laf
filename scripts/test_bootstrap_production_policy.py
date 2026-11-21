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

# Added 2026-11-15: explicit protected schema2 policy snapshot import.
"""Private tempfiles/fake Gateway and bounded stdlib CLI children; no live backend."""
from contextlib import redirect_stdout, redirect_stderr
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime/integration'),
              str(ROOT/'runtime/private_codex/src'),str(ROOT/'runtime/public_laf/mcp/src')]
import test_bootstrap_bindings as fixtures
import test_codex_catalog_effort as peers
import laf_bootstrap as boot
from backend_state import load_state,validate_state
from codex_host import HostGateway,executor_configuration
from codex_pipeline import digest


class ProductionPolicyImport(unittest.TestCase):
 def setUp(self):
  f=fixtures.BindingTests('test_codex_first_binding_is_atomic_finite_and_does_not_execute')
  f.setUp();self.addCleanup(f.doCleanups);self.f=f
  self.parent,self.prefix,self.runtime,self.marker=f.parent,f.prefix,f.runtime,f.marker
  self.project=self.prefix/'runtime/private_codex'
  self.development=self.parent/'development';self.development.mkdir()
  self.review=self.parent/'review';self.review.mkdir()
  # Explicit synthetic values, never reconstruction of a real operator policy.
  self.policy={'schema':2,'workspaces':{'development':str(self.development),'review':str(self.review)},
    'models':['gpt-6.1-sol','gpt-6-luna'],'max_threads':16,'max_tasks':16,
    'concurrency':8,'timeout_seconds':1800}
  self.source=self.parent/'operator-policy.json';self.write_policy(self.policy)
 def write_policy(self,value,*,raw=None):
  self.source.write_bytes((json.dumps(value,indent=2)+'\n').encode() if raw is None else raw)
  self.source.chmod(0o600);self.source_hash=hashlib.sha256(self.source.read_bytes()).hexdigest()
 def configure(self,**kw):
  return boot.configure_codex(self.prefix,binary=self.f.native,binary_sha256=self.f.digest,
      policy_file=self.source,policy_sha256=self.source_hash,**kw)
 def state(self):return load_state(self.runtime)[0]
 def unchanged_refusal(self,operation):
  before=self.marker.read_bytes()
  with self.assertRaises((ValueError,OSError)):operation()
  self.assertEqual(self.marker.read_bytes(),before)
  self.assertEqual(sorted(p.name for p in self.runtime.iterdir()),['logs','unconfigured-backends.json'])
 def gateway(self,*,busy=None,workspace=None):
  peer=peers.Peer(str(workspace or self.development));peer.models=[]
  for model in self.policy['models']:
   row=copy.deepcopy(peers.MODEL);row['id']=row['model']=model;peer.models.append(row)
  transports=[]
  def factory(binary):
   value=peers.FakeOwned(peer);transports.append(value);return value
  with patch.object(HostGateway,'require_host'):
   gateway=HostGateway(self.runtime,str(self.f.native),self.project,busy or (lambda:False),transport_factory=factory)
  self.addCleanup(gateway.close)
  # Any metadata connect remains an explicitly injected fixture host context.
  guard=patch.object(HostGateway,'require_host');guard.start();self.addCleanup(guard.stop)
  return gateway,peer,transports

 def test_exact_schema2_values_and_raw_provenance_are_snapshotted(self):
  before=self.source.read_bytes();result=self.configure();binding=self.state()['codex']
  self.assertEqual(binding['policy'],self.policy)
  self.assertEqual(binding['policy_source']['sha256'],self.source_hash)
  self.assertEqual(binding['policy_source']['path'],str(self.source))
  self.assertEqual(len(binding['policy_source']['identity']),7)
  self.assertEqual(self.source.read_bytes(),before)
  self.assertFalse(result['services_started']);self.assertFalse(result['execution_ready'])
  self.assertFalse((self.runtime/'codex-pipeline.sqlite3').exists())
 def test_old_canary_shape_and_restrictions_remain(self):
  self.f.codex();binding=self.state()['codex']
  self.assertNotIn('policy_source',binding)
  self.assertEqual(binding['policy'],{'schema':1,'workspaces':{'default':str(self.prefix/'workspace')},
    'models':['opaque.Sol:raw'],'max_threads':4,'max_tasks':16,'concurrency':1,'timeout_seconds':120})
 def test_direct_api_requires_exactly_one_complete_selection_mode(self):
  cases=[{}, {'policy_file':self.source}, {'policy_sha256':self.source_hash},
    {'workspace':self.prefix/'workspace'}, {'models':['gpt-6.1-sol']},
    {'policy_file':self.source,'policy_sha256':self.source_hash,'workspace':self.prefix/'workspace'},
    {'policy_file':self.source,'policy_sha256':self.source_hash,'models':['gpt-6.1-sol']}]
  for args in cases:
   with self.subTest(args=sorted(args)):
    self.unchanged_refusal(lambda:boot.configure_codex(self.prefix,binary=self.f.native,binary_sha256=self.f.digest,**args))
 def test_native_identity_and_hash_are_still_required(self):
  self.unchanged_refusal(lambda:boot.configure_codex(self.prefix,binary=self.f.native,
    binary_sha256='0'*64,policy_file=self.source,policy_sha256=self.source_hash))
  self.f.native.chmod(0o775)
  self.unchanged_refusal(self.configure)
 def test_cli_rejects_partial_mixed_and_unrelated_flags(self):
  base=['configure-codex','--prefix',str(self.prefix),'--native',str(self.f.native),'--sha256',self.f.digest]
  pair=['--policy-file',str(self.source),'--policy-sha256',self.source_hash]
  choices=[[],pair[:2],pair[2:],pair+['--workspace',str(self.prefix/'workspace')],
           pair+['--model','gpt-6.1-sol'],['--workspace',str(self.prefix/'workspace')],['--model','gpt-6.1-sol']]
  before=self.marker.read_bytes()
  with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
   for args in choices:self.assertEqual(boot.main([*base,*args,'--dry-run']),1)
   self.assertEqual(boot.main([*base,*pair,'--dry-run']),0)
   self.assertEqual(boot.main(['doctor','--prefix',str(self.prefix),*pair]),1)
  self.assertEqual(self.marker.read_bytes(),before)
 def test_fresh_copied_cli_imports_without_pythonpath_or_native_execution(self):
  cmd=[sys.executable,'-B',str(self.prefix/'runtime/integration/laf_bootstrap.py'),
    'configure-codex','--prefix',str(self.prefix),'--native',str(self.f.native),'--sha256',self.f.digest,
    '--policy-file',str(self.source),'--policy-sha256',self.source_hash]
  env={'PATH':'/usr/bin:/bin','HOME':str(self.parent),'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
  before=self.marker.read_bytes()
  for dry in (True,False):
   with patch.object(subprocess,'Popen',fixtures.REAL_POPEN):
    result=subprocess.run(cmd+(['--dry-run'] if dry else []),env=env,cwd=self.parent,
      capture_output=True,text=True,timeout=20)
   self.assertEqual(result.returncode,0,result.stderr)
   self.assertEqual(json.loads(result.stdout)['status'],'planned' if dry else 'configured')
   if dry:self.assertEqual(self.marker.read_bytes(),before)
  self.assertEqual(self.state()['codex']['policy'],self.policy)
  self.assertFalse((self.runtime/'codex-pipeline.sqlite3').exists())
 def test_dry_run_preserves_source_marker_and_plugin_anchor(self):
  before=[p.read_bytes() for p in (self.source,self.marker,self.prefix/'plugin.json')]
  self.assertEqual(self.configure(dry_run=True)['status'],'planned')
  self.assertEqual(before,[p.read_bytes() for p in (self.source,self.marker,self.prefix/'plugin.json')])
 def test_source_hash_is_expected_not_inferred(self):
  self.unchanged_refusal(lambda:boot.configure_codex(self.prefix,binary=self.f.native,
    binary_sha256=self.f.digest,policy_file=self.source,policy_sha256='0'*64))
 def test_raw_formatting_change_does_not_reuse_an_old_pin(self):
  self.source.write_bytes(json.dumps(self.policy,separators=(',',':')).encode())
  self.unchanged_refusal(self.configure)
 def test_duplicate_json_keys_refuse(self):
  raw=json.dumps(self.policy).replace('"schema": 2','"schema": 2, "schema": 2').encode()
  self.write_policy(None,raw=raw);self.unchanged_refusal(self.configure)
 def test_malformed_and_nonfinite_json_refuse(self):
  for raw in (b'{',b'NaN',b'{"schema":Infinity}',b'\xff'):
   with self.subTest(raw=raw):
    self.write_policy(None,raw=raw);self.unchanged_refusal(self.configure)
 def test_schema1_import_is_not_translated_to_production(self):
  value=copy.deepcopy(self.policy);value.update(schema=1,concurrency=1,max_threads=4,timeout_seconds=120)
  self.write_policy(value);self.unchanged_refusal(self.configure)
 def test_unknown_fields_types_bounds_and_models_refuse(self):
  cases=[{'extra':'TOKEN_FIXTURE'},{'schema':True},{'concurrency':0},{'concurrency':17},
    {'timeout_seconds':1801},{'max_threads':False},{'models':['same','same']},{'models':[]},
    {'workspaces':{}},{'models':['bad\nmodel']}]
  for change in cases:
   with self.subTest(change=change):
    value=copy.deepcopy(self.policy);value.update(change);self.write_policy(value)
    self.unchanged_refusal(self.configure)
 def test_workspace_paths_use_existing_validator_and_are_not_created(self):
  for path in (self.parent/'missing',Path('relative-workspace')):
   value=copy.deepcopy(self.policy);value['workspaces']={'development':str(path)}
   self.write_policy(value);self.unchanged_refusal(self.configure)
   self.assertFalse(path.is_absolute() and path.exists())
  alias=self.parent/'workspace-link';alias.symlink_to(self.development,target_is_directory=True)
  value['workspaces']={'development':str(alias)};self.write_policy(value);self.unchanged_refusal(self.configure)
 def test_relative_dotdot_and_symlink_policy_paths_refuse(self):
  alias=self.parent/'policy-link';alias.symlink_to(self.source)
  for path in (Path('operator-policy.json'),self.parent/'..'/self.parent.name/self.source.name,alias):
   with self.subTest(path=path):
    self.unchanged_refusal(lambda:boot.configure_codex(self.prefix,binary=self.f.native,
      binary_sha256=self.f.digest,policy_file=path,policy_sha256=self.source_hash))
 def test_policy_mode_and_owner_refuse_without_repair(self):
  self.source.chmod(0o644);self.unchanged_refusal(self.configure)
  self.assertEqual(stat.S_IMODE(self.source.stat().st_mode),0o644);self.source.chmod(0o600)
  real=boot.os.stat
  def foreign(path,*args,**kw):
   value=real(path,*args,**kw)
   if Path(path).name==self.source.name and kw.get('dir_fd') is not None:
    fields=list(value);fields[4]=os.getuid()+1;return os.stat_result(fields)
   return value
  with patch.object(boot.os,'stat',side_effect=foreign):self.unchanged_refusal(self.configure)
 def test_hardlinked_policy_is_not_an_authority_source(self):
  alias=self.parent/'hardlink';os.link(self.source,alias)
  self.unchanged_refusal(self.configure);self.assertEqual(self.source.stat().st_nlink,2)
 def test_nonregular_directory_and_fifo_refuse_without_opening(self):
  directory=self.parent/'policy-dir';directory.mkdir(mode=0o700)
  fifo=self.parent/'policy-fifo';os.mkfifo(fifo,0o600)
  for path in (directory,fifo):
   self.unchanged_refusal(lambda:boot.configure_codex(self.prefix,binary=self.f.native,
     binary_sha256=self.f.digest,policy_file=path,policy_sha256='0'*64))
 def test_untrusted_or_symlink_ancestry_refuses(self):
  unsafe=self.parent/'unsafe';unsafe.mkdir();target=unsafe/self.source.name
  target.write_bytes(self.source.read_bytes());target.chmod(0o600);unsafe.chmod(0o777)
  self.unchanged_refusal(lambda:boot.configure_codex(self.prefix,binary=self.f.native,
    binary_sha256=self.f.digest,policy_file=target,policy_sha256=self.source_hash))
  unsafe.chmod(0o700);alias=self.parent/'ancestor-link';alias.symlink_to(unsafe,target_is_directory=True)
  self.unchanged_refusal(lambda:boot.configure_codex(self.prefix,binary=self.f.native,
    binary_sha256=self.f.digest,policy_file=alias/target.name,policy_sha256=self.source_hash))
 def test_empty_and_oversized_input_refuse(self):
  for raw in (b'',b' '*(boot.OPERATOR_POLICY_LIMIT+1)):
   self.write_policy(None,raw=raw);self.unchanged_refusal(self.configure)
 def test_source_replacement_between_stat_and_open_refuses(self):
  real=boot.os.open;swapped=False
  def opening(path,*args,**kwargs):
   nonlocal swapped
   if Path(path).name==self.source.name and kwargs.get('dir_fd') is not None and not swapped:
    swapped=True;replacement=self.parent/'replacement'
    replacement.write_bytes(self.source.read_bytes());replacement.chmod(0o600);os.replace(replacement,self.source)
   return real(path,*args,**kwargs)
  with patch.object(boot.os,'open',side_effect=opening):self.unchanged_refusal(self.configure)
  self.assertTrue(swapped)
 def test_unchecked_opened_descriptor_is_refused_before_reading(self):
  real_open,real_fdopen=boot.os.open,boot.os.fdopen
  swapped=False;replacement_inode=None;reads=[]
  def opening(path,*args,**kwargs):
   nonlocal swapped,replacement_inode
   if Path(path).name==self.source.name and kwargs.get('dir_fd') is not None and not swapped:
    swapped=True;replacement=self.parent/'replacement'
    replacement.write_bytes(self.source.read_bytes());replacement.chmod(0o600)
    replacement_inode=replacement.stat().st_ino;os.replace(replacement,self.source)
   return real_open(path,*args,**kwargs)
  def reading(fd,*args,**kwargs):
   if os.fstat(fd).st_ino==replacement_inode:reads.append('unchecked read')
   return real_fdopen(fd,*args,**kwargs)
  with patch.object(boot.os,'open',side_effect=opening),patch.object(boot.os,'fdopen',side_effect=reading):
   self.unchanged_refusal(self.configure)
  self.assertTrue(swapped);self.assertEqual(reads,[])
 def test_source_ancestor_swap_after_directory_pin_is_refused(self):
  authority=self.parent/'authority';authority.mkdir(mode=0o700)
  self.source.rename(authority/self.source.name);self.source=authority/self.source.name
  real=boot.os.open;swapped=False
  def opening(path,*args,**kwargs):
   nonlocal swapped
   if Path(path).name==self.source.name and kwargs.get('dir_fd') is not None and not swapped:
    swapped=True;backup=self.parent/'authority-original';authority.rename(backup)
    authority.symlink_to(backup,target_is_directory=True)
   return real(path,*args,**kwargs)
  with patch.object(boot.os,'open',side_effect=opening):self.unchanged_refusal(self.configure)
  self.assertTrue(swapped)
 def test_same_inode_content_change_before_publication_refuses(self):
  real=boot._write_all
  def changing(fd,raw):
   real(fd,raw);self.source.write_bytes(self.source.read_bytes()+b' ')
  with patch.object(boot,'_write_all',side_effect=changing):self.unchanged_refusal(self.configure)
 def test_same_bytes_new_identity_before_publication_refuses(self):
  real=boot._write_all
  def replacing(fd,raw):
   real(fd,raw);replacement=self.parent/'replacement'
   replacement.write_bytes(self.source.read_bytes());replacement.chmod(0o600);os.replace(replacement,self.source)
  with patch.object(boot,'_write_all',side_effect=replacing):self.unchanged_refusal(self.configure)
 def test_workspace_change_before_publication_refuses(self):
  real=boot._write_all
  def changing(fd,raw):
   real(fd,raw);self.development.rename(self.parent/'original-development')
   self.development.symlink_to(self.review,target_is_directory=True)
  with patch.object(boot,'_write_all',side_effect=changing):self.unchanged_refusal(self.configure)
 def test_prepublication_failure_preserves_old_binding_and_owned_cleanup(self):
  with patch.object(boot.os,'replace',side_effect=OSError('before-publication')):
   self.unchanged_refusal(self.configure)
 def test_postpublication_failure_retains_import_and_forbids_rebinding(self):
  real=boot.os.fsync
  def fail_directory(fd):
   if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('after-publication')
   return real(fd)
  with patch.object(boot.os,'fsync',side_effect=fail_directory):
   with self.assertRaises(OSError):self.configure()
  self.assertEqual(self.state()['codex']['policy'],self.policy)
  before=self.marker.read_bytes()
  with self.assertRaises(ValueError):self.configure()
  self.assertEqual(self.marker.read_bytes(),before)
 def test_used_runtime_receipts_and_existing_binding_are_never_adopted(self):
  used=self.runtime/'tasks.sqlite';used.write_bytes(b'kept')
  before=self.marker.read_bytes()
  with self.assertRaises(ValueError):self.configure()
  self.assertEqual(used.read_bytes(),b'kept');used.unlink()
  receipt=self.prefix/'receipts/retained';receipt.write_bytes(b'UNKNOWN')
  with self.assertRaises(ValueError):self.configure()
  self.assertEqual(receipt.read_bytes(),b'UNKNOWN');receipt.unlink()
  self.assertEqual(self.marker.read_bytes(),before);self.configure();bound=self.marker.read_bytes()
  with self.assertRaises(ValueError):self.configure()
  self.assertEqual(self.marker.read_bytes(),bound)
 def test_import_provenance_shape_and_schema_cannot_be_forged_or_mixed(self):
  self.configure();original=self.state()
  cases=[]
  def changed(fn):
   value=copy.deepcopy(original);fn(value['codex']);cases.append(value)
  changed(lambda c:c.pop('policy_source'))
  changed(lambda c:c['policy_source'].update(extra=True))
  changed(lambda c:c['policy_source'].update(path='relative'))
  changed(lambda c:c['policy_source'].update(sha256='not-a-hash'))
  changed(lambda c:c['policy_source'].update(identity=[0]*7))
  changed(lambda c:c['policy_source'].pop('directory_identity'))
  changed(lambda c:c['policy_source'].update(directory_identity=[True,1]))
  changed(lambda c:c['policy'].update(schema=1,concurrency=1,max_threads=4,timeout_seconds=120))
  for value in cases:
   with self.assertRaises(ValueError):validate_state(value,self.runtime,len(json.dumps(value)))
 def test_runtime_uses_embedded_policy_after_source_is_removed(self):
  self.configure();self.source.unlink()
  self.assertEqual(self.state()['codex']['policy'],self.policy)
  with patch.object(boot,'read_operator_policy',side_effect=AssertionError('No live policy read')):
   gateway,peer,transports=self.gateway()
   result=gateway.dispatch('codex_capabilities',{})
  self.assertEqual(gateway.pipeline.policy,self.policy)
  self.assertEqual(result['bounds'],{k:self.policy[k] for k in ('max_threads','max_tasks','concurrency','timeout_seconds')})
  self.assertEqual(result['workspaces'],list(self.policy['workspaces']))
  self.assertEqual(peer.count('turn/start'),0);self.assertEqual(len(transports),1)
  self.assertTrue(transports[0].closed)
 def test_imported_policy_admits_one_named_workspace_turn_with_exact_selection(self):
  self.configure();gateway,peer,transports=self.gateway(workspace=self.review)
  thread=gateway.dispatch('codex_thread_start',{'workspace':'review','model':'gpt-6.1-sol',
    'reasoning_effort':'high','request_key':'review-thread'})
  task=gateway.dispatch('codex_task_submit',{'thread_id':thread['thread_id'],
    'model':'gpt-6.1-sol','reasoning_effort':'high','prompt':'One synthetic harmless input.',
    'request_key':'review-task'})
  self.assertEqual(task['state'],'RUNNING');self.assertEqual(peer.count('turn/start'),1)
  creation=next(args for method,args in peer.calls if method=='thread/start')
  turn=next(args for method,args in peer.calls if method=='turn/start')
  self.assertEqual(creation['cwd'],str(self.review));self.assertEqual(turn['cwd'],str(self.review))
  self.assertEqual(creation['model'],'gpt-6.1-sol');self.assertEqual(turn['model'],'gpt-6.1-sol')
  self.assertEqual(creation['config'],{'model_reasoning_effort':'high'});self.assertEqual(turn['effort'],'high')
  self.assertEqual(gateway.pipeline.policy,self.policy)
  remote=gateway.pipeline._mutate(lambda state:state['threads'][thread['thread_id']]['remote_id'])
  completed=peer.threads[remote]['turns'][-1];completed.update(status='completed',items=[])
  gateway.protocol.receive({'method':'turn/completed','params':{'threadId':remote,'turn':completed}})
  gateway.pump();self.assertIsNone(gateway.budget)
  self.assertEqual(peer.count('turn/start'),1);self.assertTrue(transports[0].closed)
 def test_imported_policy_does_not_bypass_ordinary_busy_admission(self):
  self.configure();busy=False;gateway,peer,transports=self.gateway(busy=lambda:busy)
  thread=gateway.dispatch('codex_thread_start',{'workspace':'development','model':'gpt-6.1-sol',
    'reasoning_effort':'high','request_key':'busy-thread'})
  gateway.release_idle(force=True);busy=True;before=len(peer.calls)
  task=gateway.dispatch('codex_task_submit',{'thread_id':thread['thread_id'],
    'model':'gpt-6.1-sol','reasoning_effort':'high','prompt':'A stored fixture input.',
    'request_key':'busy-task'})
  self.assertIn(task['state'],('QUEUED','BLOCKED'))
  self.assertEqual(peer.count('turn/start'),0);self.assertEqual(len(peer.calls),before)
 def test_binding_mutation_is_fenced_before_any_native_transport(self):
  self.configure();gateway,peer,transports=self.gateway()
  value=self.state();value['codex']['policy']['concurrency']=7
  self.marker.write_text(json.dumps(value))
  result=gateway.dispatch('codex_capabilities',{})
  self.assertEqual(result['catalog']['status'],'unknown')
  self.assertEqual(result['catalog']['code'],'EXECUTOR_POLICY_CHANGED')
  self.assertTrue(all(not m['available'] and not m['selectable'] for m in result['models']))
  self.assertEqual(gateway.pipeline.policy['concurrency'],8)
  self.assertEqual(transports,[]);self.assertEqual(peer.count('turn/start'),0)
 def test_final_serialized_binding_limit_is_independent_of_input_limit(self):
  directory=self.parent
  for i in range(5):
   directory=directory/(chr(0x754c)*70);directory.mkdir()
  value=copy.deepcopy(self.policy)
  value['workspaces']={'workspace-'+str(i):str(directory) for i in range(8)}
  self.write_policy(value,raw=(json.dumps(value,ensure_ascii=False)+'\n').encode())
  self.assertLess(self.source.stat().st_size,boot.OPERATOR_POLICY_LIMIT)
  self.unchanged_refusal(self.configure)
 def test_daemon_command_reports_bound_policy_instead_of_canary_default(self):
  self.configure();result=boot.daemon_command(self.prefix)
  self.assertEqual(result['pipeline_policy_schema'],2)
  self.assertEqual(result['pipeline_concurrency'],8)
  self.assertIn('--allow-codex-pipeline',result['command'])
  self.assertFalse(result['services_started']);self.assertFalse(boot.doctor(self.prefix)['execution_ready'])


if __name__=='__main__':unittest.main(verbosity=2)
