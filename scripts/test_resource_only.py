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

"""Versioned resource-only dispatch; fake telemetry and owned temporary state."""
from contextlib import closing, ExitStack
import copy
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'runtime/private_codex/src')]
import concurrency
from scripts.source_test_isolation import require_isolated
require_isolated()  # Refuse raw resource tests before any fixture or project import.
import daemon
import resource_admission as admission
import scheduler
import test_ordinary_session_pool as pool
import test_resource_admission as fixtures


ACCOUNT = {'account': {'type': 'chatgpt', 'email': 'synthetic@example.test', 'planType': 'synthetic'}}


def policy():
    value = fixtures.policy()
    value.update(schema=2, admission_mode='resources')
    value['reserve'] = fixtures.v(100, 100, 0, 0)
    value['providers']['chatgpt'].update(limit=500, account_sha256=admission.account_fingerprint(ACCOUNT))
    return value


def pending(cpu=100, ram=100):
    return {'kind': 'local_control', 'provider': 'chatgpt', 'resources': fixtures.v(cpu, ram, 0, 0)}


class OwnerAndProvider(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = admission.Ledger(policy(), self.root)
        self.addCleanup(self.ledger.close)
        self.owner = self.ledger.register_owner(self.root, 'standalone')

    def reserve(self, number=1, *, owner=True):
        task, token = str(uuid.UUID(int=number)), str(uuid.UUID(int=1000 + number))
        self.ledger.reserve(self.root, task, token, pending(), fixtures.observation,
                            now=100, wall=1000, owner=self.owner if owner else None)
        return task, token

    def test_kernel_reboot_proves_old_local_owner_dead_but_same_boot_foreign_namespace_is_unknown(self):
        identity=admission.owner_identity();identity['pid_namespace']='pid:[999999999]'
        self.assertIsNone(admission.owner_live(identity))
        identity['boot_id']=str(uuid.uuid4())
        self.assertIs(admission.owner_live(identity),False)
        identity['uid']=os.getuid()+1
        self.assertIsNone(admission.owner_live(identity))

    def test_an_unregistered_owner_cannot_get_a_grant(self):
        with self.assertRaisesRegex(admission.AdmissionWait, 'owner_unknown'): self.reserve(owner=False)
        self.assertEqual(self.ledger.held(), [])

    def test_dead_or_unknown_owner_does_not_become_reclaimable(self):
        task, token = self.reserve()
        for live in (False, None):
            with patch.object(admission, 'owner_live', return_value=live):
                with self.assertRaisesRegex(admission.AdmissionWait, 'owner_unknown'):
                    self.ledger.join(self.root, task, token)
        self.assertEqual(len(self.ledger.held()), 1)
        self.assertFalse(self.ledger.close_owner(self.owner))

    def test_same_owner_state_is_pinned_even_before_native_fork(self):
        changed = policy(); changed['capacity']['ram_bytes'] += 1
        with self.assertRaisesRegex(admission.AdmissionWait, 'generation_held'):
            admission.Ledger(changed, self.root)
        self.assertTrue(self.ledger.close_owner(self.owner))
        with closing(admission.Ledger(changed, self.root)): pass

    def test_provider_identity_is_verified_before_promoting_an_intent(self):
        task, token = self.reserve()
        other = {'account': {'type': 'chatgpt', 'email': 'other@example.test', 'planType': 'synthetic'}}
        with patch.object(admission.time, 'time', return_value=1000):
            with self.assertRaisesRegex(admission.AdmissionWait, 'account_unknown'):
                self.ledger.authorize_provider(self.root, task, token, 'chatgpt', admission.account_fingerprint(other))
        self.assertEqual(json.loads(self.ledger.held()[0]['demand'])['kind'], 'local_control')
        with patch.object(admission.time, 'time', return_value=1000):
            self.ledger.authorize_provider(self.root, task, token, 'chatgpt', admission.account_fingerprint(ACCOUNT))
        self.assertEqual(json.loads(self.ledger.held()[0]['demand'])['kind'], 'cloud_api')

    def test_provider_intents_already_count_against_real_provider_limit(self):
        self.ledger.policy['providers']['chatgpt']['limit'] = 1
        task, token = self.reserve()
        with self.assertRaisesRegex(admission.AdmissionWait, 'provider_quota'): self.reserve(2)
        with patch.object(admission.time, 'time', return_value=1000):
            self.ledger.authorize_provider(self.root, task, token, 'chatgpt', admission.account_fingerprint(ACCOUNT))

    def test_direct_cloud_grant_cannot_skip_account_authorization(self):
        with self.assertRaisesRegex(admission.AdmissionWait, 'account_unknown'):
            self.ledger.reserve(self.root, str(uuid.UUID(int=1)), str(uuid.UUID(int=2)), fixtures.demand(),
                                fixtures.observation, now=100, wall=1000, owner=self.owner)

    def test_account_hash_has_no_raw_identity_and_missing_identity_is_unknown(self):
        digest = admission.account_fingerprint(ACCOUNT)
        self.assertEqual(len(digest), 64); self.assertNotIn('synthetic', digest)
        for raw in ({}, {'account': {'type': 'chatgpt'}}, {'account': {'type': 'apiKey'}}):
            self.assertIsNone(admission.account_fingerprint(raw))

    def test_recovery_can_use_reserved_capacity_without_freeing_uncertainty(self):
        p = self.ledger.policy
        p['capacity'] = fixtures.v(200, 200, 1000, 10000)
        self.reserve()
        task, token = str(uuid.UUID(int=3)), str(uuid.UUID(int=1003))
        with self.assertRaisesRegex(admission.AdmissionWait, 'budget'): self.reserve(2)
        recovery = pending(); recovery.update(kind='reconciliation', provider=None)
        self.ledger.reserve(self.root, task, token, recovery, fixtures.observation,
                            now=100, wall=1000, owner=self.owner)
        self.assertEqual(len(self.ledger.held()), 2)


class CommonState(unittest.TestCase):
    def test_home_and_codex_home_do_not_select_different_ledgers(self):
        passwd = SimpleNamespace(stat=lambda: SimpleNamespace(st_mode=stat.S_IFREG|0o644,st_uid=0,st_size=100),
                                 is_symlink=lambda:False,
                                 read_text=lambda:'synthetic:x:'+str(os.getuid())+':1000::/owned-os-home:/bin/sh')
        paths=[]
        with patch.object(admission, 'PASSWD', passwd):
            for home in ('/synthetic-home-one', '/synthetic-home-two'):
                with patch.dict(os.environ, {'HOME':home,'CODEX_HOME':home+'/native','XDG_STATE_HOME':home+'/state'}):
                    paths.append(admission.state_directory())
        self.assertEqual(paths[0], paths[1]);self.assertTrue(str(paths[0]).startswith('/owned-os-home/'))

    def test_an_existing_different_home_ledger_requires_explicit_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy=Path(directory)/'.local/state/laf/resource-admission';legacy.mkdir(parents=True)
            (legacy/'reservations.sqlite').touch()
            passwd=SimpleNamespace(stat=lambda:SimpleNamespace(st_mode=stat.S_IFREG|0o644,st_uid=0,st_size=100),
                is_symlink=lambda:False,read_text=lambda:'synthetic:x:'+str(os.getuid())+':1000::/owned-os-home:/bin/sh')
            with patch.object(admission,'PASSWD',passwd),patch.dict(os.environ,{'HOME':directory}):
                with self.assertRaisesRegex(admission.AdmissionWait,'legacy_state_pending'):admission.state_directory()


class ResourceDispatch(pool.PoolFixture):
    fake_daemon = pool.PoolDispatcher.fake_daemon

    def configured(self):
        obj=self.fake_daemon(8)
        raw=json.loads(self.config.read_text());raw.pop('maximum_sessions')
        p=policy();p['capacity']=fixtures.v(100000,100*1024**3,1000,10000)
        p['reserve']=fixtures.v(1000,2*1024**3,0,0)
        p['providers']['chatgpt']['valid_until']=10**12
        raw['resource_admission']=p;self.config.write_text(json.dumps(raw))
        obj.settings=scheduler.configuration();obj.runner.scheduler_settings=obj.settings
        self.patched(patch.object(admission,'DIRECTORY',self.root/'shared'))
        def observe(rows, previous):
            sample,state=fixtures.observation(rows,previous);sample['at']=admission.time.monotonic()
            sample['available']=p['capacity'];return sample,state
        self.patched(patch.object(admission,'linux_observation',observe))
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        self.patched(patch.object(scheduler,'controller_descriptor',return_value={
            'unit':unit,'expected_cgroup':'/sys/fs/cgroup/agentorchestrator.slice/'+unit}))
        return obj

    def test_resource_configuration_has_no_maximum_sessions_or_full_pool_envelope(self):
        obj=self.configured()
        self.assertIsNone(obj.settings['maximum_sessions'])
        self.meminfo.write_text('malformed legacy memory-envelope input')
        self.assertIsNone(scheduler.require_pool_headroom(obj.settings))
        self.assertEqual(obj.status()['admission_mode'],'resources')
        raw=json.loads(self.config.read_text());raw['maximum_sessions']=8
        self.config.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):scheduler.configuration()

    def test_version2_leases_allow_more_than_eight_without_count_slots(self):
        obj=self.configured()
        with concurrency.pool_generation(self.root,obj.settings):
            with ExitStack() as held:
                for number in range(1,13):held.enter_context(concurrency.guard(self.root,pool.task(number),settings=obj.settings))
                for number in range(8):
                    with concurrency.lease(self.root/'leases'/('slot-'+str(number)+'.lock')): pass
            encoded=json.loads((self.root/'leases/pool-generation.lock').read_text())
            self.assertEqual(encoded['schema_version'],2)
            self.assertNotIn('maximum_sessions',encoded)

    def test_legacy_held_slot_prevents_resource_generation_adoption(self):
        obj=self.configured()
        with concurrency.lease(self.root/'leases/slot-7.lock'):
            with self.assertRaises(RuntimeError):concurrency.pool_generation(self.root,obj.settings)

    def test_tick_admits_twelve_finite_grants_and_stops_at_resource_budget(self):
        obj=self.configured()
        db=obj.store.db
        for number in range(12,30):
            candidate=pool.task(number)
            db.execute("INSERT INTO tasks(id,state,workspace_id) VALUES(?,'QUEUED',?)",(candidate['id'],candidate['workspace_id']))
        db.commit()
        # Controller + twelve unchanged runner/worker cgroup ceilings fit exactly.
        p=obj.settings['resource_admission']
        p['capacity']['cpu_millicores']=500+12*2000+1000
        raw=json.loads(self.config.read_text());raw['resource_admission']=p
        self.config.write_text(json.dumps(raw));obj.settings=scheduler.configuration()
        original=obj.start_worker
        def start(candidate):
            scheduler.reserve_task_resources(obj.root,candidate['id'],obj.settings,str(uuid.uuid4()),
                                             worker=True,owner=obj.resource_owner)
            original(candidate)
        obj.start_worker=start
        obj.tick()
        self.assertEqual(len(obj.started),12)
        self.assertIn('resource_cpu_millicores_budget',obj.blocked)
        self.assertNotIn('session_pool_capacity',obj.blocked)

    def test_incomplete_pipeline_accounting_is_refused_instead_of_using_unlimited_slots(self):
        self.configured()
        with self.assertRaises(ValueError):scheduler.configuration(codex_pipeline=True)


