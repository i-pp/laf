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

# Added 2026-11-13: original operator-binding and protected-copy regressions.

"""Offline operator-binding and native-snapshot boundaries; never run a native."""
from contextlib import ExitStack
import copy,hashlib,importlib.util,json,os,stat,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime/integration'),str(ROOT/'runtime/private_codex/src'),str(ROOT/'runtime/public_laf/mcp/src')]
import laf_bootstrap as boot
from backend_state import load_unconfigured
REAL_POPEN=subprocess.Popen

class BindingTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path.home());self.addCleanup(self.temp.cleanup)
  self.parent=Path(self.temp.name);self.parent.chmod(0o700);self.prefix=self.parent/'new'
  self.sdk=self.parent/'sdk-python';self.sdk.write_text('# synthetic protected SDK reference; never executed\n');self.sdk.chmod(0o500)
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  for name in ('subprocess.Popen','socket.socket.bind','socket.socket.connect','threading.Thread.start'):
   self.stack.enter_context(patch(name,side_effect=AssertionError('No executable, service, network or thread in binding fixtures')))
  boot.prepare_prefix(self.prefix,sdk_python=self.sdk)
  self.runtime=self.prefix/'runtime/private_codex/.runtime/main';self.marker=self.runtime/'unconfigured-backends.json'
  self.native=self.parent/'native';self.native.write_bytes(b'\x7fELFsynthetic-not-executable-test-data');self.native.chmod(0o500)
  self.digest=hashlib.sha256(self.native.read_bytes()).hexdigest()
  self.socket_dir=self.parent/'router';self.socket_dir.mkdir(mode=0o700);self.socket=self.socket_dir/'service.sock'
  self.socket.write_bytes(b'not-a-real-socket');self.socket.chmod(0o600)
 def codex(self,**kw):
  return boot.configure_codex(self.prefix,binary=self.native,binary_sha256=self.digest,
      workspace=self.prefix/'workspace',models=['opaque.Sol:raw'],**kw)
 def router(self,**kw):
  # Real Router metadata validator, but a regular synthetic file projects S_IFSOCK.
  # No actual bind/connect/SDK child is permitted or needed for source validation.
  real=os.lstat;target=self.socket
  def projected(path,*args,**kwargs):
   value=real(path,*args,**kwargs)
   if Path(path)==target:
    values=list(value);values[0]=stat.S_IFSOCK|stat.S_IMODE(value.st_mode);return os.stat_result(values)
   return value
  with patch('os.lstat',side_effect=projected):
   return boot.configure_router(self.prefix,socket_path=self.socket,models=['raw-router'],**kw)
 def state(self):return json.loads(self.marker.read_bytes())
 def test_codex_first_binding_is_atomic_finite_and_does_not_execute(self):
  old=(self.prefix/'plugin.json').read_bytes();result=self.codex()
  self.assertEqual(result['status'],'configured');value=self.state()
  self.assertEqual(value['schema'],2);self.assertNotIn('codex_pipeline',load_unconfigured(self.runtime))
  self.assertEqual(value['codex']['binary'],str(self.native));self.assertEqual(value['codex']['binary_sha256'],self.digest)
  self.assertEqual(value['codex']['policy'],{'schema':1,'workspaces':{'default':str(self.prefix/'workspace')},'models':['opaque.Sol:raw'],'max_threads':4,'max_tasks':16,'concurrency':1,'timeout_seconds':120})
  self.assertFalse(result['native_version_verified']);self.assertFalse(result['account_verified']);self.assertFalse(result['services_started'])
  self.assertEqual(old,(self.prefix/'plugin.json').read_bytes())
  self.assertFalse((self.runtime/'codex-pipeline-executor.json').exists());self.assertFalse((self.runtime/'codex-pipeline-policy.json').exists())
  self.assertFalse(any(p.suffix in ('.sqlite','.sqlite3') for p in self.runtime.iterdir()))
 def test_router_binding_is_metadata_only_with_exact_owner_identity(self):
  result=self.router();value=self.state();self.assertEqual(result['status'],'configured')
  self.assertEqual(value['router']['socket'],str(self.socket));self.assertEqual(value['router']['models'],['raw-router'])
  self.assertEqual(len(value['router']['socket_identity']),6);self.assertNotIn('router',load_unconfigured(self.runtime))
  self.assertIsNone(value['codex']);self.assertFalse(list((self.prefix/'receipts').iterdir()))
 def test_configure_dry_runs_preserve_exact_source_and_marker(self):
  before=self.marker.read_bytes();self.assertEqual(self.codex(dry_run=True)['status'],'planned')
  self.assertEqual(self.router(dry_run=True)['status'],'planned');self.assertEqual(before,self.marker.read_bytes())
 def test_existing_binding_and_rebind_refuse_without_rewriting(self):
  self.codex();before=self.marker.read_bytes()
  with self.assertRaises(ValueError):self.codex()
  self.assertEqual(before,self.marker.read_bytes())
 def test_unknown_database_and_receipts_are_not_reused_or_opened(self):
  before=self.marker.read_bytes();db=self.runtime/'codex-pipeline.sqlite3';db.write_bytes(b'UNKNOWN - must not open');db.chmod(0o600)
  with patch('sqlite3.connect',side_effect=AssertionError('Configuration cannot open a DB')):
   with self.assertRaises(ValueError):self.codex()
  self.assertEqual(db.read_bytes(),b'UNKNOWN - must not open');self.assertEqual(before,self.marker.read_bytes());db.unlink()
  receipt=self.prefix/'receipts'/'foreign.json';receipt.write_bytes(b'UNKNOWN');receipt.chmod(0o600)
  with self.assertRaises(ValueError):self.router()
  self.assertEqual(receipt.read_bytes(),b'UNKNOWN');self.assertEqual(before,self.marker.read_bytes())
 def test_unsafe_native_wrong_hash_and_foreign_workspace_refuse(self):
  before=self.marker.read_bytes();self.native.chmod(0o775)
  with self.assertRaises(ValueError):self.codex()
  self.assertEqual(stat.S_IMODE(self.native.stat().st_mode),0o775);self.native.chmod(0o500)
  with patch.object(boot,'NATIVE_LIMIT',4):
   with self.assertRaises(ValueError):self.codex()
  with self.assertRaises(ValueError):boot.configure_codex(self.prefix,binary=self.native,binary_sha256='0'*64,workspace=self.prefix/'workspace',models=['opaque.Sol:raw'])
  with self.assertRaises(ValueError):boot.configure_codex(self.prefix,binary=self.native,binary_sha256=self.digest,workspace=self.parent,models=['opaque.Sol:raw'])
  self.assertEqual(before,self.marker.read_bytes())
 def test_failure_before_publish_preserves_record_and_removes_own_temporary(self):
  before=self.marker.read_bytes()
  with patch.object(boot.os,'replace',side_effect=OSError('injected pre-publish fault')):
   with self.assertRaises(OSError):self.codex()
  self.assertEqual(before,self.marker.read_bytes());self.assertEqual(sorted(p.name for p in self.runtime.iterdir()),['logs','unconfigured-backends.json'])
 def test_after_publish_fsync_failure_does_not_rollback_or_retry(self):
  real=boot.os.fsync
  def fail_directory(fd):
   if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('injected after publish')
   return real(fd)
  with patch.object(boot.os,'fsync',side_effect=fail_directory):
   with self.assertRaises(OSError):self.codex()
  self.assertEqual(self.state()['schema'],2);self.assertIsNotNone(self.state()['codex'])
  before=self.marker.read_bytes()
  with self.assertRaises(ValueError):self.codex()
  self.assertEqual(before,self.marker.read_bytes())
 def test_workspace_scope_is_rechecked_immediately_before_publication(self):
  before=self.marker.read_bytes();victim=self.parent/'existing-work';victim.mkdir(mode=0o700);(victim/'keep').write_bytes(b'preserve')
  moved=self.parent/'moved-work';real=boot.os.fsync;changed=[False]
  def change_after_temp_is_durable(fd):
   real(fd)
   if not changed[0] and stat.S_ISREG(os.fstat(fd).st_mode):
    changed[0]=True;(self.prefix/'workspace').rename(moved);(self.prefix/'workspace').symlink_to(victim,target_is_directory=True)
  with patch.object(boot.os,'fsync',side_effect=change_after_temp_is_durable):
   with self.assertRaises((OSError,ValueError)):self.codex()
  self.assertTrue(changed[0]);self.assertEqual(self.marker.read_bytes(),before)
  self.assertEqual([(p.name,p.read_bytes()) for p in victim.iterdir()],[('keep',b'preserve')])
 def test_prefix_replacement_during_publish_never_writes_foreign_tree(self):
  victim=self.parent/'existing';victim.mkdir(mode=0o700);(victim/'keep').write_bytes(b'preserve')
  moved=self.parent/'moved';real=boot.os.replace
  def replace_then_publish(*args,**kwargs):
   self.prefix.rename(moved);self.prefix.symlink_to(victim,target_is_directory=True);return real(*args,**kwargs)
  with patch.object(boot.os,'replace',side_effect=replace_then_publish):
   with self.assertRaises((OSError,ValueError)):self.codex()
  self.assertEqual([(p.name,p.read_bytes()) for p in victim.iterdir()],[('keep',b'preserve')])
  self.assertEqual(json.loads((moved/'runtime/private_codex/.runtime/main/unconfigured-backends.json').read_bytes())['schema'],2)
 def test_copied_active_config_is_an_immutable_anchor_not_stale_model_json(self):
  target=self.parent/'active/plugin.json';boot.activate_config(self.prefix,config=target);before=target.read_bytes()
  self.router();self.codex();self.assertEqual(before,target.read_bytes());self.assertEqual(before,(self.prefix/'plugin.json').read_bytes())
  spec=importlib.util.spec_from_file_location('binding_launcher',ROOT/'launcher.py');launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
  command=launcher.arguments(launcher.read_config(target),ROOT);self.assertIn('--laf-bootstrap-bindings',command)
  import laf_stdio_cli as cli
  parts=cli._components();observed=[]
  def serve(adapter):
   observed.append((copy.deepcopy(adapter.tools),adapter.shared.app.allowed,adapter.unconfigured));self.assertTrue(adapter.close(timeout=0));return 0
  parts.serve=serve
  with patch.object(cli,'_components',return_value=parts),patch.object(cli.importlib.metadata,'version',return_value='1.30.0'):
   self.assertEqual(cli.main(command[3:]),0)
  from legacy_tool_adapter import tool_definitions
  from laf_mcp.server import TOOLS
  expected=tool_definitions(local_llm=True,local_models=True,chat_mailbox=True,codex_pipeline=True,experiments=True)
  for name,(description,properties,required) in TOOLS.items():
   write=name in ('laf_router_submit_job','laf_router_cancel_job')
   expected.append({'name':name,'description':description,'inputSchema':{'type':'object','properties':copy.deepcopy(properties),'required':list(required),'additionalProperties':False},
       'annotations':{'readOnlyHint':not write,'destructiveHint':name=='laf_router_cancel_job','idempotentHint':not write,'openWorldHint':False}})
  self.assertEqual(observed[0][0],expected);self.assertEqual(len(expected),33);self.assertEqual(observed[0][1],frozenset(['raw-router']))
  self.assertEqual(observed[0][2],frozenset(set(boot.BACKENDS)-{'router','codex_pipeline'}))

 def test_actual_host_gateway_uses_one_binding_without_legacy_shadow_files(self):
  self.codex()
  from codex_host import HostGateway
  from test_codex_catalog_effort import Peer,FakeOwned
  peer=Peer(str(self.prefix/'workspace'));peer.models[0].update(id='opaque.Sol:raw',model='opaque.Sol:raw')
  transports=[]
  def factory(binary):
   self.assertEqual(binary,str(self.native));value=FakeOwned(peer);transports.append(value);return value
  with patch.object(HostGateway,'require_host'):
   gateway=HostGateway(self.runtime,str(self.native),self.prefix/'runtime/private_codex',lambda:False,transport_factory=factory)
   try:
    self.assertEqual(gateway.pipeline.policy,self.state()['codex']['policy'])
    from backend_state import state_sha256
    self.assertEqual(gateway.snapshot['bootstrap_bindings_sha256'],state_sha256(self.marker.read_bytes()))
    result=gateway.dispatch('codex_models_list',{})
    self.assertEqual(result['models'][0]['model'],'opaque.Sol:raw');self.assertEqual(result['models'][0]['default_reasoning_effort'],'medium')
    self.assertEqual(peer.count('thread/start'),0);self.assertEqual(peer.count('turn/start'),0)
    self.assertTrue(transports and all(x.closed for x in transports));self.assertFalse(gateway.held)
   finally:gateway.close()
  self.assertFalse((self.runtime/'codex-pipeline-executor.json').exists());self.assertFalse((self.runtime/'codex-pipeline-policy.json').exists())
 def test_host_gateway_snapshot_change_blocks_before_any_transport(self):
  self.codex()
  from codex_host import HostGateway
  with patch.object(HostGateway,'require_host'):
   gateway=HostGateway(self.runtime,str(self.native),self.prefix/'runtime/private_codex',lambda:False,
       transport_factory=lambda _:self.fail('No transport after binding drift'))
   try:
    self.marker.write_bytes(self.marker.read_bytes()+b'\n')
    with self.assertRaisesRegex(ValueError,'EXECUTOR_POLICY_CHANGED'):gateway.connect()
   finally:gateway.close()
 def test_unpinned_builder_cannot_launch_a_bootstrap_binding(self):
  self.codex()
  from codex_pipeline import build_pipeline
  with self.assertRaisesRegex(ValueError,'BOOTSTRAP_REQUIRES_PINNED_HOST_GATEWAY'):
   build_pipeline(self.runtime,self.prefix/'runtime/private_codex',transport_factory=lambda:self.fail('No alternate executor'))
 def test_legacy_frontend_snapshot_change_cannot_enable_a_backend_mid_session(self):
  self.codex()
  from legacy_tool_adapter import Adapter
  class Proxy:
   def dispatch(*args):raise AssertionError('Binding drift must stop before proxy')
  adapter=Adapter(self.runtime,allow_codex_pipeline=True,pipeline=Proxy(),runtime_project_root=self.prefix/'runtime/private_codex')
  try:
   self.marker.write_bytes(self.marker.read_bytes()+b'\n')
   with self.assertRaisesRegex(ValueError,'Backend binding changed'):adapter.call_tool('codex_models_list',{},1)
  finally:adapter.audit.close()
 def test_router_fs_pin_is_checked_before_connect_and_existing_guard_is_retained(self):
  self.router();value=self.state()['router']
  from laf_shared_tools import build_router_application
  from backend_state import state_sha256
  from laf_mcp.router import Router
  app=build_router_application(socket_path=value['socket'],state_directory=value['receipts'],models=value['models'],
       socket_identity=value['socket_identity'],binding_runtime=self.runtime,binding_sha256=state_sha256(self.marker.read_bytes()))
  try:
   with patch.object(Router,'_check_identity',return_value=tuple(value['socket_identity'])):
    self.assertEqual(list(app.router._check_identity()),value['socket_identity'])
   other=list(value['socket_identity']);other[1]+=1
   with patch.object(Router,'_check_identity',return_value=tuple(other)):
    with self.assertRaisesRegex(ValueError,'Router endpoint changed'):app.router._check_identity()
  finally:app.receipts.close()
 def test_schema2_tamper_is_not_legacy_fallback(self):
  self.codex();value=self.state();raw=self.marker.read_bytes()
  for mutate in (lambda v:v.update(unconfigured=[]),lambda v:v['codex']['policy'].update(concurrency=2),lambda v:v.update(unknown=True)):
   bad=json.loads(raw);mutate(bad);self.marker.write_text(json.dumps(bad))
   with self.assertRaises(ValueError):load_unconfigured(self.runtime)
  self.marker.write_bytes(raw)
  self.marker.write_bytes(b'{"schema":2,"schema":1,"unconfigured":[]}')
  with self.assertRaises(ValueError):load_unconfigured(self.runtime)
 def test_record_and_native_fifos_are_rejected_without_a_blocking_open(self):
  self.marker.unlink();os.mkfifo(self.marker,0o600)
  with self.assertRaises(ValueError):load_unconfigured(self.runtime)
  self.marker.unlink();self.marker.write_text(json.dumps({'schema':1,'unconfigured':list(boot.BACKENDS)}));self.marker.chmod(0o600)
  fifo=self.parent/'native-fifo';os.mkfifo(fifo,0o600)
  with self.assertRaises(ValueError):boot.snapshot_codex(self.prefix,source=fifo,expected_sha256=self.digest)
 def test_command_emission_is_explicit_and_does_not_start_the_guarded_daemon(self):
  self.codex();result=boot.daemon_command(self.prefix)
  self.assertEqual(result['status'],'command_only');self.assertFalse(result['services_started'])
  self.assertEqual(result['command'],[str(self.sdk),'-B',str(self.prefix/'runtime/private_codex/src/runner.py'),
       '--runtime',str(self.runtime),'--codex',str(self.native),'--allow-codex-pipeline','daemon'])
  self.assertEqual(result['pipeline_concurrency'],1);self.assertTrue(result['ordinary_maximum_sessions_unchanged'])

 def test_exact_copied_configure_cli_runs_outside_checkout_without_pythonpath(self):
  cwd=self.parent/'empty';cwd.mkdir(mode=0o700)
  command=[sys.executable,'-B',str(self.prefix/'runtime/integration/laf_bootstrap.py'),'configure-codex','--prefix',str(self.prefix),
       '--native',str(self.native),'--sha256',self.digest,'--workspace',str(self.prefix/'workspace'),'--model','opaque.Sol:raw']
  # A bounded stdlib configuration CLI, not SDK/Codex/native execution.
  with patch('subprocess.Popen',REAL_POPEN):
   result=subprocess.run(command,cwd=cwd,env={'HOME':str(Path.home()),'PATH':'/usr/bin:/bin','LANG':'C.UTF-8',
       'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1'},stdin=subprocess.DEVNULL,capture_output=True,timeout=10)
  self.assertEqual(result.returncode,0,result.stderr.decode());self.assertFalse(result.stderr)
  self.assertEqual(json.loads(result.stdout)['status'],'configured');self.assertEqual(self.state()['codex']['policy']['models'],['opaque.Sol:raw'])
 def test_router_unsafe_socket_and_parent_modes_preserve_original_record(self):
  before=self.marker.read_bytes()
  self.socket.chmod(0o660)
  with self.assertRaises(ValueError):self.router()
  self.socket.chmod(0o600);self.socket_dir.chmod(0o770)
  with self.assertRaises(ValueError):self.router()
  self.socket_dir.chmod(0o700);self.assertEqual(self.marker.read_bytes(),before)

