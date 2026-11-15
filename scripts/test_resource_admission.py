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

# Added 2026-11-15: atomic resource admission and boundary regressions.

"""Offline durable-ledger and ordinary handoff boundaries; no real provider."""
from contextlib import closing, ExitStack
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime/private_codex/src'))
from scripts.source_test_isolation import require_isolated
require_isolated()  # Refuse raw resource tests before any fixture or project import.
import resource_admission as admission
import scheduler
import daemon
import runner
import legacy_tool_adapter
import mcp_adapter
import test_ordinary_session_pool as pool
task = pool.task


def v(cpu=10000, ram=10000, gpu=1000, vram=10000):
    return dict(zip(admission.DIMENSIONS, (cpu, ram, gpu, vram)))


def policy():
    return {'schema': 1, 'capacity': v(), 'reserve': v(0, 0, 0, 0),
            'max_age_seconds': 2, 'workload': 'cloud_api',
            'providers': {'chatgpt': {'limit': 100, 'valid_until': 2000,
                                      'evidence': 'synthetic-test-evidence'}}}


def demand(cpu=100, ram=100, gpu=0, vram=0):
    return {'kind': 'local_inference' if gpu or vram else 'cloud_api',
            'provider': None if gpu or vram else 'chatgpt',
            'resources': v(cpu, ram, gpu, vram)}


def observation(rows, previous):
    return {'at': 100, 'available': v(), 'gpu_memory_topology': 'discrete',
            'usage': {r['token']: v(0, 0, 0, 0) for r in rows}}, {'warm': True}


