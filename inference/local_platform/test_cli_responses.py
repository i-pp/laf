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

"""Offline malformed-response regression tests. No real socket or inference."""
import io
import json
import unittest
from unittest.mock import Mock, patch
from . import cli

JOB = 'a' * 32


class WireResponseTests(unittest.TestCase):
    def invoke(self, raw, status=200):
        response = Mock(status=status)
        response.read.return_value = raw
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(cli, 'checked_socket', return_value='/unused'), patch.object(cli, 'Connection', return_value=connection):
            try:
                return cli.request('/unused', 'GET', '/health')
            finally:
                connection.close.assert_called_once_with()
                connection.request.assert_called_once()
                response.read.assert_called_once_with(cli.MAX_RESPONSE + 1)

    def test_valid_utf8_object_remains_compatible(self):
        self.assertEqual(self.invoke('{"content":"日本語","value":1.5}'.encode()), {'content': '日本語', 'value': 1.5})

    def test_top_level_duplicate_state_rejected(self):
        with self.assertRaises(cli.ClientError):
            self.invoke(b'{"status":"failed","status":"completed"}')

    def test_nested_duplicate_content_rejected(self):
        with self.assertRaises(cli.ClientError):
            self.invoke(b'{"result":{"content":"first","content":"second"}}')

    def test_nonfinite_constants_rejected(self):
        for value in ('NaN', 'Infinity', '-Infinity'):
            with self.subTest(value=value), self.assertRaises(cli.ClientError):
                self.invoke(('{"value":' + value + '}').encode())

    def test_overflowing_float_rejected(self):
        with self.assertRaises(cli.ClientError):
            self.invoke(b'{"value":1e999}')

    def test_deep_json_is_normalized_as_client_error(self):
        with self.assertRaises(cli.ClientError):
            self.invoke(b'{"nested":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}')

    def test_invalid_utf8_is_normalized_as_client_error(self):
        with self.assertRaises(cli.ClientError):
            self.invoke(b'{"value":"\xff"}')

    def test_utf16_is_not_the_utf8_wire_contract(self):
        with self.assertRaises(cli.ClientError):
            self.invoke('{"status":"ready"}'.encode('utf-16'))

    def test_exact_nesting_limit_is_accepted_and_one_over_rejected(self):
        # The outer object consumes one level.
        raw = b'{"value":' + b'[' * (cli.MAX_JSON_DEPTH - 1) + b'0' + b']' * (cli.MAX_JSON_DEPTH - 1) + b'}'
        self.assertIsInstance(self.invoke(raw), dict)
        raw = b'{"value":' + b'[' * cli.MAX_JSON_DEPTH + b'0' + b']' * cli.MAX_JSON_DEPTH + b'}'
        with self.assertRaises(cli.ClientError):
            self.invoke(raw)

    def test_brackets_and_escaped_quotes_inside_strings_do_not_count(self):
        value = '[{' * 150 + '"quoted"' + '\\' + '}]' * 150
        raw = json.dumps({'value':value}).encode()
        self.assertEqual(self.invoke(raw), {'value':value})

    def test_nonobject_json_remains_rejected(self):
        for value in ('null', '[]', '1', '"ready"'):
            with self.subTest(value=value), self.assertRaises(cli.ClientError):
                self.invoke(value.encode())

    def test_oversize_json_remains_rejected(self):
        with self.assertRaises(cli.ClientError):
            self.invoke(b' ' * (cli.MAX_RESPONSE + 1))

    def test_invalid_json_remains_rejected_without_body_in_error(self):
        with self.assertRaises(cli.ClientError) as caught:
            self.invoke(b'{PRIVATE_FIXTURE_BODY')
        self.assertNotIn('PRIVATE_FIXTURE_BODY', str(caught.exception))

    def test_error_response_preserves_rejection(self):
        with self.assertRaises(cli.ClientError):
            self.invoke(b'{"error":"unavailable"}', status=503)


