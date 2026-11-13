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

"""Build a new source-only ZIP from explicit license-policy paths. No installation."""
import argparse,json,os,stat,zipfile
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
EXECUTABLES={'laf','inference/laf_npu_service/build.sh','runtime/public_laf/laf','runtime/public_laf/inference/laf_npu_service/build.sh'}
REQUIRED={'plugin.json','mcp.json','launcher.py','runtime/integration/laf_stdio_cli.py','runtime/integration/launch.py','runtime/public_laf/inference/laf_service/core.py','runtime/public_laf/inference/laf_service/server.py','SOURCE_ORIGIN.md','NOTICE','LICENSE'}
def pairs(items):
 result={}
 for key,value in items:
  if key in result:raise ValueError('duplicate JSON key')
  result[key]=value
 return result
def source_paths(root):
 policy=json.loads((root/'.github/license-policy.json').read_text(),object_pairs_hook=pairs)
 names=[n for rule in policy['rules'] for n in rule['paths']]
 if len(names)!=len(set(names)) or not REQUIRED<=set(names):raise ValueError('Incomplete or duplicate source policy')
 for name in names:
  p=PurePosixPath(name)
  if p.is_absolute() or '..' in p.parts or '\\' in name or any(c in name for c in '*?['):raise ValueError('Not an explicit source path')
  if any(x in ('.git','.venv','__pycache__','.runtime','evidence','.app.json') for x in p.parts):raise ValueError('Forbidden source path')
  path=root/name
  if any(parent.is_symlink() for parent in [path,*path.parents]) or not path.is_file():raise ValueError('Missing/nonregular source path')
 return sorted(names)
def build(root,output):
 root=Path(root).resolve();output=Path(output)
 if not output.is_absolute() or '..' in output.parts or output.exists() or output.is_symlink():raise ValueError('New absolute output outside source required')
 if any(p.is_symlink() for p in output.parents):raise ValueError('Symlink output parent')
 # Canonical containment is authoritative, including POSIX double-leading slash.
 # Open this exact canonical destination rather than the caller's lexical alias.
 output=output.parent.resolve(strict=True)/output.name
 if output==root or root in output.parents:raise ValueError('Output must be outside source')
 if output.exists() or output.is_symlink():raise ValueError('Existing output preserved')
 names=source_paths(root)
 # Exclusive creation. Failed output is retained; never retry/reuse automatically.
 with output.open('xb') as stream,zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
  for name in names:
   info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=(stat.S_IFREG|(0o755 if name in EXECUTABLES else 0o600))<<16;info.compress_type=zipfile.ZIP_DEFLATED
   z.writestr(info,(root/name).read_bytes())
 return {'status':'source_archive_created','files':len(names),'installs':0,'service_changes':0,'rights_clearance':'separate release prerequisite'}
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();os.umask(0o077)
 try:print(json.dumps(build(ROOT,args.output)));return 0
 except (OSError,ValueError,KeyError):print('Source build refused; existing/partial output preserved.');return 1
if __name__=='__main__':raise SystemExit(main())
