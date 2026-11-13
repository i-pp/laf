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

"""Local integrated MCP launcher; never installs, starts services or repairs guards."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys

CAPABILITIES=('local-llm','local-models','chat-mailbox','mailbox-grants','codex-pipeline','experiments')
KEYS={'schema','profile','sdk_python','runtime','runtime_project_root','router_socket','receipt_directory','models','capabilities'}
PATH_KEYS=('sdk_python','runtime','runtime_project_root','router_socket','receipt_directory')
class ConfigError(ValueError):pass

def decode(text):
 def pairs(items):
  result={}
  for k,v in items:
   if k in result:raise ConfigError('Duplicate JSON key')
   result[k]=v
  return result
 def bad(_):raise ConfigError('Nonfinite JSON')
 try:return json.loads(text,object_pairs_hook=pairs,parse_constant=bad)
 except (ValueError,UnicodeError,RecursionError) as e:raise ConfigError('Invalid configuration JSON') from e

def read_config(path):
 path=Path(path)
 if not path.is_absolute():raise ConfigError('Absolute configuration path required')
 for node in (path,*path.parents):
  info=node.lstat()
  if stat.S_ISLNK(info.st_mode) or info.st_uid not in (0,os.getuid()) or info.st_mode & 0o022:
   raise ConfigError('Unsafe owner configuration ancestry')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  info=os.fstat(fd)
  if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or not 0<info.st_size<=32768:
   raise ConfigError('Configuration must be private owner regular file, 0600')
  with os.fdopen(fd,'rb',closefd=False) as f:data=f.read(32769)
  if len(data)>32768:raise ConfigError('Configuration too large')
  return decode(data)
 finally:os.close(fd)

def arguments(value,root):
 if not isinstance(value,dict) or set(value)!=KEYS or type(value['schema']) is not int or value['schema']!=1 or value['profile']!='integrated-32':
  raise ConfigError('Explicit integrated-32 configuration required')
 for key in PATH_KEYS:
  text=value[key]
  if not isinstance(text,str) or not text.startswith('/') or '\0' in text or len(text)>4096 or any(x in ('','..','.') for x in text.split('/')[1:]):raise ConfigError('Invalid absolute operator path')
 flags=value['capabilities']
 if not isinstance(flags,list) or len(flags)!=len(CAPABILITIES) or any(not isinstance(x,str) for x in flags) or set(flags)!=set(CAPABILITIES):raise ConfigError('All six existing capabilities must be explicitly selected; no fallback')
 models=value['models']
 if not isinstance(models,list) or not 1<=len(models)<=16 or any(not isinstance(x,str) or not re.fullmatch('[a-z][a-z0-9-]{0,63}',x) for x in models) or len(models)!=len(set(models)):raise ConfigError('Explicit unique model allowlist required')
 args=[value['sdk_python'],'-B',str(root/'runtime/integration/laf_stdio_cli.py'),'--runtime',value['runtime'],'--runtime-project-root',value['runtime_project_root']]
 args.extend('--allow-'+x for x in CAPABILITIES)
 args.extend(['--allow-laf-router','--laf-router-socket',value['router_socket'],'--laf-router-state',value['receipt_directory']])
 for model in models:args.extend(['--laf-router-model',model])
 return args

def clean_environment():
 return {'PATH':'/usr/bin:/bin','HOME':str(Path.home()),'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}

def check_entrypoint(args):
 target=Path(args[2])
 if not target.is_file() or target.is_symlink():raise ConfigError('Integrated runtime is not assembled')
 interpreter=Path(args[0]).resolve(strict=True)
 info=interpreter.stat()
 if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0,os.getuid()) or info.st_mode & 0o022 or not os.access(interpreter,os.X_OK):raise ConfigError('Existing SDK interpreter unavailable')
 # The production CLI performs the exact SDK1.30.0 and runtime/UDS/receipt guards.
 # This launcher does not replace those checks or import/install an SDK itself.

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--config',type=Path,default=Path.home()/'.config/laf/plugin.json')
 parser.add_argument('--check',action='store_true',help='Static configuration/source check, not SDK or service acceptance')
 args=parser.parse_args(argv)
 try:
  root=Path(__file__).resolve().parent
  command=arguments(read_config(args.config),root);check_entrypoint(command)
  if args.check:
   print(json.dumps({'status':'configuration_checked','profile':'integrated-32','runtime_not_started':True,'sdk_version_checked':False,'service_or_account_acceptance':False}));return 0
  os.execve(command[0],command,clean_environment())
 except (OSError,ValueError,RuntimeError):
  print('LAF integrated startup unavailable. Check the private operator config and existing SDK/runtime. No installation, repair or fallback was attempted.',file=sys.stderr);return 1
 return 1

if __name__=='__main__':raise SystemExit(main())