class CommandResponseTests(unittest.TestCase):
    def invoke(self, command, response):
        with patch.object(cli, 'request', return_value=response) as call, patch('sys.stdout', new_callable=io.StringIO) as output, patch('sys.stderr', new_callable=io.StringIO) as error:
            code = cli.main(['--socket', '/unused', *command])
        return code, output.getvalue(), error.getvalue(), call.call_args_list

    def test_malformed_gpu_status_is_handled(self):
        for value in (None, [], 'ready', True):
            with self.subTest(value=value):
                code, out, error, calls = self.invoke(['status'], {'status':'ready','gpu':value,'npu':{}})
                self.assertEqual(code, 1)
                self.assertEqual(out, '')
                self.assertFalse(json.loads(error)['automatic_retry'])
                self.assertEqual(len(calls), 1)

    def test_malformed_npu_status_is_handled(self):
        for value in (None, [], 'ready', True):
            with self.subTest(value=value):
                code, out, error, calls = self.invoke(['status'], {'status':'ready','gpu':{},'npu':value})
                self.assertEqual(code, 1)
                self.assertEqual(out, '')
                self.assertFalse(json.loads(error)['automatic_retry'])
                self.assertEqual(len(calls), 1)

    def test_get_rejects_wrong_job_identity(self):
        code, out, error, calls = self.invoke(['get',JOB], {'id':'b'*32,'model':'flash','status':'completed','result':{'content':'wrong task'}})
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(json.loads(error)['job_id'], JOB)
        self.assertEqual(len(calls), 1)

    def test_get_rejects_unknown_state(self):
        code, out, error, calls = self.invoke(['get',JOB], {'id':JOB,'model':'flash','status':'imagined'})
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(json.loads(error)['job_id'], JOB)
        self.assertEqual(len(calls), 1)

    def test_get_rejects_completed_job_without_valid_content(self):
        for value in (None, [], {'content':None}, {'content':True}):
            with self.subTest(value=value):
                code, out, error, calls = self.invoke(['get',JOB], {'id':JOB,'model':'flash','status':'completed','result':value})
                self.assertEqual(code, 1)
                self.assertEqual(out, '')
                self.assertEqual(json.loads(error)['job_id'], JOB)
                self.assertEqual(len(calls), 1)

    def test_get_rejects_unknown_model(self):
        code, out, error, calls = self.invoke(['get',JOB], {'id':JOB,'model':'unknown','status':'queued'})
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(json.loads(error)['job_id'], JOB)
        self.assertEqual(len(calls), 1)

    def test_get_accepts_matching_terminal_job(self):
        code, out, error, calls = self.invoke(['get',JOB], {'id':JOB,'model':'flash','status':'completed','result':{'content':'fixture','thinking':'private'}})
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result['id'], JOB)
        self.assertNotIn('thinking', result['result'])
        self.assertEqual(error, '')
        self.assertEqual(len(calls), 1)

    def test_cancel_rejects_unrecognized_acknowledgement(self):
        code, out, error, calls = self.invoke(['cancel',JOB], {'status':'stopped'})
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(json.loads(error)['job_id'], JOB)
        self.assertEqual(len(calls), 1)

    def test_cancel_ack_does_not_claim_stop(self):
        code, out, error, calls = self.invoke(['cancel',JOB], {'status':'cancellation_requested'})
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)['stop_confirmed'])
        self.assertFalse(json.loads(out)['automatic_retry'])
        self.assertEqual(error, '')
        self.assertEqual(len(calls), 1)


class UncertainOutcomeTests(unittest.TestCase):
    def wire_call(self, payloads, calls):
        def call(path, method, route, body=None):
            calls.append((method, route))
            response = Mock(status=200)
            response.read.return_value = payloads.pop(0)
            connection = Mock()
            connection.getresponse.return_value = response
            with patch.object(cli, 'checked_socket', return_value='/unused'), patch.object(cli, 'Connection', return_value=connection):
                return cli.request(path, method, route, body)
        return call

    def test_ambiguous_submission_is_not_replayed(self):
        calls = []
        wire = self.wire_call([('{"id":"'+JOB+'","id":"'+'b'*32+'"}').encode()], calls)
        with self.assertRaises(cli.ClientError) as caught:
            cli.ask('/unused', 'flash', 'fixture', call=wire, sleep=lambda _:None)
        self.assertIsNone(caught.exception.job_id)
        self.assertEqual(calls, [('POST','/v1/jobs')])

    def test_bad_poll_preserves_known_id_without_replay(self):
        calls = []
        wire = self.wire_call([json.dumps({'id':JOB}).encode(), b'{"status":"failed","status":"completed"}'], calls)
        with self.assertRaises(cli.ClientError) as caught:
            cli.ask('/unused', 'flash', 'fixture', call=wire, sleep=lambda _:None)
        self.assertEqual(caught.exception.job_id, JOB)
        self.assertEqual(calls, [('POST','/v1/jobs'), ('GET','/v1/jobs/'+JOB)])


if __name__ == '__main__':
    unittest.main()
