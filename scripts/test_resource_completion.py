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


# Added 2026-11-15: standalone controller and dead-Router recovery boundaries.

"""Offline scopes, SQLite and pinned fake device evidence; no native services."""
from contextlib import closing, ExitStack
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'runtime/private_codex/src')]
import standalone
import scheduler
import daemon
import runner
import resource_admission as private
import test_resource_only as cloud
import test_local_resource_admission as local
from inference.laf_service import resource_admission_core as shared
from inference.laf_service import resource_recovery as recovery
from inference.laf_service import gpu_observation as gpu


def args(command='run',**kwargs):
    return SimpleNamespace(command=command,dispatch_token=None,offline=False,kind='verify',**kwargs)


class ExecOnly(BaseException):pass


class Standalone(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.p=cloud.policy()
        self.p['capacity']=cloud.fixtures.v(100000,100*1024**3,1000,10000)
        self.p['reserve']=cloud.fixtures.v(1000,2*1024**3,0,0)
        self.settings={'resource_admission':self.p,'daemon':daemon.scheduler.configuration()['daemon'],
                       'pool_generation':'a'*64,'codex_pipeline':False}
        self.unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        self.descriptor={'unit':self.unit,'expected_cgroup':'/sys/fs/cgroup/agentorchestrator.slice/'+self.unit}
        self.receipt=self.root/'resource-events'/(self.unit+'.json')
        self.receipt.parent.mkdir(mode=0o700);self.receipt.write_text(json.dumps({
            'state':'READY','unit':self.unit,'cgroup':self.descriptor['expected_cgroup'],'limits':self.settings['daemon']}))
        self.receipt.chmod(0o600)
        self.marker=json.dumps({'unit':self.unit,'receipt':str(self.receipt),'generation':'a'*64})
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for effect in ('subprocess.Popen','socket.socket.bind','socket.socket.connect'):
            self.stack.enter_context(patch(effect,side_effect=AssertionError('Offline test external effect')))
        for target,value in ((private,'DIRECTORY'),):
            self.stack.enter_context(patch.object(target,value,self.root/'shared'))
        self.stack.enter_context(patch.object(scheduler,'configuration',return_value=self.settings))
        self.stack.enter_context(patch.object(scheduler,'require_pool_generation',return_value=self.settings))
        self.stack.enter_context(patch.object(scheduler,'controller_descriptor',return_value=self.descriptor))
        self.stack.enter_context(patch.object(daemon,'verify_self_limits'))
        self.stack.enter_context(patch.dict(os.environ,{standalone.MARKER:self.marker}))
        self.observe=Mock(side_effect=self.observation)
        self.stack.enter_context(patch.object(private,'linux_observation',self.observe))

    def observation(self,rows,previous):
        sample,state=cloud.fixtures.observation(rows,previous)
        sample.update(at=private.time.monotonic());sample['available']=copy.deepcopy(self.p['capacity'])
        return sample,state

    def test_resource_cli_coordinator_joins_actual_scope_before_any_runner(self):
        with standalone.controller(self.root,args()) as settings:
            self.assertEqual(settings,self.settings)
            scheduler.require_charged_controller(self.root,self.settings)
            with closing(private.Ledger(self.p)) as ledger:
                row=ledger.held()[0];demand=json.loads(row['demand'])
                self.assertEqual(demand['provider'],None)
                self.assertEqual(demand['resources']['ram_bytes'],self.settings['daemon']['memory_bytes'])
                self.assertEqual(json.loads(row['scopes'])[0]['expected_cgroup'],self.descriptor['expected_cgroup'])
        with closing(private.Ledger(self.p)) as ledger:
            self.assertEqual(len(ledger.held()),1)  # Own live populated scope is retained.
            owner=ledger.db.execute("SELECT role,phase FROM admission_owners").fetchone()
            self.assertEqual(tuple(owner),('standalone_controller','HELD'))

    def test_missing_marker_execs_exact_cli_into_capped_scope_without_fallback(self):
        captured={}
        def execve(path,argv,env):
            captured.update(path=path,argv=argv,env=env);raise ExecOnly()
        with patch.dict(os.environ,{},clear=True),patch.object(os,'execve',side_effect=execve):
            with self.assertRaises(ExecOnly):
                with standalone.controller(self.root,args(),argv=['--runtime',str(self.root),'run',str(uuid.uuid4())]):
                    self.fail('Unbounded continuation')
        self.assertEqual(captured['path'],'/usr/bin/systemd-run')
        self.assertIn('-pMemoryMax='+str(self.settings['daemon']['memory_bytes']),captured['argv'])
        self.assertIn('-pMemorySwapMax=0',captured['argv']);self.assertIn('--expand-environment=no',captured['argv'])
        binding=json.loads(captured['env'][standalone.MARKER]);self.assertEqual(binding['generation'],'a'*64)
        self.assertEqual(self.observe.call_count,0)

    def test_forged_marker_or_other_scope_cannot_create_a_grant(self):
        bad=json.loads(self.marker);bad['unit']='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        for marker in ('{}',json.dumps(bad),self.marker.replace('a'*64,'b'*64)):
            with patch.dict(os.environ,{standalone.MARKER:marker}),self.assertRaisesRegex(private.AdmissionWait,'controller_required'):
                with standalone.controller(self.root,args()):pass
        self.assertEqual(self.observe.call_count,0)

    def test_receipt_link_mode_final_state_and_changed_limits_are_refused(self):
        for change in ('mode','state','limits','symlink'):
            data={'state':'READY','unit':self.unit,'cgroup':self.descriptor['expected_cgroup'],'limits':self.settings['daemon']}
            self.receipt.unlink();self.receipt.write_text(json.dumps(data));self.receipt.chmod(0o600)
            if change=='mode':self.receipt.chmod(0o644)
            elif change=='symlink':
                target=self.root/'other';target.write_text(json.dumps(data));self.receipt.unlink();self.receipt.symlink_to(target)
            else:
                if change=='state':data['state']='FINISHED'
                else:data['limits']={**data['limits'],'pids':65}
                self.receipt.write_text(json.dumps(data))
            with self.assertRaisesRegex(private.AdmissionWait,'controller_required'):
                with standalone.controller(self.root,args()):pass
        self.assertEqual(self.observe.call_count,0)

    def test_only_uncommitted_cpu_baseline_warmup_retries(self):
        count=0
        def warming(rows,previous):
            nonlocal count
            count+=1;sample,state=self.observation(rows,previous)
            if count<3:sample['available']['cpu_millicores']=None
            return sample,state
        with patch.object(private,'linux_observation',side_effect=warming),patch.object(standalone.time,'sleep') as sleep:
            with standalone.controller(self.root,args()):pass
        self.assertEqual(count,3);self.assertEqual(sleep.call_count,2)
        with closing(private.Ledger(self.p)) as ledger:self.assertEqual(len(ledger.held()),1)

    def test_non_cpu_wait_is_not_retried_and_empty_owner_closes(self):
        with patch.object(private,'linux_observation',side_effect=private.AdmissionWait('resource_observation_unknown')) as observe:
            with self.assertRaisesRegex(private.AdmissionWait,'observation_unknown'):
                with standalone.controller(self.root,args()):pass
        self.assertEqual(observe.call_count,1)
        with closing(private.Ledger(self.p)) as ledger:
            self.assertEqual(ledger.held(),[])
            self.assertEqual(ledger.db.execute('SELECT phase FROM admission_owners').fetchone()[0],'RELEASED')

    def test_direct_runner_still_needs_charged_controller_and_native_cli_is_bounded(self):
        with self.assertRaisesRegex(private.AdmissionWait,'controller_required'):
            scheduler.require_charged_controller(self.root,self.settings)
        self.assertTrue(standalone.required(args('check')))
        self.assertTrue(standalone.required(args('run-pair')))
        dispatched=args();dispatched.dispatch_token=str(uuid.uuid4());self.assertFalse(standalone.required(dispatched))
        offline=args('reconcile');offline.offline=True;self.assertFalse(standalone.required(offline))
        self.assertFalse(standalone.required(args('submit')))

    def test_runner_main_enters_boundary_before_runner_constructor(self):
        with patch.object(runner,'PROJECT',self.root),patch.object(sys,'argv',['runner.py','run',str(uuid.uuid4())]),                patch.object(standalone,'controller',side_effect=ExecOnly()),patch.object(runner,'Runner') as native:
            with self.assertRaises(ExecOnly):runner.main()
        native.assert_not_called()


class RouterLedger(local.LocalLedger):
    def snapshot(self):return self.ledger.router_snapshot(self.root,self.owner)
    def proof(self,candidate):
        return {'kind':'owned_router_exit','owner':self.owner,'generation':self.ledger.generation,
                'providers_stopped':True,'scopes_empty':True,'paired_idle':True}
    def gpu_rows(self):
        unit='docker-'+'a'*64+'.scope'
        instance=self.reserve(1,scopes=[{'unit':unit,'expected_cgroup':'/sys/fs/cgroup/system.slice/'+unit,'accounting':'gpu'}])
        self.reserve(2,instance=instance)
        return instance

    def test_dead_router_requires_physical_verifier_and_atomic_candidate(self):
        self.gpu_rows()
        with patch.object(shared,'owner_live',return_value=False):
            candidate,fingerprint=self.snapshot()
            with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):
                self.ledger.reconcile_router_owner(self.root,self.owner,fingerprint,lambda value:{},apply=True)
            ready=self.ledger.reconcile_router_owner(self.root,self.owner,fingerprint,self.proof)
            self.assertEqual(ready['status'],'READY');self.assertEqual(len(self.ledger.held()),2)
            result=self.ledger.reconcile_router_owner(self.root,self.owner,fingerprint,self.proof,apply=True)
        self.assertEqual(result['local_grants_releasable'],2);self.assertEqual(self.ledger.held(),[])
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM resource_reconciliations').fetchone()[0],2)

    def run_recovery(self,apply,final_delay=0):
        self.gpu_rows()
        identity=shared.owner_identity();identity['boot_id']=str(uuid.uuid4())
        self.ledger.db.execute('UPDATE admission_owners SET identity=? WHERE id=?',(json.dumps(identity),self.owner));self.ledger.db.commit()
        generation_lock=self.root/'generation.lock';generation_lock.touch(mode=0o600)
        (self.root/'supervisor.lock').touch(mode=0o600)
        config={'resource_admission':self.p,'state_dir':str(self.root),'generation_lock':str(generation_lock),'core':{},'ollama':{}}
        core=SimpleNamespace(inspect=Mock(return_value=None),stop=Mock())
        provider=SimpleNamespace(inspect_current=Mock(return_value=None),stop=Mock())
        now=[100.0]
        sample={'mem_available':32*recovery.GIB,'swap_used':0,'vram_total':64*recovery.GIB,'vram_used':0,'pswpin':0,'pswpout':0,'psi_full_avg10':0.0}
        device={'idle':True,'used':0,'total':64*recovery.GIB,'device':'0000:00:01.0'}
        calls=[0]
        def pressure():
            calls[0]+=1
            if calls[0]==3:now[0]+=final_delay
            return dict(sample)
        obj=recovery.RouterRecovery(config,'/synthetic/config',core_factory=lambda policy:core,
            provider_factory=lambda policy,path:provider,read_resources=pressure,read_device=lambda profile:dict(device),
            clock=lambda:now[0],sleep=lambda seconds:now.__setitem__(0,now[0]+seconds))
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        descriptor={'unit':unit,'expected_cgroup':'/sys/fs/cgroup/agentorchestrator.slice/'+unit}
        def observe(rows,previous,**kwargs):
            measured,state=self.observe(rows,previous);measured['at']=shared.time.monotonic();return measured,state
        with patch.object(shared,'DIRECTORY',self.root),patch.object(recovery,'linux_observation',side_effect=observe),\
                patch.object(recovery.LocalAdmission,'_host_scope',return_value=descriptor):
            result=obj.run(self.owner,apply=apply)
        core.stop.assert_not_called();provider.stop.assert_not_called()
        return result

    def test_full_fenced_recovery_budgets_diagnostic_and_applies_only_old_local_grants(self):
        result=self.run_recovery(True)
        self.assertEqual(result['status'],'APPLIED');self.assertEqual(len(self.ledger.held()),1)
        held=self.ledger.held()[0]
        self.assertEqual(json.loads(held['demand'])['kind'],'reconciliation')
        self.assertTrue(result['diagnostic_grant_retained_until_scope_exit'])

    def test_full_dry_run_keeps_target_gpu_and_job_grants_held(self):
        result=self.run_recovery(False)
        self.assertEqual(result['status'],'READY');self.assertEqual(len(self.ledger.held()),3)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM resource_reconciliations').fetchone()[0],0)

    def test_full_slow_final_read_keeps_both_gpu_grants_and_writes_no_release(self):
        with self.assertRaisesRegex(shared.AdmissionWait,'observation_stale'):self.run_recovery(True,final_delay=10)
        self.assertEqual(len(self.ledger.held()),3)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM resource_reconciliations').fetchone()[0],0)

    def test_full_clock_rollback_during_final_read_keeps_both_gpu_grants(self):
        with self.assertRaisesRegex(shared.AdmissionWait,'observation_stale'):self.run_recovery(True,final_delay=-1)
        self.assertEqual(len(self.ledger.held()),3)

    def test_full_fixed_name_replacement_with_running_provider_retains_grants(self):
        from inference.laf_service.lifecycle_host import OwnedOllama
        from inference.laf_service.test_provider_policy import ProviderPolicyTests,ID
        fixture=ProviderPolicyTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        provider=OwnedOllama({**fixture.config,'ollama_provider':fixture.policy},'/synthetic/config')
        backend=provider.backend;backend.provider_image_id=ID
        first,second='a'*64,'b'*64
        info_a=fixture.container(backend);info_a.update(Id=first,Name='/reviewed-provider')
        info_a['State'].update(Restarting=False,Pid=0);info_a['HostConfig']['RestartPolicy']={'Name':'no'}
        info_b=copy.deepcopy(info_a);info_b.update(Id=second);info_b['State'].update(Running=True,Pid=123)
        lookup=[first];calls=[]
        def docker(*command,**kwargs):
            calls.append(command)
            if command[0]=='ps':return lookup[0]
            if command[0]=='inspect':return json.dumps([info_a if command[1]==first else info_b])
            raise AssertionError('No mutating Docker operation allowed')
        backend.docker=docker
        original=recovery.RouterRecovery
        def factory(*args,**kwargs):
            sleep=kwargs['sleep']
            def replace(seconds):
                sleep(seconds)
                if seconds==.5:info_a['Name']='/renamed-old';lookup[0]=second
            kwargs.update(provider_factory=lambda *args:provider,sleep=replace)
            return original(*args,**kwargs)
        with patch.object(recovery,'RouterRecovery',side_effect=factory):
            with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):self.run_recovery(True)
        self.assertGreaterEqual(sum(command[0]=='ps' for command in calls),2)
        self.assertTrue(any(command[0]=='inspect' and command[1]==second for command in calls))
        self.assertEqual(len(self.ledger.held()),3)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM resource_reconciliations').fetchone()[0],0)

    def test_live_unknown_wrong_runtime_and_generation_refuse(self):
        self.gpu_rows()
        for live in (True,None):
            with patch.object(shared,'owner_live',return_value=live),self.assertRaisesRegex(shared.AdmissionWait,'owner_unknown'):self.snapshot()
        with patch.object(shared,'owner_live',return_value=False):
            with self.assertRaisesRegex(shared.AdmissionWait,'owner_unknown'):self.ledger.router_snapshot(self.root/'other',self.owner)
            self.ledger.db.execute('UPDATE admission_owners SET generation=? WHERE id=?',('0'*64,self.owner));self.ledger.db.commit()
            with self.assertRaisesRegex(shared.AdmissionWait,'owner_unknown'):self.snapshot()

    def test_changed_scope_or_new_row_between_observation_and_commit_refuses_all(self):
        self.gpu_rows()
        with patch.object(shared,'owner_live',return_value=False):
            candidate,fingerprint=self.snapshot()
            self.ledger.db.execute('UPDATE reservations SET created=created+1 WHERE token=?',(local.ident(1002),));self.ledger.db.commit()
            with self.assertRaisesRegex(shared.AdmissionWait,'reservation_retained'):
                self.ledger.reconcile_router_owner(self.root,self.owner,fingerprint,self.proof,apply=True)
        self.assertEqual(len(self.ledger.held()),2)

    def test_provider_uncertainty_remains_held_and_owner_remains_pinned(self):
        self.gpu_rows()
        self.ledger.reserve(self.root,local.ident(3),local.ident(1003),cloud.pending(),self.observe,
                            owner=self.owner,now=100,wall=1000)
        with patch.object(shared,'owner_live',return_value=False):
            candidate,fingerprint=self.snapshot()
            result=self.ledger.reconcile_router_owner(self.root,self.owner,fingerprint,self.proof,apply=True)
        self.assertEqual(result['provider_grants_retained'],1)
        self.assertEqual([row['task'] for row in self.ledger.held()],[local.ident(3)])
        self.assertEqual(self.ledger.db.execute('SELECT phase FROM admission_owners WHERE id=?',(self.owner,)).fetchone()[0],'HELD')

    def test_foreign_or_missing_model_reference_is_retained(self):
        self.gpu_rows()
        self.ledger.db.execute('DELETE FROM local_references');self.ledger.db.commit()
        with patch.object(shared,'owner_live',return_value=False),self.assertRaisesRegex(shared.AdmissionWait,'instance_unknown'):self.snapshot()
        self.assertEqual(len(self.ledger.held()),2)


