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

"""Offline model-lifecycle contracts. Never contacts an engine or downloads weights."""
# Modified 2026-11-11: advertise the Router catalog separately from model storage status.

import copy
from contextlib import contextmanager
import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import model_management as models

DIGEST = 'sha256:' + 'a' * 64
ENTRY = {
    'id': 'coder-next', 'engine': 'ollama', 'reference': 'owner/coder:v1',
    'source': 'https://example.org/models/coder', 'license': 'Apache-2.0',
    'license_url': 'https://example.org/models/coder/LICENSE', 'version': 'v1',
    'required_bytes': 1024, 'digest': DIGEST, 'user_setup_required': False,
}


class FakeEngine:
    identity = 'test-ollama'
    def __init__(self):
        self.tags = []
        self.loaded = []
        self.calls = []
        self.fail = None
        self.wrong_digest = False
    def installed(self):
        self.calls.append('list')
        return copy.deepcopy(self.tags)
    def running(self):
        self.calls.append('running')
        return copy.deepcopy(self.loaded)
    def install(self, reference):
        self.calls.append(('install', reference))
        if self.fail: raise self.fail
        self.tags = [{'reference': reference, 'digest': ('sha256:' + 'b'*64) if self.wrong_digest else DIGEST, 'size_bytes': 512}]
    def remove(self, reference):
        self.calls.append(('remove', reference))
        if self.fail: raise self.fail
        self.tags = []


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        os.chmod(self.root, 0o700)
        self.store = self.root / 'store'
        self.store.mkdir(mode=0o700)
        self.lock = self.root / 'generation.lock'
        self.lock.touch(mode=0o600)
        self.engine = FakeEngine()
        self.manager = models.ModelManager(self.root / 'state', [ENTRY], self.engine, self.lock, self.store)
    def install(self):
        return self.manager.install('coder-next')
    def test_install_persists_verified_identity_without_loading(self):
        out = self.install()
        self.assertEqual(out['status'], 'installed')
        self.assertEqual(out['digest'], DIGEST)
        self.assertNotIn('load', self.engine.calls)
        reopened = models.ModelManager(self.root/'state', [ENTRY], self.engine, self.lock, self.store)
        self.assertEqual(reopened.list()[0]['status'], 'installed')
        self.assertEqual(reopened.list()[0]['source'], ENTRY['source'])
        self.assertEqual(reopened.list()[0]['license'], 'Apache-2.0')
        self.assertIsNone(reopened.status()['pending'])
    def test_install_is_idempotent_without_second_pull(self):
        self.install(); self.install()
        self.assertEqual(self.engine.calls.count(('install', ENTRY['reference'])), 1)
    def test_preexisting_matching_unmanaged_model_is_not_adopted_or_deleted(self):
        self.engine.tags = [{'reference': ENTRY['reference'], 'digest': DIGEST, 'size_bytes': 512}]
        with self.assertRaises(models.ModelError): self.install()
        self.assertNotIn(('install', ENTRY['reference']), self.engine.calls)
        with self.assertRaises(models.ModelError): self.manager.remove('coder-next')
        self.assertNotIn(('remove', ENTRY['reference']), self.engine.calls)
    def test_preexisting_conflicting_model_is_not_overwritten(self):
        self.engine.tags = [{'reference': ENTRY['reference'], 'digest': 'sha256:'+'b'*64, 'size_bytes': 512}]
        with self.assertRaises(models.ModelError): self.install()
        self.assertNotIn(('install', ENTRY['reference']), self.engine.calls)
    def test_unknown_and_incomplete_model_never_touch_engine(self):
        with self.assertRaises(models.ModelError): self.manager.install('not-in-catalog')
        for field, value in [('digest', None), ('version', None), ('required_bytes', None), ('license', None), ('source', None)]:
            entry = dict(ENTRY, **{field:value})
            manager = models.ModelManager(self.root/'state', [entry], self.engine, self.lock, self.store)
            with self.subTest(field=field), self.assertRaises(models.ModelError): manager.install('coder-next')
        self.assertEqual(self.engine.calls, [])
    def test_user_legal_or_auth_setup_cannot_be_bypassed(self):
        manager = models.ModelManager(self.root/'state', [dict(ENTRY,user_setup_required=True)], self.engine,self.lock,self.store)
        with self.assertRaisesRegex(models.ModelError, 'user'): manager.install('coder-next')
        self.assertEqual(self.engine.calls, [])
    def test_insufficient_capacity_prevents_pull(self):
        with patch.object(models.shutil, 'disk_usage', return_value=type('D',(),{'free':1})()):
            with self.assertRaises(models.ModelError): self.install()
        self.assertNotIn(('install', ENTRY['reference']), self.engine.calls)
    def test_loaded_engine_blocks_all_mutations(self):
        self.engine.loaded = [{'reference':'unrelated:v2','digest':DIGEST,'size_bytes':512}]
        with self.assertRaises(models.ModelError): self.install()
        self.assertNotIn(('install', ENTRY['reference']), self.engine.calls)
    def test_active_router_lock_is_never_stolen_or_created(self):
        with self.lock.open('r') as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(models.ModelError): self.install()
        self.assertEqual(self.engine.calls, [])
        self.lock.unlink()
        with self.assertRaises((models.ModelError,OSError)): self.install()
        self.assertFalse(self.lock.exists())
    def test_install_timeout_is_durable_uncertain_not_automatically_retried(self):
        self.engine.fail = TimeoutError('private raw provider error')
        with self.assertRaisesRegex(models.ModelError,'uncertain'): self.install()
        self.assertEqual(self.manager.status()['pending']['action'], 'install')
        self.engine.fail = None
        with self.assertRaises(models.ModelError): self.install()
        self.assertEqual(self.engine.calls.count(('install', ENTRY['reference'])),1)
    def test_wrong_download_digest_is_uncertain_and_not_deleted(self):
        self.engine.wrong_digest = True
        with self.assertRaises(models.ModelError): self.install()
        self.assertIsNotNone(self.manager.status()['pending'])
        self.assertFalse(any(isinstance(c,tuple) and c[0]=='remove' for c in self.engine.calls))
    def test_pending_is_written_before_engine_mutation(self):
        original = self.engine.install
        def install(ref):
            self.assertEqual(self.manager.status()['pending']['action'],'install')
            original(ref)
        self.engine.install = install
        self.install()
    def test_remove_only_managed_unloaded_matching_model(self):
        with self.assertRaises(models.ModelError): self.manager.remove('coder-next')
        self.assertEqual(self.engine.calls, [])
        self.install()
        result = self.manager.remove('coder-next')
        self.assertEqual(result['status'],'removed')
        self.assertEqual(self.manager.list(), [])
        self.assertEqual(self.engine.calls[-2:], [('remove',ENTRY['reference']),'list'])
    def test_remove_rechecks_identity_and_loaded_state(self):
        self.install()
        self.engine.tags[0]['digest'] = 'sha256:'+'b'*64
        with self.assertRaises(models.ModelError): self.manager.remove('coder-next')
        self.assertNotIn(('remove',ENTRY['reference']),self.engine.calls)
        self.engine.tags[0]['digest'] = DIGEST
        self.engine.loaded = copy.deepcopy(self.engine.tags)
        with self.assertRaises(models.ModelError): self.manager.remove('coder-next')
        self.assertNotIn(('remove',ENTRY['reference']),self.engine.calls)
    def test_remove_timeout_is_not_retried(self):
        self.install(); self.engine.fail = TimeoutError()
        with self.assertRaises(models.ModelError): self.manager.remove('coder-next')
        with self.assertRaises(models.ModelError): self.manager.remove('coder-next')
        self.assertEqual(self.engine.calls.count(('remove',ENTRY['reference'])),1)
    def test_list_and_local_status_do_not_contact_engine(self):
        self.assertEqual(self.manager.list(), [])
        self.assertEqual(self.manager.status()['installed_count'],0)
        self.assertEqual(self.engine.calls, [])
    def test_metadata_is_private_and_invalid_state_fails_closed(self):
        self.install()
        state = self.root/'state'/'models.json'
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(state.parent.stat().st_mode & 0o777, 0o700)
        state.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(models.ModelError): self.manager.list()
    def test_symlink_state_lock_or_store_refused(self):
        for name in ('state','generation.lock','store'):
            with self.subTest(name=name):
                if name == 'generation.lock': self.lock.unlink()
                elif name == 'store': self.store.rmdir()
                (self.root/name).symlink_to(self.root/'outside')
                with self.assertRaises((models.ModelError,OSError)): self.install()
                (self.root/name).unlink()
                if name == 'generation.lock': self.lock.touch(mode=0o600)
                elif name == 'store': self.store.mkdir(mode=0o700)
    def test_registry_bound_blocks_new_id_but_allows_existing_reinstall(self):
        self.install()
        state_path=self.root/'state'/'models.json'
        state=json.loads(state_path.read_text())
        for number in range(127):
            entry=dict(ENTRY,id='model-'+str(number),reference='model-'+str(number)+':v1',status='installed',size_bytes=512)
            state['models'][entry['id']]=entry
        state_path.write_text(json.dumps(state))
        self.assertEqual(self.install()['status'],'installed')
        other=dict(ENTRY,id='extra',reference='extra:v1')
        manager=models.ModelManager(self.root/'state',[other],self.engine,self.lock,self.store)
        before=list(self.engine.calls)
        with self.assertRaises(models.ModelError): manager.install('extra')
        self.assertEqual(self.engine.calls,before)
        self.assertEqual(len(manager.list()),128)
    def test_registry_cannot_be_reused_with_a_different_engine(self):
        self.install()
        self.engine.identity='different-ollama'
        before=list(self.engine.calls)
        with self.assertRaises(models.ModelError): self.manager.remove('coder-next')
        self.assertEqual(self.engine.calls,before)
    def test_new_state_directory_parent_is_fsynced_before_engine_mutation(self):
        seen=[]
        original=models.os.fsync
        def sync(fd):
            seen.append(os.readlink('/proc/self/fd/'+str(fd)))
            original(fd)
        original_install=self.engine.install
        def install(reference):
            self.assertIn(str(self.root),seen)
            original_install(reference)
        self.engine.install=install
        with patch.object(models.os,'fsync',side_effect=sync): self.install()
    def test_parallel_managers_serialize_state(self):
        with models.private_lock(self.root/'state', 'models.lock', create=True):
            with self.assertRaises(models.ModelError): self.install()
        self.assertEqual(self.engine.calls, [])


