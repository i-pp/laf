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

import hashlib,json,threading,time,unittest
import test_application
class AttemptTests(unittest.TestCase):
 setUp=test_application.AppTests.setUp
 tearDown=test_application.AppTests.tearDown
 submit=test_application.AppTests.submit
 def test_same_attempt_repeated_never_reposts(self):
  first=self.submit();second=self.submit();self.assertEqual(first['handle'],second['handle']);self.assertEqual(sum(x[0]=='POST' for x in self.f.calls),1)
 def test_same_attempt_different_request_is_conflict(self):
  first=self.submit();out=self.a.call('laf_router_submit_job',{'attempt_id':'1'*32,'model':'flash','messages':[{'role':'user','content':'different'}]})
  self.assertEqual(out['error'],'attempt_conflict');self.assertEqual(out['handle'],first['handle']);self.assertEqual(sum(x[0]=='POST' for x in self.f.calls),1)
 def test_same_attempt_known_not_submitted_never_reposts(self):
  orig=self.f.request
  def rejected(m,p,b=None):
   if m=='POST':self.f.calls.append((m,p,b));return 503,{'error':'full'}
   return orig(m,p,b)
  self.f.request=rejected;one=self.submit();self.assertEqual(one['outcome'],'NOT_SUBMITTED');self.f.request=orig
  two=self.submit();self.assertEqual(two['outcome'],'NOT_SUBMITTED');self.assertEqual(sum(x[0]=='POST' for x in self.f.calls),1)
 def test_concurrent_nonce_only_one_submission(self):
  orig=self.f.request
  def slow(m,p,b=None):
   if m=='POST':time.sleep(.05)
   return orig(m,p,b)
  self.f.request=slow;results=[];threads=[threading.Thread(target=lambda:results.append(self.submit())) for _ in range(5)]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertEqual(sum(x[0]=='POST' for x in self.f.calls),1);self.assertTrue(all(r.get('handle')=='1'*32 for r in results))
 def test_hash_is_not_exposed(self):
  r=self.submit();stored=self.r.load(r['handle']);self.assertEqual(len(stored['request_hash']),64);self.assertNotIn(stored['request_hash'],json.dumps(r))
 def test_corrupt_receipt_preserves_unknown_handle(self):
  r=self.submit()
  p=self.r.path/(r['handle']+'.json');p.write_text('{bad')
  out=self.submit();self.assertEqual(out['outcome'],'UNKNOWN');self.assertEqual(out['handle'],'1'*32);self.assertEqual(sum(x[0]=='POST' for x in self.f.calls),1)
 def test_terminal_observation_does_not_regress(self):
  r=self.submit();h=r['handle'];first=self.a.call('laf_router_get_job',{'handle':h});self.assertEqual(first['status'],'completed')
  orig=self.f.request
  self.f.request=lambda m,p,b=None:(200,{'id':'b'*32,'model':'flash','status':'running','result':None}) if p.startswith('/v1/jobs/') else orig(m,p,b)
  out=self.a.call('laf_router_get_job',{'handle':h});self.assertEqual(out['status'],'completed');self.assertEqual(out['outcome'],'TERMINAL');self.assertNotIn('content',out);self.assertEqual(self.r.load(h)['status'],'completed')
if __name__=='__main__':unittest.main()
