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

# Added 2026-11-11: portable model discovery and selection safety regressions.

"""Offline catalog contract tests. No Router, provider, model or Docker is started."""
import copy
import http.client
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from . import chat, cli, developer, repair

JOB = 'a' * 32
SMALL = 'qwen-small'


def catalog(models=None, **extra):
    return {'models': [SMALL] if models is None else models,
            'context_limit':8192, 'max_output_tokens':1024,
            'max_large_models_loaded':1, **extra}


def completed(model=SMALL):
    return {'id':JOB, 'model':model, 'status':'completed', 'result':{'content':'answer'}}


def generation_wire(advertised, calls, model=SMALL):
    def call(path, method, route, body=None):
        calls.append((path, method, route, copy.deepcopy(body)))
        if route == '/v1/models':
            return advertised
        if method == 'POST':
            return {'id':JOB}
        return completed(model)
    return call


class CatalogContractTests(unittest.TestCase):
    def test_normal_catalog_and_input_are_separate(self):
        raw = catalog(default_model=SMALL, flash_enabled=False)
        out = cli.checked_catalog(raw)
        self.assertEqual(out, raw)
        out['models'].append('other')
        self.assertEqual(raw['models'], [SMALL])

    def test_legacy_default_only_if_present(self):
        for models, expected in [(['flash','coder-next','gpt-oss-120b'], 'gpt-oss-120b'),
                                 ([SMALL], None), (['flash'], None)]:
            with self.subTest(models=models):
                out = cli.checked_catalog(catalog(models))
                self.assertEqual(out['default_model'], expected)
                self.assertEqual(out['flash_enabled'], 'flash' in models)

    def test_explicit_none_does_not_fall_back_to_legacy_default(self):
        self.assertIsNone(cli.checked_catalog(catalog(['gpt-oss-120b'], default_model=None))['default_model'])

    def test_valid_boundary_identifiers_and_catalog_size(self):
        models = ['a', 'b' * 64] + ['model-' + str(n) for n in range(7)]
        self.assertEqual(cli.checked_catalog(catalog(models))['models'], models)

    def test_exact_bounded_public_schema(self):
        invalid = [None, [], {}, {**catalog(), 'provider':'private'},
                   {k:v for k,v in catalog().items() if k != 'context_limit'}]
        for models in ([], [SMALL]*2, ['m'+str(n) for n in range(10)], 'flash', {},
                       [None], [[]], [''], ['a'*65], ['Upper'], ['9start'],
                       ['a_b'], ['path/model'], ['a:tag'], ['a\n'], ['caf\u00e9']):
            invalid.append(catalog(models))
        for key, wrong in [('context_limit',8193), ('context_limit',8192.0),
                           ('max_output_tokens',True), ('max_output_tokens',2048),
                           ('max_large_models_loaded',True), ('max_large_models_loaded',2),
                           ('default_model','missing'), ('default_model',False),
                           ('default_model',[]), ('flash_enabled',1), ('flash_enabled',None),
                           ('flash_enabled',True)]:
            invalid.append(catalog(**{key:wrong}))
        invalid.append(catalog(['flash'], flash_enabled=False))
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(cli.ClientError):
                cli.checked_catalog(raw)

    def test_checked_job_requires_discovered_membership_and_exact_selection(self):
        known = cli.checked_catalog(catalog())
        self.assertEqual(cli.checked_job(completed(), JOB, known, SMALL)['id'], JOB)
        for bad in (completed('unconfigured'), completed('flash'),
                    {**completed(), 'id':'b'*32}, {**completed(), 'model':[]}):
            with self.subTest(bad=bad), self.assertRaises(cli.ClientError):
                cli.checked_job(bad, JOB, known, SMALL)


