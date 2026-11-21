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


# Added 2026-11-15: bounded immutable Flash asset/command verification.

"""Core-local model identity only. No host authority, native import or loading."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .core import ServiceError

MAX_FILES=4096
MAX_BYTES=2**50


def _path(raw):
    if not isinstance(raw,str) or len(raw)>4096:
        raise ServiceError('Explicit normalized Flash asset path required')
    path=Path(raw)
    if not path.is_absolute() or '..' in path.parts or str(path)!=raw:
        raise ServiceError('Explicit normalized Flash asset path required')
    return path


def _targets(config):
    source=_path(config['strata_source']);python=_path(config['tokenizer_python'])
    command=config['strata_command']
    if (not isinstance(command,list) or not 1<=len(command)<=128
            or any(not isinstance(arg,str) or not arg or len(arg)>4096 for arg in command)
            or command.count('--native')!=1 or command.count('--pack')!=1):
        raise ServiceError('Pinned Flash native and pack arguments required')
    try:
        native=_path(command[command.index('--native')+1]);pack=_path(command[command.index('--pack')+1])
    except IndexError as error:raise ServiceError('Pinned Flash native and pack arguments required') from error
    fixed={source,python,_path(command[0]),native,pack}
    for arg in command:
        candidate=arg.split('=',1)[1] if arg.startswith('-') and '=' in arg else arg
        if candidate.startswith('/'):fixed.add(_path(candidate))
        elif '..' in Path(candidate).parts:raise ServiceError('Relative Flash asset escape rejected')
    return fixed


def _files(config, deadline):
    found=set();visited=0
    def add(path):
        if time.monotonic()>deadline:raise ServiceError('Flash asset verification deadline')
        for parent in (path,*path.parents):
            if parent.is_symlink():raise ServiceError('Flash asset symlink rejected')
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ServiceError('Regular immutable Flash asset required')
        found.add(path)
        if len(found)>MAX_FILES:raise ServiceError('Flash asset inventory bound')
    def failed(error):raise ServiceError('Flash asset inventory unavailable') from error
    for target in _targets(config):
        for parent in (target,*target.parents):
            if parent.is_symlink():raise ServiceError('Flash asset symlink rejected')
        if target.is_dir():
            for parent,dirs,names in os.walk(target,followlinks=False,onerror=failed):
                visited+=1
                if visited>MAX_FILES or time.monotonic()>deadline:raise ServiceError('Flash asset inventory bound')
                if len(dirs)+len(names)>MAX_FILES:raise ServiceError('Flash asset inventory bound')
                for name in dirs:
                    if (Path(parent)/name).is_symlink():raise ServiceError('Flash asset symlink rejected')
                for name in names:add(Path(parent)/name)
        else:add(target)
    return found


def _hash(path,size,deadline):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size!=size:
            raise ServiceError('Flash asset size/type changed')
        digest=hashlib.sha256();total=0
        while True:
            if time.monotonic()>deadline:raise ServiceError('Flash asset verification deadline')
            block=stream.read(1024**2)
            if not block:break
            total+=len(block)
            if total>size:raise ServiceError('Flash asset grew during verification')
            digest.update(block)
        after=os.fstat(stream.fileno());current=path.lstat()
        fields=lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        if total!=size or fields(before)!=fields(after) or fields(after)!=fields(current):
            raise ServiceError('Flash asset changed during verification')
        return digest.hexdigest()


def manifest(config, *, verify=False):
    """Pin full source/pack trees plus every absolute command/interpreter asset."""
    try:
        _targets(config)
        entries=config['resource_flash_assets']
        if not isinstance(entries,list) or not 1<=len(entries)<=MAX_FILES:
            raise ServiceError('Bounded Flash asset pins required')
        pins={};total=0
        for entry in entries:
            if (not isinstance(entry,dict) or set(entry)!={'path','bytes','sha256'}
                    or type(entry['bytes']) is not int or not 0<=entry['bytes']<=MAX_BYTES
                    or not isinstance(entry['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',entry['sha256'])):
                raise ServiceError('Exact Flash asset metadata required')
            path=_path(entry['path'])
            if path in pins:raise ServiceError('Duplicate Flash asset')
            total+=entry['bytes'];pins[path]=entry
        if total>MAX_BYTES:raise ServiceError('Flash asset byte bound')
        normalized={'format':1,'strata_source':config['strata_source'],'tokenizer_python':config['tokenizer_python'],
            'strata_command':config['strata_command'],'files':[pins[path] for path in sorted(pins)]}
        digest=hashlib.sha256(json.dumps(normalized,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if config['resource_model_sha256']['flash']!=digest:raise ServiceError('Flash command/asset manifest mismatch')
        if verify:
            deadline=time.monotonic()+30
            if _files(config,deadline)!=set(pins):raise ServiceError('Incomplete or changed Flash asset inventory')
            for path,entry in pins.items():
                if _hash(path,entry['bytes'],deadline)!=entry['sha256']:raise ServiceError('Flash asset content mismatch')
            if time.monotonic()>deadline:raise ServiceError('Flash asset verification deadline')
        return digest
    except (OSError,ValueError,TypeError,KeyError,IndexError) as error:
        raise ServiceError('Flash asset identity unavailable') from error
