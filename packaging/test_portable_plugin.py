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

# Added 2026-11-12: original portable integrated package preparation.

"""Pure validation tests; no child, socket, SDK or manager invocation."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import portable_launcher as p

class Portable(unittest.TestCase):
 def config(self):return {'schema':1,'profile':'integrated-32','sdk_python':'/sdk/python','runtime':'/owner/project/.runtime/runtime','runtime_project_root':'/owner/project','router_socket':'/owner/router.sock','receipt_directory':'/owner/receipts','models':['flash'],'capabilities':list(p.CAPABILITIES)}
 def test_full_profile_argument_contract(self):
  args=p.arguments(self.config(),Path('/plugin'))
  self.assertEqual(args[:3],['/sdk/python','-B','/plugin/runtime/integration/laf_stdio_cli.py'])
  for flag in p.CAPABILITIES:self.assertIn('--allow-'+flag,args)
  self.assertIn('--allow-laf-router',args);self.assertNotIn('--allow-writes',args)
 def test_no_silent_five_tool_fallback(self):
  x=self.config();x['capabilities']=[]
  with self.assertRaises(p.ConfigError):p.arguments(x,Path('/plugin'))
 def test_unknown_key_refused(self):
  x=self.config();x['allow_writes']=True
  with self.assertRaises(p.ConfigError):p.arguments(x,Path('/plugin'))
 def test_duplicate_json_refused(self):
  with self.assertRaises(p.ConfigError):p.decode('{"schema":1,"schema":1}')
 def test_nonfinite_json_refused(self):
  with self.assertRaises(p.ConfigError):p.decode('{"x":NaN}')
 def test_relative_path_refused(self):
  x=self.config();x['runtime']='relative'
  with self.assertRaises(p.ConfigError):p.arguments(x,Path('/plugin'))
 def test_parent_path_refused(self):
  x=self.config();x['receipt_directory']='/owner/../receipts'
  with self.assertRaises(p.ConfigError):p.arguments(x,Path('/plugin'))
 def test_nul_path_refused(self):
  x=self.config();x['runtime']='/owner/\0bad'
  with self.assertRaises(p.ConfigError):p.arguments(x,Path('/plugin'))
 def test_model_duplicate_refused(self):
  x=self.config();x['models']=['flash','flash']
  with self.assertRaises(p.ConfigError):p.arguments(x,Path('/plugin'))
 def test_model_injection_refused(self):
  x=self.config();x['models']=['flash; command']
  with self.assertRaises(p.ConfigError):p.arguments(x,Path('/plugin'))
 def test_bool_schema_refused(self):
  x=self.config();x['schema']=True
  with self.assertRaises(p.ConfigError):p.arguments(x,Path('/plugin'))
 def test_capability_duplicate_refused(self):
  x=self.config();x['capabilities'].append(x['capabilities'][0])
  with self.assertRaises(p.ConfigError):p.arguments(x,Path('/plugin'))
 def test_environment_does_not_forward_credentials_or_python_override(self):
  with patch.dict(os.environ,{'OPENAI_API_KEY':'SECRET','OPENAI_ADMIN_KEY':'SECRET','CONTROL_PLANE_API_KEY':'SECRET','PYTHONPATH':'hostile','LD_PRELOAD':'hostile'}):
   env=p.clean_environment()
  self.assertEqual(set(env),{'PATH','HOME','LANG','PYTHONDONTWRITEBYTECODE'})
 def test_private_config_reader(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   path=Path(d)/'config.json';path.write_text(json.dumps(self.config()));path.chmod(0o600)
   self.assertEqual(p.read_config(path),self.config())
 def test_config_group_read_refused(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   path=Path(d)/'config.json';path.write_text('{}');path.chmod(0o640)
   with self.assertRaises(p.ConfigError):p.read_config(path)
 def test_config_symlink_refused(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   path=Path(d)/'target';path.write_text('{}');path.chmod(0o600);link=Path(d)/'config';link.symlink_to(path)
   with self.assertRaises(p.ConfigError):p.read_config(link)
 def test_wrong_profile_refused(self):
  x=self.config();x['profile']='standalone-five'
  with self.assertRaises(p.ConfigError):p.arguments(x,Path('/plugin'))
 def test_failure_does_not_print_protocol_stdout(self):
  import io
  with patch('sys.stdout',new_callable=io.StringIO) as out,patch('sys.stderr',new_callable=io.StringIO) as err:
   self.assertEqual(p.main(['--config','/nonexistent/config']),1)
  self.assertEqual(out.getvalue(),'');self.assertIn('unavailable',err.getvalue())

if __name__=='__main__':unittest.main(verbosity=2)
