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

# Added 2026-11-13: selected canonical model-catalog/effort tests adapted to
# LAF OAuth ownership, isolated temporary runtime anchors and no-effect fakes.
# Project-authored canonical feature-only adaptation; see SOURCE_ORIGIN.md.

"""Provider catalog, immutable selection and full-wire regressions, entirely offline.

No app-server, SDK child, socket bind/connect or model execution is permitted.
Unicode escapes/chr fixtures deliberately exercise UTF-8 byte limits and JSON
escaping; they are provider data, never public prose or native model aliases.
"""
import base64
import copy
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'runtime/private_codex/src'))
from codex_app_protocol import (ProtocolClient, ProtocolError, ProtocolUnknown,
    _FIELDS, METHOD_SCHEMAS, schema_version_supported)
from codex_pipeline import Pipeline, PipelineError, build_pipeline, validate_arguments, digest
from codex_host import HostGateway, DaemonPipelineProxy
from concurrency import lease
from daemon import Daemon
from local_principal import OAuthPrincipal
from mcp_adapter import Adapter, tool_definitions
from notification_outbox import NotificationOutbox, EVENT_NAMES

# Guard the whole module, including cleanup: accidental external effects fail.
def setUpModule():
    global EFFECT_GUARDS
    EFFECT_GUARDS = [patch(name, side_effect=AssertionError('Offline feature tests forbid this effect'))
        for name in ('subprocess.Popen', 'socket.socket.bind', 'socket.socket.connect')]
    for guard in EFFECT_GUARDS: guard.start()

def tearDownModule():
    for guard in reversed(EFFECT_GUARDS): guard.stop()

def oauth_principal():
    # Synthetic verified identity; the pure HTTP endpoint uses a fake verifier.
    return OAuthPrincipal('https://issuer.example.invalid', 'catalog-test-owner')

def runtime_fixture(case, prefix):
    case.temp = tempfile.TemporaryDirectory(prefix=prefix)
    case.project = Path(case.temp.name) / 'project'
    (case.project / 'src').mkdir(parents=True)
    (case.project / 'src/daemon.py').write_text('"""Inert runtime-anchor fixture, never executed."""\n')
    (case.project / '.runtime').mkdir(mode=0o700)
    case.root = case.project / '.runtime/case'
    case.root.mkdir(mode=0o700)
    (case.root / 'logs').mkdir(mode=0o700)

INITIALIZE = {"codexHome": "/offline/codex", "platformFamily": "unix",
              "platformOs": "linux", "userAgent": "codex/0.165.0"}
MODEL = {"id": "offline-model", "model": "offline-model", "displayName": "Fixture",
         "description": "Not a live model", "hidden": False, "isDefault": True,
         "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [
             {"reasoningEffort": "medium", "description": "Fixture default"},
             {"reasoningEffort": "high", "description": "Fixture high"}]}
THREAD = {"id": "thread-1", "cliVersion": "0.165.0", "createdAt": 0, "updatedAt": 0,
          "cwd": "/offline", "ephemeral": False, "modelProvider": "openai", "preview": "",
          "projectId": None, "sessionId": "session-1", "source": "appServer",
          "status": {"type": "idle"}, "turns": []}
TURN = {"id": "turn-1", "items": [], "status": "inProgress"}
THREAD_RESULT = {"thread": THREAD, "approvalPolicy": "on-request", "approvalsReviewer": "user",
                 "cwd": "/offline", "model": "offline-model", "modelProvider": "openai",
                 "sandbox": {"type": "readOnly"}}