class ValidationTests(unittest.TestCase):
    def test_builtin_catalog_has_all_compatibility_profiles_and_no_fabricated_hashes(self):
        catalog = models.read_catalog()
        self.assertEqual({entry['id'] for entry in catalog}, {'flash','coder-next','gpt-oss-120b'})
        self.assertTrue(all(entry['digest'] is None for entry in catalog))
    def test_unhashable_engine_is_rejected_as_model_error(self):
        with self.assertRaises(models.ModelError): models.validate_catalog([dict(ENTRY,engine=[])])
    def test_catalog_rejects_duplicate_ids_unknown_keys_and_unsafe_refs(self):
        for catalog in ([ENTRY, ENTRY], [dict(ENTRY,script='curl evil')], [dict(ENTRY,reference='../escape')], [dict(ENTRY,source='file:///etc/passwd')]):
            with self.subTest(catalog=catalog), self.assertRaises(models.ModelError): models.validate_catalog(catalog)
    def test_official_shaped_ollama_bare_wire_digest_is_normalized(self):
        # Ollama docs api/tags and api/ps use a bare hex manifest digest.
        fixture={'models':[{'name':'owner/coder:v1','model':'owner/coder:v1',
                  'modified_at':'2026-11-08T00:00:00Z','size':512,'digest':'a'*64,
                  'details':{'format':'gguf'},'size_vram':256}]}
        engine=models.OllamaAdapter(lambda *args:fixture)
        self.assertEqual(engine.installed()[0]['digest'],DIGEST)
        self.assertEqual(engine.running()[0]['digest'],DIGEST)
    def test_engine_only_fixed_management_endpoints_and_never_generate(self):
        calls=[]
        def request(method,path,body=None):
            calls.append((method,path,body))
            if path in ('/api/tags','/api/ps'): return {'models':[{'name':'owner/coder:v1','digest':DIGEST,'size':512}]}
            return {'status':'success'} if method == 'POST' else {}
        engine=models.OllamaAdapter(request)
        self.assertEqual(engine.installed()[0]['digest'],DIGEST)
        self.assertEqual(engine.running()[0]['reference'],ENTRY['reference'])
        engine.install(ENTRY['reference']); engine.remove(ENTRY['reference'])
        self.assertEqual(calls,[('GET','/api/tags',None),('GET','/api/ps',None),('POST','/api/pull',{'model':ENTRY['reference'],'stream':False}),('DELETE','/api/delete',{'model':ENTRY['reference']})])
    def test_invalid_engine_inventory_and_pull_error_fail_closed(self):
        for response in ({}, {'models':[{'name':'x','digest':'bad','size':1}]}, {'models':[]}):
            if response.get('models') == []: continue
            with self.subTest(response=response), self.assertRaises(models.ModelError): models.OllamaAdapter(lambda *args:response).installed()
        with self.assertRaises(models.ModelError): models.OllamaAdapter(lambda *args:{'error':'secret'}).install(ENTRY['reference'])
    def test_loopback_transport_rejects_remote_credentials_and_redirect_destinations(self):
        for url in ('http://example.org:11434','http://127.0.0.1.evil:11434','http://user:pass@127.0.0.1:11434','https://127.0.0.1:11434','http://127.0.0.1:11434/evil'):
            with self.subTest(url=url), self.assertRaises(models.ModelError): models.OllamaHTTP(url)
    def test_ollama_catalog_requires_explicit_tag_before_engine_access(self):
        with self.assertRaises(models.ModelError): models.validate_catalog([dict(ENTRY,reference='owner/coder')])
    def test_catalog_rejects_aliases_of_the_same_engine_reference(self):
        with self.assertRaises(models.ModelError): models.validate_catalog([ENTRY,dict(ENTRY,id='alias')])
    def test_falsey_invalid_configuration_is_rejected_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp,0o700)
            config=Path(tmp)/'config.json'
            for value in ([],False,0,''):
                config.write_text(json.dumps(value));config.chmod(0o600)
                with self.subTest(value=value),patch('sys.stderr',new_callable=io.StringIO):
                    self.assertEqual(models.main(['--config',str(config),'status']),1)
    def test_status_keeps_router_state_and_reports_independent_install_state(self):
        from . import cli
        def request(path, method, route, body=None):
            if route == '/v1/models':
                return {'models':['flash','coder-next'], 'context_limit':8192,
                        'max_output_tokens':1024, 'max_large_models_loaded':1}
            return {'status':'ready','gpu':{'flash_resident':True,'ollama_selected':'coder-next'}}
        with patch.object(cli,'request',side_effect=request), \
             patch.object(models.ModelManager,'status',return_value={'installed_count':2,'pending':None,'loaded_state':'not_queried'}), \
             patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(cli.main(['status']),0)
        value=json.loads(out.getvalue())
        self.assertEqual(value['selected_model'],'coder-next')
        self.assertEqual(value['model_storage']['installed_count'],2)
    def test_catalog_cli_never_initializes_engine(self):
        with patch.object(models,'OllamaHTTP',side_effect=AssertionError('network')),patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(models.main(['catalog']),0)
        self.assertEqual(len(json.loads(out.getvalue())['catalog']),3)
    def test_existing_cli_model_subcommand_delegates_without_router_request(self):
        from . import cli
        with patch.object(models,'main',return_value=0) as run,patch.object(cli,'request',side_effect=AssertionError('network')):
            self.assertEqual(cli.main(['model','catalog']),0)
        run.assert_called_once_with(['catalog'])


