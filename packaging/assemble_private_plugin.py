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

"""Assemble a private review candidate, never a public upload or installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import stat
import zipfile

INTEGRATION=frozenset(('laf_shared_tools.py','laf_stdio_adapter.py','laf_stdio_cli.py','laf_stdio_lifecycle.py','laf_stdio_transport.py','launch.py'))
TOP_CONFIG=frozenset(('scheduler-config.json','resource-limits.json','verification-commands.json'))
REQUIRED=frozenset({'integration/'+x for x in INTEGRATION}|{'private_codex/src/'+x for x in ('legacy_tool_adapter.py','mcp_adapter.py','oauth_auth.py','experiments.py','codex_host.py','codex_pipeline.py','runtime_scope.py','mailbox_grants.py')}|{'public_laf/mcp/src/laf_mcp/'+x for x in ('server.py','router.py','receipts.py','application.py','contracts.py','__init__.py')}|{'public_laf/LICENSE','public_laf/NOTICE','public_laf/mcp/licenses/MCP-SDK-LICENSE.txt'})
REQUIRED=REQUIRED|frozenset(('public_laf/inference/laf_service/core.py','public_laf/inference/laf_service/server.py','public_laf/inference/laf_service/host_supervisor.py','public_laf/inference/local_platform/cli.py','public_laf/laf','public_laf/skills/laf-router/SKILL.md'))
class PackageError(ValueError):pass

def selected(name):
 p=PurePosixPath(name)
 if name.startswith('integration/'):return len(p.parts)==2 and p.name in INTEGRATION
 if name.startswith('private_codex/src/'):return len(p.parts)==3 and p.suffix=='.py'
 if name.startswith('private_codex/config/'):return len(p.parts)==3 and p.suffix=='.json'
 if name.startswith('private_codex/'):return len(p.parts)==2 and p.name in TOP_CONFIG
 if name.startswith('public_laf/'):
  rel=p.parts[1:]
  if any(x.startswith('.') or x in ('__pycache__','runtime','evidence','models','vendor','node_modules') for x in rel):return False
  if len(rel)==1:return rel[0] in ('laf','README.md','CHANGES.md','LICENSE','NOTICE','LICENSING.md','THIRD_PARTY_NOTICES.md','pyproject.toml')
  if rel[0] in ('inference','docs','scripts','skills'):
   return p.suffix in ('.py','.cpp','.h','.md','.json','.sh','.example') or p.name=='Dockerfile'
 if name.startswith('public_laf/mcp/src/laf_mcp/'):return len(p.parts)==5 and p.suffix=='.py'
 if name.startswith('public_laf/mcp/licenses/'):return len(p.parts)==4 and p.suffix=='.txt'
 if name.startswith('public_laf/LICENSES/'):return len(p.parts)==3 and p.suffix=='.txt'
 if name in ('public_laf/LICENSE','public_laf/NOTICE','public_laf/THIRD_PARTY_NOTICES.md','public_laf/mcp/LICENSE','public_laf/mcp/NOTICE','public_laf/mcp/THIRD_PARTY_NOTICES.md'):return True
 if name.startswith('public-dependency-notices/'):return p.name in ('LICENSE','NOTICE','SCOPE.md') and len(p.parts)==3
 return False

def source_members(bundle,expected):
 if not re.fullmatch('[a-f0-9]{64}',expected):raise PackageError('Exact source artifact SHA256 required')
 if bundle.stat().st_size>32*1024**2 or hashlib.sha256(bundle.read_bytes()).hexdigest()!=expected:raise PackageError('Source artifact differs from reviewed bundle')
 result={};seen=set()
 with zipfile.ZipFile(bundle) as z:
  if len(z.infolist())>2000 or sum(i.file_size for i in z.infolist())>64*1024**2:raise PackageError('Source archive exceeds limits')
  for i in z.infolist():
   path=PurePosixPath(i.filename)
   if path.is_absolute() or '..' in path.parts or '\\' in i.filename or i.filename in seen:raise PackageError('Unsafe or duplicate archive path')
   seen.add(i.filename)
   mode=(i.external_attr>>16)&0o170000
   if mode not in (0,stat.S_IFREG,stat.S_IFDIR):raise PackageError('Non-regular archive member')
   if i.is_dir() or not i.filename.startswith('stdio/'):continue
   name=i.filename[len('stdio/'):]
   if selected(name):result[name]=z.read(i)
 if not REQUIRED<=set(result):raise PackageError('Reviewed integrated stdio/HTTP source is incomplete')
 return result

# Restore only public Git executable metadata; never chmod staged/host files.
EXECUTABLE_ARCHIVE_PATHS=frozenset(('runtime/public_laf/laf','runtime/public_laf/inference/laf_npu_service/build.sh'))
def archive_mode(name):
 return stat.S_IFREG | (0o755 if name in EXECUTABLE_ARCHIVE_PATHS else 0o600)

def archive_private_candidate(directory,archive):
 directory=Path(directory)
 with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
  for path in sorted(directory.rglob('*')):
   if path.is_symlink():raise PackageError('Symlink in candidate')
   if not path.is_file():continue
   name=path.relative_to(directory).as_posix()
   info=zipfile.ZipInfo(name);info.create_system=3
   info.external_attr=archive_mode(name)<<16
   info.compress_type=zipfile.ZIP_DEFLATED
   z.writestr(info,path.read_bytes())

def manifest():
 return {'$schema':'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json','name':'laf','version':'0.1.0','description':'Local AI Fabric integrated Codex/orchestration and owner-scoped Router tools. Private review candidate.','author':{'name':'I-plusplus Inc.'},'extensions':{'com.openai':{'interface':{'displayName':'Local AI Fabric','shortDescription':'Local models and Codex tools','category':'Developer Tools'}}}}

def mcp_manifest():
 return {'$schema':'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json','mcpServers':{'laf':{'type':'stdio','command':'python3','args':['-B','${PLUGIN_ROOT}/launcher.py'],'cwd':'${PLUGIN_ROOT}'}}}

def assemble(bundle,expected,output,templates):
 output=Path(output)
 if not output.is_absolute() or output.exists() or output.is_symlink():raise PackageError('New absolute private output directory required')
 for parent in output.parents:
  marker=parent/'.git'
  if parent.is_symlink() or marker.is_symlink() or marker.is_file() or (marker/'HEAD').is_file():raise PackageError('Output must be outside Git and symlink-free')
 payload=source_members(Path(bundle),expected)
 os.umask(0o077);output.mkdir(mode=0o700)
 try:
  for name,data in payload.items():
   dest=output/'runtime'/name;dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
   with dest.open('xb') as f:f.write(data)
  for source,target in [('portable_launcher.py','launcher.py'),('INTEGRATED_PLUGIN_GUIDE.md','README.md')]:
   with (output/target).open('xb') as f:f.write((templates/source).read_bytes())
  notices=output/'notices'/'agent-orchestrator';notices.mkdir(parents=True,mode=0o700)
  for name in ('LICENSE','NOTICE'):
   with (notices/name).open('xb') as f:f.write((templates/'upstream-orchestrator'/name).read_bytes())
  for name in ('CHANGES.md','SOURCE_ORIGIN.md'):
   with (output/name).open('xb') as f:f.write((templates/name).read_bytes())
  for name,value in [('plugin.json',manifest()),('mcp.json',mcp_manifest()),('ASSEMBLY_STATUS.json',{'schema':1,'status':'private_review_candidate','submission_ready':False,'publication_authorized':False,'source_bundle_sha256':expected,'runtime_source_files':len(payload),'runtime_executed':False,'sdk_or_models_bundled':False,'known_gaps':['Rights/changes review for imported snapshot additions','Clean-host daemon/policy/isolation provisioning acceptance','Installed plugin tool discovery and refreshed host catalog','Public listing URLs, verified publisher, supported countries and review materials']})]:
   with (output/name).open('x') as f:json.dump(value,f,indent=2);f.write('\n')
  with (output/'LICENSE').open('xb') as f:f.write(payload['public_laf/LICENSE'])
  with (output/'NOTICE').open('xb') as f:f.write(payload['public_laf/NOTICE']+b'\nIntegrated package scope: the public_laf/mcp stdio-only notice applies only to that original component. This assembled source also retains integrated HTTP transport, Core/Router and CLI source. See SOURCE_ORIGIN.md and notices/agent-orchestrator for attribution and pending local-modification rights.\n')
 except BaseException:
  # Retain partial output for inspection; never reuse it automatically.
  raise
 return {'status':'private_review_candidate','output':str(output),'runtime_source_files':len(payload),'submission_ready':False,'source_bundle_sha256':expected}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True);p.add_argument('--sha256',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 try:print(json.dumps(assemble(a.bundle,a.sha256,a.output,Path(__file__).parent)));return 0
 except (OSError,ValueError,zipfile.BadZipFile):print('Assembly stopped; any partial output retained. No installation, service or publication operation.');return 1
if __name__=='__main__':raise SystemExit(main())
