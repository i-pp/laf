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

"""Controls for English source, Unicode fixtures, and the pinned logo."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import check_english_source as c
class EnglishSource(unittest.TestCase):
 fixture='mcp/tests/test_contracts.py'
 marker='# Unicode test fixture: exercise multi-byte data.\n'
 unicode='\u3042'
 def test_ascii_source_and_pinned_logo_allowed(self):
  logo_bytes=(c.ROOT/c.LOGO_PATH).read_bytes()
  with TemporaryDirectory() as temp:
   root=Path(temp)
   (root/'source.py').write_text('value = "English"\n',encoding='utf-8')
   logo=root/c.LOGO_PATH
   logo.parent.mkdir()
   logo.write_bytes(logo_bytes)
   tracked=f'source.py\0{c.LOGO_PATH}\0'.encode()
   with patch.object(c,'ROOT',root),patch.object(c.subprocess,'check_output',return_value=tracked):
    with redirect_stdout(StringIO()):
     self.assertEqual(c.main(),0)
    for replacement in (logo_bytes+b'\0',b'ASCII text'):
     with self.subTest(replacement_size=len(replacement)),redirect_stdout(StringIO()) as output:
      logo.write_bytes(replacement)
      self.assertEqual(c.main(),1)
      self.assertIn('Plugin logo differs from the approved PNG',output.getvalue())
 def test_non_english_document_refused(self):self.assertTrue(c.check_text('docs/guide.md',self.unicode))
 def test_production_string_refused_even_with_marker(self):self.assertTrue(c.check_text('src/main.py',self.marker+'value="'+self.unicode+'"\n'))
 def test_marked_test_literal_allowed(self):self.assertEqual(c.check_text(self.fixture,self.marker+'value="'+self.unicode+'"\n'),[])
 def test_unmarked_test_literal_refused(self):self.assertTrue(c.check_text(self.fixture,'value="'+self.unicode+'"\n'))
 def test_test_comment_refused(self):self.assertTrue(c.check_text(self.fixture,self.marker+'# '+self.unicode+'\n'))
 def test_test_identifier_refused(self):self.assertTrue(c.check_text(self.fixture,self.marker+self.unicode+'=1\n'))
 def test_invalid_fixture_syntax_refused(self):self.assertTrue(c.check_text(self.fixture,self.marker+'value="'+self.unicode+'\n'))
 def test_unlisted_test_path_refused(self):self.assertTrue(c.check_text('tests/test_other.py',self.marker+'value="'+self.unicode+'"\n'))
if __name__=='__main__':unittest.main(verbosity=2)