class SubmissionDiscoveryTests(unittest.TestCase):
    def test_explicit_small_model_and_exact_body_use_same_socket(self):
        calls = []
        out = cli.ask('/chosen/private.sock', SMALL, 'hello', 64, 10,
                      call=generation_wire(catalog(default_model=None), calls))
        self.assertEqual(out['model'], SMALL)
        self.assertFalse(out['automatic_retry'])
        self.assertEqual([c[1:3] for c in calls],
                         [('GET','/v1/models'), ('POST','/v1/jobs'), ('GET','/v1/jobs/'+JOB)])
        self.assertTrue(all(c[0] == '/chosen/private.sock' for c in calls))
        self.assertEqual(calls[1][3], {'model':SMALL, 'messages':[{'role':'user','content':'hello'}],
                                      'max_tokens':64, 'timeout_seconds':10})

    def test_default_is_selected_from_current_catalog_for_each_job(self):
        calls = []
        for selected in (SMALL, 'other-small'):
            out = cli.ask('/unused', None, 'hello',
                          call=generation_wire(catalog([selected], default_model=selected), calls, selected))
            self.assertEqual(out['model'], selected)
        self.assertEqual(sum(c[2] == '/v1/models' for c in calls), 2)
        self.assertEqual([c[3]['model'] for c in calls if c[1] == 'POST'], [SMALL, 'other-small'])

    def test_removed_selection_is_rechecked_without_post_or_fallback(self):
        calls = []
        cli.ask('/unused', SMALL, 'first', call=generation_wire(catalog(), calls))
        with self.assertRaisesRegex(cli.ClientError, 'no request submitted'):
            cli.ask('/unused', SMALL, 'second', call=generation_wire(catalog(['flash']), calls))
        self.assertEqual(sum(c[1] == 'POST' for c in calls), 1)
        self.assertEqual(calls[-1][1:3], ('GET','/v1/models'))

    def test_no_default_never_picks_an_arbitrary_or_legacy_model(self):
        for raw in (catalog(), catalog(['gpt-oss-120b'], default_model=None)):
            calls = []
            with self.subTest(raw=raw), self.assertRaisesRegex(cli.ClientError, 'no request submitted'):
                cli.ask('/unused', None, 'hello', call=generation_wire(raw, calls))
            self.assertEqual([c[1:3] for c in calls], [('GET','/v1/models')])

    def test_legacy_default_remains_compatible(self):
        calls = []
        out = cli.ask('/unused', None, 'hello', call=generation_wire(
            catalog(['gpt-oss-120b']), calls, 'gpt-oss-120b'))
        self.assertEqual(out['model'], 'gpt-oss-120b')

    def test_failed_discovery_never_submits_or_claims_unknown_submission(self):
        for failure in (TimeoutError(), OSError(), ValueError(), http.client.HTTPException(),
                        cli.ClientError('bad JSON')):
            call = Mock(side_effect=failure)
            with self.subTest(failure=failure), self.assertRaises(cli.ClientError) as caught:
                cli.ask('/unused', 'flash', 'hello', call=call)
            self.assertIn('no request submitted', str(caught.exception))
            self.assertNotIn('outcome unknown', str(caught.exception))
            self.assertIsNone(caught.exception.job_id)
            call.assert_called_once_with('/unused','GET','/v1/models')

    def test_malformed_catalog_never_submits(self):
        call = Mock(return_value={**catalog(), 'secret_provider_option':'private'})
        with self.assertRaisesRegex(cli.ClientError, 'no request submitted'):
            cli.ask('/unused', SMALL, 'hello', call=call)
        self.assertEqual(call.call_count, 1)

    def test_all_invalid_request_input_fails_before_discovery(self):
        messages = [{'role':'user','content':'hello'}]
        cases = [(None, [], 64, 10), (None, [{'role':'tool','content':'x'}], 64, 10),
                 (SMALL, [{'role':'user','content':'x'*8192}], 64, 10),
                 (SMALL, messages, True, 10), (SMALL, messages, 64, 301),
                 ('bad/id', messages, 64, 10), ([], messages, 64, 10),
                 ('a'*65, messages, 64, 10), (SMALL, [{'role':'user','content':'\ud800'}], 64, 10)]
        for model, rows, tokens, timeout in cases:
            call = Mock()
            with self.subTest(model=model, tokens=tokens, timeout=timeout), self.assertRaises(cli.ClientError):
                cli.converse('/unused', model, rows, tokens, timeout, call=call)
            call.assert_not_called()
        for text in ('', '  ', 'x'*8193, '\ud800'):
            call = Mock()
            with self.subTest(text=text), self.assertRaises(cli.ClientError):
                cli.ask('/unused', None, text, call=call)
            call.assert_not_called()

    def test_post_failure_is_still_unknown_and_never_retried(self):
        for failure in (TimeoutError(), cli.ClientError('unavailable'), ValueError()):
            call = Mock(side_effect=[catalog(default_model=SMALL), failure])
            with self.subTest(failure=failure), self.assertRaisesRegex(cli.ClientError, 'submission outcome unknown') as caught:
                cli.ask('/unused', None, 'hello', call=call)
            self.assertNotIsInstance(caught.exception, cli.ClientNotSubmitted)
            self.assertEqual(call.call_count, 2)
            self.assertEqual([c.args[1] for c in call.call_args_list], ['GET','POST'])

    def test_poll_model_cannot_change_even_to_another_catalog_member(self):
        calls = []
        with self.assertRaisesRegex(cli.ClientError, 'job identity mismatch') as caught:
            cli.ask('/unused', SMALL, 'hello', call=generation_wire(
                catalog([SMALL,'other-small']), calls, 'other-small'))
        self.assertEqual(caught.exception.job_id, JOB)
        self.assertEqual(sum(c[1] == 'POST' for c in calls), 1)

    def test_wait_deadline_still_preserves_job_and_does_not_resubmit(self):
        call = Mock(side_effect=[catalog(default_model=SMALL), {'id':JOB}])
        with patch.object(cli.time, 'monotonic', side_effect=[0, 400]):
            with self.assertRaisesRegex(cli.ClientError, 'local wait deadline exceeded') as caught:
                cli.ask('/unused', None, 'hello', call=call)
        self.assertEqual(caught.exception.job_id, JOB)
        self.assertEqual(call.call_count, 2)

    def test_discovery_and_submission_use_existing_checked_transport(self):
        payloads = [catalog(default_model=SMALL), {'id':JOB}, completed()]
        connections = []
        for payload in payloads:
            response = Mock(status=200)
            response.read.return_value = json.dumps(payload).encode()
            connection = Mock()
            connection.getresponse.return_value = response
            connections.append(connection)
        with patch.object(cli, 'checked_socket', return_value='/checked/private.sock') as checked, \
             patch.object(cli, 'Connection', side_effect=connections) as transport:
            out = cli.ask('/input/private.sock', None, 'hello')
        self.assertEqual(out['model'], SMALL)
        self.assertEqual(checked.call_count, 3)
        self.assertTrue(all(c.args == ('/input/private.sock',) for c in checked.call_args_list))
        self.assertTrue(all(c.args[0] == '/checked/private.sock' for c in transport.call_args_list))
        self.assertEqual([c.request.call_args.args[:2] for c in connections],
                         [('GET','/v1/models'),('POST','/v1/jobs'),('GET','/v1/jobs/'+JOB)])
        for connection in connections:
            connection.close.assert_called_once()


