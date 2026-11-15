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


# Added 2026-11-15: local residency, GPU/UMA and Router boundary regressions.

"""Offline model/GPU/UMA/Router admission; synthetic kernel and native peers."""
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import sys
import stat
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'runtime/private_codex/src')]
import test_resource_only as cloud
from inference.laf_service import resource_admission_core as shared
from inference.laf_service import gpu_observation as gpu
from inference.laf_service import local_resource_admission as host
from inference.laf_service import core_backends,core_server
from inference.laf_service.core import Controller,ResourceWait,ServiceError
from inference.laf_service.lifecycle import HostLifecycle,LifecycleFault,StaleLease
from inference.laf_service.lifecycle_client import LifecycleClient
from inference.laf_service.test_lifecycle import sample as pressure_sample
from inference.laf_service.test_configured_profiles import selection,profiles,ConfiguredAPI,job


def setUpModule():
    global GUARDS
    GUARDS=[patch(name,side_effect=AssertionError('Offline resource tests forbid external effects'))
        for name in ('subprocess.Popen','socket.socket.bind','socket.socket.connect')]
    for guard in GUARDS:guard.start()


def tearDownModule():
    for guard in reversed(GUARDS):guard.stop()


def ident(number):return str(uuid.UUID(int=number))
def vector(*args):return cloud.fixtures.v(*args)


def policy(topology='discrete'):
    p=cloud.policy();p['capacity']=vector(10000,100000,1000,100000)
    p['reserve']=vector(100,100,0,100)
    p['local_inference']={'device':{'pci_bdf':'0000:00:01.0','vendor':'0x1002','device':'0x9999',
        'memory_topology':topology,'valid_until':2000,'evidence':'synthetic-topology'},
        'controllers':{'host':vector(100,100,0,0),'core':vector(200,2000,0,0),'ollama':vector(1000,10000,0,0)},
        'models':{item['id']:{'engine':'ollama','artifact_sha256':item['digest'][7:],
            'residency':vector(10,100,0,1000),'execution':vector(100,1000,1000,100)} for item in profiles()}}
    return p


class LocalLedger(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.p=policy();self.ledger=shared.Ledger(self.p,self.root)
        self.addCleanup(lambda:self.ledger.close());self.owner=self.ledger.register_owner(self.root,'router')

    def observe(self, rows, previous):
        return {'at':100,'available':vector(100000,1000000,1000,1000000),'gpu_device':'0000:00:01.0',
            'gpu_memory_topology':self.p['local_inference']['device']['memory_topology'],
            'usage':{r['token']:vector(0,0,0,0) for r in rows}},{}

    def reserve(self, n, *, instance=None, model=None, scopes=()):
        model=model or profiles()[0]['id'];phase='residency' if instance is None else 'execution'
        return self.ledger.reserve(self.root,ident(n),ident(1000+n),
            {'kind':'model_residency' if instance is None else 'local_inference','provider':None,
             'resources':self.p['local_inference']['models'][model][phase]},self.observe,
            owner=self.owner,now=100,wall=1000,local_model=model,local_instance=instance,scopes=scopes)

    def test_residency_is_once_and_incremental_executions_reference_the_exact_instance(self):
        instance=self.reserve(1);token=self.reserve(2,instance=instance)
        self.assertEqual(len(self.ledger.held()),2)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM local_instances').fetchone()[0],1)
        with self.assertRaisesRegex(shared.AdmissionWait,'references_held'):
            self.ledger.release(self.root,ident(1),instance,local_released=True,provider_released=True)
        self.assertTrue(self.ledger.release(self.root,ident(2),token,local_released=True,provider_released=True))
        self.assertTrue(self.ledger.release(self.root,ident(1),instance,local_released=True,provider_released=True))

    def test_actual_single_gpu_execution_is_exclusive_while_residency_is_shared(self):
        instance=self.reserve(1);self.reserve(2,instance=instance)
        with self.assertRaisesRegex(shared.AdmissionWait,'gpu_milliunits_budget'):self.reserve(3,instance=instance)
        self.assertEqual(len(self.ledger.held()),2)

    def test_uma_vram_also_consumes_the_same_ram_budget(self):
        self.p['local_inference']['device']['memory_topology']='unified'
        self.p['capacity']['ram_bytes']=1200
        # The open ledger uses a validated copy; recreate before any grant.
        self.ledger.close_owner(self.owner)
        self.ledger.close();self.ledger=shared.Ledger(self.p,self.root)
        self.owner=self.ledger.register_owner(self.root,'router')
        # Clear the unused earlier owner only through its own identity.
        self.reserve(1)
        with self.assertRaisesRegex(shared.AdmissionWait,'ram_bytes_budget'):
            self.ledger.reserve(self.root,ident(2),ident(1002),cloud.pending(cpu=1,ram=1),self.observe,
                                now=100,wall=1000,owner=self.owner)
        self.assertEqual(len(self.ledger.held()),1)

    def test_missing_device_identity_or_topology_never_becomes_local_capacity(self):
        original=self.observe
        def wrong(rows,previous):
            sample,state=original(rows,previous);sample.pop('gpu_device');return sample,state
        self.observe=wrong
        with self.assertRaisesRegex(shared.AdmissionWait,'topology_unknown'):self.reserve(1)
        self.assertEqual(self.ledger.held(),[])

    def test_unknown_instance_and_profile_cannot_be_invented_by_a_job(self):
        with self.assertRaisesRegex(shared.AdmissionWait,'instance_unknown'):self.reserve(2,instance=ident(77))
        demand={'kind':'local_inference','provider':None,'resources':vector(1,1,1000,1)}
        with self.assertRaisesRegex(shared.AdmissionWait,'profile_unknown'):
            self.ledger.reserve(self.root,ident(3),ident(1003),demand,self.observe,owner=self.owner,now=100,wall=1000)

    def test_cpu_ram_and_gpu_scope_credit_are_distinct_and_gpu_credit_has_one_owner(self):
        descriptor={'unit':'docker-'+'a'*64+'.scope','expected_cgroup':'/sys/fs/cgroup/system.slice/docker-'+'a'*64+'.scope'}
        self.ledger.reserve(self.root,ident(8),ident(1008),{'kind':'local_control','provider':None,
            'resources':vector(100,1000,0,0)},self.observe,owner=self.owner,now=100,wall=1000,scopes=[descriptor])
        descriptor['accounting']='gpu';self.reserve(1,scopes=[descriptor])
        with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):
            self.reserve(2,model=profiles()[1]['id'],scopes=[descriptor])

    def test_uncertain_physical_stop_retains_model_and_job_reservations(self):
        instance=self.reserve(1);self.reserve(2,instance=instance)
        with self.assertRaises(shared.AdmissionWait):
            self.ledger.release_local_engine(self.root,self.owner,'ollama',physical_released=False)
        self.assertEqual(len(self.ledger.held()),2)
        self.ledger.release_local_engine(self.root,self.owner,'ollama',physical_released=True)
        self.assertEqual(self.ledger.held(),[])


