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


# Added 2026-11-15: resource-only admission with durable ownership.

# Added 2026-11-15: external provider acceptance, durable restrictions and CAS.
"""Synthetic account/provider/cgroup data and private SQLite only; no inference."""
from contextlib import closing,ExitStack
import copy,hashlib,json,os,stat,subprocess,sys,tempfile,time,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime/private_codex/src'),str(ROOT/'runtime/integration')]
from scripts.source_test_isolation import require_isolated
require_isolated()  # Refuse raw resource tests before any fixture or project import.
import resource_admission as admission
import provider_control
import test_resource_only as fixtures
import test_resource_pipeline as pipeline_fixtures
import codex_app_protocol as wire
from pipeline_resource_admission import PipelineAdmission
from codex_pipeline import Pipeline
from codex_host import HostGateway
import scheduler

AUTH=admission.account_fingerprint(fixtures.ACCOUNT)
USAGE=hashlib.sha256(b'fixture-workspace-A').hexdigest()
def ident(n):return str(uuid.UUID(int=n))
def policy():
 p=fixtures.policy();p['schema']=3
 p['providers']={'chatgpt':{'mode':'feedback','account_sha256':AUTH,'usage_account_sha256':USAGE,'backoff_seconds':10}}
 return p
def usage(percent=20,*,allowed=True,account='fixture-workspace-A',secondary=None):
 bucket={'limitId':'fixture','primary':{'usedPercent':percent,'resetsAt':1001,'windowDurationMins':300},'spendControlReached':False,'rateLimitReachedType':None}
 if secondary is not None:bucket['secondary']={'usedPercent':secondary}
 return {'rateLimits':bucket,'ordinaryUsageAllowed':allowed,'accountId':account}