class CommandCatalogTests(unittest.TestCase):
    def invoke(self, arguments, call):
        with patch.object(cli, 'request', side_effect=call) as requested, \
             patch('sys.stdout', new_callable=io.StringIO) as out, \
             patch('sys.stderr', new_callable=io.StringIO) as err:
            code = cli.main(['--socket','/unused', *arguments])
        return code, out.getvalue(), err.getvalue(), requested

    def test_ask_cli_accepts_small_selection_and_omitted_default(self):
        for args in (['--model', SMALL], []):
            calls = []
            code, out, err, _ = self.invoke(['ask', *args, '--prompt','hello'],
                generation_wire(catalog(default_model=SMALL), calls))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)['model'], SMALL)
            self.assertEqual(err, '')

    def test_models_command_rejects_malformed_catalog(self):
        code, out, err, call = self.invoke(['models'], lambda *a:{'models':[SMALL]})
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertFalse(json.loads(err)['automatic_retry'])
        self.assertEqual(call.call_count, 1)

    def test_status_reports_real_catalog_and_default_without_private_metadata(self):
        def call(path, method, route, body=None):
            if route == '/v1/models':
                return catalog(default_model=SMALL, flash_enabled=False)
            return {'status':'ready','gpu':{'flash_resident':False, 'ollama_selected':SMALL,
                                           'endpoint':'private'},'npu':{},'queue_depth':0}
        with patch('inference.local_platform.model_management.local_status', return_value={}):
            code, out, err, _ = self.invoke(['status'], call)
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result['models'], [SMALL])
        self.assertEqual(result['default_model'], SMALL)
        self.assertFalse(result['flash_enabled'])
        self.assertEqual(result['selected_model'], SMALL)
        self.assertNotIn('private', out)

    def test_output_file_guard_still_precedes_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'existing.json'; output.write_text('unchanged')
            code, _, err, call = self.invoke(['ask','--prompt','hello','--output',str(output)], lambda *a:None)
            self.assertEqual(code, 1)
            call.assert_not_called()
            self.assertEqual(output.read_text(), 'unchanged')
            self.assertIn('not submitted', json.loads(err)['error'])

    def test_main_models_emits_only_validated_public_catalog(self):
        code, out, err, call = self.invoke(['models'], lambda *a:catalog(default_model=SMALL))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), catalog(default_model=SMALL, flash_enabled=False))
        self.assertEqual(call.call_count, 1)

    def test_get_validates_small_model_with_catalog(self):
        calls = []
        code, out, err, _ = self.invoke(['get',JOB], generation_wire(catalog(),calls))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['model'], SMALL)
        self.assertEqual([c[1:3] for c in calls], [('GET','/v1/models'),('GET','/v1/jobs/'+JOB)])

    def test_get_preserves_requested_job_on_discovery_or_retrieval_failure(self):
        for discovery_failure in (True, False):
            def call(path, method, route, body=None):
                if route == '/v1/models' and not discovery_failure:
                    return catalog()
                raise TimeoutError()
            code, out, err, requested = self.invoke(['get',JOB], call)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(err)['job_id'], JOB)
            self.assertEqual(requested.call_count, 1 if discovery_failure else 2)

    def test_get_error_cannot_replace_requested_job_identifier(self):
        def call(path, method, route, body=None):
            if route == '/v1/models': return catalog()
            raise cli.ClientError('retrieval failed', 'b'*32)
        code, out, err, _ = self.invoke(['get',JOB], call)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)['job_id'], JOB)

    def test_discovery_interrupt_reports_known_no_submission_and_exit_130(self):
        def call(*args): raise KeyboardInterrupt()
        code, out, err, requested = self.invoke(['ask','--prompt','hello'], call)
        self.assertEqual(code, 130)
        result = json.loads(err)
        self.assertEqual(result['status'], 'client_wait_interrupted')
        self.assertFalse(result['submission_attempted'])
        self.assertIn('no request submitted', result['error'])
        self.assertIsNone(result['job_id'])
        requested.assert_called_once_with('/unused','GET','/v1/models')

    def test_invalid_job_id_never_discovers(self):
        code, out, err, call = self.invoke(['get','invalid'], lambda *a:None)
        self.assertEqual(code, 1)
        call.assert_not_called()