class KernelGpu(unittest.TestCase):
    def raw(self, *, shared_bytes='0 KiB', resident='8 KiB', client=1):
        return 'drm-driver: amdgpu\ndrm-pdev: 0000:00:01.0\ndrm-client-id: '+str(client)+'\ndrm-resident-vram: '+resident+'\ndrm-shared-vram: '+shared_bytes+'\n'

    def test_shared_buffers_are_not_credited_as_unique_materialization(self):
        device=policy()['local_inference']['device'];raw=self.raw(shared_bytes='2 KiB')
        self.assertEqual(gpu.client_vram(raw,device),(1,6*1024))
        self.assertEqual(gpu.client_vram(raw,device,private=False),(1,8*1024))

    def test_missing_malformed_duplicate_and_unmatched_gpu_evidence(self):
        device=policy()['local_inference']['device']
        for raw in [self.raw().replace('drm-shared-vram: 0 KiB\n',''),self.raw(resident='-1 KiB'),
                    self.raw()+'drm-client-id: 2\n',self.raw(resident='1 GB')]:
            with self.assertRaises(shared.AdmissionWait):gpu.client_vram(raw,device)
        self.assertIsNone(gpu.client_vram(self.raw().replace('0000:00:01.0','0000:00:02.0'),device))

    def test_duplicate_client_views_are_credited_once_and_not_to_two_owners(self):
        desc={'unit':'docker-'+'a'*64+'.scope','expected_cgroup':'/sys/fs/cgroup/system.slice/docker-'+'a'*64+'.scope','accounting':'gpu'}
        rows=[{'token':'one','scopes':json.dumps([desc,desc])}]
        with patch.object(gpu,'scope_clients',return_value={1:1024}):
            self.assertEqual(gpu.owned_vram(rows,policy()['local_inference']['device']),{'one':1024})
            rows.append({'token':'two','scopes':json.dumps([desc])})
            with self.assertRaises(shared.AdmissionWait):gpu.owned_vram(rows,policy()['local_inference']['device'])

    def test_paired_gpu_samples_require_idle_and_use_the_lower_free_memory(self):
        p=policy('unified');observer=gpu.HostObservation(p)
        first={'free':1000,'idle':True,'device':'0000:00:01.0','topology':'unified'}
        last={**first,'free':900,'idle':False}
        native={'at':100,'available':vector(1000,800,0,0),'usage':{}}
        with patch.object(gpu.time,'monotonic',return_value=99.9),patch.object(gpu,'device_sample',side_effect=[first,last]),\
                patch.object(gpu,'owned_vram',return_value={}),patch.object(gpu,'linux_observation',return_value=(native,{})):
            observed,_=observer([],None)
        self.assertEqual(observed['available']['gpu_milliunits'],0)
        self.assertEqual(observed['available']['vram_bytes'],800)
        self.assertEqual(observed['at'],99.9)

    def test_driver_or_pci_identity_mismatch_and_missing_telemetry_fail_closed(self):
        p=policy();observer=gpu.HostObservation(p)
        with patch.object(gpu,'device_sample',side_effect=OSError('synthetic missing counter')):
            with self.assertRaisesRegex(shared.AdmissionWait,'observation_unknown'):observer([],None)
        with tempfile.TemporaryDirectory() as directory:
            device=p['local_inference']['device'];path=Path(directory)/device['pci_bdf'];path.mkdir()
            (path/'vendor').write_text('0xffff');(path/'device').write_text(device['device'])
            with patch.object(gpu,'PCI',Path(directory)),patch.object(gpu.time,'time',return_value=1000):
                with self.assertRaisesRegex(shared.AdmissionWait,'topology_unknown'):gpu.device_sample(device)


