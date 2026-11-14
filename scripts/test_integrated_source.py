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

"""Pure source/ZIP tests, no SDK child or backend operations."""
import importlib.util,json,tempfile,unittest,zipfile
from pathlib import Path
SPEC=importlib.util.spec_from_file_location('source_build',Path(__file__).with_name('build-integrated-source.py'));b=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(b)
class Source(unittest.TestCase):
 def fixture(self,d):
  root=Path(d)/'source';root.mkdir();names=b.REQUIRED|{'.github/license-policy.json','laf'}
  for n in names:
   f=root/n;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('synthetic')
  self.policy(root,names);return root
 def policy(self,r,names):(r/'.github/license-policy.json').write_text(json.dumps({'rules':[{'paths':sorted(names)}]}))
 def test_source_archive_no_private_zip_needed(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=self.fixture(d);out=Path(d)/'out.zip';b.build(root,out)
   with zipfile.ZipFile(out) as z:self.assertEqual(z.read('launcher.py'),b'synthetic');self.assertEqual(z.getinfo('laf').external_attr>>16,0o100755);self.assertEqual(z.getinfo('launcher.py').external_attr>>16,0o100600)
 def test_parent_traversal_cannot_create_output_inside_source(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=self.fixture(d);other=Path(d)/'other';other.mkdir()
   output=other/'..'/'source'/'inside.zip'
   with self.assertRaises(ValueError):b.build(root,output)
   self.assertFalse((root/'inside.zip').exists())
 def test_inside_alias_matrix_refused(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=self.fixture(d)
   for output in (root/'inside.zip',Path('/'+str(root))/'inside.zip',root/'.'/'inside.zip'):
    with self.subTest(output=str(output)):
     with self.assertRaises(ValueError):b.build(root,output)
     self.assertFalse((root/'inside.zip').exists())
 def test_symlink_output_parent_refused(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=self.fixture(d);actual=Path(d)/'outside';actual.mkdir();link=Path(d)/'link';link.symlink_to(actual,target_is_directory=True)
   with self.assertRaises(ValueError):b.build(root,link/'out.zip')
   self.assertFalse((actual/'out.zip').exists())
 def test_double_slash_outside_sibling_canonicalized(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=self.fixture(d);outside=Path(d)/'outside';outside.mkdir();out=Path('/'+str(outside))/'out.zip'
   b.build(root,out)
   self.assertTrue((outside/'out.zip').is_file());self.assertFalse((root/'out.zip').exists())
 def test_existing_output_preserved(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=self.fixture(d);out=Path(d)/'out.zip';out.write_bytes(b'keep')
   with self.assertRaises(ValueError):b.build(root,out)
   self.assertEqual(out.read_bytes(),b'keep')
 def test_duplicate_policy(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=self.fixture(d);self.policy(root,list(b.REQUIRED)+['launcher.py'])
   with self.assertRaises(ValueError):b.source_paths(root)
 def test_missing_source(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=self.fixture(d);(root/'launcher.py').unlink()
   with self.assertRaises(ValueError):b.source_paths(root)
 def test_symlink_source(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=self.fixture(d);(root/'launcher.py').unlink();(root/'launcher.py').symlink_to(root/'LICENSE')
   with self.assertRaises(ValueError):b.source_paths(root)
 def test_forbidden_path(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=self.fixture(d);self.policy(root,b.REQUIRED|{'.runtime/token'})
   with self.assertRaises(ValueError):b.source_paths(root)
if __name__=='__main__':unittest.main(verbosity=2)