class ConversationSelectionTests(unittest.TestCase):
    def test_bare_help_quit_and_all_command_help_remain_offline(self):
        with patch.object(cli, 'request') as request:
            for argv in ([], ['chat']):
                with patch('sys.stdin', io.StringIO('/help\n/quit\n')), \
                     patch('sys.stdout', io.StringIO()), patch('sys.stderr', io.StringIO()):
                    self.assertEqual(cli.main(argv), 0)
            for command in (None, 'chat','ask','repair','models','status','get','cancel','capabilities','classify','model'):
                with patch('sys.stdout', io.StringIO()), self.assertRaises(SystemExit) as caught:
                    cli.main(([command] if command else []) + ['--help'])
                self.assertEqual(caught.exception.code, 0)
            request.assert_not_called()

    def test_default_is_pinned_after_first_generation_even_after_reset(self):
        calls = []
        def wire(path, method, route, body=None):
            calls.append((method, route, copy.deepcopy(body)))
            if route == '/v1/models':
                default = SMALL if len(calls) == 1 else 'other-small'
                return catalog([SMALL,'other-small'], default_model=default)
            if method == 'POST':
                return {'id':JOB}
            return completed()
        with patch.object(cli, 'request', side_effect=wire):
            code = chat.run_chat('/unused', None, input_stream=io.StringIO('one\ntwo\n/reset\nthree\n/quit\n'),
                                 output_stream=io.StringIO(), error_stream=io.StringIO())
        self.assertEqual(code, 0)
        bodies = [body for method, _, body in calls if method == 'POST']
        self.assertEqual([body['model'] for body in bodies], [SMALL]*3)
        self.assertEqual(len(bodies[1]['messages']), 3)
        self.assertEqual(bodies[2]['messages'], [{'role':'user','content':'three'}])
        self.assertEqual(sum(route == '/v1/models' for _, route, _ in calls), 3)

    def test_discovery_interrupt_stops_chat_with_no_cancellation_request(self):
        error = io.StringIO()
        with patch.object(cli, 'request', side_effect=KeyboardInterrupt()) as call:
            code = chat.run_chat('/unused', None, input_stream=io.StringIO('one\ntwo\n'),
                                 output_stream=io.StringIO(), error_stream=error)
        self.assertEqual(code, 130)
        self.assertIn('no request submitted', error.getvalue())
        call.assert_called_once_with('/unused','GET','/v1/models')

    def test_truncated_first_answer_still_pins_selected_default(self):
        models = []
        def generate(path, model, messages, max_tokens, timeout):
            models.append(model)
            return {'model':SMALL, 'content':'partial', 'finish_reason':'length'}
        code = chat.run_chat('/unused', None, input_stream=io.StringIO('one\ntwo\n/quit\n'),
                             output_stream=io.StringIO(), error_stream=io.StringIO(), generate=generate)
        self.assertEqual(code, 0)
        self.assertEqual(models, [None, SMALL])

    def test_explicit_small_model_cli_chat_routes_offline(self):
        with patch.object(chat, 'run_chat', return_value=0) as run, patch.object(cli, 'request') as call:
            self.assertEqual(cli.main(['--socket','/unused','chat','--model',SMALL]), 0)
        run.assert_called_once_with('/unused', SMALL, 512, 180, None)
        call.assert_not_called()

    def test_removed_chat_model_stops_without_using_changed_default(self):
        calls = []
        def wire(path, method, route, body=None):
            calls.append((method, route))
            if route == '/v1/models':
                return catalog(default_model=SMALL) if len(calls) == 1 else catalog(['other-small'], default_model='other-small')
            return {'id':JOB} if method == 'POST' else completed()
        with patch.object(cli, 'request', side_effect=wire):
            code = chat.run_chat('/unused', None, input_stream=io.StringIO('one\ntwo\n'),
                                 output_stream=io.StringIO(), error_stream=io.StringIO())
        self.assertEqual(code, 1)
        self.assertEqual(sum(method == 'POST' for method, _ in calls), 1)
        self.assertEqual(calls[-1], ('GET','/v1/models'))