class RouterProof(unittest.TestCase):
    def setUp(self):
        self.p=local.policy();self.now=100.0
        self.sample={'mem_available':32*recovery.GIB,'swap_used':0,'vram_total':64*recovery.GIB,
                     'vram_used':0,'pswpin':0,'pswpout':0,'psi_full_avg10':0.0}
        self.device={'idle':True,'used':0,'free':64*recovery.GIB,'total':64*recovery.GIB,'device':'0000:00:01.0'}
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.config={'resource_admission':self.p,'state_dir':temp.name,'swap_limit_mib':512}
        self.inspect=Mock(return_value=None)
        self.provider=SimpleNamespace(inspect_current=self.inspect)
        self.obj=recovery.RouterRecovery(self.config,'/synthetic/config',read_resources=lambda:dict(self.sample),
            read_device=lambda device:dict(self.device),clock=lambda:self.now,sleep=self.tick)
        self.obj.core=SimpleNamespace(inspect=self.inspect);self.obj.provider=self.provider
        identity=shared.owner_identity();identity['boot_id']=str(uuid.uuid4())
        self.candidate={'owner':{'id':local.ident(8),'identity':json.dumps(identity)},'grants':[]}

    def tick(self,seconds):self.now+=seconds

    def test_paired_pinned_device_and_fresh_empty_scopes_are_required_at_commit(self):
        self.obj._proof(self.candidate,'a'*64)
        proof=self.obj._verify(self.candidate)
        self.assertTrue(proof['paired_idle']);self.assertEqual(proof['device'],'0000:00:01.0')
        self.now+=3
        with self.assertRaisesRegex(shared.AdmissionWait,'observation_stale'):self.obj._verify(self.candidate)

    def test_busy_device_foreign_activity_swap_or_psi_block_release(self):
        for target,key,bad in ((self.device,'idle',False),(self.device,'used',3*recovery.GIB),
                               (self.sample,'psi_full_avg10',2),(self.sample,'mem_available',7*recovery.GIB)):
            old=target[key];target[key]=bad
            with self.assertRaises((shared.AdmissionWait,recovery.ServiceError)):self.obj._proof(self.candidate,'a'*64)
            target[key]=old
        def tick(seconds):self.now+=seconds;self.sample['pswpout']+=1
        self.obj.sleep=tick
        with self.assertRaisesRegex(shared.AdmissionWait,'reservation_retained'):self.obj._proof(self.candidate,'a'*64)

    def test_running_restarting_or_auto_restart_container_never_stops_or_releases(self):
        base={'Id':'a'*64,'State':{'Running':False,'Restarting':False,'Pid':0},'HostConfig':{'RestartPolicy':{'Name':'no'}}}
        self.obj.core=SimpleNamespace(inspect=Mock(return_value=('a'*64,base)))
        for key,value in (('Running',True),('Restarting',True),('Pid',123)):
            changed=copy.deepcopy(base);changed['State'][key]=value;self.obj.core.inspect.return_value=('a'*64,changed)
            with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):self.obj._stopped()
        changed=copy.deepcopy(base);changed['HostConfig']['RestartPolicy']['Name']='always';self.obj.core.inspect.return_value=('a'*64,changed)
        with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):self.obj._stopped()
        self.obj.core.inspect.return_value=('a'*64,base);self.assertEqual(self.obj._stopped(),['a'*64,None])

    def test_swap_motion_after_paired_proof_still_blocks_commit(self):
        self.obj._proof(self.candidate,'a'*64)
        self.sample['pswpin']+=1
        with self.assertRaisesRegex(shared.AdmissionWait,'reservation_retained'):self.obj._verify(self.candidate)

    def test_fixed_provider_name_id_or_ambiguous_lookup_is_refused(self):
        from inference.laf_service.lifecycle_host import OwnedOllama
        backend=SimpleNamespace(config={'container_name':'fixed'},cid='cached',docker=Mock(return_value='a'*64),
            inspect_owned=Mock(return_value={'Id':'a'*64,'Name':'/renamed'}))
        provider=object.__new__(OwnedOllama);provider.backend=backend
        with self.assertRaises(recovery.ServiceError):provider.inspect_current()
        self.assertEqual(backend.cid,'cached')
        backend.inspect_owned.return_value={'Id':'b'*64,'Name':'/fixed'}
        with self.assertRaises(recovery.ServiceError):provider.inspect_current()
        backend.docker.return_value='a'*64+'\n'+'b'*64
        with self.assertRaises(recovery.ServiceError):provider.inspect_current()

    def test_expired_calibration_never_admits_but_does_not_prevent_positive_release(self):
        # No device is read on this machine. The release reader deliberately
        # checks pinned current identity without reauthorizing a new workload.
        device=self.p['local_inference']['device'];device['valid_until']=1
        with self.assertRaisesRegex(shared.AdmissionWait,'topology_unknown'):gpu.device_sample(device)
        with patch.object(gpu,'release_sample',return_value=dict(self.device)) as read:
            with patch.object(gpu.time,'time',return_value=0):gpu.device_sample(device)
        read.assert_called_once_with(device)

    def test_populated_or_unreadable_scope_blocks_and_missing_old_scope_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            scope=Path(directory)/'scope';scope.mkdir();(scope/'cgroup.events').write_text('populated 1\n')
            candidate={'grants':[{'demand':json.dumps({'provider':None}),'scopes':json.dumps([{'unit':'synthetic'}])}]}
            with patch.object(recovery,'_scope_path',return_value=scope):
                with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):recovery.empty_scopes(candidate)
                (scope/'cgroup.events').write_text('populated 0\n');recovery.empty_scopes(candidate)
                (scope/'cgroup.events').unlink()
                with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):recovery.empty_scopes(candidate)
                scope.rmdir();recovery.empty_scopes(candidate)


