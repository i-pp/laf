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
# Modified 2026-11-23: cover the separate owner-local model registry.
# Modified 2026-11-24: cover import-only registry scope mismatch on install.
# Modified 2026-11-24: cover independent model-state observations across the local CLI views.
# Modified 2026-11-25: cover observed loaded digests and uncertain running inventory.

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
    def test_registry_import_is_idempotent_and_never_grants_removal(self):
        self.engine.tags = [{'reference': ENTRY['reference'], 'digest': DIGEST, 'size_bytes': 512}]
        record = self.manager.register_existing('imported-coder', ENTRY['reference'], DIGEST)
        registry = self.root/'state'/'registry'
        active = registry/'active.json'
        path = registry/json.loads(active.read_bytes())['snapshot']/'imported-coder.json'
        original = path.read_bytes(), path.stat().st_ino
        original_pointer = active.read_bytes(), active.stat().st_ino
        self.assertEqual(record['ownership'], 'imported')
        self.assertEqual(record['schema_version'], 1)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(active.stat().st_mode & 0o777, 0o600)
        self.assertEqual(active.stat().st_nlink, 1)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.manager.register_existing('imported-coder', ENTRY['reference'], DIGEST), record)
        self.assertEqual((path.read_bytes(), path.stat().st_ino), original)
        self.assertEqual((active.read_bytes(), active.stat().st_ino), original_pointer)
        self.assertFalse((self.root/'state'/'models.json').exists())
        with self.assertRaises(models.ModelError):
            self.manager.remove('imported-coder')
        self.assertEqual(self.engine.calls, ['list', 'list'])
        self.assertEqual(self.manager.registry(), {
            'records': [record], 'installed': 'not_queried',
            'selectable': 'not_queried', 'loaded': 'not_queried',
        })
        self.assertEqual(self.engine.calls, ['list', 'list'])

    def test_registry_refuses_absent_changed_and_conflicting_identities(self):
        with self.assertRaisesRegex(models.ModelError, 'not confirmed'):
            self.manager.register_existing('imported-coder', ENTRY['reference'], DIGEST)
        self.assertEqual(self.manager.registry()['records'], [])
        self.engine.tags = [{'reference': ENTRY['reference'], 'digest': DIGEST, 'size_bytes': 512}]
        self.manager.register_existing('imported-coder', ENTRY['reference'], DIGEST)
        self.engine.tags[0]['digest'] = other = 'sha256:' + 'b' * 64
        with self.assertRaisesRegex(models.ModelError, 'not confirmed'):
            self.manager.register_existing('imported-coder', ENTRY['reference'], DIGEST)
        with self.assertRaisesRegex(models.ModelError, 'registry identity conflicts'):
            self.manager.register_existing('imported-coder', ENTRY['reference'], other)
        self.assertEqual(self.manager.registry()['records'][0]['digest'], DIGEST)

    def test_registry_migrates_only_confirmed_legacy_asset_without_replacing_journal(self):
        self.install()
        state = self.root/'state'/'models.json'
        original = state.read_bytes()
        self.engine.tags = []
        with self.assertRaisesRegex(models.ModelError, 'not confirmed'):
            self.manager.migrate_registry()
        self.assertEqual(self.manager.registry()['records'], [])
        self.engine.tags = [{'reference': ENTRY['reference'], 'digest': DIGEST, 'size_bytes': 512}]
        records = self.manager.migrate_registry()
        self.assertEqual(records, self.manager.migrate_registry())
        self.assertEqual(records[0]['ownership'], 'managed')
        self.assertEqual(state.read_bytes(), original)
        self.assertEqual(self.manager.list()[0]['id'], 'coder-next')
        self.manager.remove('coder-next')
        self.assertEqual(self.manager.registry()['records'], [])

    def test_registry_publication_is_readable_after_publication_syscall(self):
        self.engine.tags = [{'reference': ENTRY['reference'], 'digest': DIGEST, 'size_bytes': 512}]
        observed = []
        link, replace = os.link, os.replace

        def inspect_after(publish, source, target, *args, **kwargs):
            publish(source, target, *args, **kwargs)
            if Path(target).parent == self.root/'state'/'registry':
                observed.append([record['id'] for record in self.manager.registry()['records']])

        with patch.object(os, 'link', side_effect=lambda *a, **kw: inspect_after(link, *a, **kw)), \
                patch.object(os, 'replace', side_effect=lambda *a, **kw: inspect_after(replace, *a, **kw)):
            self.manager.register_existing('imported-coder', ENTRY['reference'], DIGEST)
        self.assertEqual(observed, [['imported-coder']])

    def _two_managed_models(self):
        second = dict(ENTRY, id='second-coder', reference='owner/coder:v2')
        self.manager.catalog[second['id']] = second
        self.install()
        self.manager.install(second['id'])
        self.engine.tags.append({'reference': ENTRY['reference'], 'digest': DIGEST, 'size_bytes': 512})
        return second

    def test_registry_migration_keeps_previous_commit_on_second_record_failure(self):
        self._two_managed_models()
        imported_ref = 'owner/preinstalled:v1'
        self.engine.tags.append({'reference': imported_ref, 'digest': DIGEST, 'size_bytes': 512})
        self.manager.register_existing('preinstalled', imported_ref, DIGEST)
        before = self.manager.registry()
        legacy = (self.root/'state'/'models.json').read_bytes()
        dumps = json.dumps

        def fail_second_record(value, *args, **kwargs):
            if isinstance(value, dict) and value.get('id') == 'second-coder':
                raise OSError('synthetic failure before batch commit')
            return dumps(value, *args, **kwargs)

        with patch.object(json, 'dumps', side_effect=fail_second_record):
            with self.assertRaisesRegex(OSError, 'synthetic failure'):
                self.manager.migrate_registry()
        self.assertEqual(self.manager.registry(), before)
        self.assertEqual((self.root/'state'/'models.json').read_bytes(), legacy)
        self.manager.migrate_registry()
        self.assertEqual([row['id'] for row in self.manager.registry()['records']],
                         ['coder-next', 'preinstalled', 'second-coder'])

    def test_registry_interrupted_before_commit_leaves_snapshot_invisible(self):
        self._two_managed_models()
        registry = self.root/'state'/'registry'
        replace = os.replace
        snapshots = []

        def stop_before_replace(source, target):
            self.assertEqual(target, registry/'active.json')
            snapshots.extend(registry.glob('snapshot-*'))
            self.assertEqual(len(list(snapshots[0].glob('*.json'))), 2)
            self.assertEqual(self.manager.registry()['records'], [])
            raise OSError('synthetic interruption before replacing active pointer')

        with patch.object(os, 'replace', side_effect=stop_before_replace):
            with self.assertRaisesRegex(OSError, 'synthetic interruption'):
                self.manager.migrate_registry()
        self.assertTrue(snapshots[0].exists())
        self.assertEqual(self.manager.registry()['records'], [])
        observed = []

        def observe_replace(source, target):
            replace(source, target)
            observed.append([row['id'] for row in self.manager.registry()['records']])

        with patch.object(os, 'replace', side_effect=observe_replace):
            self.manager.migrate_registry()
        self.assertEqual(observed, [['coder-next', 'second-coder']])

    def test_registry_reader_reopens_replaced_pointer_without_accepting_hardlinks(self):
        self._two_managed_models()
        reference = 'owner/preinstalled:v1'
        self.engine.tags.append({'reference': reference, 'digest': DIGEST, 'size_bytes': 512})
        self.manager.register_existing('preinstalled', reference, DIGEST)
        active = self.root/'state'/'registry'/'active.json'
        old_inode = active.stat().st_ino
        interrupted = False
        checked_file = models.checked_file

        def replace_during_reader_fstat(fd):
            nonlocal interrupted
            if os.fstat(fd).st_ino == old_inode and not interrupted:
                interrupted = True
                self.manager.migrate_registry()
                self.assertEqual(os.fstat(fd).st_nlink, 0)
            return checked_file(fd)

        with patch.object(models, 'checked_file', side_effect=replace_during_reader_fstat):
            self.assertEqual([row['id'] for row in self.manager.registry()['records']],
                             ['coder-next', 'preinstalled', 'second-coder'])
        self.assertTrue(interrupted)
        os.link(active, active.with_name('other-link'))
        with self.assertRaisesRegex(models.ModelError, 'single-link'):
            self.manager.registry()

    def test_registry_failure_after_replace_leaves_full_snapshot_readable(self):
        self._two_managed_models()
        registry = self.root/'state'/'registry'
        sync = self.manager._sync_registry_dir

        def fail_after_commit(path):
            if path == registry and (path/'active.json').exists():
                raise OSError('synthetic directory fsync failure after commit')
            sync(path)

        with patch.object(self.manager, '_sync_registry_dir', side_effect=fail_after_commit):
            with self.assertRaisesRegex(OSError, 'failure after commit'):
                self.manager.migrate_registry()
        reopened = models.ModelManager(self.root/'state', [ENTRY])
        self.assertEqual([row['id'] for row in reopened.registry()['records']],
                         ['coder-next', 'second-coder'])

    def test_registry_migration_reconciles_stale_managed_record_after_reinstall(self):
        self.install()
        self.manager.migrate_registry()
        self.manager.remove('coder-next')
        self.assertEqual(self.manager.registry()['records'], [])
        digest = 'sha256:'+'b'*64
        self.manager.catalog['coder-next'] = dict(ENTRY, digest=digest)
        self.engine.wrong_digest = True  # Fake engine now serves the new pinned digest.
        self.install()
        records = self.manager.migrate_registry()
        self.assertEqual(records[0]['digest'], digest)
        self.assertEqual(self.manager.registry()['records'], records)

    def _check_install_rejects_imported_collision(self, imported_id, imported_ref):
        self.engine.tags = [{'reference': imported_ref, 'digest': DIGEST, 'size_bytes': 512}]
        imported = self.manager.register_existing(imported_id, imported_ref, DIGEST)
        self.engine.tags.clear()  # The engine no longer has the historically imported asset.
        before = self.engine.calls[:]
        with self.assertRaisesRegex(models.ModelError, 'conflicts with an imported model'):
            self.install()
        self.assertEqual(self.engine.calls, before)
        self.assertFalse((self.root/'state'/'models.json').exists())
        self.assertEqual(self.manager.registry()['records'], [imported])

    def test_install_refuses_imported_reference_before_engine_access(self):
        self._check_install_rejects_imported_collision('imported-coder', ENTRY['reference'])

    def test_install_refuses_imported_id_before_engine_access(self):
        self._check_install_rejects_imported_collision(ENTRY['id'], 'owner/preinstalled:v1')

    def test_registry_migration_does_not_change_legacy_authority_on_historical_conflict(self):
        self.engine.tags = [{'reference': ENTRY['reference'], 'digest': DIGEST, 'size_bytes': 512}]
        imported = self.manager.register_existing('imported-coder', ENTRY['reference'], DIGEST)
        # Simulate a previously committed legacy install colliding with the sidecar.
        self.engine.tags.clear()
        separate_state = self.root/'legacy'
        legacy_manager = models.ModelManager(separate_state, [ENTRY], self.engine, self.lock, self.store)
        legacy_manager.install('coder-next')
        os.replace(separate_state/'models.json', self.root/'state'/'models.json')
        with self.assertRaisesRegex(models.ModelError, 'registry identity conflicts'):
            self.manager.migrate_registry()
        self.assertEqual(self.manager.registry()['records'], [imported])
        self.assertEqual(self.manager.list()[0]['id'], 'coder-next')
        self.assertEqual(self.manager.remove('coder-next')['status'], 'removed')
        self.assertEqual(self.manager.registry()['records'], [imported])

    def test_install_allows_a_nonconflicting_catalog_entry_with_existing_import(self):
        reference = 'owner/preinstalled:v1'
        self.engine.tags = [{'reference': reference, 'digest': DIGEST, 'size_bytes': 512}]
        imported = self.manager.register_existing('preinstalled', reference, DIGEST)
        self.assertEqual(self.install()['status'], 'installed')
        self.engine.tags.append({'reference': reference, 'digest': DIGEST, 'size_bytes': 512})
        records = self.manager.migrate_registry()
        self.assertEqual(self.manager.registry()['records'], records + [imported])

    def test_install_refuses_scope_changes_with_only_imported_records(self):
        reference = 'owner/preinstalled:v1'
        other_engine = FakeEngine()
        other_engine.identity = 'other-ollama'
        other_store = self.root/'other-store'
        other_store.mkdir(mode=0o700)
        other_lock_dir = self.root/'other-router'
        other_lock_dir.mkdir(mode=0o700)
        other_lock = other_lock_dir/'generation.lock'
        other_lock.touch(mode=0o600)

        for changed, engine, lock, store in (
            ('engine', other_engine, self.lock, self.store),
            ('store', self.engine, self.lock, other_store),
            ('lock', self.engine, other_lock, self.store),
        ):
            with self.subTest(changed=changed):
                self.engine.tags = [{'reference': reference, 'digest': DIGEST, 'size_bytes': 512}]
                state = self.root/f'{changed}-state'
                original = models.ModelManager(state, [ENTRY], self.engine, self.lock, self.store)
                imported = original.register_existing('preinstalled', reference, DIGEST)
                journal = state/'models.json'
                self.assertFalse(os.path.lexists(journal))
                registry = state/'registry'
                before = {path: (path.stat().st_ino, path.read_bytes()) for path in registry.rglob('*.json')}
                calls = engine.calls[:]

                manager = models.ModelManager(state, [ENTRY], engine, lock, store)
                with self.assertRaisesRegex(models.ModelError, 'local model registry scope changed'):
                    manager.install(ENTRY['id'])

                self.assertEqual(engine.calls, calls)
                self.assertFalse(os.path.lexists(journal))
                self.assertEqual({path: (path.stat().st_ino, path.read_bytes())
                                  for path in registry.rglob('*.json')}, before)
                self.assertEqual(original.registry()['records'], [imported])

    def test_registry_mutation_respects_generation_lock_and_uncertain_journal(self):
        with self.lock.open('r') as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(models.ModelError, 'active'):
                self.manager.register_existing('imported-coder', ENTRY['reference'], DIGEST)
        self.assertEqual(self.engine.calls, [])
        self.engine.fail = TimeoutError()
        with self.assertRaisesRegex(models.ModelError, 'uncertain'):
            self.install()
        before = self.engine.calls[:]
        with self.assertRaisesRegex(models.ModelError, 'uncertain'):
            self.manager.migrate_registry()
        with self.assertRaisesRegex(models.ModelError, 'uncertain'):
            self.manager.register_existing('imported-coder', ENTRY['reference'], DIGEST)
        self.assertEqual(self.engine.calls, before)
        self.assertEqual(self.manager.registry()['records'], [])

    def test_registry_refuses_scope_change_or_plugin_cache_without_rebinding(self):
        state = self.root/'state'
        self.engine.tags = [{'reference': ENTRY['reference'], 'digest': DIGEST, 'size_bytes': 512}]
        self.manager.register_existing('imported-coder', ENTRY['reference'], DIGEST)
        self.engine.identity = 'other-engine'
        with self.assertRaisesRegex(models.ModelError, 'scope changed'):
            self.manager.register_existing('imported-coder', ENTRY['reference'], DIGEST)
        self.assertEqual(self.manager.registry()['records'][0]['ownership'], 'imported')
        self.assertFalse((state/'models.json').exists())
        unsafe = self.root/'plugins'/'cache'/'state'
        manager = models.ModelManager(unsafe, [ENTRY], self.engine, self.lock, self.store)
        with self.assertRaisesRegex(models.ModelError, 'outside the plugin'):
            manager.register_existing('imported-coder', ENTRY['reference'], DIGEST)
        self.assertFalse((self.root/'plugins').exists())

    def test_installable_catalog_does_not_assert_registry_or_runtime_state(self):
        self.assertEqual(models.installable_catalog(models.read_catalog()), [])
        self.assertEqual(models.installable_catalog([ENTRY]), [ENTRY])
        for change in ({'digest': None}, {'user_setup_required': True}, {'engine': 'strata'}):
            self.assertEqual(models.installable_catalog([dict(ENTRY, **change)]), [])
        self.assertEqual(self.manager.registry()['records'], [])
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
    def test_inspect_recognizes_exact_registry_import_without_management(self):
        observed = {'reference': ENTRY['reference'], 'digest': DIGEST, 'size_bytes': 512}
        self.engine.tags = [observed]
        self.manager.register_existing(ENTRY['id'], ENTRY['reference'], DIGEST)
        self.engine.calls.clear()
        saved = self.root/'state'/'registry'/'active.json'
        pointer = (saved.read_bytes(), saved.stat().st_ino)
        for tags in ([observed], [dict(observed, digest='sha256:'+'b'*64)], []):
            self.engine.tags = tags
            with self.subTest(tags=tags):
                result = self.manager.inspect(ENTRY['id'])
                self.assertTrue(result['registry_record_matches'])
                self.assertEqual(result['registry_ownership'], 'imported')
                self.assertFalse(result['managed'])
                self.assertEqual(result['installed'], bool(tags))
                self.assertEqual(result['digest_matches'], None if not tags else tags[0]['digest']==DIGEST)
                self.assertFalse(result['loaded'])
        self.assertEqual(self.engine.calls, ['list', 'running'] * 3)
        self.assertEqual((saved.read_bytes(), saved.stat().st_ino), pointer)
        self.assertFalse((self.root/'state'/'models.json').exists())

    def test_inspect_import_not_matched_across_scope_and_catalog_identities(self):
        self.engine.tags = [{'reference': ENTRY['reference'], 'digest': DIGEST, 'size_bytes': 512}]
        self.manager.register_existing(ENTRY['id'], ENTRY['reference'], DIGEST)
        for entry, store in [(dict(ENTRY, digest=None), self.store),
                             (dict(ENTRY, digest='sha256:'+'b'*64), self.store),
                             (dict(ENTRY, reference='owner/coder:v2'), self.store),
                             (dict(ENTRY, id='other-id'), self.store),
                             (ENTRY, self.root/'other-store')]:
            with self.subTest(id=entry['id'], reference=entry['reference'], digest=entry['digest'], store=store):
                reader = models.ModelManager(self.root/'state', [entry], self.engine, self.lock, store)
                result = reader.inspect(entry['id'])
                self.assertFalse(result['registry_record_matches'])
                self.assertIsNone(result['registry_ownership'])
                self.assertFalse(result['managed'])

    def test_inspect_import_via_public_cli_is_get_only_while_generation_lock_held(self):
        config=self.root/'config.json';config.write_text(json.dumps({
            'state_dir':str(self.root/'state'),'model_store':str(self.store),
            'generation_lock':str(self.lock),'engine_url':'http://127.0.0.1:11434'}));config.chmod(0o600)
        catalog=self.root/'catalog.json';catalog.write_text(json.dumps([ENTRY]));catalog.chmod(0o600)
        calls=[]
        def endpoints(_self, method, path, body=None):
            calls.append((method, path, body))
            if (method, path) == ('GET', '/api/tags'):
                return {'models':[{'name':ENTRY['reference'],'digest':'a'*64,'size':512}]}
            if (method, path) == ('GET', '/api/ps'):
                return {'models':[]}
            self.fail(f'unexpected engine action: {method} {path}')
        with patch.object(models.OllamaHTTP, '__call__', endpoints):
            rc, row = ModelStateSeparationTests.invoke('model', '--config', str(config),
                'register-existing', ENTRY['id'], '--reference', ENTRY['reference'], '--digest', DIGEST)
            self.assertEqual(rc, 0, row)
            calls.clear()
            pointer = self.root/'state'/'registry'/'active.json'
            before = pointer.read_bytes(), pointer.stat().st_ino
            with self.lock.open('r') as fd:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                rc, row = ModelStateSeparationTests.invoke('model', '--config', str(config),
                    '--catalog', str(catalog), 'inspect', ENTRY['id'])
            self.assertEqual(rc, 0, row)
        self.assertTrue(row['registry_record_matches'])
        self.assertEqual(row['registry_ownership'], 'imported')
        self.assertTrue(row['installed'])
        self.assertFalse(row['loaded'])
        self.assertFalse(row['managed'])
        self.assertFalse(row['mutation_performed'])
        self.assertEqual(calls, [('GET','/api/tags',None),('GET','/api/ps',None)])
        self.assertEqual((pointer.read_bytes(), pointer.stat().st_ino), before)
        self.assertFalse((self.root/'state'/'models.json').exists())

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

