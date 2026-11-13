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

# Scope: this newly authored file only; imported private source is not relicensed.
"""Prepare a NEW owner-only runtime prefix from source. Never start or install."""
import argparse
import ast
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import sys
import uuid

RUNTIME=Path(__file__).resolve().parents[1]
PACKAGE=RUNTIME.parent
CAPABILITIES=('local-llm','local-models','chat-mailbox','mailbox-grants','codex-pipeline','experiments')
BACKENDS=('orchestrator','local_helper','mailbox','mailbox_grants','codex_pipeline','experiments','router')
POLICY_COMMIT='ddcaf63e50bed722d558d418deeef791f3029a6f'
POLICY_SHA256='beccfd5c9aeb63d6a06f33ef39732e832e7f4e79b0a7915829ca993b55347236'
TOP_CONFIG=('resource-limits.json','scheduler-config.json','verification-commands.json')
NATIVE_LIMIT=512*1024**2
PLUGIN_KEYS={'schema','profile','sdk_python','runtime','runtime_project_root','runtime_source_root','router_socket','receipt_directory','models','capabilities'}
class BootstrapError(ValueError):pass

class Parser(argparse.ArgumentParser):
 def error(self,message):raise BootstrapError('Invalid bootstrap arguments; use --help')

def decode(raw):
 def unique(items):
  result={}
  for key,value in items:
   if key in result:raise BootstrapError('Duplicate JSON key')
   result[key]=value
  return result
 try:
  value=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _ : (_ for _ in ()).throw(ValueError()))
  json.dumps(value,allow_nan=False).encode('utf-8');return value
 except (ValueError,UnicodeError,RecursionError):raise BootstrapError('Invalid bootstrap JSON') from None

def safe_path(path,*,new=False):
 path=Path(path).expanduser()
 if not path.is_absolute() or '..' in path.parts or str(path)=='/':raise BootstrapError('Canonical absolute prefix required')
 if (new and path.is_relative_to(PACKAGE)) or any(path.parts[i:i+2]==('plugins','cache') for i in range(len(path.parts)-1)):
  raise BootstrapError('Runtime prefix must be outside source and plugin cache')
 if new and (path.exists() or path.is_symlink()):raise BootstrapError('Prefix must be NEW; existing data is never changed')
 for item in (path.parent,*path.parent.parents):
  try:info=item.lstat()
  except OSError:raise BootstrapError('Prefix parent must already exist') from None
  if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0,os.getuid()) or info.st_mode & 0o022:
   raise BootstrapError('Protected symlink-free ancestry required')
 if path.parent.resolve()!=path.parent:raise BootstrapError('Canonical symlink-free prefix required')
 if not new:
  info=path.lstat()
  if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
   raise BootstrapError('Private owner prefix required')
 return path

