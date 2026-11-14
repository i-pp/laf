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

# Added 2026-11-12: integrated source-tree archive preparation.

"""NEW-prefix bootstrap tests: offline source/tempfiles only, no auth or starts."""
from contextlib import ExitStack
import hashlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime/integration'),str(ROOT/'runtime/private_codex/src'),str(ROOT/'runtime/public_laf/mcp/src')]
import laf_bootstrap as bootstrap
REAL_POPEN=subprocess.Popen

def tree_snapshot(root):
 result={}
 for path in (root,*root.rglob('*')):
  info=path.lstat()
  result[str(path.relative_to(root))]=(stat.S_IFMT(info.st_mode),stat.S_IMODE(info.st_mode),
      path.read_bytes() if stat.S_ISREG(info.st_mode) else os.readlink(path) if stat.S_ISLNK(info.st_mode) else None)
 return result

class BootstrapTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path.home());self.addCleanup(self.temp.cleanup)
  self.parent=Path(self.temp.name);self.parent.chmod(0o700);self.prefix=self.parent/'new'
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  for name in ('subprocess.Popen','socket.socket.bind','socket.socket.connect','threading.Thread.start'):
   self.stack.enter_context(patch(name,side_effect=AssertionError('Bootstrap tests forbid execution/network')))
 def prepare(self,**kw):return bootstrap.prepare_prefix(self.prefix,**kw)
 def test_dry_run_is_readonly_and_explicit_not_service_ready(self):
  result=self.prepare(dry_run=True)
  self.assertFalse(self.prefix.exists());self.assertEqual(result['status'],'planned')
  self.assertFalse(result['services_started']);self.assertFalse(result['execution_ready'])
 def test_real_source_and_configs_anchor_new_runtime_without_copying_state(self):
  result=self.prepare();self.assertEqual(result['status'],'prepared')
  project=self.prefix/'runtime/private_codex';runtime=project/'.runtime/main'
  self.assertEqual((project/'src/daemon.py').read_bytes(),(ROOT/'runtime/private_codex/src/daemon.py').read_bytes())
  for name in ('resource-limits.json','scheduler-config.json','verification-commands.json'):
   self.assertEqual((project/name).read_bytes(),(ROOT/'runtime/private_codex'/name).read_bytes())
  from runtime_scope import runtime_path
  self.assertEqual(runtime_path(runtime,project_root=project),runtime)
  self.assertEqual(stat.S_IMODE(runtime.stat().st_mode),0o700)
  self.assertEqual(stat.S_IMODE((self.prefix/'plugin.json').stat().st_mode),0o600)
  self.assertFalse(any(p.name in ('auth.json','AGENTS.md') or p.suffix in ('.sqlite','.sqlite3') for p in self.prefix.rglob('*')))
  self.assertFalse(result['execution_ready']);self.assertEqual(result['unconfigured_backends'],list(bootstrap.BACKENDS))
 def test_existing_prefix_is_never_reused_or_changed(self):
  self.prefix.mkdir();sentinel=self.prefix/'keep';sentinel.write_bytes(b'preserve')
  with self.assertRaises(bootstrap.BootstrapError):self.prepare()
  self.assertEqual(sentinel.read_bytes(),b'preserve');self.assertEqual(list(self.prefix.iterdir()),[sentinel])
 def test_relative_dotdot_symlink_and_cache_targets_are_refused(self):
  targets=[Path('relative'),self.parent/'x/../escape',ROOT/'runtime/owned-new',self.parent/'.codex/plugins/cache/new']
  real=self.parent/'real';real.mkdir();link=self.parent/'link';link.symlink_to(real,target_is_directory=True);targets.append(link/'new')
  for target in targets:
   with self.subTest(target=target),self.assertRaises(bootstrap.BootstrapError):bootstrap.prepare_prefix(target,dry_run=True)
 def test_writable_parent_refused_without_chmod_or_creation(self):
  self.parent.chmod(0o777)
  with self.assertRaises(bootstrap.BootstrapError):self.prepare()
  self.assertEqual(stat.S_IMODE(self.parent.stat().st_mode),0o777);self.assertFalse(self.prefix.exists())
  self.parent.chmod(0o700)
 def test_default_umask_does_not_expose_files_and_is_restored(self):
  old=os.umask(0)
  try:
   self.prepare();observed=os.umask(0)
   self.assertEqual(observed,0)
   for path in self.prefix.rglob('*'):
    self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o700 if path.is_dir() else 0o600)
  finally:os.umask(old)
 def test_fault_cleans_only_new_owned_prefix(self):
  outside=self.parent/'outside';outside.write_bytes(b'keep')
  with patch.object(bootstrap,'write_file',side_effect=OSError('injected')):
   with self.assertRaises(OSError):self.prepare()
  self.assertFalse(self.prefix.exists());self.assertEqual(outside.read_bytes(),b'keep')
 def test_doctor_is_readonly_and_missing_sdk_is_reported(self):
  self.prepare();before={str(p.relative_to(self.prefix)):p.read_bytes() for p in self.prefix.rglob('*') if p.is_file()}
  result=bootstrap.doctor(self.prefix)
  self.assertEqual(result['status'],'prepared');self.assertFalse(result['execution_ready'])
  self.assertEqual(result['sdk']['status'],'unconfigured');self.assertFalse(result['services_probed'])
  self.assertEqual(before,{str(p.relative_to(self.prefix)):p.read_bytes() for p in self.prefix.rglob('*') if p.is_file()})
 def test_policy_is_self_contained_strict_and_not_a_legacy_rewrite(self):
  self.prepare();project=self.prefix/'runtime/private_codex'
  from common_policy import load_project_policy,developer_instructions,PolicyError
  with patch('subprocess.run',side_effect=AssertionError('No Git checkout/network needed')):
   bundle=load_project_policy(project);text=developer_instructions(bundle)
  self.assertEqual(bundle.version,'git:'+bootstrap.POLICY_COMMIT)
  self.assertIn('bundled_snapshot',json.dumps(bundle.metadata()));self.assertIn(bundle.text,text)
  self.assertFalse((project/'policies.md').exists())
  path=project/'policy-snapshot/docs/ai/orchestration.md';path.write_bytes(path.read_bytes()+b'tamper')
  with self.assertRaises(PolicyError):load_project_policy(project)
 def test_actual_guarded_composition_keeps_all_schemas_without_fake_backends(self):
  self.prepare();project=self.prefix/'runtime/private_codex';runtime=project/'.runtime/main'
  from codex_host import DaemonPipelineProxy
  from laf_shared_tools import UnconfiguredRouterApplication
  from laf_stdio_adapter import StdioRouterAdapter
  opts={('allow_'+name.replace('-','_')):True for name in bootstrap.CAPABILITIES}
  adapter=StdioRouterAdapter(runtime,router_app=UnconfiguredRouterApplication(),runtime_project_root=project,
      pipeline=DaemonPipelineProxy(runtime),**opts)
  try:
   self.assertEqual(len(adapter.tools),33);self.assertEqual(len({t['name'] for t in adapter.tools}),33)
   from legacy_tool_adapter import tool_definitions
   from laf_mcp.server import TOOLS
   expected=tool_definitions(local_llm=True,local_models=True,chat_mailbox=True,codex_pipeline=True,experiments=True)
   for name,(description,properties,required) in TOOLS.items():
    write=name in ('laf_router_submit_job','laf_router_cancel_job')
    expected.append({'name':name,'description':description,'inputSchema':{'type':'object','properties':copy.deepcopy(properties),'required':list(required),'additionalProperties':False},
       'annotations':{'readOnlyHint':not write,'destructiveHint':name=='laf_router_cancel_job','idempotentHint':not write,'openWorldHint':False}})
   self.assertEqual(adapter.tools,expected)
   self.assertIsNone(adapter.mailbox);self.assertIsNone(adapter.grant);self.assertIsNone(adapter.helper_root)
   self.assertIsNone(adapter.experiments)
   for name,args in [('codex_models_list',{}),('orchestrator_status',{}),('experiment_list',{}),
      ('aceis_local_model_prepare',{'profile':'qwen-coder-30b'}),('laf_router_status',{}),
      ('chat_mailbox_register_request',{'task_id':'00000000-0000-4000-8000-000000000001','request_id':'00000000-0000-4000-8000-000000000002','request':'Synthetic non-secret question.','request_sha256':hashlib.sha256(b'Synthetic non-secret question.').hexdigest()})]:
    result=adapter.call_tool(name,args,1);self.assertTrue(result['isError'])
    data=result['structuredContent'];data=data.get('data',data)
    self.assertEqual(data['code'],'BACKEND_UNCONFIGURED');self.assertFalse(data['dispatch_started'])
  finally:self.assertTrue(adapter.close(timeout=0))
  self.assertFalse(any(p.suffix in ('.sqlite','.sqlite3') for p in runtime.iterdir()))
 def test_new_default_config_lookup_is_exclusive_and_points_outside_cache(self):
  self.prepare(sdk_python=Path(sys.executable))
  home=self.parent/'home';home.mkdir(mode=0o700)
  with patch.object(Path,'home',return_value=home):
   result=bootstrap.activate_config(self.prefix)
  target=home/'.config/laf/plugin.json'
  self.assertEqual(result['status'],'config_activated');self.assertEqual(target.read_bytes(),(self.prefix/'plugin.json').read_bytes())
  self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o600)
  spec=importlib.util.spec_from_file_location('bootstrap_launcher_test',ROOT/'launcher.py')
  launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
  value=launcher.read_config(target);args=launcher.arguments(value,ROOT)
  self.assertEqual(args[2],str(self.prefix/'runtime/integration/laf_stdio_cli.py'))
  self.assertIn('--laf-bootstrap-bindings',args)
  with patch.object(launcher.os,'execve') as execute,patch.object(Path,'home',return_value=home),patch('sys.stdout',io.StringIO()):
   self.assertEqual(launcher.main(['--check']),0)
  execute.assert_not_called()
  before=target.read_bytes()
  with patch.object(Path,'home',return_value=home),self.assertRaises(bootstrap.BootstrapError):bootstrap.activate_config(self.prefix)
  self.assertEqual(target.read_bytes(),before)
 def test_activation_dry_run_and_failure_do_not_change_existing_files(self):
  self.prepare(sdk_python=Path(sys.executable));target=self.parent/'config/new.json'
  result=bootstrap.activate_config(self.prefix,config=target,dry_run=True)
  self.assertEqual(result['status'],'planned');self.assertFalse(target.parent.exists())
  target.parent.mkdir(mode=0o700);target.write_bytes(b'existing');target.chmod(0o600)
  with self.assertRaises(bootstrap.BootstrapError):bootstrap.activate_config(self.prefix,config=target)
  self.assertEqual(target.read_bytes(),b'existing')
 def test_unconfigured_sdk_cannot_be_activated_as_working_default(self):
  self.prepare();target=self.parent/'config/new.json'
  with self.assertRaises(bootstrap.BootstrapError):bootstrap.activate_config(self.prefix,config=target)
  self.assertFalse(target.parent.exists())
 def test_activation_write_fault_removes_only_new_config_and_dirs(self):
  self.prepare(sdk_python=Path(sys.executable));target=self.parent/'new-config/child/plugin.json'
  sentinel=self.parent/'keep';sentinel.write_bytes(b'old')
  with patch.object(bootstrap.os,'fsync',side_effect=OSError('injected')):
   with self.assertRaises(OSError):bootstrap.activate_config(self.prefix,config=target)
  self.assertFalse((self.parent/'new-config').exists());self.assertEqual(sentinel.read_bytes(),b'old')
 def test_doctor_rejects_missing_marker_entrypoint_and_policy_tamper(self):
  self.prepare();runtime=self.prefix/'runtime/private_codex/.runtime/main'
  marker=runtime/'unconfigured-backends.json';raw=marker.read_bytes();marker.unlink()
  with self.assertRaises(bootstrap.BootstrapError):bootstrap.doctor(self.prefix)
  marker.write_bytes(raw);marker.chmod(0o600)
  entry=self.prefix/'launcher.py';raw=entry.read_bytes();entry.unlink()
  with self.assertRaises((OSError,bootstrap.BootstrapError)):bootstrap.doctor(self.prefix)
  entry.write_bytes(raw);entry.chmod(0o600)
  policy=self.prefix/'runtime/private_codex/policy-snapshot/docs/ai/policies.md';policy.write_bytes(policy.read_bytes()+b'changed')
  with self.assertRaises(ValueError):bootstrap.doctor(self.prefix)
 def test_legacy_policy_and_missing_backend_configuration_are_not_relaxed(self):
  self.prepare();project=self.prefix/'runtime/private_codex';runtime=project/'.runtime/main'
  from common_policy import _TITLE,load_project_policy
  # Synthetic legacy-parser fixture, not a replacement for the historical pin.
  body=(_TITLE+'1.0\nExact parser fixture.\n').encode();digest=hashlib.sha256(body).hexdigest()
  (project/'policies').mkdir(mode=0o700);(project/'policies.md').write_bytes(body)
  (project/'policies/manifest.json').write_text(json.dumps({'schema_version':1,'version':'1.0','file':'policies.md','sha256':digest}))
  (project/'config/upstream-policy.json').unlink()
  (project/'config/policy.json').write_text(json.dumps({'schema_version':1,'bundle_root':'.','expected_version':'1.0','expected_sha256':digest}))
  self.assertEqual(load_project_policy(project).text,body.decode())
  (runtime/'unconfigured-backends.json').unlink()
  from legacy_tool_adapter import Adapter
  with self.assertRaises(FileNotFoundError):Adapter(runtime,allow_local_llm=True,runtime_project_root=project)
  from chat_mailbox import Mailbox,MailboxError
  path=runtime/'chat-mailbox-policy.json';path.write_text(json.dumps({'schema':1,'allowed':[]}));path.chmod(0o600)
  with self.assertRaises(MailboxError):Mailbox.policy_snapshot(runtime)
 def test_snapshot_provenance_and_backend_marker_cannot_be_forged(self):
  self.prepare();project=self.prefix/'runtime/private_codex';runtime=project/'.runtime/main'
  from common_policy import load_project_policy,developer_instructions,PolicyError
  bundle=load_project_policy(project);meta=json.loads(bundle.provenance_json)
  meta['files']['agents/README.md']['sha256']='0'*64
  with self.assertRaises(PolicyError):developer_instructions(bundle._replace(provenance_json=json.dumps(meta)))
  from backend_state import load_unconfigured
  marker=runtime/'unconfigured-backends.json';marker.write_text('{"schema":1,"schema":1,"unconfigured":[]}')
  with self.assertRaises(ValueError):load_unconfigured(runtime)
  marker.write_text(json.dumps({'schema':1,'unconfigured':['unknown']}))
  with self.assertRaises(ValueError):load_unconfigured(runtime)
 def test_exact_emitted_copied_doctor_cli_works_outside_checkout_without_pythonpath(self):
  result=self.prepare();command=result['next_command']
  expected=['python3','-B',str(self.prefix/'runtime/integration/laf_bootstrap.py'),'doctor','--prefix',str(self.prefix)]
  self.assertEqual(command,expected)
  cwd=self.parent/'empty-cwd';cwd.mkdir(mode=0o700)
  # Explicit stdlib CLI child only. No SDK/native/daemon/socket/model is used.
  with patch('subprocess.Popen',REAL_POPEN):
   child=subprocess.run(command,cwd=cwd,env={'HOME':str(cwd),'PATH':'/usr/bin:/bin','LANG':'C.UTF-8',
      'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1'},stdin=subprocess.DEVNULL,capture_output=True,timeout=10)
  self.assertEqual(child.returncode,0,child.stderr.decode())
  observed=json.loads(child.stdout);self.assertEqual(observed['status'],'prepared')
  self.assertFalse(observed['services_started']);self.assertFalse(observed['services_probed'])
  self.assertEqual(observed['unconfigured_backends'],list(bootstrap.BACKENDS))
 def test_fault_cleanup_cannot_follow_replaced_prefix_into_existing_data(self):
  victim=self.parent/'existing-data';victim.mkdir(mode=0o700)
  (victim/'runtime').mkdir(mode=0o700);(victim/'runtime/integration').mkdir(mode=0o700)
  sentinel=victim/'keep';sentinel.write_bytes(b'preserve')
  before=tree_snapshot(victim)
  retained=self.parent/'moved-new-prefix';real_write=bootstrap.write_file;called=[]
  def replace_write_then_fail(*args):
   self.prefix.rename(retained);self.prefix.symlink_to(victim,target_is_directory=True)
   real_write(*args);called.append(True) # Exercise the actual write, not just a pre-write exception.
   raise OSError('injected after real write')
  with patch.object(bootstrap,'write_file',side_effect=replace_write_then_fail):
   with self.assertRaises(OSError):self.prepare()
  self.assertEqual(called,[True]);self.assertTrue(self.prefix.is_symlink())
  self.assertEqual(tree_snapshot(victim),before)
  self.assertEqual(list(retained.iterdir()),[])
 def test_root_removal_rechecks_identity_after_owned_cleanup(self):
  victim=self.parent/'existing-empty';victim.mkdir(mode=0o700);before=tree_snapshot(victim)
  retained=self.parent/'moved-new-prefix';real_cleanup=bootstrap.NewTree.cleanup
  def clean_then_replace(tree):
   real_cleanup(tree);self.prefix.rename(retained);victim.rename(self.prefix)
  with patch.object(bootstrap,'write_file',side_effect=OSError('injected write fault')):
   with patch.object(bootstrap.NewTree,'cleanup',clean_then_replace):
    with self.assertRaises(OSError):self.prepare()
  self.assertTrue(self.prefix.is_dir(),'Foreign replacement must survive owned cleanup')
  self.assertEqual(tree_snapshot(self.prefix),before);self.assertEqual(list(retained.iterdir()),[])
 def test_intermediate_directory_swap_cannot_redirect_real_write(self):
  victim=self.parent/'existing-data';victim.mkdir(mode=0o700);(victim/'keep').write_bytes(b'preserve')
  before=tree_snapshot(victim);retained=self.parent/'moved-integration'
  real_write=bootstrap.write_file
  def replace_then_write(*args):
   directory=self.prefix/'runtime/integration';directory.rename(retained);directory.symlink_to(victim,target_is_directory=True)
   real_write(*args)
   raise OSError('injected after redirected write attempt')
  with patch.object(bootstrap,'write_file',side_effect=replace_then_write):
   with self.assertRaises(OSError):self.prepare()
  self.assertEqual(tree_snapshot(victim),before)
  self.assertFalse(self.prefix.exists());self.assertEqual(list(retained.iterdir()),[])
 def test_foreign_real_directory_swap_is_not_adopted_as_new_tree(self):
  victim=self.parent/'existing-data';victim.mkdir(mode=0o700);(victim/'keep').write_bytes(b'preserve')
  before=tree_snapshot(victim);retained=self.parent/'moved-integration';real_write=bootstrap.write_file
  destination=self.prefix/'runtime/integration'
  def replace_then_write(*args):
   destination.rename(retained);victim.rename(destination)
   real_write(*args)
   raise OSError('injected after foreign directory write attempt')
  with patch.object(bootstrap,'write_file',side_effect=replace_then_write):
   with self.assertRaises((OSError,bootstrap.BootstrapError)):self.prepare()
  self.assertEqual(tree_snapshot(destination),before);self.assertEqual(list(retained.iterdir()),[])
 def test_root_swap_without_injected_error_is_not_reported_prepared(self):
  victim=self.parent/'existing-data';victim.mkdir(mode=0o700);(victim/'keep').write_bytes(b'preserve')
  before=tree_snapshot(victim);retained=self.parent/'moved-new-prefix'
  real_write=bootstrap.write_file;changed=[False]
  def write_then_replace(*args):
   real_write(*args)
   if not changed[0]:
    changed[0]=True;self.prefix.rename(retained);self.prefix.symlink_to(victim,target_is_directory=True)
  with patch.object(bootstrap,'write_file',side_effect=write_then_replace):
   with self.assertRaises((OSError,bootstrap.BootstrapError)):self.prepare()
  self.assertTrue(changed[0]);self.assertEqual(tree_snapshot(victim),before)
  self.assertEqual(list(retained.iterdir()),[])
 def test_prefix_swap_during_final_doctor_is_not_reported_prepared(self):
  victim=self.parent/'existing-data';victim.mkdir(mode=0o700);(victim/'keep').write_bytes(b'preserve')
  before=tree_snapshot(victim);retained=self.parent/'moved-new-prefix';real_doctor=bootstrap.doctor
  def check_then_replace(*args):
   result=real_doctor(*args);self.prefix.rename(retained);self.prefix.symlink_to(victim,target_is_directory=True)
   return result
  with patch.object(bootstrap,'doctor',side_effect=check_then_replace):
   with self.assertRaises((OSError,bootstrap.BootstrapError)):self.prepare()
  self.assertEqual(tree_snapshot(victim),before);self.assertEqual(list(retained.iterdir()),[])
 def test_activation_ancestor_swap_cannot_authorize_foreign_destination(self):
  self.prepare(sdk_python=Path(sys.executable))
  ancestor=self.parent/'activation-current';ancestor.mkdir(mode=0o700);(ancestor/'inner').mkdir(mode=0o700)
  victim=self.parent/'existing-config';victim.mkdir(mode=0o700);(victim/'inner').mkdir(mode=0o700);(victim/'inner/keep').write_bytes(b'preserve')
  before=tree_snapshot(victim)
  base=ancestor/'inner';retained=self.parent/'moved-config';real_open=bootstrap.os.open;changed=[False]
  def replace_on_open(path,flags,*args,**kwargs):
   if not changed[0] and flags & os.O_DIRECTORY and (str(path)==str(base) or str(path)==ancestor.name and kwargs.get('dir_fd') is not None):
    changed[0]=True;ancestor.rename(retained);ancestor.symlink_to(victim,target_is_directory=True)
   return real_open(path,flags,*args,**kwargs)
  with patch.object(bootstrap.os,'open',side_effect=replace_on_open):
   with self.assertRaises((OSError,bootstrap.BootstrapError)):bootstrap.activate_config(self.prefix,config=base/'plugin.json')
  self.assertTrue(changed[0]);self.assertEqual(tree_snapshot(victim),before)
 def test_activation_replacement_real_directory_is_not_adopted(self):
  self.prepare(sdk_python=Path(sys.executable))
  base=self.parent/'activation-current';base.mkdir(mode=0o700)
  victim=self.parent/'existing-config';victim.mkdir(mode=0o700);(victim/'keep').write_bytes(b'preserve')
  before=tree_snapshot(victim);retained=self.parent/'moved-config';real_open=bootstrap.os.open;changed=[False]
  def replace_on_open(path,flags,*args,**kwargs):
   if not changed[0] and flags & os.O_DIRECTORY and str(path)=='/':
    changed[0]=True;base.rename(retained);victim.rename(base)
   return real_open(path,flags,*args,**kwargs)
  with patch.object(bootstrap.os,'open',side_effect=replace_on_open):
   with self.assertRaises((OSError,bootstrap.BootstrapError)):bootstrap.activate_config(self.prefix,config=base/'plugin.json')
  self.assertTrue(changed[0]);self.assertEqual(tree_snapshot(base),before);self.assertEqual(list(retained.iterdir()),[])
 def test_activation_parent_swap_after_real_write_cleans_owned_file_only(self):
  self.prepare(sdk_python=Path(sys.executable))
  base=self.parent/'activation-current';base.mkdir(mode=0o700)
  victim=self.parent/'existing-config';victim.mkdir(mode=0o700);(victim/'keep').write_bytes(b'preserve')
  before=tree_snapshot(victim);retained=self.parent/'moved-config';real_fsync=bootstrap.os.fsync
  def sync_then_replace(fd):
   real_fsync(fd);base.rename(retained);base.symlink_to(victim,target_is_directory=True)
  with patch.object(bootstrap.os,'fsync',side_effect=sync_then_replace):
   with self.assertRaises((OSError,bootstrap.BootstrapError)):bootstrap.activate_config(self.prefix,config=base/'plugin.json')
  self.assertEqual(tree_snapshot(victim),before);self.assertEqual(list(retained.iterdir()),[])
 def test_cli_no_args_does_not_prepare_or_start(self):
  out=io.StringIO()
  with patch('sys.stderr',out):self.assertNotEqual(bootstrap.main([]),0)
  self.assertFalse(self.prefix.exists())

if __name__=='__main__':unittest.main(verbosity=2)