RESULTS = {"initialize": INITIALIZE, "model/list": {"data": [MODEL], "nextCursor": None},
           "account/read": {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": True},
           "thread/start": THREAD_RESULT, "thread/resume": THREAD_RESULT,
           "thread/read": {"thread": THREAD}, "thread/list": {"data": [THREAD]},
           "thread/archive": {}, "turn/start": {"turn": TURN},
           "turn/steer": {"turnId": "turn-1"}, "turn/interrupt": {}}


class ProtocolPeer:
    def __init__(self):
        self.calls = []
        self.answer = None

    def __call__(self, message, timeout_s):
        self.calls.append((message, timeout_s))
        if message["method"] == "initialized":
            return None
        if self.answer:
            return self.answer(message)
        return {"id": message["id"], "result": RESULTS[message["method"]]}

class ProtocolFixture:
    def client(self, **kwargs):
        peer = ProtocolPeer()
        client = ProtocolClient(peer, **kwargs)
        client.initialize()
        return client, peer

class ReasoningCatalogProtocolTests(unittest.TestCase):
    client = ProtocolFixture.client

    def test_only_fixed_reasoning_config_shape_is_transportable(self):
        client,peer = self.client()
        for method,args in [('thread/start',{}),('thread/resume',{'threadId':'thread-1'})]:
            client.request(method,{**args,'config':{'model_reasoning_effort':'provider custom/value'}})
            sent=peer.calls[-1][0]['params']
            self.assertEqual(sent['config'],{'model_reasoning_effort':'provider custom/value'})
            before=len(peer.calls)
            for config in [None,{}, {'model_reasoning_effort':None},
                {'model_reasoning_effort':'medium','model_provider':'other'},
                {'model_reasoning_effort':'bad\nvalue'}, {'model_reasoning_effort':'x'*65}]:
                with self.subTest(config=config),self.assertRaises(ProtocolError):
                    client.request(method,{**args,'config':config})
            self.assertEqual(len(peer.calls),before)

    def test_turn_effort_is_exact_raw_provider_string_and_not_reasoning_effort_key(self):
        client,peer=self.client()
        body={'threadId':'thread-1','input':[{'type':'text','text':'Fixture'}]}
        client.request('turn/start',{**body,'effort':'provider custom/value'})
        self.assertEqual(peer.calls[-1][0]['params']['effort'],'provider custom/value')
        before=len(peer.calls)
        for value in ['',None,True,'bad\nvalue','x'*65]:
            with self.subTest(value=value),self.assertRaises(ProtocolError):
                client.request('turn/start',{**body,'effort':value})
        with self.assertRaises(ProtocolError):client.request('turn/start',{**body,'reasoning_effort':'medium'})
        self.assertEqual(len(peer.calls),before)

    def test_catalog_rejects_malformed_entries_options_bounds_and_page_duplicates(self):
        import copy
        for change in ['missing','option-shape','default','empty-options','duplicate-option',
                       'option-limit','effort-control','display-bound','duplicate-model','page-bound','cursor-bound']:
            with self.subTest(change=change):
                client,peer=self.client()
                row=copy.deepcopy(MODEL);page={'data':[row],'nextCursor':None}
                if change=='missing':row.pop('defaultReasoningEffort')
                if change=='option-shape':row['supportedReasoningEfforts'][0].pop('description')
                if change=='default':row['defaultReasoningEffort']='not advertised'
                if change=='empty-options':row['supportedReasoningEfforts']=[]
                if change=='duplicate-option':row['supportedReasoningEfforts'].append(copy.deepcopy(row['supportedReasoningEfforts'][0]))
                if change=='option-limit':row['supportedReasoningEfforts']=[{'reasoningEffort':str(k),'description':''} for k in range(17)]
                if change=='effort-control':row['supportedReasoningEfforts'][0]['reasoningEffort']='bad\nvalue'
                if change=='display-bound':row['displayName']='x'*161
                if change=='duplicate-model':page['data'].append(copy.deepcopy(row))
                if change=='page-bound':page['data']=[row]*101
                if change=='cursor-bound':page['nextCursor']='x'*513
                peer.answer=lambda message:{'id':message['id'],'result':page}
                with self.assertRaises(ProtocolUnknown):client.model_catalog()

    def test_thread_effort_echo_accepts_missing_null_or_string_but_rejects_malformed(self):
        import copy
        for method in ['thread/start','thread/resume']:
            for echo in [None,'medium',True,{},'bad\nvalue','x'*65]:
                with self.subTest(method=method,echo=echo):
                    client,peer=self.client()
                    result=copy.deepcopy(THREAD_RESULT);result['reasoningEffort']=echo
                    peer.answer=lambda message:{'id':message['id'],'result':result}
                    if echo is None or echo=='medium':
                        self.assertEqual(client.request(method,{'threadId':'thread-1'} if method=='thread/resume' else {})['reasoningEffort'],echo)
                    else:
                        with self.assertRaises(ProtocolUnknown):client.request(method,{'threadId':'thread-1'} if method=='thread/resume' else {})

class Peer:
    def __init__(self, workspace):
        self.workspace, self.calls, self.threads = workspace, [], {}
        self.failure, self.account, self.scope = {}, 'chatgpt', 'readOnly'
        self.models = [copy.deepcopy(MODEL)]
        self.effort_echo = 'requested'
        self.client = ProtocolClient(self)
        self.client.initialize()

    def __call__(self, request, timeout_s):
        method, params = request['method'], request.get('params', {})
        self.calls.append((method, copy.deepcopy(params)))
        if method == 'initialized': return None
        if method in self.failure:
            outcome = self.failure[method]
            if isinstance(outcome, Exception): raise outcome
            return {'id': request['id'], 'error': {'code': -32000, 'message': 'private refusal'}}
        if method == 'initialize': result = INITIALIZE
        elif method == 'account/read': result = {'requiresOpenaiAuth': True, 'account': {'type': self.account, 'email': 'never-export@example.invalid'}}
        elif method == 'model/list': result = {'data': copy.deepcopy(self.models), 'nextCursor': None}
        elif method in ('thread/start', 'thread/resume'):
            ident = params.get('threadId', 'remote-' + str(len(self.threads)))
            self.threads.setdefault(ident, {'id': ident, 'turns': [], 'status': {'type': 'idle'}})
            result = {'thread': self.threads[ident], 'model': params['model'], 'cwd': self.workspace,
                      'modelProvider': 'openai', 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user', 'sandbox': {'type': self.scope}}
            if self.effort_echo == 'requested':
                result['reasoningEffort'] = params.get('config', {}).get('model_reasoning_effort')
            elif self.effort_echo != 'absent': result['reasoningEffort'] = self.effort_echo
        elif method == 'turn/start':
            thread = self.threads[params['threadId']]
            turn = {'id': 'turn-' + str(len(thread['turns'])), 'status': 'inProgress', 'items': []}
            thread['turns'].append(turn)
            thread['status'] = {'type': 'active', 'activeFlags': []}
            result = {'turn': copy.deepcopy(turn)}
        elif method == 'thread/read': result = {'thread': copy.deepcopy(self.threads[params['threadId']])}
        elif method == 'turn/steer': result = {'turnId': params['expectedTurnId']}
        else: result = {}
        return {'id': request['id'], 'result': result}

    def count(self, method): return sum(m == method for m, _ in self.calls)

class PipelineTests(unittest.TestCase):
    def setUp(self):
        runtime_fixture(self, 'laf-catalog-pipeline-')
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.policy = {'schema': 1, 'workspaces': {'fixture': str(self.workspace)}, 'models': ['offline-model'],
                       'max_threads': 4, 'max_tasks': 16, 'concurrency': 1, 'timeout_seconds': 120}
        self.now = 1000
        self.peer = Peer(str(self.workspace))
        self.pipeline = self.new_pipeline()

    def tearDown(self): self.temp.cleanup()

    def new_pipeline(self):
        return Pipeline(self.root, self.policy, self.peer.client, instructions='Fixed common policy. No nested isolation.',
                        policy_sha256='a' * 64, clock=lambda: self.now)

    def call(self, name, **args): return self.pipeline.dispatch('codex_' + name, args)

    def start(self, key='start'):
        return self.call('thread_start', workspace='fixture', model='offline-model', request_key=key)['thread_id']

    def submit(self, thread, key='submit'):
        return self.call('task_submit', thread_id=thread, prompt='Synthetic public input', request_key=key)

    def complete(self, thread, status='completed', answer='Synthetic answer', notify=True):
        remote = self.pipeline._mutate(lambda s: s['threads'][thread]['remote_id'])
        turn = self.peer.threads[remote]['turns'][-1]
        turn.update(status=status, items=[{'type': 'agentMessage', 'id': 'answer', 'text': answer}])
        self.peer.threads[remote]['status'] = {'type': 'idle'}
        if notify: self.peer.client.receive({'method': 'turn/completed', 'params': {'threadId': remote, 'turn': copy.deepcopy(turn)}})

    def test_models_list_has_same_authenticated_metadata_prerequisite_as_capabilities(self):
        from local_principal import OAuthPrincipal
        from types import SimpleNamespace
        adapter = Adapter(self.root,allow_codex_pipeline=True,pipeline=self.pipeline, runtime_project_root=self.project)
        self.addCleanup(adapter.audit.close)
        principal = oauth_principal()
        before = len(self.peer.calls)
        for tool in ['codex_capabilities','codex_models_list']:
            result = adapter.call_tool(tool,{},1,request_principal=principal)
            self.assertTrue(result['isError'])
            self.assertEqual(result['structuredContent']['data']['code'],'NOT_AUTHORIZED')
        self.assertEqual(len(self.peer.calls),before)
        # Once the exact existing prerequisite is satisfied, metadata remains
        # read-only and introduces no task/thread ownership import.
        adapter.events_enabled = True
        adapter.notification_runtime = SimpleNamespace(outbox=object())
        result = adapter.call_tool('codex_models_list',{},2,request_principal=principal)
        self.assertFalse(result['isError'])
        self.assertEqual(result['structuredContent']['data']['catalog']['status'],'observed')
        self.assertEqual(self.peer.count('turn/start'),0)

    def test_build_uses_shared_inspected_version_gate_for_0167_without_generation(self):
        path = self.root/'codex-pipeline-policy.json'
        path.write_text(json.dumps(self.policy));path.chmod(0o600)
        peer=self.peer
        class Transport:
            def __init__(self):self.closed=False
            def __call__(self,request,timeout_s):return peer(request,timeout_s)
            def bind_client(self,client):self.client=client
            def start(self):pass
            def close(self):self.closed=True
        transport=Transport()
        bundle=type('Bundle',(),{'sha256':'a'*64})()
        with patch('common_policy.load_project_policy',return_value=bundle), \
                patch('common_policy.developer_instructions',return_value='Fixed policy'), \
                patch.dict(INITIALIZE,{'userAgent':'codex_work_desktop/0.167.0 (Linux)'}):
            pipeline=build_pipeline(self.root,self.project,transport_factory=lambda:transport)
        self.addCleanup(transport.close)
        self.assertEqual(pipeline.capabilities()['catalog']['status'],'observed')
        self.assertEqual(self.peer.count('turn/start'),0)

class CatalogEffortTests(unittest.TestCase):
    setUp = PipelineTests.setUp
    tearDown = PipelineTests.tearDown
    new_pipeline = PipelineTests.new_pipeline
    call = PipelineTests.call
    start = PipelineTests.start
    submit = PipelineTests.submit
    complete = PipelineTests.complete

    def test_fresh_catalog_projects_selection_metadata_without_descriptions(self):
        extra = copy.deepcopy(MODEL)
        extra.update(id='other-model', model='other-model', displayName='Other', isDefault=False)
        self.peer.models.append(extra)
        result = self.call('capabilities')
        row = result['models'][0]
        self.assertEqual(row['availability'], 'observed')
        self.assertEqual(row['default_reasoning_effort'], 'medium')
        self.assertEqual(row['supported_reasoning_efforts'], ['medium', 'high'])
        self.assertEqual(row['display_name'], 'Fixture')
        self.assertFalse(result['model_execution_verified'])
        other = result['provider_catalog']['models'][1]
        self.assertFalse(other['operator_allowed'])
        self.assertFalse(other['selectable'])
        self.assertNotIn('description', json.dumps(result))
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_hidden_absent_and_discovery_failure_do_not_claim_availability(self):
        self.peer.models[0]['hidden'] = True
        hidden = self.call('capabilities')['models'][0]
        self.assertFalse(hidden['available'])
        self.assertEqual(hidden['availability'], 'unavailable')
        self.peer.models = []
        self.assertFalse(self.call('capabilities')['models'][0]['available'])
        self.peer.failure['model/list'] = TimeoutError('private authentication detail')
        result = self.call('capabilities')
        self.assertEqual(result['catalog']['status'], 'unknown')
        self.assertEqual(result['models'][0]['availability'], 'unknown')
        self.assertFalse(result['models'][0]['available'])
        self.assertNotIn('private authentication', json.dumps(result))

    def test_malformed_default_and_duplicate_models_fail_closed(self):
        for change in ('effort', 'default', 'duplicate', 'label', 'id'):
            with self.subTest(change=change):
                self.peer.models = [copy.deepcopy(MODEL)]
                row = self.peer.models[0]
                if change == 'effort': row['supportedReasoningEfforts'].append(copy.deepcopy(row['supportedReasoningEfforts'][0]))
                if change == 'default': row['defaultReasoningEffort'] = 'not-advertised'
                if change == 'duplicate': self.peer.models.append(copy.deepcopy(row))
                if change == 'label': row['displayName'] = 'bad\nlabel'
                if change == 'id': row['id'] = '../bad'
                result = self.call('capabilities')
                self.assertEqual(result['catalog']['status'], 'unknown')
                self.assertFalse(result['models'][0]['available'])
        self.assertEqual(self.peer.count('thread/start'), 0)

    def test_explicit_and_omitted_effort_are_persisted_and_transported(self):
        thread = self.call('thread_start', workspace='fixture', model='offline-model',
            reasoning_effort='high', request_key='start')['thread_id']
        task = self.call('task_submit', thread_id=thread, prompt='Synthetic public input', request_key='task')
        self.assertEqual(task['reasoning_effort'], 'medium')
        self.assertEqual(task['reasoning_effort_source'], 'provider_default')
        self.assertIsNone(task['requested_reasoning_effort'])
        start = next(p for m, p in self.peer.calls if m == 'thread/start')
        turn = next(p for m, p in self.peer.calls if m == 'turn/start')
        self.assertEqual(start['config'], {'model_reasoning_effort': 'high'})
        self.assertEqual(turn['effort'], 'medium')
        self.assertEqual(task['reasoning_effort_evidence']['turn_transport'], 'acknowledged')
        self.assertFalse(task['reasoning_effort_evidence']['actual_execution_verified'])
        self.assertEqual(task['reasoning_effort_evidence']['turn_provider_echo'], 'not_available')

    def test_explicit_unsupported_effort_rejected_before_generation(self):
        with self.assertRaisesRegex(PipelineError, 'REASONING_EFFORT_NOT_SUPPORTED'):
            self.call('thread_start', workspace='fixture', model='offline-model', reasoning_effort='max', request_key='bad')
        thread = self.start()
        result = self.call('task_submit', thread_id=thread, prompt='No turn', reasoning_effort='max', request_key='bad-task')
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertEqual(result['reason_code'], 'REASONING_EFFORT_NOT_SUPPORTED')
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_retry_precedes_discovery_and_retains_original_default_and_fingerprint(self):
        thread = self.start()
        task = self.call('chat_store', thread_id=thread, prompt='Fixed input', request_key='store')
        original = self.pipeline._mutate(lambda s: copy.deepcopy(s['ops']['store']))
        self.peer.models[0]['defaultReasoningEffort'] = 'high'
        duplicate = self.call('chat_store', thread_id=thread, prompt='Fixed input', request_key='store')
        self.assertEqual(duplicate['reasoning_effort'], 'medium')
        before = len(self.peer.calls)
        self.peer.failure['model/list'] = TimeoutError()
        self.assertEqual(self.start(), thread)
        self.assertEqual(self.call('chat_store', thread_id=thread, prompt='Fixed input', request_key='store')['id'], task['id'])
        self.assertEqual(len(self.peer.calls), before)
        self.assertEqual(self.pipeline._mutate(lambda s:s['ops']['store']), original)
        with self.assertRaisesRegex(PipelineError, 'REQUEST_KEY_CONFLICT'):
            self.call('chat_store', thread_id=thread, prompt='Fixed input', reasoning_effort='medium', request_key='store')

    def test_queued_effort_stays_fixed_when_provider_default_changes(self):
        thread = self.start()
        task = self.call('chat_store', thread_id=thread, prompt='Fixed', request_key='task')
        self.peer.models[0]['defaultReasoningEffort'] = 'high'
        self.call('chat_queue', task_id=task['id'], request_key='queue')
        self.call('pipeline_tick')
        turn = next(p for m, p in self.peer.calls if m == 'turn/start')
        self.assertEqual(turn['effort'], 'medium')

    def test_changed_supported_effort_blocks_queued_task(self):
        thread = self.start()
        task = self.call('chat_store', thread_id=thread, prompt='Fixed', request_key='task')
        self.call('chat_queue', task_id=task['id'], request_key='queue')
        self.peer.models[0].update(defaultReasoningEffort='high', supportedReasoningEfforts=[
            {'reasoningEffort':'high', 'description':'Fixture'}])
        self.call('pipeline_tick')
        current = self.call('task_get', task_id=task['id'])
        self.assertEqual(current['state'], 'BLOCKED')
        self.assertEqual(current['reason_code'], 'REASONING_EFFORT_NOT_SUPPORTED')
        self.assertEqual(current['reasoning_effort'], 'medium')
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_legacy_input_is_blocked_without_history_relabel_or_catalogue(self):
        thread = self.start()
        task = self.call('chat_store', thread_id=thread, prompt='Legacy', request_key='task')
        self.call('chat_queue', task_id=task['id'], request_key='queue')
        def legacy(s):
            row = s['tasks'][task['id']]
            for k in list(row):
                if k.startswith(('reasoning_effort', 'requested_reasoning_effort', 'selection_')): del row[k]
            return copy.deepcopy(s['ops'])
        ops = self.pipeline._mutate(legacy)
        before = len(self.peer.calls)
        self.call('pipeline_tick')
        current = self.call('task_get', task_id=task['id'])
        self.assertEqual(current['state'], 'BLOCKED')
        self.assertEqual(current['reason_code'], 'LEGACY_REASONING_EFFORT_MISSING')
        self.assertNotIn('reasoning_effort', current)
        self.assertEqual(self.pipeline._mutate(lambda s:s['ops']), ops)
        self.assertEqual(len(self.peer.calls), before)

    def test_unknown_legacy_execution_keeps_held_slot(self):
        thread = self.start()
        task = self.submit(thread)
        def legacy(s):
            s['tasks'][task['id']].pop('reasoning_effort')
            s['tasks'][task['id']]['state'] = 'UNKNOWN'
        self.pipeline._mutate(legacy)
        self.call('pipeline_tick')
        state = self.pipeline._mutate(lambda s:copy.deepcopy(s))
        self.assertEqual(state['threads'][thread]['active_task'], task['id'])
        self.assertNotIn('reasoning_effort', state['tasks'][task['id']])
        self.assertEqual(self.peer.count('turn/start'), 1)

    def test_thread_effort_echo_absence_is_not_execution_proof(self):
        self.peer.effort_echo = 'absent'
        thread = self.start()
        row = self.pipeline._mutate(lambda s:s['threads'][thread])
        self.assertEqual(row['context_reasoning_effort_evidence']['echo_status'], 'not_reported')
        self.assertFalse(row['context_reasoning_effort_evidence']['actual_execution_verified'])
        self.submit(thread)
        self.assertEqual(self.peer.count('turn/start'), 1)

    def test_effective_thread_echo_mismatch_is_unknown_and_never_runs(self):
        self.peer.effort_echo = 'high'
        result = self.call('thread_start', workspace='fixture', model='offline-model', request_key='bad-echo')
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(self.peer.count('turn/start'), 0)
        self.assertTrue(self.call('thread_start', workspace='fixture', model='offline-model', request_key='bad-echo')['duplicate'])
        self.assertEqual(self.peer.count('thread/start'), 1)

    def test_resume_transports_fixed_effort_and_checks_echo_before_turn(self):
        thread = self.start()
        task = self.call('chat_store', thread_id=thread, prompt='Resume', reasoning_effort='high', request_key='task')
        self.call('chat_queue', task_id=task['id'], request_key='queue')
        self.pipeline.loaded.clear()
        self.peer.effort_echo = 'medium'
        self.call('pipeline_tick')
        resume = next(p for m, p in self.peer.calls if m == 'thread/resume')
        self.assertEqual(resume['config'], {'model_reasoning_effort':'high'})
        self.assertEqual(self.call('task_get', task_id=task['id'])['reason_code'], 'RESUME_REASONING_EFFORT_MISMATCH')
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_provider_catalog_paging_is_bounded_and_unknown_never_selectable(self):
        self.peer.models = []
        for n in range(41):
            row = copy.deepcopy(MODEL)
            row.update(id='model-'+str(n), model='model-'+str(n), isDefault=False)
            self.peer.models.append(row)
        first = self.call('models_list', limit=20)
        second = self.call('models_list', limit=20, cursor=first['next_cursor'])
        third = self.call('models_list', limit=20, cursor=second['next_cursor'])
        self.assertEqual([len(x['models']) for x in [first, second, third]], [20,20,1])
        self.assertIsNone(third['next_cursor'])
        self.assertTrue(all(not row['selectable'] for row in first['models']))
        self.assertLess(len(json.dumps(first).encode()), 64*1024)
        self.peer.models[0]['displayName'] = 'Changed'
        with self.assertRaisesRegex(PipelineError, 'LIST_SNAPSHOT_CHANGED'):
            self.call('models_list', cursor=first['next_cursor'])

    def test_catalog_pagination_rejects_repeated_cursors_and_cross_page_duplicates(self):
        original = self.peer.client.request
        for duplicate in [True,False]:
            with self.subTest(duplicate=duplicate):
                count = 0
                def request(method, params):
                    nonlocal count
                    if method != 'model/list': return original(method, params)
                    count += 1
                    row = copy.deepcopy(MODEL)
                    if not duplicate: row.update(id='row-'+str(count), model='row-'+str(count))
                    return {'data':[row], 'nextCursor':'repeated'}
                with patch.object(self.peer.client, 'request', side_effect=request):
                    result = self.call('capabilities')
                self.assertEqual(result['catalog']['status'], 'unknown')
                self.assertLessEqual(count, 4)
        self.assertEqual(self.peer.count('turn/start'), 0)

class EffortStorageBoundaryTests(unittest.TestCase):
    setUp = PipelineTests.setUp
    tearDown = PipelineTests.tearDown
    new_pipeline = PipelineTests.new_pipeline
    call = PipelineTests.call
    start = PipelineTests.start

    def test_explicit_store_is_offline_and_keeps_intent_without_entitlement_claim(self):
        thread = self.start()
        before = len(self.peer.calls)
        self.peer.failure['account/read'] = TimeoutError('secret transport detail')
        task = self.call('chat_store', thread_id=thread, prompt='Keep intent', reasoning_effort='high', request_key='store')
        self.assertEqual(task['state'], 'STORED')
        self.assertEqual(task['reasoning_effort_source'], 'explicit')
        self.assertIsNone(task['selection_catalog_sha256'])
        self.call('chat_queue', task_id=task['id'], request_key='queue')
        self.assertEqual(len(self.peer.calls), before)
        self.call('pipeline_tick')
        current = self.call('task_get', task_id=task['id'])
        self.assertEqual(current['state'], 'BLOCKED')
        self.assertEqual(current['reason_code'], 'MODEL_CATALOG_UNKNOWN')
        self.assertEqual(current['reasoning_effort'], 'high')
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_new_omission_fails_closed_without_creating_partial_task(self):
        thread = self.start()
        self.peer.failure['model/list'] = TimeoutError('private')
        before = self.pipeline._mutate(lambda s: copy.deepcopy(s))
        with self.assertRaisesRegex(PipelineError, 'MODEL_CATALOG_UNKNOWN'):
            self.call('chat_store', thread_id=thread, prompt='No guessed default', request_key='task')
        self.assertEqual(self.pipeline._mutate(lambda s:s), before)
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_unusual_advertised_effort_is_preserved_not_mapped_to_native_enum(self):
        effort = 'custom strength/v2'
        self.peer.models[0]['supportedReasoningEfforts'].append({'reasoningEffort':effort, 'description':'Fixture'})
        self.peer.models[0]['defaultReasoningEffort'] = effort
        thread = self.start()
        result = self.call('task_submit', thread_id=thread, prompt='Exact raw effort', request_key='task')
        self.assertEqual(result['reasoning_effort'], effort)
        turn = next(p for m, p in self.peer.calls if m == 'turn/start')
        self.assertEqual(turn['effort'], effort)

    def test_changed_immutable_selection_is_blocked_before_discovery(self):
        thread = self.start()
        task = self.call('chat_store', thread_id=thread, prompt='Fixed', request_key='task')
        self.call('chat_queue', task_id=task['id'], request_key='queue')
        self.pipeline._mutate(lambda s:s['tasks'][task['id']].update(reasoning_effort='high'))
        before = len(self.peer.calls)
        self.call('pipeline_tick')
        current = self.call('task_get', task_id=task['id'])
        self.assertEqual(current['reason_code'], 'IMMUTABLE_MODEL_SELECTION_CHANGED')
        self.assertEqual(len(self.peer.calls), before)

    def test_legacy_stored_input_rejected_without_mutating_history(self):
        thread = self.start()
        task = self.call('chat_store', thread_id=thread, prompt='Legacy stored', request_key='task')
        self.pipeline._mutate(lambda s:s['tasks'][task['id']].pop('reasoning_effort'))
        before = self.pipeline._mutate(lambda s:copy.deepcopy(s))
        with self.assertRaisesRegex(PipelineError, 'LEGACY_REASONING_EFFORT_MISSING'):
            self.call('chat_queue', task_id=task['id'], request_key='queue')
        self.assertEqual(self.pipeline._mutate(lambda s:s), before)

    def test_null_effort_echo_is_distinct_from_matched_and_does_not_prove_execution(self):
        self.peer.effort_echo = None
        thread = self.start()
        self.pipeline.loaded.clear()
        task = self.call('task_submit',thread_id=thread,prompt='Null receipt',request_key='task')
        for row in [self.pipeline._mutate(lambda s:s['threads'][thread]),task]:
            evidence = row['context_reasoning_effort_evidence']
            self.assertEqual(evidence['echo_status'],'not_reported')
            self.assertIsNone(evidence['provider_echo'])
            self.assertFalse(evidence['actual_execution_verified'])
        self.assertEqual(task['state'],'RUNNING')

    def test_catalog_four_page_bound_with_no_fifth_read(self):
        original = self.peer.client.request
        pages = 0
        def request(method, params):
            nonlocal pages
            if method != 'model/list': return original(method,params)
            pages += 1
            return {'data': [], 'nextCursor':'page-'+str(pages)}
        with patch.object(self.peer.client, 'request', side_effect=request):
            result = self.call('capabilities')
        self.assertEqual(pages,4)
        self.assertEqual(result['catalog']['code'],'MODEL_CATALOG_BOUND')
        self.assertFalse(result['models'][0]['available'])

    def test_complete_large_projection_stays_under_wire_budget_with_explicit_truncation(self):
        self.peer.models = []
        for n in range(30):
            row = copy.deepcopy(MODEL)
            row.update(id=('model'+str(n)).ljust(160,'x'),model=('model'+str(n)).ljust(160,'x'),
                displayName='\u00e9'*80, isDefault=False)
            row['supportedReasoningEfforts'] = [
                {'reasoningEffort':(str(k)+'\u00e9'*31), 'description':'Fixture'} for k in range(16)]
            row['defaultReasoningEffort'] = row['supportedReasoningEfforts'][0]['reasoningEffort']
            self.peer.models.append(row)
        policy = copy.deepcopy(self.policy)
        policy['models'] = [row['model'] for row in self.peer.models[:8]]
        self.pipeline.policy = policy
        for tool in ['capabilities','models_list']:
            result = self.call(tool)
            self.assertLessEqual(len(json.dumps(result,ensure_ascii=True).encode()),20*1024)
            page = result.get('provider_catalog',result)
            self.assertTrue(page['truncated'])
            self.assertIsNotNone(page['next_cursor'])
            self.assertGreater(len(page['models']),0)


    def test_exact_duplicated_mcp_wire_envelope_fits_64k_for_escaped_metadata(self):
        from mcp_adapter import REQUEST_PROTOCOL, VERSION_META, CAPABILITIES_META
        from mcp_http_transport import MAX_HTTP_BODY
        self.peer.models = []
        for n in range(40):
            row = copy.deepcopy(MODEL)
            row.update(id=('model'+str(n)).ljust(160,'x'),model=('model'+str(n)).ljust(160,'x'),
                displayName='"\\'*80, isDefault=False)
            row['supportedReasoningEfforts'] = [
                {'reasoningEffort':str(k)+'"\\'*30, 'description':'Fixture'} for k in range(16)]
            row['defaultReasoningEffort'] = row['supportedReasoningEfforts'][0]['reasoningEffort']
            self.peer.models.append(row)
        self.pipeline.policy['models'] = [row['model'] for row in self.peer.models[:8]]
        adapter = Adapter(self.root,allow_codex_pipeline=True,pipeline=self.pipeline, runtime_project_root=self.project)
        self.addCleanup(adapter.audit.close)
        for tool in ['codex_capabilities','codex_models_list']:
            reply = adapter.handle({'jsonrpc':'2.0','id':'q'*128,'method':'tools/call','params':{
                'name':tool,'arguments':{},'_meta':{VERSION_META:REQUEST_PROTOCOL,CAPABILITIES_META:{}}}})
            self.assertNotIn('error',reply)
            self.assertFalse(reply['result']['isError'])
            wire = json.dumps(reply,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
            self.assertLess(len(wire),MAX_HTTP_BODY)
            body = reply['result']['structuredContent']
            self.assertEqual(json.loads(reply['result']['content'][0]['text']),body)
            self.assertLessEqual(len(json.dumps(body['data'],ensure_ascii=True).encode()),20*1024)
            page = body['data'].get('provider_catalog',body['data'])
            self.assertTrue(page['truncated'])
            self.assertTrue(page['models'])


    def test_large_legacy_listing_uses_existing_pagination_instead_of_oversized_wire(self):
        self.pipeline.policy['max_tasks'] = 32
        thread = self.start()
        for n in range(32):
            self.call('chat_store',thread_id=thread,prompt='Stored '+str(n),
                reasoning_effort='medium',request_key='large-'+str(n))
        result = self.call('threads_list')
        self.assertIsNotNone(result['next_cursor'])
        self.assertLess(len(json.dumps(result).encode()),16000)
        seen = set()
        while True:
            rows = result['threads']+result['tasks']
            self.assertFalse(seen.intersection(row['id'] for row in rows))
            seen.update(row['id'] for row in rows)
            if result['next_cursor'] is None: break
            result = self.call('threads_list',cursor=result['next_cursor'])
        self.assertEqual(len(seen),33)

class OwnerScopedListingBoundaryTests(unittest.TestCase):
    setUp = PipelineTests.setUp
    tearDown = PipelineTests.tearDown
    new_pipeline = PipelineTests.new_pipeline
    call = PipelineTests.call
    complete = PipelineTests.complete

    def owned_adapter(self, tasks, threads):
        from types import SimpleNamespace
        class Owned:
            def thread_owned_by(self, identifier, principal):
                return principal == oauth_principal() and identifier in threads
            def task_owned_by(self, identifier, principal):
                return principal == oauth_principal() and identifier in tasks
        adapter = Adapter(self.root, allow_codex_pipeline=True, pipeline=self.pipeline, runtime_project_root=self.project)
        adapter.events_enabled = True
        adapter.notification_runtime = SimpleNamespace(outbox=Owned())
        self.addCleanup(adapter.audit.close)
        return adapter

    def test_authenticated_owner_pages_fit_exact_wire_and_keep_all_owned_records(self):
        from local_principal import OAuthPrincipal
        from mcp_adapter import REQUEST_PROTOCOL, VERSION_META, CAPABILITIES_META
        from mcp_http_transport import McpHttpEndpoint, MAX_HTTP_BODY
        model, effort = 'm' * 160, '"\\' * 32
        row = copy.deepcopy(MODEL)
        row.update(id=model, model=model, defaultReasoningEffort=effort,
                   supportedReasoningEfforts=[{'reasoningEffort': effort, 'description': ''}])
        self.peer.models = [row]
        self.pipeline.policy['models'] = [model]
        self.pipeline.policy['max_tasks'] = 32
        owned_thread = self.call('thread_start', workspace='fixture', model=model,
            reasoning_effort=effort, request_key='owned-thread')['thread_id']
        other_thread = self.call('thread_start', workspace='fixture', model=model,
            reasoning_effort=effort, request_key='other-thread')['thread_id']
        tasks = set()
        for n in range(32):
            thread = other_thread if n == 0 else owned_thread
            task = self.call('task_submit', thread_id=thread, prompt='Owned fixture ' + str(n),
                            reasoning_effort=effort, request_key='owned-task-' + str(n))
            self.complete(thread)
            if n: tasks.add(task['id'])
        adapter = self.owned_adapter(tasks, {owned_thread})
        endpoint = McpHttpEndpoint(adapter, expected_port=8765,
            oauth_verifier=SimpleNamespace(verify=lambda header: oauth_principal()),
            oauth_metadata={}, oauth_metadata_uri='https://resource.example.invalid/.well-known/oauth-protected-resource')
        cursor, seen, offsets = None, set(), []
        integer_limit = sys.get_int_max_str_digits()
        digits = min(4096, integer_limit - 1) if integer_limit else 4096
        request_ids = ['q' * 128, int('9' * digits), chr(0x1f642) * 128, '"\\' * 64]
        for page_index in range(40):
            arguments = {'limit': 20, **({'cursor': cursor} if cursor else {})}
            request = {'jsonrpc': '2.0', 'id': request_ids[page_index % len(request_ids)], 'method': 'tools/call', 'params': {
                'name': 'codex_threads_list', 'arguments': arguments,
                '_meta': {VERSION_META: REQUEST_PROTOCOL, CAPABILITIES_META: {}}}}
            raw = json.dumps(request, separators=(',', ':')).encode()
            headers = {'Host': '127.0.0.1:8765', 'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream', 'Content-Length': str(len(raw)),
                'MCP-Protocol-Version': REQUEST_PROTOCOL, 'Mcp-Method': 'tools/call',
                'Mcp-Name': 'codex_threads_list'}
            status, _, wire = endpoint.handle('POST', '/mcp', headers, raw)
            self.assertEqual(status, 200, wire[:200])
            self.assertLess(len(wire), MAX_HTTP_BODY)
            result = json.loads(wire)['result']
            self.assertFalse(result['isError'])
            self.assertEqual(json.loads(result['content'][0]['text']), result['structuredContent'])
            page = result['structuredContent']['data']
            identifiers = {x['id'] for x in page['threads'] + page['tasks']}
            self.assertTrue(identifiers)
            self.assertFalse(seen & identifiers)
            seen.update(identifiers)
            self.assertNotIn(other_thread, identifiers)
            self.assertEqual(page['total_records'], len(tasks) + 1)
            cursor = page['next_cursor']
            if cursor is None: break
            offsets.append(int(cursor.split(':')[1]))
        else: self.fail('Owner cursor did not reach its terminal page')
        self.assertEqual(seen, tasks | {owned_thread})
        self.assertTrue(offsets)
        self.assertEqual(offsets, sorted(set(offsets)))

    def test_owner_scan_keeps_logical_record_bound_with_short_global_pages(self):
        from local_principal import OAuthPrincipal
        from mcp_adapter import Invalid
        import uuid
        tasks = {str(uuid.UUID(int=n + 1)) for n in range(81)}
        adapter = self.owned_adapter(tasks, set())
        class Pages:
            def __init__(self, count): self.count, self.calls = count, 0
            def dispatch(self, name, arguments):
                offset = self.calls
                self.calls += 1
                return {'threads': [], 'tasks': [{'id': str(uuid.UUID(int=offset + 1))}],
                    'next_cursor': 'a' * 64 + ':' + str(self.calls) if self.calls < self.count else None}
        adapter.pipeline = pages = Pages(80)
        result = adapter._owner_scoped_thread_listing(oauth_principal(), {})
        self.assertEqual(result['total_records'], 80)
        self.assertEqual(pages.calls, 80)
        adapter.pipeline = pages = Pages(81)
        with self.assertRaisesRegex(Invalid, 'Owner listing exceeds'):
            adapter._owner_scoped_thread_listing(oauth_principal(), {})
        self.assertLessEqual(pages.calls, 80)

    def test_owner_scan_deadline_stops_before_another_global_read(self):
        from local_principal import OAuthPrincipal
        from mcp_adapter import Invalid
        adapter = self.owned_adapter(set(), set())
        class Pages:
            def __init__(self): self.calls = 0
            def dispatch(self, name, arguments):
                self.calls += 1
                return {'threads': [], 'tasks': [], 'next_cursor': 'a' * 64 + ':1'}
        adapter.pipeline = pages = Pages()
        with patch('mcp_adapter.time.monotonic', side_effect=[0, 0, 6]):
            with self.assertRaisesRegex(Invalid, 'Owner listing scan deadline'):
                adapter._owner_scoped_thread_listing(oauth_principal(), {})
        self.assertEqual(pages.calls, 1)

    def test_owner_scan_rejects_repeated_cursor_without_looping(self):
        from local_principal import OAuthPrincipal
        from mcp_adapter import Invalid
        adapter = self.owned_adapter(set(), set())
        class Pages:
            def __init__(self): self.calls = 0
            def dispatch(self, name, arguments):
                self.calls += 1
                return {'threads': [], 'tasks': [], 'next_cursor': 'a' * 64 + ':1'}
        adapter.pipeline = pages = Pages()
        with self.assertRaisesRegex(Invalid, 'Invalid pipeline listing cursor'):
            adapter._owner_scoped_thread_listing(oauth_principal(), {})
        self.assertEqual(pages.calls, 2)

    def test_oversized_single_owned_record_is_not_dropped_or_partially_returned(self):
        from local_principal import OAuthPrincipal
        from mcp_adapter import Invalid
        import uuid
        task = str(uuid.uuid4())
        adapter = self.owned_adapter({task}, set())
        class Page:
            def dispatch(self, name, arguments):
                return {'threads': [], 'tasks': [{'id': task, 'legacy_receipt': 'x' * 30000}],
                        'next_cursor': None}
        adapter.pipeline = Page()
        with self.assertRaisesRegex(Invalid, 'Owner listing exceeds bounded record size'):
            adapter._owner_scoped_thread_listing(oauth_principal(), {})

class FakeOwned:
    def __init__(self, peer, safe=True): self.peer, self.cleanup_confirmed, self.closed = peer, safe, False
    def bind_client(self, client): self.client = client
    def start(self): pass
    def __call__(self, request, timeout_s): return self.peer(request, timeout_s)
    def poll(self, timeout_s=0): return 0
    def close(self): self.closed = True

class GatewayTests(unittest.TestCase):
    def setUp(self):
        runtime_fixture(self, 'laf-catalog-gateway-')
        self.addCleanup(self.temp.cleanup)
        self.workspace = self.root / 'workspace'; self.workspace.mkdir()
        self.binary = self.root / 'codex'; self.binary.write_bytes(b'\x7fELFfixture'); self.binary.chmod(0o700)
        self.policy = {'schema': 1, 'workspaces': {'fixture': str(self.workspace)}, 'models': ['offline-model'],
                       'max_threads': 4, 'max_tasks': 16, 'concurrency': 1, 'timeout_seconds': 120}
        for name, value in [('codex-pipeline-policy.json', self.policy), ('codex-pipeline-executor.json',
                {'schema': 1, 'binary_sha256': hashlib.sha256(self.binary.read_bytes()).hexdigest()})]:
            path = self.root / name; path.write_text(json.dumps(value)); path.chmod(0o600)
        self.bundle = type('Bundle', (), {'sha256': 'a' * 64})()
        for target, value in [('codex_host.load_project_policy', self.bundle), ('codex_host.developer_instructions', 'fixed policy')]:
            p = patch(target, return_value=value); p.start(); self.addCleanup(p.stop)
        p = patch.object(HostGateway, 'require_host'); p.start(); self.addCleanup(p.stop)
        self.busy, self.transports = False, []
        self.peer = Peer(str(self.workspace))
        self.gateway = HostGateway(self.root, str(self.binary), self.project, lambda: self.busy, transport_factory=self.factory)
        self.addCleanup(self.gateway.close)

    def factory(self, binary):
        self.assertEqual(binary, str(self.binary))
        transport = FakeOwned(self.peer); self.transports.append(transport); return transport

    def call(self, name, **args): return self.gateway.dispatch('codex_' + name, args)

    def start(self): return self.call('thread_start', workspace='fixture', model='offline-model', request_key='start')['thread_id']

    def submit(self, thread, key='task'): return self.call('task_submit', thread_id=thread, prompt='public fixture', request_key=key)

    def complete(self, thread):
        remote = self.gateway.pipeline._mutate(lambda s: s['threads'][thread]['remote_id'])
        self.peer.threads[remote]['turns'][-1].update(status='completed', items=[])

    def test_metadata_session_closes_and_releases_runner_budget(self):
        self.call('capabilities')
        self.assertTrue(self.transports[0].closed)
        self.assertFalse(self.gateway.held)
        self.assertEqual(self.transports[0].workspace, str(self.workspace))
        self.assertEqual(self.transports[0].client.timeout_s, 10)
        with lease(self.root / 'runner.lock', shared=True): pass

    def test_models_list_reserves_metadata_budget_closes_fake_and_rediscovers(self):
        self.busy = True
        before = len(self.peer.calls)
        with self.assertRaisesRegex(PipelineError, 'SHARED_BUDGET_HELD'):
            self.call('models_list')
        self.assertEqual(len(self.peer.calls), before)
        self.busy = False
        first = self.call('models_list')
        self.assertEqual(first['models'][0]['default_reasoning_effort'], 'medium')
        self.peer.models[0]['defaultReasoningEffort'] = 'high'
        second = self.call('models_list')
        self.assertEqual(second['models'][0]['default_reasoning_effort'], 'high')
        self.assertEqual(len(self.transports), 2)
        self.assertTrue(all(t.closed for t in self.transports))
        self.assertIsNone(self.gateway.budget)
        self.assertEqual(self.peer.count('thread/start'), 0)
        self.assertEqual(self.peer.count('turn/start'), 0)


class EffortGatewayBoundaryTests(unittest.TestCase):
    def setUp(self):
        fixture=GatewayTests('test_metadata_session_closes_and_releases_runner_budget')
        fixture.setUp();self.addCleanup(fixture.doCleanups);self.f=fixture

    def test_explicit_intent_can_be_stored_and_queued_while_real_runner_lease_is_held(self):
        f=self.f;thread=f.start();f.gateway.release_idle(force=True)
        before=len(f.peer.calls)
        with lease(f.root/'runner.lock',shared=True):
            task=f.call('task_submit',thread_id=thread,prompt='Explicit offline intent',
                reasoning_effort='high',request_key='explicit-while-busy')
            self.assertEqual(task['state'],'QUEUED')
            self.assertEqual(task['reasoning_effort'],'high')
            self.assertIsNone(task['selection_catalog_sha256'])
            self.assertEqual(len(f.peer.calls),before)
        f.gateway.pump()
        self.assertEqual(f.peer.count('turn/start'),1)
        turn=next(args for method,args in f.peer.calls if method=='turn/start')
        self.assertEqual(turn['effort'],'high')

    def test_new_effort_survives_proxy_and_daemon_routing(self):
        f=self.f
        arguments={'thread_id':'10000000-0000-4000-8000-000000000091',
            'prompt':'Fixture','reasoning_effort':'provider custom/value','request_key':'route'}
        proxy=DaemonPipelineProxy(f.root)
        with patch('codex_host.request',return_value={'state':'STORED'}) as request:
            self.assertEqual(proxy.dispatch('codex_chat_store',arguments),{'state':'STORED'})
        self.assertEqual(request.call_args.args[2],{'tool':'codex_chat_store','arguments':arguments})
        daemon=Daemon.__new__(Daemon);daemon.allow_codex_pipeline=True;daemon.pipeline=Mock()
        daemon.handle('codex_pipeline',{'tool':'codex_chat_store','arguments':arguments})
        daemon.pipeline.dispatch.assert_called_once_with('codex_chat_store',arguments)
        with self.assertRaisesRegex(PipelineError,'INVALID_REASONING_EFFORT'):
            proxy.dispatch('codex_chat_store',{**arguments,'reasoning_effort':'bad\nvalue'})
        daemon.pipeline.dispatch.reset_mock()
        result=daemon.handle('codex_pipeline',{'tool':'codex_chat_store','arguments':{**arguments,'reasoning_effort':'bad\nvalue'}})
        self.assertEqual(result['code'],'INVALID_REASONING_EFFORT')
        daemon.pipeline.dispatch.assert_not_called()

CONTRACT = json.loads((SOURCE / 'scripts/fixtures/codex-0.167-contract.json').read_text())

class Codex0167ContractTests(unittest.TestCase):
    def test_exact_version_admission_not_future_release(self):
        self.assertTrue(schema_version_supported('codex_work_desktop/0.167.0 (Linux)'))
        for value in ('codex/0.167.0-dev', 'codex/0.167.1', 'codex/0.168.0', 'codex/10.167.0'):
            self.assertFalse(schema_version_supported(value))

    def test_exposed_parameter_fields_are_in_generated_stable_schema(self):
        for method, fields in _FIELDS.items():
            with self.subTest(method=method):
                self.assertLessEqual(fields, set(CONTRACT['methods'][method]['properties']))

    def test_generated_required_parameters_are_enforced(self):
        for method, value in METHOD_SCHEMAS.items():
            with self.subTest(method=method):
                self.assertLessEqual(set(CONTRACT['methods'][method]['required']), set(value[2]))

    def test_reasoning_contract_is_advertised_strings_not_native_role_enum(self):
        value = CONTRACT['reasoning_contract']
        self.assertEqual((value['effort_type'],value['effort_min_length']),('string',1))
        self.assertIsNone(value['effort_enum'])
        self.assertEqual(set(value['option_required']),{'description','reasoningEffort'})
        self.assertEqual(value['thread_config_key'],'model_reasoning_effort')
        self.assertTrue(value['thread_echo_optional'])
        self.assertTrue(value['thread_echo_nullable'])
        self.assertFalse(value['turn_has_effort_receipt'])
        for schema in value['schemas'].values():
            self.assertRegex(schema['sha256'],r'^[0-9a-f]{64}$')

    def test_evidence_is_versioned_bounded_and_stable_only(self):
        self.assertEqual(CONTRACT['version'], '0.167.0')
        self.assertFalse(CONTRACT['experimental_api'])
        self.assertEqual(set(CONTRACT['methods']), set(METHOD_SCHEMAS))
        for value in CONTRACT['methods'].values():
            self.assertRegex(value['sha256'], r'^[0-9a-f]{64}$')

OWNER = 'mcp-principal:test-user-1'
OTHER_OWNER = 'mcp-principal:test-user-2'
TASK_A = '00000000-0000-4000-8000-000000000001'
TASK_B = '00000000-0000-4000-8000-000000000002'
THREAD_A = '10000000-0000-4000-8000-000000000001'
SECRET = 'whsec_' + base64.b64encode(b'x' * 32).decode('ascii')


class TestCipher:
    """Insecure reversible test double; production requires a real secret vault."""
    def encrypt(self, value):
        return b'test-cipher:' + base64.urlsafe_b64encode(value)

    def decrypt(self, value):
        if not value.startswith(b'test-cipher:'):
            raise ValueError('bad fixture ciphertext')
        return base64.urlsafe_b64decode(value[len(b'test-cipher:'):])


class NotificationOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='notification-outbox-')
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.source_db = self.root / 'codex-pipeline.sqlite3'
        self.outbox_db = self.root / 'notification-outbox.sqlite3'
        self.now = [1_800_000_000.0]
        self.write_source({'tasks': {TASK_A: {'id': TASK_A}, TASK_B: {'id': TASK_B}}, 'events': []})
        self.outbox = self.make_outbox()

    def tearDown(self):
        self.temp.cleanup()

    def make_outbox(self):
        return NotificationOutbox(self.outbox_db, source_db_path=self.source_db,
            secret_cipher=TestCipher(), owner_hash_key=b'outbox-test-owner-key-000000000000000000000000',
            clock=lambda: self.now[0],
            resolver=lambda host: ['8.8.8.8'], base_backoff=1, max_backoff=4,
            max_attempts=4, lease_seconds=5)

    def write_source(self, state):
        conn = sqlite3.connect(self.source_db)
        conn.execute('CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        conn.execute('INSERT INTO ledger VALUES (1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                     (json.dumps(state, sort_keys=True, separators=(',', ':')),))
        conn.commit()
        conn.close()
        os.chmod(self.source_db, 0o600)

    def append_events(self, *events):
        state = self.outbox._read_source_state()
        state['events'].extend(events)
        self.write_source(state)

    def event(self, kind, task_id=TASK_A, seq=1, **extra):
        return {'seq': seq, 'kind': kind, 'time': self.now[0], 'task_id': task_id,
                'thread_id': THREAD_A, **extra}

    def subscribe(self, task_id=TASK_A, owner=OWNER, callback='https://notify.example.net/mcp-events/cb', event_name=None):
        if event_name is None:
            event_name = EVENT_NAMES['task_completed'][0]
        self.outbox.bind_task_origin(task_id, owner)
        result = self.outbox.subscribe(callback_url=callback, event_name=event_name,
            arguments={'task_id': task_id}, authenticated_principal=owner,
            signing_secret=SECRET, ttl_ms=60_000, cursor=0)
        if result['verification_required']:
            request = self.outbox.build_verification_request(result['id'], now=self.now[0])
            challenge = json.loads(request['body'])['challenge']
            self.outbox.complete_verification(result['id'], status_code=200,
                echoed_challenge=challenge, now=self.now[0])
        return result['id']

    def test_reasoning_catalog_blockers_replay_exactly_once_without_private_metadata(self):
        self.subscribe(event_name=EVENT_NAMES['task_blocked'][0])
        reasons = {
            'REASONING_EFFORT_NOT_SUPPORTED','LEGACY_REASONING_EFFORT_MISSING',
            'IMMUTABLE_MODEL_SELECTION_CHANGED','MODEL_CATALOG_UNKNOWN',
            'MODEL_CATALOG_INVALID','MODEL_CATALOG_BOUND','RESUME_REASONING_EFFORT_MISMATCH'}
        events = [self.event('task_blocked',seq=n,reason_code=code,
            prompt='private prompt',provider_description='private provider text')
            for n,code in enumerate(sorted(reasons),1)]
        self.append_events(*(events+events))
        self.outbox.sync_from_ledger()
        self.outbox.sync_from_ledger()
        with closing(sqlite3.connect(self.outbox_db)) as conn:
            rows = conn.execute('SELECT event_id,payload,status FROM notification_outbox').fetchall()
        self.assertEqual(len(rows),len(reasons))
        self.assertEqual(len({row[0] for row in rows}),len(reasons))
        self.assertTrue(all(row[2]=='PENDING' for row in rows))
        payloads = [json.loads(row[1]) for row in rows]
        self.assertEqual({row['data']['reason_code'] for row in payloads},reasons)
        for payload in payloads:
            self.assertEqual(set(payload['data']),{'task_id','thread_id','state','action_required','reason_code'})
            self.assertNotIn('private',json.dumps(payload))

class LafPortRegressionTests(unittest.TestCase):
    setUp = PipelineTests.setUp
    tearDown = PipelineTests.tearDown
    new_pipeline = PipelineTests.new_pipeline
    call = PipelineTests.call
    start = PipelineTests.start

    def test_integrated_inventory_only_adds_catalog_and_retains_router_and_legacy(self):
        import legacy_tool_adapter as legacy
        with patch.object(sys, 'path', [str(SOURCE / 'runtime/integration'),
                str(SOURCE / 'runtime/public_laf/mcp/src'), *sys.path]):
            from laf_shared_tools import CompositeSharedTools
        expected = {
            'orchestrator_status', 'orchestrator_list_tasks', 'orchestrator_get_task',
            'aceis_local_llm_generate', 'aceis_local_model_status',
            'aceis_local_model_generate', 'aceis_local_model_prepare',
            'chat_mailbox_register_request', 'chat_mailbox_get_request',
            'chat_mailbox_save_answer', 'chat_mailbox_get_answer',
            'codex_capabilities', 'codex_models_list', 'codex_thread_start',
            'codex_task_submit', 'codex_chat_store', 'codex_chat_queue',
            'codex_chat_steer', 'codex_threads_list', 'codex_task_get', 'codex_events',
            'codex_task_cancel', 'codex_thread_archive', 'codex_pipeline_tick',
            'experiment_list', 'experiment_get', 'experiment_artifacts', 'experiment_compare'}
        definitions = legacy.tool_definitions(local_llm=True, local_models=True,
            chat_mailbox=True, codex_pipeline=True, experiments=True)
        self.assertEqual({t['name'] for t in definitions}, expected)
        stub = SimpleNamespace(tools=definitions, audit=Mock(), call_tool=Mock())
        composite = CompositeSharedTools(stub, SimpleNamespace(call=Mock()), server_info=legacy.SERVER_INFO)
        router = {'laf_router_models', 'laf_router_status', 'laf_router_submit_job',
                  'laf_router_get_job', 'laf_router_cancel_job'}
        self.assertEqual({t['name'] for t in composite.tools}, expected | router)
        self.assertEqual(len(composite.tools), 33)
        self.assertEqual(len({t['name'] for t in composite.tools}), len(composite.tools))
        self.assertFalse(any(t['name'].startswith('codex_approval_') for t in composite.tools))
        self.assertEqual(self.call('capabilities')['approval'], 'notify_only')
        self.assertEqual(legacy.SERVER_INFO['version'], '0.1.0')

    def test_capability_order_and_legacy_fields_survive_unknown_fresh_discovery(self):
        other = copy.deepcopy(MODEL)
        other.update(id='second', model='second', isDefault=False)
        self.peer.models.append(other)
        self.pipeline.policy['models'] = ['second', 'offline-model', 'missing']
        observed = self.call('capabilities')
        self.assertEqual([r['model'] for r in observed['models']], self.pipeline.policy['models'])
        self.assertEqual([r['available'] for r in observed['models']], [True, True, False])
        self.assertEqual(observed['workspaces'], ['fixture'])
        self.assertEqual(observed['policy_sha256'], 'a' * 64)
        self.assertFalse(observed['billing_verified'])
        before = self.peer.count('model/list')
        self.peer.failure['model/list'] = TimeoutError('Synthetic private transport error')
        unknown = self.call('capabilities')
        self.assertEqual(self.peer.count('model/list'), before + 1)
        self.assertTrue(all(not r['available'] and not r['selectable']
                            and r['availability'] == 'unknown' for r in unknown['models']))
        self.assertEqual(unknown['provider_catalog']['models'], [])
        self.assertNotIn('Synthetic private', json.dumps(unknown))

    def test_historical_omitted_intent_duplicates_preserve_old_records_without_discovery(self):
        thread = self.start()
        task = self.call('chat_store', thread_id=thread, prompt='Historical input', request_key='store')
        def historical(state):
            for record in [state['threads'][thread], state['tasks'][task['id']]]:
                for key in list(record):
                    if key.startswith(('reasoning_effort', 'requested_reasoning_effort',
                                       'selection_', 'context_reasoning_effort')):
                        del record[key]
            for operation in state['ops'].values():
                operation['context'].pop('selection_sha256', None)
                for key in list(operation.get('result', {})):
                    if key.startswith(('reasoning_effort', 'requested_reasoning_effort',
                                       'selection_', 'context_reasoning_effort')):
                        del operation['result'][key]
            return copy.deepcopy(state)
        before = self.pipeline._mutate(historical)
        calls = len(self.peer.calls)
        self.peer.failure['model/list'] = TimeoutError('Offline')
        self.assertEqual(self.start(), thread)
        duplicate = self.call('chat_store', thread_id=thread, prompt='Historical input', request_key='store')
        self.assertEqual(duplicate['id'], task['id'])
        self.assertNotIn('reasoning_effort', duplicate)
        self.assertEqual(self.pipeline._mutate(lambda state: state), before)
        self.assertEqual(len(self.peer.calls), calls)

    def test_catalog_exact_full_wire_bound_for_both_facades_and_extreme_request_ids(self):
        import legacy_tool_adapter as legacy
        from mcp_adapter import REQUEST_PROTOCOL, VERSION_META, CAPABILITIES_META
        from mcp_http_transport import MAX_HTTP_BODY
        self.peer.models = []
        for number in range(40):
            row = copy.deepcopy(MODEL)
            row.update(id=('model'+str(number)).ljust(160, 'x'),
                       model=('model'+str(number)).ljust(160, 'x'), displayName='"\\'*80)
            row['supportedReasoningEfforts'] = [
                {'reasoningEffort':str(n)+'"\\'*30, 'description':''} for n in range(16)]
            row['defaultReasoningEffort'] = row['supportedReasoningEfforts'][0]['reasoningEffort']
            self.peer.models.append(row)
        self.pipeline.policy['models'] = [row['model'] for row in self.peer.models[:8]]
        integer_limit = sys.get_int_max_str_digits()
        digits = min(4096, integer_limit - 1) if integer_limit else 4096
        ids = [int('9' * digits), chr(0x1f642) * 128, '"\\' * 64]
        for adapter_type in (legacy.Adapter, Adapter):
            adapter = adapter_type(self.root, allow_codex_pipeline=True, pipeline=self.pipeline,
                                   runtime_project_root=self.project)
            self.addCleanup(adapter.audit.close)
            for name in ('codex_models_list', 'codex_capabilities'):
                for identifier in ids:
                    with self.subTest(adapter=adapter_type.__module__, name=name, id_type=type(identifier).__name__):
                        reply = adapter.handle({'jsonrpc':'2.0', 'id':identifier, 'method':'tools/call',
                            'params':{'name':name, 'arguments':{}, '_meta':{
                                VERSION_META:REQUEST_PROTOCOL, CAPABILITIES_META:{}}}})
                        self.assertNotIn('error', reply)
                        self.assertFalse(reply['result']['isError'])
                        for ascii_wire in (True, False):
                            wire = json.dumps(reply, ensure_ascii=ascii_wire, allow_nan=False).encode() + b'\n'
                            self.assertLessEqual(len(wire), MAX_HTTP_BODY)
                        self.assertEqual(json.loads(reply['result']['content'][0]['text']),
                                         reply['result']['structuredContent'])
        self.assertEqual(self.peer.count('thread/start'), 0)
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_effort_utf8_byte_limit_and_unicode_controls_fail_before_transport(self):
        from codex_app_protocol import valid_reasoning_effort, validate_model_page
        # Unicode test fixture: multibyte provider tokens exercise bytes, not character counts.
        allowed, oversized = chr(0xe9) * 32, chr(0xe9) * 33
        self.assertTrue(valid_reasoning_effort(allowed))
        for value in [oversized, 'x'+chr(0x200b), 'x'+chr(0x2028), 'x'+chr(0x2029), chr(0xd800)]:
            self.assertFalse(valid_reasoning_effort(value))
            with self.assertRaises(ProtocolError):
                self.peer.client.request('turn/start', {'threadId':'fake',
                    'input':[{'type':'text','text':'Fixture'}], 'effort':value})
        row = copy.deepcopy(MODEL)
        row.update(defaultReasoningEffort=allowed,
            supportedReasoningEfforts=[{'reasoningEffort':allowed, 'description':''}])
        self.assertEqual(validate_model_page({'data':[row]}), {'data':[row]})
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_owner_scoping_retains_oauth_type_filter_and_changed_snapshot_refusal(self):
        from local_principal import current_local_principal
        from mcp_adapter import Invalid
        fixture = OwnerScopedListingBoundaryTests()
        fixture.root, fixture.project, fixture.pipeline = self.root, self.project, self.pipeline
        self.addCleanup(fixture.doCleanups)
        thread = self.start()
        task = self.call('chat_store', thread_id=thread, prompt='Owned', reasoning_effort='medium', request_key='one')
        owned = {task['id']}
        adapter = fixture.owned_adapter(owned, {thread})
        with self.assertRaisesRegex(Invalid, 'Authenticated task ACL'):
            adapter._owner_scoped_thread_listing(current_local_principal(), {})
        stranger = OAuthPrincipal('https://issuer.example.invalid', 'different-owner')
        empty = adapter._owner_scoped_thread_listing(stranger, {})
        self.assertEqual(empty, {'threads':[], 'tasks':[], 'total_records':0, 'next_cursor':None})
        page = adapter._owner_scoped_thread_listing(oauth_principal(), {'limit':1})
        self.assertIsNotNone(page['next_cursor'])
        owned.clear()
        with self.assertRaisesRegex(Invalid, 'Owner listing changed'):
            adapter._owner_scoped_thread_listing(oauth_principal(), {'cursor':page['next_cursor']})
        with self.assertRaisesRegex(Invalid, 'Resource not available'):
            adapter._require_owned_pipeline_resource(stranger, 'task', task['id'])

    def test_catalog_extension_does_not_expand_shared_tool_oauth_access(self):
        from mcp_adapter import Invalid
        shared = SimpleNamespace(tools=[{'name':'orchestrator_status'}], call_tool=Mock(return_value={'ok':True}))
        adapter = Adapter(self.root, allow_codex_pipeline=True, pipeline=self.pipeline,
            runtime_project_root=self.project, shared_tool_adapter=shared)
        self.addCleanup(adapter.audit.close)
        with self.assertRaisesRegex(Invalid, 'Shared tool surface is not authorized'):
            adapter.call_tool('orchestrator_status', {}, 1, request_principal=oauth_principal())
        shared.call_tool.assert_not_called()
        # Existing explicit single-principal permission is neither widened nor
        # substituted by local UID when the new read-only catalog is enabled.
        adapter.allow_shared_tools_http = True
        adapter.shared_tools_principal = oauth_principal()
        stranger = OAuthPrincipal('https://issuer.example.invalid', 'different-owner')
        with self.assertRaises(Invalid):
            adapter.call_tool('orchestrator_status', {}, 2, request_principal=stranger)
        self.assertEqual(adapter.call_tool('orchestrator_status', {}, 3,
            request_principal=oauth_principal()), {'ok':True})
        shared.call_tool.assert_called_once_with('orchestrator_status', {}, 3)

    def test_build_rejects_uninspected_version_and_reaps_fake_before_discovery(self):
        path = self.root / 'codex-pipeline-policy.json'
        path.write_text(json.dumps(self.policy)); path.chmod(0o600)
        transport = SimpleNamespace(start=Mock(), close=Mock(), bind_client=Mock())
        peer = self.peer
        class Transport:
            def start(self): transport.start()
            def close(self): transport.close()
            def bind_client(self, client): transport.bind_client(client)
            def __call__(self, message, timeout): return peer(message, timeout)
        before = self.peer.count('model/list')
        with patch('common_policy.load_project_policy', return_value=SimpleNamespace(sha256='a'*64)), \
                patch('common_policy.developer_instructions', return_value='Fixed policy'), \
                patch.dict(INITIALIZE, {'userAgent':'codex/0.167.1'}):
            with self.assertRaisesRegex(PipelineError, 'UNVERIFIED_CODEX_VERSION'):
                build_pipeline(self.root, self.project, transport_factory=Transport)
        transport.close.assert_called_once_with()
        self.assertEqual(self.peer.count('model/list'), before)
        self.assertEqual(self.peer.count('turn/start'), 0)


class EmptyContextEffortRegressionTests(unittest.TestCase):
    setUp = PipelineTests.setUp
    tearDown = PipelineTests.tearDown
    new_pipeline = PipelineTests.new_pipeline
    call = PipelineTests.call

    def recovery_input(self):
        thread = self.call('thread_start', workspace='fixture', model='offline-model',
            reasoning_effort='high', request_key='thread')['thread_id']
        task = self.call('chat_store', thread_id=thread, prompt='Stored input',
                         reasoning_effort='medium', request_key='stored')
        return thread, task

    def evidence(self, thread):
        state = self.pipeline._mutate(lambda s: copy.deepcopy(s))
        diagnostic = {'thread_id':thread, 'inference_calls':0, 'new_threads':0,
            'cleanup_status':'RELEASED', 'remote_turn_count':0, 'checks':[{
                'method':'thread/resume', 'exception_class':'RpcError',
                'rpc_error_code':-32600, 'reason_categories':['NO_ROLLOUT']}]}
        return {'schema':1, 'thread_id':thread, 'ledger_sha256':digest(state),
            'remote_id_sha256':digest(state['threads'][thread]['remote_id']),
            'diagnostic_sha256':digest(diagnostic), 'diagnostic':diagnostic}

    def restore(self, value):
        def change(state):
            state.clear(); state.update(copy.deepcopy(value))
        self.pipeline._mutate(change)

    def test_operator_replacement_carries_fixed_effort_preserves_input_and_is_single_shot(self):
        thread, task = self.recovery_input()
        old = self.pipeline._mutate(lambda s: copy.deepcopy(s['threads'][thread]))
        proof = self.evidence(thread)
        result = self.pipeline.replace_empty_context(thread, 'replace', proof)
        self.assertEqual(result, {'status':'READY', 'thread_id':thread, 'native_creation_count':2})
        state = self.pipeline._mutate(lambda s: copy.deepcopy(s))
        current = state['threads'][thread]
        self.assertEqual(current['reasoning_effort'], old['reasoning_effort'])
        self.assertEqual(state['tasks'][task['id']]['selection_sha256'], task['selection_sha256'])
        self.assertEqual(state['tasks'][task['id']]['state'], 'STORED')
        self.assertNotEqual(current['remote_id'], old['remote_id'])
        self.assertEqual(current['original_remote_id'], old['remote_id'])
        self.assertEqual(current['replacement_attempts'], 1)
        self.assertEqual(current['context_reasoning_effort_evidence']['provider_echo'], 'high')
        self.assertFalse(current['context_reasoning_effort_evidence']['actual_execution_verified'])
        replacement = current['native_replacements'][0]
        self.assertEqual(replacement['previous_remote_id'], old['remote_id'])
        self.assertEqual(replacement['proof_sha256'], digest(proof))
        self.assertEqual(replacement['request_key_hash'], digest('replace'))
        starts = [args for method, args in self.peer.calls if method == 'thread/start']
        self.assertEqual(starts[-1]['config'], {'model_reasoning_effort':'high'})
        self.assertEqual(starts[-1]['approvalPolicy'], 'on-request')
        self.assertEqual(starts[-1]['sandbox'], 'read-only')
        calls = len(self.peer.calls)
        self.assertTrue(self.pipeline.replace_empty_context(thread, 'replace', proof)['duplicate'])
        with self.assertRaisesRegex(PipelineError, 'EMPTY_CONTEXT_RECOVERY_FORBIDDEN'):
            self.pipeline.replace_empty_context(thread, 'second-replace', self.evidence(thread))
        self.assertEqual(len(self.peer.calls), calls)
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_replacement_never_backfills_legacy_thread_or_input_selection(self):
        thread, task = self.recovery_input()
        original = self.pipeline._mutate(lambda s: copy.deepcopy(s))
        for kind, identifier in [('threads',thread), ('tasks',task['id'])]:
            with self.subTest(kind=kind):
                self.restore(original)
                self.pipeline._mutate(lambda state: state[kind][identifier].pop('reasoning_effort'))
                before = self.pipeline._mutate(lambda state: copy.deepcopy(state))
                calls = len(self.peer.calls)
                with self.assertRaisesRegex(PipelineError, 'LEGACY_REASONING_EFFORT_MISSING'):
                    self.pipeline.replace_empty_context(thread, 'legacy-replace', self.evidence(thread))
                self.assertEqual(self.pipeline._mutate(lambda state: state), before)
                self.assertEqual(len(self.peer.calls), calls)
                self.assertNotIn('replacement_attempts', before['threads'][thread])

    def test_replacement_evidence_scope_history_and_attempt_guards_remain_before_transport(self):
        thread, task = self.recovery_input()
        original = self.pipeline._mutate(lambda state: copy.deepcopy(state))
        changes = [
            ('threads',thread,{'archived':True},'EMPTY_CONTEXT_RECOVERY_FORBIDDEN'),
            ('threads',thread,{'closing':True},'EMPTY_CONTEXT_RECOVERY_FORBIDDEN'),
            ('threads',thread,{'history_verified':True},'EMPTY_CONTEXT_RECOVERY_FORBIDDEN'),
            ('threads',thread,{'native_creation_count':2},'EMPTY_CONTEXT_RECOVERY_FORBIDDEN'),
            ('threads',thread,{'replacement_attempts':1},'EMPTY_CONTEXT_RECOVERY_FORBIDDEN'),
            ('tasks',task['id'],{'state':'UNKNOWN'},'PRIOR_OR_UNKNOWN_EXECUTION_FORBIDS_REPLACEMENT'),
            ('tasks',task['id'],{'deadline':123},'PRIOR_OR_UNKNOWN_EXECUTION_FORBIDS_REPLACEMENT')]
        for kind, identifier, change, code in changes:
            with self.subTest(change=change):
                self.restore(original)
                self.pipeline._mutate(lambda state: state[kind][identifier].update(change))
                before = self.pipeline._mutate(lambda state: copy.deepcopy(state))
                calls = len(self.peer.calls)
                with self.assertRaisesRegex(PipelineError, code):
                    self.pipeline.replace_empty_context(thread, 'invalid-replace', self.evidence(thread))
                self.assertEqual(self.pipeline._mutate(lambda state: state), before)
                self.assertEqual(len(self.peer.calls), calls)
        self.restore(original)
        for field in ('ledger_sha256','remote_id_sha256'):
            proof = self.evidence(thread); proof[field] = '0'*64
            with self.assertRaisesRegex(PipelineError, 'EMPTY_CONTEXT_EVIDENCE_STALE'):
                self.pipeline.replace_empty_context(thread, 'stale-replace', proof)
        proof = self.evidence(thread)
        proof['diagnostic']['remote_turn_count'] = 1
        proof['diagnostic_sha256'] = digest(proof['diagnostic'])
        with self.assertRaisesRegex(PipelineError, 'EMPTY_CONTEXT_NOT_PROVEN'):
            self.pipeline.replace_empty_context(thread, 'unproven-replace', proof)
        self.assertEqual(self.pipeline._mutate(lambda state: state), original)
        self.assertEqual(self.peer.count('thread/start'), 1)

    def test_replacement_wrong_remote_id_or_effort_echo_is_unknown_without_replay(self):
        thread, _ = self.recovery_input()
        original = self.pipeline._mutate(lambda state: copy.deepcopy(state))
        remote = original['threads'][thread]['remote_id']
        original_request = self.peer.client.request
        for mismatch in ('remote_id','effort_echo'):
            with self.subTest(mismatch=mismatch):
                self.restore(original)
                def request(method, arguments):
                    result = original_request(method, arguments)
                    if method == 'thread/start':
                        if mismatch == 'remote_id': result['thread']['id'] = remote
                        else: result['reasoningEffort'] = 'medium'
                    return result
                proof = self.evidence(thread)
                with patch.object(self.peer.client, 'request', side_effect=request):
                    result = self.pipeline.replace_empty_context(thread, 'mismatch-'+mismatch, proof)
                self.assertEqual(result['status'], 'UNKNOWN')
                calls = len(self.peer.calls)
                duplicate = self.pipeline.replace_empty_context(thread, 'mismatch-'+mismatch, proof)
                self.assertEqual(duplicate['status'], 'UNKNOWN')
                self.assertTrue(duplicate['duplicate'])
                self.assertEqual(len(self.peer.calls), calls)
                current = self.pipeline._mutate(lambda state: state['threads'][thread])
                self.assertEqual(current['native_context_state'], 'REPLACEMENT_UNKNOWN')
                self.assertEqual(current['replacement_attempts'], 1)
                self.assertEqual(current['remote_id'], remote)
                self.assertNotIn('native_replacements', current)
        self.assertEqual(self.peer.count('turn/start'), 0)


class OAuthPersistedBlockerTests(unittest.TestCase):
    setUp = PipelineTests.setUp
    tearDown = PipelineTests.tearDown
    new_pipeline = PipelineTests.new_pipeline

    def compose(self, *, fixture=None, proxy=False):
        from mcp_events_service import McpEventsService
        target = fixture or self
        self.owner = oauth_principal()
        self.stranger = OAuthPrincipal('https://issuer.example.invalid', 'other-blocker-owner')
        self.source = target.gateway.pipeline if fixture else self.pipeline
        self.outbox = NotificationOutbox(target.root / 'owner-outbox.sqlite3',
            source_db_path=self.source.db, secret_cipher=TestCipher(),
            owner_hash_key=b'blocked-input-owner-key-0000000000000000000000',
            resolver=lambda host: ['8.8.8.8'])
        allowed = {self.owner.identity, self.stranger.identity}
        self.service = McpEventsService(self.outbox,
            authorize_task=lambda identity, task: identity in allowed and self.outbox.source_task_exists(task),
            owner_access_check=lambda owner_hash, task: self.outbox.task_owned_by_hash(task, owner_hash),
            callback_verifier=lambda request: {'status_code':200}, allow_external_callbacks=True)
        pipeline = DaemonPipelineProxy(target.root) if proxy else self.source
        self.adapter = Adapter(target.root, allow_codex_pipeline=True, pipeline=pipeline,
            events_service=self.service, runtime_project_root=target.project)
        self.adapter.notification_runtime = SimpleNamespace(outbox=self.outbox)
        self.addCleanup(self.adapter.audit.close)
        self.rpc_id = 0
        return self.api('codex_thread_start', {'workspace':'fixture', 'model':'offline-model',
            'reasoning_effort':'medium', 'request_key':'oauth-owner-thread'})['structuredContent']['data']['thread_id']

    def api(self, name, arguments, principal=None):
        self.rpc_id += 1
        return self.adapter.call_tool(name, arguments, self.rpc_id,
            request_principal=self.owner if principal is None else principal)

    def arguments(self, thread, key='blocked-owned'):
        return {'thread_id':thread, 'prompt':'Keep private blocked input',
                'reasoning_effort':'not-advertised', 'request_key':key}

    def blocked_without_adapter(self, thread):
        arguments = self.arguments(thread)
        result = self.source.dispatch('codex_task_submit', arguments)
        self.assertEqual(result['state'], 'BLOCKED')
        self.assertFalse(self.outbox.task_owned_by(result['id'], self.owner))
        return arguments, result

    def assert_owner_access(self, task, thread):
        self.assertTrue(self.outbox.task_owned_by(task, self.owner))
        self.assertFalse(self.outbox.task_owned_by(task, self.stranger))
        read = self.api('codex_task_get', {'task_id':task})['structuredContent']['data']
        self.assertEqual((read['id'], read['state']), (task, 'BLOCKED'))
        listing = self.api('codex_threads_list', {})['structuredContent']['data']
        self.assertIn(task, {item['id'] for item in listing['tasks']})
        events = self.api('codex_events', {})['structuredContent']['data']['events']
        self.assertTrue(any(item.get('task_id') == task and item['kind'] == 'task_blocked' for item in events))
        for tool in ('codex_task_get', 'codex_task_cancel'):
            args = {'task_id':task, **({'request_key':'foreign-cancel'} if tool.endswith('cancel') else {})}
            denied = self.api(tool, args, self.stranger)
            self.assertEqual(denied['structuredContent']['data']['code'], 'NOT_AUTHORIZED')
        foreign_list = self.api('codex_threads_list', {}, self.stranger)['structuredContent']['data']
        self.assertNotIn(task, {item['id'] for item in foreign_list['tasks']})
        cancel = self.api('codex_task_cancel', {'task_id':task, 'request_key':'owner-cancel-'+task})
        self.assertFalse(cancel['isError'])
        self.assertEqual(cancel['structuredContent']['data']['status'], 'CANCELLED')
        self.assertNotIn('Keep private blocked input', repr(events))
        self.assertEqual(thread, read['thread_id'])

    def test_oauth_unsupported_effort_submit_retains_owner_access_and_no_generation(self):
        thread = self.compose()
        arguments = self.arguments(thread)
        reply = self.api('codex_task_submit', arguments)
        task = reply['structuredContent']['data']
        self.assertTrue(reply['isError'])
        self.assertEqual((task['state'], task['reason_code']), ('BLOCKED', 'REASONING_EFFORT_NOT_SUPPORTED'))
        self.assertEqual(task.get('access_binding'), 'BOUND')
        self.assertEqual(task.get('notification_binding'), 'BOUND')
        before = len(self.peer.calls)
        duplicate = self.api('codex_task_submit', arguments)['structuredContent']['data']
        self.assertEqual(duplicate['id'], task['id'])
        self.assertEqual(duplicate.get('access_binding'), 'BOUND')
        self.assertEqual(len(self.peer.calls), before)
        self.assert_owner_access(task['id'], thread)
        self.assertEqual(self.peer.count('turn/start'), 0)
        self.assertEqual(self.peer.count('turn/interrupt'), 0)

    def test_other_definitive_pre_turn_blockers_preserve_owner_access(self):
        thread = self.compose()
        for index, (kind, reason) in enumerate([
                ('unknown','MODEL_CATALOG_UNKNOWN'), ('rejected','CATALOG_REJECTED'),
                ('absent','MODEL_NOT_AVAILABLE_OR_ALLOWED'), ('resume','RESUME_REASONING_EFFORT_MISMATCH')]):
            with self.subTest(kind=kind):
                self.peer.failure.clear(); self.peer.models = [copy.deepcopy(MODEL)]
                self.peer.effort_echo = 'requested'
                if kind == 'unknown': self.peer.failure['model/list'] = TimeoutError('Synthetic private cause')
                if kind == 'rejected': self.peer.failure['model/list'] = True
                if kind == 'absent': self.peer.models = []
                if kind == 'resume': self.pipeline.loaded.clear(); self.peer.effort_echo = 'high'
                arguments = {**self.arguments(thread, 'blocked-'+str(index)), 'reasoning_effort':'medium'}
                reply = self.api('codex_task_submit', arguments)
                task = reply['structuredContent']['data']
                self.assertEqual(task['reason_code'], reason)
                self.assertTrue(reply['isError'])
                self.assertEqual(task.get('access_binding'), 'BOUND')
                self.assert_owner_access(task['id'], thread)
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_oauth_proxy_daemon_host_gateway_blocker_uses_same_readonly_ledger_proof(self):
        fixture = GatewayTests('test_metadata_session_closes_and_releases_runner_budget')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        daemon = Daemon.__new__(Daemon); daemon.allow_codex_pipeline = True; daemon.pipeline = fixture.gateway
        calls = []
        def route(runtime, method, params, *, timeout):
            self.assertEqual(runtime, fixture.root)
            self.assertEqual(method, 'codex_pipeline')
            calls.append(params['tool'])
            return daemon.handle(method, params)
        with patch('codex_host.request', side_effect=route):
            thread = self.compose(fixture=fixture, proxy=True)
            reply = self.api('codex_task_submit', self.arguments(thread))
            task = reply['structuredContent']['data']
            self.assertEqual(task['reason_code'], 'REASONING_EFFORT_NOT_SUPPORTED')
            self.assertEqual(task.get('access_binding'), 'BOUND')
            self.assertEqual(task.get('notification_binding'), 'BOUND')
            self.assert_owner_access(task['id'], thread)
        self.assertTrue(set(calls) <= {'codex_thread_start','codex_task_submit','codex_task_get',
            'codex_threads_list','codex_task_cancel'})
        self.assertEqual(fixture.peer.count('turn/start'), 0)
        fixture.gateway.release_idle(force=True)
        self.assertTrue(all(transport.closed for transport in fixture.transports))

    def test_unknown_failed_or_ambiguous_results_do_not_gain_owner_bindings(self):
        thread = self.compose()
        arguments, task = self.blocked_without_adapter(thread)
        variants = [
            {'status':'UNKNOWN'}, {'status':'FAILED'}, {'status':'BLOCKED'},
            {'state':'UNKNOWN'}, {'state':'FAILED','status':'FAILED'},
            {'id':task['id'], 'task_id':'00000000-0000-4000-8000-000000000099'},
            {'state':'BLOCKED', 'execution_started':True}, {'state':'BLOCKED','execution_started':0}]
        for change in variants:
            with self.subTest(change=change), patch.object(self.pipeline, 'dispatch', return_value={**task, **change}):
                reply = self.api('codex_task_submit', arguments)['structuredContent']['data']
                self.assertNotEqual(reply.get('access_binding'), 'BOUND')
                self.assertNotEqual(reply.get('notification_binding'), 'BOUND')
                self.assertFalse(self.outbox.task_owned_by(task['id'], self.owner))
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_forged_foreign_or_mismatched_submission_cannot_create_an_origin(self):
        thread = self.compose()
        arguments, task = self.blocked_without_adapter(thread)
        other = self.source.dispatch('codex_thread_start', {'workspace':'fixture','model':'offline-model',
            'reasoning_effort':'medium','request_key':'foreign-thread'})['thread_id']
        self.outbox.bind_thread_origin(other, self.stranger)
        foreign_args = {**self.arguments(other), 'request_key':'foreign-input'}
        foreign = self.source.dispatch('codex_task_submit', foreign_args)
        forged = {**foreign, 'thread_id':thread}
        variants = [forged, {**task, 'prompt_sha256':'0'*64}, {**task, 'selection_sha256':'0'*64}]
        for result in variants:
            with self.subTest(task=result['id']), patch.object(self.pipeline, 'dispatch', return_value=result):
                reply = self.api('codex_task_submit', arguments)['structuredContent']['data']
                self.assertNotEqual(reply.get('access_binding'), 'BOUND')
                self.assertFalse(self.outbox.task_owned_by(result['id'], self.owner))
        for change in ({'request_key':'different'}, {'prompt':'Different input'},
                       {'model':'offline-model'}, {'reasoning_effort':'medium'}):
            with self.subTest(change=change), patch.object(self.pipeline, 'dispatch', return_value=copy.deepcopy(task)):
                reply = self.api('codex_task_submit', {**arguments, **change})['structuredContent']['data']
                self.assertNotEqual(reply.get('access_binding'), 'BOUND')
                self.assertFalse(self.outbox.task_owned_by(task['id'], self.owner))
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_durable_input_operation_and_selection_provenance_are_required(self):
        thread = self.compose()
        arguments, task = self.blocked_without_adapter(thread)
        original = self.pipeline._mutate(lambda state: copy.deepcopy(state))
        changes = [lambda state: state['ops'][arguments['request_key']].update(status='UNKNOWN'),
            lambda state: state['ops'][arguments['request_key']].update(fingerprint='0'*64),
            lambda state: state['ops'][arguments['request_key']]['context'].update(task_id=thread),
            lambda state: state['ops'][arguments['request_key']]['context'].pop('selection_sha256'),
            lambda state: state['tasks'][task['id']].update(remote_turn='uncertain-turn'),
            lambda state: state['tasks'][task['id']].update(deadline=100),
            lambda state: state['tasks'][task['id']].update(state='UNKNOWN')]
        for change in changes:
            def restore(state): state.clear(); state.update(copy.deepcopy(original)); change(state)
            self.pipeline._mutate(restore)
            with patch.object(self.pipeline, 'dispatch', return_value=copy.deepcopy(task)):
                reply = self.api('codex_task_submit', arguments)['structuredContent']['data']
            self.assertNotEqual(reply.get('access_binding'), 'BOUND')
            self.assertFalse(self.outbox.task_owned_by(task['id'], self.owner))
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_existing_origin_and_events_authorizer_still_control_blocked_binding(self):
        thread = self.compose()
        arguments, task = self.blocked_without_adapter(thread)
        with patch.object(self.service, 'authorize_task', return_value=False), \
                patch.object(self.pipeline, 'dispatch', return_value=copy.deepcopy(task)):
            denied = self.api('codex_task_submit', arguments)['structuredContent']['data']
        self.assertNotEqual(denied.get('access_binding'), 'BOUND')
        self.assertFalse(self.outbox.task_owned_by(task['id'], self.owner))
        self.outbox.bind_task_origin(task['id'], self.stranger)
        with patch.object(self.pipeline, 'dispatch', return_value=copy.deepcopy(task)):
            conflict = self.api('codex_task_submit', arguments)['structuredContent']['data']
        self.assertEqual(conflict.get('access_binding'), 'UNAVAILABLE')
        self.assertEqual(conflict.get('notification_binding'), 'UNAVAILABLE')
        self.assertTrue(self.outbox.task_owned_by(task['id'], self.stranger))
        self.assertFalse(self.outbox.task_owned_by(task['id'], self.owner))
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_temporary_proof_failure_retry_binds_the_existing_input_without_replay(self):
        from notification_outbox import NotificationError
        thread = self.compose()
        arguments = self.arguments(thread)
        with patch.object(self.outbox, 'source_blocked_input_matches',
                side_effect=NotificationError('Synthetic private proof failure')):
            first = self.api('codex_task_submit', arguments)['structuredContent']['data']
        self.assertEqual(first['state'], 'BLOCKED')
        self.assertFalse(self.outbox.task_owned_by(first['id'], self.owner))
        before = self.pipeline._mutate(lambda state: copy.deepcopy(state))
        calls = len(self.peer.calls)
        retry = self.api('codex_task_submit', arguments)['structuredContent']['data']
        self.assertEqual(retry['id'], first['id'])
        self.assertEqual(retry.get('access_binding'), 'BOUND')
        self.assertTrue(self.outbox.task_owned_by(first['id'], self.owner))
        self.assertEqual(self.pipeline._mutate(lambda state: state), before)
        self.assertEqual(len(self.peer.calls), calls)
        self.assertNotIn('Synthetic private proof failure', json.dumps(first))
        self.assertEqual(self.peer.count('turn/start'), 0)

    def test_proof_read_lock_size_and_runtime_path_fail_closed_without_replay(self):
        thread = self.compose()
        arguments, task = self.blocked_without_adapter(thread)
        before = len(self.peer.calls)
        with closing(sqlite3.connect(self.pipeline.db)) as writer:
            writer.execute('BEGIN EXCLUSIVE')
            with patch.object(self.pipeline, 'dispatch', return_value=copy.deepcopy(task)):
                reply = self.api('codex_task_submit', arguments)['structuredContent']['data']
            self.assertNotEqual(reply.get('access_binding'), 'BOUND')
            writer.rollback()
        self.assertFalse(self.outbox.task_owned_by(task['id'], self.owner))
        source = self.outbox.source_path
        with patch.object(self.outbox, 'source_path', self.root / 'different-ledger.sqlite3'), \
                patch.object(self.pipeline, 'dispatch', return_value=copy.deepcopy(task)):
            reply = self.api('codex_task_submit', arguments)['structuredContent']['data']
        self.assertNotEqual(reply.get('access_binding'), 'BOUND')
        self.assertFalse(self.outbox.task_owned_by(task['id'], self.owner))
        with closing(sqlite3.connect(source)) as connection:
            raw = connection.execute('SELECT body FROM ledger WHERE id=1').fetchone()[0]
            connection.execute('UPDATE ledger SET body=? WHERE id=1', (raw.ljust(2*1024**2+1),))
            connection.commit()
        with patch.object(self.pipeline, 'dispatch', return_value=copy.deepcopy(task)):
            reply = self.api('codex_task_submit', arguments)['structuredContent']['data']
        self.assertNotEqual(reply.get('access_binding'), 'BOUND')
        self.assertFalse(self.outbox.task_owned_by(task['id'], self.owner))
        with closing(sqlite3.connect(source)) as connection:
            self.assertEqual(connection.execute('SELECT length(CAST(body AS BLOB)) FROM ledger WHERE id=1').fetchone()[0], 2*1024**2+1)
        self.assertEqual(len(self.peer.calls), before)


if __name__ == '__main__':
    from run_integrated_source_checks import run_checked
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    try:
        result = run_checked(suite, suite.countTestCases(), unittest.TextTestRunner(verbosity=2))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    print(json.dumps({'status':'passed', 'scope':'offline Codex catalog/effort source and fakes only', **result}))