class RouterResources(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.p=policy();self.now=100
        self.running=False
        self.core=SimpleNamespace(policy={'nano_cpus':200000000,'memory':2000},
            inspect=lambda:('core',{'engine':'core'}),running=lambda:True)
        self.provider=SimpleNamespace(backend=SimpleNamespace(provider={'cpus':1,'memory_bytes':10000},
            inspect_owned=lambda:{'engine':'ollama'},address='10.0.0.2'),running=lambda:self.running,
            ensure=self.ensure,stop=self.stop)
        self.scopes={name:{'unit':'docker-'+label*64+'.scope','expected_cgroup':'/sys/fs/cgroup/system.slice/docker-'+label*64+'.scope'} for name,label in [('core','a'),('ollama','b')]}
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        self.host_scope={'unit':unit,'expected_cgroup':'/sys/fs/cgroup/agentorchestrator.slice/'+unit}
        def observe(rows,previous):
            return {'at':self.now,'available':vector(100000,1000000,1000,1000000),'gpu_device':'0000:00:01.0',
                'gpu_memory_topology':'discrete','usage':{r['token']:vector(0,0,0,0) for r in rows}},{}
        for target,value in [('inference.laf_service.resource_admission_core.DIRECTORY',self.root/'ledger'),
                ('inference.laf_service.resource_admission_core.time.time',lambda:1000),
                ('inference.laf_service.resource_admission_core.time.monotonic',lambda:self.now),
                ('inference.laf_service.local_resource_admission.container_scope',lambda info:copy.deepcopy(self.scopes[info['engine']]))]:
            guard=patch(target,value);guard.start();self.addCleanup(guard.stop)
        with patch.object(host.LocalAdmission,'_host_scope',return_value=self.host_scope):
            self.authority=host.LocalAdmission(self.p,self.root,self.core,self.provider,observe=observe)
        self.port=HostLifecycle(self.provider,pressure_sample,read_endpoint=lambda:'10.0.0.2',execution_resources=self.authority,panic_callback=Mock())
        self.client=LifecycleClient(lambda request,timeout:self.port.dispatch(request))
        self.client.resource_policy();self.client.stop();self.client.ensure()
        self.addCleanup(self.stop)

    def ensure(self, check):check();self.running=True;check()
    def stop(self):self.running=False
    def held(self):
        with closing(shared.Ledger(self.p)) as ledger:return ledger.held()
    def acquire(self, n=1):
        model=profiles()[0]['id'];instance=self.client.resource_call('model_reserve',model=model,
            artifact='a'*64,request_id=ident(100+n))['instance']
        token=self.client.resource_call('execution_reserve',model=model,instance=instance,request_id=ident(n))['token']
        return instance,token

    def test_native_lifecycle_and_common_ledger_share_residency_job_and_controller_ownership(self):
        instance,token=self.acquire();self.assertEqual(len(self.held()),5)
        with patch.object(self.authority,'_idle_upper',return_value=1000):
            reply=self.client.resource_call('execution_finish',instance=instance,request_id=ident(1),token=token)
        self.assertIs(reply['released'],True);self.assertEqual(len(self.held()),4)
        with patch.object(self.authority,'_idle_upper',return_value=0):self.client.resource_call('model_release',instance=instance)
        self.assertEqual(len(self.held()),3)

    def test_known_no_dispatch_wait_keeps_provider_identity_and_unknown_finish_does_not(self):
        instance,token=self.acquire()
        with self.assertRaises(ResourceWait):self.client.resource_call('execution_reserve',model=profiles()[0]['id'],instance=instance,request_id=ident(2))
        self.assertFalse(self.client.uncertain);self.assertEqual(len(self.held()),5)
        with patch.object(self.authority,'_idle_upper',side_effect=shared.AdmissionWait('resource_gpu_milliunits_headroom')):
            with self.assertRaises(LifecycleFault):self.client.resource_call('execution_finish',instance=instance,request_id=ident(1),token=token)
        self.assertTrue(self.client.uncertain);self.assertEqual(len(self.held()),5)

    def test_stale_generation_cannot_acquire_and_physical_stop_releases_only_local_provider(self):
        instance,token=self.acquire();before=len(self.held())
        with self.assertRaises(StaleLease):self.port.dispatch({'operation':'execution_reserve','epoch':self.port.epoch,
            'generation':self.port.generation-1,'model':profiles()[0]['id'],'instance':instance,'request_id':ident(2)})
        self.assertEqual(len(self.held()),before)
        self.client.stop();self.assertEqual(len(self.held()),2)

    def test_model_manifest_mismatch_and_live_references_never_release(self):
        with self.assertRaises(LifecycleFault):self.client.resource_call('model_reserve',model=profiles()[0]['id'],artifact='c'*64,request_id=ident(4))
        self.assertEqual(len(self.held()),3)

    def test_unknown_kernel_scope_release_retains_all_model_and_job_grants(self):
        self.acquire()
        original=Path.stat
        def scope_info(path,*args,**kwargs):
            if str(path)==self.scopes['ollama']['expected_cgroup']:
                return SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_dev=1,st_ino=1)
            return original(path,*args,**kwargs)
        original_read=Path.read_text
        def read(path,*args,**kwargs):
            if str(path)==self.scopes['ollama']['expected_cgroup']+'/cgroup.events':return 'populated 1\n'
            return original_read(path,*args,**kwargs)
        with patch.object(Path,'stat',scope_info),patch.object(Path,'read_text',read):
            with self.assertRaises(LifecycleFault):self.client.stop()
        self.assertEqual(len(self.held()),5)

    def test_real_core_backend_reserves_before_native_chat_and_releases_after_exact_terminal(self):
        config=selection();config.update(resource_admission_generation=self.authority.generation,
            resource_model_sha256={p['id']:p['digest'][7:] for p in profiles()})
        backend=core_backends.CoreBackends(config,'/synthetic',self.client,namespace_probe=lambda:'pid:[222]',
            process_factory=Mock(side_effect=AssertionError('No Flash in resource mode')))
        api=ConfiguredAPI();calls=[]
        def native(path,body=None,**kwargs):
            if path=='/api/chat':
                calls.append(path)
                self.assertEqual(len(self.held()),5)
                self.assertEqual(json.loads(self.held()[-1]['demand'])['kind'],'local_inference')
            return api(path,body,**kwargs)
        backend.api=native;backend.start_ollama();backend.verify_models();backend.idle_base=pressure_sample()
        with patch.object(self.authority,'_idle_upper',return_value=1000):result=backend.generate(job(),lambda:None)
        self.assertEqual(result['content'],'synthetic reply');self.assertEqual(calls,['/api/chat'])
        self.assertEqual(len(self.held()),4)
        backend.abort();self.assertEqual(len(self.held()),2)

    def test_real_core_does_not_call_native_chat_when_resource_acquisition_is_refused(self):
        self.acquire()
        config=selection();config.update(resource_admission_generation=self.authority.generation,
            resource_model_sha256={p['id']:p['digest'][7:] for p in profiles()})
        backend=core_backends.CoreBackends(config,'/synthetic',self.client,namespace_probe=lambda:'pid:[222]')
        # The existing model grant belongs to another Core request and must not
        # be silently adopted as this backend's instance.
        api=ConfiguredAPI();backend.api=api;backend.start_ollama();backend.verify_models();backend.idle_base=pressure_sample()
        with self.assertRaises(LifecycleFault):backend.generate(job(),lambda:None)
        self.assertFalse(any(path=='/api/chat' for path,_ in api.calls))

    def test_lost_acquisition_ack_retains_the_grant_and_is_never_replayed(self):
        calls=[];original=self.client.exchange
        def lost(request,timeout):
            calls.append(request);original(request,timeout);raise TimeoutError('synthetic lost acknowledgement')
        self.client.exchange=lost
        arguments={'model':profiles()[0]['id'],'artifact':'a'*64,'request_id':ident(5)}
        with self.assertRaises(TimeoutError):self.client.resource_call('model_reserve',**arguments)
        self.assertEqual(len(self.held()),4)
        with self.assertRaises(ServiceError):self.client.resource_call('model_reserve',**arguments)
        self.assertEqual(len(calls),1)
        self.client.exchange=original;self.client.stop()
        self.assertEqual(len(self.held()),2)

    def test_router_and_ordinary_runtimes_use_the_same_durable_state_without_releasing_cloud_grants(self):
        import resource_admission as ordinary
        other=self.root/'ordinary'
        with patch.object(ordinary,'DIRECTORY',self.root/'ledger'):
            with closing(ordinary.Ledger(self.p)) as ledger:
                owner=ledger.register_owner(other,'standalone')
                token=ledger.reserve(other,ident(90),ident(190),cloud.pending(cpu=1,ram=1),self.authority.observe,owner=owner)
                ledger.authorize_provider(other,ident(90),token,'chatgpt',ordinary.account_fingerprint(cloud.ACCOUNT))
            self.acquire();self.client.stop()
            with closing(ordinary.Ledger(self.p)) as ledger:
                row=ledger.join(other,ident(90),token)
                self.assertEqual(json.loads(row['demand'])['kind'],'cloud_api')
        self.assertEqual(len(self.held()),3)