def read_source(path):
 before=path.lstat()
 if not stat.S_ISREG(before.st_mode) or before.st_size>2*1024**2:raise BootstrapError('Regular bounded source required')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as stream:
  opened=os.fstat(stream.fileno());raw=stream.read(2*1024**2+1);after=os.fstat(stream.fileno())
 if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns) or (opened.st_size,opened.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or len(raw)>2*1024**2:
  raise BootstrapError('Source changed or exceeded its bound')
 return raw

def source_paths():
 # Never descend into operational state, even to enumerate its filenames.
 excluded={'__pycache__','node_modules','vendor','models','weights','sdk','cache','logs','state'}
 count=0
 for directory,dirs,names in os.walk(RUNTIME,followlinks=False):
  dirs[:]=sorted(x for x in dirs if not x.startswith('.') and x not in excluded)
  for name in dirs:
   if (Path(directory)/name).is_symlink():raise BootstrapError('Source symlinks are not copied')
  for name in sorted(names):
   if name.startswith('.') or name in {'auth.json','config.toml','models_cache.json'}:continue
   count+=1
   if count>2000:raise BootstrapError('Source inventory exceeds its bound')
   yield Path(directory)/name

def source_snapshot():
 result={}
 for path in source_paths():
  relative=path.relative_to(RUNTIME);parts=relative.parts
  if any(p.startswith('.') or p in ('__pycache__','node_modules','vendor','models','weights','sdk','cache','logs','state') for p in parts):continue
  if path.is_symlink():raise BootstrapError('Source symlinks are not copied')
  if path.is_dir():continue
  include=False
  if parts[0]=='private_codex':
   include=(len(parts)==3 and parts[1]=='src' and path.suffix=='.py' or len(parts)==2 and parts[1] in TOP_CONFIG
      or len(parts)==3 and parts[1]=='config' and path.name=='upstream-policy.json'
      or parts[1:2]==('policy-snapshot',) and (path.suffix in ('.md','.json') or path.name in ('LICENSE','NOTICE')))
  elif parts[0]=='integration':include=len(parts)==2 and path.suffix=='.py'
  elif parts[0]=='public_laf':include=(len(parts)==2 and path.name in ('laf','README.md','CHANGES.md','LICENSE','NOTICE','LICENSING.md','THIRD_PARTY_NOTICES.md','pyproject.toml') or len(parts)>2 and parts[1] in ('mcp','inference','docs','scripts','skills','LICENSES') and (path.suffix in ('.py','.md','.json','.txt','.toml','.sh','.cpp','.h','.example') or path.name in ('LICENSE','NOTICE','Dockerfile')))
  elif parts[0]=='public-dependency-notices':include=path.name in ('LICENSE','NOTICE','SCOPE.md')
  if not include or relative.as_posix()=='private_codex/config/policy.json':continue
  raw=read_source(path)
  if path.suffix=='.py':ast.parse(raw,filename=str(relative))
  result[relative.as_posix()]=raw
 if sum(map(len,result.values()))>64*1024**2:raise BootstrapError('Source snapshot exceeds64MiB')
 required={'private_codex/src/daemon.py','private_codex/src/runner.py','private_codex/src/backend_state.py','private_codex/src/bundled_policy.py','integration/laf_stdio_cli.py','public_laf/mcp/src/laf_mcp/application.py'}|{'private_codex/'+x for x in TOP_CONFIG}
 if not required<=set(result):raise BootstrapError('Genuine integrated runtime source is incomplete')
 return result

def protected_directory(path):
 """Pin an absolute directory without following any symlink component."""
 path=Path(path)
 if not path.is_absolute() or '..' in path.parts:raise BootstrapError('Canonical absolute directory required')
 current=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  for name in path.parts[1:]:
   child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=current)
   os.close(current);current=child
   info=os.fstat(current)
   if info.st_uid not in (0,os.getuid()) or info.st_mode & 0o022:
    raise BootstrapError('Protected directory ancestry required')
  return current
 except BaseException:
  os.close(current);raise

def same_directory(path,fd):
 current=protected_directory(path)
 try:
  info=os.fstat(current);pinned=os.fstat(fd)
  if (info.st_dev,info.st_ino)!=(pinned.st_dev,pinned.st_ino):raise BootstrapError('Directory identity changed')
 finally:os.close(current)

class NewTree:
 """Only directories and files created beneath the pinned NEW root are owned."""
 def __init__(self,fd):self.directories={Path('.'):fd};self.files=[]
 def directory(self,path,*,create=False):
  path=Path(path)
  if path.is_absolute() or '..' in path.parts:raise BootstrapError('Relative new-tree path required')
  current=self.directories[Path('.')];relative=Path('.')
  for name in path.parts:
   relative=relative/name;known=self.directories.get(relative)
   if known is None:
    if not create:raise BootstrapError('New tree directory was not created')
    os.mkdir(name,0o700,dir_fd=current)
   opened=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=current)
   try:
    info=os.fstat(opened)
    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
     raise BootstrapError('New tree directory identity unavailable')
    if known is not None:
     pinned=os.fstat(known)
     if (info.st_dev,info.st_ino)!=(pinned.st_dev,pinned.st_ino):raise BootstrapError('New tree directory identity changed')
    else:self.directories[relative]=opened;opened=None
   finally:
    if opened is not None:os.close(opened)
   current=self.directories[relative]
  return current
 def verify(self):
  for relative in self.directories:self.directory(relative)
  for parent,name,dev,ino in self.files:
   info=os.stat(name,dir_fd=parent,follow_symlinks=False)
   if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=(dev,ino):raise BootstrapError('New file identity changed')
 def cleanup(self):
  # Exact owned file identities and pinned directory fds also cover moved
  # subdirectories. Unknown replacement directories are never recursed into.
  for parent,name,dev,ino in reversed(self.files):
   try:info=os.stat(name,dir_fd=parent,follow_symlinks=False)
   except FileNotFoundError:continue
   if (info.st_dev,info.st_ino)==(dev,ino) and not stat.S_ISDIR(info.st_mode):os.unlink(name,dir_fd=parent)
  for relative,fd in reversed(list(self.directories.items())):
   for name in os.listdir(fd):
    info=os.stat(name,dir_fd=fd,follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode):os.unlink(name,dir_fd=fd)
   if relative!=Path('.'):
    parent=self.directories[relative.parent]
    try:info=os.stat(relative.name,dir_fd=parent,follow_symlinks=False)
    except FileNotFoundError:continue
    pinned=os.fstat(fd)
    if (info.st_dev,info.st_ino)==(pinned.st_dev,pinned.st_ino):os.rmdir(relative.name,dir_fd=parent)
 def close(self):
  for fd in reversed(list(self.directories.values())):os.close(fd)