class Reservations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.ledger = admission.Ledger(policy(), self.directory)
        self.addCleanup(lambda: self.ledger.close())

    def reserve(self, number, value=None, observe=observation, **kwargs):
        return self.ledger.reserve(self.directory, str(uuid.UUID(int=number)),
            str(uuid.UUID(int=1000 + number)), value or demand(), observe, now=100, wall=1000, **kwargs)

    def test_vector_and_policy_reject_unknown_invalid_and_ambiguous_values(self):
        for value in (True, -1, .5, '100', None, 2**63):
            raw = v(); raw['ram_bytes'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): admission.vector(raw)
        for field, value in (('max_age_seconds', float('inf')), ('max_age_seconds', True),
                             ('schema', True), ('workload', 'unknown')):
            raw = policy(); raw[field] = value
            with self.assertRaises(ValueError): admission.policy(raw)

    def test_reservation_is_incremental_and_durable(self):
        self.reserve(1)
        self.reserve(2)
        self.assertEqual(len(self.ledger.held()), 2)
        with closing(admission.Ledger(policy(), self.directory)) as other:
            self.assertEqual(len(other.held()), 2)

    def test_all_dimensions_commit_together_or_not_at_all(self):
        with self.assertRaisesRegex(admission.AdmissionWait, 'vram_bytes_budget'):
            self.reserve(1, demand(100, 100, 100, 10001))
        self.assertEqual(self.ledger.held(), [])
        self.reserve(2, demand(100, 100, 100, 100))
        self.assertEqual(len(self.ledger.held()), 1)

    def test_stale_and_future_samples_fail_closed(self):
        for at in (90, 101, float('nan'), None):
            def observe(rows, previous):
                sample, state = observation(rows, previous); sample['at'] = at
                return sample, state
            with self.subTest(at=at), self.assertRaises(admission.AdmissionWait):
                self.reserve(1, observe=observe)
        self.assertEqual(self.ledger.held(), [])

    def test_unknown_ram_and_gpu_do_not_become_free_capacity(self):
        for dimension, value in (('ram_bytes', demand()), ('gpu_milliunits', demand(gpu=100, vram=100))):
            def observe(rows, previous):
                sample, state = observation(rows, previous); sample['available'][dimension] = None
                return sample, state
            with self.subTest(dimension=dimension), self.assertRaisesRegex(admission.AdmissionWait, dimension + '_unknown'):
                self.reserve(1, value, observe)

    def test_cloud_api_does_not_need_local_gpu_telemetry(self):
        def observe(rows, previous):
            sample, state = observation(rows, previous)
            sample['available']['gpu_milliunits'] = sample['available']['vram_bytes'] = None
            return sample, state
        self.reserve(1, observe=observe)
        self.assertEqual(len(self.ledger.held()), 1)
        with self.assertRaises(ValueError): admission.request({'kind': 'cloud_api', 'provider': 'chatgpt', 'resources': v(100,100,1,1)})

    def test_unified_or_unknown_gpu_topology_is_not_two_free_pools(self):
        for topology in (None, 'unified'):
            def observe(rows, previous):
                sample, state = observation(rows, previous); sample['gpu_memory_topology'] = topology
                return sample, state
            with self.assertRaisesRegex(admission.AdmissionWait, 'topology_unknown'):
                self.reserve(1, demand(gpu=100, vram=100), observe)

    def test_unmaterialized_memory_is_charged_against_observed_headroom(self):
        self.reserve(1, demand(ram=1000))
        def observe(rows, previous):
            sample, state = observation(rows, previous); sample['available']['ram_bytes'] = 2000
            return sample, state
        with self.assertRaisesRegex(admission.AdmissionWait, 'ram_bytes_headroom'):
            self.reserve(2, demand(ram=1500), observe)
        self.assertEqual(len(self.ledger.held()), 1)

    def test_materialized_memory_is_not_subtracted_twice(self):
        self.reserve(1, demand(ram=2000))
        def observe(rows, previous):
            sample, state = observation(rows, previous); sample['available']['ram_bytes'] = 2000
            sample['usage'][rows[0]['token']]['ram_bytes'] = 1500
            return sample, state
        self.reserve(2, demand(ram=1500), observe)
        self.assertEqual(len(self.ledger.held()), 2)

    def test_provider_quota_is_atomic_and_requires_unexpired_evidence(self):
        p = policy(); p['providers']['chatgpt']['limit'] = 1
        self.ledger.close(); self.ledger = admission.Ledger(p, self.directory)
        self.reserve(1)
        with self.assertRaisesRegex(admission.AdmissionWait, 'provider_quota$'): self.reserve(2)
        self.assertEqual(len(self.ledger.held()), 1)
        with self.assertRaisesRegex(admission.AdmissionWait, 'provider_quota_unknown'):
            self.ledger.reserve(self.directory, str(uuid.UUID(int=3)), str(uuid.UUID(int=1003)), demand(), observation, now=100, wall=2001)
        self.ledger.close(); self.ledger = admission.Ledger(p, self.directory)

    def test_provider_evidence_expiring_during_observation_cannot_create_a_grant(self):
        p=policy();p['providers']['chatgpt']['valid_until']=1001
        self.ledger.close();self.ledger=admission.Ledger(p,self.directory)
        clock={'wall':1000}
        def observe(rows, previous):
            clock['wall']=1002
            return observation(rows,previous)
        with patch.object(admission.time,'time',side_effect=lambda:clock['wall']):
            with self.assertRaisesRegex(admission.AdmissionWait,'provider_quota_unknown'):
                self.ledger.reserve(self.directory,str(uuid.UUID(int=1)),str(uuid.UUID(int=1001)),
                                    demand(),observe,now=100)
        self.assertEqual(self.ledger.held(),[])

    def test_warmup_observation_is_saved_without_a_partial_grant(self):
        def cold(rows, previous):
            sample, state = observation(rows, previous); sample['available']['cpu_millicores'] = None
            return sample, {'cpu_baseline': 'synthetic'}
        with self.assertRaises(admission.AdmissionWait): self.reserve(1, observe=cold)
        self.assertEqual(self.ledger.held(), [])
        self.assertEqual(json.loads(self.ledger.db.execute('SELECT observation FROM admission_meta').fetchone()[0]), {'cpu_baseline': 'synthetic'})

    def test_policy_generation_cannot_rebind_held_reservations(self):
        self.reserve(1)
        p = policy(); p['capacity']['ram_bytes'] += 1
        with self.assertRaisesRegex(admission.AdmissionWait, 'generation_held'):
            admission.Ledger(p, self.directory)
        self.assertEqual(len(self.ledger.held()), 1)

    def test_a_physical_scope_cannot_be_credited_to_two_reservations(self):
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        descriptor={'unit':unit,'expected_cgroup':'/sys/fs/cgroup/agentorchestrator.slice/'+unit}
        self.reserve(1,scopes=[descriptor])
        with self.assertRaisesRegex(admission.AdmissionWait,'scope_unknown'):
            self.reserve(2,scopes=[descriptor])
        token=self.reserve(3)
        with self.assertRaisesRegex(admission.AdmissionWait,'scope_unknown'):
            self.ledger.add_scope(self.directory,str(uuid.UUID(int=3)),token,descriptor)
        self.assertEqual(len(self.ledger.held()),2)

    def test_task_identity_and_token_cannot_replay_or_join_foreign_grants(self):
        token = self.reserve(1)
        with self.assertRaisesRegex(admission.AdmissionWait, 'retained'): self.reserve(1)
        with self.assertRaisesRegex(admission.AdmissionWait, 'missing'):
            self.ledger.join(self.directory, str(uuid.UUID(int=2)), token)
        with self.assertRaisesRegex(admission.AdmissionWait, 'missing'):
            self.ledger.join(self.directory, str(uuid.UUID(int=1)), str(uuid.UUID(int=9)))

    def test_expiry_and_incomplete_cleanup_do_not_release_capacity(self):
        token = self.reserve(1)
        for local, provider in ((False, True), (True, False), (None, True), (True, 1)):
            self.assertFalse(self.ledger.release(self.directory, str(uuid.UUID(int=1)), token,
                                                 local_released=local, provider_released=provider))
        self.assertEqual(len(self.ledger.held()), 1)
        self.assertTrue(self.ledger.release(self.directory, str(uuid.UUID(int=1)), token,
                                            local_released=True, provider_released=True))
        self.assertEqual(self.ledger.held(), [])

    def test_private_ledger_rejects_symlinks_and_public_directory(self):
        with tempfile.TemporaryDirectory() as other:
            directory = Path(other) / 'public'; directory.mkdir(mode=0o755)
            directory.chmod(0o755)  # Deliberate unsafe fixture despite a private caller umask.
            with self.assertRaises(ValueError): admission.Ledger(policy(), directory)
            link = Path(other) / 'link'; link.symlink_to(self.directory, target_is_directory=True)
            with self.assertRaises(ValueError): admission.Ledger(policy(), link)

    def test_concurrent_connections_cannot_overcommit_cpu_or_ram(self):
        p = policy(); p['capacity'] = v(250,250,1000,10000)
        self.ledger.close(); self.ledger = admission.Ledger(p, self.directory)
        entered=[]; errors=[]; barrier=threading.Barrier(8)
        def contender(number):
            try:
                with closing(admission.Ledger(p, self.directory)) as ledger:
                    barrier.wait(timeout=5)
                    try:
                        ledger.reserve(self.directory, str(uuid.UUID(int=number)), str(uuid.UUID(int=number+1000)), demand(), observation, now=100, wall=1000)
                        entered.append(number)
                    except admission.AdmissionWait: pass
            except BaseException as exc: errors.append(exc)
        threads=[threading.Thread(target=contender,args=(n,)) for n in range(1,9)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        self.assertFalse(any(t.is_alive() for t in threads)); self.assertEqual(errors, [])
        self.assertEqual(len(entered),2); self.assertEqual(len(self.ledger.held()),2)


class OrdinaryIntegration(pool.PoolFixture):
    fake_daemon = pool.PoolDispatcher.fake_daemon

    def configured(self):
        obj = self.fake_daemon(8)
        p=policy(); p['capacity']=v(100000,100*1024**3,1000,10000)
        p['providers']['chatgpt']['valid_until']=time.time()+60
        raw=json.loads(self.config.read_text()); raw['resource_admission']=p
        self.config.write_text(json.dumps(raw)); obj.settings=scheduler.configuration()
        obj.runner.scheduler_settings=obj.settings
        self.patched(patch.object(admission,'DIRECTORY',self.root/'host-ledger'))
        def observe(rows, previous):
            sample,state=observation(rows,previous);sample['at']=time.monotonic();sample['available']=p['capacity']
            return sample,state
        self.patched(patch.object(admission,'linux_observation',observe))
        return obj

    def test_daemon_commits_host_reservation_before_fake_process_handoff(self):
        obj=self.configured();obj.runner.binary='/fixture/codex';obj.owner={}
        obj.worker_factory=lambda *args:['fixture'];obj.event=lambda *args,**kwargs:None
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        control=SimpleNamespace(unit=unit,descriptor={'unit':unit})
        original=Path.read_text
        def read(path,*args,**kwargs):
            if str(path)=='/proc/self/cgroup':return '0::/agentorchestrator.slice/fake-daemon.scope\n'
            return original(path,*args,**kwargs)
        def launch(*args,**kwargs):
            with closing(admission.Ledger(obj.settings['resource_admission'])) as ledger:
                self.assertEqual(len(ledger.held()),1)
            return SimpleNamespace(seen={})
        with patch.object(daemon,'Control',return_value=control),patch.object(Path,'read_text',read),patch.object(daemon,'Process',side_effect=launch):
            daemon.Daemon.start_worker(obj,obj.store.get(task(1)['id']))
        with closing(admission.Ledger(obj.settings['resource_admission'])) as ledger:
            row=ledger.held()[0];self.assertEqual(row['task'],task(1)['id'])
            self.assertEqual(len(json.loads(row['scopes'])),1)
            scheduler.join_task_resources(obj.root,task(1)['id'],obj.settings,row['token'])

    def test_unknown_and_missing_provider_terminal_keep_the_whole_reservation(self):
        obj=self.configured();token=str(uuid.uuid4())
        scheduler.reserve_task_resources(obj.root,task(1)['id'],obj.settings,token,worker=True)
        obj.store.db.execute("UPDATE tasks SET state='UNKNOWN' WHERE id=?",(task(1)['id'],))
        self.assertFalse(scheduler.finish_task_resources(obj.root,obj.store,task(1)['id'],obj.settings,worker_released=True))
        obj.store.db.execute("UPDATE tasks SET state='FAILED',active_execution_id='execution' WHERE id=?",(task(1)['id'],))
        obj.store.execution=lambda _:{'dispatched':1,'terminal':None}
        with patch('recovery.resource_facts',return_value={'released':True}):
            self.assertFalse(scheduler.finish_task_resources(obj.root,obj.store,task(1)['id'],obj.settings,worker_released=True))
            obj.store.execution=lambda _:{'dispatched':1,'terminal':'{"status":"failed"}'}
            self.assertTrue(scheduler.finish_task_resources(obj.root,obj.store,task(1)['id'],obj.settings,worker_released=True))

    def test_resume_probe_scope_binds_executing_task_not_inspected_source(self):
        obj=self.configured();token=str(uuid.uuid4())
        scheduler.reserve_task_resources(obj.root,task(1)['id'],obj.settings,token,worker=True)
        obj.store.db.execute('CREATE TABLE executions(id TEXT,task_id TEXT,owner TEXT)')
        obj.store.db.execute("INSERT INTO executions VALUES('resume-execution',?,'{}')",(task(1)['id'],))
        obj.store.db.commit()
        obj.store.execution=lambda key:dict(obj.store.db.execute('SELECT * FROM executions WHERE id=?',(key,)).fetchone())
        fake=object.__new__(runner.Runner);fake.root=obj.root;fake.store=obj.store
        fake.resource_limits=obj.runner.resource_limits;fake.scheduler_settings=obj.settings
        fake.resource_token=token;fake.resource_task_id=task(1)['id']
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        descriptor={'unit':unit,'expected_cgroup':'/sys/fs/cgroup/agentorchestrator.slice/'+unit}
        control=SimpleNamespace(descriptor=descriptor)
        with patch.object(runner.resources,'Control',return_value=control):
            fake.resource_control(obj.store.get(task(2)['id']),'resume-execution')
        with closing(admission.Ledger(obj.settings['resource_admission'])) as ledger:
            rows=ledger.held();self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['task'],task(1)['id'])
            self.assertEqual(len(json.loads(rows[0]['scopes'])),1)

    def test_resource_wait_codes_cross_both_facades_without_free_text(self):
        for module in (legacy_tool_adapter,mcp_adapter):
            data=module.task_view({'id':task(1)['id'],'state':'QUEUED','scheduler_wait_reason':'resource_ram_bytes_headroom'})
            self.assertEqual(data['scheduler_wait_reason'],'resource_ram_bytes_headroom')

    def test_standalone_orders_workspace_exclusion_before_resource_reservation(self):
        calls=[]
        fake=object.__new__(runner.Runner)
        fake.root=self.root;fake.development=False;fake.resource_token=None
        fake.store=SimpleNamespace(get=lambda _:task(1),recover=lambda _:calls.append('recover'),
                                   claim=lambda *args:calls.append('claim'))
        fake.workroot=lambda _:self.root
        settings={'resource_admission':policy()}
        stack=ExitStack();stack.resource_token='token';stack.resource_settings=settings
        def lock(*args):
            calls.append('workspace');return ExitStack()
        fake.task_lock=lock
        fake._execute=lambda *args:calls.append('execute')
        with patch.object(scheduler,'execution_admission',return_value=stack),patch.object(scheduler,'reserve_task_resources',side_effect=lambda *args,**kwargs:calls.append('reserve')),patch.object(runner,'workspace_snapshot',return_value={}),patch.object(runner,'proc_identity',return_value={}):
            fake.run(task(1)['id'])
        self.assertEqual(calls,['workspace','recover','reserve','claim','execute'])
        self.assertIsNone(fake.resource_token)