class InspectTests(unittest.TestCase):
    """Exercise one complete CLI GET-only path, not a live engine acceptance."""
    setUp = ModelTests.setUp
    install = ModelTests.install
    def test_inspect_uses_only_inventory_without_creating_or_locking_state(self):
        self.engine.tags=[{'reference':ENTRY['reference'],'digest':DIGEST,'size_bytes':512}]
        self.engine.loaded=copy.deepcopy(self.engine.tags)
        out=self.manager.inspect('coder-next')
        self.assertTrue(out['installed']);self.assertTrue(out['loaded']);self.assertTrue(out['digest_matches'])
        self.assertFalse(out['managed']);self.assertFalse(out['mutation_performed'])
        self.assertEqual(out['compatibility'],'not_evaluated')
        self.assertEqual(self.engine.calls,['list','running'])
        self.assertFalse((self.root/'state').exists())
    def test_inspect_reports_missing_or_differing_model_without_mutation(self):
        out=self.manager.inspect('coder-next')
        self.assertFalse(out['installed']);self.assertIsNone(out['digest_matches'])
        self.engine.tags=[{'reference':ENTRY['reference'],'digest':'sha256:'+'b'*64,'size_bytes':512}]
        out=self.manager.inspect('coder-next')
        self.assertTrue(out['installed']);self.assertFalse(out['digest_matches'])
        self.assertFalse(any(isinstance(c,tuple) for c in self.engine.calls))
    def test_inspect_unknown_catalog_id_does_not_contact_engine(self):
        with self.assertRaises(models.ModelError): self.manager.inspect('unknown')
        self.assertEqual(self.engine.calls,[])
    def test_inspect_does_not_grant_management_to_a_different_engine(self):
        self.install();self.engine.identity='other-engine'
        out=self.manager.inspect('coder-next')
        self.assertFalse(out['managed']);self.assertFalse(out['management_scope_matches'])
    def test_inspect_does_not_claim_current_management_for_stale_registry(self):
        self.install()
        self.engine.tags[0]['digest']='sha256:'+'b'*64
        out=self.manager.inspect('coder-next')
        self.assertTrue(out['registry_record_matches']);self.assertFalse(out['managed'])
        self.assertFalse(out['digest_matches'])
        self.engine.tags=[]
        out=self.manager.inspect('coder-next')
        self.assertTrue(out['registry_record_matches']);self.assertFalse(out['managed'])
        self.assertFalse(out['installed'])
    def test_inspect_cli_end_to_end_is_get_only_and_no_lock_changes(self):
        config=self.root/'config.json';config.write_text(json.dumps({
            'state_dir':str(self.root/'state'),'model_store':str(self.store),
            'generation_lock':str(self.lock),'engine_url':'http://127.0.0.1:11434'}));config.chmod(0o600)
        catalog=self.root/'catalog.json';catalog.write_text(json.dumps([ENTRY]));catalog.chmod(0o600)
        wire_calls=[]
        def request(_self,method,path,body=None):
            wire_calls.append((method,path,body))
            return {'models':[{'name':ENTRY['reference'],'digest':'a'*64,'size':512}]} if path=='/api/tags' else {'models':[]}
        with self.lock.open('r') as fd:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(models.OllamaHTTP,'__call__',request),patch('sys.stdout',new_callable=io.StringIO) as out:
                code=models.main(['--config',str(config),'--catalog',str(catalog),'inspect','coder-next'])
        self.assertEqual(code,0);self.assertTrue(json.loads(out.getvalue())['installed'])
        self.assertEqual(wire_calls,[('GET','/api/tags',None),('GET','/api/ps',None)])
        self.assertFalse((self.root/'state').exists())