def write_file(tree,path,raw):
 path=Path(path);parent=tree.directory(path.parent)
 fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
 with os.fdopen(fd,'wb') as stream:
  info=os.fstat(stream.fileno());tree.files.append((parent,path.name,info.st_dev,info.st_ino))
  stream.write(raw);stream.flush();os.fsync(stream.fileno())

def make_directory(tree,path):tree.directory(path,create=True)

def sdk_reference(value):
 if value is None:return None
 path=Path(value).expanduser()
 if not path.is_absolute() or '..' in path.parts:raise BootstrapError('Explicit absolute SDK interpreter required')
 resolved=path.resolve(strict=True);info=resolved.stat()
 if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0,os.getuid()) or info.st_mode & 0o022 or not os.access(resolved,os.X_OK):
  raise BootstrapError('Existing protected SDK interpreter required; no install')
 return str(path)

def prepare_prefix(prefix,*,sdk_python=None,dry_run=False):
 if sys.version_info<(3,11):raise BootstrapError('Bootstrap requires Python3.11 or later')
 prefix=safe_path(prefix,new=True);sdk=sdk_reference(sdk_python)
 sys.path.insert(0,str(RUNTIME/'private_codex/src'))
 from bundled_policy import load_snapshot
 selection={'schema_version':2,'source':'bundled_snapshot','bundle_root':'policy-snapshot',
            'expected_version':'git:'+POLICY_COMMIT,'expected_sha256':POLICY_SHA256}
 load_snapshot(RUNTIME/'private_codex',selection)
 payload=source_snapshot();launcher=read_source(PACKAGE/'launcher.py')
 project=prefix/'runtime/private_codex';runtime=project/'.runtime/main'
 config={'schema':2,'profile':'integrated-32','sdk_python':sdk,'runtime':str(runtime),'runtime_project_root':str(project),
         'runtime_source_root':str(prefix/'runtime'),'router_socket':None,'receipt_directory':str(prefix/'receipts'),
         'models':[],'capabilities':list(CAPABILITIES)}
 result={'status':'planned' if dry_run else 'prepared','prefix':str(prefix),'config':str(prefix/'plugin.json'),
         'runtime':str(runtime),'project':str(project),'source_files':len(payload),'source_bytes':sum(map(len,payload.values())),
         'unconfigured_backends':list(BACKENDS),'services_started':False,'network_used':False,'execution_ready':False,
         'next_command':['python3','-B',str(prefix/'runtime/integration/laf_bootstrap.py'),'doctor','--prefix',str(prefix)]}
 if dry_run:return result
 old_umask=os.umask(0o077);identity=None;parent_fd=None;owned_fd=None;tree=None
 try:
  parent_fd=protected_directory(prefix.parent)
  os.mkdir(prefix.name,0o700,dir_fd=parent_fd);identity=os.stat(prefix.name,dir_fd=parent_fd,follow_symlinks=False)
  owned_fd=os.open(prefix.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent_fd)
  opened=os.fstat(owned_fd)
  if (opened.st_dev,opened.st_ino)!=(identity.st_dev,identity.st_ino):raise BootstrapError('New prefix identity changed')
  tree=NewTree(owned_fd)
  for name,raw in payload.items():
   dest=Path('runtime')/name
   make_directory(tree,dest.parent);write_file(tree,dest,raw)
  for directory in ('runtime/private_codex/config','runtime/private_codex/.runtime','runtime/private_codex/.runtime/main',
                    'runtime/private_codex/.runtime/main/logs','receipts','workspace'):
   make_directory(tree,directory)
  policy=selection
  for path,value in ((project/'config/policy.json',policy),(runtime/'unconfigured-backends.json',{'schema':1,'unconfigured':list(BACKENDS)}),(prefix/'plugin.json',config)):
   write_file(tree,path.relative_to(prefix),(json.dumps(value,indent=2)+'\n').encode())
  write_file(tree,'launcher.py',launcher)
  for name in ('LICENSE','NOTICE'):
   source=PACKAGE/name
   if source.is_file():write_file(tree,name,read_source(source))
  tree.verify();same_directory(prefix,owned_fd)
  doctor(prefix) # Read-only validation of the NEW tree; never Runner/isolation.prepare.
  tree.verify();same_directory(prefix,owned_fd)
 except BaseException:
  if identity is not None:
   if tree is not None:tree.cleanup()
   try:now=os.stat(prefix.name,dir_fd=parent_fd,follow_symlinks=False)
   except FileNotFoundError:now=None
   if now is not None and (now.st_dev,now.st_ino)==(identity.st_dev,identity.st_ino) and stat.S_ISDIR(now.st_mode):
    os.rmdir(prefix.name,dir_fd=parent_fd)
  raise
 finally:
  if tree is not None:tree.close()
  elif owned_fd is not None:os.close(owned_fd)
  if parent_fd is not None:os.close(parent_fd)
  os.umask(old_umask)
 return result

