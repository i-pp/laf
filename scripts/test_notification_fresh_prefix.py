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

# Added 2026-11-15: synthetic first-ledger Events activation regressions.
"""Real temporary SQLite with fake providers; no native, thread or callback."""
from contextlib import closing
import os
import sqlite3
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'runtime/integration'),
               str(ROOT / 'runtime/private_codex/src')]
import test_bootstrap_bindings as fixtures
import test_codex_catalog_effort as peers
import codex_host
import notification_runtime as composition
from codex_host import HostGateway
from codex_pipeline import PipelineError
from local_principal import OAuthPrincipal
from notification_outbox import BoundedResolver, NotificationError
from notification_runtime import NotificationRuntime, build_systemd_notification_runtime


class FreshPrefixEvents(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.BindingTests('test_codex_first_binding_is_atomic_finite_and_does_not_execute')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.f = fixture
        fixture.codex()
        self.runtime = fixture.runtime
        self.principal = OAuthPrincipal('https://issuer.example.invalid', 'events-fixture-owner')
        self.provider_calls = 0
        self.effects = []
        outer = self

        class Resolver:
            interruptible = True

            def resolve_with_timeout(self, host, timeout_seconds):
                outer.effects.append('DNS')
                raise AssertionError('Activation cannot resolve a callback')

        resolver = BoundedResolver(Resolver())

        class Callback:
            def __init__(self, name):
                self.resolver = resolver
                self.name = name

            def __call__(self, *args, **kwargs):
                outer.effects.append(self.name)
                raise AssertionError('Activation cannot contact a callback')

        self.providers = {'secret_cipher': peers.TestCipher(),
                          'owner_hash_key': b'fixture-key-never-production-00000',
                          'resolver': resolver, 'sender': Callback('SEND'),
                          'callback_verifier': Callback('VERIFY')}

    def loader(self, **kwargs):
        self.provider_calls += 1
        return dict(self.providers)

    def components(self, **kwargs):
        options = {'events_transport': True, 'environ': {'LAF_MCP_EVENTS_ENABLED': '1'},
                   'authenticated_principals': (self.principal,), 'provider_loader': self.loader}
        options.update(kwargs)
        return build_systemd_notification_runtime(self.runtime, **options)

    def gateway(self, components, **kwargs):
        with patch.object(HostGateway, 'require_host'):
            gateway = HostGateway(self.runtime, str(self.f.native),
                                  self.f.prefix / 'runtime/private_codex', lambda: False,
                                  notification_runtime=components, **kwargs)
        self.addCleanup(gateway.close)
        return gateway

    def test_missing_first_ledger_activates_same_runtime_after_real_pipeline_creation(self):
        components = self.components()
        self.assertFalse(components.enabled)
        self.assertEqual(components.disabled_reason, 'PRIVATE_PIPELINE_LEDGER_UNAVAILABLE')
        self.assertFalse(components.source_path.exists())
        gateway = self.gateway(components)
        self.assertTrue(components.enabled)
        self.assertIs(gateway.notification_worker_factory.__self__, components)
        self.assertTrue(gateway.allow_external_notifications)
        self.assertEqual(components.outbox.source_path, gateway.pipeline.db)
        self.assertIs(components.events_service.outbox, components.outbox)
        self.assertEqual(self.provider_calls, 1)
        self.assertEqual(self.effects, [])
        self.assertIsNone(gateway.notification_worker)

    def test_builder_authorization_closures_keep_the_activated_runtime_and_exact_principal(self):
        components = self.components()
        gateway = self.gateway(components)
        outbox = components.outbox
        service = components.events_service
        task = peers.TASK_A
        other = OAuthPrincipal(self.principal.issuer, 'other-fixture-owner')
        # Synthetic source task in the real temporary ledger; no thread/turn.
        gateway.pipeline._mutate(lambda state: state['tasks'].update({task: {'id': task}}))
        self.assertTrue(service.authorize_task(self.principal.identity, task))
        self.assertFalse(service.authorize_task(other.identity, task))
        self.assertFalse(service.authorize_task(self.principal.identity, peers.TASK_B))
        service.bind_task_owner(task, self.principal)
        self.assertTrue(service.owner_access_check(outbox.owner_hash(self.principal), task))
        self.assertFalse(service.owner_access_check(outbox.owner_hash(other), task))
        with self.assertRaises(NotificationError):
            service.bind_task_owner(task, other)
        self.assertEqual(self.effects, [])

    def test_deferred_activation_uses_retained_validated_provider_objects_without_reload(self):
        components = self.components()
        expected = dict(self.providers)
        components.sender = object()
        self.providers.clear()
        self.gateway(components)
        self.assertTrue(components.enabled)
        self.assertIs(components.sender, expected['sender'])
        self.assertIs(components.outbox.secret_cipher, expected['secret_cipher'])
        self.assertIs(components.outbox.resolver, expected['resolver'])
        self.assertIs(components.events_service.callback_verifier, expected['callback_verifier'])
        self.assertEqual(self.provider_calls, 1)
        self.assertEqual(self.effects, [])

    def test_activation_and_worker_creation_do_not_start_delivery_and_repeated_pump_starts_once(self):
        components = self.components()
        gateway = self.gateway(components)
        outbox = components.outbox
        service = components.events_service
        for _ in range(2):
            self.assertTrue(components.activate_after_pipeline(gateway.pipeline))
            self.assertIs(components.outbox, outbox)
            self.assertIs(components.events_service, service)
        worker = components.worker_factory(gateway.pipeline)
        self.assertIsNone(worker._thread)
        with patch.object(worker, 'start', return_value=True) as start, \
                patch.object(worker, 'stop', return_value=True), \
                patch.object(gateway, 'notification_worker_factory', return_value=worker) as factory:
            self.assertEqual(gateway._start_notification_worker(), {'status': 'RUNNING'})
            self.assertEqual(gateway._start_notification_worker(), {'status': 'RUNNING'})
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(start.call_count, 1)
            self.assertIs(gateway.pipeline.notification_outbox, outbox)
            self.assertEqual(gateway._stop_notification_worker(), {'status': 'STOPPED'})
        self.assertEqual(self.effects, [])

    def test_existing_private_pipeline_still_enables_immediately_and_reuses_composition(self):
        first = self.gateway(None)
        first.close()
        components = self.components()
        self.assertTrue(components.enabled)
        outbox = components.outbox
        gateway = self.gateway(components)
        self.assertIs(components.outbox, outbox)
        self.assertTrue(gateway.allow_external_notifications)
        self.assertEqual(self.provider_calls, 1)

    def test_disabled_default_never_loads_providers_or_activates(self):
        components = self.components(events_transport=False)
        gateway = self.gateway(components)
        self.assertFalse(components.enabled)
        self.assertEqual(components.disabled_reason, 'EXTERNAL_DELIVERY_DISABLED')
        self.assertFalse(components.activate_after_pipeline(gateway.pipeline))
        self.assertIsNone(gateway.notification_worker_factory)
        self.assertFalse(components.outbox_path.exists())
        self.assertEqual(self.provider_calls, 0)

    def test_principal_rejections_remain_disabled_after_pipeline_creation(self):
        cases = [(None, 'PRINCIPAL_REQUIRED'),
                 ((self.principal, self.principal), 'PRINCIPAL_CONFIGURATION_INVALID')]
        first = self.gateway(None)
        for principals, reason in cases:
            with self.subTest(reason=reason):
                components = self.components(authenticated_principals=principals)
                self.assertFalse(components.activate_after_pipeline(first.pipeline))
                self.assertEqual(components.disabled_reason, reason)
                self.assertIsNone(components.outbox)
        self.assertEqual(self.provider_calls, 0)

    def test_local_mode_mismatch_cannot_activate_later(self):
        components = self.components(local_single_user=True)
        gateway = self.gateway(components)
        self.assertFalse(components.enabled)
        self.assertEqual(components.disabled_reason, 'PRINCIPAL_MODE_MISMATCH')
        self.assertFalse(gateway.allow_external_notifications)
        self.assertEqual(self.provider_calls, 0)

    def test_invalid_provider_stays_disabled_after_real_ledger_exists(self):
        expected = {'resolver': ('BOUNDED_RESOLVER_REQUIRED', lambda host: []),
                    'sender': ('NOTIFICATION_PROVIDER_INVALID', object()),
                    'owner_hash_key': ('OWNER_HASH_KEY_UNAVAILABLE', b'short'),
                    'secret_cipher': ('NOTIFICATION_PROVIDER_UNAVAILABLE', None)}
        for name, (reason, value) in expected.items():
            with self.subTest(provider=name):
                old = self.providers[name]
                self.providers[name] = value
                components = self.components()
                self.providers[name] = old
                gateway = self.gateway(components)
                self.assertFalse(components.activate_after_pipeline(gateway.pipeline))
                self.assertEqual(components.disabled_reason, reason)
                self.assertIsNone(components.outbox)
                gateway.close()
        self.assertFalse((self.runtime / 'mcp-events.sqlite3').exists())

    def test_provider_loader_failure_is_not_retried_after_pipeline_creation(self):
        loader = Mock(side_effect=NotificationError('OWNER_HASH_KEY_UNAVAILABLE'))
        components = self.components(provider_loader=loader)
        gateway = self.gateway(components)
        self.assertFalse(components.activate_after_pipeline(gateway.pipeline))
        self.assertEqual(components.disabled_reason, 'OWNER_HASH_KEY_UNAVAILABLE')
        self.assertEqual(loader.call_count, 1)
        self.assertFalse(components.outbox_path.exists())

    def test_failed_pipeline_constructor_never_creates_outbox_or_activates(self):
        components = self.components()
        with patch.object(codex_host, 'Pipeline', side_effect=PipelineError('INJECTED_FAILURE')):
            with self.assertRaises(PipelineError):
                self.gateway(components)
        self.assertFalse(components.enabled)
        self.assertFalse(components.source_path.exists())
        self.assertFalse(components.outbox_path.exists())
        self.assertEqual(self.effects, [])

    def test_wrong_database_consumes_deferral_without_creating_storage(self):
        components = self.components()
        self.assertFalse(components.activate_after_pipeline(SimpleNamespace(db=self.runtime / 'foreign.sqlite3')))
        gateway = self.gateway(components)
        self.assertFalse(components.activate_after_pipeline(gateway.pipeline))
        self.assertFalse(gateway.allow_external_notifications)
        self.assertFalse(components.outbox_path.exists())

    def test_missing_ledger_at_activation_consumes_deferral_without_later_retry(self):
        components = self.components()
        self.assertFalse(components.activate_after_pipeline(SimpleNamespace(db=components.source_path)))
        self.gateway(components)
        self.assertFalse(components.enabled)
        self.assertFalse(components.outbox_path.exists())

    def test_replaced_runtime_directory_cannot_inherit_delivery_authority(self):
        components = self.components()
        old_root = self.runtime.with_name('preserved-original')
        self.runtime.rename(old_root)
        self.runtime.mkdir(mode=0o700)
        source = self.runtime / 'codex-pipeline.sqlite3'
        source.write_bytes(b'unrelated private file')
        source.chmod(0o600)
        before = source.read_bytes()
        self.assertFalse(components.activate_after_pipeline(SimpleNamespace(db=source)))
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(components.outbox_path.exists())
        self.assertTrue(old_root.is_dir())

    def test_existing_unsafe_ledger_never_gets_a_deferral_even_after_repair(self):
        source = self.runtime / 'codex-pipeline.sqlite3'
        source.write_bytes(b'foreign unknown data')
        source.chmod(0o644)
        components = self.components()
        source.chmod(0o600)
        with patch.object(composition, 'NotificationOutbox') as outbox:
            self.assertFalse(components.activate_after_pipeline(SimpleNamespace(db=source)))
            outbox.assert_not_called()
        self.assertFalse(components.enabled)
        self.assertEqual(source.read_bytes(), b'foreign unknown data')

    def test_unsafe_source_disappearing_after_observation_stays_disabled(self):
        source = self.runtime / 'codex-pipeline.sqlite3'
        source.write_bytes(b'unknown unsafe source')
        source.chmod(0o644)
        real = Path.lstat
        observed = []

        def disappear(path, *args, **kwargs):
            info = real(path, *args, **kwargs)
            if path == source and not observed:
                observed.append(stat.S_IMODE(info.st_mode))
                source.unlink()
            return info

        with patch.object(Path, 'lstat', disappear):
            components = self.components()
        self.assertEqual(observed, [0o644])
        self.gateway(components)
        self.assertFalse(components.enabled)
        self.assertFalse(components.outbox_path.exists())

    def test_unsafe_root_repaired_after_observation_stays_disabled(self):
        self.runtime.chmod(0o755)
        real = NotificationRuntime._private_root_identity
        observed = []

        def repair_after_check(components):
            identity = real(components)
            if not observed:
                observed.append(identity)
                self.runtime.chmod(0o700)
            return identity

        with patch.object(NotificationRuntime, '_private_root_identity', repair_after_check):
            components = self.components()
        self.assertEqual(observed, [None])
        self.gateway(components)
        self.assertFalse(components.enabled)
        self.assertFalse(components.outbox_path.exists())

    def test_existing_fifo_is_refused_without_open_or_deferred_activation(self):
        source = self.runtime / 'codex-pipeline.sqlite3'
        os.mkfifo(source, 0o600)
        components = self.components()
        with patch.object(composition, 'NotificationOutbox') as outbox:
            self.assertFalse(components.activate_after_pipeline(SimpleNamespace(db=source)))
            outbox.assert_not_called()
        self.assertFalse(components.outbox_path.exists())

    def test_unsafe_root_never_gets_a_deferral_even_after_repair(self):
        self.runtime.chmod(0o755)
        components = self.components()
        self.runtime.chmod(0o700)
        self.gateway(components)
        self.assertFalse(components.enabled)
        self.assertFalse(components.outbox_path.exists())

    def test_symlink_ledger_inserted_after_deferral_cannot_enable_or_modify_target(self):
        components = self.components()
        target = self.f.parent / 'unrelated-ledger'
        target.write_bytes(b'untouched')
        target.chmod(0o600)
        components.source_path.symlink_to(target)
        with patch.object(composition, 'NotificationOutbox') as outbox:
            self.assertFalse(components.activate_after_pipeline(SimpleNamespace(db=components.source_path)))
            outbox.assert_not_called()
        self.assertEqual(target.read_bytes(), b'untouched')
        self.assertFalse(components.outbox_path.exists())

    def test_private_mode_changed_after_deferral_is_refused(self):
        components = self.components()
        source = components.source_path
        source.write_bytes(b'unknown')
        source.chmod(0o644)
        self.assertFalse(components.activate_after_pipeline(SimpleNamespace(db=source)))
        self.assertFalse(components.outbox_path.exists())

    def test_root_mode_changed_after_deferral_is_refused(self):
        components = self.components()
        source = components.source_path
        source.write_bytes(b'unknown')
        source.chmod(0o600)
        self.runtime.chmod(0o755)
        self.assertFalse(components.activate_after_pipeline(SimpleNamespace(db=source)))
        self.runtime.chmod(0o700)
        self.assertFalse(components.outbox_path.exists())

    def test_symlink_runtime_never_gets_a_deferral_even_after_restoration(self):
        original = self.runtime.with_name('original-private-root')
        self.runtime.rename(original)
        self.runtime.symlink_to(original, target_is_directory=True)
        components = self.components()
        self.runtime.unlink()
        original.rename(self.runtime)
        self.gateway(components)
        self.assertFalse(components.enabled)
        self.assertFalse(components.outbox_path.exists())

    def test_foreign_source_owner_cannot_acquire_a_deferral(self):
        source = self.runtime / 'codex-pipeline.sqlite3'
        source.write_bytes(b'unknown')
        source.chmod(0o600)
        real = Path.lstat

        def foreign(path, *args, **kwargs):
            info = real(path, *args, **kwargs)
            if path == source:
                fields = list(info)
                fields[stat.ST_UID] = os.getuid() + 1
                return os.stat_result(fields)
            return info

        with patch.object(Path, 'lstat', foreign):
            components = self.components()
        self.assertFalse(components.activate_after_pipeline(SimpleNamespace(db=source)))
        self.assertFalse(components.outbox_path.exists())

    def test_existing_outbox_symlink_refuses_without_touching_target(self):
        components = self.components()
        target = self.f.parent / 'unrelated-outbox'
        target.write_bytes(b'preserve private data')
        target.chmod(0o600)
        components.outbox_path.symlink_to(target)
        gateway = self.gateway(components)
        self.assertFalse(components.enabled)
        self.assertEqual(components.disabled_reason, 'NOTIFICATION_STORAGE_UNAVAILABLE')
        self.assertFalse(gateway.allow_external_notifications)
        self.assertEqual(target.read_bytes(), b'preserve private data')

    def test_retained_and_unmarked_notification_state_stays_untouched(self):
        gateway = self.gateway(self.components(events_transport=False))
        names = ('mcp-events.sqlite3', 'mcp-events.sqlite3-wal', 'laf-mcp-events.sqlite3')
        for name in names:
            with self.subTest(name=name):
                saved = self.runtime / name
                with closing(sqlite3.connect(saved)) as conn:
                    conn.execute('CREATE TABLE saved_notification_state (id INTEGER)')
                    conn.commit()
                saved.chmod(0o600)
                old_bytes = saved.read_bytes()
                components = self.components()
                self.assertFalse(components.activate_after_pipeline(gateway.pipeline))
                self.assertFalse(components.enabled)
                self.assertEqual(saved.read_bytes(), old_bytes)
                if name != components.OUTBOX_NAME:
                    self.assertFalse(components.outbox_path.exists())
                    self.assertEqual(components.disabled_reason, 'NOTIFICATION_RETAINED_STATE_REQUIRES_NEW_PREFIX')
                else:
                    self.assertEqual(components.disabled_reason, 'NOTIFICATION_STORAGE_UNAVAILABLE')
                saved.unlink()
        components = self.components()
        self.assertTrue(components.enabled)
        with closing(sqlite3.connect(components.outbox_path)) as conn:
            self.assertEqual(conn.execute('PRAGMA application_id').fetchone()[0],
                             composition.NotificationOutbox.APPLICATION_ID)
        self.assertTrue(self.components().enabled)

    def test_interrupted_storage_consumes_deferral_and_cannot_be_silently_retried(self):
        components = self.components()
        with patch.object(composition, 'NotificationOutbox', side_effect=KeyboardInterrupt) as outbox:
            with self.assertRaises(KeyboardInterrupt):
                self.gateway(components)
            self.assertFalse(components.activate_after_pipeline(SimpleNamespace(db=components.source_path)))
            self.assertEqual(outbox.call_count, 1)
        self.assertFalse(components.enabled)
        self.assertFalse(components.outbox_path.exists())

    def test_storage_failure_is_sticky_and_never_reloads_or_retries(self):
        components = self.components()
        with patch.object(composition, 'NotificationOutbox', side_effect=NotificationError('INJECTED_FAILURE')) as outbox:
            gateway = self.gateway(components)
            self.assertFalse(components.activate_after_pipeline(gateway.pipeline))
            self.assertEqual(outbox.call_count, 1)
        self.assertEqual(components.disabled_reason, 'NOTIFICATION_STORAGE_UNAVAILABLE')
        self.assertFalse(gateway.allow_external_notifications)
        self.assertEqual(self.provider_calls, 1)
        self.assertEqual(self.effects, [])

    def test_conflicting_gateway_configuration_still_refuses_before_pipeline_creation(self):
        components = self.components()
        for options in ({'notification_worker_factory': lambda pipeline: None},
                        {'allow_external_notifications': True}):
            with self.subTest(options=tuple(options)):
                with self.assertRaises(PipelineError) as caught:
                    self.gateway(components, **options)
                self.assertEqual(caught.exception.code, 'CONFLICTING_NOTIFICATION_CONFIGURATION')
        self.assertFalse(components.source_path.exists())
        self.assertFalse(components.outbox_path.exists())

    def test_existing_duck_typed_disabled_injection_is_not_activated(self):
        components = SimpleNamespace(worker_factory=lambda pipeline: None,
                                     enabled=False, runtime_root=self.runtime)
        gateway = self.gateway(components)
        self.assertFalse(gateway.allow_external_notifications)
        self.assertIsNone(gateway.notification_worker_factory)

    def test_changed_public_paths_cannot_redirect_a_deferred_binding(self):
        for attribute in ('source_path', 'outbox_path', 'runtime_root'):
            with self.subTest(attribute=attribute):
                components = self.components()
                setattr(components, attribute, self.f.parent / 'unrelated')
                with patch.object(composition, 'NotificationOutbox') as outbox:
                    self.assertFalse(components.activate_after_pipeline(
                        SimpleNamespace(db=self.runtime / 'codex-pipeline.sqlite3')))
                    outbox.assert_not_called()
        self.assertFalse((self.runtime / 'mcp-events.sqlite3').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
