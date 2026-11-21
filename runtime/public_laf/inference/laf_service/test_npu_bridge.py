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

import http.server
import json
import os
from pathlib import Path
import socket
import socketserver
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from .npu_bridge import classify, validate_input, validate_result, MODEL, NPUUnavailable, NPUBusy
from . import test_service as service_test
body=service_test.body


def ready():
    return {'status':'ready','backend':'ryzenai','model':MODEL,'provider':'VitisAI',
            'execution_target':'npu','execution_verified':True,
            'execution_scope':'mixed_npu_cpu','verification_kind':'ort_warmup_profile',
            'logits':[float(x) for x in range(10)],'class_index':9,'latency_ms':2.0}


class InputResultTest(unittest.TestCase):
    def test_strict_input(self):
        validate_input({'model':MODEL,'input':[0.0]*3072})
        for value in [None,{}, {'input':[0]*3071},{'input':[True]*3072},
                      {'input':[float('nan')]*3072}, {'input':[1e40]*3072},
                      {'model':[],'input':[0]*3072},{'input':[0]*3072,'options':{}}]:
            with self.subTest(value=str(value)[:80]):
                with self.assertRaises(ValueError):validate_input(value)
    def test_float32_max_decimal_logit_is_not_false_rejected(self):
        data=ready();data['logits'][0]=3.40282347e38;data['class_index']=0
        self.assertEqual(validate_result(data)['class_index'],0)
    def test_no_false_npu_identity(self):
        for key,value in [('execution_target','cpu'),('provider','CPU'),('execution_verified',False),
                          ('execution_scope',[]),('status','not_ready'),('model','other'),
                          ('class_index',True),('class_index',1),('latency_ms',float('nan')),
                          ('logits',[float('inf')]*10),('logits',[10**1000]*10)]:
            data=ready();data[key]=value
            with self.subTest(key=key):
                with self.assertRaises(NPUUnavailable):validate_result(data)
        data=ready();data['private_path']='/never-export'
        self.assertNotIn('private_path',validate_result(data))


class Upstream(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True
class Peer(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_POST(self):
        self.server.calls+=1
        self.server.last_body=self.rfile.read(int(self.headers['Content-Length']))
        time.sleep(self.server.delay)
        raw=json.dumps(self.server.result).encode()
        try:
            self.send_response(self.server.code)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)+getattr(self.server,'length_extra',0)))
            if getattr(self.server,'duplicate_length',False):
                self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        except OSError:pass


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.path=Path(self.dir.name)/'npu.sock'
        self.srv=Upstream(str(self.path),Peer);self.path.chmod(0o600)
        self.srv.result=ready();self.srv.code=200;self.srv.calls=0;self.srv.delay=0
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.srv.shutdown();self.srv.server_close();self.dir.cleanup()
    def test_real_private_http_one_attempt(self):
        self.assertEqual(classify(str(self.path),{'input':[0]*3072})['class_index'],9)
        self.assertEqual(self.srv.calls,1)
    def test_compact_boundary_rounds_to_float32_without_body_growth(self):
        raw=json.dumps({'input':[10**30]*3072},separators=(',',':')).encode()
        self.assertLess(len(raw),100000)
        classify(str(self.path),json.loads(raw))
        self.assertLessEqual(len(self.srv.last_body),100000)
        from .npu_bridge import validate_input
        self.assertEqual(json.loads(self.srv.last_body),validate_input(json.loads(raw)))
        self.assertEqual(self.srv.calls,1)
    def test_busy_and_error_no_retry(self):
        self.srv.code=429
        with self.assertRaises(NPUBusy):classify(str(self.path),{'input':[0]*3072})
        self.srv.code=503
        with self.assertRaises(NPUUnavailable):classify(str(self.path),{'input':[0]*3072})
        self.assertEqual(self.srv.calls,2)
    def test_private_mode_and_absent_endpoint(self):
        self.path.chmod(0o666)
        with self.assertRaises(NPUUnavailable):classify(str(self.path),{'input':[0]*3072})
        with self.assertRaises(NPUUnavailable):classify(None,{'input':[0]*3072})
        self.assertEqual(self.srv.calls,0)
    def test_deadline_closes_connection_does_not_retry(self):
        self.srv.delay=.5
        start=time.monotonic()
        with self.assertRaises(NPUUnavailable):classify(str(self.path),{'input':[0]*3072},timeout=1.05)
        self.assertLess(time.monotonic()-start,.4)
        self.assertEqual(self.srv.calls,1)
    def test_truncated_or_duplicate_framing_fails_closed(self):
        self.srv.length_extra=100
        with self.assertRaises(NPUUnavailable):classify(str(self.path),{'input':[0]*3072})
        self.srv.length_extra=0;self.srv.duplicate_length=True
        with self.assertRaises(NPUUnavailable):classify(str(self.path),{'input':[0]*3072})
        self.assertEqual(self.srv.calls,2)
    def test_malformed_output_fails_closed(self):
        self.srv.result=ready();self.srv.result['execution_scope']=[]
        with self.assertRaises(NPUUnavailable):classify(str(self.path),{'input':[0]*3072})
    def test_oversized_upstream_fails_closed(self):
        self.srv.result={'extra':'x'*17000}
        with self.assertRaises(NPUUnavailable):classify(str(self.path),{'input':[0]*3072})


