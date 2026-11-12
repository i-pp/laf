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

"""Offline manifest and source-boundary checks; not a runtime acceptance test."""
import ast
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
def check():
 p=json.loads((ROOT/'plugin.json').read_text());m=json.loads((ROOT/'mcp.json').read_text())
 assert re.fullmatch('[a-z][a-z0-9-]{0,63}',p['name']) and p['name']=='laf'
 assert re.fullmatch(r'\d+\.\d+\.\d+',p['version'])
 assert not set(p)&{'skills','mcpServers','apps','interface'}
 assert p['$schema']=='https://agent-plugins.org/schemas/1.0.0/plugin.schema.json'
 assert set(m['mcpServers'])=={'laf'}
 c=m['mcpServers']['laf'];assert c=={'type':'stdio','command':'python3','args':['${PLUGIN_ROOT}/mcp/launch.py'],'cwd':'${PLUGIN_ROOT}'}
 assert (ROOT/'mcp/launch.py').is_file()
 tree=ast.parse((ROOT/'mcp/src/laf_mcp/__init__.py').read_text())
 versions=[n.value.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__version__' for t in n.targets)]
 assert versions==[p['version']]
 assert (ROOT/'mcp/LICENSE').read_bytes()==(ROOT/'LICENSE').read_bytes()
 assert 'name: laf-router' in (ROOT/'skills/laf-router/SKILL.md').read_text()
 for f in (ROOT/'mcp').rglob('*'):
  assert not f.is_symlink()
  if f.suffix=='.py':
   assert 'SPDX-License-Identifier: LicenseRef-KOL-v1' in f.read_text()
 return {'manifest':'passed','server_version':p['version'],'transport':'stdio','runtime_acceptance':False}
if __name__=='__main__':
 try:print(json.dumps(check(),sort_keys=True))
 except Exception:print('plugin source validation failed',file=sys.stderr);raise SystemExit(1)
