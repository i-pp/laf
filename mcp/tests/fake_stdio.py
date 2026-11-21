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

"""Test-only SDK subprocess with synthetic Router data; never opens a socket."""
import os,time
import anyio
from laf_mcp.application import Application
from laf_mcp.receipts import Receipts
from laf_mcp.server import serve
class FakeRouter:
 endpoint_key='a'*64
 def validate_endpoint(self,key=None,*,cancel=False):
  if key is not None and key!=self.endpoint_key:raise ValueError()
  return self.endpoint_key
 def request(self,method,path,body=None):
  if path=='/v1/models':return 200,{'models':['flash'],'context_limit':8192,'max_output_tokens':1024,'max_large_models_loaded':1}
  if path=='/health':
   time.sleep(float(os.environ.get('FAKE_DELAY','0')))
   return 200,{'status':'ready'}
  if path=='/v1/capabilities':return 200,{'api_version':1,'npu':{'configured':False}}
  if method=='POST':
   if os.environ.get('FAKE_POST_MARKER'):
    from pathlib import Path
    Path(os.environ['FAKE_POST_MARKER']).write_text('synthetic admission')
   time.sleep(float(os.environ.get('FAKE_POST_DELAY','0')))
   if os.environ.get('FAKE_UNKNOWN'):raise OSError()
   return 202,{'id':'b'*32,'status':'queued'}
  if method=='DELETE':return 202,{'status':'cancellation_requested'}
  return 200,{'id':'b'*32,'model':'flash','status':'completed','result':{'content':'\x00'*70000 if os.environ.get('FAKE_LARGE') else 'synthetic reply'}}
r=Receipts(os.environ['LAF_MCP_STATE_DIR'])
try:anyio.run(serve,Application(FakeRouter(),r,{'flash'}))
finally:r.close()