def doctor(prefix):
 prefix=safe_path(prefix)
 path=prefix/'plugin.json';info=path.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or info.st_size>32768:
  raise BootstrapError('Private plugin configuration required')
 config=decode(path.read_bytes())
 if type(config) is not dict or set(config)!=PLUGIN_KEYS or type(config['schema']) is not int or config['schema']!=2 or config['profile']!='integrated-32':
  raise BootstrapError('Bootstrap schema2 configuration required')
 project=prefix/'runtime/private_codex';runtime=project/'.runtime/main'
 if (config['runtime_source_root']!=str(prefix/'runtime') or config['runtime_project_root']!=str(project) or config['runtime']!=str(runtime)
     or config['capabilities']!=list(CAPABILITIES)):
  raise BootstrapError('Bootstrap runtime anchor/configuration mismatch')
 sys.path.insert(0,str(RUNTIME/'private_codex/src'))
 from runtime_scope import runtime_path
 from common_policy import load_project_policy
 from backend_state import load_unconfigured
 runtime_path(runtime,project_root=project);bundle=load_project_policy(project)
 for required in (prefix/'launcher.py',prefix/'runtime/integration/laf_stdio_cli.py',prefix/'runtime/integration/laf_bootstrap.py'):
  info=required.lstat()
  if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:raise BootstrapError('Private real source entrypoints required')
  ast.parse(read_source(required),filename=required.name)
 if not (runtime/'unconfigured-backends.json').exists():raise BootstrapError('Explicit bootstrap backend state is required')
 unconfigured=load_unconfigured(runtime)
 from backend_state import load_state
 operator_state,operator_raw=load_state(runtime)
 for name in TOP_CONFIG:
  value=decode(read_source(project/name))
  if type(value) is not dict:raise BootstrapError('Tracked resource configuration invalid')
  if name=='resource-limits.json':
   from resources import policy
   policy(value)
 sdk={'status':'unconfigured','version_verified':False,'required_version':'1.30.0'}
 if config['sdk_python'] is not None:sdk_reference(config['sdk_python']);sdk['status']='interpreter_present'
 return {'status':'prepared','prefix':str(prefix),'profile':'integrated-32','sdk':sdk,
         'policy':bundle.metadata(),'unconfigured_backends':[x for x in BACKENDS if x in unconfigured],
         'services_started':False,'services_probed':False,'network_used':False,'execution_ready':False,
         'bindings':{name:('configured_not_probed' if operator_state is not None and operator_state.get(name) is not None else 'unconfigured') for name in ('router','codex')},
         'ordinary_dependencies':{name:shutil.which(name) is not None for name in ('unshare','mount','setpriv','bwrap','systemd-run')},
         'cgroup_and_namespace_admission':'not_probed',
         'limitations':['No SDK/account/native version or service readiness probe','Only explicitly bound backends can dispatch; configured is not service readiness','No daemon, model or Router lifecycle was started']}


def open_directory_at(root,path):
 """No absolute destination lookup after an owner root has been pinned."""
 path=Path(path)
 if path.is_absolute() or '..' in path.parts:raise BootstrapError('Relative directory required')
 current=os.dup(root)
 try:
  for name in path.parts:
   opened=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=current)
   os.close(current);current=opened;info=os.fstat(current)
   if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:raise BootstrapError('Private owner directory required')
  return current
 except BaseException:os.close(current);raise

def _operator_imports():
 sys.path[:0]=[str(RUNTIME/'private_codex/src'),str(RUNTIME/'public_laf/mcp/src')]

