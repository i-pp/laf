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
"""Explicit NEW-prefix bindings. Missing marker and schema1 keep legacy behavior."""
import hashlib
import os
from pathlib import Path
import re
import stat
from chat_mailbox import _json

BACKENDS=('orchestrator','local_helper','mailbox','mailbox_grants','codex_pipeline','experiments','router')
FILE='unconfigured-backends.json'
LIMIT=16384

def file_identity(info):
 return [info.st_dev,info.st_ino,info.st_uid,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns]

def _absolute(value):
 return (type(value) is str and 0<len(value)<=4096 and value.startswith('/') and '\0' not in value
         and all(x not in ('','.','..') for x in value.split('/')[1:]))

def bootstrap_paths(runtime):
 root=Path(runtime).absolute();project=root.parent.parent
 if root.name!='main' or root.parent.name!='.runtime' or project.name!='private_codex' or project.parent.name!='runtime':
  raise ValueError('Explicit bootstrap runtime anchor required')
 from runtime_scope import runtime_path
 if runtime_path(root,project_root=project)!=root:raise ValueError('Bootstrap runtime mismatch')
 return project.parent.parent,project

def validate_state(value,runtime,raw_size):
 if (type(value) is not dict or type(value.get('schema')) is not int or type(value.get('unconfigured')) is not list
     or any(type(x) is not str or x not in BACKENDS for x in value['unconfigured'])
     or len(set(value['unconfigured']))!=len(value['unconfigured'])):
  raise ValueError('Invalid explicit backend configuration')
 if value['schema']==1:
  if set(value)!={'schema','unconfigured'} or raw_size>4096:raise ValueError('Invalid legacy backend configuration')
  return value
 if value['schema']!=2 or set(value)!={'schema','unconfigured','router','codex'}:
  raise ValueError('Invalid operator binding record')
 prefix,project=bootstrap_paths(runtime)
 router,codex=value['router'],value['codex']
 enabled=set()
 if router is not None:
  if (type(router) is not dict or set(router)!={'socket','socket_identity','models','receipts'}
      or not _absolute(router['socket']) or router['receipts']!=str(prefix/'receipts')
      or type(router['models']) is not list or not 1<=len(router['models'])<=16
      or any(type(x) is not str or not re.fullmatch('[a-z][a-z0-9-]{0,63}',x) for x in router['models']) or len(set(router['models']))!=len(router['models'])
      or type(router['socket_identity']) is not list or len(router['socket_identity'])!=6
      or any(type(x) is not int or x<0 for x in router['socket_identity'])
      or router['socket_identity'][2]!=os.getuid() or router['socket_identity'][5]!=os.getuid()):
   raise ValueError('Invalid Router binding')
  enabled.add('router')
 if codex is not None:
  from codex_pipeline import validate_policy
  if (type(codex) is not dict or set(codex)!={'binary','binary_sha256','binary_identity','policy'}
      or not _absolute(codex['binary']) or type(codex['binary_sha256']) is not str
      or not re.fullmatch('[a-f0-9]{64}',codex['binary_sha256'])
      or type(codex['binary_identity']) is not list or len(codex['binary_identity'])!=7
      or any(type(x) is not int or x<0 for x in codex['binary_identity'])):
   raise ValueError('Invalid Codex binding')
  policy=validate_policy(codex['policy'])
  if (policy['schema']!=1 or policy['concurrency']!=1 or policy['workspaces']!={'default':str(prefix/'workspace')}
      or codex['binary_identity'][2] not in (0,os.getuid()) or codex['binary_identity'][3]&0o022
      or not 4<=codex['binary_identity'][4]<=512*1024**2
      or not stat.S_ISREG(codex['binary_identity'][3])):
   raise ValueError('Invalid bounded Codex scope')
  enabled.add('codex_pipeline')
 if value['unconfigured']!=[x for x in BACKENDS if x not in enabled]:raise ValueError('Backend state and binding disagree')
 return value

def load_state(runtime,*,dir_fd=None):
 """Read one bounded immutable record; never initialize any DB or contact a peer."""
 try:fd=os.open(Path(runtime)/FILE if dir_fd is None else FILE,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,**({} if dir_fd is None else {'dir_fd':dir_fd}))
 except FileNotFoundError:return None,b''
 try:
  before=os.fstat(fd)
  if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid() or stat.S_IMODE(before.st_mode)!=0o600
      or before.st_nlink!=1 or not 0<before.st_size<=LIMIT):raise ValueError('Private bounded backend record required')
  raw=b''
  while len(raw)<=LIMIT:
   part=os.read(fd,LIMIT+1-len(raw))
   if not part:break
   raw+=part
  if len(raw)!=before.st_size or file_identity(before)!=file_identity(os.fstat(fd)):
   raise ValueError('Backend record changed during read')
  return validate_state(_json(raw),runtime,len(raw)),raw
 finally:os.close(fd)

def state_sha256(raw):return hashlib.sha256(raw).hexdigest()

def check_snapshot(runtime,expected):
 _,raw=load_state(runtime)
 if state_sha256(raw)!=expected:raise ValueError('Backend binding changed; restart from a reviewed configuration')

def load_unconfigured(runtime):
 value,_=load_state(runtime)
 return frozenset() if value is None else frozenset(value['unconfigured'])

def tool_backend(name):
 for prefix,backend in (('orchestrator_','orchestrator'),('aceis_local_','local_helper'),
     ('chat_mailbox_','mailbox'),('codex_','codex_pipeline'),('experiment_','experiments'),('laf_router_','router')):
  if name.startswith(prefix):return backend
 return None

def blocked_result(backend):
 return {'status':'BLOCKED','code':'BACKEND_UNCONFIGURED','backend':backend,
         'automatic_retry':False,'dispatch_started':False}
