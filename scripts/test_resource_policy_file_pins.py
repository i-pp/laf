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

# Added 2026-11-15: protected resource-policy reader and selector metadata boundaries.
"""Synthetic regular/FIFO/type projections only; no device or service open."""
import os,stat,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime/integration'),str(ROOT/'runtime/private_codex/src')]
import laf_bootstrap as boot
import scheduler
import test_bootstrap_resource_policy as fixtures

class SourcePins(unittest.TestCase):
 def setUp(self):
  f=fixtures.ResourcePolicyImport('test_raw_bytes_and_provenance_are_retained_without_source_drift')
  f.setUp();self.addCleanup(f.doCleanups);self.f=f
 def test_unreadable_binding_discovery_never_falls_back_to_legacy(self):
  self.f.configure();selected=scheduler.configuration();self.assertTrue(scheduler.resource_only(selected))
  real_stat,real_lstat=os.stat,os.lstat
  for targets in ({self.f.marker},{self.f.marker,self.f.project/'config/policy.json'}):
   def deny(original):
    def metadata(path,*args,**kw):
     if isinstance(path,(str,bytes,os.PathLike)) and Path(path) in targets:raise PermissionError('synthetic inaccessible selector')
     return original(path,*args,**kw)
    return metadata
   with patch('os.stat',side_effect=deny(real_stat)),patch('os.lstat',side_effect=deny(real_lstat)):
    with self.assertRaises((OSError,ValueError)):scheduler.configuration()
 def test_unreadable_bootstrap_policy_discovery_is_not_known_absence(self):
  self.f.marker.unlink();target=self.f.project/'config/policy.json'
  real_stat,real_lstat=os.stat,os.lstat
  def deny(original):
   def metadata(path,*args,**kw):
    if isinstance(path,(str,bytes,os.PathLike)) and Path(path)==target:raise PermissionError('synthetic inaccessible bootstrap selection')
    return original(path,*args,**kw)
   return metadata
  with patch('os.stat',side_effect=deny(real_stat)),patch('os.lstat',side_effect=deny(real_lstat)):
   with self.assertRaises((OSError,ValueError)):scheduler.configuration()
 def test_fifo_swap_is_rejected_before_any_read_open(self):
  real=boot.os.open;seen=[];swapped=False
  def opening(path,flags,*args,**kw):
   nonlocal swapped
   if Path(path).name==self.f.source.name and kw.get('dir_fd') is not None:
    self.assertTrue(flags&os.O_PATH)
    if not swapped:swapped=True;self.f.source.unlink();os.mkfifo(self.f.source,0o600)
   if str(path).startswith('/proc/self/fd/'):seen.append(path)
   return real(path,flags,*args,**kw)
  with patch.object(boot.os,'open',side_effect=opening):self.f.refuse(self.f.configure)
  self.assertTrue(swapped);self.assertEqual(seen,[])
 def test_projected_device_type_is_refused_before_device_callback(self):
  real_open,real_stat=boot.os.open,boot.os.fstat;pins=set();reads=[]
  def opening(path,flags,*args,**kw):
   if str(path).startswith('/proc/self/fd/'):reads.append(path)
   fd=real_open(path,flags,*args,**kw)
   if Path(path).name==self.f.source.name and kw.get('dir_fd') is not None:
    self.assertTrue(flags&os.O_PATH);pins.add(fd)
   return fd
  def metadata(fd):
   value=real_stat(fd)
   if fd in pins:
    fields=list(value);fields[0]=stat.S_IFCHR|0o600;return os.stat_result(fields)
   return value
  with patch.object(boot.os,'open',side_effect=opening),patch.object(boot.os,'fstat',side_effect=metadata):
   self.f.refuse(self.f.configure)
  self.assertTrue(pins);self.assertEqual(reads,[])
 def test_replacement_before_pinned_read_never_publishes_new_source(self):
  real=boot.os.open;changed=False;original=self.f.source.read_bytes()
  def opening(path,flags,*args,**kw):
   nonlocal changed
   if str(path).startswith('/proc/self/fd/') and not changed:
    changed=True;replacement=self.f.f.parent/'replacement';replacement.write_bytes(original)
    replacement.chmod(0o600);os.replace(replacement,self.f.source)
   return real(path,flags,*args,**kw)
  with patch.object(boot.os,'open',side_effect=opening):self.f.refuse(self.f.configure)
  self.assertTrue(changed);self.assertEqual(self.f.source.read_bytes(),original)

if __name__=='__main__':unittest.main()
