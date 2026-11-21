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

# Added 2026-11-15: private scheduler policy selection without source drift.
"""Private fixtures and copied stdlib CLI only; no resource ledger or live peer."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime/integration'),str(ROOT/'runtime/private_codex/src')]
from scripts.source_test_isolation import require_isolated
require_isolated()  # Refuse raw resource tests before any fixture or project import.
import laf_bootstrap as boot
import scheduler
from backend_state import load_state,scheduler_binding_policy,state_sha256
import test_bootstrap_bindings as fixtures
import test_resource_only as resources
import test_notification_fresh_prefix as events
import test_bootstrap_production_policy as pipelines
import laf_acceptance as acceptance

class ResourcePolicyImport(unittest.TestCase):
 def setUp(self):
  f=fixtures.BindingTests('test_codex_first_binding_is_atomic_finite_and_does_not_execute');f.setUp();self.addCleanup(f.doCleanups)
  self.f=f;self.prefix=f.prefix;self.project=self.prefix/'runtime/private_codex';self.runtime=f.runtime;self.marker=f.marker
  self.config=self.project/'scheduler-config.json';self.packaged=self.config.read_bytes();self.source=f.parent/'private-scheduler.json'
  self.policy=json.loads(self.packaged);self.policy.pop('maximum_sessions')
  raw=resources.policy();raw['capacity']=resources.fixtures.v(10000,32*(1<<30),0,0);raw['reserve']=resources.fixtures.v(1000,2*(1<<30),0,0)
  raw['pipeline']=resources.fixtures.v(100,512*(1<<20),0,0);self.policy['resource_admission']=raw
  self.write(self.policy)
  guard=patch.object(scheduler,'CONFIG',self.config);guard.start();self.addCleanup(guard.stop)
  for name in ('resource_admission.Ledger','pipeline_resource_admission.Ledger','socket.socket.connect','socket.socket.bind','threading.Thread.start'):
   guard=patch(name,side_effect=AssertionError('No resource state, native, thread or peer during policy selection'));guard.start();self.addCleanup(guard.stop)
 def write(self,value=None,raw=None):
  self.source.write_bytes(raw if raw is not None else (json.dumps(value,indent=2)+'\n').encode());self.source.chmod(0o600)
  self.sha=hashlib.sha256(self.source.read_bytes()).hexdigest()
 def configure(self,**kw):return boot.configure_resource_policy(self.prefix,policy_file=self.source,policy_sha256=self.sha,**kw)
 def state(self):return load_state(self.runtime)[0]
 def refuse(self,operation):
  before=self.marker.read_bytes()
  with self.assertRaises((ValueError,OSError)):operation()
  self.assertEqual(before,self.marker.read_bytes());self.assertEqual(self.config.read_bytes(),self.packaged)
  self.assertEqual(sorted(p.name for p in self.runtime.iterdir()),['logs','unconfigured-backends.json'])
 def test_raw_bytes_and_provenance_are_retained_without_source_drift(self):
  raw=self.source.read_bytes();self.configure();v=self.state()['scheduler']
  self.assertEqual(v['raw'].encode(),raw);self.assertEqual(v['sha256'],self.sha);self.assertEqual(v['source']['sha256'],self.sha)
  self.assertEqual(v['source']['path'],str(self.source));self.assertEqual(self.config.read_bytes(),self.packaged)
  self.assertEqual(boot.doctor(self.prefix)['bindings']['scheduler'],'configured_not_probed')
 def test_import_alone_enables_none_of_the_seven_backends(self):
  self.configure();s=self.state();self.assertEqual(s['unconfigured'],list(boot.BACKENDS));self.assertIsNone(s['codex']);self.assertIsNone(s['router'])
  self.assertFalse(list(self.runtime.glob('*.sqlite3')));self.assertFalse(list((self.prefix/'receipts').iterdir()))
 def test_ordinary_pipeline_ordinary_preserve_raw_and_effective_limits(self):
  before=copy.deepcopy(self.policy);raw=self.source.read_bytes();self.configure()
  ordinary=scheduler.configuration();pipeline=scheduler.configuration(codex_pipeline=True);again=scheduler.configuration()
  self.assertEqual(ordinary,again);self.assertIsNone(ordinary['maximum_sessions']);self.assertEqual(ordinary['admission_mode'] if 'admission_mode' in ordinary else ordinary['resource_admission']['admission_mode'],'resources')
  self.assertEqual(ordinary['daemon']['memory_bytes'],before['daemon']['memory_bytes']);self.assertEqual(pipeline['daemon']['memory_bytes'],before['pipeline_daemon_memory_bytes'])
  self.assertEqual(self.source.read_bytes(),raw);self.assertEqual(scheduler_binding_policy(self.state()['scheduler']),before)
 def test_validator_does_not_mutate_its_input(self):
  raw=copy.deepcopy(self.policy);before=copy.deepcopy(raw)
  for mode in (False,True,False):scheduler.validate_configuration(raw,codex_pipeline=mode)
  self.assertEqual(raw,before)
 def test_legacy_generation_shape_remains_byte_compatible(self):
  # Observed from the immutable c2ab056 scheduler with unchanged packaged inputs.
  for mode,expected in ((False,'b62b907c5b5a02418eaa04e32f49ae0c543b1724972d4b9e34500a679838bf3b'),(True,'5a7ae23a509d12e92f166d66b5574a2624709dde6353c961fd138a12c8c9a03e')):
   self.assertEqual(scheduler.configuration(codex_pipeline=mode)['pool_generation'],expected)
  v=scheduler.configuration()
  self.assertNotIn('bootstrap_bindings_sha256',v);self.assertEqual(v['maximum_sessions'],8)
 def test_source_deletion_after_import_does_not_reopen_private_path(self):
  self.configure();first=scheduler.configuration();self.source.unlink();self.assertEqual(first,scheduler.configuration())
 def test_source_edit_after_import_does_not_replace_selected_bytes(self):
  self.configure();first=scheduler.configuration();self.write(raw=b'not the retained snapshot');self.assertEqual(first,scheduler.configuration())
 def test_dry_run_has_no_binding_or_source_side_effect(self):
  before=self.marker.read_bytes();self.assertEqual(self.configure(dry_run=True)['status'],'planned');self.assertEqual(before,self.marker.read_bytes())
 def test_rebind_refuses_unchanged_even_when_raw_policy_matches(self):
  self.configure();before=self.marker.read_bytes()
  with self.assertRaises(ValueError):self.configure()
  self.assertEqual(before,self.marker.read_bytes())
 def test_existing_database_and_receipts_refuse_before_reading_them(self):
  for parent,name in ((self.runtime,'unknown.sqlite3'),(self.prefix/'receipts','unknown.json')):
   q=parent/name;q.write_bytes(b'UNKNOWN')
   with patch('sqlite3.connect',side_effect=AssertionError('Never open existing DB')):
    before=self.marker.read_bytes()
    with self.assertRaises(ValueError):self.configure()
    self.assertEqual(before,self.marker.read_bytes());self.assertEqual(q.read_bytes(),b'UNKNOWN')
   q.unlink()
 def test_wrong_hash_is_refused(self):
  self.sha='0'*64;self.refuse(self.configure)
 def test_unsafe_source_mode_and_link_are_refused(self):
  self.source.chmod(0o640);self.refuse(self.configure);self.source.chmod(0o600)
  target=self.f.parent/'linked-policy';self.source.rename(target);self.source.symlink_to(target);self.refuse(self.configure)
 def test_hard_link_and_fifo_are_refused_without_blocking(self):
  other=self.f.parent/'hard-link';os.link(self.source,other);self.refuse(self.configure);other.unlink()
  self.source.unlink();os.mkfifo(self.source,0o600);self.refuse(self.configure)
 def test_duplicate_nonfinite_and_malformed_documents_refuse(self):
  raw=json.dumps(self.policy)
  for bad in (b'{',b'NaN',b'\xff',raw.replace('"poll_seconds": 0.2','"poll_seconds": 0.2, "poll_seconds": 0.2').encode(),raw.replace('"poll_seconds": 0.2','"poll_seconds": NaN').encode()):
   with self.subTest(raw=bad[:20]):self.write(raw=bad);self.refuse(self.configure)
 def test_explicit_slot_limit_schema1_and_unknown_fields_refuse(self):
  for kind in ('slot','schema1','unknown'):
   value=copy.deepcopy(self.policy)
   if kind=='slot':value['maximum_sessions']=1
   elif kind=='schema1':value['resource_admission'].update(schema=1);value['resource_admission'].pop('admission_mode');value['resource_admission'].pop('pipeline');value['resource_admission']['providers']['chatgpt'].pop('account_sha256')
   else:value['new_unknown_setting']=True
   self.write(value);self.refuse(self.configure)
 def test_ordinary_only_snapshot_needs_no_pipeline_or_native(self):
  v=copy.deepcopy(self.policy);v['resource_admission'].pop('pipeline')
  for key in ('pipeline_daemon_memory_bytes','pipeline_daemon_pids','pipeline_daemon_file_bytes'):v.pop(key)
  self.write(v);self.configure();self.assertIsNone(scheduler.configuration()['maximum_sessions'])
  with self.assertRaises(ValueError):scheduler.configuration(codex_pipeline=True)
 def test_cli_rejects_partial_and_unrelated_inputs(self):
  from contextlib import redirect_stdout,redirect_stderr
  import io
  base=['configure-resource-policy','--prefix',str(self.prefix)]
  with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
   for args in ([],['--policy-file',str(self.source)],['--policy-sha256',self.sha],['--policy-file',str(self.source),'--policy-sha256',self.sha,'--native',str(self.f.native)]):self.assertEqual(boot.main(base+args),1)
   self.assertEqual(boot.main(base+['--policy-file',str(self.source),'--policy-sha256',self.sha,'--dry-run']),0)
 def test_copied_cli_selects_the_private_policy_without_pythonpath(self):
  cmd=[sys.executable,'-B',str(self.prefix/'runtime/integration/laf_bootstrap.py'),'configure-resource-policy','--prefix',str(self.prefix),'--policy-file',str(self.source),'--policy-sha256',self.sha]
  env={'PATH':'/usr/bin:/bin','HOME':str(self.f.parent),'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
  with patch.object(subprocess,'Popen',fixtures.REAL_POPEN):p=subprocess.run(cmd,cwd=self.f.parent,env=env,capture_output=True,text=True,timeout=20)
  self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads(p.stdout)['status'],'configured');self.assertIsNone(scheduler.configuration()['maximum_sessions'])
 def test_missing_marker_refuses_instead_of_legacy_fallback(self):
  self.configure();self.marker.unlink()
  with self.assertRaisesRegex(ValueError,'binding is missing'):scheduler.configuration()
 def test_fresh_copied_process_and_live_generation_both_refuse_marker_loss(self):
  self.configure();settings=scheduler.configuration();self.marker.unlink()
  with self.assertRaises(scheduler.PoolPolicyError):scheduler.require_pool_generation(settings)
  code="import sys;sys.path.insert(0,sys.argv[1]);import scheduler;scheduler.configuration()"
  env={'PATH':'/usr/bin:/bin','HOME':str(self.f.parent),'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
  with patch.object(subprocess,'Popen',fixtures.REAL_POPEN):p=subprocess.run([sys.executable,'-B','-c',code,str(self.project/'src')],env=env,cwd=self.f.parent,capture_output=True,text=True,timeout=20)
  self.assertNotEqual(p.returncode,0);self.assertIn('Bootstrap scheduler binding is missing',p.stderr)
 def test_malformed_symlink_and_fifo_markers_refuse(self):
  self.configure();before=self.marker.read_bytes();target=self.f.parent/'saved-marker';target.write_bytes(before);target.chmod(0o600)
  for kind in ('malformed','symlink','fifo'):
   if self.marker.exists() or self.marker.is_symlink():self.marker.unlink()
   if kind=='malformed':self.marker.write_bytes(b'{');self.marker.chmod(0o600)
   elif kind=='symlink':self.marker.symlink_to(target)
   else:os.mkfifo(self.marker,0o600)
   with self.assertRaises((ValueError,OSError)):scheduler.configuration()
 def test_binding_mutations_fence_live_generation_and_classify_legacy_compatibility(self):
  self.configure();selected=scheduler.configuration();original=self.marker.read_bytes()
  for kind in ('whitespace','provenance','removed','null'):
   self.marker.write_bytes(original)
   if kind=='whitespace':self.marker.write_bytes(original+b' ')
   else:
    value=json.loads(original)
    if kind=='provenance':value['scheduler']['source']['directory_identity'][1]+=1
    elif kind=='removed':value.pop('scheduler')
    else:value['scheduler']=None
    self.marker.write_text(json.dumps(value))
   with self.assertRaises(scheduler.PoolPolicyError):scheduler.require_pool_generation(selected)
   if kind in ('removed','null'):
    # An older valid unselected marker stays compatible in a fresh process.
    # This is not proof against unsupported manual marker rollback.
    self.assertEqual(scheduler.configuration()['maximum_sessions'],8)
 def test_worker_must_use_the_selected_generation_and_pipeline_mode(self):
  self.configure();v=scheduler.configuration(codex_pipeline=True)
  with patch.dict(os.environ,{'LAF_SESSION_POOL_CODEX_PIPELINE':'1','LAF_SESSION_POOL_GENERATION':v['pool_generation']}):self.assertEqual(scheduler.worker_configuration(),v)
  with patch.dict(os.environ,{'LAF_SESSION_POOL_CODEX_PIPELINE':'1','LAF_SESSION_POOL_GENERATION':'0'*64}):
   with self.assertRaises(scheduler.PoolPolicyError):scheduler.worker_configuration()
 def test_tampered_raw_hash_and_provenance_cannot_select_a_policy(self):
  self.configure();original=self.marker.read_bytes()
  for kind in ('raw','hash','identity'):
   v=json.loads(original)
   if kind=='raw':v['scheduler']['raw']+=' '
   elif kind=='hash':v['scheduler']['sha256']='0'*64
   else:v['scheduler']['source']['identity'][2]+=1
   self.marker.write_text(json.dumps(v))
   with self.assertRaises(ValueError):scheduler.configuration()
 def test_source_change_before_publication_preserves_original_marker(self):
  original=boot.read_operator_document;calls=0
  def changing(*args):
   nonlocal calls
   result=original(*args);calls+=1
   if calls==1:self.source.write_bytes(self.source.read_bytes()+b' ')
   return result
  with patch.object(boot,'read_operator_document',side_effect=changing):self.refuse(self.configure)
 def test_marker_size_bound_is_independent_of_document_bound(self):
  # Valid harmless trailing whitespace fits source bound but exceeds encoded marker.
  raw=json.dumps(self.policy).encode();self.write(raw=raw+b' '*(16384-len(raw)))
  self.refuse(self.configure)
 def test_selected_policy_reaches_real_gateway_and_same_events_runtime(self):
  policy=pipelines.ProductionPolicyImport('test_exact_schema2_values_and_raw_provenance_are_snapshotted');policy.setUp();self.addCleanup(policy.doCleanups);policy.configure()
  notification=events.FreshPrefixEvents('test_missing_first_ledger_activates_same_runtime_after_real_pipeline_creation');notification.setUp();self.addCleanup(notification.doCleanups)
  notification.f,notification.runtime=policy.f,policy.runtime
  with patch.object(scheduler,'CONFIG',policy.project/'scheduler-config.json'):
   boot.configure_resource_policy(policy.prefix,policy_file=self.source,policy_sha256=self.sha)
   command=boot.daemon_command(policy.prefix)
   self.assertEqual(command['scheduler_admission_mode'],'resources');self.assertIsNone(command['pipeline_concurrency']);self.assertFalse(command['services_started'])
   runtime=notification.components();gateway=notification.gateway(runtime,resource_controller=lambda:('fixture-owner','fixture-token'))
   from pipeline_resource_admission import PipelineAdmission
   self.assertIsInstance(gateway.resource_gate,PipelineAdmission);self.assertIs(gateway.pipeline.resource_gate,gateway.resource_gate)
   self.assertIsNone(gateway.resource_settings['maximum_sessions']);self.assertEqual(gateway.resource_settings,scheduler.configuration(codex_pipeline=True))
   self.assertTrue(runtime.enabled);self.assertIs(gateway.notification_worker_factory.__self__,runtime);self.assertEqual(notification.effects,[])
 def test_private_selection_preserves_copied_source_verification_and_drift_refusal(self):
  self.configure();config=json.loads((self.prefix/'plugin.json').read_bytes())
  self.assertEqual(acceptance.verify_runtime(ROOT,config),self.prefix/'runtime')
  self.config.write_bytes(self.config.read_bytes()+b' ')
  with self.assertRaisesRegex(acceptance.Refused,'copied_runtime_drift'):acceptance.verify_runtime(ROOT,config)

if __name__=='__main__':unittest.main(verbosity=2)