@contextmanager
def operator_context(prefix):
 _operator_imports();prefix=safe_path(prefix);doctor(prefix)
 fds=[]
 try:
  owner=protected_directory(prefix);fds.append(owner)
  try:fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except OSError:raise BootstrapError('Another operator configuration owns this prefix') from None
  runtime_fd=open_directory_at(owner,'runtime/private_codex/.runtime/main');fds.append(runtime_fd)
  receipts_fd=open_directory_at(owner,'receipts');fds.append(receipts_fd)
  same_directory(prefix,owner)
  yield prefix,prefix/'runtime/private_codex/.runtime/main',owner,runtime_fd,receipts_fd
 finally:
  for fd in reversed(fds):os.close(fd)

def unused_runtime(runtime_fd,receipts_fd,temporary=None):
 allowed={'logs','unconfigured-backends.json'}|({temporary} if temporary is not None else set())
 if set(os.listdir(runtime_fd))-allowed or os.listdir(receipts_fd):
  raise BootstrapError('Configure only before daemon/receipt/database use; no existing or uncertain state is reused')

def _write_all(fd,raw):
 offset=0
 while offset<len(raw):
  count=os.write(fd,raw[offset:])
  if count<=0:raise BootstrapError('Write made no progress')
  offset+=count

def _binding_change(prefix,backend,field,builder,*,dry_run):
 from backend_state import BACKENDS,FILE,LIMIT,load_state,state_sha256,validate_state
 with operator_context(prefix) as (prefix,runtime,owner,runtime_fd,receipts_fd):
  unused_runtime(runtime_fd,receipts_fd);state,old=load_state(runtime,dir_fd=runtime_fd)
  if state is None:raise BootstrapError('Explicit bootstrap state is required')
  if state['schema']==1:
   if set(state['unconfigured'])!=set(BACKENDS):raise BootstrapError('Only fresh deny-all bootstrap state can be configured')
   state={'schema':2,'unconfigured':list(BACKENDS),'router':None,'codex':None}
  if state[field] is not None:raise BootstrapError('Existing backend binding is immutable; use a NEW prefix, never rebind')
  binding=builder();state[field]=binding;state['unconfigured']=[x for x in BACKENDS if x!=backend and x in state['unconfigured']]
  raw=(json.dumps(state,indent=2)+'\n').encode();validate_state(state,runtime,len(raw))
  if len(raw)>LIMIT:raise BootstrapError('Binding record exceeds its bound')
  result={'status':'planned' if dry_run else 'configured','backend':backend,'prefix':str(prefix),
          'binding_sha256':state_sha256(raw),'services_started':False,'network_used':False,
          'native_version_verified':False,'account_verified':False,'provider_advertisement_verified':False,
          'restart_frontend_after_configuration':True,'execution_ready':False}
  if dry_run:return result
  temporary=FILE+'.tmp-'+uuid.uuid4().hex;identity=None;published=False;old_umask=os.umask(0o077)
  try:
   fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=runtime_fd)
   try:
    info=os.fstat(fd);identity=(info.st_dev,info.st_ino);_write_all(fd,raw);os.fsync(fd)
   finally:os.close(fd)
   unused_runtime(runtime_fd,receipts_fd,temporary)
   if load_state(runtime,dir_fd=runtime_fd)[1]!=old or builder()!=binding:raise BootstrapError('Operator source changed before publication')
   validate_state(state,runtime,len(raw))
   same_directory(prefix,owner)
   check=open_directory_at(owner,'runtime/private_codex/.runtime/main')
   try:
    if (os.fstat(check).st_dev,os.fstat(check).st_ino)!=(os.fstat(runtime_fd).st_dev,os.fstat(runtime_fd).st_ino):raise BootstrapError('Runtime directory changed')
   finally:os.close(check)
   os.replace(temporary,FILE,src_dir_fd=runtime_fd,dst_dir_fd=runtime_fd);published=True
   os.fsync(runtime_fd);same_directory(prefix,owner)
   return result
  finally:
   try:
    if identity is not None and not published:
     try:info=os.stat(temporary,dir_fd=runtime_fd,follow_symlinks=False)
     except FileNotFoundError:info=None
     if info is not None and (info.st_dev,info.st_ino)==identity:os.unlink(temporary,dir_fd=runtime_fd)
   finally:os.umask(old_umask)