class CpuWindowReuse(unittest.TestCase):
    sample=fixtures.LinuxTelemetry.sample

    def test_fast_denied_admissions_do_not_reset_the_cpu_warmup_window(self):
        sample,state=self.sample()
        for at in (100.01,100.02,100.03):
            sample,next_state=self.sample(state,at=at)
            self.assertIsNone(sample['available']['cpu_millicores'])
            self.assertEqual(next_state['at'],100)
            state=next_state
        sample,state=self.sample(state,at=100.06,stat_line='cpu0 1100 0 0 1100 0 0 0 0')
        self.assertEqual(sample['available']['cpu_millicores'],500)

    def test_fast_reservations_preserve_cpu_sample_age_and_refresh_ram(self):
        _,state=self.sample()
        sample,state=self.sample(state,at=100.2,stat_line='cpu0 1100 0 0 1100 0 0 0 0')
        with patch.object(admission,'_memory_headroom',return_value=17):
            fast,next_state=self.sample(state,at=100.21)
        self.assertEqual(fast['available']['cpu_millicores'],500)
        self.assertEqual(fast['available']['ram_bytes'],17)
        self.assertEqual(fast['cpu_observed_at'],100.2)
        self.assertEqual(next_state['at'],100.2)


if __name__=='__main__':unittest.main(verbosity=2)
