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

import io,json,unittest
from laf_mcp.contracts import strict_json,validate_submission,validate_catalog,final_frame,read_frame,ContractError,MAX_FRAME
class Contracts(unittest.TestCase):
 def test_json_rejects_ambiguous_inputs(self):
  for raw in [b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":1e999}',b'{"x":"\xff"}',b'[]',b'{"x":"\\ud800"}',b'{"x":'+b'['*40+b'0'+b']'*40+b'}']:
   with self.subTest(raw=raw),self.assertRaises(ContractError): strict_json(raw)
 def test_catalog_intersection(self):
  c=validate_catalog({'models':['flash','coder-next'],'context_limit':8192,'max_output_tokens':1024,'max_large_models_loaded':1,'new':42},frozenset(['flash']))
  self.assertEqual(c['models'],['flash'])
 def test_catalog_not_widened(self):
  for x in [True,2,None]:
   with self.assertRaises(ContractError):validate_catalog({'models':['flash'],'context_limit':8192,'max_output_tokens':x,'max_large_models_loaded':1},{'flash'})
 def test_exact_model_required(self):
  with self.assertRaises(ContractError): validate_submission({'messages':[{'role':'user','content':'hello'}]})
 def test_request_extras_types_and_budget(self):
  base={'model':'flash','messages':[{'role':'user','content':'hello'}]}
  self.assertEqual(validate_submission(base)['max_tokens'],256)
  for x in [dict(base,model='https://bad'),dict(base,max_tokens=True),dict(base,timeout_seconds=301),dict(base,foo=1),dict(base,messages=[{'role':'user','content':'x'*8000}])]:
   with self.assertRaises(ContractError):validate_submission(x)
 def test_read_frame_is_byte_bounded_strict_and_terminated(self):
  for raw in [b'a'*(MAX_FRAME+1),b'{"x":1}',b'\xff\n']:
   with self.assertRaises(ContractError):read_frame(io.BytesIO(raw))
  self.assertEqual(read_frame(io.BytesIO(b'{"x":1}\n')),{'x':1})
 def test_final_size_preserves_unknown_handle_and_job(self):
  out={'outcome':'UNKNOWN','handle':'a'*32,'job_id':'b'*32,'content':'\x00'*MAX_FRAME}
  # Unicode test fixture: a multi-byte request ID exercises the byte limit.
  frame=final_frame({'jsonrpc':'2.0','id':'識'*128,'result':{'content':[{'type':'text','text':out['content']}],'structuredContent':out,'isError':False}})
  self.assertLessEqual(len(frame),MAX_FRAME)
  r=json.loads(frame)['result']['structuredContent']
  self.assertEqual(r['outcome'],'UNKNOWN');self.assertEqual(r['handle'],'a'*32);self.assertEqual(r['job_id'],'b'*32)
 def test_final_size_preserves_known_completion(self):
  # Unicode test fixture: emoji output must still respect the byte limit.
  out={'outcome':'TERMINAL','status':'completed','handle':'c'*32,'content':'😀'*MAX_FRAME}
  r=json.loads(final_frame({'jsonrpc':'2.0','id':1,'result':{'structuredContent':out,'content':[]}}))['result']['structuredContent']
  self.assertEqual(r['status'],'completed');self.assertEqual(r['outcome'],'TERMINAL')
if __name__=='__main__':unittest.main()