def configure_router(prefix,*,socket_path,models,dry_run=False):
 _operator_imports()
 from laf_mcp.router import Router
 from laf_mcp.contracts import valid_model
 if (type(models) is not list or not 1<=len(models)<=16 or any(not valid_model(x) for x in models) or len(set(models))!=len(models)):
  raise BootstrapError('Explicit unique raw Router profile allowlist required')
 path=Path(socket_path)
 def binding():
  identity=Router(str(path))._check_identity() # Filesystem metadata only; no socket or peer connection.
  return {'socket':str(path),'socket_identity':list(identity),'models':list(models),'receipts':str(Path(prefix)/'receipts')}
 return _binding_change(prefix,'router','router',binding,dry_run=dry_run)

def configure_codex(prefix,*,binary,binary_sha256,workspace,models,dry_run=False):
 _operator_imports()
 from backend_state import file_identity
 from codex_host import binary_snapshot
 from codex_pipeline import validate_policy
 prefix=Path(prefix);path=Path(binary);workspace=Path(workspace)
 if workspace!=prefix/'workspace':raise BootstrapError('Explicit dedicated prefix/workspace required')
 if type(binary_sha256) is not str or not re.fullmatch('[a-f0-9]{64}',binary_sha256):raise BootstrapError('Expected native SHA256 required')
 policy=validate_policy({'schema':1,'workspaces':{'default':str(workspace)},'models':models,
                         'max_threads':4,'max_tasks':16,'concurrency':1,'timeout_seconds':120})
 def binding():
  info=path.lstat()
  if not 4<=info.st_size<=NATIVE_LIMIT:raise BootstrapError('Native binary exceeds the explicit bound')
  before=file_identity(info);binary_snapshot(str(path),binary_sha256)
  if file_identity(path.lstat())!=before:raise BootstrapError('Native binary changed during validation')
  return {'binary':str(path),'binary_sha256':binary_sha256,'binary_identity':before,'policy':policy}
 return _binding_change(prefix,'codex_pipeline','codex',binding,dry_run=dry_run)

def _stream_digest(fd):
 os.lseek(fd,0,os.SEEK_SET);digest=hashlib.sha256();size=0
 while True:
  raw=os.read(fd,1024**2)
  if not raw:break
  size+=len(raw)
  if size>NATIVE_LIMIT:raise BootstrapError('Native source exceeds512MiB')
  digest.update(raw)
 return digest.hexdigest(),size

def _native_source(path):
 # A source may be group-writable (for example a normal npm install). It is
 # never executed here: the explicit expected hash and coherent copy are required.
 path=Path(path)
 if not path.is_absolute() or '..' in path.parts:raise BootstrapError('Absolute native source required')
 parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  for name in path.parts[1:-1]:
   opened=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);os.close(parent);parent=opened
   if os.fstat(parent).st_uid not in (0,os.getuid()):raise BootstrapError('Native source ancestry owner unavailable')
  fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
 finally:os.close(parent)
 try:
  info=os.fstat(fd)
  if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0,os.getuid()) or not 4<=info.st_size<=NATIVE_LIMIT:
   raise BootstrapError('Bounded regular owned native source required')
  if os.read(fd,4)!=b'\x7fELF':raise BootstrapError('Native ELF source required')
  return fd,info
 except BaseException:os.close(fd);raise

