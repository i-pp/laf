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

# Scope: this newly authored file only; imported private source is not relicensed.
"""No manager, child, socket, SDK, live runtime or model execution."""
import copy
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('cutover',Path(__file__).with_name('frontend_cutover.py'))
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

class Service:
 def __init__(self,root):
  self.root=root; self.health=root/'health';self.health.write_text('old health');self.health.chmod(0o600)
  self.units={'daemon':'d.service','tunnel':'t.service'}
  self.client=Path('/known/client');self.state={'phase':'READY','invocations':{'daemon':'d1','tunnel':'t1'},'credential_file_reference':'/private/key','schema':1}
  self.ops=[];self.props={role:{'LoadState':'loaded','ActiveState':'active','InvocationID':inv,'role':role} for role,inv in self.state['invocations'].items()}
  for x in c.FLAGS:setattr(self,'allow_'+x,True)
  self.write_state(self.state)
 def read_state(self):return copy.deepcopy(self.state)
 def write_state(self,state):self.state=copy.deepcopy(state);(self.root/'poc-services-state.json').write_text(json.dumps(state))
 def owned(self,state,role,p):
  if p.get('LoadState')=='not-found':return False
  if p.get('InvocationID')!=state['invocations'][role]:raise c.Refused('owner mismatch')
  return True
 def locked(self):return nullcontext()
 def preflight(self,key):self.ops.append(('preflight',key))
 def require_idle_database(self):self.ops.append(('idle',))
 def commands(self):return {'daemon':['never-start-daemon'],'tunnel':['client','run','--control-plane.api-key=file:/private/key','--mcp.command=channel=main,command=old']}
 def call(self,argv,timeout=10):
  if argv[0].endswith('systemctl') and argv[2]=='show':
   role='tunnel' if argv[3]=='t.service' else 'daemon'
   return '\n'.join(k+'='+str(v) for k,v in self.props[role].items())
  self.ops.append(('call',tuple(argv)))
  if argv==['/usr/bin/systemctl','--user','stop','t.service']:
   self.props['tunnel']={'LoadState':'not-found','ActiveState':'inactive'};return ''
  raise AssertionError(argv)
 def launch(self,state,role,argv):
  assert role=='tunnel'
  self.ops.append(('launch',role,argv))
  inv='t'+str(sum(x[0]=='launch' for x in self.ops)+1)
  state['invocations'][role]=inv;self.write_state(state)
  self.props[role]={'LoadState':'loaded','ActiveState':'active','InvocationID':inv,'role':role}
  self.health.write_text('new health');self.health.chmod(0o600)

