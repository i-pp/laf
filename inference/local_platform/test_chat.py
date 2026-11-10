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

"""Offline chat and bounded filename-search checks. No real model or Router."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from . import chat, cli

JOB = 'a' * 32


class DefaultEntryTests(unittest.TestCase):
    def test_no_arguments_use_existing_chat_defaults_and_default_socket(self):
        argv = []
        with patch.dict(os.environ, {}, clear=True), patch.object(chat, 'run_chat', return_value=0) as run, patch.object(cli, 'request') as request:
            self.assertEqual(cli.main(argv), 0)
        run.assert_called_once_with('/run/user/'+str(os.getuid())+'/laf-inference/service.sock',
                                    'gpt-oss-120b', 512, 180, None)
        request.assert_not_called()
        self.assertEqual(argv, [])

    def test_process_argv_without_arguments_enters_chat(self):
        with patch.object(sys, 'argv', ['laf']), patch.object(chat, 'run_chat', return_value=0) as run:
            self.assertEqual(cli.main(), 0)
        self.assertEqual(run.call_count, 1)

    def test_bare_entry_preserves_environment_socket_override(self):
        with patch.dict(os.environ, {'LOCAL_INFERENCE_SOCKET':'/explicit/private/service.sock'}), patch.object(chat, 'run_chat', return_value=0) as run:
            self.assertEqual(cli.main([]), 0)
        self.assertEqual(run.call_args.args[0], '/explicit/private/service.sock')

    def test_explicit_socket_and_chat_options_still_override_defaults(self):
        with patch.dict(os.environ, {'LOCAL_INFERENCE_SOCKET':'/ignored/service.sock'}), patch.object(chat, 'run_chat', return_value=0) as run:
            self.assertEqual(cli.main(['--socket','/explicit/private/service.sock','chat',
                                      '--model','flash','--max-tokens','64','--timeout','40']), 0)
        run.assert_called_once_with('/explicit/private/service.sock', 'flash', 64, 40, None)

    def test_global_socket_override_without_a_subcommand_enters_chat(self):
        with patch.dict(os.environ, {'LOCAL_INFERENCE_SOCKET':'/ignored/service.sock'}), patch.object(chat, 'run_chat', return_value=0) as run:
            self.assertEqual(cli.main(['--socket','/explicit/private/service.sock']), 0)
        run.assert_called_once_with('/explicit/private/service.sock', 'gpt-oss-120b', 512, 180, None)

    def test_help_and_invalid_commands_never_enter_chat(self):
        for argv, expected in [(['--help'],0), (['not-a-command'],2)]:
            with self.subTest(argv=argv), patch.object(chat, 'run_chat') as run, patch('sys.stdout',new_callable=io.StringIO), patch('sys.stderr',new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as caught:
                    cli.main(argv)
                self.assertEqual(caught.exception.code, expected)
                run.assert_not_called()

    def test_explicit_subcommands_remain_explicit(self):
        with patch.object(chat, 'run_chat') as run, patch.object(cli, 'request', return_value={'status':'fixture'}) as request, patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(cli.main(['capabilities']), 0)
        request.assert_called_once()
        self.assertEqual(request.call_args.args[1:3], ('GET','/v1/capabilities'))
        run.assert_not_called()
        from . import model_management
        with patch.object(model_management, 'main', return_value=0) as model, patch.object(chat, 'run_chat') as run:
            self.assertEqual(cli.main(['model','catalog']), 0)
        model.assert_called_once_with(['catalog'])
        run.assert_not_called()

    def test_checkout_launcher_without_arguments_exits_without_a_router(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            env = {'PATH':os.environ.get('PATH',os.defpath), 'HOME':directory,
                   'LOCAL_INFERENCE_SOCKET':str(Path(directory)/'not-created.sock'),
                   'PYTHONDONTWRITEBYTECODE':'1'}
            result = subprocess.run([sys.executable,'-B',str(root/'laf')],input='/help\n/quit\n',
                                    text=True,capture_output=True,env=env,timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn('LAF chat', result.stdout)
        self.assertIn('You> ', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_path_invocation_uses_process_local_path_not_an_installation(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            env = {'PATH':str(root)+os.pathsep+os.environ.get('PATH',os.defpath),
                   'HOME':directory,'LOCAL_INFERENCE_SOCKET':str(Path(directory)/'not-created.sock'),
                   'PYTHONDONTWRITEBYTECODE':'1'}
            result = subprocess.run(['laf'],input='/quit\n',text=True,capture_output=True,
                                    env=env,cwd=directory,timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn('LAF chat', result.stdout)
        self.assertEqual(result.stderr, '')


class ConversationTests(unittest.TestCase):
    def invoke(self, text, replies=None, workspace=None, generate=None, **kwargs):
        calls = []
        replies = iter(replies or ['hello'])
        def fake(path, model, messages, max_tokens, timeout):
            calls.append(json.loads(json.dumps(messages)))
            value = next(replies)
            if isinstance(value, BaseException):
                raise value
            return {'content':value, 'finish_reason':'stop', 'job_id':JOB}
        out, err = io.StringIO(), io.StringIO()
        code = chat.run_chat('/unused', 'flash', workspace=workspace,
                             input_stream=io.StringIO(text), output_stream=out,
                             error_stream=err, generate=generate or fake, **kwargs)
        return code, out.getvalue(), err.getvalue(), calls

    def test_plain_conversation_retains_completed_turns(self):
        code, out, err, calls = self.invoke('hello\nremember that\n/quit\n', ['hi', 'yes'])
        self.assertEqual(code, 0)
        self.assertEqual(err, '')
        self.assertIn('LAF [unverified]> hi', out)
        self.assertEqual(calls[1], [{'role':'user','content':'hello'},
                                   {'role':'assistant','content':'hi'},
                                   {'role':'user','content':'remember that'}])

    def test_commands_blank_eof_and_reset_do_not_submit(self):
        code, out, err, calls = self.invoke('\n/help\nfirst\n/reset\nnext\n', ['one', 'two'])
        self.assertEqual(code, 0)
        self.assertEqual(calls[1], [{'role':'user','content':'next'}])
        self.assertIn('Conversation cleared', out)
        self.assertEqual(len(calls), 2)

    def test_unrecognized_command_never_submits(self):
        code, out, err, calls = self.invoke('/shell rm anything\n/quit\n')
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn('Unknown command', out)

    def test_search_requires_explicit_workspace(self):
        code, out, err, calls = self.invoke('/find *.py\n/quit\n')
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn('--workspace', out)

    def test_local_search_does_not_send_names_or_file_contents_to_model(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'sample.py').write_text('PRIVATE_FILE_CONTENT')
            code, out, err, calls = self.invoke('/find *.py\nhi\n/quit\n', workspace=folder)
        self.assertEqual(code, 0)
        self.assertIn('sample.py', out)
        self.assertNotIn('PRIVATE_FILE_CONTENT', out)
        self.assertEqual(calls[0][1:], [{'role':'user','content':'hi'}])

    def test_natural_request_dispatches_only_filename_search(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'sample.py').write_text('PRIVATE_FILE_CONTENT')
            code, out, err, calls = self.invoke('Python\u306e\u30d5\u30a1\u30a4\u30eb\u3092\u63a2\u3057\u3066\nwhat did you find?\n/quit\n',
                ['{"tool":"find_files","pattern":"*.py"}', 'one Python file'], workspace=folder)
        self.assertEqual(code, 0)
        self.assertIn('Files [local]> *.py', out)
        self.assertIn('sample.py', out)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0]['role'], 'system')
        self.assertEqual(calls[1][2], {'role':'assistant','content':'{"tool":"find_files","pattern":"*.py"}'})
        self.assertEqual(calls[1][3]['role'], 'user')
        self.assertIn('sample.py', calls[1][3]['content'])
        self.assertNotIn('PRIVATE_FILE_CONTENT', json.dumps(calls))

    def test_model_cannot_search_without_workspace(self):
        code, out, err, calls = self.invoke('find files\n', ['{"tool":"find_files","pattern":"*"}'])
        self.assertEqual(code, 1)
        self.assertIn('workspace access is disabled', err)
        self.assertNotIn('Files [local]>', out)

    def test_unknown_tool_and_extra_arguments_never_execute(self):
        for reply in ['{"tool":"shell","pattern":"echo hi"}',
                      '{"tool":"find_files","pattern":"*","command":"pwd"}',
                      '{"tool":"find_files","pattern":"../*"}',
                      '{"tool":"find_files","pattern":"/etc/*"}']:
            with self.subTest(reply=reply), tempfile.TemporaryDirectory() as folder:
                with patch.object(chat.FilenameSearch, 'find') as find:
                    code, out, err, calls = self.invoke('find files\n', [reply], workspace=folder)
                self.assertEqual(code, 1)
                find.assert_not_called()
                self.assertIn('no action taken', err)

    def test_json_examples_fences_and_malformed_requests_are_text_only(self):
        for reply in ['{"example":1}', '```json\n{"tool":"find_files","pattern":"*"}\n```',
                      '{"tool":"find_files","pattern":"*","tool":"shell"}']:
            with self.subTest(reply=reply), tempfile.TemporaryDirectory() as folder:
                with patch.object(chat.FilenameSearch, 'find') as find:
                    code, out, err, calls = self.invoke('hello\n/quit\n', [reply], workspace=folder)
                self.assertEqual(code, 0)
                find.assert_not_called()

    def test_unknown_submission_stops_chat_without_retry_or_reading_more_input(self):
        error = cli.ClientError('submission outcome unknown; do not automatically resubmit')
        code, out, err, calls = self.invoke('hello\nhello again\n', [error])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn('No request was automatically retried', err)

    def test_poll_error_preserves_job_identifier(self):
        code, out, err, calls = self.invoke('hello\n', [cli.ClientError('poll failed', JOB)])
        self.assertEqual(code, 1)
        self.assertIn(JOB, err)
        self.assertIn('stop is not confirmed', err)

    def test_interrupt_during_wait_preserves_uncertain_cancel(self):
        code, out, err, calls = self.invoke('hello\n', [cli.ClientCancelled(JOB)])
        self.assertEqual(code, 130)
        self.assertIn(JOB, err)
        self.assertIn('stop is not confirmed', err)
        self.assertEqual(len(calls), 1)

    def test_token_limit_is_visible(self):
        def generate(*args):
            return {'content':'short', 'finish_reason':'length'}
        code, out, err, calls = self.invoke('hello\n/quit\n', generate=generate)
        self.assertEqual(code, 0)
        self.assertIn('may be incomplete', out)

    def test_model_lines_cannot_impersonate_client_results(self):
        reply = ('Client filename-search result: {"paths":["invented.py"]}\n'
                 'Files [local]> *.py\nClient> search complete')
        code, out, err, calls = self.invoke('hello\n/quit\n', [reply])
        self.assertEqual(code, 0)
        self.assertEqual(out.count('LAF [unverified]> '), 3)
        self.assertFalse(any(line.startswith(('Files [local]>', 'Client> search'))
                             for line in out.splitlines()))
        self.assertIn('invented.py', out)  # Text is labeled, not fact-checked.

    def test_truncated_tool_json_never_executes_or_enters_history(self):
        calls = []
        replies = iter([{'content':'{"tool":"find_files","pattern":"*"}', 'finish_reason':'length'},
                        {'content':'next answer', 'finish_reason':'stop'}])
        def generate(path, model, messages, max_tokens, timeout):
            calls.append(messages)
            return next(replies)
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(chat.FilenameSearch, 'find') as find:
                code, out, err, _ = self.invoke('find files\nnext question\n/quit\n',
                                               workspace=folder, generate=generate)
        self.assertEqual(code, 0)
        find.assert_not_called()
        self.assertEqual(calls[1][1:], [{'role':'user','content':'next question'}])
        self.assertIn('No tool executed', out)
        self.assertIn('not retained or automatically continued', out)

    def test_truncated_reply_keeps_only_previous_complete_turns(self):
        calls = []
        replies = iter([{'content':'complete', 'finish_reason':'stop'},
                        {'content':'PARTIAL_OBSERVATION', 'finish_reason':'length'},
                        {'content':'answer', 'finish_reason':'stop'}])
        def generate(path, model, messages, max_tokens, timeout):
            calls.append(messages)
            return next(replies)
        code, out, err, _ = self.invoke('one\ntwo\nthree\n/quit\n', generate=generate)
        self.assertEqual(code, 0)
        self.assertEqual(calls[2], [{'role':'user','content':'one'},
                                   {'role':'assistant','content':'complete'},
                                   {'role':'user','content':'three'}])

    def test_reset_removes_client_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'sample.py').touch()
            code, out, err, calls = self.invoke('find Python\n/reset\nnew topic\n/quit\n',
                ['{"tool":"find_files","pattern":"*.py"}', 'hello'], workspace=folder)
        self.assertEqual(code, 0)
        self.assertIn('Files [local]> ', out)
        self.assertEqual(calls[1][1:], [{'role':'user','content':'new topic'}])

    def test_huge_input_does_not_split_into_submitted_turns(self):
        code, out, err, calls = self.invoke('x' * 20000 + '\nhello\n')
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])

    def test_utf8_bound_and_context_overflow_never_submit(self):
        code, out, err, calls = self.invoke('\u8a9e' * 3000 + '\n' + 'x' * 8000 + '\n/quit\n')
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn('no request submitted', out)

    def test_invalid_bounds_do_not_read_or_submit(self):
        for options in [{'max_tokens':0}, {'max_tokens':True}, {'timeout':301}]:
            with self.subTest(options=options):
                code, out, err, calls = self.invoke('hello\n', **options)
                self.assertEqual(code, 1)
                self.assertEqual(calls, [])

    def test_invalid_workspace_fails_before_model_call(self):
        code, out, err, calls = self.invoke('hello\n', workspace='relative/path')
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])

    def test_chat_subcommand_routes_without_touching_router(self):
        with patch.object(chat, 'run_chat', return_value=0) as run:
            self.assertEqual(cli.main(['--socket','/unused','chat','--model','flash','--workspace','/scope']), 0)
        run.assert_called_once_with('/unused', 'flash', 512, 180, '/scope')


class ContextAndDisplayTests(unittest.TestCase):
    def test_history_drops_whole_turns_and_keeps_new_message(self):
        history = [[{'role':role, 'content':str(turn) + 'x'*900}
                    for role in ('user','assistant')] for turn in range(8)]
        current = {'role':'user', 'content':'new'}
        messages, kept, dropped = chat.context(history, current, 512, True)
        self.assertGreater(dropped, 0)
        self.assertEqual(kept[0][0]['role'], 'user')
        self.assertEqual(kept[-1][-1]['role'], 'assistant')
        self.assertEqual(messages[-1], current)
        self.assertEqual(history[0][0]['content'], '0'+'x'*900)
        from inference.laf_service.core import validate_request
        validate_request({'model':'flash','messages':messages,'max_tokens':512})

    def test_message_count_is_also_bounded(self):
        history = [[{'role':role,'content':'x'} for role in ('user','assistant')] for _ in range(25)]
        messages, _, dropped = chat.context(history, {'role':'user','content':'new'}, 1, True)
        self.assertLessEqual(len(messages), 32)
        self.assertGreater(dropped, 0)

    def test_terminal_controls_and_bidi_are_escaped(self):
        text = '\u65e5\u672c\u8a9e\x1b[2J\r\x00\x7f\x9b\u202e\nnext'
        safe = chat.display(text)
        self.assertIn('\u65e5\u672c\u8a9e', safe)
        self.assertIn('\nnext', safe)
        for value in ('\x1b','\r','\x00','\x7f','\x9b','\u202e'):
            self.assertNotIn(value, safe)

    def test_client_observation_is_pruned_with_its_entire_search_turn(self):
        history = [
            [{'role':'user','content':'old search'},
             {'role':'assistant','content':'{"tool":"find_files","pattern":"*"}'},
             {'role':'user','content':'OLD_OBSERVATION' + 'x'*8000}],
            [{'role':'user','content':'recent'}, {'role':'assistant','content':'answer'}],
        ]
        messages, kept, dropped = chat.context(history, {'role':'user','content':'new'}, 64, True)
        self.assertEqual(dropped, 1)
        self.assertEqual(kept, [history[1]])
        self.assertNotIn('OLD_OBSERVATION', json.dumps(messages))
        self.assertNotIn('old search', json.dumps(messages))
        self.assertNotIn('"tool"', ''.join(m['content'] for m in messages[1:]))

    def test_unicode_line_separators_cannot_break_client_filename_lines(self):
        for value in ('\u2028', '\u2029'):
            with self.subTest(value=value):
                self.assertNotIn(value, chat.display('name' + value + '.py'))
                self.assertIn('\\u', chat.display('name' + value + '.py'))


class FilenameSearchTests(unittest.TestCase):
    def test_owned_relative_names_only_no_hidden_links_or_contents(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            root = Path(folder)
            (root/'src').mkdir()
            (root/'src/good.py').write_text('PRIVATE_CONTENT')
            (root/'.hidden.py').write_text('hidden')
            (root/'credentials').write_text('secret')
            (root/'bad\nname.py').write_text('hidden')
            (Path(outside)/'escaped.py').write_text('outside')
            (root/'linked').symlink_to(outside, target_is_directory=True)
            (root/'link.py').symlink_to(root/'src/good.py')
            os.link(root/'src/good.py', root/'hardlink.py')
            # Hard-linked names are excluded even if both happen to be in the scope.
            (root/'plain.py').write_text('PRIVATE_CONTENT')
            search = chat.FilenameSearch(folder)
            try:
                found = search.find('*')
            finally:
                search.close()
        self.assertEqual(found['paths'], ['plain.py'])
        self.assertTrue(found['filename_only'])
        self.assertNotIn('PRIVATE_CONTENT', json.dumps(found))

    def test_symlink_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            real = Path(parent)/'real'; real.mkdir()
            link = Path(parent)/'link'; link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(OSError):
                chat.FilenameSearch(str(link))

    def test_restricted_root_is_rejected_before_opening(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'.codex'; root.mkdir()
            with patch.object(chat, 'open_absolute_dir') as opened, self.assertRaises(ValueError):
                chat.FilenameSearch(str(root))
            opened.assert_not_called()

    def test_directory_swap_to_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            root = Path(folder); (root/'child').mkdir()
            (Path(outside)/'escape.py').write_text('outside')
            search = chat.FilenameSearch(folder)
            original = os.open
            def swapped(name, flags, *args, **kwargs):
                if name == 'child' and kwargs.get('dir_fd') is not None:
                    (root/'child').rename(root/'old')
                    (root/'child').symlink_to(outside, target_is_directory=True)
                return original(name, flags, *args, **kwargs)
            try:
                with patch.object(chat.os, 'open', side_effect=swapped):
                    found = search.find('*.py')
            finally:
                search.close()
        self.assertEqual(found['paths'], [])
        self.assertTrue(found['incomplete'])

    def test_invalid_patterns_are_rejected(self):
        for pattern in ('../*', '/etc/*', '.env', 'credentials', '*\n*', '', 'a/../../b', 'a\\b', '*'*129):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                chat.search_pattern(pattern)

    def test_entry_and_result_limits_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            for index in range(10):
                (Path(folder)/(str(index)+'.py')).touch()
            search = chat.FilenameSearch(folder)
            try:
                with patch.object(chat, 'MAX_ENTRIES', 3):
                    first = search.find('*')
                with patch.object(chat, 'MAX_MATCHES', 2):
                    second = search.find('*')
                with patch.object(chat, 'MAX_RESULT_BYTES', 15):
                    third = search.find('*')
            finally:
                search.close()
        self.assertEqual(first['scanned_entries'], 3)
        self.assertTrue(first['incomplete'])
        self.assertEqual(len(second['paths']), 2)
        self.assertTrue(second['incomplete'])
        self.assertTrue(third['incomplete'])

    def test_depth_limit_and_repeated_search_release_all_handles(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'child').mkdir(); (root/'child/file.py').touch()
            search = chat.FilenameSearch(folder)
            try:
                with patch.object(chat, 'MAX_DEPTH', 1):
                    limited = search.find('*.py')
                for _ in range(5):
                    self.assertEqual(search.find('*.py')['paths'], ['child/file.py'])
            finally:
                search.close()
                search.close()
        self.assertTrue(limited['incomplete'])


class JSONJobCompatibilityTests(unittest.TestCase):
    def test_conversation_posts_exact_json_messages_once(self):
        messages = [{'role':'user','content':'hello'}, {'role':'assistant','content':'hi'},
                    {'role':'user','content':'continue'}]
        calls = []
        def call(path, method, route, body=None):
            calls.append((method, route, body))
            if method == 'POST':
                return {'id':JOB}
            return {'id':JOB,'model':'flash','status':'completed','result':{'content':'yes'}}
        result = cli.converse('/unused', 'flash', messages, call=call)
        self.assertEqual([entry[0] for entry in calls], ['POST','GET'])
        self.assertEqual(calls[0][2]['messages'], messages)
        self.assertEqual(result['content'], 'yes')
        self.assertFalse(result['automatic_retry'])

    def test_wire_still_serializes_json_through_existing_connection(self):
        from unittest.mock import Mock
        response = Mock(status=200); response.read.return_value = b'{"status":"ready"}'
        connection = Mock(); connection.getresponse.return_value = response
        body = {'model':'flash','messages':[{'role':'user','content':'\u65e5\u672c\u8a9e'}]}
        with patch.object(cli, 'checked_socket', return_value='/unused'), patch.object(cli, 'Connection', return_value=connection):
            self.assertEqual(cli.request('/unused', 'POST', '/v1/jobs', body), {'status':'ready'})
        args = connection.request.call_args.args
        self.assertEqual(json.loads(args[2]), body)
        self.assertEqual(args[3], {'Content-Type':'application/json'})
        connection.close.assert_called_once()

    def test_bad_conversation_never_submits(self):
        for messages in ([], [{'role':'tool','content':'x'}], [{'role':'user','content':'x'*8192}]):
            with self.subTest(messages=messages):
                with patch.object(cli, 'request') as request, self.assertRaises(cli.ClientError):
                    cli.converse('/unused', 'flash', messages)
                request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
