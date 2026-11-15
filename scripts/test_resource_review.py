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


# Added 2026-11-15: independent-review resource admission regressions.

"""Regressions reproduced by the independent resource-only source review."""
import copy
import json
from contextlib import closing, ExitStack
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'runtime/private_codex/src')]
import test_resource_only as fixtures
import resource_admission as admission
import runner, recovery, resources, verification


class Store:
    def __init__(self, task):
        self.task=task
        self.current=dict(id=task['active_execution_id'],task_id=task['id'],owner='{}',processes='{}',
            launch_pending=0,dispatched=0,thread_id='thread',turn_id='old-turn',workspace_before=None,cleanup=None)
        self.evidence=None
    def get(self, _):return dict(self.task)
    def execution(self, _):return dict(self.current)
    def update(self, _, **values):self.task.update(values)
    def execution_update(self, _, **values):self.current.update(values)
    def transition(self, _, old, new, reason):
        assert self.task['state']==old;self.task['state']=new
    def recover(self, *args):pass
    def finish_verification(self, _, evidence, state, status, human):
        self.evidence=evidence;self.task.update(state=state,verification_status=status,human_status=human)


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);(self.root/'workspace').mkdir();(self.root/'logs').mkdir()
        task=dict(id=str(uuid.UUID(int=2)),active_execution_id=str(uuid.UUID(int=12)),state='RUNNING',
            resume_of=None,resume_approval=None,session_id=None,turn_id=None,workspace_id=None,role='implementer',
            snapshot_id=None,access='read-only',prompt='offline',acceptance=None,result=None,cancel_requested=0,error=None)
        self.store=Store(task);self.active=object.__new__(runner.Runner)
        active=self.active;active.root,active.store,active.binary=self.root,self.store,'/fixture/codex'
        active.launcher=lambda *args:None;active.development=active.signal_cancel=False
        active.resource_limits=resources.policy();active.workroot=lambda _:self.root
        active.launch_task=lambda *args:(['fixture'],{})
        self.policy=fixtures.policy()
        self.policy['capacity']=fixtures.fixtures.v(10000,10*1024**3,1000,10000)
        self.policy['reserve']=fixtures.fixtures.v(1000,2*1024**3,0,0)
        active.scheduler_settings={'resource_admission':self.policy,'runner_resource_policy':resources.policy()}
        active.resource_task_id=task['id'];active.resource_token=str(uuid.uuid4())
        self.clock={'mono':100.,'account':copy.deepcopy(fixtures.ACCOUNT)}
        self.calls=[]
        def observation(rows,previous):
            sample,state=fixtures.fixtures.observation(rows,previous)
            sample['at']=self.clock['mono'];sample['available']=self.policy['capacity'];return sample,state
        for target,value in [('resource_admission.DIRECTORY',self.root/'ledger'),
                ('resource_admission.linux_observation',observation),
                ('resource_admission.time.time',lambda:1000),
                ('resource_admission.time.monotonic',lambda:self.clock['mono'])]:
            p=patch(target,value);p.start();self.addCleanup(p.stop)

    def test_context_delay_and_account_change_require_a_second_fresh_native_read_before_turn(self):
        active=self.active;case=self
        active.resource_control=lambda *args:None
        class Peer:
            def __init__(self,*args,**kwargs):kwargs['on_observed']({99999999:'42'})
            def call(self,method,params,timeout=30):
                case.calls.append((method,case.clock['mono']))
                if method=='initialize':return {}
                if method=='account/read':return copy.deepcopy(case.clock['account'])
                if method=='thread/start':
                    case.clock['mono']=106
                    case.clock['account']['account']['email']='other@example.test'
                    return {'thread':{'id':'thread'},'modelProvider':'openai','approvalPolicy':'never',
                            'sandbox':{'type':'readOnly','networkAccess':False}}
                if method=='thread/unsubscribe':return {}
                raise AssertionError('Unexpected native call: '+method)
            def send(self,*args):pass
            def close(self,*args):return {'exit_code':0,'survivors':[],
                'resources':{'exceeded':None,'released':True,'confirmed':True}}
        with closing(admission.Ledger(self.policy)) as ledger:
            owner=ledger.register_owner(self.root,'standalone')
            ledger.reserve(self.root,self.store.task['id'],active.resource_token,fixtures.pending(),
                           admission.linux_observation,owner=owner)
        with patch.object(runner,'binary_version',return_value='fixture'),patch.object(runner,'Process',Peer):
            active._execute(self.store.task['id'],timeout=120)
        self.assertEqual([at for method,at in self.calls if method=='account/read'],[100,106])
        self.assertFalse(any(method=='turn/start' for method,_ in self.calls))
        self.assertEqual(self.store.current['dispatched'],0)

    def test_saved_result_verification_gets_a_separate_bounded_no_provider_grant(self):
        active=self.active;active.resource_token=active.resource_task_id=None
        task=self.store.task;task.update(state='UNKNOWN',session_id='thread',turn_id='old-turn')
        spec={'argv':['/fixture/offline-test'],'timeout':1}
        task['acceptance']=json.dumps({'artifacts':[],'tests':['offline'],'commands':{'offline':spec},'conditions':[],'human':False})
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);self.db.row_factory=sqlite3.Row
        self.store.db=self.db
        self.db.execute('CREATE TABLE reconciliations(id,task_id,execution_id,facts)')
        self.db.execute('CREATE TABLE decisions(id,task_id,execution_id,kind,actor,reason,reconciliation_id,created)')
        self.db.execute('CREATE TABLE executions(id,owner)')
        self.db.execute("INSERT INTO executions VALUES(?,'{}')",(task['active_execution_id'],))
        proof=str(uuid.uuid4());facts={'workspace':verification.workspace_snapshot(self.root/'workspace'),
            'session':{'codex_succeeded':True,'agent_result':{'result':'saved result'},'turn':{'id':'old-turn','status':'completed'}}}
        self.db.execute('INSERT INTO reconciliations VALUES(?,?,?,?)',(proof,task['id'],task['active_execution_id'],json.dumps(facts)));self.db.commit()
        active.task_lock=lambda _:ExitStack()
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        control=SimpleNamespace(descriptor={'unit':unit,'expected_cgroup':'/sys/fs/cgroup/agentorchestrator.slice/'+unit})
        launches=[];case=self
        class TestProcess:
            def __init__(self,*args,**kwargs):
                with closing(admission.Ledger(case.policy)) as ledger:
                    grants=ledger.held();case.assertEqual(len(grants),1)
                    case.assertEqual(json.loads(grants[0]['demand'])['kind'],'reconciliation')
                    case.assertIsNone(json.loads(grants[0]['demand'])['provider'])
                    case.assertNotEqual(grants[0]['task'],task['id'])
                launches.append(kwargs['resources']);kwargs['on_observed']({99999999:'42'})
                self.proc=SimpleNamespace(stdin=SimpleNamespace(close=lambda:None))
            def next_frame(self,*args):raise EOFError()
            def close(self,**kwargs):return {'exit_code':0,'survivors':[],
                'resources':{'exceeded':None,'released':True,'confirmed':True}}
        with patch('scheduler.require_charged_controller'),\
                patch.object(recovery,'resource_facts',return_value={'released':True}),\
                patch.object(verification,'command_registry',return_value={'offline':spec}),\
                patch.object(verification,'verification_launch',return_value=(['fixture'],{})),\
                patch.object(runner.resources,'Control',return_value=control),\
                patch.object(resources,'scope_released',side_effect=lambda descriptor:descriptor==control.descriptor),\
                patch.object(verification,'Process',TestProcess):
            result=active.resolve(task['id'],proof,'verify','offline reviewer','saved result verified')
        self.assertEqual(len(launches),1);self.assertEqual(result['task_state'],'COMPLETED')
        self.assertEqual(self.store.current['launch_pending'],0)
        self.assertIsNone(active.resource_token);self.assertIsNone(active.resource_task_id)
        with closing(admission.Ledger(self.policy)) as ledger:self.assertEqual(ledger.held(),[])

    def test_direct_unbounded_standalone_is_refused_before_claim_or_native_launch(self):
        import os,time
        import test_ordinary_session_pool as pool
        case=pool.PoolFixture();case.setUp();self.addCleanup(case.doCleanups)
        raw=case.write_config(8);raw.pop('maximum_sessions')
        policy=fixtures.policy();policy['capacity']=fixtures.fixtures.v(100000,100*1024**3,0,0)
        limits=resources.policy();policy['reserve']=fixtures.fixtures.v(limits['cpu_percent']*10,limits['memory_bytes'],0,0)
        policy['providers']['chatgpt']['valid_until']=1000000000000
        raw['resource_admission']=policy;case.config.write_text(json.dumps(raw))
        task={'id':str(uuid.uuid4()),'workspace_id':str(uuid.uuid4()),'state':'QUEUED','active_execution_id':None}
        active=object.__new__(runner.Runner);active.root=case.root;active.development=False
        claim=Mock();active.store=SimpleNamespace(get=lambda _:dict(task),recover=lambda _:None,claim=claim)
        active.workroot=lambda _:case.root;active._execute=Mock()
        scope=Path('/sys/fs/cgroup/agentorchestrator.slice')/('agentorchestrator-'+str(uuid.uuid4())+'.scope')
        reads=[];original=Path.read_text
        def read(path,*args,**kwargs):
            name=str(path);reads.append(name)
            if name=='/proc/self/cgroup':return '0::/'+str(scope.relative_to('/sys/fs/cgroup'))+'\n'
            if name.endswith('/memory.max'):return 'max'
            if name.endswith('/cpu.max'):return 'max 100000'
            return original(path,*args,**kwargs)
        with patch.object(admission,'DIRECTORY',case.root/'ledger'),patch.object(Path,'read_text',read):
            with self.assertRaisesRegex(admission.AdmissionWait,'resource_controller_required'):
                active.run(task['id'])
        self.assertIn(str(scope/'cpu.max'),reads);claim.assert_not_called();active._execute.assert_not_called()
        self.assertEqual(task['state'],'QUEUED')

    def test_a_controller_must_be_both_verified_and_charged_to_the_same_live_process(self):
        import scheduler
        settings=self.active.scheduler_settings
        settings.update(daemon={'cpu_percent':50,'memory_bytes':512*1024**2},codex_pipeline=False)
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        descriptor={'unit':unit,'expected_cgroup':'/sys/fs/cgroup/agentorchestrator.slice/'+unit}
        with patch('daemon.verify_self_limits'),patch.object(scheduler,'controller_descriptor',return_value=descriptor):
            with self.assertRaisesRegex(admission.AdmissionWait,'controller_required'):
                scheduler.require_charged_controller(self.root,settings)
            with closing(admission.Ledger(self.policy)) as ledger:
                owner=ledger.register_owner(self.root,'daemon')
                demand={'kind':'local_control','provider':None,'resources':fixtures.fixtures.v(500,512*1024**2,0,0)}
                ledger.reserve(self.root,str(uuid.uuid4()),str(uuid.uuid4()),demand,admission.linux_observation,
                               scopes=[descriptor],owner=owner)
            scheduler.require_charged_controller(self.root,settings)
            with patch('daemon.verify_self_limits',side_effect=RuntimeError('unbounded')):
                with self.assertRaisesRegex(admission.AdmissionWait,'controller_required'):
                    scheduler.require_charged_controller(self.root,settings)
            with patch.object(admission,'owner_live',return_value=None):
                with self.assertRaisesRegex(admission.AdmissionWait,'controller_required'):
                    scheduler.require_charged_controller(self.root,settings)

    def test_packaged_host_ledgers_are_byte_identical_and_share_the_resource_exception(self):
        root=Path(__file__).resolve().parents[1]
        copies=[root/path for path in ['runtime/private_codex/src/resource_admission_core.py',
            'inference/laf_service/resource_admission_core.py',
            'runtime/public_laf/inference/laf_service/resource_admission_core.py']]
        self.assertEqual(len({p.read_bytes() for p in copies}),1)
        self.assertIs(resources.ResourceExceeded,admission.ResourceExceeded)

    def test_pipeline_account_read_age_starts_before_a_delayed_rpc(self):
        from codex_app_protocol import ProtocolClient
        import test_codex_catalog_effort as native
        from pipeline_resource_admission import PipelineAdmission
        case=self
        def exchange(request,timeout):
            if request['method']=='initialized':return None
            if request['method']=='account/read':
                case.clock['mono']=103
                return {'id':request['id'],'result':{'requiresOpenaiAuth':True,**fixtures.ACCOUNT}}
            return {'id':request['id'],'result':native.INITIALIZE}
        client=ProtocolClient(exchange);client.initialize()
        gateway=SimpleNamespace(protocol=client,rpc=lambda method,params,**kwargs:client.request(method,params))
        settings={'resource_admission':{**self.policy,'pipeline':fixtures.fixtures.v(1,1,0,0)}}
        gate=PipelineAdmission(gateway,settings,lambda:None)
        with self.assertRaisesRegex(admission.AdmissionWait,'account_unknown'):gate.account()
        self.assertEqual(client._resource_account_observed_at,100)

    def test_an_account_change_during_a_pipeline_read_cannot_revalidate_old_identity(self):
        from codex_app_protocol import ProtocolClient
        import test_codex_catalog_effort as native
        client=None
        def exchange(request,timeout):
            if request['method']=='initialized':return None
            if request['method']=='account/read':
                client.receive({'method':'account/updated','params':{'authMode':'chatgpt'}})
                return {'id':request['id'],'result':{'requiresOpenaiAuth':True,**fixtures.ACCOUNT}}
            return {'id':request['id'],'result':native.INITIALIZE}
        client=ProtocolClient(exchange);client.initialize()
        result=client.request('account/read',{'refreshToken':False})
        self.assertIsNone(client._resource_account_sha256);self.assertIsNone(client._resource_account_observed_at)
        self.assertNotIn('synthetic@example.test',json.dumps(result))

    def test_ordinary_account_notification_during_the_final_read_blocks_native_turn(self):
        from collections import deque
        active=self.active;active.resource_control=lambda *args:None;case=self
        class Peer:
            def __init__(self,*args,**kwargs):self.notifications=deque();self.reads=0;kwargs['on_observed']({99999999:'42'})
            def call(self,method,params,timeout=30):
                case.calls.append((method,case.clock['mono']))
                if method=='initialize':return {}
                if method=='account/read':
                    self.reads+=1
                    if self.reads==2:self.notifications.append({'method':'account/updated','params':{}})
                    return copy.deepcopy(fixtures.ACCOUNT)
                if method=='thread/start':return {'thread':{'id':'thread'},'modelProvider':'openai',
                    'approvalPolicy':'never','sandbox':{'type':'readOnly','networkAccess':False}}
                if method=='thread/unsubscribe':return {}
                raise AssertionError('Unexpected native call: '+method)
            def send(self,*args):pass
            def close(self,*args):return {'exit_code':0,'survivors':[],
                'resources':{'exceeded':None,'released':True,'confirmed':True}}
        with closing(admission.Ledger(self.policy)) as ledger:
            owner=ledger.register_owner(self.root,'standalone')
            ledger.reserve(self.root,self.store.task['id'],active.resource_token,fixtures.pending(),admission.linux_observation,owner=owner)
        with patch.object(runner,'binary_version',return_value='fixture'),patch.object(runner,'Process',Peer):
            active._execute(self.store.task['id'],timeout=120)
        self.assertFalse(any(method=='turn/start' for method,_ in self.calls))
        self.assertEqual(self.store.current['dispatched'],0)


if __name__=='__main__':unittest.main(verbosity=2)