class TestCutover(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path.home());self.root=Path(self.tmp.name);self.s=Service(self.root)
  self.plan={'expected_invocations':{'daemon':'d1','tunnel':'t1'},'expected_state_sha256':c.digest((self.root/'poc-services-state.json').read_bytes()),'expected_tunnel_properties':copy.deepcopy(self.s.props['tunnel']),'python':'/sdk/python','candidate_root':'/release','router_socket':'/owner/service.sock','router_state':'/owner/receipts','models':['flash','coder-next']}
  self.j=self.root/'journal'
  self.ready=patch.object(c,'service_wait_ready',return_value=True);self.ready.start()
 def tearDown(self):self.ready.stop();self.tmp.cleanup()
 def apply(self):return c.switch(self.plan,self.s,self.j)
 def stops(self):return [x for x in self.s.ops if x[0]=='call']
 def test_success_tunnel_only_state_and_evidence(self):
  result=self.apply();self.assertEqual(result['status'],'transport_ready_client_acceptance_pending');self.assertEqual(self.s.state['phase'],'READY');self.assertEqual(self.s.state['invocations'],{'daemon':'d1','tunnel':'t2'});self.assertEqual(len(self.stops()),1);self.assertEqual((self.j/'old-health-url').read_text(),'old health');self.assertEqual(c.read_json(self.j/'before-state.json')['invocations']['tunnel'],'t1')
 def test_command_preserves_all_outer_arguments_and_flags(self):
  out=c.new_command(self.s,self.plan);self.assertEqual(out[:3],self.s.commands()['tunnel'][:3]);value=out[3]
  for flag in c.FLAGS:self.assertIn('--allow-'+flag.replace('_','-'),value)
  self.assertNotIn('--allow-writes',value);self.assertIn('--allow-laf-router',value);self.assertIn('--runtime-project-root',value)
 def test_changed_invocation_stops_nothing(self):
  self.s.props['tunnel']['InvocationID']='foreign'
  with self.assertRaises(c.Refused):self.apply()
  self.assertEqual(self.stops(),[]);self.assertFalse(self.j.exists())
 def test_changed_state_stops_nothing(self):
  self.s.write_state({**self.s.state,'extra':'changed'})
  with self.assertRaises(c.Refused):self.apply()
  self.assertEqual(self.stops(),[])
 def test_property_drift_stops_nothing(self):
  self.s.props['tunnel']['Restart']='always'
  with self.assertRaises(c.Refused):self.apply()
  self.assertEqual(self.stops(),[])
 def test_preflight_failure_stops_nothing(self):
  self.s.preflight=lambda _:(_ for _ in ()).throw(c.Refused('active work'))
  with self.assertRaises(c.Refused):self.apply()
  self.assertEqual(self.stops(),[])
 def test_existing_journal_retained(self):
  self.j.mkdir();(self.j/'keep').write_text('keep')
  with self.assertRaises(c.Refused):self.apply()
  self.assertEqual((self.j/'keep').read_text(),'keep');self.assertEqual(self.stops(),[])
 def test_stop_error_is_not_retried_or_rolled_back(self):
  real=self.s.call
  def call(argv,timeout=10):
   if argv[2]=='stop':self.s.ops.append(('call',tuple(argv)));raise c.OperationError('denied',uncertain=True)
   return real(argv,timeout)
  self.s.call=call
  with self.assertRaises(c.OperationError):self.apply()
  self.assertEqual(len(self.stops()),1);self.assertEqual(self.s.state['phase'],'CLEANUP_UNCONFIRMED');self.assertFalse(any(x[0]=='launch' for x in self.s.ops));self.assertEqual(self.s.health.read_text(),'old health')
 def test_launch_error_retains_evidence_no_auto_retry(self):
  self.s.launch=lambda *args:(_ for _ in ()).throw(c.OperationError('uncertain',uncertain=True))
  with self.assertRaises(c.OperationError):self.apply()
  self.assertEqual(len(self.stops()),1);self.assertEqual((self.j/'old-health-url').read_text(),'old health');self.assertEqual(self.s.state['phase'],'CLEANUP_UNCONFIRMED')
 def test_readiness_error_keeps_owned_new_invocation_for_reconciliation(self):
  with patch.object(c,'wait_tunnel',side_effect=c.Refused('not ready')):
   with self.assertRaises(c.Refused):self.apply()
  record=c.read_json(self.j/'transaction.json');self.assertEqual(record['new_invocation'],'t2');self.assertEqual(record['stage'],'reconcile_before_retry');self.assertEqual(len(self.stops()),1)
 def test_daemon_drift_after_stop_prevents_new_launch(self):
  with patch.object(c,'daemon_unchanged',side_effect=c.Refused('daemon changed')):
   with self.assertRaises(c.Refused):self.apply()
  self.assertFalse(any(x[0]=='launch' for x in self.s.ops));self.assertEqual(len(self.stops()),1)
 def test_rollback_only_new_owned_tunnel_to_exact_old_command(self):
  self.apply();result=c.rollback(self.plan,self.s,self.j);self.assertEqual(result['status'],'rolled_back_transport_ready');self.assertEqual(self.s.state['invocations'],{'daemon':'d1','tunnel':'t3'});launches=[x for x in self.s.ops if x[0]=='launch'];self.assertEqual(launches[-1][2],self.s.commands()['tunnel']);self.assertEqual(len(self.stops()),2);self.assertEqual((self.j/'replacement-health-url').read_text(),'new health')
 def test_rollback_unknown_new_generation_not_adopted(self):
  self.apply();self.s.state['invocations']['tunnel']='other'
  with self.assertRaises(c.Refused):c.rollback(self.plan,self.s,self.j)
  self.assertEqual(len(self.stops()),1)
 def test_rollback_changed_daemon_not_stopped(self):
  self.apply();self.s.props['daemon']['InvocationID']='other'
  with self.assertRaises(c.Refused):c.rollback(self.plan,self.s,self.j)
  self.assertEqual(len(self.stops()),1)
 def test_rollback_repeated_not_retried(self):
  self.apply();c.rollback(self.plan,self.s,self.j)
  with self.assertRaises(c.Refused):c.rollback(self.plan,self.s,self.j)
  self.assertEqual(len(self.stops()),2)
 def test_rollback_stop_failure_never_leaves_ready(self):
  self.apply();real=self.s.call
  def call(argv,timeout=10):
   if argv[2]=='stop':self.s.ops.append(('call',tuple(argv)));raise c.OperationError('denied',uncertain=True)
   return real(argv,timeout)
  self.s.call=call
  with self.assertRaises(c.OperationError):c.rollback(self.plan,self.s,self.j)
  self.assertEqual(self.s.state['phase'],'CLEANUP_UNCONFIRMED');self.assertEqual(len(self.stops()),2);self.assertEqual(c.read_json(self.j/'transaction.json')['stage'],'rollback_reconcile_before_retry')
 def test_rollback_launch_uncertain_never_leaves_ready(self):
  self.apply();self.s.launch=lambda *a:(_ for _ in ()).throw(c.OperationError('uncertain',uncertain=True))
  with self.assertRaises(c.OperationError):c.rollback(self.plan,self.s,self.j)
  self.assertEqual(self.s.state['phase'],'CLEANUP_UNCONFIRMED');self.assertEqual(len(self.stops()),2)
 def test_rollback_readiness_failed_retains_generation_and_uncertainty(self):
  self.apply()
  with patch.object(c,'wait_tunnel',side_effect=c.Refused('not ready')):
   with self.assertRaises(c.Refused):c.rollback(self.plan,self.s,self.j)
  self.assertEqual(self.s.state['phase'],'CLEANUP_UNCONFIRMED');self.assertEqual(c.read_json(self.j/'transaction.json')['rollback_invocation'],'t3');self.assertEqual(len(self.stops()),2)
 def test_atomic_record_failure_preserves_last_complete_record(self):
  dest=self.root/'record.json';c.write_json(dest,{'stage':'before'},exclusive=True);old=dest.read_bytes()
  with patch.object(c.os,'replace',side_effect=OSError('fault')):
   with self.assertRaises(OSError):c.write_json(dest,{'stage':'after'})
  self.assertEqual(dest.read_bytes(),old);self.assertEqual(list(self.root.glob('.cutover-record-*')),[])
 def test_exclusive_record_never_overwrites_old_evidence(self):
  dest=self.root/'record.json';c.write_json(dest,{'stage':'before'},exclusive=True);old=dest.read_bytes()
  with self.assertRaises(FileExistsError):c.write_json(dest,{'stage':'after'},exclusive=True)
  self.assertEqual(dest.read_bytes(),old)
 def test_missing_locator_is_bounded_transient_without_service_operation(self):
  with patch.object(c,'service_wait_ready',side_effect=[FileNotFoundError('not created'),True]) as ready,patch.object(c.time,'sleep'):
   c.wait_tunnel(self.s,self.s.state,1)
  self.assertEqual(ready.call_count,2);self.assertEqual(self.stops(),[])
 def test_permission_error_is_not_a_transient_retry(self):
  with patch.object(c,'service_wait_ready',side_effect=PermissionError('denied')) as ready:
   with self.assertRaises(PermissionError):c.wait_tunnel(self.s,self.s.state,1)
  self.assertEqual(ready.call_count,1);self.assertEqual(self.stops(),[])
 def test_reconcile_only_recorded_ready_generation_without_restart(self):
  with patch.object(c,'wait_tunnel',side_effect=FileNotFoundError('startup race')):
   with self.assertRaises(FileNotFoundError):self.apply()
  count=len(self.stops());out=c.reconcile(self.plan,self.s,self.j)
  self.assertEqual(out['service_changes'],0);self.assertEqual(self.s.state['phase'],'READY');self.assertEqual(self.s.state['invocations'],{'daemon':'d1','tunnel':'t2'});self.assertEqual(len(self.stops()),count)
 def test_reconcile_wrong_generation_never_adopted(self):
  with patch.object(c,'wait_tunnel',side_effect=FileNotFoundError('startup race')):
   with self.assertRaises(FileNotFoundError):self.apply()
  self.s.props['tunnel']['InvocationID']='foreign'
  with self.assertRaises(c.Refused):c.reconcile(self.plan,self.s,self.j)
  self.assertEqual(self.s.state['phase'],'CLEANUP_UNCONFIRMED');self.assertEqual(len(self.stops()),1)
 def test_reconcile_not_ready_preserves_uncertainty(self):
  with patch.object(c,'wait_tunnel',side_effect=FileNotFoundError('startup race')):
   with self.assertRaises(FileNotFoundError):self.apply()
  with patch.object(c,'service_wait_ready',return_value=False):
   with self.assertRaises(c.Refused):c.reconcile(self.plan,self.s,self.j)
  self.assertEqual(self.s.state['phase'],'CLEANUP_UNCONFIRMED');self.assertEqual(len(self.stops()),1)
 def test_manager_call_inherits_terminal_environment_no_bus_override(self):
  with patch.object(c.subprocess,'run',return_value=type('Result',(),{'returncode':0,'stdout':'ok'})()) as run:
   self.assertEqual(c.run(['systemctl','--user','show']),'ok');self.assertNotIn('env',run.call_args.kwargs)
 def test_manager_failure_not_retried(self):
  with patch.object(c.subprocess,'run',return_value=type('Result',(),{'returncode':1,'stdout':''})()) as run:
   with self.assertRaises(c.OperationError):c.run(['systemctl','--user','stop'])
   self.assertEqual(run.call_count,1)
 def test_unsafe_model_allowlist_refused(self):
  self.plan['models']=['flash','bad; arg']
  with self.assertRaises(c.Refused):c.new_command(self.s,self.plan)

if __name__=='__main__':unittest.main(verbosity=2)
