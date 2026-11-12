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

import importlib.metadata
import json,os,pathlib,select,subprocess,sys,tempfile,time,unittest
from laf_mcp.contracts import MAX_FRAME
ROOT=pathlib.Path(__file__).resolve().parents[2]
class ProtocolTests(unittest.TestCase):
 # Modified 2026-11-11: real protocol results must use the selected SDK, never an older installed copy.
 def setUp(self):
  self.assertEqual(importlib.metadata.version('mcp'), '1.30.0', 'Real protocol checks require actual MCP SDK 1.30.0')
  self.tmp=tempfile.TemporaryDirectory();self.p=None
 def tearDown(self):
  if self.p:
   self.p.terminate()
   try:self.p.communicate(timeout=4)
   except subprocess.TimeoutExpired:self.p.kill();self.p.communicate()
  self.tmp.cleanup()
 def start(self,**extra):
  env=dict(os.environ,PYTHONPATH=str(ROOT/'mcp/src'),LAF_MCP_STATE_DIR=self.tmp.name+'/receipts',**extra)
  self.p=subprocess.Popen([sys.executable,'-B',str(ROOT/'mcp/tests/fake_stdio.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,bufsize=0)
  self.send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'test','version':'1'}}})
  out=self.read();self.assertEqual(out['result']['serverInfo'],{'name':'laf','version':'0.1.0'})
  self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
 def send(self,obj):self.p.stdin.write(json.dumps(obj).encode()+b'\n');self.p.stdin.flush()
 def read(self):
  deadline=time.monotonic()+6;raw=b''
  while not raw.endswith(b'\n'):
   remain=deadline-time.monotonic()
   if remain<=0 or not select.select([self.p.stdout],[],[],remain)[0]:self.fail('protocol response timeout')
   piece=os.read(self.p.stdout.fileno(),1)
   if not piece:self.fail('protocol ended without response')
   raw+=piece
   self.assertLessEqual(len(raw),MAX_FRAME)
  return json.loads(raw)
 def call(self,id,name,args):
  self.send({'jsonrpc':'2.0','id':id,'method':'tools/call','params':{'name':name,'arguments':args}});return self.read()['result']['structuredContent']
 def test_real_launcher_initialization_and_tools_without_router(self):
  env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
  env.pop('PYTHONPATH',None)
  self.p=subprocess.Popen([sys.executable,'-I','-B',str(ROOT/'mcp/launch.py'),
   '--state-dir',self.tmp.name+'/launch-receipts','--allow-model','flash',
   '--socket',self.tmp.name+'/nonexistent-router.sock'],
   stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,bufsize=0)
  self.send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'launcher-test','version':'1'}}})
  self.assertEqual(self.read()['result']['serverInfo'],{'name':'laf','version':'0.1.0'})
  self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
  self.send({'jsonrpc':'2.0','id':2,'method':'tools/list'})
  self.assertEqual(len(self.read()['result']['tools']),5)
  self.p.stdin.close();self.p.stdin=None
  out,err=self.p.communicate(timeout=6)
  self.assertEqual(self.p.returncode,0);self.assertEqual(out,b'');self.assertEqual(err,b'')
  self.assertFalse(pathlib.Path(self.tmp.name+'/nonexistent-router.sock').exists())
  self.assertEqual(sorted(p.name for p in pathlib.Path(self.tmp.name+'/launch-receipts').iterdir()),['.lock'])
 def test_real_sdk_initialize_five_tools_and_job_flow(self):
  self.start();self.send({'jsonrpc':'2.0','id':2,'method':'tools/list'});tools=self.read()['result']['tools'];self.assertEqual(len(tools),5)
  self.assertEqual({t['name'] for t in tools},{'laf_router_models','laf_router_status','laf_router_submit_job','laf_router_get_job','laf_router_cancel_job'})
  r=self.call(3,'laf_router_submit_job',{'attempt_id':'1'*32,'model':'flash','messages':[{'role':'user','content':'hello'}]});self.assertEqual(r['outcome'],'ADMITTED')
  o=self.call(4,'laf_router_get_job',{'handle':r['handle']});self.assertEqual(o['content'],'synthetic reply')
  c=self.call(5,'laf_router_cancel_job',{'handle':r['handle']});self.assertEqual(c['cancel_outcome'],'REQUESTED');self.assertFalse(c['physical_stop_confirmed'])
 def test_actual_sdk_encoder_preserves_handle_when_large(self):
  self.start(FAKE_LARGE='1');r=self.call(2,'laf_router_submit_job',{'attempt_id':'1'*32,'model':'flash','messages':[{'role':'user','content':'x'}]})
  o=self.call(3,'laf_router_get_job',{'handle':r['handle']});self.assertEqual(o['error'],'response_too_large');self.assertEqual(o['handle'],r['handle']);self.assertEqual(o['status'],'completed');self.assertEqual(o['outcome'],'TERMINAL')
 def test_unknown_durable_receipt_and_no_replay(self):
  self.start(FAKE_UNKNOWN='1');r=self.call(2,'laf_router_submit_job',{'attempt_id':'1'*32,'model':'flash','messages':[{'role':'user','content':'x'}]});self.assertEqual(r['outcome'],'UNKNOWN')
  o=self.call(3,'laf_router_get_job',{'handle':r['handle']});self.assertEqual(o['outcome'],'UNKNOWN')
 def test_pre_dispatch_busy_admission(self):
  self.start(FAKE_DELAY='0.25')
  for i in range(2,7):self.send({'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':'laf_router_status','arguments':{}}})
  results=[self.read() for _ in range(5)];self.assertGreaterEqual(sum(r['result']['structuredContent'].get('error')=='adapter_busy' for r in results),2)
 def test_eof_after_admission_recovers_known_attempt_in_new_session(self):
  marker=self.tmp.name+'/marker';self.start(FAKE_POST_MARKER=marker,FAKE_POST_DELAY='0.15')
  self.send({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'laf_router_submit_job','arguments':{'attempt_id':'1'*32,'model':'flash','messages':[{'role':'user','content':'x'}]}}})
  end=time.monotonic()+5
  while not pathlib.Path(marker).exists() and time.monotonic()<end:time.sleep(.01)
  self.assertTrue(pathlib.Path(marker).exists());self.p.stdin.close();self.p.stdin=None
  self.p.communicate(timeout=6);self.p=None
  self.start()
  recovered=self.call(2,'laf_router_get_job',{'handle':'1'*32})
  self.assertEqual(recovered['outcome'],'TERMINAL');self.assertEqual(recovered['handle'],'1'*32)
 def test_sdk_validation_never_logs_sensitive_invalid_params(self):
  self.start();self.send({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':123,'arguments':{'prompt':'PRIVATE_SENTINEL'}}})
  reply=self.read();self.assertNotIn('PRIVATE_SENTINEL',json.dumps(reply))
  self.p.stdin.close();self.p.stdin=None;out,err=self.p.communicate(timeout=6);self.assertNotIn(b'PRIVATE_SENTINEL',out+err)
 def test_transport_cancellation_does_not_drop_handle_or_duplicate_attempt(self):
  self.start(FAKE_POST_DELAY='0.1')
  self.send({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'laf_router_submit_job','arguments':{'attempt_id':'1'*32,'model':'flash','messages':[{'role':'user','content':'x'}]}}})
  self.send({'jsonrpc':'2.0','method':'notifications/cancelled','params':{'requestId':2}})
  reply=self.read()['result']['structuredContent'];self.assertEqual(reply['handle'],'1'*32);self.assertEqual(reply['outcome'],'ADMITTED')
 def test_invalid_utf8_closes_session_without_echo(self):
  self.start();self.p.stdin.write(b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"secret":"\xff"}}\n');self.p.stdin.flush();self.p.stdin.close();self.p.stdin=None
  out,err=self.p.communicate(timeout=6);self.assertNotIn(b'secret',out+err);self.assertIn(b'invalid or oversized',err)
 def test_oversized_unterminated_frame_closes(self):
  self.start();self.p.stdin.write(b'x'*(MAX_FRAME+1));self.p.stdin.flush();self.p.stdin.close();self.p.stdin=None
  out,err=self.p.communicate(timeout=6);self.assertLessEqual(len(out),MAX_FRAME);self.assertIn(b'invalid or oversized',err)
if __name__=='__main__':unittest.main()