class QueueResourceWait(unittest.TestCase):
    def test_oldest_waiting_job_drains_before_newer_work_without_replaying_a_turn(self):
        permit=threading.Event();waiting=threading.Event();native=[]
        class Backend:
            models={'tiny'};requires_flash=False
            def generate(self,job,check):
                if not permit.is_set():waiting.set();raise ResourceWait('resource_ram_bytes_headroom')
                native.append(job.id);return {'content':'ok'}
            def abort(self):pass
            def close(self):pass
        controller=Controller(Backend(),pressure_sample);self.addCleanup(controller.close)
        body={'model':'tiny','messages':[{'role':'user','content':'synthetic'}]}
        first=controller.submit(body);self.assertTrue(waiting.wait(1))
        second=controller.submit(body)
        for _ in range(100):
            if first.status=='queued' and first.wait_reason:break
            time.sleep(.005)
        self.assertEqual(first.public().get('resource_wait_reason'),'resource_ram_bytes_headroom')
        self.assertFalse(native);self.assertFalse(first.done.is_set())
        permit.set();self.assertTrue(first.done.wait(2));self.assertTrue(second.done.wait(2))
        self.assertEqual(native,[first.id,second.id])

    def test_ambiguous_backend_result_fails_once_and_never_requeues(self):
        calls=[]
        class Backend:
            models={'tiny'};requires_flash=False
            def generate(self,job,check):calls.append(job.id);raise LifecycleFault('native outcome unknown')
            def abort(self):pass
            def close(self):pass
        controller=Controller(Backend(),pressure_sample);self.addCleanup(controller.close)
        job=controller.submit({'model':'tiny','messages':[{'role':'user','content':'synthetic'}]})
        self.assertTrue(job.done.wait(1));self.assertEqual(job.status,'failed');self.assertEqual(calls,[job.id])


