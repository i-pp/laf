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


# Added 2026-11-15: Flash artifact/loading/terminal resource boundaries.
# Modified 2026-11-20: terminal preload-wait residency reconciliation.
# Modified 2026-11-20: exercise canonical KFD refusal on the preload cleanup path.
# Modified 2026-11-20: exercise cancellation and watchdog decisions inside Flash retries.

"""Synthetic files, native pipes and host lifecycle; never load a model/device."""
from contextlib import closing
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
from types import SimpleNamespace,ModuleType
import unittest
from unittest.mock import Mock,patch
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'runtime/private_codex/src')]
from scripts.source_test_isolation import require_isolated
require_isolated()  # Refuse raw resource tests before any fixture or project import.
import test_local_resource_admission as local
from inference.laf_service import flash_resources as assets
from inference.laf_service import core_backends,core_server,core_flash_worker
from inference.laf_service.core import Controller,GIB,ResourceWait,ServiceError
from inference.laf_service.lifecycle import LifecycleFault
from inference.laf_service.test_configured_profiles import selection,profiles,job,ConfiguredAPI


def asset_config(root):
    source=root/'source';source.mkdir();(source/'frontend.py').write_text('# synthetic source\n')
    pack=root/'pack';pack.mkdir();(pack/'tokenizer').mkdir();(pack/'tokenizer/template').write_text('synthetic template')
    native=root/'native.gguf';native.write_bytes(b'synthetic fixed model identity, no model data')
    program=root/'program';program.write_text('synthetic nonexecutable program identity')
    python=root/'python';python.write_text('synthetic nonexecutable interpreter identity')
    config={**selection(True),'state_dir':str(root/'state'),'runtime_dir':str(root/'runtime'),
        'lifecycle_runtime_dir':str(root/'host'),'strata_source':str(source),'tokenizer_python':str(python),
        'strata_command':[str(program),'--native',str(native),'--pack',str(pack)],
        'resource_admission_generation':'f'*64,'resource_model_sha256':{p['id']:p['digest'][7:] for p in profiles()}}
    entries=[]
    for path in sorted(p for p in root.rglob('*') if p.is_file()):
        raw=path.read_bytes();entries.append({'path':str(path),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    config['resource_flash_assets']=entries
    normalized={'format':1,'strata_source':config['strata_source'],'tokenizer_python':config['tokenizer_python'],
        'strata_command':config['strata_command'],'files':entries}
    digest=hashlib.sha256(json.dumps(normalized,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    config['resource_model_sha256']['flash']=digest
    return config


class AssetIdentity(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.config=asset_config(self.root)

    def test_full_asset_and_command_pin_is_verified_without_native_loading(self):
        with patch('subprocess.Popen',side_effect=AssertionError('No native loading')):
            self.assertEqual(assets.manifest(self.config,verify=True),self.config['resource_model_sha256']['flash'])
        path=self.root/'core.json';path.write_text(json.dumps(self.config));path.chmod(0o600);path.chmod(0o600)
        self.assertEqual(core_server.load_config(path),self.config)

    def test_changed_same_size_content_and_new_unlisted_file_are_refused(self):
        path=self.root/'native.gguf';raw=path.read_bytes();path.write_bytes(b'X'*len(raw))
        with self.assertRaisesRegex(ServiceError,'content mismatch'):assets.manifest(self.config,verify=True)
        path.write_bytes(raw);(self.root/'pack/new').write_bytes(b'unlisted')
        with self.assertRaisesRegex(ServiceError,'inventory'):assets.manifest(self.config,verify=True)

    def test_absolute_optional_asset_with_equals_is_inventoried_and_hashed(self):
        path=self.root/'part=2.gguf';path.write_bytes(b'synthetic optional asset')
        config=copy.deepcopy(self.config);config['strata_command'].extend(['--ple-gguf',str(path)])
        def repin():
            config['resource_flash_assets'].sort(key=lambda item:item['path'])
            normalized={'format':1,'strata_source':config['strata_source'],'tokenizer_python':config['tokenizer_python'],
                'strata_command':config['strata_command'],'files':config['resource_flash_assets']}
            config['resource_model_sha256']['flash']=hashlib.sha256(json.dumps(normalized,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        repin()
        with self.assertRaisesRegex(ServiceError,'inventory'):assets.manifest(config,verify=True)
        raw=path.read_bytes();config['resource_flash_assets'].append({'path':str(path),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        repin();assets.manifest(config,verify=True)
        path.write_bytes(b'X'*len(raw))
        with self.assertRaisesRegex(ServiceError,'content mismatch'):assets.manifest(config,verify=True)

    def test_pinned_command_options_and_interpreter_cannot_change_silently(self):
        changed=copy.deepcopy(self.config);changed['strata_command'].append('--different-workload')
        with self.assertRaisesRegex(ServiceError,'manifest mismatch'):assets.manifest(changed)
        changed=copy.deepcopy(self.config);changed['tokenizer_python']=str(self.root/'other-python')
        with self.assertRaisesRegex(ServiceError,'manifest mismatch'):assets.manifest(changed)

    def test_symlink_escape_and_hardlinked_asset_are_not_immutable(self):
        path=self.root/'pack/tokenizer/template';raw=path.read_bytes();path.unlink();path.symlink_to(self.root/'native.gguf')
        with self.assertRaisesRegex(ServiceError,'symlink'):assets.manifest(self.config,verify=True)
        path.unlink();path.write_bytes(raw);os.link(path,self.root/'second-link')
        with self.assertRaisesRegex(ServiceError,'immutable'):assets.manifest(self.config,verify=True)

    def test_duplicate_unknown_or_unbounded_manifest_fields_refuse(self):
        for change in ('duplicate','unknown','count','negative','hash'):
            config=copy.deepcopy(self.config)
            if change=='duplicate':config['resource_flash_assets'].append(config['resource_flash_assets'][0])
            elif change=='unknown':config['resource_flash_assets'][0]['extra']=1
            elif change=='count':config['resource_flash_assets']*=assets.MAX_FILES
            elif change=='negative':config['resource_flash_assets'][0]['bytes']=-1
            else:config['resource_flash_assets'][0]['sha256']='unknown'
            with self.assertRaises(ServiceError):assets.manifest(config)

    def test_verification_deadline_and_missing_native_pack_are_not_ignored(self):
        with patch.object(assets.time,'monotonic',side_effect=[100,131]):
            with self.assertRaisesRegex(ServiceError,'bound|deadline'):assets.manifest(self.config,verify=True)
        changed=copy.deepcopy(self.config);changed['strata_command'].remove('--native')
        with self.assertRaisesRegex(ServiceError,'native and pack'):assets.manifest(changed)

    def test_worker_requires_the_parent_pin_before_import_or_native_start(self):
        path=self.root/'core.json';path.write_text(json.dumps(self.config));path.chmod(0o600)
        for argv in ([str(path)],[str(path),'0'*64]):
            with patch.object(sys,'argv',['worker.py',*argv]),patch('subprocess.Popen') as native:
                with self.assertRaises((ServiceError,ValueError)):core_flash_worker.main()
            native.assert_not_called()
        changed=copy.deepcopy(self.config);changed.pop('resource_admission_generation');path.write_text(json.dumps(changed))
        with patch.object(sys,'argv',['worker.py',str(path),self.config['resource_model_sha256']['flash']]),patch('subprocess.Popen') as native:
            with self.assertRaises(ValueError):core_flash_worker.main()
        native.assert_not_called()

    def test_worker_duplicate_oversize_symlink_and_nonprivate_config_refuse_before_native(self):
        path=self.root/'core.json';path.write_text(json.dumps(self.config));path.chmod(0o600)
        digest=self.config['resource_model_sha256']['flash']
        for kind in ('duplicate','oversize','symlink','mode'):
            if path.exists() or path.is_symlink():path.unlink()
            path.write_text(json.dumps(self.config));path.chmod(0o600)
            if kind=='duplicate':path.write_text('{"a":1,"a":2}')
            elif kind=='oversize':path.write_text(' '*32769)
            elif kind=='mode':path.chmod(0o644)
            else:
                target=self.root/'copy.json';target.write_text(json.dumps(self.config));target.chmod(0o600)
                path.unlink();path.symlink_to(target)
            with patch.object(sys,'argv',['worker.py',str(path),digest]),patch('subprocess.Popen') as native:
                with self.assertRaises((OSError,ValueError)):core_flash_worker.main()
            native.assert_not_called()

    def test_real_worker_protocol_echoes_exact_artifact_and_terminal_request(self):
        # Only the Python adapter runs. Vendor tokenizer, template and native
        # process are synthetic objects; no executable or model is invoked.
        path=self.root/'core.json';path.write_text(json.dumps(self.config));path.chmod(0o600)
        tokenizer=SimpleNamespace(encode=lambda text,**kw:([248044] if text=='<|im_end|>' else [248046] if text=='<|endoftext|>' else [1,2]),
            decode=lambda ids,**kw:'synthetic reply')
        tools=ModuleType('tools');tools.__path__=[];token=ModuleType('tools.strata_tokenizer')
        token.Tokenizer=SimpleNamespace(from_gguf=lambda path:tokenizer)
        serve=ModuleType('serve');serve.__path__=[];frontend=ModuleType('serve.frontend')
        frontend.ChatTemplate=lambda path:SimpleNamespace(render=lambda *args,**kw:'rendered')
        process=SimpleNamespace(pid=42,stdin=io.StringIO(),stdout=io.StringIO('READY\nT 248044\nDONE 1 x x x stop\n'),
            poll=lambda:None,terminate=Mock(),wait=Mock(),kill=Mock())
        request=local.ident(700);request_body={'messages':[{'role':'user','content':'hello'}],'max_tokens':8,'resource_request_id':request}
        output=io.StringIO();before=list(sys.path)
        try:
            with patch.dict(sys.modules,{'tools':tools,'tools.strata_tokenizer':token,'serve':serve,'serve.frontend':frontend}),\
                    patch.object(sys,'argv',['worker.py',str(path),self.config['resource_model_sha256']['flash']]),\
                    patch.object(sys,'stdin',io.StringIO(json.dumps(request_body)+'\n')),patch.object(sys,'stdout',output),\
                    patch('subprocess.Popen',return_value=process):core_flash_worker.main()
        finally:sys.path[:]=before
        records=[json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(records[0]['artifact_sha256'],self.config['resource_model_sha256']['flash'])
        self.assertEqual(records[1]['artifact_sha256'],records[0]['artifact_sha256'])
        self.assertEqual(records[2]['resource_request_id'],request);self.assertEqual(records[2]['completion_tokens'],1)


class FlashBoundary(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.config=asset_config(self.root);self.digest=self.config['resource_model_sha256']['flash']
        p=local.policy();p['local_inference']['models']['flash']={
            'engine':'core','artifact_sha256':self.digest,'residency':local.vector(10,100,0,1000),
            'execution':local.vector(100,1000,1000,100),'loading':local.vector(50,500,1000,200)}
        fixture=local.RouterResources();self.fixture=fixture
        with patch.object(local,'policy',return_value=p):fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.config['resource_admission_generation']=fixture.authority.generation
        self.process=SimpleNamespace(pid=42,stdin=io.StringIO(),stdout=io.StringIO(),poll=lambda:None)
        def create(*args,**kwargs):
            kinds=[json.loads(row['demand'])['kind'] for row in fixture.held()]
            self.assertIn('model_residency',kinds);self.assertIn('model_loading',kinds)
            self.assertEqual(args[0][-1],self.digest)
            return self.process
        self.create=Mock(side_effect=create)
        self.backend=core_backends.CoreBackends(self.config,'/synthetic/core.json',fixture.client,
            namespace_probe=lambda:'pid:[222]',process_factory=self.create)
        self.backend.api=ConfiguredAPI();self.backend.wait_idle=Mock()
        def identity(pid):return {'state':'S','pgid':42,'sid':42,'uids':{os.getuid()},'start':1 if pid==42 else 2}
        self.backend._process=identity
        self.output=[{'starting':True,'engine_pid':84,'artifact_sha256':self.digest},
                     {'ready':True,'engine_pid':84,'artifact_sha256':self.digest}]
        self.backend.read_flash=self.read
        self.guard=patch.object(fixture.authority,'_idle_upper',return_value=1000);self.guard.start();self.addCleanup(self.guard.stop)

    def read(self,*args):
        if self.output:return self.output.pop(0)
        body=json.loads(self.process.stdin.getvalue().splitlines()[-1])
        kinds=[json.loads(row['demand'])['kind'] for row in self.fixture.held()]
        self.assertIn('local_inference',kinds)
        return {'content':'synthetic reply','finish_reason':'stop','prompt_tokens':2,'completion_tokens':1,
                'resource_request_id':body['resource_request_id']}

    def start(self):self.backend.start_flash(lambda:None)

    def kinds(self):return [json.loads(row['demand'])['kind'] for row in self.fixture.held()]

    def test_load_is_charged_before_native_and_settled_residency_is_shared(self):
        self.start();self.create.assert_called_once();self.assertNotIn('model_loading',self.kinds())
        self.assertEqual(self.kinds().count('model_residency'),1)
        result=self.backend.generate(job('flash'),lambda:None)
        self.assertEqual(result['content'],'synthetic reply');self.assertNotIn('resource_request_id',result)
        self.assertNotIn('local_inference',self.kinds());self.assertEqual(self.kinds().count('model_residency'),1)

    def test_no_native_load_when_peak_reservation_is_definitively_refused(self):
        original=self.fixture.client.resource_call
        def refuse(operation,**kwargs):
            if operation=='loading_reserve':raise ResourceWait('resource_gpu_milliunits_headroom')
            return original(operation,**kwargs)
        with patch.object(self.fixture.client,'resource_call',side_effect=refuse):
            with self.assertRaises(ResourceWait):self.start()
        self.create.assert_not_called();self.assertIn('model_residency',self.kinds())

    def run_waiting_flash(self, settle, *, expect_abort=False):
        """Run the serial queue and real host ledger against synthetic headroom."""
        authority = self.fixture.authority
        policy = self.fixture.p['local_inference']
        flash = policy['models']['flash']
        residency_ram = local.shared.effective_resources({'resources': flash['residency']}, policy['device'])['ram_bytes']
        self.loading_ram = local.shared.effective_resources({'resources': flash['loading']}, policy['device'])['ram_bytes']
        self.ram_headroom = self.loading_ram - 1
        observe = authority.observe
        def limit_loading(rows, prior):
            sample, state = observe(rows, prior)
            demands = [json.loads(row['demand']) for row in rows]
            promised = sum(local.shared.effective_resources(demand, policy['device'])['ram_bytes'] for demand in demands)
            has_residency = any(demand['kind'] == 'model_residency' for demand in demands)
            free = self.ram_headroom if has_residency else residency_ram
            sample['available']['ram_bytes'] = promised + self.fixture.p['reserve']['ram_bytes'] + free
            return sample, state

        controller = Controller(self.backend, local.pressure_sample, start=False)
        self.controller = controller
        queued = controller.submit({'model': 'flash', 'messages': [{'role': 'user', 'content': 'synthetic'}]})
        original_get = controller.queue.get
        def get(timeout):
            try:
                return original_get(block=False)
            except queue.Empty:
                controller.stop.set()
                raise

        def wait(timeout):
            self.assertIsNone(controller.active)
            self.assertEqual(queued.status, 'queued')
            settle(queued)

        with patch.object(authority, 'observe', limit_loading), patch.object(self.backend, 'abort') as abort, \
                patch.object(controller.queue, 'get', get), patch.object(controller.stop, 'wait', wait):
            controller.run()
        if self.backend.flash_drain is not None:
            self.backend.flash_drain.join(1)
            self.assertFalse(self.backend.flash_drain.is_alive())
        if expect_abort:
            self.assertTrue(abort.called)
        else:
            abort.assert_not_called()
        self.assertTrue(queued.done.is_set())
        self.assertEqual(controller.queue.unfinished_tasks, 0)
        return queued

    def watch_once(self):
        with patch.object(self.controller.stop, 'wait', side_effect=[False, True]):
            self.controller.watch()

    def on_retry_idle_interval(self, action):
        def wait_idle(check):
            # Actual two-sample idle gate. Inject time or cancel between its
            # samples without delaying the synthetic controller.
            with patch.object(core_backends.time, 'sleep', side_effect=action) as sleep:
                try:
                    self.backend.__class__.wait_idle(self.backend, check)
                finally:
                    sleep.assert_called_once_with(.5)
        self.backend.wait_idle.side_effect = wait_idle

    def assert_retry_idle_interruption(self, *, expire, watchdog):
        def retry(queued):
            self.assertEqual(queued.wait_reason, 'resource_ram_bytes_headroom')
            def interrupt(_delay):
                if expire:
                    queued.deadline = self.fixture.now
                else:
                    queued.cancel.set()
                if watchdog:
                    self.watch_once()
            self.on_retry_idle_interval(interrupt)

        with patch.object(self.fixture.authority, '_idle_upper', return_value=0) as proof:
            queued = self.run_waiting_flash(retry)
        self.assertEqual(queued.status, 'failed' if expire else 'cancelled')
        self.assertFalse(queued.abort_requested)
        proof.assert_called_once()
        self.assertEqual(self.kinds(), ['local_control'] * 3)
        self.assertNotIn('flash', self.backend.resource_instances)
        self.create.assert_not_called()

    def test_cancel_during_retried_idle_samples_releases_without_generic_abort(self):
        self.assert_retry_idle_interruption(expire=False, watchdog=False)

    def test_watchdog_cancel_during_retried_idle_samples_leaves_provider_running(self):
        self.assert_retry_idle_interruption(expire=False, watchdog=True)

    def test_watchdog_expiry_during_retried_idle_samples_leaves_provider_running(self):
        self.assert_retry_idle_interruption(expire=True, watchdog=True)

    def test_watchdog_hard_fault_during_retried_idle_samples_still_stops_provider(self):
        def retry(_queued):
            def hard_fault(_delay):
                under_floor = {**local.pressure_sample(), 'mem_available': 7 * GIB}
                with patch.object(self.controller, 'read_resources', return_value=under_floor):
                    self.watch_once()
            self.on_retry_idle_interval(hard_fault)

        with patch.object(self.fixture.authority, '_idle_upper', return_value=0) as proof:
            queued = self.run_waiting_flash(retry, expect_abort=True)
        self.assertEqual(self.controller.fault, 'RAM reserve below 8 GiB')
        self.assertEqual(queued.status, 'cancelled')
        proof.assert_not_called()
        self.assertIn('model_residency', self.kinds())
        self.create.assert_not_called()

    def test_watchdog_cancel_after_retried_load_starts_keeps_stop_barrier(self):
        def retry(queued):
            self.ram_headroom = self.loading_ram
            start = self.create.side_effect
            def launch(*args, **kwargs):
                process = start(*args, **kwargs)
                queued.cancel.set()
                self.watch_once()
                return process
            self.create.side_effect = launch

        with patch.object(self.fixture.client, 'resource_call', wraps=self.fixture.client.resource_call) as rpc:
            queued = self.run_waiting_flash(retry, expect_abort=True)
        self.assertEqual(queued.status, 'cancelled')
        self.assertTrue(queued.abort_requested)
        self.assertNotIn('model_release', [call.args[0] for call in rpc.call_args_list])
        self.assertIn('model_loading', self.kinds())
        self.create.assert_called_once()

    def test_cancelled_cold_wait_releases_only_the_unused_residency(self):
        def cancel(queued):
            self.assertEqual(queued.wait_reason, 'resource_ram_bytes_headroom')
            self.assertIn('model_residency', self.kinds())
            self.assertNotIn('model_loading', self.kinds())
            queued.cancel.set()

        with patch.object(self.fixture.authority, '_idle_upper', return_value=0) as proof:
            queued = self.run_waiting_flash(cancel)
        self.assertEqual(queued.status, 'cancelled')
        proof.assert_called_once()
        self.assertEqual(self.kinds(), ['local_control'] * 3)
        self.assertEqual(self.backend.resource_instances, {})
        self.create.assert_not_called()

    def test_expired_cold_wait_remembers_the_residency_through_later_idle_refusal(self):
        self.backend.wait_idle.side_effect = [None, ResourceWait('resource_vram_bytes_headroom')]
        reasons = []
        def expire(queued):
            reasons.append(queued.wait_reason)
            if len(reasons) == 2:
                queued.deadline = self.fixture.now

        with patch.object(self.fixture.authority, '_idle_upper', return_value=0) as proof:
            queued = self.run_waiting_flash(expire)
        self.assertEqual(reasons, ['resource_ram_bytes_headroom', 'resource_vram_bytes_headroom'])
        self.assertEqual(queued.status, 'failed')
        proof.assert_called_once()
        self.assertNotIn('model_residency', self.kinds())
        self.assertNotIn('flash', self.backend.resource_instances)
        self.create.assert_not_called()

    def test_cold_cancel_retains_residency_with_an_owned_loading_reference(self):
        def add_reference_and_cancel(queued):
            self.ram_headroom = self.loading_ram
            self.fixture.client.resource_call('loading_reserve', model='flash',
                instance=self.backend.resource_instances['flash'], request_id=local.ident(199))
            queued.cancel.set()

        with patch.object(self.fixture.authority, '_idle_upper', return_value=0) as proof:
            queued = self.run_waiting_flash(add_reference_and_cancel)
        proof.assert_not_called()
        self.assertIn('flash', self.backend.resource_instances)
        self.assertIn('model_residency', self.kinds())
        self.assertIn('model_loading', self.kinds())
        self.assertEqual(queued.error, 'unused Flash residency release unconfirmed')
        self.assertTrue(self.fixture.client.uncertain)
        self.create.assert_not_called()

    def test_cold_cancel_retains_residency_when_gpu_ownership_is_unknown(self):
        def cancel(queued):
            queued.cancel.set()

        with patch.object(self.fixture.authority, '_idle_upper', side_effect=
                          local.shared.AdmissionWait('resource_gpu_ownership_unknown')) as proof:
            queued = self.run_waiting_flash(cancel)
        proof.assert_called_once()
        self.assertIn('flash', self.backend.resource_instances)
        self.assertIn('model_residency', self.kinds())
        self.assertEqual(queued.error, 'unused Flash residency release unconfirmed')
        self.assertTrue(self.fixture.client.uncertain)
        self.create.assert_not_called()

    def test_cold_cancel_with_canonical_kfd_fd_never_releases_residency(self):
        self.guard.stop()
        kernel = local.GpuDescriptorEvidence()
        kernel.setUp()
        self.addCleanup(kernel.doCleanups)
        kernel.descriptor = (local.stat.S_IFCHR | 0o600, 1, 2, kernel.kfd_number)
        kernel.file.write_text('pos: 0\n')
        with patch.object(local.host, 'device_sample', return_value={'idle': True}), \
                patch.object(local.host, '_scope_path', return_value=kernel.scope):
            queued = self.run_waiting_flash(lambda job: job.cancel.set())

        self.assertIn('model_residency', self.kinds())
        self.assertIn('flash', self.backend.resource_instances)
        self.assertEqual(queued.error, 'unused Flash residency release unconfirmed')
        self.assertTrue(self.fixture.client.uncertain)
        self.create.assert_not_called()

    def test_cold_cancel_retains_residency_when_host_owner_mismatches(self):
        def change_owner_and_cancel(queued):
            self.fixture.authority.owner = local.ident(190)
            queued.cancel.set()

        with patch.object(self.fixture.authority, '_idle_upper', return_value=0) as proof:
            queued = self.run_waiting_flash(change_owner_and_cancel)
        proof.assert_not_called()
        self.assertIn('model_residency', self.kinds())
        self.assertIn('flash', self.backend.resource_instances)
        self.assertEqual(queued.error, 'unused Flash residency release unconfirmed')
        self.create.assert_not_called()

    def test_cold_cancel_does_not_send_release_after_local_generation_changes(self):
        def change_generation_and_cancel(queued):
            self.backend.generation += 1
            queued.cancel.set()

        with patch.object(self.fixture.client, 'resource_call', wraps=self.fixture.client.resource_call) as rpc:
            queued = self.run_waiting_flash(change_generation_and_cancel)
        self.assertEqual(queued.status, 'cancelled')
        self.assertEqual([call.args[0] for call in rpc.call_args_list], ['model_reserve', 'loading_reserve'])
        self.assertIn('model_residency', self.kinds())
        self.assertIn('flash', self.backend.resource_instances)
        self.create.assert_not_called()

    def test_cold_cancel_lost_release_receipt_leaves_core_uncertain_without_retry(self):
        original = self.fixture.client.exchange
        releases = []
        def lost(request, timeout):
            result = original(request, timeout)
            if request['operation'] == 'model_release':
                releases.append(request['instance'])
                raise TimeoutError('synthetic lost release receipt')
            return result

        with patch.object(self.fixture.authority, '_idle_upper', return_value=0), \
                patch.object(self.fixture.client, 'exchange', lost):
            queued = self.run_waiting_flash(lambda job: job.cancel.set())
        self.assertEqual(releases, [self.backend.resource_instances['flash']])
        self.assertNotIn('model_residency', self.kinds())  # Host committed before the receipt was lost.
        self.assertEqual(queued.error, 'unused Flash residency release unconfirmed')
        self.assertTrue(self.fixture.client.uncertain)
        self.create.assert_not_called()

    def test_cancelled_execution_wait_does_not_release_a_flash_loaded_after_the_wait(self):
        waits = []
        def load_then_cancel(queued):
            waits.append(queued.wait_reason)
            if len(waits) == 1:
                # Admit loading while blocking the larger execution grant.
                self.ram_headroom = self.loading_ram
            else:
                queued.cancel.set()

        with patch.object(self.fixture.client, 'resource_call', wraps=self.fixture.client.resource_call) as rpc:
            queued = self.run_waiting_flash(load_then_cancel)
        self.assertEqual(queued.status, 'cancelled')
        self.assertEqual(waits, ['resource_ram_bytes_headroom'] * 2)
        self.assertEqual([call.args[0] for call in rpc.call_args_list],
                         ['model_reserve', 'loading_reserve', 'loading_reserve', 'loading_finish', 'execution_reserve'])
        self.create.assert_called_once()
        self.assertTrue(self.backend.flash_alive())
        self.assertIn('model_residency', self.kinds())
        self.assertNotIn('model_loading', self.kinds())
        self.assertEqual(self.process.stdin.getvalue(), '')

    def test_lost_loading_ack_retains_both_grants_without_native_or_retry(self):
        original=self.fixture.client.exchange;count=[]
        def lost(request,timeout):
            result=original(request,timeout)
            if request['operation']=='loading_reserve':count.append(1);raise TimeoutError('synthetic lost receipt')
            return result
        with patch.object(self.fixture.client,'exchange',side_effect=lost):
            with self.assertRaises(TimeoutError):self.start()
        self.create.assert_not_called();self.assertEqual(count,[1]);self.assertIn('model_loading',self.kinds())
        self.assertTrue(self.fixture.client.uncertain)

    def test_wrong_ready_artifact_does_not_release_loading_grant(self):
        self.output[1]['artifact_sha256']='0'*64
        with self.assertRaisesRegex(ServiceError,'artifact mismatch'):self.start()
        self.assertIn('model_loading',self.kinds());self.create.assert_called_once()

    def test_unknown_terminal_identity_retains_job_and_is_not_retried(self):
        self.start()
        self.backend.read_flash=lambda *args:{'content':'synthetic','finish_reason':'stop','prompt_tokens':2,
            'completion_tokens':1,'resource_request_id':local.ident(999)}
        with self.assertRaisesRegex(ServiceError,'terminal identity'):self.backend.generate(job('flash'),lambda:None)
        self.assertIn('local_inference',self.kinds())
        self.assertEqual(len(self.process.stdin.getvalue().splitlines()),1)

    def test_peak_loading_and_execution_share_exact_instance_and_single_gpu_budget(self):
        instance=self.fixture.client.resource_call('model_reserve',model='flash',artifact=self.digest,request_id=local.ident(30))['instance']
        loading=self.fixture.client.resource_call('loading_reserve',model='flash',instance=instance,request_id=local.ident(31))['token']
        with self.assertRaises(ResourceWait):
            self.fixture.client.resource_call('execution_reserve',model='flash',instance=instance,request_id=local.ident(32))
        with self.assertRaises(LifecycleFault):
            self.fixture.client.resource_call('execution_finish',instance=instance,request_id=local.ident(31),token=loading)
        self.assertIn('model_loading',self.kinds())

    def test_generic_dead_owner_cleanup_never_releases_loading_gpu_grant(self):
        self.fixture.client.resource_call('model_reserve',model='flash',artifact=self.digest,request_id=local.ident(40))
        instance=self.fixture.held()[-1]['token']
        self.fixture.client.resource_call('loading_reserve',model='flash',instance=instance,request_id=local.ident(41))
        with closing(local.shared.Ledger(self.fixture.p)) as ledger,patch.object(local.shared,'owner_live',return_value=False):
            result=ledger.reconcile_owner(self.fixture.authority.owner,apply=True)
        self.assertEqual(result['local_gpu_grants_retained'],2);self.assertIn('model_loading',self.kinds())

    def test_core_physical_stop_retains_if_gpu_bytes_remain_and_never_drops_controller(self):
        self.start()
        with self.assertRaises(LifecycleFault):self.fixture.client.resource_call('core_models_stopped')
        self.assertIn('model_residency',self.kinds())
        self.fixture.client.stop()
        with patch.object(self.fixture.authority,'_idle_upper',return_value=0):
            self.fixture.client.resource_call('core_models_stopped')
        self.assertEqual(self.kinds(),['local_control','local_control'])

    def counterless_release(self, *, kfd=False):
        self.start();instance=self.backend.resource_instances['flash']
        self.fixture.client.resource_call('execution_reserve',model='flash',instance=instance,request_id=local.ident(799))
        before={row['token'] for row in self.fixture.held()};self.guard.stop()
        kernel=local.GpuDescriptorEvidence();kernel.setUp();self.addCleanup(kernel.doCleanups)
        kernel.file.write_text('pos: 0\nflags: 0100002\nmnt_id: 41\nino: 928\n')
        if kfd:kernel.descriptor=(local.stat.S_IFCHR|0o600,1,2,kernel.kfd_number)
        with patch.object(local.host,'device_sample',return_value={'idle':True,'used':8192}),\
                patch.object(local.host,'_scope_path',return_value=kernel.scope):
            with self.assertRaises(LifecycleFault):self.fixture.client.resource_call('core_models_stopped')
        self.assertEqual(before,{row['token'] for row in self.fixture.held()})
        self.assertIn('model_residency',self.kinds());self.assertIn('local_inference',self.kinds())
        self.create.assert_called_once()

    def test_counterless_owned_drm_keeps_real_core_model_job_and_controller_grants(self):
        self.counterless_release()

    def test_unattributed_kfd_keeps_real_core_model_job_and_controller_grants(self):
        self.counterless_release(kfd=True)

    def test_abort_uses_owned_group_then_fixed_provider_stop_then_core_gpu_proof(self):
        self.start();sequence=[]
        self.backend.stop_group=lambda process:sequence.append('owned_core_group')
        old_stop=self.fixture.client.stop;old_resource=self.fixture.client.resource_call
        def stop():sequence.append('fixed_provider_stop');return old_stop()
        def resource(operation,**kwargs):sequence.append(operation);return old_resource(operation,**kwargs)
        with patch.object(self.fixture.client,'stop',side_effect=stop),patch.object(self.fixture.client,'resource_call',side_effect=resource),\
                patch.object(self.fixture.authority,'_idle_upper',return_value=0):self.backend.abort()
        self.assertEqual(sequence,['owned_core_group','fixed_provider_stop','core_models_stopped'])
        self.assertEqual(self.backend.resource_instances,{});self.assertEqual(self.kinds(),['local_control','local_control'])


if __name__=='__main__':unittest.main()
