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

# Added 2026-11-15: explicit owner-daemon binding, metadata-only portable profile.
"""Source/tempfile and fake daemon checks. No service, native, account or model."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime/integration'),str(ROOT/'runtime/private_codex/src'),
              str(ROOT/'runtime/public_laf/mcp/src')]
import laf_bootstrap as boot
from backend_state import BACKENDS,load_state,load_unconfigured,validate_state
REAL_POPEN=subprocess.Popen


class OrchestratorBinding(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path.home());self.addCleanup(self.temp.cleanup)
  self.parent=Path(self.temp.name);self.parent.chmod(0o700);self.prefix=self.parent/'new'
  self.sdk=self.parent/'sdk-python';self.sdk.write_text('# synthetic protected SDK reference; never executed\n');self.sdk.chmod(0o500)
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  for name in ('subprocess.Popen','socket.socket.bind','socket.socket.connect','threading.Thread.start'):
   self.stack.enter_context(patch(name,side_effect=AssertionError('No service/native/account/model in binding tests')))
  boot.prepare_prefix(self.prefix,sdk_python=self.sdk)
  self.project=self.prefix/'runtime/private_codex';self.runtime=self.project/'.runtime/main'
  self.marker=self.runtime/'unconfigured-backends.json'
 def configure(self,**kw):return boot.configure_orchestrator(self.prefix,**kw)
 def state(self):return load_state(self.runtime)[0]
 def adapter(self):
  from legacy_tool_adapter import Adapter
  class Proxy:
   def dispatch(*args):raise AssertionError('No Codex dispatch')
  value=Adapter(self.runtime,allow_local_llm=True,allow_local_models=True,allow_chat_mailbox=True,
      allow_mailbox_grants=True,allow_codex_pipeline=True,pipeline=Proxy(),allow_experiments=True,
      runtime_project_root=self.project)
  self.addCleanup(value.audit.close);return value
 def test_explicit_binding_is_atomic_metadata_not_a_live_probe(self):
  result=self.configure();value=self.state()
  self.assertEqual(value['orchestrator'],{'transport':'owner-control-socket'})
  self.assertNotIn('orchestrator',load_unconfigured(self.runtime))
  self.assertEqual(value['unconfigured'],[x for x in BACKENDS if x!='orchestrator'])
  self.assertFalse(result['services_started']);self.assertFalse(result['network_used'])
  self.assertFalse(result['execution_ready'])
  self.assertFalse((self.runtime/'tasks.sqlite').exists())
  self.assertFalse((self.runtime/'mcp-task-policy.json').exists())
 def test_dry_run_preserves_marker_and_anchor(self):
  before=self.marker.read_bytes();anchor=(self.prefix/'plugin.json').read_bytes()
  self.assertEqual(self.configure(dry_run=True)['status'],'planned')
  self.assertEqual(before,self.marker.read_bytes());self.assertEqual(anchor,(self.prefix/'plugin.json').read_bytes())
 def test_old_schema2_without_optional_field_is_unchanged(self):
  value={'schema':2,'unconfigured':list(BACKENDS),'router':None,'codex':None}
  self.marker.write_text(json.dumps(value))
  self.assertEqual(self.state(),value)
  self.assertIn('orchestrator',load_unconfigured(self.runtime))
  self.configure();self.assertNotIn('orchestrator',load_unconfigured(self.runtime))
 def test_binding_does_not_implicitly_enable_other_backends(self):
  self.configure();adapter=self.adapter()
  for name,args in [('codex_capabilities',{}),('experiment_list',{}),
                    ('aceis_local_model_status',{'profile':'qwen-coder-30b'})]:
   r=adapter.call_tool(name,args,1)
   self.assertTrue(r['isError']);self.assertEqual(r['structuredContent']['data']['code'],'BACKEND_UNCONFIGURED')
 def test_invalid_transport_and_extra_fields_refuse(self):
  self.configure();original=self.state()
  for binding in (True,{}, {'transport':'tcp'}, {'transport':'owner-control-socket','writes':True}):
   value=dict(original,orchestrator=binding)
   with self.subTest(binding=binding),self.assertRaises(ValueError):
    validate_state(value,self.runtime,len(json.dumps(value).encode()))
 def test_missing_binding_cannot_remove_the_denial(self):
  value={'schema':2,'unconfigured':[x for x in BACKENDS if x!='orchestrator'],'router':None,'codex':None}
  with self.assertRaises(ValueError):validate_state(value,self.runtime,len(json.dumps(value).encode()))
 def test_new_null_binding_cannot_enable_backend(self):
  value={'schema':2,'unconfigured':[x for x in BACKENDS if x!='orchestrator'],
         'router':None,'codex':None,'orchestrator':None}
  with self.assertRaises(ValueError):validate_state(value,self.runtime,len(json.dumps(value).encode()))
 def test_unknown_backend_field_refuses(self):
  value={'schema':2,'unconfigured':list(BACKENDS),'router':None,'codex':None,'mailbox':{}}
  with self.assertRaises(ValueError):validate_state(value,self.runtime,len(json.dumps(value).encode()))
 def test_rebinding_is_refused_without_mutation(self):
  self.configure();before=self.marker.read_bytes()
  with self.assertRaises(ValueError):self.configure()
  self.assertEqual(before,self.marker.read_bytes())
 def test_used_runtime_and_receipts_are_never_adopted(self):
  before=self.marker.read_bytes();owned=self.runtime/'runner.lock';owned.write_bytes(b'preserve')
  with self.assertRaises(ValueError):self.configure()
  self.assertEqual(before,self.marker.read_bytes());owned.unlink()
  owned=self.prefix/'receipts'/'retained.json';owned.write_bytes(b'preserve')
  with self.assertRaises(ValueError):self.configure()
  self.assertEqual(owned.read_bytes(),b'preserve');self.assertEqual(before,self.marker.read_bytes())
 def test_config_anchor_is_not_rewritten(self):
  active=self.parent/'active/plugin.json';boot.activate_config(self.prefix,config=active)
  before=active.read_bytes();self.configure()
  self.assertEqual(active.read_bytes(),before);self.assertEqual((self.prefix/'plugin.json').read_bytes(),before)
  self.assertEqual(boot.doctor(self.prefix)['bindings']['orchestrator'],'configured_not_probed')
 def test_ordinary_command_needs_no_codex_binding_or_native_probe(self):
  self.configure()
  with patch('codex_host.binary_snapshot',side_effect=AssertionError('No native probe')):
   value=boot.daemon_command(self.prefix)
  self.assertNotIn('--codex',value['command']);self.assertNotIn('--allow-codex-pipeline',value['command'])
  self.assertEqual(value['command'][-1],'daemon');self.assertIsNone(value['pipeline_concurrency'])
  self.assertFalse(value['services_started'])
 def test_codex_command_still_uses_its_explicit_native_and_opt_in(self):
  self.configure();native=self.parent/'native';native.write_bytes(b'\x7fELFsynthetic-never-executed');native.chmod(0o500)
  boot.configure_codex(self.prefix,binary=native,binary_sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
      workspace=self.prefix/'workspace',models=['opaque.model'])
  command=boot.daemon_command(self.prefix)['command']
  self.assertIn('--allow-codex-pipeline',command);self.assertEqual(command[command.index('--codex')+1],str(native))
 def test_metadata_rpcs_and_sanitization_are_exact(self):
  self.configure();adapter=self.adapter();observed=[];task='11111111-1111-4111-8111-111111111111'
  private={'id':task,'state':'CANCELLED','prompt':'PRIVATE_PROMPT','result':'PRIVATE_RESULT','owner':'PRIVATE_OWNER'}
  def request(root,method,params,**kw):
   observed.append((method,params))
   if method=='list':return [private]
   if params:return private
   return {'paused':False,'stopping':False,'maximum_sessions':8,'active_tasks':[],'task_counts':{}}
  with patch('legacy_tool_adapter.request',side_effect=request):
   status=adapter.call_tool('orchestrator_status',{},1)
   listed=adapter.call_tool('orchestrator_list_tasks',{'limit':2,'offset':3},2)
   got=adapter.call_tool('orchestrator_get_task',{'task_id':task},3)
  self.assertEqual(observed,[('status',{}),('list',{'limit':2,'offset':3}),('status',{'task_id':task})])
  self.assertTrue(all(not x['isError'] for x in (status,listed,got)))
  exported=json.dumps((status,listed,got))
  for text in ('PRIVATE_PROMPT','PRIVATE_RESULT','PRIVATE_OWNER'):self.assertNotIn(text,exported)
  self.assertEqual(got['structuredContent']['data']['task_id'],task)
 def test_portable_metadata_binding_does_not_create_task_write_authority(self):
  self.configure();adapter=self.adapter();names={x['name'] for x in adapter.tools}
  self.assertNotIn('orchestrator_submit_task',names);self.assertNotIn('orchestrator_cancel_task',names)
  from legacy_tool_adapter import Adapter
  with self.assertRaises(FileNotFoundError):
   Adapter(self.runtime,allow_writes=True,runtime_project_root=self.project)
 def test_changed_snapshot_refuses_existing_frontend(self):
  self.configure();adapter=self.adapter();self.marker.write_bytes(self.marker.read_bytes()+b'\n')
  with self.assertRaisesRegex(ValueError,'Backend binding changed'):adapter.call_tool('orchestrator_status',{},1)
 def test_failure_before_publication_preserves_old_record(self):
  before=self.marker.read_bytes()
  with patch.object(boot.os,'replace',side_effect=OSError('pre-publication fixture')):
   with self.assertRaises(OSError):self.configure()
  self.assertEqual(before,self.marker.read_bytes())
  self.assertEqual(sorted(p.name for p in self.runtime.iterdir()),['logs','unconfigured-backends.json'])
 def test_failure_after_publication_preserves_new_binding(self):
  real=boot.os.fsync
  def fail_directory(fd):
   if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('post-publication fixture')
   return real(fd)
  with patch.object(boot.os,'fsync',side_effect=fail_directory):
   with self.assertRaises(OSError):self.configure()
  self.assertNotIn('orchestrator',load_unconfigured(self.runtime));before=self.marker.read_bytes()
  with self.assertRaises(ValueError):self.configure()
  self.assertEqual(before,self.marker.read_bytes())
 def test_cli_has_explicit_action_and_rejects_unrelated_inputs(self):
  with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
   self.assertEqual(boot.main(['configure-orchestrator','--prefix',str(self.prefix),'--dry-run']),0)
   self.assertEqual(boot.main(['configure-orchestrator','--prefix',str(self.prefix),'--model','invented']),1)
  self.assertEqual(self.state()['schema'],1)
 def test_copied_cli_runs_in_a_fresh_stdlib_process_without_pythonpath(self):
  command=[sys.executable,'-B',str(self.prefix/'runtime/integration/laf_bootstrap.py'),
           'configure-orchestrator','--prefix',str(self.prefix)]
  env={'PATH':'/usr/bin:/bin','HOME':str(self.parent),'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
  before=self.marker.read_bytes()
  with patch.object(subprocess,'Popen',REAL_POPEN):
   planned=subprocess.run([*command,'--dry-run'],env=env,cwd=self.parent,
       capture_output=True,text=True,timeout=15)
  self.assertEqual(planned.returncode,0,planned.stderr)
  self.assertEqual(json.loads(planned.stdout)['status'],'planned')
  self.assertEqual(self.marker.read_bytes(),before)
  with patch.object(subprocess,'Popen',REAL_POPEN):
   configured=subprocess.run(command,env=env,cwd=self.parent,capture_output=True,text=True,timeout=15)
  self.assertEqual(configured.returncode,0,configured.stderr)
  self.assertEqual(json.loads(configured.stdout)['status'],'configured')
  self.assertNotIn('orchestrator',load_unconfigured(self.runtime))
  self.assertFalse((self.runtime/'tasks.sqlite').exists())


if __name__=='__main__':unittest.main(verbosity=2)