class LedgerFeedback(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)/'runtime';self.root.mkdir()
  self.p=policy();self.clock={'mono':100.,'wall':1000.}
  for target,value in [('resource_admission.time.time',lambda:self.clock['wall']),('resource_admission.time.monotonic',lambda:self.clock['mono'])]:
   q=patch(target,value);q.start();self.addCleanup(q.stop)
  self.path=Path(temp.name)/'ledger';self.ledger=admission.Ledger(self.p,self.path);self.addCleanup(self.ledger.close)
  self.owner=self.ledger.register_owner(self.root,'standalone');self.task=ident(1);self.token=ident(1001);self.reserve(1)
 def reserve(self,n,observe=fixtures.fixtures.observation,**kwargs):
  return self.ledger.reserve(self.root,ident(n),ident(1000+n),fixtures.pending(cpu=10,ram=10),observe,
      now=100,wall=1000,owner=self.owner,**kwargs)
 def observe(self,raw=None,*,kind='notification',revision=None,fresh=False,account=AUTH,value=None,observed_at=None):
  self.ledger.observe_provider(self.root,self.task,self.token,'chatgpt',account,
    value or admission.provider_snapshot(raw or usage(100)),kind=kind,revision=revision,fresh=fresh,observed_at=self.clock['mono'] if observed_at is None else observed_at)
 def revision(self):return self.ledger.provider_revision(self.root,self.task,self.token,'chatgpt')
 def later(self):self.clock['mono']+=20;self.clock['wall']+=20
 def authorize(self):return self.ledger.authorize_provider(self.root,self.task,self.token,'chatgpt',AUTH)
 def blocked(self):
  with self.assertRaisesRegex(admission.AdmissionWait,'provider_(backoff|feedback_unknown)'):self.authorize()

 def test_feedback_policy_has_no_inferred_limit_expiry_or_slots(self):
  p=admission.policy(self.p);self.assertTrue(admission.resource_only(p));self.assertNotIn('limit',p['providers']['chatgpt'])
  for n in range(2,14):self.reserve(n);self.ledger.authorize_provider(self.root,ident(n),ident(1000+n),'chatgpt',AUTH)
  self.assertEqual(len(self.ledger.held()),13)
 def test_unknown_or_zero_physical_resources_still_refuse_schema3(self):
  def missing(rows,previous):
   sample,state=fixtures.fixtures.observation(rows,previous);sample['available']['ram_bytes']=None;return sample,state
  with self.assertRaisesRegex(admission.AdmissionWait,'ram_bytes_unknown'):self.reserve(2,observe=missing)
 def test_invalid_feedback_policy_cannot_smuggle_a_fixed_limit(self):
  for change in ({'limit':8},{'valid_until':9999},{'backoff_seconds':0},{'backoff_seconds':float('inf')},{'account_sha256':'x'},{'usage_account_sha256':None}):
   p=copy.deepcopy(self.p);p['providers']['chatgpt'].update(change)
   with self.subTest(change=change),self.assertRaises(ValueError):admission.policy(p)
 def test_explicit_known_concurrency_contract_still_enforces_actual_limit(self):
  p=fixtures.policy();p['providers']['chatgpt']['limit']=1
  path=self.path.parent/'finite'
  with closing(admission.Ledger(p,path)) as ledger:
   owner=ledger.register_owner(self.root,'standalone');ledger.reserve(self.root,ident(10),ident(1010),fixtures.pending(),fixtures.fixtures.observation,now=100,wall=1000,owner=owner)
   with self.assertRaisesRegex(admission.AdmissionWait,'provider_quota$'):
    ledger.reserve(self.root,ident(11),ident(1011),fixtures.pending(),fixtures.fixtures.observation,now=100,wall=1000,owner=owner)
 def test_account_binding_and_unknown_owner_are_required(self):
  with self.assertRaisesRegex(admission.AdmissionWait,'account_unknown'):self.ledger.authorize_provider(self.root,self.task,self.token,'chatgpt','0'*64)
  with patch.object(admission,'owner_live',return_value=None):
   with self.assertRaisesRegex(admission.AdmissionWait,'owner_unknown'):self.authorize()
 def test_usage_restriction_blocks_without_releasing_existing_grants(self):
  self.authorize();self.observe();self.blocked()
  with self.assertRaisesRegex(admission.AdmissionWait,'provider_backoff'):self.reserve(2)
  self.assertEqual(len(self.ledger.held()),1);self.assertEqual(json.loads(self.ledger.held()[0]['demand'])['kind'],'cloud_api')
 def test_reset_time_and_lower_percentages_never_recover(self):
  self.observe();self.later();self.observe(usage(0,allowed=None),kind='snapshot',revision=self.revision(),fresh=True);self.blocked()
 def test_sparse_favorable_notifications_never_recover(self):
  self.observe();self.later();self.observe(usage(0),kind='notification');self.blocked()
 def test_positive_full_matching_snapshot_recovers_after_backoff(self):
  self.observe();self.later();self.observe(usage(10),kind='snapshot',revision=self.revision(),fresh=True);self.authorize()
 def test_backoff_alone_does_not_recover_or_retry(self):
  self.observe();self.later();self.blocked();self.assertEqual(len(self.ledger.held()),1)
 def test_full_positive_before_backoff_remains_blocked(self):
  self.observe();self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True);self.blocked()
 def test_another_connection_rejection_invalidates_prior_favorable_read(self):
  rev=self.revision()
  with closing(admission.Ledger(self.p,self.path)) as other:
   other.observe_provider(self.root,self.task,self.token,'chatgpt',AUTH,provider_control.observation('block'),kind='rejection')
  self.later();self.observe(usage(0),kind='snapshot',revision=rev,fresh=True);self.blocked()
 def test_any_notification_invalidates_a_prior_recovery_read(self):
  self.observe();self.later();rev=self.revision();self.observe(usage(0),kind='notification')
  self.observe(usage(0),kind='snapshot',revision=rev,fresh=True);self.blocked()
 def test_restriction_survives_reopen(self):
  self.observe()
  with closing(admission.Ledger(self.p,self.path)) as other:
   with self.assertRaises(admission.AdmissionWait):other.authorize_provider(self.root,self.task,self.token,'chatgpt',AUTH)
 def test_missing_or_changed_usage_account_cannot_recover(self):
  self.observe();self.later()
  for account in (None,'different-workspace'):
   self.observe(usage(0,account=account),kind='snapshot',revision=self.revision(),fresh=True);self.blocked();self.later()
 def test_unpinned_usage_identity_cannot_recover(self):
  self.p['providers']['chatgpt'].pop('usage_account_sha256');self.ledger.policy=self.p
  self.observe();self.later();self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True);self.blocked()
 def test_missing_previously_exhausted_secondary_window_cannot_recover(self):
  self.observe(usage(10,secondary=100));self.later();self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True);self.blocked()
 def test_missing_previously_blocked_bucket_cannot_recover(self):
  raw=usage(10);raw['rateLimitsByLimitId']={'another':{'primary':{'usedPercent':100}}};self.observe(raw)
  self.later();self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True);self.blocked()
 def test_spend_or_reached_contradictions_override_ordinary_permission(self):
  for field,value in (('spendControlReached',True),('rateLimitReachedType','individualLimit')):
   raw=usage(0);raw['rateLimits'][field]=value;self.assertEqual(admission.provider_snapshot(raw)['decision'],'block')
 def test_empty_or_null_usage_does_not_establish_recovery(self):
  for raw in ({'rateLimits':{},'ordinaryUsageAllowed':True},{'rateLimits':{'primary':None},'rateLimitsByLimitId':None,'ordinaryUsageAllowed':True}):
   self.assertEqual(admission.provider_snapshot(raw)['decision'],'unavailable')
 def test_malformed_usage_is_rejected_without_numeric_coercion(self):
  for bad in (True,-1,'50',float('nan')):
   raw=usage();raw['rateLimits']['primary']['usedPercent']=bad
   with self.assertRaises(ValueError):admission.provider_snapshot(raw)
 def test_missing_snapshot_auth_cannot_clear_known_restriction(self):
  self.observe();self.later();self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True,account=None);self.blocked()
 def test_schema3_cannot_select_an_undocumented_concurrency_mode(self):
  p=policy();p['providers']['chatgpt']={**fixtures.policy()['providers']['chatgpt'],'mode':'concurrency'}
  with self.assertRaises(ValueError):admission.policy(p)
 def test_corrupt_durable_restriction_never_means_open_capacity(self):
  with self.ledger.db:self.ledger.db.execute('INSERT INTO provider_feedback VALUES(?,?,?)',('chatgpt',AUTH,'{"blocked":false}'))
  with self.assertRaisesRegex(admission.AdmissionWait,'provider_feedback_unknown'):self.authorize()
 def test_restriction_scope_overflow_is_unknown_and_cannot_auto_clear(self):
  for offset in range(3):
   value=provider_control.observation('block');value['denied_scopes']=['limit:fixture-'+str(offset)+'-'+str(n)+'/primary' for n in range(90)]
   self.observe(value=value)
  self.later();self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True);self.blocked()
 def test_new_usage_pin_cannot_recover_a_different_original_workspace(self):
  self.observe();self.later();other=hashlib.sha256(b'different-workspace').hexdigest()
  self.ledger.policy['providers']['chatgpt']['usage_account_sha256']=other
  self.observe(usage(0,account='different-workspace'),kind='snapshot',revision=self.revision(),fresh=True);self.blocked()
 def test_later_usage_pin_cannot_recover_an_originally_unbound_restriction(self):
  self.ledger.policy['providers']['chatgpt'].pop('usage_account_sha256')
  self.observe();self.later();self.ledger.policy['providers']['chatgpt']['usage_account_sha256']=USAGE
  self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True);self.blocked()
 def test_forward_wall_jump_does_not_expire_monotonic_backoff(self):
  self.observe();self.clock['wall']+=1000
  self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True);self.blocked()
  self.clock['mono']+=20
  self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True);self.authorize()
 def test_prior_boot_receipt_restarts_backoff_before_positive_recovery(self):
  self.observe();row=self.ledger.db.execute('SELECT body FROM provider_feedback').fetchone();state=json.loads(row[0])
  # Synthetic past-boot restriction; the current owner is freshly valid here.
  state['boot']=str(uuid.uuid4())
  with self.ledger.db:self.ledger.db.execute('UPDATE provider_feedback SET body=?',(json.dumps(state),))
  self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True);self.blocked()
  self.later();self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True);self.authorize()
 def test_old_read_timestamp_cannot_recover_after_delayed_commit(self):
  self.observe();self.later();began=self.clock['mono'];self.clock['mono']+=10;self.clock['wall']+=10
  self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True,observed_at=began);self.blocked()
 def test_nested_bucket_identity_is_validated_before_normalization(self):
  for declared in (['invalid'],'different'):
   raw=usage(0);raw['rateLimitsByLimitId']={'another':{'limitId':declared,'primary':{'usedPercent':0}}}
   with self.assertRaises(ValueError):admission.provider_snapshot(raw)
 def test_feedback_never_changes_reservation_phase(self):
  before=[dict(x) for x in self.ledger.held()];self.observe();self.later();self.observe(usage(0),kind='snapshot',revision=self.revision(),fresh=True)
  self.assertEqual(self.ledger.held(),before)
 def test_held_generation_cannot_be_rebound_to_feedback(self):
  p=copy.deepcopy(self.p);p['reserve']['ram_bytes']+=1
  with self.assertRaisesRegex(admission.AdmissionWait,'generation_held'):admission.Ledger(p,self.path)