class LocalPreflight(unittest.TestCase):
    def test_gpu_partition_capacity_is_refused_without_a_verified_partition(self):
        p=policy();p['capacity']['gpu_milliunits']=2000
        with self.assertRaises(ValueError):shared.policy(p)

    def test_core_owned_model_without_flash_loading_profile_is_refused_before_host_registration(self):
        p=policy();p['local_inference']['models'][profiles()[0]['id']]['engine']='core'
        with patch.object(host,'Ledger') as ledger:
            with self.assertRaisesRegex(ValueError,'local model profile'):
                host.LocalAdmission(p,'/synthetic',SimpleNamespace(),SimpleNamespace())
        ledger.assert_not_called()

    def test_core_configuration_requires_exact_generation_manifest_and_enabled_asset_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'core.json'
            base={**selection(),'state_dir':str(root/'state'),'runtime_dir':str(root/'runtime'),
                'lifecycle_runtime_dir':str(root/'host'),'resource_admission_generation':'f'*64,
                'resource_model_sha256':{p['id']:p['digest'][7:] for p in profiles()}}
            path.write_text(json.dumps(base));path.chmod(0o600)
            self.assertEqual(core_server.load_config(path),base)
            for field,value in [('resource_admission_generation','bad'),('resource_model_sha256',{}),('flash_enabled',True)]:
                path.write_text(json.dumps({**base,field:value}))
                with self.assertRaises(ServiceError):core_server.load_config(path)

    def test_mismatched_host_generation_is_refused_before_backend_or_runtime_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'core.json'
            base={**selection(),'state_dir':str(root/'state'),'runtime_dir':str(root/'runtime'),
                'lifecycle_runtime_dir':str(root/'host'),'resource_admission_generation':'f'*64,
                'resource_model_sha256':{p['id']:p['digest'][7:] for p in profiles()}}
            path.write_text(json.dumps(base));path.chmod(0o600)
            client=SimpleNamespace(resource_policy=lambda:{'epoch':'a'*32,'swap_limit_mib':512,'resource_admission_generation':'b'*64})
            factory=Mock()
            with self.assertRaisesRegex(ServiceError,'generation mismatch'):
                core_server.run_core(path,client_factory=lambda _:client,backend_factory=factory,install_signals=False)
            factory.assert_not_called();self.assertFalse((root/'state').exists())

    def test_unbounded_host_supervisor_is_refused_before_any_ledger_write(self):
        authority=object.__new__(host.LocalAdmission);authority.profiles=policy()['local_inference']
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        scope='/sys/fs/cgroup/agentorchestrator.slice/'+unit
        def read(path,*args,**kwargs):
            if str(path)=='/proc/self/cgroup':return '0::'+scope.removeprefix('/sys/fs/cgroup')
            if str(path).endswith('/cpu.max'):return 'max 100000'
            if str(path).endswith('/memory.max'):return 'max'
            if str(path).endswith('/pids.max'):return '512'
            if str(path).endswith('/memory.swap.max'):return '0'
            raise AssertionError(str(path))
        with patch.object(Path,'read_text',read):
            with self.assertRaisesRegex(shared.AdmissionWait,'controller_required'):authority._host_scope()


