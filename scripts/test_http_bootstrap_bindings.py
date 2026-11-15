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
# All terms and conditions of KOL-v1 apply. See the accompanying LICENSE file for the complete license text.
# This header is a summary, not a separate license or an amendment to KOL-v1.

# Added 2026-11-15: HTTP parity with explicit immutable bootstrap bindings.
"""Fake OAuth/HTTP/provider composition; no listener, credential or model use."""
from contextlib import ExitStack, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'runtime/integration'),
               str(ROOT / 'runtime/private_codex/src'), str(ROOT / 'runtime/public_laf/mcp/src')]
import test_bootstrap_bindings as fixtures
import mcp_adapter as facade
import mcp_http_transport
import notification_runtime
import oauth_auth
import codex_host
import laf_shared_tools as shared
import legacy_tool_adapter
from local_principal import OAuthPrincipal


class HttpBootstrapBindings(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.BindingTests('test_codex_first_binding_is_atomic_finite_and_does_not_execute')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.f = fixture
        self.runtime, self.marker = fixture.runtime, fixture.marker
        self.project = fixture.prefix / 'runtime/private_codex'
        self.owner = OAuthPrincipal('https://issuer.example.invalid', 'http-fixture-owner')
        self.other = OAuthPrincipal(self.owner.issuer, 'http-fixture-other')
        self.principals = (self.owner,)
        self.events = SimpleNamespace(enabled=True, handle=Mock(return_value={}))
        self.notifications = SimpleNamespace(enabled=True, runtime_root=self.runtime,
            adapter_options=lambda: {'events_service': self.events}, outbox=object())
        self.apps = []
        self.served = []
        self.callback = None
        self.router_hook = None

    def mutate(self):
        self.marker.write_bytes(self.marker.read_bytes() + b' ')

    def flags(self, *, binding=True, router=True):
        result = ['--runtime', str(self.runtime), '--transport', 'http',
                  '--allow-codex-pipeline']
        if router:
            result += ['--allow-existing-tools', '--allow-laf-router']
            if binding:
                result += ['--laf-bootstrap-bindings']
            else:
                result += ['--laf-router-socket', str(self.f.socket),
                           '--laf-router-state', str(self.f.prefix / 'receipts'),
                           '--laf-router-model', 'raw-router']
        return result

    def invoke(self, flags=None, *, notifications=None, composite=None):
        verifier = SimpleNamespace(principals=self.principals,
            issuer=self.owner.issuer, protected_resource_metadata=Mock(return_value={}))
        self.pipeline = SimpleNamespace(dispatch=Mock(return_value={}))

        def router(**kwargs):
            app = SimpleNamespace(call=Mock(return_value={'outcome': 'OBSERVED'}),
                                  receipts=SimpleNamespace(close=Mock()))
            self.apps.append((kwargs, app))
            if self.router_hook:
                self.router_hook(app)
            return app

        def serve(adapter, **options):
            self.served.append((adapter, options))
            if self.callback:
                self.callback(adapter)
            return 0

        def integrated(adapter, function, **options):
            try:
                return function(adapter, **options)
            finally:
                self.assertTrue(adapter.close(timeout=0))

        with ExitStack() as stack:
            stack.enter_context(patch.object(sys, 'argv', ['fixture-http', *(flags or self.flags())]))
            # A copied launch.py uses this prefix as its default module anchor.
            # runtime_path and bootstrap_paths retain their real scope guards.
            stack.enter_context(patch.object(facade, 'resolve_project_root', return_value=self.project))
            stack.enter_context(patch.object(oauth_auth.OAuthJWTVerifier, 'from_environment', return_value=verifier))
            stack.enter_context(patch.dict(os.environ, {'ACEIS_OAUTH_RESOURCE_URL': 'https://resource.example.invalid/mcp'}))
            self.provider = stack.enter_context(patch.object(notification_runtime,
                'build_systemd_notification_runtime', return_value=notifications or self.notifications))
            self.proxy = stack.enter_context(patch.object(codex_host, 'DaemonPipelineProxy', return_value=self.pipeline))
            self.router = stack.enter_context(patch.object(shared, 'build_router_application', side_effect=router))
            stack.enter_context(patch.object(shared, 'run_integrated_http', side_effect=integrated))
            stack.enter_context(patch.object(mcp_http_transport, 'serve_http', side_effect=serve))
            if composite:
                stack.enter_context(patch.object(shared, 'CompositeSharedTools', side_effect=composite))
            with redirect_stderr(io.StringIO()):
                return facade.main()

    def test_configured_mode_forwards_one_exact_snapshot_and_socket_identity(self):
        self.f.router()
        raw = self.marker.read_bytes()
        binding = json.loads(raw)['router']
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(len(self.apps), 1)
        args, app = self.apps[0]
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual(args, {'socket_path': binding['socket'], 'state_directory': binding['receipts'],
            'models': binding['models'], 'socket_identity': binding['socket_identity'],
            'binding_runtime': self.runtime, 'binding_sha256': digest})
        adapter, options = self.served[0]
        self.assertEqual(adapter.backend_state_sha256, digest)
        self.assertEqual(adapter.shared_tool_adapter.legacy.backend_state_sha256, digest)
        self.assertIs(options['oauth_verifier'].principals[0], self.owner)
        self.assertFalse(options['local_single_user'])
        self.assertEqual(app.receipts.close.call_count, 1)

    def test_explicit_deny_mode_keeps_five_router_schemas_without_endpoint_or_receipts(self):
        responses = []
        self.callback = lambda adapter: responses.append(adapter.call_tool(
            'laf_router_status', {}, 1, request_principal=self.owner))
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(len(self.apps), 0)
        self.router.assert_not_called()
        data = responses[0]['structuredContent']
        self.assertEqual(data['code'], 'BACKEND_UNCONFIGURED')
        self.assertFalse(data['dispatch_started'])
        names = {tool['name'] for tool in self.served[0][0].tools}
        self.assertEqual({name for name in names if name.startswith('laf_router_')}, set(shared.TOOLS))
        self.assertEqual(list((self.f.prefix / 'receipts').iterdir()), [])

    def test_configured_codex_with_denied_router_cannot_submit_or_open_receipts(self):
        self.f.codex()

        def check(adapter):
            result = adapter.call_tool('laf_router_submit_job', {
                'attempt_id': '0' * 32, 'model': 'raw-router',
                'messages': [{'role': 'user', 'content': 'Synthetic denied fixture'}],
                'max_tokens': 1}, 1, request_principal=self.owner)
            data = result['structuredContent']
            self.assertEqual(data['code'], 'BACKEND_UNCONFIGURED')
            self.assertEqual(data['outcome'], 'NOT_SUBMITTED')
            self.assertFalse(data['dispatch_started'])
            self.pipeline.dispatch.assert_not_called()

        self.callback = check
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.apps, [])
        self.assertEqual(list((self.f.prefix / 'receipts').iterdir()), [])

    def test_raw_options_cannot_bypass_schema1_explicit_denial(self):
        self.assertEqual(self.invoke(self.flags(binding=False)), 1)
        self.assertEqual(self.served, [])
        self.provider.assert_not_called()

    def test_raw_options_cannot_bypass_schema2_binding(self):
        self.f.router()
        self.assertEqual(self.invoke(self.flags(binding=False)), 1)
        self.provider.assert_not_called()
        self.assertEqual(self.apps, [])

    def test_removing_schema1_denial_does_not_supply_a_router_binding(self):
        state = json.loads(self.marker.read_bytes())
        state['unconfigured'].remove('router')
        self.marker.write_text(json.dumps(state))
        self.assertEqual(self.invoke(self.flags(binding=False)), 1)
        self.provider.assert_not_called()

    def test_explicit_legacy_raw_mode_without_a_bootstrap_marker_is_retained(self):
        self.marker.unlink()
        self.assertEqual(self.invoke(self.flags(binding=False)), 0)
        args, app = self.apps[0]
        self.assertEqual(args, {'socket_path': self.f.socket,
            'state_directory': self.f.prefix / 'receipts', 'models': ['raw-router']})
        self.assertIsNone(self.served[0][0].backend_state_sha256)
        self.assertEqual(app.receipts.close.call_count, 1)

    def test_mixed_raw_and_binding_mode_rejects_before_provider_setup(self):
        self.assertEqual(self.invoke(self.flags() + ['--laf-router-model', 'other-model']), 1)
        self.provider.assert_not_called()
        self.assertEqual(self.served, [])

    def test_binding_mode_requires_router_activation(self):
        self.assertEqual(self.invoke(self.flags(router=False) + ['--laf-bootstrap-bindings']), 1)
        self.provider.assert_not_called()

    def test_router_mode_requires_explicit_shared_surface(self):
        flags = self.flags()
        flags.remove('--allow-existing-tools')
        self.assertEqual(self.invoke(flags), 1)
        self.provider.assert_not_called()

    def test_binding_mode_rejects_stdio_transport(self):
        flags = self.flags()
        flags[flags.index('http')] = 'stdio'
        self.assertEqual(self.invoke(flags), 1)
        self.provider.assert_not_called()

    def test_missing_explicit_state_cannot_be_discovered_or_synthesized(self):
        self.marker.unlink()
        self.assertEqual(self.invoke(), 1)
        self.provider.assert_not_called()
        self.assertEqual(self.apps, [])

    def test_unsafe_state_refuses_before_credentials_or_endpoint(self):
        self.marker.chmod(0o644)
        self.assertEqual(self.invoke(), 1)
        self.provider.assert_not_called()
        self.assertEqual(self.apps, [])

    def test_startup_drift_before_legacy_construction_refuses(self):
        original = legacy_tool_adapter.Adapter

        def changed(*args, **kwargs):
            self.mutate()
            return original(*args, **kwargs)

        with patch.object(legacy_tool_adapter, 'Adapter', side_effect=changed):
            self.assertEqual(self.invoke(), 1)
        self.provider.assert_not_called()
        self.assertEqual(self.apps, [])

    def test_startup_drift_after_router_creation_closes_receipts_and_refuses(self):
        self.f.router()
        self.router_hook = lambda app: self.mutate()
        self.assertEqual(self.invoke(), 1)
        self.assertEqual(self.served, [])
        self.assertEqual(self.apps[0][1].receipts.close.call_count, 1)

    def test_runtime_binding_drift_stops_shared_calls_before_router(self):
        self.f.router()

        def check(adapter):
            self.mutate()
            with self.assertRaises(ValueError):
                adapter.call_tool('laf_router_status', {}, 1, request_principal=self.owner)
            self.apps[0][1].call.assert_not_called()

        self.callback = check
        self.assertEqual(self.invoke(), 0)

    def test_runtime_binding_drift_stops_events_before_verifier_or_delivery(self):
        def check(adapter):
            self.mutate()
            adapter.initialized = adapter.ready = True
            result = adapter.handle({'jsonrpc': '2.0', 'id': 1,
                'method': 'events/subscribe', 'params': {}}, principal=self.owner)
            self.assertEqual(result['error']['code'], -32603)
            self.events.handle.assert_not_called()

        self.callback = check
        self.assertEqual(self.invoke(), 0)

    def test_http_codex_without_router_also_pins_existing_schema2_state(self):
        self.f.codex()
        self.f.router()

        def check(adapter):
            adapter.call_tool('codex_capabilities', {}, 1, request_principal=self.owner)
            self.assertEqual(self.pipeline.dispatch.call_count, 1)
            self.pipeline.dispatch.reset_mock()
            self.mutate()
            with self.assertRaises(ValueError):
                adapter.call_tool('codex_capabilities', {}, 1, request_principal=self.owner)
            self.pipeline.dispatch.assert_not_called()

        self.callback = check
        self.assertEqual(self.invoke(self.flags(router=False)), 0)

    def test_wrong_oauth_principal_cannot_call_the_shared_router(self):
        self.f.router()

        def check(adapter):
            with self.assertRaises(facade.Invalid):
                adapter.call_tool('laf_router_status', {}, 1, request_principal=self.other)
            self.apps[0][1].call.assert_not_called()

        self.callback = check
        self.assertEqual(self.invoke(), 0)

    def test_shared_mode_does_not_broaden_a_multiple_principal_allowlist(self):
        self.principals = (self.owner, self.other)
        self.assertEqual(self.invoke(), 1)
        self.provider.assert_not_called()
        self.assertEqual(self.apps, [])

    def test_oauth_cannot_use_explicit_anchor_override_as_local_fallback(self):
        self.assertEqual(self.invoke(self.flags() + ['--runtime-project-root', str(self.project)]), 1)
        self.provider.assert_not_called()

    def test_unavailable_notifications_keep_oauth_frontend_closed(self):
        disabled = SimpleNamespace(enabled=False, runtime_root=self.runtime)
        self.assertEqual(self.invoke(notifications=disabled), 1)
        self.assertEqual(self.served, [])
        self.assertEqual(self.apps, [])

    def test_denied_router_constructor_failure_keeps_cleanup_bounded(self):
        # No receipts attribute exists on the denied application.
        self.assertEqual(self.invoke(composite=ValueError('INJECTED_COMPOSITION_FAILURE')), 1)
        self.assertEqual(self.apps, [])
        self.assertEqual(self.served, [])

    def test_bound_router_rejects_replaced_socket_before_connect(self):
        self.f.router()
        raw = self.marker.read_bytes()
        binding = json.loads(raw)['router']
        application = shared.build_router_application(socket_path=binding['socket'],
            state_directory=binding['receipts'], models=binding['models'],
            socket_identity=binding['socket_identity'], binding_runtime=self.runtime,
            binding_sha256=hashlib.sha256(raw).hexdigest())
        self.addCleanup(application.receipts.close)
        real = os.lstat

        def socket_metadata(path, *args, **kwargs):
            value = real(path, *args, **kwargs)
            if Path(path) == self.f.socket:
                fields = list(value)
                fields[0] = stat.S_IFSOCK | stat.S_IMODE(value.st_mode)
                return os.stat_result(fields)
            return value

        with patch('os.lstat', side_effect=socket_metadata):
            self.assertEqual(list(application.router._check_identity()), binding['socket_identity'])
            self.f.socket.rename(self.f.socket.with_name('preserved-original'))
            self.f.socket.write_bytes(b'replacement-endpoint')
            self.f.socket.chmod(0o600)
            with self.assertRaises(ValueError):
                application.router._check_identity()

    def test_bound_router_rechecks_binding_before_socket_identity(self):
        self.f.router()
        raw = self.marker.read_bytes()
        binding = json.loads(raw)['router']
        application = shared.build_router_application(socket_path=binding['socket'],
            state_directory=binding['receipts'], models=binding['models'],
            socket_identity=binding['socket_identity'], binding_runtime=self.runtime,
            binding_sha256=hashlib.sha256(raw).hexdigest())
        self.addCleanup(application.receipts.close)
        self.mutate()
        # A nonsocket error must not masquerade as the binding-change fence.
        with patch('laf_mcp.router.Router._check_identity',
                   side_effect=AssertionError('Socket metadata must not be reached')) as identity:
            with self.assertRaises(ValueError):
                application.router._check_identity()
            identity.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
