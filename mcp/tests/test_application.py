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

# Modified 2026-11-25: check reported Router provider modes without extra calls.

import tempfile,pathlib,json,unittest,os
from unittest.mock import MagicMock, patch
from laf_mcp.application import Application
from laf_mcp.receipts import Receipts,ReceiptError
class Fake:
 endpoint_key='a'*64
 def __init__(self):self.calls=[];self.fail=None;self.receipts=None
 def validate_endpoint(self,expected_key=None,*,cancel=False):
  if expected_key is not None and expected_key != self.endpoint_key: raise ValueError('changed')
  return self.endpoint_key
 def request(self,m,p,b=None):
  self.calls.append((m,p,b))
  if m=='POST':
   if self.receipts:assert any(x['outcome']=='UNKNOWN' for x in self.receipts.records())
   if self.fail: raise self.fail
   return 202,{'id':'b'*32,'status':'queued'}
  if m=='DELETE':return 202,{'status':'cancellation_requested'}
  if p=='/v1/models':return 200,{'models':['flash','coder-next'],'context_limit':8192,'max_output_tokens':1024,'max_large_models_loaded':1}
  if p=='/health':return 503,{'status':'not_ready','fault':'SECRET'}
  if p=='/v1/capabilities':return 200,{'api_version':1,'gpu':{'models':['flash'],'max_concurrent_generation':1},'npu':{'configured':False}}
  return 200,{'id':'b'*32,'model':'flash','status':'completed','result':{'content':'hello','thinking':'DO NOT EXECUTE'},'error':None}


class CatalogTests(unittest.TestCase):
 def setUp(self):
  self.f = Fake()
  self.r = MagicMock(spec=Receipts)
  self.a = Application(self.f, self.r, {'flash'})

 def test_models_observe_empty_and_different_catalog_limits(self):
  standard = {'context_limit': 8192, 'max_output_tokens': 1024, 'max_large_models_loaded': 1}
  different = {'context_limit': 16384, 'max_output_tokens': 512, 'max_large_models_loaded': 0}
  cases = [
   (['flash', 'coder-next'], standard, ['flash']),
   (['flash', 'coder-next'], different, ['flash']),
   ([], standard, []),
  ]
  for models, limits, visible in cases:
   with self.subTest(models=models, limits=limits):
    catalog = {'models': models, **limits}
    with patch.object(self.f, 'request', return_value=(200, catalog)) as request:
     observed = self.a.call('laf_router_models', {})
    request.assert_called_once_with('GET', '/v1/models')
    self.assertEqual(observed, {'outcome': 'OBSERVED', 'models': visible, **limits,
                                'explicit_model_required': True})
  self.assertEqual(self.r.mock_calls, [])

 def test_models_refuse_malformed_catalog_metadata(self):
  catalog = {'models': ['flash'], 'context_limit': 8192,
             'max_output_tokens': 1024, 'max_large_models_loaded': 1}
  cases = [
   {'max_output_tokens': True}, {'max_output_tokens': 0}, {'context_limit': 2**53},
   {'max_large_models_loaded': -1}, {'models': ['flash', 'flash']},
   {'models': [f'model-{i}' for i in range(10)]},
   {'default_model': 'other'}, {'flash_enabled': False},
  ]
  for invalid in cases:
   with self.subTest(invalid=invalid):
    with patch.object(self.f, 'request', return_value=(200, {**catalog, **invalid})):
     observed = self.a.call('laf_router_models', {})
    self.assertEqual(observed['outcome'], 'UNAVAILABLE')

 def test_submit_rejects_empty_or_incompatible_catalog(self):
  catalog = {'models': ['flash'], 'context_limit': 8192,
             'max_output_tokens': 1024, 'max_large_models_loaded': 1}
  cases = [
   {'models': []}, {'context_limit': 16384}, {'max_output_tokens': 512},
   {'max_large_models_loaded': 0},
  ]
  for i, override in enumerate(cases, 1):
   with self.subTest(override=override):
    args = {'attempt_id': f'{i:032x}', 'model': 'flash',
            'messages': [{'role': 'user', 'content': 'synthetic'}]}
    receipt = {'handle': args['attempt_id'], 'model': 'flash', 'job_id': None,
               'outcome': 'UNKNOWN', 'status': None, 'cancel_outcome': 'NOT_REQUESTED'}
    self.r.find.return_value = None
    self.r.create.return_value = receipt.copy()
    self.r.load.return_value = receipt.copy()
    with patch.object(self.f, 'request', return_value=(200, {**catalog, **override})) as request:
     result = self.a.call('laf_router_submit_job', args)
    request.assert_called_once_with('GET', '/v1/models')
    self.assertEqual(result['outcome'], 'NOT_SUBMITTED')
    self.assertEqual(result['error'], 'preflight_rejected')


class AppTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.r=Receipts(pathlib.Path(self.tmp.name)/'receipts');self.f=Fake();self.f.receipts=self.r;self.a=Application(self.f,self.r,{'flash'})
 def tearDown(self):self.r.close();self.tmp.cleanup()
 def submit(self):return self.a.call('laf_router_submit_job',{'attempt_id':'1'*32,'model':'flash','messages':[{'role':'user','content':'private text'}]})
 def test_preflight_fail_never_posts(self):
  r=self.a.call('laf_router_submit_job',{'attempt_id':'2'*32,'model':'coder-next','messages':[{'role':'user','content':'x'}]});self.assertEqual(r['outcome'],'NOT_SUBMITTED');self.assertFalse(any(x[0]=='POST' for x in self.f.calls))
 def test_admission_receipt_before_post_and_no_prompt_storage(self):
  r=self.submit();self.assertEqual(r['outcome'],'ADMITTED');self.assertEqual(len(r['handle']),32)
  disk=''.join(p.read_text() for p in pathlib.Path(self.tmp.name).rglob('*.json'));self.assertNotIn('private text',disk);self.assertNotIn('hello',disk)
 def test_unknown_post_not_replayed_after_reopen(self):
  self.f.fail=OSError('SECRET');r=self.submit();self.assertEqual(r['outcome'],'UNKNOWN');self.assertEqual(len(r['handle']),32)
  self.r.close();self.r=Receipts(pathlib.Path(self.tmp.name)/'receipts');self.a=Application(self.f,self.r,{'flash'})
  r2=self.a.call('laf_router_get_job',{'handle':r['handle']});self.assertEqual(r2['outcome'],'UNKNOWN');self.assertEqual(sum(x[0]=='POST' for x in self.f.calls),1)
 def test_only_own_handles(self):
  for args in [{'job_id':'b'*32},{'handle':'b'*32},{'handle':'../x'}]:
   r=self.a.call('laf_router_cancel_job',args);self.assertFalse(any(x[0]=='DELETE' for x in self.f.calls));self.assertTrue(r['error'])
 def test_get_filters_output_and_cancel_once(self):
  r=self.submit();h=r['handle'];o=self.a.call('laf_router_get_job',{'handle':h});self.assertEqual(o['status'],'completed');self.assertNotIn('thinking',str(o));self.assertTrue(o['unverified_output'])
  c=self.a.call('laf_router_cancel_job',{'handle':h});self.assertEqual(c['cancel_outcome'],'REQUESTED');self.assertFalse(c['physical_stop_confirmed'])
  self.a.call('laf_router_cancel_job',{'handle':h});self.assertEqual(sum(x[0]=='DELETE' for x in self.f.calls),1)
 def test_wrong_endpoint_no_get_or_cancel(self):
  r=self.submit();self.f.endpoint_key='c'*64;n=len(self.f.calls)
  self.assertTrue(self.a.call('laf_router_cancel_job',{'handle':r['handle']})['error']);self.assertEqual(n,len(self.f.calls))
 def test_wrong_job_model_becomes_unknown(self):
  r=self.submit();orig=self.f.request
  self.f.request=lambda m,p,b=None:(200,{'id':'b'*32,'model':'coder-next','status':'completed','result':{'content':'x'}}) if p.startswith('/v1/jobs/') else orig(m,p,b)
  o=self.a.call('laf_router_get_job',{'handle':r['handle']});self.assertEqual(o['outcome'],'UNKNOWN')
 def test_status_503_not_ready_and_no_raw_error(self):
  o=self.a.call('laf_router_status',{});self.assertEqual(o['status'],'not_ready');self.assertNotIn('SECRET',str(o));self.assertFalse(o['atomic'])
 def test_status_observes_only_recognized_provider_modes(self):
  cases = [
   (200, {'provider_mode': 'cpu-only'}, 'cpu-only'),
   (503, {'provider_mode': 'host-lifecycle'}, 'host-lifecycle'),
   (200, {}, 'unknown'),
   (200, [], 'unknown'),
   (200, {'provider_mode': ['/private/path']}, 'unknown'),
   (200, {'provider_mode': '/private/path'}, 'unknown'),
  ]
  for code, gpu, mode in cases:
   with self.subTest(code=code, gpu=gpu):
    status = 'ready' if code == 200 else 'not_ready'
    responses = [(code, {'status': status, 'gpu': gpu}),
                 (200, {'api_version': 1, 'npu': {'configured': True}})]
    with patch.object(self.f, 'request', side_effect=responses) as request:
     result = self.a.call('laf_router_status', {})
    self.assertEqual([call.args for call in request.call_args_list],
                     [('GET', '/health'), ('GET', '/v1/capabilities')])
    self.assertEqual(result['outcome'], 'OBSERVED')
    self.assertEqual(result['status'], status)
    self.assertEqual(result['provider_mode'], mode)
    self.assertIs(result['npu_configured'], True)
    self.assertIs(result['npu_readiness_verified'], False)
    self.assertIs(result['hardware_attested'], False)
    self.assertNotIn('/private/path', str(result))
 def test_persist_failure_before_post(self):
  with patch.object(self.r,'create',side_effect=ReceiptError('blocked')):r=self.submit()
  self.assertEqual(r['outcome'],'NOT_SUBMITTED');self.assertFalse(any(x[0]=='POST' for x in self.f.calls))
 def test_persist_failure_after_ack_preserves_known_id(self):
  original=self.r.save
  def save(record):
   if record['job_id'] is not None:raise ReceiptError('blocked')
   return original(record)
  with patch.object(self.r,'save',side_effect=save):r=self.submit()
  self.assertEqual(r['job_id'],'b'*32);self.assertEqual(r['outcome'],'UNKNOWN');self.assertFalse(r['receipt_durable'])
 def test_cancel_failure_persists_unknown_and_never_retries(self):
  r=self.submit();orig=self.f.request
  def call(m,p,b=None):
   if m=='DELETE':self.f.calls.append((m,p,b));raise OSError('SECRET')
   return orig(m,p,b)
  self.f.request=call;c=self.a.call('laf_router_cancel_job',{'handle':r['handle']});self.assertEqual(c['cancel_outcome'],'UNKNOWN')
  self.a.call('laf_router_cancel_job',{'handle':r['handle']});self.assertEqual(sum(x[0]=='DELETE' for x in self.f.calls),1)
if __name__=='__main__':unittest.main()