class KernelScopeEvidence(unittest.TestCase):
    def fixture(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);scope=root/'scope';scope.mkdir();(scope/'cgroup.procs').write_text('42\n')
        files=root/'proc/42/fdinfo';files.mkdir(parents=True)
        raw=KernelGpu().raw
        (files/'1').write_text(raw(resident='8 KiB'))
        (files/'2').write_text(raw(resident='4 KiB'))
        self.enterContext(patch.object(gpu,'_device_nodes',return_value=({os.makedev(226,128)},None)))
        self.enterContext(patch.object(gpu,'_fd',return_value=(stat.S_IFCHR|0o600,1,2,os.makedev(226,128))))
        return root,scope

    def test_duplicate_fd_credit_uses_minimum_but_release_upper_bound_uses_maximum(self):
        root,scope=self.fixture();device=policy()['local_inference']['device']
        with patch.object(gpu,'PROC',root/'proc'),patch.object(gpu,'_pid_snapshot',return_value='same-start'):
            self.assertEqual(gpu.scope_clients(scope,device),{1:4*1024})
            self.assertEqual(gpu.scope_clients(scope,device,private=False),{1:8*1024})

    def test_pid_reuse_permissions_and_telemetry_work_exhaustion_never_credit_usage(self):
        root,scope=self.fixture();device=policy()['local_inference']['device']
        with patch.object(gpu,'PROC',root/'proc'):
            for effect in ([1,2],PermissionError('synthetic')):
                with patch.object(gpu,'_pid_snapshot',side_effect=effect):
                    with self.assertRaises((shared.AdmissionWait,PermissionError)):gpu.scope_clients(scope,device)
            with self.assertRaisesRegex(shared.AdmissionWait,'observation_stale'):
                gpu.scope_clients(scope,device,budget=[0])