def snapshot_codex(prefix,*,source,expected_sha256,dry_run=False):
 _operator_imports()
 from backend_state import file_identity,load_state
 if type(expected_sha256) is not str or not re.fullmatch('[a-f0-9]{64}',expected_sha256):raise BootstrapError('Explicit expected native SHA256 required')
 with operator_context(prefix) as (prefix,runtime,owner,runtime_fd,receipts_fd):
  unused_runtime(runtime_fd,receipts_fd)
  state,_=load_state(runtime,dir_fd=runtime_fd)
  if state is None or state.get('codex') is not None or 'codex_pipeline' not in state['unconfigured']:raise BootstrapError('Native is selected before first Codex binding only')
  try:os.stat('bin',dir_fd=owner,follow_symlinks=False)
  except FileNotFoundError:pass
  else:raise BootstrapError('NEW bin destination required; existing data preserved')
  source_fd,initial=_native_source(source)
  bin_fd=None;output_fd=None;bin_identity=None;file_identity_value=None;complete=False;old_umask=os.umask(0o077)
  try:
   digest,size=_stream_digest(source_fd)
   if digest!=expected_sha256 or file_identity(initial)!=file_identity(os.fstat(source_fd)):raise BootstrapError('Native source identity/hash mismatch')
   result={'status':'planned' if dry_run else 'snapshotted','binary':str(prefix/'bin/codex'),'sha256':digest,'bytes':size,
           'executed':False,'native_version_verified':False,'source_modified':False,'authentication_copied':False}
   if dry_run:return result
   os.mkdir('bin',0o700,dir_fd=owner);info=os.stat('bin',dir_fd=owner,follow_symlinks=False);bin_identity=(info.st_dev,info.st_ino)
   bin_fd=os.open('bin',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=owner)
   if (os.fstat(bin_fd).st_dev,os.fstat(bin_fd).st_ino)!=bin_identity:raise BootstrapError('Native destination identity changed')
   output_fd=os.open('codex',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o500,dir_fd=bin_fd)
   info=os.fstat(output_fd);file_identity_value=(info.st_dev,info.st_ino)
   os.lseek(source_fd,0,os.SEEK_SET);copied=hashlib.sha256();copied_bytes=0
   while True:
    raw=os.read(source_fd,1024**2)
    if not raw:break
    copied_bytes+=len(raw)
    if copied_bytes>NATIVE_LIMIT:raise BootstrapError('Native source exceeds512MiB')
    copied.update(raw);_write_all(output_fd,raw)
   os.fsync(output_fd);os.close(output_fd);output_fd=None
   after_digest,after_size=_stream_digest(source_fd)
   if (copied.hexdigest()!=digest or after_digest!=digest or copied_bytes!=size or after_size!=size
       or file_identity(initial)!=file_identity(os.fstat(source_fd))):raise BootstrapError('Native source changed during copy')
   same_directory(prefix,owner);same_directory(prefix/'bin',bin_fd)
   from codex_host import binary_snapshot
   binary_snapshot(str(prefix/'bin/codex'),digest)
   os.fsync(bin_fd);os.fsync(owner)
   observed,observed_info=_native_source(source)
   try:
    if file_identity(observed_info)!=file_identity(initial):raise BootstrapError('Native source path changed during copy')
   finally:os.close(observed)
   same_directory(prefix,owner);same_directory(prefix/'bin',bin_fd)
   complete=True;return result
  finally:
   try:
    if output_fd is not None:os.close(output_fd)
    if not complete and bin_identity is not None:
     if bin_fd is not None and file_identity_value is not None:
      try:info=os.stat('codex',dir_fd=bin_fd,follow_symlinks=False)
      except FileNotFoundError:info=None
      if info is not None and (info.st_dev,info.st_ino)==file_identity_value:os.unlink('codex',dir_fd=bin_fd)
     try:info=os.stat('bin',dir_fd=owner,follow_symlinks=False)
     except FileNotFoundError:info=None
     if info is not None and (info.st_dev,info.st_ino)==bin_identity:os.rmdir('bin',dir_fd=owner)
   finally:
    if bin_fd is not None:os.close(bin_fd)
    os.close(source_fd);os.umask(old_umask)

def daemon_command(prefix):
 _operator_imports();prefix=safe_path(prefix);checked=doctor(prefix)
 from backend_state import load_state
 state,_=load_state(prefix/'runtime/private_codex/.runtime/main')
 if state is None or state.get('codex') is None or checked['sdk']['status']!='interpreter_present':raise BootstrapError('Configure Codex and SDK before emitting its guarded daemon command')
 value=decode(read_source(prefix/'plugin.json'))
 return {'status':'command_only','services_started':False,'command':[value['sdk_python'],'-B',str(prefix/'runtime/private_codex/src/runner.py'),
          '--runtime',value['runtime'],'--codex',state['codex']['binary'],'--allow-codex-pipeline','daemon'],
         'requires':'Existing normal host, namespace/cgroup/user-bus/resource admission; no bypass or automatic start',
         'ordinary_maximum_sessions_unchanged':True,'pipeline_concurrency':1}


