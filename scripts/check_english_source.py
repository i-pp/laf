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

"""English/ASCII source policy guard; intentional Unicode test data is explicit."""
import ast,io,json,subprocess,tokenize
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
UNICODE_FIXTURES=frozenset({
 'mcp/tests/test_contracts.py','mcp/tests/test_integrated_router.py',
 'mcp/tests/test_package.py','mcp/tests/test_sdist.py',
 'scripts/test_sdk_installer.py','runtime/public_laf/scripts/test_sdk_installer.py'})
MARKER='# Unicode test fixture:'
def check_text(name,text):
 positions=[(row,col) for row,line in enumerate(text.splitlines(),1) for col,c in enumerate(line) if ord(c)>127]
 if not positions:return []
 if name not in UNICODE_FIXTURES:return [{'path':name,'line':row,'reason':'Non-ASCII prose/source'} for row in sorted({p[0] for p in positions})]
 try:
  ast.parse(text,filename=name)
  spans=[(t.start,t.end) for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type==tokenize.STRING]
 except (SyntaxError,tokenize.TokenError):return [{'path':name,'line':1,'reason':'Invalid Unicode fixture syntax'}]
 lines=text.splitlines();bad=[]
 for row,col in positions:
  marked=row>1 and lines[row-2].strip().startswith(MARKER)
  literal=any(start<=(row,col)<end for start,end in spans)
  if not marked or not literal:bad.append(row)
 return [{'path':name,'line':row,'reason':'Unicode must be marked literal test data, not prose/comment/identifier'} for row in sorted(set(bad))]
def main():
 names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0');names=[n for n in names if n]
 if not names:raise SystemExit('No tracked source checked')
 issues=[];fixture_lines=0
 for name in names:
  path=ROOT/name
  if path.is_symlink() or not path.is_file():
   issues.append({'path':name,'line':0,'reason':'Nonregular/symlink source is not inspected'});continue
  text=path.read_text(encoding='utf-8');issues.extend(check_text(name,text))
  fixture_lines+=sum(not line.isascii() for line in text.splitlines())
 print(json.dumps({'status':'failed' if issues else 'passed','tracked_files':len(names),'violations':issues,'intentional_unicode_fixture_lines':fixture_lines,'scope':'English/ASCII checked source; explicit literal-only Unicode regression data is retained'}))
 return 1 if issues else 0
if __name__=='__main__':raise SystemExit(main())
