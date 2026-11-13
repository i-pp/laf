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

"""Pure metadata contracts for Codex packaging; never installs or connects."""
import ast,json,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def read(name):return json.loads((ROOT/name).read_text())
class CodexPackage(unittest.TestCase):
 def test_release_identity_and_both_protocol_versions(self):
  self.assertEqual(read('plugin.json')['version'],'0.1.0')
  for name in ('mcp_adapter.py','legacy_tool_adapter.py'):
   tree=ast.parse((ROOT/'runtime/private_codex/src'/name).read_text())
   values=[ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SERVER_INFO' for t in n.targets)]
   self.assertEqual(len(values),1);self.assertEqual(values[0]['version'],'0.1.0')
 def test_codex_compatibility_manifest_matches_portable_identity(self):
  portable=read('plugin.json');compat=read('.codex-plugin/plugin.json')
  for key in ('name','version','description','author'):self.assertEqual(compat[key],portable[key])
  self.assertEqual(compat['interface'],portable['extensions']['com.openai']['interface'])
  self.assertEqual(compat['mcpServers'],'./.mcp.json');self.assertEqual(compat['skills'],'./skills')
  self.assertNotIn('apps',compat)
 def test_codex_mcp_is_local_stdio_without_auth_or_auto_install(self):
  self.assertEqual(read('.mcp.json'),{'mcpServers':{'laf':{'command':'python3','args':['-B','./launcher.py'],'cwd':'.'}}})
  self.assertTrue((ROOT/'launcher.py').is_file())
 def test_marketplace_source_contained_and_classified(self):
  market=read('.agents/plugins/marketplace.json')
  self.assertEqual(market['name'],'laf-experimental')
  self.assertEqual(len(market['plugins']),1)
  self.assertEqual(market['plugins'][0],{'name':'laf','source':{'source':'local','path':'.'},'policy':{'installation':'AVAILABLE','authentication':'ON_USE'},'category':'Developer Tools'})
  names=[p for rule in read('.github/license-policy.json')['rules'] for p in rule['paths']]
  for p in ('.codex-plugin/plugin.json','.mcp.json','.agents/plugins/marketplace.json','docs/codex-install.md','scripts/test_codex_package.py'):self.assertIn(p,names)
if __name__=='__main__':unittest.main(verbosity=2)