class ClassificationHTTPTest(unittest.TestCase):
    setUp=service_test.HTTPTest.setUp
    tearDown=service_test.HTTPTest.tearDown
    request=service_test.HTTPTest.request
    def test_capabilities_preserve_gpu_models_and_separate_npu(self):
        code,out=self.request('GET','/v1/capabilities')
        self.assertEqual(code,200);self.assertEqual(out['api_version'],1)
        self.assertEqual(len(out['gpu']['models']),3)
        self.assertEqual(out['npu']['input_elements'],3072)
        self.assertFalse(out['npu']['application_accuracy_verified'])
        self.assertFalse(out['npu']['configured'])
    def test_classification_success_separate_from_gpu_lane(self):
        with patch('inference.laf_service.server.npu_classify',return_value=ready()) as call:
            code,result=self.request('POST','/v1/classify',{'input':[0]*3072})
            self.assertEqual(code,200);self.assertEqual(result['class_index'],9)
            self.assertEqual(call.call_count,1)
            self.assertEqual(self.ctl.backend.calls,[])
            self.assertEqual(self.ctl.queue.qsize(),0)
    def test_npu_outage_keeps_gpu_ready_and_serving(self):
        with patch('inference.laf_service.server.npu_classify',side_effect=NPUUnavailable()):
            self.assertEqual(self.request('POST','/v1/classify',{'input':[0]*3072})[0],503)
        self.assertEqual(self.request('GET','/health')[0],200)
        self.assertEqual(self.request('POST','/v1/chat/completions',body())[1]['status'],'completed')
        self.assertIsNone(self.ctl.fault)
    def test_proxy_slot_rejects_concurrent_request(self):
        self.srv.npu_slot.acquire()
        try:self.assertEqual(self.request('POST','/v1/classify',{'input':[0]*3072})[0],429)
        finally:self.srv.npu_slot.release()
    def test_only_classification_gets_larger_body(self):
        with patch('inference.laf_service.server.npu_classify',return_value=ready()):
            self.assertEqual(self.request('POST','/v1/classify',{'input':[1.23456789012345e20]*3072})[0],200)
        # Oversized requests must be rejected from headers before reading the body.
        # Sending that body races with the server closing its rejected connection.
        connection = service_test.UnixHTTPConnection(self.path)
        try:
            connection.putrequest('POST', '/v1/jobs')
            connection.putheader('Content-Type', 'application/json')
            connection.putheader('Content-Length', str(len(json.dumps(body(content='x'*40000)))))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            self.assertEqual(json.loads(response.read())['error'], 'body exceeds route bound')
            self.assertEqual(self.ctl.backend.calls, [])
        finally:
            connection.close()
    def test_empty_origin_is_rejected(self):
        # Origin is rejected before body consumption. Sending the body separately
        # races with the server closing the connection after its valid 400 reply.
        connection = service_test.UnixHTTPConnection(self.path)
        try:
            connection.putrequest('POST', '/v1/classify')
            connection.putheader('Content-Type', 'application/json')
            connection.putheader('Content-Length', str(len(json.dumps({'input': [0] * 3072}))))
            connection.putheader('Origin', '')
            with patch('inference.laf_service.server.npu_classify') as classify:
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                self.assertEqual(
                    json.loads(response.read())['error'],
                    'browser origin and transfer encoding are not accepted',
                )
                classify.assert_not_called()
            self.assertEqual(self.ctl.backend.calls, [])
        finally:
            connection.close()
    def test_bad_classification_is_400(self):
        self.assertEqual(self.request('POST','/v1/classify',{'input':[]})[0],400)

if __name__=='__main__':unittest.main()
