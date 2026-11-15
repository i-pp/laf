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


# Added 2026-11-15: ordinary provider lifecycle, durable feedback and wire boundaries.

"""Contained real Store/Runner lifecycle with fake native frames and resources."""
from collections import deque
from contextlib import closing,ExitStack
import copy,json,os,sqlite3,sys,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime/private_codex/src')]
from source_test_isolation import require_isolated
require_isolated()
import test_provider_feedback as fixtures
import test_resource_admission as physical
import resource_admission as admission
import ordinary_provider_feedback as ordinary
import provider_control,runner,scheduler,storage,transport,resources


def setUpModule():
 global GUARDS
 GUARDS=[patch(target,side_effect=AssertionError('Synthetic ordinary fixtures forbid live effects'))
   for target in ('subprocess.Popen','socket.socket.bind','socket.socket.connect','threading.Thread.start')]
 for guard in GUARDS:guard.start()


def tearDownModule():
 for guard in reversed(GUARDS):guard.stop()


class Ordinary(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)/'runtime';self.root.mkdir(mode=0o700)
  for name in ('logs','workspace'):(self.root/name).mkdir()
  self.clock={'mono':100.,'wall':1000.};self.p=fixtures.policy()
  self.p['capacity']=physical.v(16000,64*1024**3,1000,16*1024**3);self.p['reserve']=physical.v(1000,2*1024**3,0,0)
  self.limits=resources.policy();self.settings={'resource_admission':self.p,'runner_resource_policy':self.limits,'worker':self.limits}
  def observation(rows,previous):
   sample,state=physical.observation(rows,previous);sample.update(at=self.clock['mono'],available=self.p['capacity']);return sample,state
  self.observation=observation
  for target,value in [('resource_admission.DIRECTORY',Path(temp.name)/'ledger'),('resource_admission.linux_observation',observation),
     ('resource_admission.time.monotonic',lambda:self.clock['mono']),('resource_admission.time.time',lambda:self.clock['wall'])]:
   guard=patch(target,value);guard.start();self.addCleanup(guard.stop)
  self.store=storage.Store(self.root/'tasks.sqlite');self.addCleanup(self.store.close);scheduler.initialize(self.store)
  self.task=self.store.submit('synthetic offline task',acceptance={'artifacts':[],'tests':[],'conditions':[{'kind':'result_equals','value':'synthetic result'}],'human':False})
  owner={'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'process':transport.proc_identity(os.getpid())}
  self.execution=self.store.claim(self.task,owner,{})
  self.token=str(uuid.uuid4())
  with closing(admission.Ledger(self.p)) as ledger:self.owner=ledger.register_owner(self.root,'standalone')
  scheduler.reserve_task_resources(self.root,self.task,self.settings,self.token,worker=False,owner=self.owner)
  self.r=object.__new__(runner.Runner);self.r.root=self.root;self.r.store=self.store;self.r.binary='/fixture/codex'
  self.r.development=self.r.signal_cancel=False;self.r.launcher=lambda *args:None
  self.r.resource_limits=self.limits;self.r.scheduler_settings=self.settings;self.r.resource_token=self.token;self.r.resource_task_id=self.task
  self.r.workroot=lambda _:self.root;self.r.launch_task=lambda *args:(['synthetic-codex'],{})
  self.control=SimpleNamespace(check=Mock());self.r.resource_control=lambda *args:self.control
  self.mode='stream';self.calls=[];self.sent=0;self.peer=None;self.on_wait=None;self.account_hook=None;self.wait_until=None;self.cleanup_exceeded=None
  self.negative={'codexErrorInfo':'rateLimitExceeded','message':'synthetic restriction'}
  self.terminal={'id':'fixture-turn','status':'failed','items':[],'error':self.negative}
 def advance(self,seconds):self.clock['mono']+=seconds;self.clock['wall']+=seconds
 def feedback(self):
  with closing(admission.Ledger(self.p)) as ledger:return ledger._feedback_state('chatgpt',fixtures.AUTH)
 def phase(self,token=None):
  with closing(admission.Ledger(self.p)) as ledger:return ledger.db.execute('SELECT phase FROM reservations WHERE token=?',(token or self.token,)).fetchone()[0]
 def records(self):return [dict(row) for row in self.store.db.execute('SELECT * FROM ordinary_provider_outbox ORDER BY rowid')]
 def run_fixture(self,timeout=120):
  case=self
  class Peer:
   def __init__(self,*args,**kwargs):
    case.peer=self;self.resources=kwargs['resources'];self.provider_epoch=0;self.provider_feedback_failed=False
    self.notifications=deque();self.on_provider_message=None;self.before_turn_send=None;self.closing=False;self.started=False;self.after_start_accounts=0;self.finished=False
    kwargs['on_observed']({99999999:'42'})
   def emit(self,message):
    if message.get('method','').startswith('account/'):self.provider_epoch+=1
    try:
     if self.on_provider_message is not None and self.on_provider_message(copy.deepcopy(message)) is False:return
    except Exception as error:self.provider_feedback_failed=True;raise transport.ProtocolError('Synthetic provider persistence failure') from error
    self.notifications.append(copy.deepcopy(message))
   def call(self,method,params,timeout=30):
    case.calls.append((method,copy.deepcopy(params)))
    if method=='initialize':
     if case.mode=='initial_account_event':self.emit({'method':'account/updated','params':{}})
     return {}
    if method=='account/read':
     if self.started:self.after_start_accounts+=1
     if case.account_hook:case.account_hook(self)
     return copy.deepcopy(fixtures.fixtures.ACCOUNT)
    if method=='account/rateLimits/read':return fixtures.usage(100 if case.wait_until is not None and case.clock['mono']<case.wait_until else 0)
    if method=='thread/start':
     self.started=True
     return {'thread':{'id':'fixture-thread'},'modelProvider':'openai','approvalPolicy':'never','sandbox':{'type':'readOnly','networkAccess':False}}
    if method=='turn/start':
     if self.before_turn_send is not None:self.before_turn_send()
     case.sent+=1
     if case.mode=='lost_ack':raise EOFError('synthetic lost ACK')
     if case.mode=='rpc429':raise transport.RpcError('synthetic refusal',code=429,method=method,correlated=True)
     if case.mode in ('early_failed','early_retry'):
      message=({'method':'turn/completed','params':{'threadId':'fixture-thread','turn':case.terminal}} if case.mode=='early_failed' else
        {'method':'error','params':{'threadId':'fixture-thread','turnId':'fixture-turn','willRetry':True,'error':case.negative}})
      self.emit(message)
     if case.mode=='ack_failed':return {'turn':copy.deepcopy(case.terminal)}
     if case.mode=='ack_completed':return {'turn':{'id':'fixture-turn','status':'completed','error':None,'items':[{'type':'agentMessage','text':'{"outcome":"success","result":"synthetic result"}'}]}}
     return {'turn':{'id':'fixture-turn','status':'inProgress','items':[],'error':None}}
    if method=='thread/read':return {'thread':{'id':'fixture-thread','status':{'type':'idle'}}}
    if method in ('thread/unsubscribe','turn/interrupt'):return {}
    raise AssertionError('Unexpected synthetic native method '+method)
   def send(self,*args):pass
   def observe_children(self):pass
   def record(self,*args):pass
   def next_message(self,budget):
    if not self.notifications and case.sent==0:
     case.advance(budget)
     if case.on_wait:case.on_wait()
     return None
    if not self.notifications and not self.finished:
     self.finished=True
     if case.mode=='live_failure':self.emit({'method':'error','params':{'threadId':'fixture-thread','turnId':'fixture-turn','willRetry':True,'error':case.negative}})
     self.emit({'method':'item/completed','params':{'threadId':'fixture-thread','turnId':'fixture-turn','item':{'type':'agentMessage','text':'{"outcome":"success","result":"synthetic result"}'}}})
     self.emit({'method':'turn/completed','params':{'threadId':'fixture-thread','turn':{'id':'fixture-turn','status':'completed','items':[],'error':None}}})
    return self.notifications.popleft() if self.notifications else None
   def close(self):return {'exit_code':0,'survivors':[],'resources':{'confirmed':True,'released':True,'exceeded':case.cleanup_exceeded}}
  with patch.object(runner,'Process',Peer),patch.object(runner,'binary_version',return_value='synthetic'):
   return self.r._execute(self.task,timeout)
 def other(self):
  task=self.store.submit('another synthetic task');token=str(uuid.uuid4())
  scheduler.reserve_task_resources(self.root,task,self.settings,token,worker=False,owner=self.owner)
  return task,token
 def authorize_other(self):
  task,token=self.other()
  return scheduler.authorize_task_provider(self.root,task,self.settings,token,None,feedback_rpc=lambda m,p:self.peer.call(m,p),feedback_client=self.peer,feedback_store=self.store)
 def bind_for_outbox(self):
  self.store.execution_update(self.execution,thread_id='fixture-thread',dispatched=1)
  self.store.update(self.task,session_id='fixture-thread')
  gate=ordinary.OrdinaryFeedback(self.root,self.store,self.settings,self.task,self.token)
  gate.bind({'id':'fixture-turn','status':'inProgress','items':[],'error':None});return gate

 def test_normal_stream_keeps_one_execution_and_completes(self):
  result=self.run_fixture();self.assertEqual(result['state'],'WAITING_FOR_HUMAN');self.assertEqual(result['codex_status'],'SUCCEEDED');self.assertEqual(self.sent,1)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM executions').fetchone()[0],1);self.assertEqual(result['active_execution_id'],self.execution)
 def test_wait_preserves_execution_grant_and_reports_a_reason_then_dispatches_once(self):
  self.wait_until=110.;seen=[]
  def waiting():
   task=self.store.get(self.task);execution=self.store.execution(self.execution)
   seen.append(task['scheduler_wait_reason']);self.assertEqual(task['state'],'RUNNING');self.assertEqual(execution['phase'],'WAITING_PROVIDER')
   self.assertEqual(execution['dispatched'],0);self.assertEqual(self.phase(),'HELD')
  self.on_wait=waiting;result=self.run_fixture();self.assertTrue(seen);self.assertEqual(result['state'],'WAITING_FOR_HUMAN');self.assertEqual(result['codex_status'],'SUCCEEDED');self.assertEqual(self.sent,1)
  self.assertGreater(self.control.check.call_count,0);self.assertEqual(self.store.db.execute('SELECT count(*) FROM executions').fetchone()[0],1)
 def test_cancel_during_wait_never_sends_a_turn_and_can_release_after_cleanup(self):
  self.wait_until=200.;self.on_wait=lambda:self.store.update(self.task,cancel_requested=1)
  result=self.run_fixture();self.assertEqual(result['state'],'CANCELLED');self.assertEqual(self.sent,0);self.assertEqual(self.store.execution(self.execution)['dispatched'],0)
  self.assertTrue(scheduler.finish_task_resources(self.root,self.store,self.task,self.settings,token=self.token,worker_released=True));self.assertEqual(self.phase(),'RELEASED')
 def test_timeout_during_wait_never_sends_or_requeues(self):
  self.wait_until=200.;result=self.run_fixture(timeout=1);self.assertEqual(result['state'],'TIMED_OUT');self.assertEqual(self.sent,0)
  self.assertEqual(self.store.execution(self.execution)['dispatched'],0)
  with self.assertRaises(ValueError):self.store.claim(self.task,{}, {})
 def test_cancel_inside_the_final_account_read_is_checked_before_wire_checkpoint(self):
  def cancel(peer):
   if peer.after_start_accounts==3:self.store.update(self.task,cancel_requested=1)
  self.account_hook=cancel;result=self.run_fixture();self.assertEqual(result['state'],'CANCELLED');self.assertEqual(self.sent,0)
  self.assertEqual(self.store.execution(self.execution)['dispatched'],0)
 def test_delayed_post_authorization_commit_refuses_the_actual_send(self):
  original=self.store.provider_wait
  def delayed(*args):
   result=original(*args)
   if args[-1] is None and self.peer.started:self.advance(self.p['max_age_seconds']+1)
   return result
  with patch.object(self.store,'provider_wait',side_effect=delayed):result=self.run_fixture()
  self.assertEqual(result['state'],'UNKNOWN');self.assertEqual(self.sent,0);self.assertEqual(self.store.execution(self.execution)['dispatched'],1)
 def test_late_account_notification_epoch_refuses_the_actual_send(self):
  original=self.store.provider_wait
  def notify(*args):
   result=original(*args)
   if args[-1] is None and self.peer.started:self.peer.provider_epoch+=1
   return result
  with patch.object(self.store,'provider_wait',side_effect=notify):result=self.run_fixture()
  self.assertEqual(result['state'],'UNKNOWN');self.assertEqual(self.sent,0)
 def test_immediate_terminal_ack_delivers_restriction_and_allows_only_grant_cleanup(self):
  self.mode='ack_failed';result=self.run_fixture();self.assertEqual(result['state'],'FAILED');self.assertTrue(self.feedback()['blocked'])
  self.assertEqual(self.sent,1);self.assertTrue(scheduler.finish_task_resources(self.root,self.store,self.task,self.settings,token=self.token,worker_released=True))
  self.assertEqual(self.phase(),'RELEASED')
  with self.assertRaises(admission.AdmissionWait):self.authorize_other()
 def test_immediate_successful_ack_does_not_wait_for_a_second_completion(self):
  self.mode='ack_completed';result=self.run_fixture();self.assertEqual(result['state'],'WAITING_FOR_HUMAN');self.assertEqual(result['codex_status'],'SUCCEEDED');self.assertEqual(self.sent,1)
 def test_early_completion_is_delivered_once_after_ack_and_not_again_on_dequeue(self):
  self.mode='early_failed';result=self.run_fixture();self.assertEqual(result['state'],'FAILED');self.assertTrue(self.feedback()['blocked'])
  rows=self.records();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['phase'],'ACKED')
 def test_early_retry_error_remains_a_shared_restriction_after_task_success(self):
  self.mode='early_retry';result=self.run_fixture();self.assertEqual(result['state'],'WAITING_FOR_HUMAN');self.assertEqual(result['codex_status'],'SUCCEEDED');self.assertTrue(self.feedback()['blocked'])
  self.assertEqual(len(self.records()),1)
 def test_valid_correlated_rejection_keeps_attempt_evidence_without_replaying(self):
  self.mode='rpc429';result=self.run_fixture();self.assertEqual(result['state'],'FAILED');self.assertEqual(self.sent,1)
  execution=self.store.execution(self.execution);self.assertEqual(execution['dispatched'],1);self.assertIsNone(execution['turn_id'])
  self.assertTrue(self.feedback()['blocked']);self.assertTrue(scheduler.finish_task_resources(self.root,self.store,self.task,self.settings,token=self.token,worker_released=True))
  with self.assertRaises(ValueError):self.store.claim(self.task,{}, {})
 def test_lost_ack_stays_unknown_charged_and_cannot_be_claimed_again(self):
  self.mode='lost_ack';result=self.run_fixture();self.assertEqual(result['state'],'UNKNOWN');self.assertEqual(self.sent,1)
  self.assertFalse(scheduler.finish_task_resources(self.root,self.store,self.task,self.settings,token=self.token,worker_released=True));self.assertEqual(self.phase(),'HELD')
  with self.assertRaises(ValueError):self.store.claim(self.task,{}, {})
 def test_feedback_failure_overrides_resource_limit_failure_and_retains_grant(self):
  self.mode='live_failure';self.cleanup_exceeded='memory_bytes';original=provider_control.record
  def fail(*args,**kwargs):
   if kwargs.get('delivery_receipt') is not None:raise sqlite3.OperationalError('synthetic ledger failure')
   return original(*args,**kwargs)
  with patch.object(provider_control,'record',side_effect=fail):result=self.run_fixture()
  self.assertEqual(result['state'],'UNKNOWN');self.assertEqual(self.phase(),'HELD');self.assertTrue(any(r['phase']=='PENDING' for r in self.records()))
  self.assertTrue(ordinary.flush(self.root,self.store,self.settings));self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.store.get(self.task)['state'],'UNKNOWN')
 def test_waiting_owner_loss_normalizes_to_unknown_without_another_execution(self):
  self.store.provider_wait(self.task,self.execution,'resource_provider_backoff');self.store.recover(self.task)
  task=self.store.get(self.task);execution=self.store.execution(self.execution)
  self.assertEqual(task['state'],'UNKNOWN');self.assertEqual(task['codex_status'],'UNKNOWN');self.assertEqual(execution['phase'],'UNKNOWN')
  self.assertEqual(execution['dispatched'],0);self.assertEqual(self.phase(),'HELD')
 def test_wait_reason_is_visible_in_both_facades_without_private_payloads(self):
  import daemon,legacy_tool_adapter,mcp_adapter
  self.store.provider_wait(self.task,self.execution,'resource_provider_backoff');task=self.store.get(self.task)
  d=object.__new__(daemon.Daemon);d.store=self.store
  self.assertEqual(d.task_wait_reason(task),'resource_provider_backoff')
  for facade in (legacy_tool_adapter,mcp_adapter):
   view=facade.task_view(task);self.assertEqual(view['scheduler_wait_reason'],'resource_provider_backoff')
   self.assertNotIn('prompt',view);self.assertNotIn('error',view)
 def test_pending_publication_blocks_another_authorization_before_metadata_reads(self):
  gate=self.bind_for_outbox();gate.put('terminal','fixture-turn');task,token=self.other();rpc=Mock()
  with patch.object(provider_control,'record',side_effect=sqlite3.OperationalError('synthetic write failure')):
   with self.assertRaises(admission.AdmissionWait):scheduler.authorize_task_provider(self.root,task,self.settings,token,None,feedback_rpc=rpc,feedback_client=SimpleNamespace(provider_epoch=0),feedback_store=self.store)
  rpc.assert_not_called();self.assertEqual(self.phase(),'HELD')
 def test_two_new_live_errors_after_valid_recovery_have_distinct_receipts(self):
  gate=self.bind_for_outbox();event={'method':'error','params':{'threadId':'fixture-thread','turnId':'fixture-turn','willRetry':True,'error':self.negative}}
  gate.capture(event);self.assertTrue(self.feedback()['blocked']);self.advance(20)
  revision=None
  with closing(admission.Ledger(self.p)) as ledger:revision=ledger.provider_revision(self.root,self.task,self.token,'chatgpt')
  provider_control.record(self.settings,self.root,self.task,self.token,fixtures.AUTH,admission.provider_snapshot(fixtures.usage(0)),kind='snapshot',revision=revision,fresh=True,observed_at=self.clock['mono'])
  self.assertFalse(self.feedback()['blocked']);gate.capture(event);self.assertTrue(self.feedback()['blocked'])
  rows=self.records();self.assertEqual(len(rows),2);self.assertNotEqual(rows[0]['id'],rows[1]['id'])


 def probe(self,*,role='standalone',kind='reconciliation',scoped=True):
  task,token=str(uuid.uuid4()),str(uuid.uuid4());unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
  descriptor={'unit':unit,'expected_cgroup':'/sys/fs/cgroup/agentorchestrator.slice/'+unit}
  with closing(admission.Ledger(self.p)) as ledger:
   owner=ledger.register_owner(self.root,role)
   ledger.reserve(self.root,task,token,{'kind':kind,'provider':None,'resources':physical.v(100,128*1024**2,0,0)},self.observation,owner=owner,scopes=[descriptor] if scoped else [])
  return {'task':task,'token':token},owner
 def expire_original_owner(self):
  with closing(admission.Ledger(self.p)) as ledger:
   identity=json.loads(ledger.db.execute('SELECT identity FROM admission_owners WHERE id=?',(self.owner,)).fetchone()[0]);identity['boot_id']=str(uuid.uuid4())
   with ledger.db:ledger.db.execute('UPDATE admission_owners SET identity=? WHERE id=?',(json.dumps(identity),self.owner))
 def terminal_receipt(self,gate):
  task,execution=gate.current()
  return {'generation':admission.generation(self.p),'binding':ordinary.binding(task,execution),'thread':'fixture-thread','turn':'fixture-turn','event':'terminal'}
 def recover_account(self,*,task=None,token=None):
  self.advance(20)
  task=task or self.task;token=token or self.token
  with closing(admission.Ledger(self.p)) as ledger:
   revision=ledger.provider_revision(self.root,task,token,'chatgpt')
   ledger.observe_provider(self.root,task,token,'chatgpt',fixtures.AUTH,admission.provider_snapshot(fixtures.usage(0)),kind='snapshot',revision=revision,fresh=True,observed_at=self.clock['mono'])
 def test_recovery_probe_can_publish_old_owner_negative_without_releasing_the_subject(self):
  gate=self.bind_for_outbox();gate.terminal(self.terminal,history=True);self.expire_original_owner();authority,owner=self.probe()
  self.assertFalse(gate.flush());gate.authority=authority;self.assertTrue(gate.flush())
  self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.phase(),'HELD');self.assertEqual(self.phase(authority['token']),'HELD')
  with closing(admission.Ledger(self.p)) as ledger:
   evidence=[json.loads(row[0]) for row in ledger.db.execute('SELECT evidence FROM resource_reconciliations')]
  self.assertEqual(sum(e['kind']=='provider_terminal_restriction' for e in evidence),1)
  self.assertEqual(sum(e['kind']=='provider_feedback_authority' for e in evidence),1)
 def test_new_probe_tokens_do_not_change_old_terminal_fact_identity_after_recovery(self):
  gate=self.bind_for_outbox();gate.terminal(self.terminal,history=True);self.expire_original_owner();first,owner=self.probe()
  gate.authority=first;self.assertTrue(gate.flush());self.recover_account(task=first['task'],token=first['token']);before=self.feedback()
  second,owner=self.probe();gate.authority=second;gate.terminal(self.terminal,history=True);self.assertTrue(gate.flush())
  self.assertEqual(self.feedback(),before);self.assertEqual(self.phase(),'HELD')
 def test_receipt_commit_before_private_ack_replays_safely_under_another_probe(self):
  gate=self.bind_for_outbox();gate.terminal(self.terminal,history=True);record=self.records()[0]
  self.expire_original_owner();first,owner=self.probe()
  provider_control.record(self.settings,self.root,self.task,self.token,None,provider_control.observation('block'),kind='rejection',delivery_receipt=self.terminal_receipt(gate),authority=first)
  self.recover_account(task=first['task'],token=first['token']);before=self.feedback();second,owner=self.probe();gate.authority=second
  self.assertTrue(gate.flush());self.assertEqual(self.feedback(),before);self.assertEqual(self.records()[0]['phase'],'ACKED');self.assertEqual(self.phase(),'HELD')
 def test_metadata_authority_without_a_scope_cannot_deliver_a_negative(self):
  gate=self.bind_for_outbox();gate.terminal(self.terminal,history=True);self.expire_original_owner();authority,owner=self.probe(scoped=False);gate.authority=authority
  self.assertFalse(gate.flush());self.assertFalse(self.feedback()['blocked']);self.assertEqual(self.records()[0]['phase'],'PENDING')
 def test_wrong_authority_kind_or_role_cannot_borrow_recovery_permission(self):
  gate=self.bind_for_outbox();gate.terminal(self.terminal,history=True);self.expire_original_owner()
  for role,kind in (('daemon','reconciliation'),('standalone','local_control')):
   with self.subTest(role=role,kind=kind):
    authority,owner=self.probe(role=role,kind=kind);gate.authority=authority
    self.assertFalse(gate.flush());self.assertFalse(self.feedback()['blocked'])
 def test_another_live_actor_cannot_borrow_a_recovery_grant(self):
  gate=self.bind_for_outbox();gate.terminal(self.terminal,history=True);self.expire_original_owner();authority,owner=self.probe()
  with closing(admission.Ledger(self.p)) as ledger:
   identity=json.loads(ledger.db.execute('SELECT identity FROM admission_owners WHERE id=?',(owner,)).fetchone()[0]);identity['pid_namespace']='pid:[999999999]'
   with ledger.db:ledger.db.execute('UPDATE admission_owners SET identity=? WHERE id=?',(json.dumps(identity),owner))
  gate.authority=authority
  with patch.object(admission,'owner_live',return_value=True):self.assertFalse(gate.flush())
  self.assertFalse(self.feedback()['blocked']);self.assertEqual(self.phase(),'HELD')
 def test_recovery_authority_never_allows_a_favorable_snapshot_or_uncharged_write(self):
  gate=self.bind_for_outbox();authority,owner=self.probe()
  with closing(admission.Ledger(self.p)) as ledger:
   with self.assertRaises(ValueError):ledger.observe_provider(self.root,self.task,self.token,'chatgpt',fixtures.AUTH,admission.provider_snapshot(fixtures.usage(0)),kind='snapshot',delivery_receipt=self.terminal_receipt(gate),authority=authority)
   with self.assertRaises(ValueError):ledger.observe_provider(self.root,self.task,self.token,'chatgpt',None,provider_control.observation('block'),kind='rejection',authority=authority)
  self.assertFalse(self.feedback()['blocked']);self.assertEqual(self.phase(),'HELD')
 def test_history_terminal_fact_does_not_repeat_a_previously_delivered_negative(self):
  gate=self.bind_for_outbox();gate.terminal(self.terminal,history=True);self.assertTrue(gate.flush());self.recover_account();before=self.feedback()
  gate.terminal(self.terminal,history=True);self.assertTrue(gate.flush());self.assertEqual(self.feedback(),before)
 def test_malformed_history_retains_uncertainty_until_an_exact_valid_terminal(self):
  gate=self.bind_for_outbox();gate.terminal({**self.terminal,'error':42},history=True)
  self.assertFalse(gate.flush());self.assertEqual(self.phase(),'HELD');self.assertFalse(self.feedback()['blocked'])
  gate.terminal(self.terminal,history=True);self.assertTrue(gate.flush());self.assertTrue(self.feedback()['blocked'])
 def test_binding_change_after_staging_prevents_delivery_and_local_ack(self):
  gate=self.bind_for_outbox();gate.terminal(self.terminal,history=True)
  with self.store.db:self.store.db.execute("UPDATE tasks SET role='reviewer' WHERE id=?",(self.task,))
  self.assertFalse(gate.flush());self.assertEqual(self.phase(),'HELD');self.assertFalse(self.feedback()['blocked'])
 def test_correlation_proof_without_an_acknowledged_outbox_cannot_release_a_grant(self):
  self.store.execution_update(self.execution,thread_id='fixture-thread',dispatched=1,phase='FAILED',terminal=json.dumps({'status':'failed','error':{'kind':'correlated_rpc_rejection','method':'turn/start','code':429}}),cleanup=json.dumps({'survivors':[],'resources':{'released':True,'confirmed':True}}),processes=json.dumps({'99999999':'42'}))
  self.store.transition(self.task,'RUNNING','FAILED','synthetic forged unsupported proof')
  self.assertFalse(scheduler.finish_task_resources(self.root,self.store,self.task,self.settings,token=self.token,worker_released=True));self.assertEqual(self.phase(),'HELD')


 def inspect(self,history=None,*,wrong_thread=False):
  import recovery
  case=self;authority,owner=self.probe();self.r.resource_grant_task_id=authority['task'];self.r.resource_token=authority['token'];calls=[]
  class ProbePeer:
   def __init__(self,*args,**kwargs):self.provider_epoch=0;self.on_provider_message=None
   def call(self,method,params,timeout=30):
    calls.append(method)
    if method=='initialize':return {}
    if method=='account/read':return copy.deepcopy(fixtures.fixtures.ACCOUNT)
    if method=='thread/read':return {'thread':{'id':'other-thread' if wrong_thread else 'fixture-thread','status':{'type':'idle'}}}
    if method=='thread/turns/list':return {'data':[copy.deepcopy(history or case.terminal)],'nextCursor':None}
    raise AssertionError('Recovery may never send a turn: '+method)
   def send(self,*args):pass
   def close(self):return {'exit_code':0,'survivors':[],'resources':{'released':True,'confirmed':True}}
  with patch.object(recovery,'Process',ProbePeer):
   task=self.store.get(self.task);execution=self.store.execution(self.execution)
   facts=recovery.inspect_session(self.r,task,execution,None)
  return facts,authority,calls
 def test_real_history_recovery_publishes_restriction_before_attesting_terminal(self):
  self.bind_for_outbox();self.store.recover(self.task);facts,authority,calls=self.inspect()
  self.assertTrue(facts['exists']);self.assertTrue(facts['ended']);self.assertTrue(facts['latest_turn_matches']);self.assertTrue(self.feedback()['blocked'])
  self.assertIn('thread/turns/list',calls);self.assertEqual(self.phase(),'HELD');self.assertTrue(all(row['phase']=='ACKED' for row in self.records()))
 def test_real_history_recovery_uses_current_probe_after_original_owner_dies(self):
  self.bind_for_outbox();self.store.recover(self.task);self.expire_original_owner();facts,authority,calls=self.inspect()
  self.assertTrue(facts['ended']);self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.phase(),'HELD')
  self.assertEqual(self.phase(authority['token']),'HELD');self.assertIn('provider_account_observed_at',facts)
 def test_repeated_real_history_under_new_probe_does_not_reblock_after_recovery(self):
  self.bind_for_outbox();self.store.recover(self.task);self.expire_original_owner();facts,first,calls=self.inspect()
  self.assertTrue(facts['ended']);self.recover_account(task=first['task'],token=first['token']);before=self.feedback()
  facts,second,calls=self.inspect();self.assertTrue(facts['ended']);self.assertNotEqual(first['token'],second['token']);self.assertEqual(self.feedback(),before)
 def test_foreign_history_thread_never_publishes_a_false_restriction(self):
  self.bind_for_outbox();self.store.recover(self.task);facts,authority,calls=self.inspect(wrong_thread=True)
  self.assertFalse(facts['ended']);self.assertFalse(facts['exists']);self.assertFalse(self.feedback()['blocked']);self.assertEqual(self.phase(),'HELD')
 def test_foreign_history_turn_never_publishes_a_false_restriction(self):
  self.bind_for_outbox();self.store.recover(self.task);facts,authority,calls=self.inspect({**self.terminal,'id':'other-turn'})
  self.assertFalse(facts['ended']);self.assertFalse(self.feedback()['blocked']);self.assertEqual(self.phase(),'HELD')
 def test_lost_ack_cannot_be_recovered_by_null_turn_or_list_order(self):
  self.store.execution_update(self.execution,thread_id='fixture-thread',dispatched=1);self.store.update(self.task,session_id='fixture-thread');self.store.recover(self.task)
  facts,authority,calls=self.inspect({**self.terminal,'id':None})
  self.assertFalse(facts['ended']);self.assertEqual(facts['reason'],'turn_id_not_recorded');self.assertFalse(calls);self.assertEqual(self.phase(),'HELD')
 def test_shared_commit_then_binding_change_keeps_exact_local_ack_pending(self):
  gate=self.bind_for_outbox();gate.terminal(self.terminal,history=True);original=provider_control.record
  def changed(*args,**kwargs):
   result=original(*args,**kwargs)
   with self.store.db:self.store.db.execute("UPDATE tasks SET role='reviewer' WHERE id=?",(self.task,))
   return result
  with patch.object(provider_control,'record',side_effect=changed):self.assertFalse(gate.flush())
  self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.records()[0]['phase'],'PENDING');self.assertEqual(self.phase(),'HELD')
 def test_early_mismatched_retry_error_is_never_published_on_another_ack(self):
  self.store.execution_update(self.execution,thread_id='fixture-thread',dispatched=1)
  gate=ordinary.OrdinaryFeedback(self.root,self.store,self.settings,self.task,self.token)
  gate.capture({'method':'error','params':{'threadId':'fixture-thread','turnId':'other-turn','willRetry':True,'error':self.negative}})
  gate.bind({'id':'fixture-turn','status':'inProgress','items':[],'error':None})
  self.assertFalse(self.feedback()['blocked']);self.assertEqual(self.records()[0]['phase'],'IGNORED')
 def test_lost_early_retry_overflow_is_not_resolved_by_terminal_null(self):
  self.store.execution_update(self.execution,thread_id='fixture-thread',dispatched=1)
  gate=ordinary.OrdinaryFeedback(self.root,self.store,self.settings,self.task,self.token)
  for native in [*('other-'+str(n) for n in range(33)),'fixture-turn']:
   gate.capture({'method':'error','params':{'threadId':'fixture-thread','turnId':native,'willRetry':True,'error':self.negative}})
  with self.assertRaises(admission.AdmissionWait):gate.bind({'id':'fixture-turn','status':'inProgress','items':[],'error':None})
  gate.terminal({'id':'fixture-turn','status':'completed','items':[],'error':None},history=True)
  self.assertFalse(gate.flush());self.assertFalse(self.feedback()['blocked']);self.assertEqual(self.phase(),'HELD')
  gate.terminal(self.terminal,history=True);self.assertTrue(gate.flush());self.assertTrue(self.feedback()['blocked'])
 def test_provider_wait_cannot_relabel_a_dispatched_or_bound_execution(self):
  self.bind_for_outbox()
  with self.assertRaises(ValueError):self.store.provider_wait(self.task,self.execution,'resource_provider_backoff')
  self.assertEqual(self.store.execution(self.execution)['dispatched'],1);self.assertEqual(self.phase(),'HELD')


 def test_transient_local_enqueue_loss_blocks_an_unrelated_workspace(self):
  workspace=str(uuid.uuid4())
  with self.store.db:
   self.store.db.execute('UPDATE tasks SET workspace_id=? WHERE id=?',(workspace,self.task));self.store.db.execute('UPDATE executions SET workspace_id=? WHERE id=?',(workspace,self.execution))
  self.mode='live_failure';original=ordinary.OrdinaryFeedback.put;failed=[]
  def put(gate,event,turn,**kwargs):
   if event=='live_error' and not failed:failed.append(True);raise sqlite3.OperationalError('synthetic one-off capture loss')
   return original(gate,event,turn,**kwargs)
  with patch.object(ordinary.OrdinaryFeedback,'put',put):result=self.run_fixture()
  self.assertTrue(failed);self.assertEqual(result['state'],'UNKNOWN');self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.phase(),'HELD')
  with self.assertRaises(admission.AdmissionWait):self.authorize_other()
  self.assertTrue(any(json.loads(r['body'])['event']=='capture_unknown' for r in self.records()))
 def test_metadata_capture_failure_before_thread_uses_an_explicit_host_fact(self):
  self.mode='initial_account_event'
  with patch.object(provider_control,'notification',side_effect=sqlite3.OperationalError('synthetic metadata capture failure')):result=self.run_fixture()
  self.assertEqual(result['state'],'UNKNOWN');self.assertEqual(self.sent,0);self.assertTrue(self.feedback()['blocked'])
  rows=self.records();self.assertEqual(len(rows),1);body=json.loads(rows[0]['body']);self.assertEqual(body['event'],'capture_unknown')
  self.assertIsNone(body['thread']);self.assertIsNone(body['turn']);self.assertEqual(rows[0]['phase'],'ACKED')
  with self.assertRaises(admission.AdmissionWait):self.authorize_other()
 def marker_write_failure(self,*,recover_second=False):
  case=self;original=self.store.db
  class BrokenOwner:
   count=0
   def __enter__(self):original.__enter__();return self
   def __exit__(self,*args):return original.__exit__(*args)
   def execute(self,sql,args=()):
    if sql.startswith('UPDATE executions SET owner='):
     self.count+=1
     if recover_second and self.count==2:case.recover_account()
     raise sqlite3.OperationalError('synthetic marker write failure')
    return original.execute(sql,args)
  return BrokenOwner()
 def test_shared_fallback_and_later_private_ack_reuse_one_event_after_recovery(self):
  self.mode='initial_account_event';original_db=self.store.db;broken=self.marker_write_failure(recover_second=True)
  original_retry=ordinary.OrdinaryFeedback.retry_capture_failures;gates=[]
  def retry(gate):gates.append(gate);return original_retry(gate)
  self.store.db=broken
  try:
   with patch.object(provider_control,'notification',side_effect=sqlite3.OperationalError('synthetic metadata failure')),patch.object(ordinary.OrdinaryFeedback,'retry_capture_failures',retry):result=self.run_fixture()
  finally:self.store.db=original_db
  self.assertEqual(result['state'],'UNKNOWN');self.assertFalse(self.feedback()['blocked']);before=self.feedback();self.assertTrue(gates[-1].capture_faults)
  gates[-1].retry_capture_failures();self.assertEqual(self.feedback(),before);self.assertFalse(gates[-1].capture_faults)
  self.assertEqual(self.phase(),'HELD');self.assertTrue(all(row['phase']=='ACKED' for row in self.records()))
  owner=json.loads(self.store.execution(self.execution)['owner']);self.assertEqual(owner.get('provider_capture_unknown'),{})
 def test_shared_failure_retains_a_durable_marker_until_publication(self):
  self.mode='initial_account_event';original=provider_control.record
  def record(*args,**kwargs):
   if (kwargs.get('delivery_receipt') or {}).get('event')=='capture_unknown':raise sqlite3.OperationalError('synthetic shared failure')
   return original(*args,**kwargs)
  with patch.object(provider_control,'notification',side_effect=sqlite3.OperationalError('synthetic metadata failure')),patch.object(provider_control,'record',side_effect=record):
   result=self.run_fixture();self.assertEqual(result['state'],'UNKNOWN')
   self.assertTrue(json.loads(self.store.execution(self.execution)['owner'])['provider_capture_unknown'])
   with self.assertRaises(admission.AdmissionWait):self.authorize_other()
  self.assertTrue(ordinary.flush(self.root,self.store,self.settings));self.assertTrue(self.feedback()['blocked'])
  self.assertEqual(json.loads(self.store.execution(self.execution)['owner']).get('provider_capture_unknown'),{})
 def test_both_writes_unavailable_preserve_the_event_ids_in_final_checkpoint(self):
  self.mode='initial_account_event';original_db=self.store.db;self.store.db=self.marker_write_failure();original=provider_control.record
  def record(*args,**kwargs):
   if (kwargs.get('delivery_receipt') or {}).get('event')=='capture_unknown':raise sqlite3.OperationalError('synthetic shared failure')
   return original(*args,**kwargs)
  try:
   with patch.object(provider_control,'notification',side_effect=sqlite3.OperationalError('synthetic metadata failure')),patch.object(provider_control,'record',side_effect=record):result=self.run_fixture()
  finally:self.store.db=original_db
  self.assertEqual(result['state'],'UNKNOWN');self.assertEqual(self.phase(),'HELD');self.assertFalse(self.feedback()['blocked'])
  status=self.store.execution(self.execution)['codex_status'];self.assertTrue(status.startswith('PROVIDER_CAPTURE_UNKNOWN:'))
  self.assertTrue(ordinary.flush(self.root,self.store,self.settings));self.assertTrue(self.feedback()['blocked'])
  self.assertEqual(self.store.execution(self.execution)['codex_status'],'UNKNOWN')
  with self.assertRaises(admission.AdmissionWait):self.authorize_other()
 def preintent(self,method):
  emitted=[]
  def history(peer):
   if peer.after_start_accounts==1 and not emitted:
    emitted.append(True);self.assertEqual(self.store.execution(self.execution)['dispatched'],0)
    params={'threadId':'fixture-thread','turnId':'historical-other-turn','willRetry':True,'error':self.negative}
    if method=='turn/completed':params={'threadId':'fixture-thread','turn':{**self.terminal,'id':'historical-other-turn'}}
    if method=='item/completed':params['item']={'type':'agentMessage','text':'unrelated old result'}
    peer.emit({'method':method,'params':params})
  self.account_hook=history;result=self.run_fixture();self.assertTrue(emitted);self.assertEqual(result['state'],'WAITING_FOR_HUMAN')
  self.assertEqual(self.sent,1);self.assertFalse(self.records());self.assertFalse(self.feedback()['blocked'])
 def test_preintent_error_is_not_evidence_for_this_execution(self):self.preintent('error')
 def test_preintent_completion_is_not_evidence_for_this_execution(self):self.preintent('turn/completed')
 def test_preintent_item_is_not_evidence_for_this_execution(self):self.preintent('item/completed')
 def test_preintent_usage_is_not_evidence_for_this_execution(self):self.preintent('thread/tokenUsage/updated')
 def test_account_notifications_are_still_observed_before_dispatch(self):
  emitted=[];waited=[]
  def notify(peer):
   if peer.after_start_accounts==1 and not emitted:
    emitted.append(True);peer.emit({'method':'account/rateLimits/updated','params':fixtures.usage(100)})
  self.account_hook=notify;self.on_wait=lambda:waited.append(self.clock['mono'])
  result=self.run_fixture();self.assertTrue(emitted and waited);self.assertEqual(self.sent,1);self.assertEqual(result['state'],'WAITING_FOR_HUMAN')
  self.assertGreaterEqual(self.clock['mono'],110)
 def test_host_capture_fact_rejects_fake_native_ids_and_positive_observations(self):
  gate=self.bind_for_outbox();receipt={**self.terminal_receipt(gate),'event':'capture_unknown','event_id':str(uuid.uuid4()),'thread':None,'turn':None}
  with closing(admission.Ledger(self.p)) as ledger:
   with self.assertRaises(ValueError):ledger.observe_provider(self.root,self.task,self.token,'chatgpt',None,provider_control.observation('unknown'),kind='unknown',delivery_receipt={**receipt,'thread':'invented'})
   with self.assertRaises(ValueError):ledger.observe_provider(self.root,self.task,self.token,'chatgpt',fixtures.AUTH,admission.provider_snapshot(fixtures.usage(0)),kind='snapshot',delivery_receipt=receipt)
  self.assertFalse(self.feedback()['blocked'])
 def test_released_host_subject_requires_a_current_charged_probe(self):
  gate=ordinary.OrdinaryFeedback(self.root,self.store,self.settings,self.task,self.token)
  with closing(admission.Ledger(self.p)) as ledger:ledger.release(self.root,self.task,self.token,local_released=True,provider_released=True)
  receipt={'generation':admission.generation(self.p),'binding':gate.capture_base['binding'],'thread':None,'turn':None,'event':'capture_unknown','event_id':str(uuid.uuid4())}
  with closing(admission.Ledger(self.p)) as ledger:
   with self.assertRaises(admission.AdmissionWait):ledger.observe_provider(self.root,self.task,self.token,'chatgpt',None,provider_control.observation('unknown'),kind='unknown',delivery_receipt=receipt)
   authority,owner=self.probe()
   self.assertTrue(ledger.observe_provider(self.root,self.task,self.token,'chatgpt',None,provider_control.observation('unknown'),kind='unknown',delivery_receipt=receipt,authority=authority))
  self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.phase(),'RELEASED')
 def test_unfinished_metadata_owner_loss_derives_one_durable_host_uncertainty(self):
  ordinary.OrdinaryFeedback(self.root,self.store,self.settings,self.task,self.token)
  self.store.execution_update(self.execution,processes=json.dumps({'99999999':'42'}));self.store.recover(self.task)
  self.assertTrue(ordinary.flush(self.root,self.store,self.settings));self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.phase(),'HELD')
  self.recover_account();before=self.feedback();self.assertTrue(ordinary.flush(self.root,self.store,self.settings));self.assertEqual(self.feedback(),before)


 def capture_marker_fixture(self):
  gate=ordinary.OrdinaryFeedback(self.root,self.store,self.settings,self.task,self.token)
  event=str(uuid.uuid4());self.store.recover(self.task)
  self.store.execution_update(self.execution,codex_status='PROVIDER_CAPTURE_UNKNOWN:'+json.dumps([event]),finished=self.clock['wall'])
  return event,gate.capture_base
 def test_materializer_reads_fresh_marker_after_another_connection_acks(self):
  event,_=self.capture_marker_fixture();other=storage.Store(self.root/'tasks.sqlite');self.addCleanup(other.close)
  original=self.store.db;interleaved=[];case=self
  class BeforeWrite:
   def __enter__(self):original.__enter__();return self
   def __exit__(self,*args):return original.__exit__(*args)
   def execute(self,sql,args=()):
    if sql=='BEGIN IMMEDIATE' and not interleaved:
     interleaved.append(True);case.assertTrue(ordinary.flush(case.root,other,case.settings))
    return original.execute(sql,args)
  self.store.db=BeforeWrite()
  try:self.assertTrue(ordinary.flush(self.root,self.store,self.settings))
  finally:self.store.db=original
  self.assertTrue(interleaved);self.assertEqual(self.records()[0]['id'],event);self.assertEqual(self.records()[0]['phase'],'ACKED')
  self.recover_account();before=self.feedback();self.assertTrue(ordinary.flush(self.root,self.store,self.settings));self.assertEqual(self.feedback(),before)
  self.assertFalse(json.loads(self.store.execution(self.execution)['owner']).get('provider_capture_unknown'))
 def test_materializer_excludes_a_second_writer_until_marker_commit(self):
  self.capture_marker_fixture();other=storage.Store(self.root/'tasks.sqlite');self.addCleanup(other.close);other.db.execute('PRAGMA busy_timeout=0')
  original=self.store.db;interleaved=[];case=self
  class DuringWrite:
   def __enter__(self):original.__enter__();return self
   def __exit__(self,*args):return original.__exit__(*args)
   def execute(self,sql,args=()):
    if sql.startswith('UPDATE executions SET owner=') and not interleaved:
     interleaved.append(True);case.assertFalse(ordinary.flush(case.root,other,case.settings))
    return original.execute(sql,args)
  self.store.db=DuringWrite()
  try:self.assertTrue(ordinary.flush(self.root,self.store,self.settings))
  finally:self.store.db=original
  self.assertTrue(interleaved);self.recover_account();self.assertTrue(ordinary.flush(self.root,self.store,self.settings))
  self.assertFalse(json.loads(self.store.execution(self.execution)['owner']).get('provider_capture_unknown'))
 def test_exact_acked_marker_cleanup_is_idempotent_without_a_second_negative(self):
  event,body=self.capture_marker_fixture();self.assertTrue(ordinary.flush(self.root,self.store,self.settings))
  self.recover_account();before=self.feedback();owner=json.loads(self.store.execution(self.execution)['owner'])
  owner['provider_capture_unknown']={event:body}
  with self.store.db:self.store.db.execute('UPDATE executions SET owner=? WHERE id=?',(json.dumps(owner),self.execution))
  for _ in range(2):self.assertTrue(ordinary.flush(self.root,self.store,self.settings));self.assertEqual(self.feedback(),before)
  self.assertFalse(json.loads(self.store.execution(self.execution)['owner'])['provider_capture_unknown'])
 def test_acked_overflow_marker_cannot_be_cleared_by_materialization(self):
  event,body=self.capture_marker_fixture();body={**body,'overflow':True}
  owner=json.loads(self.store.execution(self.execution)['owner']);owner['provider_capture_unknown']={event:body}
  with self.store.db:self.store.db.execute('UPDATE executions SET owner=? WHERE id=?',(json.dumps(owner),self.execution))
  self.assertFalse(ordinary.flush(self.root,self.store,self.settings))
  self.assertEqual(self.records()[0]['phase'],'ACKED');self.recover_account();self.assertFalse(ordinary.flush(self.root,self.store,self.settings))
  self.assertEqual(json.loads(self.store.execution(self.execution)['owner'])['provider_capture_unknown'],{event:body})
 def test_checkpoint_only_overflow_retains_unclassified_restriction_after_recovery(self):
  gate=ordinary.OrdinaryFeedback(self.root,self.store,self.settings,self.task,self.token);original=self.store.db
  self.store.db=self.marker_write_failure()
  try:
   with patch.object(provider_control,'record',side_effect=sqlite3.OperationalError('synthetic shared outage')):
    for _ in range(33):gate.capture_failure()
  finally:self.store.db=original
  overflow=str(uuid.uuid5(uuid.NAMESPACE_URL,self.execution+':capture-overflow'));self.assertTrue(gate.capture_faults[overflow]['overflow'])
  self.store.recover(self.task)
  self.store.execution_update(self.execution,codex_status='PROVIDER_CAPTURE_UNKNOWN:'+json.dumps(list(gate.capture_faults)),finished=self.clock['wall'])
  self.assertFalse(ordinary.flush(self.root,self.store,self.settings));rows={r['id']:r for r in self.records()}
  self.assertTrue(json.loads(rows[overflow]['body'])['overflow']);self.assertEqual(rows[overflow]['phase'],'ACKED')
  self.assertIn('unclassified',self.feedback()['denied_scopes']);self.recover_account()
  self.assertFalse(ordinary.flush(self.root,self.store,self.settings));self.assertTrue(self.feedback()['blocked'])
  with self.assertRaises(admission.AdmissionWait):self.authorize_other()


