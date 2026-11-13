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

# Added 2026-11-13: ordinary-pool status evidence boundary regressions.

"""Fake daemon only: status must reflect observed capacity, never fabricate2."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/private_codex/src'))
import legacy_tool_adapter as legacy
import mcp_adapter as http
TASK='00000000-0000-4000-8000-000000000001'
class SessionStatusBoundary(unittest.TestCase):
    def call(self,module,raw):
        adapter=object.__new__(module.Adapter)
        adapter.tools=module.tool_definitions()
        adapter.shared_tool_names=set()
        adapter.audit=SimpleNamespace(tell=lambda:0)
        adapter.record=lambda *args,**kwargs:None
        requests=[]
        def daemon(method,arguments):
            requests.append((method,copy.deepcopy(arguments)))
            return copy.deepcopy(raw)
        adapter.daemon=daemon
        result=adapter.call_tool('orchestrator_status',{},1)
        self.assertFalse(result['isError']);self.assertEqual(requests,[('status',{})])
        return result['structuredContent']['data']
    def test_real_capacity_is_not_hardcoded_to_two(self):
        for module in (legacy,http):
            for maximum in (1,2,8):
                with self.subTest(module=module.__name__,maximum=maximum):
                    self.assertEqual(self.call(module,{'maximum_sessions':maximum})['maximum_sessions'],maximum)
    def test_missing_capacity_remains_unknown(self):
        for module in (legacy,http):
            self.assertIsNone(self.call(module,{})['maximum_sessions'])
    def test_invalid_capacity_is_not_promoted_or_treated_as_bool(self):
        for module in (legacy,http):
            for value in (None,True,False,0,-1,9,'8',[],{}):
                with self.subTest(module=module.__name__,value=value):
                    self.assertIsNone(self.call(module,{'maximum_sessions':value})['maximum_sessions'])
    def test_status_read_preserves_active_jobs_and_redacts_resource_settings(self):
        raw={'maximum_sessions':8,'active_tasks':[TASK,'not-an-id'],'paused':False,'stopping':False,
             'resource_policy':{'memory_bytes':123456},'worker_resource_policy':{'pids':32}}
        for module in (legacy,http):
            data=self.call(module,raw)
            self.assertEqual(data['active_task_ids'],[TASK]);self.assertFalse(data['stopping'])
            self.assertNotIn('resource_policy',data);self.assertNotIn('worker_resource_policy',data)
        self.assertEqual(raw['active_tasks'],[TASK,'not-an-id'])
if __name__=='__main__':unittest.main(verbosity=2)
