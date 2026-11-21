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

import tempfile,pathlib,os,unittest,stat,types
from unittest.mock import patch
from laf_mcp.receipts import Receipts,ReceiptError
class ReceiptTests(unittest.TestCase):
 def test_modes_and_no_pruning(self):
  with tempfile.TemporaryDirectory() as t:
   r=Receipts(pathlib.Path(t)/'receipts')
   with r.locked():
    first=r.create('a'*64,'flash','1'*32,'f'*64);r.MAX_RECORDS=1
    with self.assertRaises(ReceiptError):r.create('a'*64,'flash','1'*32,'f'*64)
   self.assertEqual(r.load(first['handle'])['outcome'],'UNKNOWN');self.assertEqual(os.stat(pathlib.Path(t)/'receipts').st_mode&0o777,0o700);r.close()
 def test_new_directory_parent_is_synced(self):
  with tempfile.TemporaryDirectory() as t:
   seen=[];original=os.fsync
   def sync(fd):seen.append(os.readlink('/proc/self/fd/'+str(fd)));return original(fd)
   with patch('os.fsync',side_effect=sync):r=Receipts(pathlib.Path(t)/'receipts')
   r.close();self.assertIn(t,seen)
 def test_zero_progress_write_is_rejected_not_looped(self):
  with tempfile.TemporaryDirectory() as t:
   r=Receipts(pathlib.Path(t)/'receipts')
   with patch('os.write',side_effect=[0,RuntimeError('would loop')]):
    with self.assertRaises(ReceiptError):r.create('a'*64,'flash','1'*32,'f'*64)
   r.close()
 def test_symlink_and_bad_mode_receipts_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   r=Receipts(pathlib.Path(t)/'receipts');x=r.create('a'*64,'flash','1'*32,'f'*64);p=pathlib.Path(t)/'receipts'/(x['handle']+'.json');os.chmod(p,0o644)
   with self.assertRaises(ReceiptError):r.load(x['handle'])
   r.close()
 def test_foreign_owned_ancestor_is_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   parent=pathlib.Path(t);orig=pathlib.Path.lstat
   def changed(path,*a,**kw):
    value=orig(path,*a,**kw)
    if path==parent:return types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=os.getuid()+1000)
    return value
   with patch.object(pathlib.Path,'lstat',changed),self.assertRaises(ReceiptError):Receipts(parent/'receipts')
if __name__=='__main__':unittest.main()
