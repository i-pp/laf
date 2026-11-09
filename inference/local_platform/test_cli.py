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

import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from .cli import ask, checked_socket, ClientError

JOB='a'*32

class TestCLI(unittest.TestCase):
    def test_ask_submits_once_and_only_polls(self):
        calls=[]
        def call(path,method,route,body=None):
            calls.append((method,route,body))
            if method=='POST':return {'id':JOB,'status':'queued'}
            return {'id':JOB,'model':'gpt-oss-120b','status':'completed',
                    'result':{'content':'advice','thinking':'not exported','finish_reason':'length','completion_tokens':10}}
        out=ask('/unused','gpt-oss-120b','fixture',call=call)
        self.assertEqual([c[0] for c in calls],['POST','GET'])
        self.assertFalse(out['code_executed']);self.assertNotIn('thinking',out)
    def test_submit_timeout_never_retries(self):
        calls=[]
        def call(*args):calls.append(args);raise TimeoutError()
        with self.assertRaises(ClientError):ask('/unused','gpt-oss-120b','fixture',call=call)
        self.assertEqual(len(calls),1)
    def test_invalid_submit_response_never_retries(self):
        calls=[]
        def call(*args):calls.append(args);raise ValueError('invalid JSON')
        with self.assertRaises(ClientError):ask('/unused','gpt-oss-120b','fixture',call=call)
        self.assertEqual(len(calls),1)
    def test_cancel_preserves_id_and_calls_delete_once(self):
        from .cli import ClientCancelled
        calls=[]
        def call(path,method,route,body=None):
            calls.append(method)
            if method=='POST':return {'id':JOB}
            if method=='GET':raise KeyboardInterrupt()
            return {'status':'cancellation_requested'}
        with self.assertRaises(ClientCancelled) as caught:
            ask('/unused','gpt-oss-120b','fixture',call=call)
        self.assertEqual(caught.exception.job_id,JOB)
        self.assertEqual(calls,['POST','GET','DELETE'])
    def test_bad_input_does_not_submit(self):
        for args in [('other','x',512,180),('flash','x'*8193,512,180),('flash','x',True,180),('flash','x',512,301)]:
            with patch('inference.local_platform.cli.request') as call:
                with self.assertRaises(ClientError):ask('/unused',*args)
                call.assert_not_called()
    def test_job_identity_is_fixed(self):
        def call(path,method,route,body=None):
            return {'id':JOB} if method=='POST' else {'id':'b'*32,'model':'gpt-oss-120b','status':'completed'}
        with self.assertRaises(ClientError):ask('/unused','gpt-oss-120b','fixture',call=call)
    def test_private_socket_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'service.sock';s=socket.socket(socket.AF_UNIX);s.bind(str(p));p.chmod(0o600)
            try:
                self.assertEqual(checked_socket(p),p)
                p.chmod(0o666)
                with self.assertRaises(ClientError):checked_socket(p)
            finally:s.close()


class TestClassificationCLI(unittest.TestCase):
    def result(self):
        return {'status':'ready','backend':'ryzenai','model':'ryzenai-quicktest-10class',
                'provider':'VitisAI','execution_target':'npu','execution_verified':True,
                'execution_scope':'mixed_npu_cpu','verification_kind':'ort_warmup_profile',
                'logits':[0]*10,'class_index':0,'latency_ms':1.0}
    def invoke(self,raw,response):
        import io,json
        from .cli import main
        with tempfile.TemporaryDirectory() as parent:
            file=Path(parent)/'input.json';file.write_text(json.dumps(raw))
            with patch('inference.local_platform.cli.request',return_value=response) as call, patch('sys.stdout',new_callable=io.StringIO) as out, patch('sys.stderr',new_callable=io.StringIO) as err:
                code=main(['--socket','/unused','classify','--input',str(file)])
                return code,call.call_args_list,out.getvalue(),err.getvalue()
    def test_one_request_preserves_mixed_identity_no_accuracy_claim(self):
        import json
        code,calls,out,err=self.invoke({'input':[0]*3072},self.result())
        self.assertEqual(code,0);self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].args[1:3],('POST','/v1/classify'))
        self.assertEqual(calls[0].kwargs,{'timeout':13})
        value=json.loads(out);self.assertFalse(value['application_accuracy_verified'])
        self.assertEqual(value['execution_scope'],'mixed_npu_cpu')
    def test_bad_input_sends_nothing(self):
        code,calls,out,err=self.invoke({'input':[True]*3072},self.result())
        self.assertEqual(code,1);self.assertEqual(calls,[])
    def test_cpu_result_rejected_without_retry(self):
        data=self.result();data['execution_target']='cpu'
        code,calls,out,err=self.invoke({'input':[0]*3072},data)
        self.assertEqual(code,1);self.assertEqual(len(calls),1)


class TestKnownOutcome(unittest.TestCase):
    def test_poll_error_preserves_received_job_identifier(self):
        def call(path,method,route,body=None):
            if method=='POST':return {'id':JOB}
            raise TimeoutError()
        with self.assertRaises(ClientError) as caught:
            ask('/unused','gpt-oss-120b','fixture',call=call)
        self.assertEqual(caught.exception.job_id,JOB)


class TestEncodingSafety(unittest.TestCase):
    def test_encoding_cookie_cannot_change_executed_source(self):
        import ast, json, tokenize, io
        from .bounded_patch import validated_replacement
        code='def add(a, b):\n    return a + b\n'
        proposed='# coding: unicode_escape\n# '+chr(92)+'u000aimport os\n'+code
        actual,_=validated_replacement(json.dumps({'replacement':proposed,'explanation':'fixture'}),code)
        encoding,_=tokenize.detect_encoding(io.BytesIO(actual.encode('utf-8')).readline)
        parsed=ast.parse(actual.encode('utf-8').decode(encoding))
        self.assertEqual([type(n).__name__ for n in parsed.body],['FunctionDef'])
        self.assertNotIn('coding:',actual)

if __name__=='__main__':unittest.main()