class LinuxTelemetry(unittest.TestCase):
    def sample(self, previous=None, *, at=100, stat_line=None, meminfo=None, rows=()):
        line = stat_line or 'cpu0 1000 0 0 1000 0 0 0 0'
        def read(path, *args, **kwargs):
            name=str(path)
            if name=='/proc/stat':return line+'\n'
            if name=='/proc/meminfo':return meminfo or 'MemAvailable: 1000 kB\n'
            if name=='/proc/sys/kernel/random/boot_id':return 'synthetic-boot'
            if name=='/proc/self/cgroup':return '0::/user.slice/agentorchestrator.slice/test.scope'
            if name.endswith('/memory.max'):return '1000000' if 'agentorchestrator.slice' in name else 'max'
            if name.endswith('/memory.current'):return '100000'
            if name.endswith('/cpu.max'):return '100000 100000'
            if name.endswith('/cpu.stat'):return 'usage_usec 0'
            raise OSError('Unexpected synthetic telemetry path')
        with patch.object(Path,'read_text',read),patch.object(os,'sched_getaffinity',return_value={0}),patch.object(admission.time,'monotonic',return_value=at):
            return admission.linux_observation(rows,previous)

    def test_first_window_is_unknown_then_cpu_and_ancestor_ram_are_measured(self):
        sample, state=self.sample()
        self.assertIsNone(sample['available']['cpu_millicores'])
        sample, state=self.sample(state,at=100.2,stat_line='cpu0 1100 0 0 1100 0 0 0 0')
        self.assertEqual(sample['available']['cpu_millicores'],500)
        self.assertEqual(sample['available']['ram_bytes'],900000)
        self.assertIsNone(sample['available']['gpu_milliunits'])
        self.assertIsNone(sample['available']['vram_bytes'])

    def test_counter_rollback_and_malformed_meminfo_refuse_evidence(self):
        _, state=self.sample()
        with self.assertRaises(admission.AdmissionWait):
            self.sample(state,at=100.2,stat_line='cpu0 900 0 0 900 0 0 0 0')
        with self.assertRaises(admission.AdmissionWait):
            self.sample(meminfo='MemAvailable: unknown kB\n')

    def test_paired_ram_growth_and_release_cannot_credit_stale_free_memory(self):
        row={'token':'synthetic','demand':json.dumps(demand(ram=1024)),'scopes':'[]'}
        for free,owned in (((1024,0),(0,1024)),((0,1024),(1024,0))):
            with self.subTest(free=free,owned=owned):
                calls=[]
                def memory(group):
                    calls.append(group)
                    return free[len(calls)-1]
                usage=iter([({'synthetic':owned[0]},{}),({'synthetic':owned[1]}, {})])
                _,state=self.sample()
                with patch.object(admission,'_memory_headroom',side_effect=memory),patch.object(admission,'_owned_usage',side_effect=lambda rows:next(usage)):
                    sample,_=self.sample(state,at=100.2,stat_line='cpu0 1100 0 0 1100 0 0 0 0',rows=[row])
                self.assertEqual(sample['available']['ram_bytes'],0)
                self.assertEqual(sample['usage']['synthetic']['ram_bytes'],0)

    def test_ancestor_cpu_consumption_reduces_remaining_quota(self):
        _,state=self.sample()
        # The nested sample fixture supplies the rest of the synthetic filesystem.
        with patch.object(admission,'_memory_headroom',return_value=10000),patch.object(admission,'_owned_usage',return_value=({},{})):
            def synthetic(path,*args,**kwargs):
                name=str(path)
                if name=='/proc/stat':return 'cpu0 1000 0 0 1200 0 0 0 0'
                if name=='/proc/sys/kernel/random/boot_id':return 'synthetic-boot'
                if name=='/proc/self/cgroup':return '0::/user.slice/agentorchestrator.slice/test.scope'
                if name.endswith('/cpu.max'):return '100000 100000'
                if name.endswith('/cpu.stat'):return 'usage_usec 200000'
                raise OSError('Unexpected synthetic CPU path')
            with patch.object(Path,'read_text',synthetic),patch.object(os,'sched_getaffinity',return_value={0}),patch.object(admission.time,'monotonic',return_value=100.2):
                sample,_=admission.linux_observation([],state)
        self.assertEqual(sample['available']['cpu_millicores'],0)

    def test_boot_cpuset_or_old_window_cannot_be_reused(self):
        _, state=self.sample()
        for field, value in (('boot','different-boot'),('cpus',[0,1]),('at',90)):
            changed=copy.deepcopy(state);changed[field]=value
            sample,_=self.sample(changed,at=100.2)
            self.assertIsNone(sample['available']['cpu_millicores'])


if __name__=='__main__':unittest.main(verbosity=2)