class ModelStateSeparationTests(unittest.TestCase):
    """Use the real CLI parsers with synthetic provider-specific observations."""

    setUp = ModelTests.setUp

    @staticmethod
    def invoke(*args):
        from . import cli
        with patch('sys.stdout', new_callable=io.StringIO) as out, \
             patch('sys.stderr', new_callable=io.StringIO) as err:
            rc = cli.main(list(args))
        return rc, json.loads((out if rc == 0 else err).getvalue())

    def test_installed_selectable_and_loaded_are_independent_observations(self):
        from . import cli
        config = self.root/'config.json'
        config.write_text(json.dumps({
            'state_dir': str(self.root/'state'), 'model_store': str(self.store),
            'generation_lock': str(self.lock), 'engine_url': 'http://127.0.0.1:11434',
        }))
        config.chmod(0o600)
        catalog = self.root/'catalog.json'
        catalog.write_text(json.dumps([ENTRY]))
        catalog.chmod(0o600)
        model_args = ('model', '--config', str(config), '--catalog', str(catalog))
        reference = ENTRY['reference']
        model_id = ENTRY['id']
        wire_row = {'name': reference, 'digest': DIGEST, 'size': 512}

        self.engine.tags = [{'reference': reference, 'digest': DIGEST, 'size_bytes': 512}]
        self.manager.register_existing(model_id, reference, DIGEST)
        self.engine.calls.clear()
        state_dir = self.root/'state'
        state_before = {p.relative_to(state_dir): p.read_bytes() for p in state_dir.rglob('*') if p.is_file()}

        with patch.object(models.OllamaHTTP, '__call__', side_effect=AssertionError('registry contacted engine')), \
             patch.object(cli, 'request', side_effect=AssertionError('registry contacted Router')):
            rc, registry = self.invoke(*model_args, 'registry')
        self.assertEqual(rc, 0, registry)
        self.assertEqual([r['id'] for r in registry['records']], [model_id])
        self.assertEqual([registry[name] for name in ('installed', 'selectable', 'loaded')],
                         ['not_queried'] * 3)
        self.assertEqual(self.engine.calls, [])

        loaded_digest = 'sha256:' + 'b' * 64
        for selectable, installed, running_rows, expected_digest in (
            (False, True, [], None),
            (True, False, [dict(wire_row, name='owner/coder:other')], None),
            (True, True, [], None),
            (True, True, [wire_row], DIGEST),
            (True, True, [dict(wire_row, digest='b' * 64)], loaded_digest),
            (True, False, [dict(wire_row, digest=loaded_digest)], loaded_digest),
        ):
            loaded = expected_digest is not None
            with self.subTest(selectable=selectable, installed=installed, loaded=loaded):
                router_calls, engine_calls = [], []
                def router(path, method, route):
                    router_calls.append((method, route))
                    self.assertEqual(route, '/v1/models')
                    return {'models': [model_id] if selectable else ['other-model'],
                            'context_limit': 8192, 'max_output_tokens': 1024,
                            'max_large_models_loaded': 1}

                def engine(_self, method, route, body=None):
                    engine_calls.append((method, route, body))
                    self.assertIn(route, {'/api/tags', '/api/ps'})
                    if route == '/api/tags':
                        return {'models': [wire_row] if installed else []}
                    return {'models': running_rows}

                with patch.object(cli, 'request', side_effect=router), \
                     patch.object(models.OllamaHTTP, '__call__', engine):
                    rc, router_view = self.invoke('models')
                    self.assertEqual(rc, 0, router_view)
                    self.assertEqual(engine_calls, [])
                    rc, engine_view = self.invoke(*model_args, 'inspect', model_id)
                    self.assertEqual(rc, 0, engine_view)
                self.assertIs(model_id in router_view['models'], selectable)
                self.assertIs(engine_view['installed'], installed)
                self.assertIs(engine_view['loaded'], loaded)
                self.assertEqual(engine_view['loaded_manifest_digest'], expected_digest)
                self.assertEqual(engine_view['engine_manifest_digest'], DIGEST if installed else None)
                self.assertIs(engine_view['digest_matches'], True if installed else None)
                self.assertEqual(router_calls, [('GET', '/v1/models')])
                self.assertEqual(engine_calls, [('GET', '/api/tags', None), ('GET', '/api/ps', None)])
                self.assertFalse(engine_view['mutation_performed'])

        self.assertEqual({p.relative_to(state_dir): p.read_bytes() for p in state_dir.rglob('*') if p.is_file()},
                         state_before)

        with patch.object(cli, 'request', side_effect=OSError('synthetic Router unavailable')):
            rc, unavailable = self.invoke('models')
        self.assertNotEqual(rc, 0)
        self.assertNotIn('models', unavailable)

    def test_inspect_cli_reports_uncertain_without_partial_output(self):
        from . import cli
        config = self.root/'config.json'
        config.write_text(json.dumps({
            'state_dir': str(self.root/'state'), 'model_store': str(self.store),
            'generation_lock': str(self.lock), 'engine_url': 'http://127.0.0.1:11434',
        }))
        config.chmod(0o600)
        catalog = self.root/'catalog.json'
        catalog.write_text(json.dumps([ENTRY]))
        catalog.chmod(0o600)
        row = {'name': ENTRY['reference'], 'digest': DIGEST, 'size': 512}
        other_digest = 'sha256:' + 'b' * 64

        for running_rows in ([row, dict(row, digest=other_digest)],
                             [dict(row, digest='not-a-manifest-digest')],
                             [{'name': row['name'], 'size': row['size']}],
                             OSError('synthetic engine unavailable')):
            with self.subTest(running_rows=running_rows):
                calls = []
                def engine(_self, method, route, body=None):
                    calls.append((method, route, body))
                    if route == '/api/tags':
                        return {'models': [row]}
                    if isinstance(running_rows, OSError):
                        raise running_rows
                    return {'models': running_rows}

                with patch.object(cli, 'request', side_effect=AssertionError('inspect contacted Router')), \
                     patch.object(models.OllamaHTTP, '__call__', engine), \
                     patch('sys.stdout', new_callable=io.StringIO) as out, \
                     patch('sys.stderr', new_callable=io.StringIO) as err:
                    rc = cli.main(['model', '--config', str(config), '--catalog', str(catalog),
                                   'inspect', ENTRY['id']])
                self.assertNotEqual(rc, 0)
                self.assertEqual(out.getvalue(), '')
                self.assertEqual(json.loads(err.getvalue())['status'], 'blocked_or_uncertain')
                self.assertEqual(calls, [('GET', '/api/tags', None), ('GET', '/api/ps', None)])
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
