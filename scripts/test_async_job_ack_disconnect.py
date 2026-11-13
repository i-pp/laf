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

# Scope: this newly authored file only; imported private source is not relicensed.
"""ASYNC-ACK-01: lost async ACK is not provider cancellation; sync remains so."""
import http.client,json,select,sys,unittest
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'mcp/tests'))
from integrated_router_fixture import ConfiguredFixture
from inference.laf_service.server import Handler

class AckDisconnectTests(unittest.TestCase):
 def setUp(self):self.fixture=ConfiguredFixture(start=False);self.addCleanup(self.fixture.close)
 def handler(self,path):
  body={'model':'qwen-coder-small','messages':[{'role':'user','content':'synthetic ack-disconnect test'}],'max_tokens':8};raw=json.dumps(body).encode();h=object.__new__(Handler);h.server=SimpleNamespace(controller=self.fixture.controller,config={});h.path=path;h.headers=Message();h.headers['Host']='localhost';h.headers['Content-Type']='application/json';h.headers['Content-Length']=str(len(raw));h.rfile=BytesIO(raw);return h
 def job(self):self.assertEqual(len(self.fixture.controller.jobs),1);return next(iter(self.fixture.controller.jobs.values()))
 def test_async_ack_broken_pipe_does_not_cancel_admitted_job(self):
  h=self.handler('/v1/jobs')
  def response(code,value):self.assertEqual(code,202);raise BrokenPipeError('synthetic peer vanished')
  h.response=response;h.do_POST();self.assertFalse(self.job().cancel.is_set());self.assertEqual(self.job().status,'queued')
 def test_async_ack_http_failure_does_not_cancel_admitted_job(self):
  h=self.handler('/v1/jobs');h.response=lambda *_:(_ for _ in ()).throw(http.client.HTTPException('synthetic ack framing failure'));h.do_POST();self.assertFalse(self.job().cancel.is_set())
 def test_async_success_still_returns_one_202_and_keeps_job(self):
  h=self.handler('/v1/jobs');responses=[];h.response=lambda *args:responses.append(args);h.do_POST();self.assertEqual(responses,[(202,{'id':self.job().id,'status':'queued'})]);self.assertFalse(self.job().cancel.is_set())
 def test_sync_completed_response_write_failure_still_cancels(self):
  h=self.handler('/v1/chat/completions');original=self.fixture.controller.submit
  def submit(body):job=original(body);job.status='completed';job.done.set();return job
  h.response=lambda *_:(_ for _ in ()).throw(BrokenPipeError('synthetic sync disconnect'))
  with patch.object(self.fixture.controller,'submit',submit):h.do_POST()
  self.assertTrue(self.job().cancel.is_set())
 def test_sync_waiting_client_eof_still_cancels(self):
  h=self.handler('/v1/chat/completions');h.connection=SimpleNamespace(recv=lambda *_:b'');h.response=lambda *_:self.fail('sync EOF must not write a response')
  with patch.object(select,'select',return_value=([h.connection],[],[])):h.do_POST()
  self.assertTrue(self.job().cancel.is_set())
