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


# Added 2026-11-15: permission denial never proves scope/device cleanup.

"""Real shared ledger/lifecycle with synthetic metadata; no native or service."""
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import stat
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'runtime/private_codex/src')]
import test_local_resource_admission as local
import test_flash_resource_admission as flash
from inference.laf_service import resource_admission_core as shared,resource_recovery as recovery
import resources
import scheduler


def setUpModule():
    global GUARDS
    GUARDS=[patch(name,side_effect=AssertionError('No native/service side effects in scope fixtures'))
        for name in ('subprocess.Popen','socket.socket.bind','socket.socket.connect','threading.Thread.start')]
    for guard in GUARDS:guard.start()


def tearDownModule():
    for guard in reversed(GUARDS):guard.stop()


class ScopeProof(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.group=self.root/'scope';self.group.mkdir();(self.group/'cgroup.events').write_text('populated 0\nfrozen 0\n')

    def deny(self,path):
        original=os.stat
        def denied(target,*args,**kwargs):
            if isinstance(target,(str,bytes,os.PathLike)) and Path(target)==path:
                raise PermissionError('synthetic scope metadata denial')
            return original(target,*args,**kwargs)
        return patch.object(os,'stat',side_effect=denied)

    def test_known_missing_or_stable_zero_is_empty_and_populated_is_retained(self):
        self.assertTrue(shared.scope_empty(self.root/'absent'));self.assertTrue(shared.scope_empty(self.group))
        (self.group/'cgroup.events').write_text('populated 1\n');self.assertFalse(shared.scope_empty(self.group))

    def test_permission_denial_is_unknown_instead_of_absence(self):
        with self.deny(self.group):
            with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):shared.scope_stat(self.group)
            with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):shared.scope_empty(self.group)

    def test_missing_partial_duplicate_or_malformed_events_are_unknown(self):
        events=self.group/'cgroup.events';events.unlink()
        with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):shared.scope_empty(self.group)
        for raw in ('','populated 0\npopulated 1\n','populated unknown\n','populated 0 extra\n','frozen 0\n'):
            events.write_text(raw)
            with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):shared.scope_empty(self.group)

    def test_changed_scope_inode_never_proves_cleanup(self):
        original=Path.read_text
        def replace(path,*args,**kwargs):
            result=original(path,*args,**kwargs)
            if path==self.group/'cgroup.events':
                self.group.rename(self.root/'old-scope');self.group.mkdir();(self.group/'cgroup.events').write_text('populated 0\n')
            return result
        with patch.object(Path,'read_text',replace),self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):
            shared.scope_empty(self.group)

    def test_confirmed_scope_disappearance_after_read_is_positive_absence(self):
        original=Path.read_text
        def remove(path,*args,**kwargs):
            result=original(path,*args,**kwargs)
            if path==self.group/'cgroup.events':path.unlink();self.group.rmdir()
            return result
        with patch.object(Path,'read_text',remove):self.assertTrue(shared.scope_empty(self.group))

    def test_owner_metadata_denial_never_looks_like_an_empty_owner_inventory(self):
        directory=self.root/'ledger';path=directory/'reservations.sqlite'
        with patch.object(shared,'DIRECTORY',directory),self.deny(path):
            with self.assertRaisesRegex(shared.AdmissionWait,'owner_unknown'):shared.owner_views(self.root)

    def test_unreadable_legacy_state_cannot_authorize_an_independent_new_ledger(self):
        legacy=self.root/'.local/state/laf/resource-admission/reservations.sqlite'
        passwd=Mock();passwd.stat.return_value=SimpleNamespace(st_mode=stat.S_IFREG|0o644,st_uid=0,st_size=128)
        passwd.is_symlink.return_value=False;passwd.read_text.return_value='fixture:x:'+str(os.getuid())+':1000:fixture:/canonical-fixture:/bin/false\n'
        with patch.object(shared,'PASSWD',passwd),patch.object(Path,'home',return_value=self.root),self.deny(legacy):
            with self.assertRaisesRegex(shared.AdmissionWait,'legacy_state_pending'):shared.state_directory()

    def test_dead_owner_cannot_release_local_controller_under_denied_scope_metadata(self):
        case=local.LocalLedger();case.setUp();self.addCleanup(case.doCleanups)
        descriptor={'unit':'docker-'+'a'*64+'.scope','expected_cgroup':'/sys/fs/cgroup/system.slice/docker-'+'a'*64+'.scope'}
        token=case.ledger.reserve(case.root,local.ident(900),local.ident(1900),
            {'kind':'local_control','provider':None,'resources':local.vector(100,1000,0,0)},
            case.observe,owner=case.owner,now=100,wall=1000,scopes=[descriptor])
        with patch.object(shared,'owner_live',return_value=False),patch.object(shared,'_scope_path',return_value=self.group),self.deny(self.group):
            with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):case.ledger.reconcile_owner(case.owner,apply=True)
        self.assertEqual([row['token'] for row in case.ledger.held()],[token])
        self.assertFalse(case.ledger.db.execute('SELECT 1 FROM resource_reconciliations').fetchone())

    def test_router_candidate_cannot_prove_empty_scopes_under_denial(self):
        candidate={'grants':[{'demand':json.dumps({'provider':None}),'scopes':json.dumps([{'unit':'synthetic'}])}]}
        with patch.object(recovery,'_scope_path',return_value=self.group),self.deny(self.group):
            with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):recovery.empty_scopes(candidate)

    def flash_case(self):
        case=flash.FlashBoundary();case.setUp();self.addCleanup(case.doCleanups)
        with patch('threading.Thread.start'):case.start()
        instance=case.backend.resource_instances['flash']
        case.fixture.client.resource_call('execution_reserve',model='flash',instance=instance,request_id=local.ident(909))
        return case

    def test_gpu_denied_scope_keeps_exact_model_job_and_controller_grants(self):
        case=self.flash_case();before={row['token'] for row in case.fixture.held()};case.guard.stop()
        with patch.object(local.host,'device_sample',return_value={'idle':True,'used':8192}),patch.object(local.host,'_scope_path',return_value=self.group),self.deny(self.group):
            with self.assertRaises(local.LifecycleFault):case.fixture.client.resource_call('core_models_stopped')
        self.assertEqual(before,{row['token'] for row in case.fixture.held()});self.assertEqual(len(before),5)

    def test_stopped_local_provider_keeps_exact_grants_if_scope_state_is_denied(self):
        case=self.flash_case();before={row['token'] for row in case.fixture.held()}
        with patch.object(local.host,'_scope_path',return_value=self.group),self.deny(self.group):
            with self.assertRaisesRegex(shared.AdmissionWait,'scope_unknown'):case.fixture.authority.stopped('core')
        self.assertEqual(before,{row['token'] for row in case.fixture.held()})

    def test_legacy_finished_receipt_does_not_make_denied_scope_released(self):
        control=resources.Control(self.root,resources.policy());data={'state':'FINISHED','violation':None}
        control.cgroup=self.group;control.read=Mock(return_value=data);control.check=Mock()
        with self.deny(self.group):result=control.finish()
        self.assertTrue(result['confirmed']);self.assertFalse(result['released'])

    def test_legacy_release_receipt_cannot_turn_denied_scope_into_absence(self):
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope'
        receipt=self.root/'.runtime/resource-events'/(unit+'.json');receipt.parent.mkdir(parents=True)
        group=Path('/sys/fs/cgroup/agentorchestrator.slice')/unit
        receipt.write_text(json.dumps({'unit':unit,'cgroup':str(group)}))
        with patch.object(resources,'PROJECT',self.root),self.deny(group):
            self.assertFalse(resources.scope_released({'unit':unit,'receipt':str(receipt)}))

    def test_prospective_worker_scope_denial_is_not_a_cleanup_receipt(self):
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope';candidate=Path('/sys/fs/cgroup/agentorchestrator.slice')/unit
        original=Path.read_text
        def read(path,*args,**kwargs):
            if str(path)=='/proc/self/cgroup':return '0::/agentorchestrator.slice/owner.scope'
            return original(path,*args,**kwargs)
        with patch.object(scheduler,'scope_released',return_value=False),patch.object(scheduler,'live',return_value=False),patch.object(Path,'read_text',read),self.deny(candidate):
            self.assertFalse(scheduler.worker_scope_clear({'unit':unit,'expected_cgroup':str(candidate)},{}))


if __name__=='__main__':unittest.main()