class PipelineFeedback(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(dir=Path.home());self.addCleanup(temp.cleanup);self.root=Path(temp.name)/'runtime';self.root.mkdir(mode=0o700)
  self.p=policy();self.p['pipeline']=fixtures.fixtures.v(100,100,0,0);self.settings={'resource_admission':self.p}
  self.clock={'mono':100.,'wall':1000.};self.parent=ident(1001);self.state={'tasks':{},'threads':{},'ops':{}};self.calls=[];self.snapshot=usage();self.interleave=None
  self.gateway=SimpleNamespace(root=self.root,protocol=SimpleNamespace(),pipeline=SimpleNamespace(_mutate=lambda f:f(self.state)),rpc=self.rpc)
  for target,value in [('resource_admission.DIRECTORY',Path(temp.name)/'ledger'),('resource_admission.time.time',lambda:self.clock['wall']),('resource_admission.time.monotonic',lambda:self.clock['mono']),('pipeline_resource_admission.linux_observation',self.observe)]:
   q=patch(target,value);q.start();self.addCleanup(q.stop)
  with closing(admission.Ledger(self.p)) as ledger:
   self.owner=ledger.register_owner(self.root,'daemon');ledger.reserve(self.root,ident(1),self.parent,{'kind':'local_control','provider':None,'resources':fixtures.fixtures.v(1000,1000,0,0)},self.observe,owner=self.owner)
  self.gate=PipelineAdmission(self.gateway,self.settings,lambda:(self.owner,self.parent));self.gate.ready=lambda:(self.owner,self.parent)
  self.task={'id':ident(2),'thread_id':ident(102),'selection_sha256':'a'*64,'state':'QUEUED','reasoning_effort_evidence':{}}
  self.state['tasks'][self.task['id']]=self.task;self.state['threads'][self.task['thread_id']]={'remote_id':'remote-thread','active_task':self.task['id']}
  self.task.update(resource_token=self.gate.reserve(self.task),resource_binding=self.gate.binding(self.task))
 def observe(self,rows,previous):
  sample,state=fixtures.fixtures.observation(rows,previous);sample['containers']={self.parent:fixtures.fixtures.v(1000,1000,0,0)};sample['at']=self.clock['mono'];return sample,state
 def rpc(self,method,params,**kw):
  self.calls.append((method,params))
  if method=='account/read':
   self.gateway.protocol._resource_account_sha256=AUTH;self.gateway.protocol._resource_account_observed_at=self.clock['mono'];return fixtures.ACCOUNT
  if method=='account/rateLimits/read':
   if self.interleave:self.interleave()
   if isinstance(self.snapshot,Exception):raise self.snapshot
   return copy.deepcopy(self.snapshot)
  raise AssertionError('No model/task RPC in provider fixtures')
 def operation(self,error):
  self.gate.authorize(self.task);workspace=self.root/'workspace';workspace.mkdir(mode=0o700)
  client=SimpleNamespace(event_sink=None)
  p=Pipeline(self.root,{'schema':2,'workspaces':{'fixture':str(workspace)},'models':['fixture-model'],
      'max_threads':4,'max_tasks':16,'concurrency':8,'timeout_seconds':10},client,
      instructions='Synthetic provider feedback boundary',policy_sha256='0'*64,resource_gate=self.gate)
  self.gateway.pipeline=p;self.gateway.resource_gate=self.gate;self.gateway.connect=lambda:None
  self.gateway.record_failure=Mock();self.gateway.failed=False
  request=Mock(side_effect=error)
  self.gateway.protocol=SimpleNamespace(request=request,timeout_s=10,_resource_account_sha256=AUTH,_resource_account_observed_at=self.clock['mono'])
  thread_id=self.task['thread_id'];task_id=self.task['id']
  def install(s):
   s['tasks'][task_id]=copy.deepcopy(self.task)
   s['threads'][thread_id]={'id':thread_id,'remote_id':'remote-thread','active_task':None}
  p._mutate(install)
  def prepare(s):
   s['tasks'][task_id].update(state='DISPATCHING',remote_turn=None,reasoning_effort_evidence={'turn_transport':'outcome_unknown'})
   s['threads'][thread_id]['active_task']=task_id;return {'task_id':task_id}
  def call():
   return p._operation('dispatch:'+task_id,'dispatch',task_id,prepare,
     lambda context:HostGateway.rpc(self.gateway,'turn/start',{'clientUserMessageId':task_id}),
     lambda *args:self.fail('Rejected/unknown operation cannot finish as success'))
  return p,call,request
 def test_correlated_429_is_failed_and_duplicate_never_sends_again(self):
  p,call,request=self.operation(wire.RpcError(429,'fixture'))
  first=call();second=call();self.assertEqual(first['status'],'FAILED');self.assertTrue(second.pop('duplicate'));self.assertEqual(first,second)
  self.assertEqual(request.call_count,1)
  row=p._mutate(lambda s:dict(s['tasks'][self.task['id']]));self.assertEqual(row['state'],'FAILED')
  self.gate.reap()
  with closing(admission.Ledger(self.p)) as ledger:self.assertEqual(len(ledger.held()),1)
 def test_lost_turn_ack_stays_unknown_charged_and_never_replays(self):
  p,call,request=self.operation(wire.ProtocolUnknown('fixture lost ack'))
  first=call();second=call();self.assertEqual(first['status'],'UNKNOWN');self.assertTrue(second.pop('duplicate'));self.assertEqual(first,second)
  self.assertEqual(request.call_count,1);self.gate.reap()
  row=p._mutate(lambda s:dict(s['tasks'][self.task['id']]));self.assertEqual(row['state'],'UNKNOWN')
  with closing(admission.Ledger(self.p)) as ledger:self.assertEqual(len(ledger.held()),2)
 def test_recovery_read_is_reachable_after_last_rejected_job_is_reaped(self):
  p,call,request=self.operation(wire.RpcError(429,'fixture'));self.assertEqual(call()['status'],'FAILED');self.gate.reap()
  next_task={'id':ident(3),'thread_id':ident(103),'selection_sha256':'b'*64,'state':'QUEUED','reasoning_effort_evidence':{}}
  next_task.update(resource_token=self.gate.reserve(next_task),resource_binding=self.gate.binding(next_task))
  def put(s):s['tasks'][next_task['id']]=next_task;s['threads'][next_task['thread_id']]={'id':next_task['thread_id'],'remote_id':'next-thread','active_task':None}
  p._mutate(put);self.clock['mono']+=20;self.clock['wall']+=20;before=len(self.calls)
  self.gate.authorize(next_task);self.gate.before_turn(next_task['id'])
  self.assertTrue(any(m=='account/rateLimits/read' for m,_ in self.calls[before:]));self.assertEqual(request.call_count,1)
 def test_delay_between_read_and_record_cannot_clear_restriction(self):
  self.gate.notification({'method':'account/rateLimits/updated','params':usage(100)})
  self.clock['mono']+=20;self.clock['wall']+=20;original=provider_control.record
  def delayed(*args,**kw):
   if kw.get('kind')=='snapshot':self.clock['mono']+=10;self.clock['wall']+=10
   return original(*args,**kw)
  with patch.object(provider_control,'record',side_effect=delayed):
   with self.assertRaises(admission.AdmissionWait):self.gate.authorize(self.task)
 def test_pipeline_backoff_keeps_same_queued_task_and_grant_without_dispatch(self):
  self.snapshot=usage(100);task=self.task
  task.update(queue_seq=1,model='fixture-model',reasoning_effort='max')
  self.state['threads'][task['thread_id']].update(active_task=None,archived=False,closing=False)
  receiver=SimpleNamespace(resource_gate=self.gate,policy={'concurrency':8},admission=None,
    _mutate=lambda f:f(self.state),_task=lambda s,i:s['tasks'][i],_thread=lambda s,i,**kw:s['threads'][i],
    _assert_selection=lambda t:None,_model=lambda *a:{'supported_reasoning_efforts':['max']},
    room=lambda *a:True,loaded={task['thread_id']})
  token=task['resource_token']
  for _ in range(2):
   result=Pipeline._dispatch(receiver)
   self.assertEqual(result,{'status':'QUEUED','task_id':task['id'],'waiting_reason':'resource_provider_backoff'})
   self.assertEqual(task['state'],'QUEUED');self.assertEqual(task['resource_token'],token)
   self.assertEqual(task['resource_wait_reason'],'resource_provider_backoff');self.assertFalse(self.state['ops'])
  with closing(admission.Ledger(self.p)) as ledger:self.assertEqual(len(ledger.held()),2)
  self.assertFalse(any(m=='turn/start' for m,p in self.calls))
 def test_normal_feedback_authorization_uses_only_supported_metadata(self):
  self.gate.authorize(self.task);self.gate.before_turn(self.task['id'])
  self.assertTrue(any(m=='account/rateLimits/read' and p=={} for m,p in self.calls));self.assertFalse(any(m=='turn/start' for m,p in self.calls))
 def test_explicitly_unsupported_usage_read_preserves_unknown_initial_capacity(self):
  self.snapshot=wire.RpcError(-32601,'not available');self.gate.authorize(self.task);self.gate.before_turn(self.task['id'])
 def test_unknown_usage_transport_refuses_instead_of_clearing(self):
  self.snapshot=TimeoutError('fixture');
  with self.assertRaisesRegex(admission.AdmissionWait,'provider_feedback_unknown'):self.gate.authorize(self.task)
 def test_interleaved_restriction_during_favorable_read_cannot_clear(self):
  self.interleave=lambda:self.gate.notification({'method':'account/rateLimits/updated','params':usage(100)})
  with self.assertRaises(admission.AdmissionWait):self.gate.authorize(self.task)
 def test_other_client_negative_after_authorize_blocks_before_turn(self):
  self.gate.authorize(self.task);self.gate.notification({'method':'account/rateLimits/updated','params':usage(100)})
  with self.assertRaises(admission.AdmissionWait):self.gate.before_turn(self.task['id'])
 def test_raw_receipt_cannot_impersonate_normalized_protocol_output(self):
  self.snapshot={'decision':'allow','usage_account_sha256':USAGE,'denied_scopes':[],'clear_scopes':['provider']}
  # An unvalidated adapter must parse the raw native schema.
  with self.assertRaises(admission.AdmissionWait):
   provider_control.refresh(self.settings,self.root,self.task['id'],self.task['resource_token'],self.rpc)
 def test_retrying_error_keeps_running_task_and_grant(self):
  self.gate.authorize(self.task);self.task.update(state='RUNNING',remote_turn='turn-1')
  receiver=SimpleNamespace(resource_gate=self.gate,_mutate=lambda f:f(self.state),_task=lambda s,i:s['tasks'][i])
  message={'method':'error','params':{'threadId':'remote-thread','turnId':'turn-1','willRetry':True,'error':{'codexErrorInfo':'rateLimitExceeded','message':'private fixture'}}}
  Pipeline.receive(receiver,message)
  self.assertEqual(self.task['state'],'RUNNING')
  with closing(admission.Ledger(self.p)) as ledger:self.assertEqual(len(ledger.held()),2)
  with self.assertRaises(admission.AdmissionWait):self.gate.before_turn(self.task['id'])
 def test_unrelated_error_cannot_change_provider_feedback(self):
  self.gate.authorize(self.task);self.task.update(state='RUNNING',remote_turn='turn-1')
  receiver=SimpleNamespace(resource_gate=self.gate,_mutate=lambda f:f(self.state),_task=lambda s,i:s['tasks'][i])
  Pipeline.receive(receiver,{'method':'error','params':{'threadId':'other','turnId':'turn-1','willRetry':True,'error':{'codexErrorInfo':'rateLimitExceeded'}}})
  self.gate.before_turn(self.task['id'])
 def test_rpc_rejection_is_shared_feedback_without_task_replay(self):
  self.gate.authorize(self.task);self.gate.rejected(self.task['id'],wire.RpcError(429,'fixture'))
  with self.assertRaises(admission.AdmissionWait):self.gate.before_turn(self.task['id'])
  self.assertEqual(self.task['state'],'QUEUED');self.assertFalse(any(m=='turn/start' for m,p in self.calls))

class ProtocolFeedback(unittest.TestCase):
 def test_structured_rate_errors_only_never_message_text(self):
  self.assertTrue(wire.RpcError(429,'fixture').provider_restriction)
  self.assertTrue(wire.RpcError(-32000,'fixture',{'codexErrorInfo':'usageLimitExceeded'}).provider_restriction)
  self.assertFalse(wire.RpcError(-32000,'429 rateLimitExceeded').provider_restriction)
  self.assertFalse(admission.provider_restriction(data={'httpStatusCode':429}))
 def test_rate_read_has_no_reserve_or_payment_capability(self):
  self.assertIn('account/rateLimits/read',wire.ALLOWED_METHODS)
  self.assertEqual(wire.METHOD_SCHEMAS['account/rateLimits/read'][0],'ClientRequest.json#/definitions/GetAccountRateLimitsParams')
  wire._params('account/rateLimits/read',{})
  for params in ({'supportsLunaReserve':True},{'supportsLunaReserve':False},{'excludeResetCreditDetails':True}):
   with self.assertRaises(wire.ProtocolError):wire._params('account/rateLimits/read',params)
 def test_protocol_omits_raw_usage_account_identity(self):
  result=wire._result('account/rateLimits/read',usage());encoded=json.dumps(result)
  self.assertNotIn('fixture-workspace-A',encoded);self.assertEqual(result['usage_account_sha256'],USAGE)
 def test_selector_discovery_denial_never_means_legacy_absence(self):
  with patch.object(Path,'lstat',side_effect=PermissionError('fixture')):
   with self.assertRaises(PermissionError):scheduler._selector_stat(Path('/unused'))
 def bootstrap_fixture(self):
  import test_bootstrap_resource_policy as selector
  f=selector.ResourcePolicyImport('test_raw_bytes_and_provenance_are_retained_without_source_drift')
  f.setUp();self.addCleanup(f.doCleanups);return f
 def test_schema3_uses_supported_copied_cli_without_editing_packaged_config(self):
  import laf_bootstrap as boot
  import test_bootstrap_bindings as bindings
  f=self.bootstrap_fixture();p=copy.deepcopy(f.policy);p['resource_admission']=policy()
  p['resource_admission']['capacity']['ram_bytes']=32*1024**3;p['resource_admission']['reserve']=fixtures.fixtures.v(1000,2*1024**3,0,0)
  f.write(p);f.configure();boot.configure_orchestrator(f.prefix)
  env={'PATH':'/usr/bin:/bin','HOME':str(f.f.parent),'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
  with patch.object(subprocess,'Popen',bindings.REAL_POPEN):
   result=subprocess.run([sys.executable,'-B',str(f.prefix/'runtime/integration/laf_bootstrap.py'),'daemon-command','--prefix',str(f.prefix)],cwd=f.f.parent,env=env,capture_output=True,text=True,timeout=20)
  self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['scheduler_admission_mode'],'resources')
  self.assertIsNone(scheduler.configuration()['maximum_sessions']);self.assertEqual(f.config.read_bytes(),f.packaged)
  self.assertFalse(list(f.runtime.glob('*.sqlite3')))
 def test_policy_fifo_swap_is_refused_before_read_open(self):
  import laf_bootstrap as boot
  f=self.bootstrap_fixture();real=boot.os.open;seen=[];swapped=False
  def opening(path,flags,*args,**kw):
   nonlocal swapped
   if Path(path).name==f.source.name and kw.get('dir_fd') is not None:
    self.assertTrue(flags&os.O_PATH)
    if not swapped:swapped=True;f.source.unlink();os.mkfifo(f.source,0o600)
   if str(path).startswith('/proc/self/fd/'):seen.append(path)
   return real(path,flags,*args,**kw)
  with patch.object(boot.os,'open',side_effect=opening):f.refuse(f.configure)
  self.assertTrue(swapped);self.assertEqual(seen,[])
 def test_projected_device_type_is_refused_before_read_open(self):
  import laf_bootstrap as boot
  f=self.bootstrap_fixture();real_open,real_stat=boot.os.open,boot.os.fstat;pins=set();reads=[]
  def opening(path,flags,*args,**kw):
   if str(path).startswith('/proc/self/fd/'):reads.append(path)
   fd=real_open(path,flags,*args,**kw)
   if Path(path).name==f.source.name and kw.get('dir_fd') is not None:self.assertTrue(flags&os.O_PATH);pins.add(fd)
   return fd
  def metadata(fd):
   value=real_stat(fd)
   if fd in pins:
    fields=list(value);fields[0]=stat.S_IFCHR|0o600;return os.stat_result(fields)
   return value
  with patch.object(boot.os,'open',side_effect=opening),patch.object(boot.os,'fstat',side_effect=metadata):f.refuse(f.configure)
  self.assertTrue(pins);self.assertEqual(reads,[])
 def test_ordinary_demand_uses_metadata_without_inventing_provider_entitlement(self):
  settings={'resource_admission':policy(),'runner_resource_policy':{'cpu_percent':100,'memory_bytes':100},'worker':{'cpu_percent':100,'memory_bytes':100}}
  demand=admission.ordinary_request(settings,worker=True)
  self.assertEqual(demand['kind'],'local_control');self.assertIsNone(demand['provider']);self.assertEqual(demand['resources']['cpu_millicores'],2000)
  settings['resource_admission']=fixtures.policy();demand=admission.ordinary_request(settings,worker=True)
  self.assertEqual(demand['kind'],'local_control');self.assertEqual(demand['provider'],'chatgpt')
 def test_feedback_ordinary_support_cannot_be_selected_outside_schema3(self):
  for schema in (1,2,None):
   selected=policy();selected['schema']=schema
   with self.subTest(schema=schema),patch.object(admission,'Ledger',side_effect=AssertionError('Ledger forbidden')):
    with self.assertRaisesRegex(admission.AdmissionWait,'provider_feedback_unsupported'):
     admission.require_ordinary_provider({'resource_admission':selected})
 def test_standalone_run_still_requires_a_charged_controller_before_claim(self):
  import runner
  with tempfile.TemporaryDirectory() as temporary:
   active=object.__new__(runner.Runner);active.root=Path(temporary);active.store=SimpleNamespace(claim=Mock(),get=Mock())
   active.task_lock=Mock(side_effect=AssertionError('No task lock or claim'));active.resource_token=None
   with patch.object(scheduler,'configuration',return_value={'resource_admission':policy()}),patch.object(scheduler,'lease',return_value=ExitStack()),patch.object(scheduler,'pool_generation',return_value=ExitStack()),patch.object(scheduler,'require_charged_controller',side_effect=admission.AdmissionWait('resource_controller_required')),patch.object(admission,'Ledger',side_effect=AssertionError('Ledger forbidden')):
    with self.assertRaisesRegex(admission.AdmissionWait,'resource_controller_required'):active.run(ident(1))
   active.store.get.assert_not_called();active.store.claim.assert_not_called();self.assertIsNone(active.resource_token)
 def test_dispatch_bound_ordinary_worker_still_requires_exact_generation(self):
  db=SimpleNamespace(execute=Mock(return_value=SimpleNamespace(fetchone=lambda:{'owner':'{}'})))
  with patch.object(scheduler,'live',return_value=True),patch.object(scheduler,'worker_configuration',return_value={'codex_pipeline':True,'resource_admission':policy()}),patch.object(admission,'Ledger',side_effect=AssertionError('Ledger forbidden')):
   with self.assertRaisesRegex(ValueError,'generation/budget'):
    scheduler.execution_admission(Path('/unused'),SimpleNamespace(db=db),ident(1),ident(2))
  self.assertEqual(db.execute.call_count,1)
 def test_standalone_controller_rejects_a_forged_bound_scope_marker(self):
  import standalone
  args=SimpleNamespace(command='run',dispatch_token=None)
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary)/'runtime'
   with patch.object(scheduler,'configuration',return_value={'resource_admission':policy(),'pool_generation':'a'*64}),patch.dict(os.environ,{standalone.MARKER:'{}'}),patch.object(standalone,'Control',side_effect=AssertionError('No new scope')),patch.object(admission,'Ledger',side_effect=AssertionError('Ledger forbidden')):
    with self.assertRaisesRegex(admission.AdmissionWait,'resource_controller_required'):
     with standalone.controller(root,args):self.fail('Forged controller')
 def test_ordinary_probe_still_requires_a_charged_controller_before_ledger(self):
  active=SimpleNamespace(root=Path('/unused'),scheduler_settings={'resource_admission':policy()})
  with patch.object(scheduler,'require_charged_controller',side_effect=admission.AdmissionWait('resource_controller_required')),patch.object(admission,'Ledger',side_effect=AssertionError('Ledger forbidden')):
   with self.assertRaisesRegex(admission.AdmissionWait,'resource_controller_required'):
    with scheduler.resource_probe(active,ident(1)):self.fail('Uncharged probe')
 def test_ordinary_provider_authorization_requires_owned_metadata_and_private_outbox(self):
  rpc=Mock()
  with patch.object(admission,'Ledger',side_effect=AssertionError('Ledger forbidden')):
   with self.assertRaisesRegex(admission.AdmissionWait,'provider_feedback_unknown'):
    scheduler.authorize_task_provider(Path('/unused'),ident(1),{'resource_admission':policy()},ident(2),None,feedback_rpc=rpc)
  rpc.assert_not_called()
 def test_schema3_selects_resource_only_maximum_sessions_refusal(self):
  value=json.loads((ROOT/'runtime/private_codex/scheduler-config.json').read_text());value.pop('maximum_sessions');value['resource_admission']=policy()
  value['resource_admission']['reserve']=fixtures.fixtures.v(1000,2*1024**3,0,0);value['resource_admission']['capacity']['ram_bytes']=32*1024**3
  normalized=scheduler.validate_configuration(value);self.assertIsNone(normalized['maximum_sessions'])
  value['maximum_sessions']=8
  with self.assertRaises(ValueError):scheduler.validate_configuration(value)

if __name__=='__main__':unittest.main()