def activate_config(prefix,*,config=None,dry_run=False):
 """Explicit user-run NEW standard config. Never overwrite or register/start."""
 prefix=safe_path(prefix);target=Path(config) if config is not None else Path.home()/'.config/laf/plugin.json'
 target=target.expanduser()
 if not target.is_absolute() or '..' in target.parts or target.is_relative_to(PACKAGE):
  raise BootstrapError('Absolute private configuration destination required')
 if target.exists() or target.is_symlink():raise BootstrapError('Existing configuration is preserved; no overwrite')
 missing=[];base=target.parent
 while not base.exists():
  if base.is_symlink():raise BootstrapError('Configuration ancestry must not use symlinks')
  missing.append(base.name);base=base.parent
 for node in (base,*base.parents):
  info=node.lstat()
  if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0,os.getuid()) or info.st_mode & 0o022:
   raise BootstrapError('Protected symlink-free configuration ancestry required')
 base_identity=base.lstat()
 checked=doctor(prefix)
 if checked['sdk']['status']!='interpreter_present':raise BootstrapError('Select an existing SDK interpreter during prepare before activation')
 raw=read_source(prefix/'plugin.json')
 result={'status':'planned' if dry_run else 'config_activated','config':str(target),'prefix':str(prefix),
         'services_started':False,'network_used':False,'codex_registered':False,'execution_ready':False}
 if dry_run:return result
 old_umask=os.umask(0o077);fds=[];created=[];file_identity=None;current=None;complete=False
 try:
  current=protected_directory(base);fds.append(current)
  opened=os.fstat(current)
  if (opened.st_dev,opened.st_ino)!=(base_identity.st_dev,base_identity.st_ino):raise BootstrapError('Configuration directory identity changed')
  for name in reversed(missing):
   os.mkdir(name,0o700,dir_fd=current)
   info=os.stat(name,dir_fd=current,follow_symlinks=False);created.append((current,name,info.st_dev,info.st_ino))
   current=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=current);fds.append(current)
  fd=os.open(target.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=current)
  info=os.fstat(fd);file_identity=(info.st_dev,info.st_ino)
  with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
  same_directory(target.parent,current)
  complete=True;return result
 finally:
  try:
   if not complete:
    if file_identity is not None:
     info=os.stat(target.name,dir_fd=current,follow_symlinks=False)
     if (info.st_dev,info.st_ino)==file_identity:os.unlink(target.name,dir_fd=current)
    for parent,name,dev,ino in reversed(created):
     info=os.stat(name,dir_fd=parent,follow_symlinks=False)
     if (info.st_dev,info.st_ino)==(dev,ino):os.rmdir(name,dir_fd=parent)
  finally:
   for fd in reversed(fds):os.close(fd)
   os.umask(old_umask)

def main(argv=None):
 parser=Parser(description=__doc__);parser.add_argument('action',choices=('prepare','doctor','activate-config','configure-router','configure-codex','snapshot-codex','daemon-command'))
 parser.add_argument('--prefix',type=Path,required=True);parser.add_argument('--sdk-python',type=Path)
 parser.add_argument('--dry-run',action='store_true');parser.add_argument('--config',type=Path,help='NEW standard config destination for explicit activate-config only')
 parser.add_argument('--socket',type=Path);parser.add_argument('--model',action='append');parser.add_argument('--native',type=Path)
 parser.add_argument('--sha256');parser.add_argument('--workspace',type=Path);parser.add_argument('--source',type=Path)
 try:
  args=parser.parse_args(argv)
  selected={name for name in ('sdk_python','config','socket','model','native','sha256','workspace','source') if getattr(args,name) is not None}
  allowed={'prepare':{'sdk_python'},'doctor':set(),'activate-config':{'config'},'configure-router':{'socket','model'},
           'configure-codex':{'native','sha256','workspace','model'},'snapshot-codex':{'source','sha256'},'daemon-command':set()}
  if selected-allowed[args.action] or args.action in ('doctor','daemon-command') and args.dry_run:raise BootstrapError('Options do not match the explicit action')
  required={'configure-router':{'socket','model'},'configure-codex':{'native','sha256','workspace','model'},'snapshot-codex':{'source','sha256'}}
  if required.get(args.action,set())-selected:raise BootstrapError('Explicit operator inputs are missing')
  if args.action=='prepare':result=prepare_prefix(args.prefix,sdk_python=args.sdk_python,dry_run=args.dry_run)
  elif args.action=='doctor':result=doctor(args.prefix)
  elif args.action=='activate-config':result=activate_config(args.prefix,config=args.config,dry_run=args.dry_run)
  elif args.action=='configure-router':result=configure_router(args.prefix,socket_path=args.socket,models=args.model,dry_run=args.dry_run)
  elif args.action=='configure-codex':result=configure_codex(args.prefix,binary=args.native,binary_sha256=args.sha256,workspace=args.workspace,models=args.model,dry_run=args.dry_run)
  elif args.action=='snapshot-codex':result=snapshot_codex(args.prefix,source=args.source,expected_sha256=args.sha256,dry_run=args.dry_run)
  else:result=daemon_command(args.prefix)
  print(json.dumps(result,indent=2));return 0
 except (OSError,ValueError,RuntimeError,SyntaxError):
  print('LAF bootstrap unavailable. Use a NEW private prefix and inspect prerequisites; no install/start/repair was attempted.',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