class GpuDescriptorEvidence(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.scope=self.root/'scope';self.scope.mkdir();(self.scope/'cgroup.procs').write_text('42\n')
        self.fdinfo=self.root/'proc/42/fdinfo';self.fdinfo.mkdir(parents=True);self.file=self.fdinfo/'1'
        self.file.write_text(KernelGpu().raw())
        self.device=policy()['local_inference']['device'];pci=self.root/'pci';target=pci/self.device['pci_bdf'];target.mkdir(parents=True)
        drm=self.root/'drm';node=drm/'renderD128';node.mkdir(parents=True)
        (node/'device').symlink_to(target);self.number=node/'dev';self.number.write_text('226:128')
        self.kfd=self.root/'kfd';self.kfd.mkdir();(self.kfd/'dev').write_text('235:0')
        for name,value in (('PROC',self.root/'proc'),('PCI',pci),('DRM',drm),('KFD',self.kfd)):
            self.enterContext(patch.object(gpu,name,value))
        self.enterContext(patch.object(gpu,'_pid_snapshot',return_value='pinned-start'))
        self.descriptor=(stat.S_IFCHR|0o600,1,2,os.makedev(226,128))
        self.enterContext(patch.object(gpu,'_fd',side_effect=lambda path:self.descriptor))

    def capture(self,private=False):return gpu.scope_clients(self.scope,self.device,private=private)

    def test_positive_fd_and_counter_device_binding_still_reports_upper_or_private_bytes(self):
        self.file.write_text(KernelGpu().raw(shared_bytes='2 KiB'))
        self.assertEqual(self.capture(),{1:8192});self.assertEqual(self.capture(private=True),{1:6144})

    def test_generic_only_owned_gpu_descriptor_never_proves_zero_or_materialization(self):
        self.file.write_text('pos: 0\nflags: 0100002\nmnt_id: 41\nino: 928\n')
        for private in (True,False):
            with self.assertRaisesRegex(shared.AdmissionWait,'gpu_counters_unknown'):self.capture(private)

    def test_regular_descriptors_are_ignored_but_forged_gpu_counter_identity_is_unknown(self):
        self.descriptor=(stat.S_IFREG|0o600,1,2,0)
        with self.assertRaisesRegex(shared.AdmissionWait,'usage_unknown'):self.capture()
        self.file.write_text('pos: 0\n');self.assertEqual(self.capture(),{})

    def test_kfd_descriptor_is_unknown_even_without_drm_counters_and_never_opens_device(self):
        self.descriptor=(stat.S_IFCHR|0o600,1,2,os.makedev(235,0));self.file.write_text('pos: 0\n')
        with self.assertRaisesRegex(shared.AdmissionWait,'gpu_ownership_unknown'):self.capture()

    def test_wrong_pci_counters_on_the_selected_drm_device_cannot_be_ignored(self):
        self.file.write_text(KernelGpu().raw().replace('0000:00:01.0','0000:00:02.0'))
        with self.assertRaisesRegex(shared.AdmissionWait,'gpu_counters_unknown'):self.capture()

    def test_descriptor_stat_denial_or_replacement_retains_unknown(self):
        for change in (PermissionError('synthetic'),[self.descriptor,(stat.S_IFREG|0o600,1,3,0)]):
            with patch.object(gpu,'_fd',side_effect=change),self.assertRaises((shared.AdmissionWait,PermissionError)):self.capture()

    def test_sysfs_device_disappearance_or_rebinding_is_unknown(self):
        original=gpu._device_nodes;calls=[0]
        def changed(device,check):
            calls[0]+=1
            if calls[0]==2:self.number.write_text('226:129')
            return original(device,check)
        with patch.object(gpu,'_device_nodes',side_effect=changed),self.assertRaisesRegex(shared.AdmissionWait,'topology_unknown'):self.capture()
        self.number.unlink()
        with self.assertRaises(FileNotFoundError):self.capture()

    def test_missing_kfd_is_distinct_from_denied_kernel_metadata(self):
        (self.kfd/'dev').unlink();self.kfd.rmdir();self.assertEqual(self.capture(),{1:8192})
        self.kfd.mkdir();(self.kfd/'dev').write_text('235:0');original=os.stat
        def denied(path,*args,**kwargs):
            if isinstance(path,(str,bytes,os.PathLike)) and Path(path)==self.kfd:raise PermissionError('synthetic metadata denial')
            return original(path,*args,**kwargs)
        with patch.object(os,'stat',denied),self.assertRaises(PermissionError):self.capture()

    def test_final_slow_descriptor_read_cannot_return_stale_zero_evidence(self):
        now=[100.0];self.file.write_text('pos: 0\n');self.descriptor=(stat.S_IFREG|0o600,1,2,0)
        calls=[0]
        def descriptor(path):
            calls[0]+=1
            if calls[0]==2:now[0]+=2
            return self.descriptor
        with patch.object(gpu.time,'monotonic',side_effect=lambda:now[0]),patch.object(gpu,'_fd',side_effect=descriptor),self.assertRaisesRegex(shared.AdmissionWait,'observation_stale'):self.capture()


class WireResourceWait(unittest.TestCase):
    def test_framed_wait_requires_fixed_code_and_pre_dispatch_acquisition_operation(self):
        from inference.laf_service.lifecycle_uds import FramedExchange,TransportError,encode
        stream=SimpleNamespace(close=lambda:None);exchange=FramedExchange(lambda _:stream)
        response={'ok':False,'error':'resource_wait','code':'resource_cpu_millicores_headroom'}
        with patch('inference.laf_service.lifecycle_uds.verify_peer'),patch('inference.laf_service.lifecycle_uds.write_frame'),\
                patch('inference.laf_service.lifecycle_uds.read_frame',side_effect=lambda *args:encode(response)):
            with self.assertRaises(ResourceWait):exchange({'operation':'execution_reserve'},1)
            with self.assertRaises(TransportError):exchange({'operation':'execution_finish'},1)
            response['code']='private arbitrary text'
            with self.assertRaises(TransportError):exchange({'operation':'execution_reserve'},1)


class LocalReviewRegressions(unittest.TestCase):
    def test_earlier_resource_semantics_cannot_join_a_live_new_protocol_generation(self):
        import hashlib
        case=LocalLedger();case.setUp();self.addCleanup(case.doCleanups)
        case.reserve(1)
        validated=shared.policy(case.p)
        for old in (validated,{'protocol':1,'policy':validated},{'protocol':2,'policy':validated},{'protocol':3,'policy':validated},{'protocol':4,'policy':validated}):
            previous=hashlib.sha256(json.dumps(old,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            self.assertNotEqual(shared.generation(case.p),previous)
            with case.ledger.db:case.ledger.db.execute('UPDATE admission_meta SET generation=? WHERE id=1',(previous,))
            with self.assertRaisesRegex(shared.AdmissionWait,'generation_held'):shared.Ledger(case.p,case.root)
            self.assertEqual(len(case.ledger.held()),1)

    def test_missing_drm_identity_is_unknown_for_admission_and_release(self):
        device=policy()['local_inference']['device'];raw=KernelGpu().raw()
        for malformed in [raw.replace('drm-pdev: 0000:00:01.0\n',''),
                          raw.replace('drm-driver: amdgpu\n',''),
                          raw.replace('0000:00:01.0','malformed-device')]:
            with self.assertRaisesRegex(shared.AdmissionWait,'usage_unknown'):gpu.client_vram(malformed,device)
            with self.assertRaisesRegex(shared.AdmissionWait,'usage_unknown'):gpu.client_vram(malformed,device,private=False)
        self.assertIsNone(gpu.client_vram('pos: 0\nflags: 0100000\n',device))

    def test_discrete_declaration_still_couples_gpu_promises_and_headroom_to_ram(self):
        p=policy('discrete');demand={'kind':'model_residency','provider':None,'resources':vector(10,100,0,1000)}
        self.assertEqual(shared.effective_resources(demand,p['local_inference']['device'])['ram_bytes'],1100)
        native={'at':100,'available':vector(1000,800,0,0),'usage':{}}
        sample={'free':1000,'idle':True,'device':'0000:00:01.0','topology':'discrete'}
        with patch.object(gpu,'device_sample',return_value=sample),patch.object(gpu,'owned_vram',return_value={}),\
                patch.object(gpu,'linux_observation',return_value=(native,{})):
            observed,_=gpu.HostObservation(p)([],None)
        self.assertEqual(observed['available']['vram_bytes'],800)

    def startup(self, *, permanently_unknown=False):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        p=policy();clock={'now':100.};calls=[]
        core=SimpleNamespace(policy={'nano_cpus':200000000,'memory':2000})
        provider=SimpleNamespace(backend=SimpleNamespace(provider={'cpus':1,'memory_bytes':10000}))
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        descriptor={'unit':unit,'expected_cgroup':'/sys/fs/cgroup/agentorchestrator.slice/'+unit}
        def observe(rows,previous):
            calls.append(clock['now'])
            ready=previous is not None and clock['now']-previous['baseline']>=.05 and not permanently_unknown
            available=vector(10000,100000,1000,100000)
            if not ready:available['cpu_millicores']=None
            return {'at':clock['now'],'available':available,'usage':{r['token']:vector(0,0,0,0) for r in rows}},previous or {'baseline':clock['now']}
        with patch.object(shared,'DIRECTORY',root/'ledger'),patch.object(shared.time,'monotonic',side_effect=lambda:clock['now']),\
                patch.object(shared.time,'time',return_value=1000),\
                patch.object(host.time,'sleep',side_effect=lambda seconds:clock.__setitem__('now',clock['now']+seconds)),\
                patch.object(host.LocalAdmission,'_host_scope',return_value=descriptor):
            if permanently_unknown:
                with self.assertRaisesRegex(shared.AdmissionWait,'cpu_millicores_unknown'):
                    host.LocalAdmission(p,root,core,provider,observe=observe)
                with closing(shared.Ledger(p)) as ledger:
                    self.assertEqual(ledger.held(),[])
                    self.assertFalse(ledger.db.execute("SELECT 1 FROM admission_owners WHERE phase='HELD'").fetchone())
            else:
                authority=host.LocalAdmission(p,root,core,provider,observe=observe)
                with closing(shared.Ledger(p)) as ledger:self.assertEqual(len(ledger.held()),2)
        self.assertLessEqual(clock['now'],101.01);self.assertLessEqual(len(calls),17)
        return calls

    def test_cold_router_constructor_warms_cpu_without_starting_a_provider(self):
        calls=self.startup();self.assertGreaterEqual(len(calls),3)

    def test_missing_cpu_evidence_stops_bounded_warmup_without_partial_grants(self):
        self.assertEqual(len(self.startup(permanently_unknown=True)),17)

    def test_resource_supervisor_waits_for_explicit_core_start_without_rolling_policy(self):
        from inference.laf_service.test_host_supervisor import SupervisorTest
        fixture=SupervisorTest();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.config['resource_admission']=policy()
        authority=Mock()
        with patch.object(host,'LocalAdmission',return_value=authority):supervisor=fixture.make()
        supervisor.panic=Mock()
        supervisor.tick()
        self.assertTrue(supervisor.awaiting_core);supervisor.panic.assert_not_called();authority.ready_core.assert_not_called()

    def test_generic_dead_owner_cleanup_never_substitutes_for_gpu_physical_release(self):
        case=LocalLedger();case.setUp();self.addCleanup(case.doCleanups)
        descriptor={'unit':'docker-'+'a'*64+'.scope','expected_cgroup':'/sys/fs/cgroup/system.slice/docker-'+'a'*64+'.scope','accounting':'gpu'}
        instance=case.reserve(1,scopes=[descriptor]);case.reserve(2,instance=instance)
        with patch.object(shared,'owner_live',return_value=False):result=case.ledger.reconcile_owner(case.owner,apply=True)
        self.assertEqual(result['local_gpu_grants_retained'],2);self.assertEqual(result['local_grants_releasable'],0)
        self.assertEqual(len(case.ledger.held()),2)
        self.assertFalse(case.ledger.db.execute('SELECT 1 FROM resource_reconciliations').fetchone())

    def test_model_release_with_missing_pci_identity_keeps_its_real_ledger_grant(self):
        case=RouterResources();case.setUp();self.addCleanup(case.doCleanups)
        instance,token=case.acquire()
        with patch.object(case.authority,'_idle_upper',return_value=1000):
            case.client.resource_call('execution_finish',instance=instance,request_id=ident(1),token=token)
        root,scope=KernelScopeEvidence.fixture(case)
        (root/'proc/42/fdinfo/1').write_text(KernelGpu().raw().replace('drm-pdev: 0000:00:01.0\n',''))
        sample={'idle':True}
        with patch.object(host,'_scope_path',return_value=scope),patch.object(host,'device_sample',return_value=sample),\
                patch.object(gpu,'PROC',root/'proc'),patch.object(gpu,'_pid_snapshot',return_value='same-start'):
            with self.assertRaises(LifecycleFault):case.client.resource_call('model_release',instance=instance)
        self.assertEqual(len(case.held()),4)


if __name__=='__main__':unittest.main(verbosity=2)