class ProvenanceSafetyTests(unittest.TestCase):
    def evidence(self):
        path = Path(__file__).with_name('fixtures') / 'provenance-synthetic.json'
        return json.loads(path.read_text())

    def test_fixture_is_explicitly_synthetic_and_not_verified_asset_evidence(self):
        value = self.evidence()
        self.assertEqual(value['schema_version'], 1)
        self.assertIs(value['synthetic_test_fixture'], True)
        self.assertTrue(all(source['source_bytes_verified'] is False
                            for source in value['sources']))
        self.assertEqual({p['id'] for p in value['profiles']},
                         {'flash', 'coder-next', 'gpt-oss-120b'})
        for profile in value['profiles']:
            self.assertFalse(profile['installable'])
            self.assertFalse(profile['current_full_weight_sha256_recomputed'])
            self.assertIsNone(profile['verified_weight_sha256'])
            self.assertIsNone(profile['verified_license'])
            self.assertEqual(len(profile['historical_asset_revision']), 40)

    def test_report_cannot_be_loaded_as_an_install_catalog(self):
        with self.assertRaises(models.ModelError):
            models.validate_catalog(self.evidence())
        with self.assertRaises(models.ModelError):
            models.validate_catalog(self.evidence()['profiles'])

    def test_runtime_and_report_digests_do_not_populate_builtin_model_catalog(self):
        value = self.evidence()
        sources = {source['id'] for source in value['sources']}
        for profile in value['profiles']:
            self.assertIn(profile['revision_source'], sources)
        for identity in value['runtime_identities']:
            self.assertIn(identity['kind'], {'runtime_image', 'runtime_executable'})
            self.assertIn(identity['source'], sources)
            self.assertFalse(identity['is_model_weight_or_manifest_digest'])
        for entry in models.read_catalog():
            for field in ('digest', 'source', 'license', 'license_url',
                          'version', 'required_bytes'):
                self.assertIsNone(entry[field])
            self.assertIs(entry['user_setup_required'], True)

    def test_all_builtin_installs_fail_closed_without_engine_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / 'store'; store.mkdir(mode=0o700)
            lock = root / 'generation.lock'; lock.touch(mode=0o600)
            engine = FakeEngine()
            manager = models.ModelManager(root / 'state', models.read_catalog(),
                                          engine, lock, store)
            for entry in models.read_catalog():
                with self.subTest(model=entry['id']), self.assertRaises(models.ModelError):
                    manager.install(entry['id'])
            self.assertEqual(engine.calls, [])

if __name__ == '__main__': unittest.main()