class CalibrationPlan(unittest.TestCase):
    def config(self):return {'resource_admission':local.policy()}

    def test_plan_is_exact_finite_scope_and_preserves_target_mode(self):
        config=self.config()
        with tempfile.TemporaryDirectory() as directory:
            for apply in (False,True):
                plan=recovery.plan_command(config,str(Path(directory)/'private config.json'),local.ident(9),apply=apply)
                self.assertFalse(plan['executes']);self.assertEqual(plan['argv'][0],'/usr/bin/systemd-run')
                self.assertIn('-pMemorySwapMax=0',plan['argv']);self.assertIn('-pTasksMax=64',plan['argv'])
                self.assertIn('-pCPUQuota=10%',plan['argv']);self.assertIn('-pMemoryMax=100',plan['argv'])
                self.assertEqual('--apply' in plan['argv'],apply)
                self.assertEqual(plan['working_directory'],str(ROOT))

    def test_insufficient_reserve_and_noncanonical_owner_do_not_produce_a_command(self):
        config=self.config();config['resource_admission']['reserve']['ram_bytes']=99
        with self.assertRaisesRegex(shared.AdmissionWait,'controller_required'):
            recovery.plan_command(config,'/tmp/config',local.ident(9))
        with self.assertRaises(ValueError):recovery.plan_command(self.config(),'/tmp/config','unknown')

    def test_plan_cli_never_creates_provider_ledger_or_cgroup(self):
        with patch.object(recovery,'read_config',return_value=self.config()),patch.object(recovery,'RouterRecovery') as owner,\
                patch.object(recovery,'Ledger',side_effect=AssertionError('No ledger in plan')),patch('builtins.print'):
            self.assertEqual(recovery.main(['--config','/tmp/config','--owner',local.ident(9),'--plan']),0)
        owner.assert_not_called()


if __name__=='__main__':unittest.main()
