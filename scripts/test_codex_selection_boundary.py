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

# Added 2026-11-13: explicit Codex model/effort MCP boundary regressions.

"""MCP selection boundaries only; fake backend, no account, child or model call."""
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime/private_codex/src'))
import legacy_tool_adapter as legacy
import mcp_adapter as http

THREAD = '00000000-0000-4000-8000-000000000001'

class SelectionBoundary(unittest.TestCase):
    modules = (legacy, http)

    def adapter(self, module, data=None):
        adapter = object.__new__(module.Adapter)
        # This deliberately constructor-free fixture represents legacy configured state.
        adapter.unconfigured = frozenset()
        adapter.tools = module.tool_definitions(codex_pipeline=True)
        adapter.shared_tool_names = set()
        adapter.events_enabled = False
        adapter.notification_runtime = None
        adapter.principal_provider = None
        adapter.audit = SimpleNamespace(tell=lambda: 0)
        self.calls = []
        self.records = []
        result = {'status': 'STORED'} if data is None else data
        def dispatch(name, arguments):
            self.calls.append((name, copy.deepcopy(arguments)))
            return copy.deepcopy(result)
        adapter.pipeline = SimpleNamespace(dispatch=dispatch)
        adapter.record = lambda event, **facts: self.records.append((event, facts))
        return adapter

    def inputs(self):
        return {
            'codex_thread_start': {'workspace': 'development', 'model': 'provider-model-7', 'request_key': 'start-1'},
            'codex_chat_store': {'thread_id': THREAD, 'prompt': 'Synthetic input.', 'request_key': 'store-1'},
            'codex_task_submit': {'thread_id': THREAD, 'prompt': 'Synthetic input.', 'request_key': 'submit-1'},
        }

    def test_selection_schema_has_optional_non_enum_effort(self):
        for module in self.modules:
            definitions = {t['name']: t for t in module.tool_definitions(codex_pipeline=True)}
            for name in self.inputs():
                with self.subTest(module=module.__name__, tool=name):
                    schema = definitions[name]['inputSchema']
                    self.assertIn('reasoning_effort', schema['properties'])
                    self.assertEqual(schema['properties']['reasoning_effort']['type'], 'string')
                    self.assertNotIn('enum', schema['properties']['reasoning_effort'])
                    self.assertNotIn('reasoning_effort', schema['required'])
                    self.assertIs(schema['additionalProperties'], False)

    def test_explicit_provider_effort_is_forwarded_without_alias_mapping(self):
        for module in self.modules:
            for name, arguments in self.inputs().items():
                with self.subTest(module=module.__name__, tool=name):
                    adapter = self.adapter(module)
                    arguments['reasoning_effort'] = 'provider-effort-v7'
                    result = adapter.call_tool(name, arguments, 1)
                    self.assertFalse(result['isError'])
                    self.assertEqual(self.calls, [(name, arguments)])
                    self.assertEqual(result['structuredContent']['data'], {'status': 'STORED'})
                    self.assertNotIn('provider-effort-v7', repr(self.records))
                    self.assertNotIn('Synthetic input.', repr(self.records))

    def test_omission_is_preserved_for_backend_default_resolution(self):
        for module in self.modules:
            for name, arguments in self.inputs().items():
                adapter = self.adapter(module)
                adapter.call_tool(name, arguments, 2)
                self.assertEqual(self.calls, [(name, arguments)])
                self.assertNotIn('reasoning_effort', self.calls[0][1])

    def test_raw_effort_and_arbitrary_config_are_rejected_before_dispatch(self):
        for module in self.modules:
            for field, value in (('effort', 'high'), ('config', {'model_reasoning_effort': 'high'})):
                for name, arguments in self.inputs().items():
                    adapter = self.adapter(module)
                    with self.assertRaises(module.Invalid):
                        adapter.call_tool(name, {**arguments, field: value}, 3)
                    self.assertEqual(self.calls, [])

    def test_invalid_effort_values_are_not_sent_to_backend(self):
        for module in self.modules:
            for value in (None, True, 1, [], '', ' ', 'line\nbreak', 'x' * 161):
                adapter = self.adapter(module)
                arguments = self.inputs()['codex_thread_start']
                with self.subTest(module=module.__name__, value=value):
                    with self.assertRaises(module.Invalid):
                        adapter.call_tool('codex_thread_start', {**arguments, 'reasoning_effort': value}, 4)
                    self.assertEqual(self.calls, [])

    def test_active_steer_cannot_change_model_or_effort(self):
        for module in self.modules:
            for field in ('model', 'reasoning_effort'):
                adapter = self.adapter(module)
                arguments = {'thread_id': THREAD, 'prompt': 'Steer text.', 'request_key': 'steer-1', field: 'new'}
                with self.assertRaises(module.Invalid):
                    adapter.call_tool('codex_chat_steer', arguments, 5)
                self.assertEqual(self.calls, [])

    def test_catalog_unknown_is_not_promoted_to_available(self):
        data = {'catalog': {'status': 'unknown', 'source': 'model/list', 'code': 'CATALOG_UNAVAILABLE'},
                'models': [{'model': 'provider-model-7', 'available': False, 'availability': 'unknown'}],
                'model_execution_verified': False, 'billing_verified': False}
        for module in self.modules:
            adapter = self.adapter(module, data)
            result = adapter.call_tool('codex_capabilities', {}, 6)
            self.assertEqual(result['structuredContent']['data'], data)
            self.assertEqual(self.calls, [('codex_capabilities', {})])

    def test_model_catalog_page_has_readonly_hint_and_exact_forwarding(self):
        cursor = 'a' * 64 + ':20'
        for module in self.modules:
            adapter = self.adapter(module, {'models': [], 'catalog': {'status': 'unknown'}})
            tool = next(t for t in adapter.tools if t['name'] == 'codex_models_list')
            self.assertTrue(tool['annotations']['readOnlyHint'])
            self.assertFalse(tool['annotations']['destructiveHint'])
            arguments = {'limit': 20, 'cursor': cursor}
            adapter.call_tool('codex_models_list', arguments, 7)
            self.assertEqual(self.calls, [('codex_models_list', arguments)])

    def test_catalog_invalid_page_never_dispatches(self):
        for module in self.modules:
            for arguments in ({'limit': 0}, {'limit': 21}, {'limit': True}, {'cursor': 'bad'}, {'refresh_auth': True}):
                adapter = self.adapter(module)
                with self.assertRaises(module.Invalid):
                    adapter.call_tool('codex_models_list', arguments, 8)
                self.assertEqual(self.calls, [])

    def test_catalog_gate_stays_explicitly_disabled_without_pipeline(self):
        for module in self.modules:
            adapter = self.adapter(module)
            adapter.tools = module.tool_definitions(codex_pipeline=False)
            with self.assertRaises(module.Invalid):
                adapter.call_tool('codex_models_list', {}, 9)
            self.assertEqual(self.calls, [])

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SelectionBoundary)
    ids = [test.id() for test in suite]
    if len(ids) != 10 or len(set(ids)) != 10:
        raise SystemExit('Expected ten unique boundary cases')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    abnormal = (result.failures, result.errors, result.skipped, result.expectedFailures, result.unexpectedSuccesses)
    raise SystemExit(0 if result.testsRun == 10 and result.wasSuccessful() and not any(abnormal) else 1)
