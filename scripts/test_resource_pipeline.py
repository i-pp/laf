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


# Added 2026-11-15: resource-only pipeline and UNKNOWN recovery regressions.

"""Offline shared ledger, provider and pipeline handoff/recovery boundaries."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'runtime/private_codex/src')]
from scripts.source_test_isolation import require_isolated
require_isolated()  # Refuse raw resource tests before any fixture or project import.
import resource_admission as admission
import pipeline_resource_admission as pipeline_admission
import scheduler
import test_resource_only as fixtures


def ident(number): return str(uuid.UUID(int=number))


class PipelineGrants(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/'runtime';self.root.mkdir()
        self.policy=fixtures.policy();self.policy['pipeline']=fixtures.fixtures.v(100,100,0,0)
        self.policy['providers']['chatgpt']['limit']=2
        self.settings={'resource_admission':self.policy}
        self.state={'tasks':{},'threads':{},'ops':{}}
        self.parent_available=fixtures.fixtures.v(1000,1000,0,0)
        self.account=fixtures.ACCOUNT
        self.remote={'id':'remote-thread','turns':[]}
        self.calls=[]
        self.gateway=SimpleNamespace(root=self.root,protocol=SimpleNamespace(),
            pipeline=SimpleNamespace(_mutate=lambda operation:operation(self.state)),rpc=self.rpc)
        for target,value in [('resource_admission.DIRECTORY',Path(temp.name)/'ledger'),
                ('resource_admission.time.monotonic',lambda:100),('resource_admission.time.time',lambda:1000),
                ('pipeline_resource_admission.linux_observation',self.observation)]:
            p=patch(target,value);p.start();self.addCleanup(p.stop)
        with closing(admission.Ledger(self.policy)) as ledger:
            self.owner=ledger.register_owner(self.root,'daemon')
            self.parent=ident(1001)
            ledger.reserve(self.root,ident(1),self.parent,
                {'kind':'local_control','provider':None,'resources':fixtures.fixtures.v(1000,1000,0,0)},
                self.observation,owner=self.owner)
        self.gate=pipeline_admission.PipelineAdmission(self.gateway,self.settings,lambda:(self.owner,self.parent))
        self.gate.ready=lambda:(self.owner,self.parent)

    def observation(self, rows, previous):
        sample,state=fixtures.fixtures.observation(rows,previous)
        sample['containers']={self.parent:copy.deepcopy(self.parent_available)} if hasattr(self,'parent') else {}
        return sample,state

    def rpc(self, method, params, **kwargs):
        self.calls.append((method,copy.deepcopy(params),kwargs))
        if method=='account/read':
            self.gateway.protocol._resource_account_sha256=admission.account_fingerprint(self.account)
            self.gateway.protocol._resource_account_observed_at=100
            return {'account':{'type':'chatgpt'}}
        if method=='thread/read':return {'thread':copy.deepcopy(self.remote)}
        raise AssertionError('Unexpected provider effect: '+method)

    def task(self, number=2):
        task={'id':ident(number),'thread_id':ident(100+number),'selection_sha256':'a'*64,
              'state':'QUEUED','reasoning_effort_evidence':{}}
        self.state['tasks'][task['id']]=task
        self.state['threads'][task['thread_id']]={'remote_id':'remote-thread'}
        return task

    def grant(self, task):
        task.update(resource_token=self.gate.reserve(task),resource_binding=self.gate.binding(task))
        return task['resource_token']

    def held(self):
        with closing(admission.Ledger(self.policy)) as ledger:return ledger.held()

    def test_pipeline_and_ordinary_intents_share_one_provider_limit(self):
        self.grant(self.task(2));self.grant(self.task(3))
        with closing(admission.Ledger(self.policy)) as ledger:
            other=self.root.parent/'another-runtime'
            owner=ledger.register_owner(other,'standalone')
            with self.assertRaisesRegex(admission.AdmissionWait,'provider_quota$'):
                ledger.reserve(other,ident(4),ident(1004),fixtures.pending(),self.observation,owner=owner)
        self.assertEqual(len(self.held()),3)

    def test_shared_process_parent_headroom_is_required_atomically(self):
        self.parent_available['cpu_millicores']=150
        self.grant(self.task(2))
        with self.assertRaisesRegex(admission.AdmissionWait,'container_cpu_millicores_headroom'):
            self.grant(self.task(3))
        self.assertEqual(len(self.held()),2)

    def test_wrong_account_or_missing_live_account_never_authorizes_a_turn(self):
        task=self.task();self.grant(task)
        self.account={'account':{'type':'chatgpt','email':'other@example.test','planType':'synthetic'}}
        with self.assertRaisesRegex(admission.AdmissionWait,'provider_account_unknown'):self.gate.authorize(task)
        self.account=fixtures.ACCOUNT;self.gate.authorize(task);self.gate.before_turn(task['id'])
        self.gateway.protocol._resource_account_sha256=None
        with self.assertRaisesRegex(admission.AdmissionWait,'provider_account_unknown'):self.gate.before_turn(task['id'])
        self.rpc('account/read',{})
        self.gateway.protocol._resource_account_observed_at=90
        with self.assertRaisesRegex(admission.AdmissionWait,'provider_account_unknown'):self.gate.before_turn(task['id'])
        self.assertFalse(any(method=='turn/start' for method,_,_ in self.calls))

    def test_handoff_rechecks_materialized_growth_and_parent_pressure(self):
        task=self.task();self.grant(task)
        self.parent_available['ram_bytes']=99
        with self.assertRaisesRegex(admission.AdmissionWait,'container_ram_bytes_headroom'):self.gate.authorize(task)
        self.assertEqual(json.loads(self.held()[1]['demand'])['kind'],'local_control')

    def test_changed_immutable_binding_cannot_adopt_or_replay_a_grant(self):
        task=self.task();self.grant(task);task['selection_sha256']='b'*64
        with self.assertRaisesRegex(admission.AdmissionWait,'retained'):self.gate.reserve(task)
        self.assertEqual(len(self.held()),2)

    def test_exact_history_and_account_releases_only_the_completed_pipeline_grant(self):
        task=self.task();self.grant(task);self.gate.authorize(task)
        task.update(state='COMPLETED',remote_turn='turn-exact')
        self.remote['turns']=[{'id':'other-turn','status':'completed'}]
        self.gate.reap();self.assertEqual(len(self.held()),2)
        self.remote['turns']=[{'id':'turn-exact','status':'completed'}]
        self.gate.reap();self.assertEqual([r['token'] for r in self.held()],[self.parent])
        self.assertTrue(all(call[2].get('timeout')==1 for call in self.calls if call[0]=='thread/read'))
        self.assertEqual(task['state'],'COMPLETED')

    def test_unknown_and_lost_dispatch_ack_remain_held_even_with_terminal_history(self):
        task=self.task();self.grant(task);self.gate.authorize(task)
        task.update(state='UNKNOWN',remote_turn='turn-exact')
        self.remote['turns']=[{'id':'turn-exact','status':'completed'}]
        self.gate.reap();self.assertEqual(len(self.held()),2)
        task.update(state='FAILED');task.pop('remote_turn')
        self.state['ops']['dispatch:'+task['id']]={'status':'ISSUED'}
        self.gate.reap();self.assertEqual(len(self.held()),2)

    def test_definitive_pre_turn_rejection_releases_but_missing_ack_does_not(self):
        task=self.task();self.grant(task)
        task.update(state='FAILED',reasoning_effort_evidence={'turn_transport':'rejected'})
        self.state['ops']['dispatch:'+task['id']]={'status':'FAILED'}
        self.gate.reap();self.assertEqual(len(self.held()),1)

    def test_token_and_binding_are_private_in_task_views(self):
        from codex_pipeline import Pipeline
        task=self.task();self.grant(task)
        projected=Pipeline._view(task)
        self.assertNotIn('resource_token',projected);self.assertNotIn('resource_binding',projected)


class RecoveryBoundaries(unittest.TestCase):
    def test_unknown_proc_read_is_not_a_dead_owner(self):
        identity=admission.owner_identity()
        original=Path.read_text
        def denied(path,*args,**kwargs):
            if str(path).endswith('/stat'):raise PermissionError('synthetic')
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',denied):self.assertIsNone(admission.owner_live(identity))

    def test_recovery_probe_uses_separate_grant_and_restores_runner_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);policy=fixtures.policy();settings={'resource_admission':policy,
                'runner_resource_policy':{'cpu_percent':1,'memory_bytes':100}}
            runner=SimpleNamespace(root=root,scheduler_settings=settings,resource_token='old-token',
                resource_task_id='old-task',resource_grant_task_id='old-grant')
            with patch('scheduler.require_charged_controller'),\
                    patch.object(admission,'DIRECTORY',root/'ledger'),\
                    patch.object(admission,'linux_observation',fixtures.fixtures.observation),\
                    patch.object(admission.time,'monotonic',return_value=100),\
                    patch.object(admission.time,'time',return_value=1000):
                with scheduler.resource_probe(runner,ident(55)):
                    self.assertEqual(runner.resource_task_id,ident(55))
                    self.assertNotEqual(runner.resource_grant_task_id,ident(55))
                    with closing(admission.Ledger(policy)) as ledger:
                        self.assertEqual(json.loads(ledger.held()[0]['demand'])['kind'],'reconciliation')
                self.assertEqual((runner.resource_token,runner.resource_task_id,runner.resource_grant_task_id),
                                 ('old-token','old-task','old-grant'))
                with closing(admission.Ledger(policy)) as ledger:
                    self.assertEqual(ledger.held(),[])
                    self.assertFalse(ledger.db.execute("SELECT 1 FROM admission_owners WHERE phase='HELD'").fetchone())


class ResourceGateway(unittest.TestCase):
    observation=PipelineGrants.observation
    rpc=PipelineGrants.rpc
    held=PipelineGrants.held

    def setUp(self):
        PipelineGrants.setUp(self)
        import hashlib
        import test_codex_catalog_effort as native
        from codex_host import HostGateway
        self.root.chmod(0o700);(self.root/'logs').mkdir(mode=0o700)
        self.workspace=self.root/'workspace';self.workspace.mkdir()
        binary=self.root/'codex';binary.write_bytes(b'\x7fELFsynthetic');binary.chmod(0o700)
        policy={'schema':2,'workspaces':{'fixture':str(self.workspace)},'models':['offline-model'],
                'max_threads':1,'max_tasks':1,'concurrency':1,'timeout_seconds':120}
        for name,value in [('codex-pipeline-policy.json',policy),('codex-pipeline-executor.json',
                {'schema':1,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()})]:
            path=self.root/name;path.write_text(json.dumps(value));path.chmod(0o600)
        case=self
        class AccountPeer(native.Peer):
            def __call__(self, request, timeout_s):
                if request['method']=='account/read':
                    self.calls.append(('account/read',copy.deepcopy(request.get('params',{}))))
                    return {'id':request['id'],'result':{'requiresOpenaiAuth':True,**case.account}}
                return super().__call__(request,timeout_s)
        self.peer=AccountPeer(str(self.workspace));self.transports=[]
        def factory(binary):
            transport=native.FakeOwned(self.peer);self.transports.append(transport);return transport
        for target,value in [('codex_host.load_project_policy',SimpleNamespace(sha256='a'*64)),
                ('codex_host.developer_instructions','Fixed offline policy.')]:
            p=patch(target,return_value=value);p.start();self.addCleanup(p.stop)
        p=patch.object(HostGateway,'require_host');p.start();self.addCleanup(p.stop)
        self.gateway=HostGateway(self.root,str(binary),self.root.parent,lambda:False,
            transport_factory=factory,resource_settings=self.settings,resource_controller=lambda:(self.owner,self.parent))
        self.gateway.resource_gate.ready=lambda:(self.owner,self.parent)
        self.addCleanup(self.gateway.close)

    def start(self, key):
        return self.gateway.dispatch('codex_thread_start',{'workspace':'fixture','model':'offline-model','request_key':key})['thread_id']

    def submit(self, thread, key):
        return self.gateway.dispatch('codex_task_submit',{'thread_id':thread,'prompt':'Offline fixture.','request_key':key})

    def test_real_gateway_and_protocol_use_resource_grants_past_legacy_pipeline_counts(self):
        threads=[self.start('start-'+str(n)) for n in range(3)]
        tasks=[self.submit(thread,'task-'+str(n)) for n,thread in enumerate(threads)]
        self.assertEqual(self.peer.count('turn/start'),2)
        state=self.gateway.pipeline._mutate(lambda s:copy.deepcopy(s))
        self.assertEqual(sum(t['state']=='RUNNING' for t in state['tasks'].values()),2)
        third=state['tasks'][tasks[2]['id']]
        self.assertEqual(third['state'],'QUEUED')
        self.assertEqual(third['resource_wait_reason'],'resource_provider_quota')
        capacity=self.gateway.pipeline.capacity(state)
        self.assertIsNone(capacity['execution_slots']);self.assertEqual(capacity['admission_mode'],'resources')
        listing=self.gateway.dispatch('codex_threads_list',{})
        self.assertNotIn('resource_token',json.dumps(listing));self.assertNotIn('resource_binding',json.dumps(listing))
        self.assertNotIn('synthetic@example.test',json.dumps(listing))
        remote=state['threads'][threads[0]]['remote_id'];turn=self.peer.threads[remote]['turns'][0]
        turn.update(status='completed',items=[])
        self.gateway.protocol.receive({'method':'turn/completed','params':{'threadId':remote,'turn':copy.deepcopy(turn)}})
        self.gateway.pump()
        self.assertEqual(self.peer.count('turn/start'),3)
        self.assertEqual(len(self.held()),3)  # Controller and two current turns.

    def test_raw_account_identity_is_privately_pinned_and_change_stops_native_turn(self):
        thread=self.start('start')
        self.account={'account':{'type':'chatgpt','email':'changed@example.test','planType':'synthetic'}}
        task=self.submit(thread,'wrong-account')
        self.assertEqual(self.peer.count('turn/start'),0)
        source=self.gateway.pipeline._mutate(lambda s:dict(s['tasks'][task['id']]))
        self.assertEqual(source['resource_wait_reason'],'resource_provider_account_unknown')
        self.assertEqual(len(self.held()),2)


class OperatorReconciliation(unittest.TestCase):
    def setUp(self):
        import sqlite3
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.settings={'resource_admission':fixtures.policy()}
        self.task={'id':ident(2),'state':'UNKNOWN','active_execution_id':'execution-exact'}
        self.execution={'thread_id':'thread-exact','turn_id':'turn-exact'}
        self.session={'thread_id':'thread-exact','turn_id':'turn-exact','exists':True,'ended':True,
                      'latest_turn_matches':True,'resource_provider_account_verified':True}
        self.proof=ident(88)
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE reconciliations(id,task_id,execution_id,facts,created)')
        self.db.execute('CREATE TABLE scheduler_dispatches(task_id,resources,owner)')
        self.runner=SimpleNamespace(root=self.root,scheduler_settings=self.settings,
            store=SimpleNamespace(db=self.db,get=lambda _:self.task,execution=lambda _:self.execution))
        for target,value in [('resource_admission.DIRECTORY',self.root/'ledger'),
                ('resource_admission.time.time',lambda:1000),('resource_admission.time.monotonic',lambda:100)]:
            p=patch(target,value);p.start();self.addCleanup(p.stop)
        p=patch('recovery.resource_facts',return_value={'released':True});p.start();self.addCleanup(p.stop)
        with closing(admission.Ledger(self.settings['resource_admission'])) as ledger:
            owner=ledger.register_owner(self.root,'standalone')
            ledger.reserve(self.root,self.task['id'],ident(1002),fixtures.pending(),fixtures.fixtures.observation,owner=owner)
        self.store_proof()

    def store_proof(self, *, created=1000, execution='execution-exact'):
        self.db.execute('DELETE FROM reconciliations')
        self.db.execute('INSERT INTO reconciliations VALUES(?,?,?,?,?)',
            (self.proof,self.task['id'],execution,json.dumps({'session':self.session}),created))
        self.db.commit()

    def test_fresh_exact_proof_releases_reservation_without_replay_or_task_state_change(self):
        ready=scheduler.reconcile_task_resources(self.runner,self.task['id'],self.proof)
        self.assertEqual(ready['status'],'READY')
        result=scheduler.reconcile_task_resources(self.runner,self.task['id'],self.proof,apply=True)
        self.assertEqual(result['status'],'RELEASED');self.assertEqual(self.task['state'],'UNKNOWN')
        self.assertIs(result['automatic_retry'],False)
        with closing(admission.Ledger(self.settings['resource_admission'])) as ledger:
            self.assertEqual(ledger.held(),[])
            self.assertEqual(ledger.db.execute('SELECT id FROM resource_reconciliations').fetchone()[0],self.proof)

    def test_stale_wrong_execution_account_and_local_release_do_not_free_unknown(self):
        for field,value in [('turn_id','other'),('latest_turn_matches',False),('resource_provider_account_verified',False)]:
            saved=self.session[field];self.session[field]=value;self.store_proof()
            with self.assertRaises(ValueError):scheduler.reconcile_task_resources(self.runner,self.task['id'],self.proof,apply=True)
            self.session[field]=saved
        for kwargs in ({'created':900},{'execution':'other'}):
            self.store_proof(**kwargs)
            with self.assertRaises(ValueError):scheduler.reconcile_task_resources(self.runner,self.task['id'],self.proof,apply=True)
        self.store_proof()
        with patch('recovery.resource_facts',return_value={'released':False}):
            with self.assertRaises(ValueError):scheduler.reconcile_task_resources(self.runner,self.task['id'],self.proof,apply=True)
        with closing(admission.Ledger(self.settings['resource_admission'])) as ledger:self.assertEqual(len(ledger.held()),1)


if __name__=='__main__':unittest.main(verbosity=2)
