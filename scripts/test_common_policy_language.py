#!/usr/bin/env python3
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

# Added 2026-11-12: integrated source-tree archive preparation.

"""English prompts must retain the exact legacy policy parsing contract."""
import hashlib,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/private_codex/src'))
import common_policy as policy
class PolicyLanguage(unittest.TestCase):
 def bundle(self):
  text=policy._TITLE+'1.2\nCanonical verified content.\n'
  return policy.PolicyBundle(text,'1.2',hashlib.sha256(text.encode()).hexdigest(),'/synthetic/policy')
 def test_legacy_header_bytes_retained(self):
  expected='# AI'+''.join(chr(n) for n in [38283,30330,12458,12540,12465,12473,12488,12524,12540,12471,12519,12531,32113,21512,25351,31034])+' v'
  self.assertEqual(policy._TITLE,expected)
 def test_english_generated_preamble_preserves_verified_body(self):
  bundle=self.bundle();text=policy.developer_instructions(bundle,'Specific role.')
  preamble,body=text.split('--- BEGIN COMMON POLICY ---\n',1)
  self.assertTrue(preamble.isascii())
  for phrase in ('verified snapshot','does not grant permissions or approval','Higher-priority runtime rules','existing safety guards','additional isolation','scope, authentication and cost limits'):
   self.assertIn(phrase,preamble)
  self.assertIn(bundle.text+'--- END COMMON POLICY ---',body)
  self.assertTrue(text.endswith('Specific role.'))
 def test_heading_mismatch_still_refused(self):
  with self.assertRaises(policy.PolicyError):policy._heading('Different heading v1.2','1.2')
 def test_tampered_policy_still_refused(self):
  bundle=self.bundle();bad=bundle._replace(text=bundle.text+'tampered')
  with self.assertRaises(policy.PolicyError):policy.developer_instructions(bad)
if __name__=='__main__':unittest.main(verbosity=2)