class NativeSnapshotTests(unittest.TestCase):
 setUp=BindingTests.setUp
 codex=BindingTests.codex
 router=BindingTests.router
 state=BindingTests.state
 def snapshot(self,**kw):return boot.snapshot_codex(self.prefix,source=self.native,expected_sha256=self.digest,**kw)
 def test_explicit_snapshot_accepts_source775_but_preserves_it_and_private_destination(self):
  self.native.chmod(0o775);alias=self.parent/'same-installed-bytes';os.link(self.native,alias)
  before=self.native.read_bytes();result=self.snapshot()
  output=self.prefix/'bin/codex';self.assertEqual(output.read_bytes(),before);self.assertEqual(self.native.read_bytes(),before)
  self.assertEqual(stat.S_IMODE(self.native.stat().st_mode),0o775);self.assertEqual(stat.S_IMODE(output.stat().st_mode),0o500)
  self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode),0o700);self.assertEqual(result['sha256'],self.digest)
  self.assertEqual(alias.read_bytes(),before);self.assertEqual(os.stat(output).st_nlink,1)
  self.assertFalse(result['executed']);self.assertFalse(result['native_version_verified'])
 def test_snapshot_existing_destination_is_preserved(self):
  (self.prefix/'bin').mkdir(mode=0o700);sentinel=self.prefix/'bin/codex';sentinel.write_bytes(b'keep');sentinel.chmod(0o500)
  with self.assertRaises(ValueError):self.snapshot()
  self.assertEqual(sentinel.read_bytes(),b'keep')
 def test_snapshot_wrong_hash_or_cap_cleans_only_new_destination(self):
  with self.assertRaises(ValueError):boot.snapshot_codex(self.prefix,source=self.native,expected_sha256='0'*64)
  self.assertFalse((self.prefix/'bin').exists())
  with patch.object(boot,'NATIVE_LIMIT',4):
   with self.assertRaises(ValueError):self.snapshot()
  self.assertFalse((self.prefix/'bin').exists());self.assertEqual(hashlib.sha256(self.native.read_bytes()).hexdigest(),self.digest)
 def test_source_symlink_is_not_followed(self):
  link=self.parent/'native-link';link.symlink_to(self.native)
  with self.assertRaises((OSError,ValueError)):boot.snapshot_codex(self.prefix,source=link,expected_sha256=self.digest)
  self.assertFalse((self.prefix/'bin').exists())
 def test_snapshot_root_swap_writes_only_owned_directory_and_cleans_copy(self):
  victim=self.parent/'existing';victim.mkdir(mode=0o700);(victim/'keep').write_bytes(b'preserve');moved=self.parent/'moved'
  real=boot.os.write;changed=[False]
  def swap_then_write(fd,raw):
   if not changed[0] and stat.S_IMODE(os.fstat(fd).st_mode)==0o500:
    changed[0]=True;self.prefix.rename(moved);self.prefix.symlink_to(victim,target_is_directory=True)
   return real(fd,raw)
  with patch.object(boot.os,'write',side_effect=swap_then_write):
   with self.assertRaises((OSError,ValueError)):self.snapshot()
  self.assertTrue(changed[0]);self.assertEqual([(p.name,p.read_bytes()) for p in victim.iterdir()],[('keep',b'preserve')]);self.assertFalse((moved/'bin').exists())

 def test_snapshot_partial_writes_are_completed_and_zero_progress_is_rejected(self):
  real=boot.os.write;reached=[]
  def partial(fd,raw):reached.append(True);return real(fd,raw[:max(1,len(raw)//3)])
  with patch.object(boot.os,'write',side_effect=partial):self.snapshot()
  self.assertTrue(reached);self.assertEqual((self.prefix/'bin/codex').read_bytes(),self.native.read_bytes())
  (self.prefix/'bin/codex').unlink();(self.prefix/'bin').rmdir();reached.clear()
  def zero(fd,raw):self.assertTrue(raw);reached.append(True);return 0
  with patch.object(boot.os,'write',side_effect=zero):
   with self.assertRaisesRegex(ValueError,'Write made no progress'):self.snapshot()
  self.assertEqual(reached,[True]);self.assertFalse((self.prefix/'bin').exists())
 def test_snapshot_source_drift_is_not_published_and_existing_source_is_not_restored(self):
  real=boot._stream_digest;calls=[];changed=b'\x7fELFchanged by the synthetic actor'
  def change_before_final_hash(fd):
   calls.append(True)
   if len(calls)==2:self.native.chmod(0o700);self.native.write_bytes(changed)
   return real(fd)
  with patch.object(boot,'_stream_digest',side_effect=change_before_final_hash):
   with self.assertRaisesRegex(ValueError,'Native source changed'):self.snapshot()
  self.assertEqual(len(calls),2);self.assertEqual(self.native.read_bytes(),changed);self.assertFalse((self.prefix/'bin').exists())
 def test_snapshot_dry_run_and_zero_umask_preserve_all_original_bytes(self):
  marker=self.marker.read_bytes();self.assertEqual(self.snapshot(dry_run=True)['status'],'planned');self.assertFalse((self.prefix/'bin').exists())
  previous=os.umask(0)
  try:
   self.snapshot();observed=os.umask(0);self.assertEqual(observed,0)
  finally:os.umask(previous)
  self.assertEqual(self.marker.read_bytes(),marker);self.assertEqual(stat.S_IMODE((self.prefix/'bin/codex').stat().st_mode),0o500)
  self.assertEqual(boot.NATIVE_LIMIT,512*1024**2)

 def test_snapshot_source_ancestor_symlink_is_refused_without_destination(self):
  directory=self.parent/'source-tree';directory.mkdir(mode=0o700)
  source=directory/'codex';source.write_bytes(self.native.read_bytes());source.chmod(0o775)
  link=self.parent/'source-link';link.symlink_to(directory,target_is_directory=True)
  with self.assertRaises((OSError,ValueError)):boot.snapshot_codex(self.prefix,source=link/'codex',expected_sha256=self.digest)
  self.assertFalse((self.prefix/'bin').exists());self.assertEqual(source.read_bytes(),self.native.read_bytes())

 def host_source(self):
  p=self.parent/'installed-code-mode-host';p.write_bytes(b'\x7fELFsynthetic-code-mode-host');p.chmod(0o775)
  return p,hashlib.sha256(p.read_bytes()).hexdigest()
 def pair(self,**kw):
  host,sha=self.host_source()
  result=self.snapshot(code_mode_host_source=host,code_mode_host_sha256=sha,**kw)
  return host,sha,result
 def bind_pair(self,*,imported=False):
  host,sha,result=self.pair();main=self.prefix/'bin/codex';helper=self.prefix/'bin/codex-code-mode-host'
  args={'binary':main,'binary_sha256':self.digest,'code_mode_host':helper,'code_mode_host_sha256':sha}
  if imported:
   policy={'schema':2,'workspaces':{'e2e':str(self.prefix/'workspace')},'models':['opaque.Sol:raw'],'max_threads':4,'max_tasks':16,'concurrency':2,'timeout_seconds':120}
   source=self.parent/'operator-policy.json';source.write_text(json.dumps(policy));source.chmod(0o600)
   args.update(policy_file=source,policy_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
  else:args.update(workspace=self.prefix/'workspace',models=['opaque.Sol:raw'])
  boot.configure_codex(self.prefix,**args)
  return main,helper,sha
 def test_pair_snapshot_is_explicit_private_and_does_not_execute(self):
  host,sha=self.host_source();before=host.read_bytes()
  planned=self.snapshot(code_mode_host_source=host,code_mode_host_sha256=sha,dry_run=True)
  self.assertEqual(planned['code_mode_host']['sha256'],sha);self.assertFalse((self.prefix/'bin').exists())
  result=self.snapshot(code_mode_host_source=host,code_mode_host_sha256=sha)
  for name,data in (('codex',self.native.read_bytes()),('codex-code-mode-host',before)):
   p=self.prefix/'bin'/name;self.assertEqual(p.read_bytes(),data);self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o500);self.assertEqual(p.stat().st_nlink,1)
  self.assertEqual(host.read_bytes(),before);self.assertEqual(stat.S_IMODE(host.stat().st_mode),0o775)
  self.assertFalse(result['executed']);self.assertFalse(result['native_version_verified'])
 def test_pair_partial_flags_bad_hash_and_symlink_preserve_destination(self):
  host,sha=self.host_source();link=self.parent/'host-link';link.symlink_to(host)
  for args in ({'code_mode_host_source':host},{'code_mode_host_sha256':sha},
               {'code_mode_host_source':host,'code_mode_host_sha256':'0'*64},
               {'code_mode_host_source':link,'code_mode_host_sha256':sha}):
   with self.subTest(args=args),self.assertRaises((ValueError,OSError)):self.snapshot(**args)
   self.assertFalse((self.prefix/'bin').exists())
 def test_pair_second_copy_failure_rolls_back_both_new_files(self):
  host,sha=self.host_source();real=boot._write_all;calls=[]
  def fail_second(fd,raw):
   calls.append(True)
   if len(calls)==2:raise OSError('synthetic second-copy failure')
   return real(fd,raw)
  with patch.object(boot,'_write_all',side_effect=fail_second),self.assertRaises(OSError):
   self.snapshot(code_mode_host_source=host,code_mode_host_sha256=sha)
  self.assertEqual(len(calls),2);self.assertFalse((self.prefix/'bin').exists())
  self.assertEqual(hashlib.sha256(host.read_bytes()).hexdigest(),sha)
 def test_pair_rollback_does_not_remove_unrelated_actor_file(self):
  host,sha=self.host_source();real=boot._write_all;calls=[]
  def fail_second(fd,raw):
   calls.append(True)
   if len(calls)==2:
    (self.prefix/'bin/keep').write_bytes(b'preserve unrelated data')
    raise OSError('synthetic second-copy failure')
   return real(fd,raw)
  with patch.object(boot,'_write_all',side_effect=fail_second),self.assertRaises(OSError):
   self.snapshot(code_mode_host_source=host,code_mode_host_sha256=sha)
  self.assertEqual(sorted(p.name for p in (self.prefix/'bin').iterdir()),['keep'])
  self.assertEqual((self.prefix/'bin/keep').read_bytes(),b'preserve unrelated data')
 def test_pair_binding_verifies_both_hashes_and_rejects_helper_drift(self):
  from codex_host import executor_snapshot,PipelineError
  import codex_host
  main,helper,sha=self.bind_pair();selected=self.marker.read_bytes()
  self.assertEqual(self.state()['codex']['code_mode_host']['binary_sha256'],sha)
  with patch.object(codex_host,'binary_snapshot',wraps=codex_host.binary_snapshot) as checked:
   executor_snapshot(self.runtime,str(main))
  self.assertEqual(checked.call_args_list,[unittest.mock.call(str(main),self.digest),unittest.mock.call(str(helper),sha)])
  helper.chmod(0o700);helper.write_bytes(b'\x7fELFchanged companion');helper.chmod(0o500)
  with self.assertRaises(PipelineError):executor_snapshot(self.runtime,str(main))
  self.assertEqual(self.marker.read_bytes(),selected)
 def test_pair_binding_missing_or_replaced_helper_refuses_before_transport(self):
  from codex_host import executor_snapshot,PipelineError
  main,helper,sha=self.bind_pair();original=helper.read_bytes();helper.unlink()
  with self.assertRaises(PipelineError):executor_snapshot(self.runtime,str(main))
  helper.write_bytes(original);helper.chmod(0o500)
  with self.assertRaises(PipelineError):executor_snapshot(self.runtime,str(main))
 def test_declared_absence_refuses_later_unpinned_sibling(self):
  from codex_host import executor_snapshot,PipelineError
  self.codex();self.assertIsNone(self.state()['codex']['code_mode_host'])
  helper=self.native.with_name('codex-code-mode-host');helper.write_bytes(b'\x7fELFunpinned');helper.chmod(0o500)
  with self.assertRaisesRegex(PipelineError,'UNPINNED_NATIVE_COMPANION'):executor_snapshot(self.runtime,str(self.native))
 def test_old_binding_without_companion_field_keeps_metadata_compatibility(self):
  from codex_host import executor_snapshot
  self.codex();value=self.state();value['codex'].pop('code_mode_host');self.marker.write_text(json.dumps(value));self.marker.chmod(0o600)
  self.assertEqual(executor_snapshot(self.runtime,str(self.native))['binary_sha256'],self.digest)
 def test_pair_supports_schema2_private_policy_without_finite_slot_changes(self):
  from codex_host import executor_snapshot
  main,helper,sha=self.bind_pair(imported=True);value=self.state()['codex']
  self.assertEqual(value['policy']['schema'],2);self.assertEqual(value['policy']['concurrency'],2)
  self.assertEqual(value['code_mode_host']['binary'],str(helper));executor_snapshot(self.runtime,str(main))
 def test_cli_companion_pairs_are_action_scoped_and_dry_run_only(self):
  from contextlib import redirect_stdout,redirect_stderr
  import io
  host,sha=self.host_source();base=['snapshot-codex','--prefix',str(self.prefix),'--source',str(self.native),'--sha256',self.digest]
  with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
   self.assertEqual(boot.main(base+['--code-mode-host-source',str(host),'--dry-run']),1)
   self.assertEqual(boot.main(base+['--code-mode-host-source',str(host),'--code-mode-host-sha256',sha,'--dry-run']),0)
   self.assertEqual(boot.main(base+['--code-mode-host',str(host),'--code-mode-host-sha256',sha,'--dry-run']),1)
  self.assertFalse((self.prefix/'bin').exists())

 def test_cli_configure_forwards_the_selected_companion(self):
  from contextlib import redirect_stdout,redirect_stderr
  import io
  host,sha,result=self.pair();main=self.prefix/'bin/codex';helper=self.prefix/'bin/codex-code-mode-host'
  args=['configure-codex','--prefix',str(self.prefix),'--native',str(main),'--sha256',self.digest,'--code-mode-host',str(helper),'--code-mode-host-sha256',sha,'--workspace',str(self.prefix/'workspace'),'--model','opaque.Sol:raw']
  with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):self.assertEqual(boot.main(args),0)
  self.assertEqual(self.state()['codex']['code_mode_host']['binary_sha256'],sha)

if __name__=='__main__':unittest.main(verbosity=2)
