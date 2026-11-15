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


# Added 2026-11-15: non-admitting NPU evidence and observation refusal boundaries.

"""Synthetic sysfs/proc files only. No device open, native SDK or model."""
from contextlib import ExitStack
import copy
import io
import json
import os
from pathlib import Path
import sys
import stat
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from inference.laf_npu_service import resource_evidence as evidence


def policy():
    return {'schema':1,'device':{'pci_bdf':'0000:00:01.1','vendor':'0x1022','device':'0x9999',
        'driver':'amdxdna','firmware':'1.2.3.4'},'valid_until':10**12,'evidence':'synthetic-pinned-npu',
        'max_age_seconds':2,'controller':{'cpu_millicores':500,'ram_bytes':64*1024**2,'pids':16}}


def raw(engine=100,total=4096,shared=1024,client=7):
    return ('drm-driver: amdxdna_accel_driver\ndrm-pdev: 0000:00:01.1\ndrm-client-id: '+str(client)
        +'\ndrm-engine-npu: '+str(engine)+' ns\ndrm-total-memory: '+str(total)+'\ndrm-shared-memory: '+str(shared)+'\n')


class Evidence(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.proc=self.root/'proc';self.pci=self.root/'pci';self.cgroup=self.root/'cgroup'
        for name,value in (('PROC',self.proc),('PCI',self.pci),('CGROUP',self.cgroup)):
            self.stack.enter_context(patch.object(evidence,name,value))
        self.scope=self.cgroup/'user.slice/laf-npu.service';self.scope.mkdir(parents=True)
        (self.scope/'cgroup.procs').write_text('42\n')
        target=self.proc/'42';(target/'fdinfo').mkdir(parents=True)
        (target/'stat').write_text('42 (synthetic) S '+'0 '*18+'7\n')
        (target/'status').write_text('Uid: '+' '.join([str(os.getuid())]*4)+'\n')
        (target/'cgroup').write_text('0::/'+str(self.scope.relative_to(self.cgroup))+'\n')
        (target/'fdinfo/1').write_text(raw())
        namespace=self.proc/'self/ns';namespace.mkdir(parents=True);(namespace/'pid').symlink_to('pid:[synthetic]')
        self.p=policy();device=self.pci/self.p['device']['pci_bdf'];device.mkdir(parents=True)
        for name,value in (('vendor','0x1022'),('device','0x9999'),('fw_version','1.2.3.4')):(device/name).write_text(value)
        driver=self.root/'drivers/amdxdna';driver.mkdir(parents=True);(device/'driver').symlink_to(driver)
        accel=self.root/'accel';node=accel/'accel0';node.mkdir(parents=True)
        (node/'device').symlink_to(device);(node/'dev').write_text('261:0')
        self.stack.enter_context(patch.object(evidence,'ACCEL',accel))
        self.fd=(stat.S_IFCHR|0o600,1,2,os.makedev(261,0))
        self.stack.enter_context(patch.object(evidence,'_fd',side_effect=lambda path:self.fd))
        self.now=100.0;self.stack.enter_context(patch.object(evidence.time,'monotonic',side_effect=lambda:self.now))

    def tick(self,seconds):self.now+=seconds

    def capture(self):return evidence.capture(self.p,str(self.scope),sleep=self.tick)

    def test_pinned_owned_counters_are_deduplicated_and_never_admit_a_device(self):
        (self.proc/'42/fdinfo/2').write_text(raw())
        result=self.capture()
        self.assertEqual(result['owned_client_count'],1);self.assertEqual(result['owned_allocated_bytes_upper'],4096)
        self.assertEqual(result['owned_engine_ns_delta_upper'],0)
        self.assertFalse(result['admission_eligible']);self.assertFalse(result['global_idle_verified'])
        self.assertEqual(result['wait_reason'],'resource_npu_global_ownership_unknown')
        self.assertNotIn('42',json.dumps(result));self.assertNotIn(str(self.scope),json.dumps(result))

    def test_zero_owned_clients_still_does_not_mean_global_idle(self):
        (self.scope/'cgroup.procs').write_text('')
        result=self.capture();self.assertEqual(result['owned_client_count'],0)
        self.assertFalse(result['global_idle_verified']);self.assertFalse(result['admission_eligible'])

    def test_engine_progress_reports_activity_without_granting_capacity(self):
        def tick(seconds):
            self.tick(seconds);(self.proc/'42/fdinfo/1').write_text(raw(engine=160))
        result=evidence.capture(self.p,str(self.scope),sleep=tick)
        self.assertEqual(result['owned_engine_ns_delta_upper'],60);self.assertFalse(result['admission_eligible'])

    def test_duplicate_missing_driver_pci_and_counter_identity_fail_closed(self):
        for text in (raw()+'drm-client-id: 8\n',raw().replace('drm-pdev: 0000:00:01.1\n',''),
                     raw().replace('drm-driver: amdxdna_accel_driver\n',''),raw().replace('100 ns','100 us'),
                     raw(total=1,shared=2),raw().replace('drm-client-id: 7','drm-client-id: unknown')):
            with self.assertRaises(evidence.AdmissionWait):evidence.client(text,self.p['device'])
        self.assertIsNone(evidence.client('pos: 0\n',self.p['device']))
        self.assertIsNone(evidence.client(raw().replace('0000:00:01.1','0000:00:02.1'),self.p['device']))

    def test_device_driver_firmware_expiry_or_missing_metadata_are_unknown(self):
        device=self.pci/self.p['device']['pci_bdf']
        for name,bad in (('vendor','0xffff'),('device','0xffff'),('fw_version','9.9.9.9')):
            path=device/name;old=path.read_text();path.write_text(bad)
            with self.assertRaisesRegex(evidence.AdmissionWait,'device_unknown'):self.capture()
            path.write_text(old)
        self.p['valid_until']=1
        with self.assertRaisesRegex(evidence.AdmissionWait,'device_unknown'):self.capture()

    def test_permission_or_pid_uid_cgroup_change_is_unknown(self):
        path=self.proc/'42/status';path.write_text('Uid: 999999 999999 999999 999999\n')
        with self.assertRaisesRegex(evidence.AdmissionWait,'usage_unknown'):self.capture()
        path.write_text('Uid: '+' '.join([str(os.getuid())]*4)+'\n')
        (self.proc/'42/cgroup').write_text('0::/other\n')
        with self.assertRaisesRegex(evidence.AdmissionWait,'usage_unknown'):self.capture()
        with patch.object(evidence,'_scan',side_effect=PermissionError('synthetic')):
            with self.assertRaisesRegex(evidence.AdmissionWait,'observation_unknown'):self.capture()

    def test_counter_reset_client_churn_and_namespace_change_refuse(self):
        for kind in ('reset','client','namespace'):
            (self.proc/'42/fdinfo/1').write_text(raw());(self.proc/'self/ns/pid').unlink();(self.proc/'self/ns/pid').symlink_to('pid:[synthetic]')
            def tick(seconds):
                self.tick(seconds)
                if kind=='namespace':
                    (self.proc/'self/ns/pid').unlink();(self.proc/'self/ns/pid').symlink_to('pid:[changed]')
                else:(self.proc/'42/fdinfo/1').write_text(raw(engine=50) if kind=='reset' else raw(client=8))
            with self.assertRaisesRegex(evidence.AdmissionWait,'observation_stale'):
                evidence.capture(self.p,str(self.scope),sleep=tick)

    def test_slow_final_device_read_or_clock_rollback_refuse_after_all_reads(self):
        original=evidence._device
        for seconds in (10,-1):
            self.now=100;calls=[0]
            def read(value):
                calls[0]+=1
                if calls[0]==2:self.now+=seconds
                return original(value)
            with patch.object(evidence,'_device',side_effect=read),self.assertRaisesRegex(evidence.AdmissionWait,'observation_stale'):self.capture()

    def test_wall_evidence_expiry_during_short_final_firmware_read_refuses(self):
        self.p['valid_until']=1000.07;wall=[1000.0];calls=[0];original=evidence._read
        def read(path,maximum=65536):
            result=original(path,maximum)
            if Path(path).name=='fw_version':
                calls[0]+=1
                if calls[0]==2:wall[0]+=.02;self.now+=.02
            return result
        def tick(seconds):self.tick(seconds);wall[0]+=seconds
        with patch.object(evidence.time,'time',side_effect=lambda:wall[0]),patch.object(evidence,'_read',side_effect=read):
            with self.assertRaisesRegex(evidence.AdmissionWait,'device_unknown'):
                evidence.capture(self.p,str(self.scope),sleep=tick)
        self.assertLess(self.now-100,self.p['max_age_seconds'])
        self.assertGreater(wall[0],self.p['valid_until'])

    def test_invalid_wall_time_is_unknown_instead_of_valid_pin_evidence(self):
        for value in (0,-1,True,float('nan'),float('inf')):
            with patch.object(evidence.time,'time',return_value=value),self.assertRaisesRegex(evidence.AdmissionWait,'device_unknown'):
                self.capture()

    def test_scope_symlink_descendants_and_work_budget_refuse(self):
        alias=self.cgroup/'alias';alias.symlink_to(self.scope)
        with self.assertRaisesRegex(evidence.AdmissionWait,'scope_unknown'):evidence.capture(self.p,str(alias),sleep=self.tick)
        child=self.scope/'child';child.mkdir()
        with self.assertRaisesRegex(evidence.AdmissionWait,'scope_unknown'):self.capture()
        child.rmdir()
        with self.assertRaisesRegex(evidence.AdmissionWait,'observation_stale'):
            evidence._scan(self.scope,self.p['device'],self.now-1)

    def test_unbounded_reader_cli_refuses_before_device_or_capture(self):
        path=self.root/'policy.json';path.write_text(json.dumps(self.p));path.chmod(0o600)
        with patch.object(evidence,'reader_scope',side_effect=evidence.AdmissionWait('resource_controller_required')),\
                patch.object(evidence,'capture') as capture,patch('builtins.print'):
            self.assertEqual(evidence.main(['--policy',str(path),'--scope',str(self.scope)]),1)
        capture.assert_not_called()

    def test_reader_kernel_caps_are_verified_without_native_or_provider(self):
        unit='agentorchestrator-'+str(uuid.uuid4())+'.scope';group=self.cgroup/'agentorchestrator.slice'/unit;group.mkdir(parents=True)
        for name,value in (('cpu.max','50000 100000'),('memory.max',str(64*1024**2)),('pids.max','16'),('memory.swap.max','0')):(group/name).write_text(value)
        (self.proc/'self/cgroup').write_text('0::/'+str(group.relative_to(self.cgroup)))
        with patch.object(evidence,'_scope_path'):
            evidence.reader_scope(self.p['controller'])
            (group/'cpu.max').write_text('max 100000')
            with self.assertRaisesRegex(evidence.AdmissionWait,'controller_required'):evidence.reader_scope(self.p['controller'])

    def test_policy_unknown_fields_nonfinite_or_zero_bounds_do_not_become_safe(self):
        for key,value in (('max_age_seconds',float('nan')),('max_age_seconds',0),('valid_until',0),('schema',True)):
            changed=copy.deepcopy(self.p);changed[key]=value
            with self.assertRaises(ValueError):evidence.policy(changed)
        changed=copy.deepcopy(self.p);changed['controller']['ram_bytes']=0
        with self.assertRaises(ValueError):evidence.policy(changed)

    def test_private_policy_rejects_duplicates_mode_and_symlinks(self):
        path=self.root/'policy.json';path.write_text('{"schema":1,"schema":1}');path.chmod(0o600)
        with self.assertRaises(ValueError):evidence._load(path)
        path.write_text(json.dumps(self.p));path.chmod(0o644)
        with self.assertRaises(ValueError):evidence._load(path)
        alias=self.root/'alias.json';alias.symlink_to(path)
        with self.assertRaises(ValueError):evidence._load(alias)

    def test_open_accelerator_without_fdinfo_counters_is_unknown_not_zero_clients(self):
        (self.proc/'42/fdinfo/1').write_text('pos: 0\nflags: 0100002\nmnt_id: 41\nino: 928\n')
        with self.assertRaisesRegex(evidence.AdmissionWait,'npu_counters_unknown'):self.capture()

    def test_ordinary_descriptor_without_counters_is_ignored_without_admission(self):
        self.fd=(stat.S_IFREG|0o600,1,2,0)
        (self.proc/'42/fdinfo/1').write_text('pos: 0\n')
        result=self.capture();self.assertEqual(result['owned_client_count'],0)
        self.assertFalse(result['admission_eligible'])

    def test_counter_identity_must_match_actual_accelerator_descriptor(self):
        self.fd=(stat.S_IFREG|0o600,1,2,0)
        with self.assertRaisesRegex(evidence.AdmissionWait,'usage_unknown'):self.capture()
        self.fd=(stat.S_IFCHR|0o600,1,2,os.makedev(261,1))
        with self.assertRaisesRegex(evidence.AdmissionWait,'usage_unknown'):self.capture()

    def test_descriptor_replacement_or_stat_denial_cannot_hide_unknown_client(self):
        for change in (lambda p:(_ for _ in ()).throw(PermissionError('synthetic')),
                       Mock(side_effect=[self.fd,(stat.S_IFREG|0o600,1,3,0)])):
            with patch.object(evidence,'_fd',side_effect=change),self.assertRaises(evidence.AdmissionWait):self.capture()

    def test_accelerator_node_disappearance_or_rebinding_is_unknown(self):
        node=evidence.ACCEL/'accel0/dev';node.unlink()
        with self.assertRaisesRegex(evidence.AdmissionWait,'observation_unknown'):self.capture()
        node.write_text('261:0')
        def tick(seconds):self.tick(seconds);node.write_text('261:1')
        with self.assertRaisesRegex(evidence.AdmissionWait,'device_unknown'):
            evidence.capture(self.p,str(self.scope),sleep=tick)

    def test_capture_never_opens_device_sdk_socket_model_or_ledger(self):
        with patch('subprocess.Popen',side_effect=AssertionError('No native executable')),\
                patch('socket.socket',side_effect=AssertionError('No provider socket')),\
                patch('inference.laf_service.resource_admission_core.Ledger',side_effect=AssertionError('No admission mutation')):
            result=self.capture()
        self.assertFalse(result['admission_eligible'])


if __name__=='__main__':unittest.main()