class Envelope(unittest.TestCase):
 def call(self,response):
  p=object.__new__(transport.Process);p.seq=0;p.notifications=deque();p.send=Mock();p.record=Mock()
  p.resources=None;p.frames=deque([json.dumps(response).encode()]);p.provider_epoch=0;p.on_provider_message=None
  return p.call('turn/start',{})
 def test_malformed_or_uncorrelated_envelopes_never_produce_definitive_rejection(self):
  error={'code':429,'message':'synthetic'}
  for response in ({'id':True,'error':error},{'id':1.0,'error':error},{'id':'1','error':error},{'id':2,'error':error},
    {'id':1,'result':{},'error':error},{'id':1,'error':error,'method':'x'}, {'id':1,'error':error,'jsonrpc':'1.0'},
    {'id':1,'error':{'code':True,'message':'synthetic'}},{'id':1,'error':error,'extra':1}):
   with self.subTest(response=response),self.assertRaises(transport.ProtocolError):self.call(response)
 def test_decoded_notification_is_captured_once_even_after_queue_dequeue(self):
  p=object.__new__(transport.Process);p.seq=0;p.notifications=deque();p.send=Mock();p.record=Mock();p.resources=None;p.provider_epoch=0
  notice={'method':'account/updated','params':{}};p.frames=deque([json.dumps(notice).encode(),b'{"id":1,"result":{}}']);p.on_provider_message=Mock()
  self.assertEqual(p.call('account/read',{}),{});self.assertEqual(p.provider_epoch,1)
  self.assertEqual(p.next_message(),notice);self.assertEqual(p.on_provider_message.call_count,1)
 def test_actual_send_guard_failure_writes_no_wire_bytes(self):
  p=object.__new__(transport.Process);p.resources=None;p.record=Mock();p.proc=SimpleNamespace(stdin=Mock())
  p.before_turn_send=Mock(side_effect=transport.ProtocolError('synthetic stale authorization'))
  with self.assertRaises(transport.ProtocolError):p.send({'id':1,'method':'turn/start','params':{}})
  p.proc.stdin.write.assert_not_called();p.proc.stdin.flush.assert_not_called()
 def test_filtered_preintent_notification_is_not_requeued(self):
  p=object.__new__(transport.Process);p.seq=0;p.notifications=deque();p.send=Mock();p.record=Mock();p.resources=None;p.provider_epoch=0
  notice={'method':'error','params':{'threadId':'fixture-thread','turnId':'old-turn','willRetry':True}}
  p.frames=deque([json.dumps(notice).encode(),b'{"id":1,"result":{}}']);p.on_provider_message=Mock(return_value=False)
  self.assertEqual(p.call('account/read',{}),{});self.assertFalse(p.notifications);self.assertEqual(p.on_provider_message.call_count,1)
 def test_exact_response_only_429_is_a_correlated_rejection(self):
  with self.assertRaises(transport.RpcError) as caught:self.call({'id':1,'error':{'code':429,'message':'synthetic'}})
  self.assertTrue(caught.exception.correlated);self.assertEqual(caught.exception.method,'turn/start');self.assertTrue(caught.exception.provider_restriction)


class PipelineMalformedCompletion(unittest.TestCase):
 def fixture(self):
  import test_provider_history_feedback as history
  fixture=history.TerminalFeedback('test_running_history_persists_restriction_before_releasing_grant');fixture.setUp();self.addCleanup(fixture.doCleanups);return fixture
 def test_malformed_early_completion_status_cannot_drop_a_matching_negative(self):
  f=self.fixture();bad={**f.terminal,'status':'inProgress'};result=f.early([bad])
  self.assertEqual(result['state'],'UNKNOWN');self.assertTrue(f.feedback()['blocked']);f.refused(f.next_task());self.assertEqual(f.phase(),'HELD')
 def test_malformed_bound_completion_keeps_task_unknown_and_negative_durable(self):
  f=self.fixture();f.p.receive({'method':'turn/completed','params':{'threadId':'remote-thread','turn':{**f.terminal,'status':'unknown-native-status'}}})
  self.assertEqual(f.state()['tasks'][f.task]['state'],'UNKNOWN');self.assertTrue(f.feedback()['blocked']);self.assertEqual(f.phase(),'HELD');f.refused(f.next_task())


if __name__=='__main__':unittest.main()
