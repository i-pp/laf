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


# Added 2026-11-15: terminal history restriction delivery and crash-order regressions.

"""Permanent contained terminal-history feedback, crash-order and correlation oracles."""
from contextlib import closing,ExitStack
import copy,json,sqlite3,sys,unittest
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime/private_codex/src')]
from source_test_isolation import require_isolated
require_isolated()
import test_provider_feedback as fixtures
import provider_control
from codex_host import HostGateway
from codex_pipeline import Pipeline
from pipeline_resource_admission import PipelineAdmission
from resource_admission import AdmissionWait,Ledger


def setUpModule():
 global GUARDS
 GUARDS=[patch(target,side_effect=AssertionError('Synthetic history fixtures forbid live effects'))
   for target in ('subprocess.Popen','socket.socket.bind','socket.socket.connect','threading.Thread.start')]
 for guard in GUARDS:guard.start()


def tearDownModule():
 for guard in reversed(GUARDS):guard.stop()


class TerminalFeedback(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.PipelineFeedback();self.f.setUp();self.addCleanup(self.f.doCleanups)
  self.p,unused,self.native=self.f.operation(fixtures.wire.ProtocolUnknown('Unused synthetic transport'))
  self.task=self.f.task['id'];self.thread=self.f.task['thread_id'];self.turn='acknowledged-fixture-turn'
  self.terminal={'id':self.turn,'status':'failed','items':[],
    'error':{'codexErrorInfo':'rateLimitExceeded','message':'synthetic restriction only'}}
  self.history={'thread':{'id':'remote-thread','turns':[copy.deepcopy(self.terminal)]}}
  self.metadata=self.f.rpc;self.reads=[]
  self.f.gateway.rpc=self.rpc;self.p.client.request=self.rpc
  self.bind()
 def rpc(self,method,params,**kw):
  if method=='thread/read':
   self.assertEqual(params,{'threadId':'remote-thread','includeTurns':True});self.reads.append(method)
   return copy.deepcopy(self.history)
  return self.metadata(method,params,**kw)
 def bind(self,*,state='RUNNING',turn=True):
  def update(s):
   s['tasks'][self.task].update(state=state,remote_turn=self.turn if turn else None,
      reasoning_effort_evidence={'turn_transport':'acknowledged' if turn else 'outcome_unknown'})
   s['threads'][self.thread]['active_task']=self.task if state in ('RUNNING','DISPATCHING','UNKNOWN') else None
  self.p._mutate(update)
 def state(self):return self.p._mutate(lambda s:copy.deepcopy(s))
 def feedback(self):
  with closing(Ledger(self.f.p)) as ledger:return ledger._feedback_state('chatgpt',fixtures.AUTH)
 def phase(self,token=None):
  with closing(Ledger(self.f.p)) as ledger:
   return ledger.db.execute('SELECT phase FROM reservations WHERE token=?',(token or self.f.task['resource_token'],)).fetchone()[0]
 def receipts(self):
  with closing(Ledger(self.f.p)) as ledger:
   return [json.loads(r[0]) for r in ledger.db.execute("SELECT evidence FROM resource_reconciliations WHERE json_extract(evidence,'$.kind') IN ('provider_terminal_restriction','provider_preack_restriction')")]
 def next_task(self,n=3):
  task={'id':fixtures.ident(n),'thread_id':fixtures.ident(100+n),'selection_sha256':'b'*64,'state':'QUEUED','reasoning_effort_evidence':{}}
  task.update(resource_token=self.f.gate.reserve(task),resource_binding=self.f.gate.binding(task))
  def put(s):
   s['tasks'][task['id']]=task;s['threads'][task['thread_id']]={'remote_id':'next-thread-'+str(n),'active_task':None}
  self.p._mutate(put);return task
 def refused(self,task):
  with self.assertRaises(AdmissionWait):self.f.gate.authorize(task)
 def recover_provider(self):
  self.f.clock['mono']+=20;self.f.clock['wall']+=20;self.f.snapshot=fixtures.usage(0)
  provider_control.refresh(self.f.settings,self.f.root,fixtures.ident(1),self.f.parent,self.f.rpc,client=lambda:self.f.gateway.protocol)
  self.assertFalse(self.feedback()['blocked'])
 def assert_no_native(self):
  self.assertEqual(self.native.call_count,0);self.assertFalse(any(m=='turn/start' for m,p in self.f.calls))
 def stage(self):
  self.p._mutate(lambda s:self.f.gate.stage_terminal(s,s['tasks'][self.task],copy.deepcopy(self.terminal),history=True))
 def fail_record(self):
  original=provider_control.record
  def record(*args,**kwargs):
   if kwargs.get('delivery_receipt') is not None:raise sqlite3.OperationalError('synthetic publication failure')
   return original(*args,**kwargs)
  return patch.object(provider_control,'record',side_effect=record)

 def test_running_history_persists_restriction_before_releasing_grant(self):
  result=self.p.refresh(self.task);self.assertEqual(result['state'],'FAILED');self.f.gate.reap()
  self.assertEqual(self.phase(),'RELEASED');self.assertTrue(self.feedback()['blocked']);self.refused(self.next_task())
  self.assertEqual(len(self.receipts()),1);self.assert_no_native()
 def test_direct_terminal_reap_persists_restriction_before_release(self):
  self.bind(state='FAILED');self.f.gate.reap()
  self.assertEqual(self.phase(),'RELEASED');self.assertTrue(self.feedback()['blocked']);self.refused(self.next_task())
  self.assertEqual(len(self.receipts()),1);self.assert_no_native()
 def test_unrelated_native_thread_never_blocks_account_or_releases_grant(self):
  self.bind(state='FAILED');self.history['thread']['id']='other-thread';self.f.gate.reap()
  self.assertEqual(self.phase(),'HELD');self.assertFalse(self.feedback()['blocked']);self.assertFalse(self.receipts());self.assert_no_native()
 def test_unrelated_native_turn_never_blocks_account_or_releases_grant(self):
  self.bind(state='FAILED');self.history['thread']['turns'][0]['id']='other-turn';self.f.gate.reap()
  self.assertEqual(self.phase(),'HELD');self.assertFalse(self.feedback()['blocked']);self.assertFalse(self.receipts());self.assert_no_native()
 def test_lost_ack_does_not_adopt_history_by_list_order(self):
  self.bind(state='UNKNOWN',turn=False);self.assertEqual(self.p.refresh(self.task)['state'],'UNKNOWN');self.f.gate.reap()
  self.assertEqual(self.phase(),'HELD');self.assertFalse(self.feedback()['blocked']);self.assertFalse(self.receipts());self.assert_no_native()
 def test_persistence_failure_holds_pending_evidence_and_every_new_authorization(self):
  with self.fail_record():
   result=self.p.refresh(self.task);self.assertEqual(result['state'],'UNKNOWN');self.assertEqual(result['reconciliation'],'UNKNOWN')
   self.assertTrue(self.state()['tasks'][self.task]['provider_feedback_pending']);self.assertEqual(self.phase(),'HELD')
   self.refused(self.next_task());self.f.gate.reap();self.assertEqual(self.phase(),'HELD')
  self.p.refresh(self.task);self.f.gate.reap()
  self.assertEqual(self.phase(),'RELEASED');self.assertTrue(self.feedback()['blocked']);self.assert_no_native()
 def test_crash_after_local_enqueue_is_recovered_before_another_task_authorizes(self):
  self.bind(state='FAILED');self.stage();self.assertFalse(self.feedback()['blocked'])
  controller=lambda:(self.f.owner,self.f.parent)
  restarted=PipelineAdmission(self.f.gateway,self.f.settings,controller);restarted.ready=controller
  self.f.gate=restarted;self.p.resource_gate=restarted;self.f.gateway.resource_gate=restarted
  self.refused(self.next_task());self.assertTrue(self.feedback()['blocked']);self.assertEqual(len(self.receipts()),1)
  self.assertNotIn('provider_feedback_pending',self.state()['tasks'][self.task]);self.assert_no_native()
 def test_shared_commit_before_local_ack_does_not_reblock_after_other_recovery(self):
  original=self.p._mutate
  def mutation(fn):
   if fn.__name__=='acknowledge':raise OSError('synthetic crash before local ack')
   return original(fn)
  with patch.object(self.p,'_mutate',side_effect=mutation):self.p.refresh(self.task)
  self.assertTrue(self.feedback()['blocked']);self.assertTrue(self.state()['tasks'][self.task]['provider_feedback_pending'])
  self.recover_provider();before=self.feedback();self.assertTrue(self.f.gate.flush_feedback())
  self.assertEqual(self.feedback(),before);self.f.gate.reap();self.assertFalse(self.feedback()['blocked'])
  self.assertEqual(self.phase(),'RELEASED');self.f.gate.authorize(self.next_task());self.assert_no_native()
 def test_local_ack_before_release_and_duplicate_history_keep_recovery(self):
  self.p.refresh(self.task);self.assertEqual(self.phase(),'HELD');self.assertNotIn('provider_feedback_pending',self.state()['tasks'][self.task])
  self.recover_provider();before=self.feedback();self.f.gate.reap()
  self.assertEqual(self.phase(),'RELEASED');self.assertEqual(self.feedback(),before)
  self.stage();self.assertTrue(self.f.gate.flush_feedback());self.assertEqual(self.feedback(),before)
  self.f.gate.authorize(self.next_task());self.assert_no_native()
 def test_negative_and_receipt_roll_back_together_on_receipt_insert_failure(self):
  self.bind(state='FAILED');record=self.p._mutate(lambda s:self.f.gate.terminal_record(s,s['tasks'][self.task],self.terminal))
  with closing(Ledger(self.f.p)) as ledger:
   before=ledger._feedback_state('chatgpt',fixtures.AUTH);original=ledger.db
   class BrokenInsert:
    def __enter__(self):original.__enter__();return self
    def __exit__(self,*args):return original.__exit__(*args)
    def execute(self,sql,*args):
     if sql.startswith('INSERT INTO resource_reconciliations'):raise sqlite3.OperationalError('synthetic insert failure')
     return original.execute(sql,*args)
   ledger.db=BrokenInsert()
   try:
    with self.assertRaises(sqlite3.OperationalError):
     ledger.observe_provider(self.f.root,self.task,self.f.task['resource_token'],'chatgpt',None,provider_control.observation('block'),
       kind='rejection',delivery_receipt={k:record[k] for k in ('generation','binding','thread','turn','event')})
   finally:ledger.db=original
   self.assertEqual(ledger._feedback_state('chatgpt',fixtures.AUTH),before)
   self.assertEqual(ledger.db.execute("SELECT count(*) FROM resource_reconciliations").fetchone()[0],0)
  self.assertEqual(self.phase(),'HELD');self.assert_no_native()
 def test_forged_receipt_evidence_is_not_a_delivery_proof(self):
  self.p.refresh(self.task)
  with closing(Ledger(self.f.p)) as ledger:
   with ledger.db:ledger.db.execute("UPDATE resource_reconciliations SET evidence='{}'")
  self.stage();self.assertFalse(self.f.gate.flush_feedback());self.assertEqual(self.phase(),'HELD')
  self.assertEqual(self.state()['tasks'][self.task]['state'],'UNKNOWN');self.assert_no_native()
 def test_changed_task_binding_cannot_acknowledge_or_release_pending_negative(self):
  self.bind(state='FAILED');self.stage()
  self.p._mutate(lambda s:s['tasks'][self.task].update(resource_binding='0'*64))
  self.assertFalse(self.f.gate.flush_feedback());self.assertEqual(self.phase(),'HELD');self.refused(self.next_task());self.assert_no_native()
 def test_pending_failure_blocks_even_an_unrelated_unused_grant_release(self):
  self.bind(state='FAILED');self.stage();other=self.next_task()
  self.p._mutate(lambda s:s['tasks'][other['id']].update(state='FAILED'))
  with self.fail_record():self.f.gate.reap()
  self.assertEqual(self.phase(),'HELD');self.assertEqual(self.phase(other['resource_token']),'HELD');self.assert_no_native()
 def test_live_retry_error_is_a_new_negative_after_successful_recovery(self):
  message={'method':'error','params':{'threadId':'remote-thread','turnId':self.turn,'willRetry':True,'error':self.terminal['error']}}
  self.p.receive(message);self.assertTrue(self.feedback()['blocked']);self.recover_provider();self.p.receive(message)
  self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.state()['tasks'][self.task]['state'],'RUNNING')
  self.assertFalse(self.receipts());self.assert_no_native()

 def prepare_dispatch(self,request):
  def queued(s):
   task=s['tasks'][self.task]
   task.update(state='QUEUED',remote_turn=None,model='fixture-model',reasoning_effort='max',queue_seq=1,prompt='synthetic prompt')
   s['threads'][self.thread].update(active_task=None,archived=False,closing=False,workspace='fixture')
  self.p._mutate(queued);self.p._assert_selection=lambda _:None
  self.p._model=lambda *args:{'supported_reasoning_efforts':['max']};self.p.room=lambda *args:True
  self.p.loaded.add(self.thread);self.p.client.request=request
 def early(self,order):
  def request(method,params):
   self.assertEqual(method,'turn/start')
   for turn in order:
    self.p.receive({'method':'turn/completed','params':{'threadId':'remote-thread','turn':copy.deepcopy(turn)}})
    self.assertFalse(self.feedback()['blocked'])  # No native turn binding exists until ACK.
   return {'turn':{'id':self.turn,'status':'inProgress','items':[],'error':None}}
  self.prepare_dispatch(request);return self.p._dispatch()
 def test_matching_early_negative_is_not_erased_by_later_unrelated_completion(self):
  result=self.early([self.terminal,{'id':'other-turn','status':'completed','items':[],'error':None}])
  self.assertEqual(result['state'],'FAILED');self.assertTrue(self.feedback()['blocked']);self.assertEqual(len(self.receipts()),1)
 def test_matching_early_negative_survives_reverse_completion_order(self):
  result=self.early([{'id':'other-turn','status':'completed','items':[],'error':None},self.terminal])
  self.assertEqual(result['state'],'FAILED');self.assertTrue(self.feedback()['blocked']);self.assertEqual(len(self.receipts()),1)
 def test_unmatched_early_negative_never_publishes_a_false_account_block(self):
  other=copy.deepcopy(self.terminal);other['id']='other-turn';result=self.early([other])
  self.assertEqual(result['state'],'RUNNING');self.assertFalse(self.feedback()['blocked']);self.assertFalse(self.receipts())
 def test_duplicate_early_terminal_cannot_erase_its_restriction_bit(self):
  sparse={**self.terminal,'error':None};result=self.early([self.terminal,sparse])
  self.assertEqual(result['state'],'FAILED');self.assertTrue(self.feedback()['blocked']);self.assertEqual(len(self.receipts()),1)
 def test_early_overflow_holds_uncertainty_until_exact_complete_history(self):
  unrelated=[{'id':'other-'+str(n),'status':'completed','items':[],'error':None} for n in range(33)]
  result=self.early([*unrelated,self.terminal]);self.assertEqual(result['state'],'UNKNOWN')
  self.assertTrue(self.state()['tasks'][self.task]['provider_feedback_uncertain']);self.assertEqual(self.phase(),'HELD')
  self.refused(self.next_task());self.assertFalse(self.feedback()['blocked'])
  self.p.client.request=self.rpc;self.p.refresh(self.task);self.f.gate.reap()
  self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.phase(),'RELEASED')
 def test_incomplete_history_never_clears_early_evidence_overflow(self):
  unrelated=[{'id':'other-'+str(n),'status':'completed','items':[],'error':None} for n in range(33)]
  self.early([*unrelated,self.terminal]);self.p.client.request=self.rpc
  self.history['thread']['turns'][0].pop('error')
  result=self.p.refresh(self.task);self.assertEqual(result['state'],'UNKNOWN')
  self.assertEqual(result['reconciliation'],'UNKNOWN');self.f.gate.reap()
  self.assertEqual(self.phase(),'HELD');self.refused(self.next_task());self.assertFalse(self.receipts())
  self.history['thread']['turns'][0]['error']=self.terminal['error']
  self.p.refresh(self.task);self.f.gate.reap()
  self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.phase(),'RELEASED')
 def test_previous_schema3_protocol_cannot_join_a_held_generation(self):
  import hashlib
  from resource_admission import generation
  with closing(Ledger(self.f.p)) as ledger:
   policy=ledger.policy
   previous=hashlib.sha256(json.dumps({'protocol':10,'policy':policy},sort_keys=True,separators=(',',':')).encode()).hexdigest()
   self.assertNotEqual(previous,generation(policy))
   with ledger.db:ledger.db.execute("UPDATE admission_meta SET generation=? WHERE id=1",(previous,))
  with self.assertRaises(AdmissionWait) as caught:
   with closing(Ledger(self.f.p)):pass
  self.assertEqual(caught.exception.code,'resource_generation_held')
  # The synthetic fixture is removed by its owner cleanup; no rebind/repair.
 def early_messages(self,messages):
  def request(method,params):
   self.assertEqual(method,'turn/start')
   for message in messages:
    self.p.receive(copy.deepcopy(message));self.assertFalse(self.feedback()['blocked'])
   return {'turn':{'id':self.turn,'status':'inProgress','items':[],'error':None}}
  self.prepare_dispatch(request);return self.p._dispatch()
 def error_message(self,native=None,*,will_retry=True):
  return {'method':'error','params':{'threadId':'remote-thread','turnId':native or self.turn,
    'willRetry':will_retry,'error':copy.deepcopy(self.terminal['error'])}}
 def test_matching_retry_error_before_ack_is_delivered_before_next_authorization(self):
  result=self.early_messages([self.error_message()]);self.assertEqual(result['state'],'RUNNING')
  self.assertTrue(self.feedback()['blocked']);self.refused(self.next_task());self.assertEqual(self.phase(),'HELD')
  self.assertEqual(self.state()['threads'][self.thread]['active_task'],self.task);self.assertEqual(len(self.receipts()),1)
 def test_nonretrying_preack_error_does_not_infer_terminal_execution(self):
  result=self.early_messages([self.error_message(will_retry=False)]);self.assertEqual(result['state'],'RUNNING')
  self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.phase(),'HELD');self.refused(self.next_task())
 def test_mismatched_preack_error_cannot_block_the_bound_account(self):
  result=self.early_messages([self.error_message('other-turn')]);self.assertEqual(result['state'],'RUNNING')
  self.assertFalse(self.feedback()['blocked']);self.assertFalse(self.receipts());self.f.gate.authorize(self.next_task())
 def test_preack_error_bit_survives_sparse_terminal_replacement(self):
  terminal={**self.terminal,'error':None}
  result=self.early_messages([self.error_message(),{'method':'turn/completed','params':{'threadId':'remote-thread','turn':terminal}}])
  self.assertEqual(result['state'],'FAILED');self.assertTrue(self.feedback()['blocked']);self.assertEqual(len(self.receipts()),1)
 def test_preack_error_after_terminal_keeps_both_distinct_negative_receipts(self):
  result=self.early_messages([{'method':'turn/completed','params':{'threadId':'remote-thread','turn':self.terminal}},self.error_message()])
  self.assertEqual(result['state'],'FAILED');self.assertTrue(self.feedback()['blocked'])
  self.assertEqual({r['kind'] for r in self.receipts()},{'provider_terminal_restriction','provider_preack_restriction'})
 def test_late_terminal_restriction_is_not_suppressed_by_recovered_preack_error(self):
  self.early_messages([self.error_message()]);self.recover_provider();self.p.client.request=self.rpc
  self.p.refresh(self.task);self.f.gate.reap()
  self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.phase(),'RELEASED');self.assertEqual(len(self.receipts()),2)
 def test_preack_receipt_replay_after_recovery_is_idempotent_without_terminal_inference(self):
  original=self.p._mutate
  def mutation(fn):
   if fn.__name__=='acknowledge':raise OSError('synthetic preack receipt crash')
   return original(fn)
  with patch.object(self.p,'_mutate',side_effect=mutation):result=self.early_messages([self.error_message()])
  self.assertEqual(result['state'],'UNKNOWN');self.assertTrue(self.state()['tasks'][self.task]['provider_error_pending'])
  self.recover_provider();before=self.feedback();self.assertTrue(self.f.gate.flush_feedback())
  self.assertEqual(self.feedback(),before);self.assertEqual(self.state()['tasks'][self.task]['state'],'UNKNOWN')
  self.assertEqual(self.phase(),'HELD');self.assertEqual(self.state()['threads'][self.thread]['active_task'],self.task)
 def test_preack_publication_failure_holds_every_onward_authorization(self):
  with self.fail_record():
   result=self.early_messages([self.error_message()]);self.assertEqual(result['state'],'UNKNOWN')
   self.assertTrue(self.state()['tasks'][self.task]['provider_error_pending']);self.refused(self.next_task())
   self.f.gate.reap();self.assertEqual(self.phase(),'HELD')
  self.assertTrue(self.f.gate.flush_feedback());self.assertTrue(self.feedback()['blocked'])
  self.assertEqual(self.state()['tasks'][self.task]['state'],'UNKNOWN');self.assertEqual(self.phase(),'HELD')
 def test_preack_acknowledgement_keeps_a_later_approval_wait(self):
  with self.fail_record():self.early_messages([self.error_message()])
  self.p._mutate(lambda s:s['tasks'][self.task].update(state='WAITING_APPROVAL'))
  self.assertTrue(self.f.gate.flush_feedback());task=self.state()['tasks'][self.task]
  self.assertEqual(task['state'],'WAITING_APPROVAL');self.assertNotIn('provider_error_pending',task)
  self.assertNotIn('reason_code',task);self.assertEqual(self.phase(),'HELD')
 def test_error_overflow_cannot_be_cleared_by_later_terminal_null(self):
  messages=[self.error_message('other-'+str(n)) for n in range(33)]+[self.error_message()]
  self.assertEqual(self.early_messages(messages)['state'],'UNKNOWN');self.p.client.request=self.rpc
  self.history['thread']['turns'][0].update(status='completed',error=None)
  self.assertEqual(self.p.refresh(self.task)['state'],'UNKNOWN');self.f.gate.reap()
  self.assertEqual(self.phase(),'HELD');self.assertTrue(self.state()['tasks'][self.task]['provider_error_uncertain'])
  self.refused(self.next_task());self.assertFalse(self.feedback()['blocked'])
  self.history['thread']['turns'][0]=copy.deepcopy(self.terminal)
  self.p.refresh(self.task);self.f.gate.reap();self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.phase(),'RELEASED')
 def test_malformed_history_never_resolves_terminal_overflow(self):
  unrelated=[{'id':'other-'+str(n),'status':'completed','items':[],'error':None} for n in range(33)]
  self.early([*unrelated,self.terminal]);self.p.client.request=self.rpc;other=self.next_task()
  malformed=(42,{},'unknown',{'message':42},{'message':'synthetic','codexErrorInfo':42},
    {'message':'synthetic','codexErrorInfo':{'httpConnectionFailed':{'httpStatusCode':True}}})
  for error in malformed:
   with self.subTest(error=error):
    self.history['thread']['turns'][0]['error']=error
    self.assertEqual(self.p.refresh(self.task)['state'],'UNKNOWN');self.f.gate.reap()
    self.assertEqual(self.phase(),'HELD');self.refused(other);self.assertFalse(self.feedback()['blocked'])
 def test_direct_terminal_reap_also_holds_malformed_error_history(self):
  self.bind(state='FAILED');self.history['thread']['turns'][0]['error']=42;self.f.gate.reap()
  self.assertEqual(self.state()['tasks'][self.task]['state'],'UNKNOWN');self.assertEqual(self.phase(),'HELD')
  self.refused(self.next_task());self.assertFalse(self.feedback()['blocked'])
 def test_valid_nonprovider_history_can_resolve_only_terminal_overflow(self):
  unrelated=[{'id':'other-'+str(n),'status':'completed','items':[],'error':None} for n in range(33)]
  self.early([*unrelated,self.terminal]);self.p.client.request=self.rpc
  self.history['thread']['turns'][0]['error']={'message':'synthetic nonprovider failure','codexErrorInfo':'internalServerError'}
  self.assertEqual(self.p.refresh(self.task)['state'],'FAILED');self.f.gate.reap()
  self.assertEqual(self.phase(),'RELEASED');self.assertFalse(self.feedback()['blocked'])
 def test_early_malformed_terminal_keeps_unknown_until_valid_exact_history(self):
  malformed={**self.terminal,'error':42};self.assertEqual(self.early([malformed])['state'],'UNKNOWN')
  self.refused(self.next_task());self.assertEqual(self.phase(),'HELD');self.p.client.request=self.rpc
  self.p.refresh(self.task);self.f.gate.reap();self.assertTrue(self.feedback()['blocked']);self.assertEqual(self.phase(),'RELEASED')
 def test_terminal_ack_uses_one_receipted_delivery_path(self):
  before=[]
  def native(method,params):
   self.assertEqual(method,'turn/start');before.append(self.feedback()['revision'])
   return {'turn':copy.deepcopy(self.terminal)}
  self.f.gateway.protocol.request=Mock(side_effect=native)
  self.prepare_dispatch(lambda method,params:HostGateway.rpc(self.f.gateway,method,params))
  result=self.p._dispatch();self.assertEqual(result['state'],'FAILED');self.assertTrue(self.feedback()['blocked'])
  self.assertEqual(self.feedback()['revision'],before[0]+1);self.assertEqual(len(self.receipts()),1)


if __name__=='__main__':unittest.main()