class WorkflowSelectionTests(unittest.TestCase):
    def test_repair_small_model_preserves_tests_and_uses_catalog(self):
        source = 'def add(a,b):\n    return a-b\n'
        cases = [{'function':'add','args':[2,3],'expected':5}]
        calls = []
        def wire(path, method, route, body=None):
            calls.append((method, route, copy.deepcopy(body)))
            if route == '/v1/models': return catalog()
            if method == 'POST': return {'id':JOB}
            return {**completed(), 'result':{'content':json.dumps({
                'replacement':source.replace('a-b','a+b'), 'explanation':'candidate'})}}
        with tempfile.TemporaryDirectory() as folder, patch.object(cli, 'request', side_effect=wire):
            root = Path(folder)/'new'
            out = repair.repair('/unused',source,cases,root,model=SMALL)
            self.assertTrue(out['tests_passed'])
            self.assertEqual(json.loads((root/'cases.json').read_text()), cases)
            self.assertIn('a - b', (root/'source.before.py.txt').read_text())
        self.assertEqual([m for m, _, _ in calls], ['GET','POST','GET'])
        self.assertEqual(calls[1][2]['model'], SMALL)

    def test_repair_discovery_failure_stops_without_uncertain_submit_or_retry(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cli, 'request', side_effect=TimeoutError()) as call:
            result = repair.repair('/unused','def add(a,b):\n    return a-b\n',
                [{'function':'add','args':[2,3],'expected':5}],Path(folder)/'new',model=SMALL)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(len(result['attempts']), 1)
        self.assertFalse(result['attempts'][0]['submission_attempted'])
        self.assertIn('no request submitted', result['attempts'][0]['error_detail'])

    def test_workflow_cli_accepts_explicit_small_ids_without_new_options(self):
        from .test_developer import manifest
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'source.py'; source.write_text('def add(a,b):\n    return a+b\n')
            cases = root/'cases.json'; cases.write_text('[]')
            plan = root/'manifest.json'; plan.write_text(json.dumps(manifest()))
            with patch.object(repair, 'repair', return_value={'tests_passed':True}) as run, patch('sys.stdout', io.StringIO()):
                self.assertEqual(repair.main(['--socket','/unused','--source',str(source),'--cases',str(cases),
                    '--workdir',str(root/'out'),'--model',SMALL]), 0)
            self.assertEqual(run.call_args.args[4], SMALL)
            with patch.object(repair, 'main', return_value=0) as run:
                self.assertEqual(cli.main(['repair','--source',str(source),'--cases',str(cases),
                    '--workdir',str(root/'out'),'--model',SMALL]), 0)
            self.assertIn(SMALL, run.call_args.args[0])
            with patch.object(developer, 'develop', return_value={'status':'completed'}) as run, \
                 patch('inference.local_platform.project_runner.ContainerRunner'), patch('sys.stdout', io.StringIO()):
                self.assertEqual(developer.main(['--socket','/unused','--source',str(root),'--manifest',str(plan),
                    '--workdir',str(root/'out'),'--model',SMALL,'--test-image','sha256:'+'a'*64]), 0)
            self.assertEqual(run.call_args.args[5], SMALL)

    def test_developer_discovery_failure_is_known_not_submitted(self):
        from .test_developer import Runner, manifest
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'source'; source.mkdir(); (source/'tests').mkdir()
            (source/'code.py').write_text('def add(a,b):\n    return a-b\n')
            (source/'tests/test_code.py').write_text('# immutable tests\n')
            with patch.object(cli, 'request', side_effect=TimeoutError()) as call:
                out = developer.develop('/unused',source,manifest(),root/'run',Runner(),model=SMALL)
        self.assertEqual(out['status'], 'failed')
        self.assertIsNone(out['job_id'])
        self.assertIn('no request submitted', out['reason'])
        self.assertEqual(call.call_count, 1)

    def test_developer_discovery_interrupt_is_not_an_uncertain_inference(self):
        from .test_developer import Runner, manifest
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'source'; source.mkdir(); (source/'tests').mkdir()
            (source/'code.py').write_text('def add(a,b):\n    return a-b\n')
            (source/'tests/test_code.py').write_text('# immutable tests\n')
            with patch.object(cli, 'request', side_effect=KeyboardInterrupt()) as call:
                out = developer.develop('/unused',source,manifest(),root/'run',Runner(),model=SMALL)
        self.assertEqual(out['status'], 'cancelled')
        self.assertFalse(out['submission_attempted'])
        self.assertIn('no request submitted', out['reason'])
        self.assertNotIn('stop_confirmed', out)
        self.assertEqual(call.call_count, 1)

    def test_repair_discovery_interrupt_reports_no_submission_and_exit_130(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'source.py'; source.write_text('def add(a,b):\n    return a-b\n')
            cases = root/'cases.json'; cases.write_text(json.dumps([{'function':'add','args':[2,3],'expected':5}]))
            with patch.object(cli, 'request', side_effect=KeyboardInterrupt()) as call, \
                 patch('sys.stderr', new_callable=io.StringIO) as err:
                code = repair.main(['--socket','/unused','--source',str(source),'--cases',str(cases),
                    '--workdir',str(root/'out'),'--model',SMALL])
        self.assertEqual(code, 130)
        report = json.loads(err.getvalue())
        self.assertEqual(report['status'], 'client_wait_interrupted')
        self.assertFalse(report['submission_attempted'])
        self.assertIn('no request submitted', report['error'])
        call.assert_called_once_with('/unused','GET','/v1/models')

    def test_developer_small_model_keeps_snapshot_and_readonly_test_boundary(self):
        from .test_developer import Runner, manifest
        calls = []
        def wire(path, method, route, body=None):
            calls.append((method, route))
            if route == '/v1/models': return catalog()
            if method == 'POST': return {'id':JOB}
            return {**completed(), 'result':{'content':json.dumps({
                'tool':'finish','arguments':{'summary':'unverified'}})}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'source'; source.mkdir(); (source/'tests').mkdir()
            (source/'code.py').write_text('def add(a,b):\n    return a-b\n')
            test = source/'tests/test_code.py'; test.write_text('# immutable tests\n')
            with patch.object(cli, 'request', side_effect=wire):
                out = developer.develop('/unused',source,manifest(),root/'run',Runner(),model=SMALL,max_turns=1)
            self.assertEqual(out['status'], 'failed')
            self.assertEqual(out['changes'], [])
            self.assertEqual(test.read_text(), '# immutable tests\n')
            self.assertEqual((root/'run/workspace/tests/test_code.py').read_text(), test.read_text())
            self.assertFalse((root/'run/workspace/tests/test_code.py').stat().st_mode & 0o222)
        self.assertEqual(calls, [('GET','/v1/models'),('POST','/v1/jobs'),('GET','/v1/jobs/'+JOB)])


if __name__ == '__main__':
    unittest.main()
