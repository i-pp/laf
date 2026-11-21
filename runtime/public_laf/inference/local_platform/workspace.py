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

"""Client-side file capabilities over a fresh private snapshot, never original writeback."""
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

MAX_FILE = 65536
MAX_TOTAL = 1024 * 1024
FORBIDDEN = {'.git','.ssh','.aws','.codex','.env','credentials','id_rsa','id_ed25519'}


class WorkspaceIntegrityError(ValueError):
    pass


def strict_json(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate JSON object key')
            result[key]=value
        return result
    def constant(_):raise ValueError('nonstandard JSON number')
    return json.loads(text,object_pairs_hook=pairs,parse_constant=constant)


def relative_path(value, root=False):
    if root and value == '.': return ()
    if (not isinstance(value,str) or not value or len(value.encode())>240
            or '\\' in value or any(ord(c)<32 for c in value)):
        raise ValueError('invalid relative path')
    parts=value.split('/')
    if (len(parts)>16 or any(not p or p in {'.','..'} or p.startswith('.') or p in FORBIDDEN
                           or len(p.encode())>128 for p in parts)
            or PurePosixPath(value).is_absolute() or ':' in value):
        raise ValueError('path is outside the file capability grammar')
    return tuple(parts)


def validate_manifest(value):
    if (not isinstance(value,dict) or set(value)!={'version','goal','files','test'}
            or type(value['version']) is not int or value['version']!=1
            or not isinstance(value['goal'],str) or not 1<=len(value['goal'].encode())<=1500):
        raise ValueError('version1 goal/files/test manifest required')
    files=value['files']; test=value['test']
    if not isinstance(files,list) or not 1<=len(files)<=64:raise ValueError('1..64 approved files required')
    seen={}
    for entry in files:
        if not isinstance(entry,dict) or set(entry)!={'path','access'}:
            raise ValueError('file requires path/access only')
        parts=relative_path(entry['path'])
        if entry['path'] in seen or entry['access'] not in ['read','write']:
            raise ValueError('duplicate file or invalid access')
        seen[entry['path']]=entry['access']
    writable=[p for p,a in seen.items() if a=='write']
    if not 1<=len(writable)<=8:raise ValueError('1..8 writable files required')
    if not isinstance(test,dict) or set(test)!={'kind','start_dir','pattern'} or test['kind']!='python-unittest':
        raise ValueError('fixed python-unittest test configuration required')
    start=relative_path(test['start_dir'],root=True)
    pattern=test['pattern']
    from .project_runner import _test_spec
    _test_spec(test['start_dir'],pattern)
    if len(pattern)>64:raise ValueError('bounded test filename pattern required')
    tests=[]
    for path,access in seen.items():
        parts=relative_path(path)
        under=parts[:len(start)]==start
        if under and fnmatch.fnmatchcase(parts[-1],pattern):
            if access=='write':raise ValueError('selected tests must be read-only')
            tests.append(path)
        if fnmatch.fnmatchcase(parts[-1],'test*.py') and access=='write':
            raise ValueError('test files must be read-only')
    if not tests:raise ValueError('at least one approved read-only test file required')
    ordered=sorted(seen)
    for a,b in zip(ordered,ordered[1:]):
        if b.startswith(a+'/'):raise ValueError('file/directory capability collision')
    # Detach caller-owned mutable dictionaries before enforcing capabilities.
    return json.loads(json.dumps(value,allow_nan=False))


def open_absolute_dir(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise ValueError('absolute directory required')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=new
        return fd
    except BaseException:
        os.close(fd);raise


def parent_fd(root,parts,create=False):
    fd=os.dup(root)
    try:
        for part in parts[:-1]:
            if create:
                try:os.mkdir(part,mode=0o700,dir_fd=fd)
                except FileExistsError:pass
            new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=new
        return fd
    except BaseException:
        os.close(fd);raise


def read_regular(root,path):
    parts=relative_path(path);parent=parent_fd(root,parts)
    fd=None
    try:
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid()
                or before.st_nlink!=1 or before.st_size>MAX_FILE):
            raise ValueError('owned, single-link, bounded regular text file required')
        data=b''
        while len(data)<=MAX_FILE:
            chunk=os.read(fd,min(8192,MAX_FILE+1-len(data)))
            if not chunk:break
            data+=chunk
        after=os.fstat(fd)
        if (len(data)>MAX_FILE or (before.st_dev,before.st_ino,before.st_mtime_ns,before.st_size)
                !=(after.st_dev,after.st_ino,after.st_mtime_ns,after.st_size)):
            raise ValueError('file changed during bounded read')
        text=data.decode('utf-8')
        if '\x00' in text:raise ValueError('binary file unsupported')
        return data
    finally:
        if fd is not None:os.close(fd)
        os.close(parent)


def sha(data):return hashlib.sha256(data).hexdigest()


class Workspace:
    def __init__(self,source,workdir,manifest):
        self.manifest=validate_manifest(manifest)
        self.permissions={e['path']:e['access'] for e in self.manifest['files']}
        self.root=Path(workdir)
        self.fd=None;self.closed=False;self.edits=0
        if not self.root.is_absolute() or self.root.name in {'','.','..'}:
            raise ValueError('new absolute workdir required')
        source_fd=open_absolute_dir(source)
        try:
            if os.fstat(source_fd).st_uid!=os.getuid():raise ValueError('source root must be owned by client user')
            contents={p:read_regular(source_fd,p) for p in self.permissions}
            if sum(map(len,contents.values()))>MAX_TOTAL:raise ValueError('snapshot exceeds1MiB')
        finally:os.close(source_fd)
        parent=open_absolute_dir(self.root.parent)
        try:os.mkdir(self.root.name,mode=0o700,dir_fd=parent)
        finally:os.close(parent)
        self.control_fd=open_absolute_dir(self.root)
        try:
            os.mkdir('workspace',mode=0o700,dir_fd=self.control_fd)
            self.path=self.root/'workspace'
            self.fd=os.open('workspace',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=self.control_fd)
            self.original_hashes={p:sha(b) for p,b in contents.items()}
            for path,data in contents.items():
                parts=relative_path(path);parent=parent_fd(self.fd,parts,create=True)
                try:
                    fd=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,
                               0o600 if self.permissions[path]=='write' else 0o400,dir_fd=parent)
                    with os.fdopen(fd,'wb') as f:f.write(data)
                finally:os.close(parent)
            self.write_control('capability.json',{'version':1,'manifest':self.manifest,
                               'original_hashes':self.original_hashes,'original_writeback':False})
        except BaseException:
            self.close();raise

    def verify_snapshot(self):
        try:self._verify_snapshot()
        except (OSError,ValueError):
            raise WorkspaceIntegrityError('snapshot root, entries or permissions changed') from None

    def _verify_snapshot(self):
        if self.closed:raise ValueError('workspace closed')
        control=open_absolute_dir(self.root)
        current=None
        try:
            held=os.fstat(self.control_fd);now=os.fstat(control)
            if (held.st_dev,held.st_ino)!=(now.st_dev,now.st_ino) or now.st_uid!=os.getuid() or stat.S_IMODE(now.st_mode)!=0o700:
                raise ValueError('controller directory identity changed')
            current=os.open('workspace',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=control)
            held=os.fstat(self.fd);now=os.fstat(current)
            if (held.st_dev,held.st_ino)!=(now.st_dev,now.st_ino) or now.st_uid!=os.getuid() or stat.S_IMODE(now.st_mode)!=0o700:
                raise ValueError('snapshot directory identity changed')
        finally:
            if current is not None:os.close(current)
            os.close(control)
        expected_dirs=set()
        for path in self.permissions:
            parts=relative_path(path)
            expected_dirs.update('/'.join(parts[:n]) for n in range(1,len(parts)))
        seen=set()
        def scan(fd,prefix=''):
            with os.scandir(fd) as entries:
                names=[e.name for e in entries]
            for name in names:
                path=prefix+name;info=os.stat(name,dir_fd=fd,follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if path not in expected_dirs or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
                        raise ValueError('unapproved snapshot directory')
                    child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                    try:scan(child,path+'/')
                    finally:os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    if (path not in self.permissions or info.st_uid!=os.getuid() or info.st_nlink!=1
                            or stat.S_IMODE(info.st_mode)!=(0o600 if self.permissions[path]=='write' else 0o400)):
                        raise ValueError('unapproved snapshot file, link or permissions')
                    seen.add(path)
                else:raise ValueError('nonregular snapshot entry')
        scan(self.fd)
        if seen!=set(self.permissions):raise ValueError('snapshot file set changed')

    def identity(self):
        self.verify_snapshot()
        entry=os.fstat(self.fd)
        return (entry.st_dev,entry.st_ino)

    def write_control(self,name,value):
        if name not in {'capability.json','result.json'}:raise ValueError('unknown controller artifact')
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.control_fd)
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)

    def check(self,path,write=False):
        self.verify_snapshot()
        relative_path(path)
        if path not in self.permissions or (write and self.permissions[path]!='write'):
            raise ValueError('path is not authorized for this operation')

    def list_files(self,cursor=0):
        self.verify_snapshot()
        if type(cursor) is not int or not 0<=cursor<=len(self.permissions):raise ValueError('invalid cursor')
        items=sorted(self.permissions)
        return {'files':[{'path':p,'access':self.permissions[p]} for p in items[cursor:cursor+8]],
                'next_cursor':cursor+8 if cursor+8<len(items) else None}

    def read_file(self,path,start_line=1,max_lines=40):
        self.check(path)
        if type(start_line) is not int or start_line<1 or type(max_lines) is not int or not 1<=max_lines<=60:
            raise ValueError('bounded line range required')
        raw=read_regular(self.fd,path);lines=raw.decode().splitlines(keepends=True)
        selected=[];size=0
        for line in lines[start_line-1:start_line-1+max_lines]:
            if size+len(line.encode())>2048:
                if not selected:raise ValueError('individual line exceeds bounded read response')
                break
            selected.append(line);size+=len(line.encode())
        return {'path':path,'sha256':sha(raw),'start_line':start_line,
                'next_line':start_line+len(selected) if start_line-1+len(selected)<len(lines) else None,
                'content':''.join(selected),'access':self.permissions[path]}

    def replace_text(self,path,expected_sha256,old,new):
        self.check(path,write=True)
        if (not isinstance(expected_sha256,str) or not re.fullmatch('[0-9a-f]{64}',expected_sha256)
                or not isinstance(old,str) or not old or len(old.encode())>4096
                or not isinstance(new,str) or len(new.encode())>4096 or '\x00' in new or self.edits>=16):
            raise ValueError('bounded edit/hash required')
        before=read_regular(self.fd,path)
        if sha(before)!=expected_sha256:raise ValueError('stale file hash; read again before editing')
        text=before.decode()
        first=text.find(old)
        if first<0 or text.find(old,first+1)>=0:raise ValueError('old text must match exactly once')
        updated=text.replace(old,new,1).encode()
        if len(updated)>MAX_FILE:raise ValueError('updated file exceeds bound')
        total=sum(len(read_regular(self.fd,p)) for p in self.permissions)
        if total-len(before)+len(updated)>MAX_TOTAL:raise ValueError('updated snapshot exceeds1MiB')
        parts=relative_path(path);parent=parent_fd(self.fd,parts)
        temporary='.client-edit-'+os.urandom(16).hex()
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
            with os.fdopen(fd,'wb') as f:f.write(updated);f.flush();os.fsync(f.fileno())
            os.replace(temporary,parts[-1],src_dir_fd=parent,dst_dir_fd=parent)
        finally:
            try:os.unlink(temporary,dir_fd=parent)
            except FileNotFoundError:pass
            os.close(parent)
        self.edits+=1
        return {'path':path,'sha256':sha(updated),'edits':self.edits}

    def hashes(self):
        self.verify_snapshot()
        try:return {path:sha(read_regular(self.fd,path)) for path in self.permissions}
        except (OSError,ValueError):raise WorkspaceIntegrityError('snapshot text changed outside client') from None

    def changes(self):
        current_hashes=self.hashes()
        changed=[]
        for path,current in current_hashes.items():
            if current!=self.original_hashes[path]:
                if self.permissions[path]!='write':raise WorkspaceIntegrityError('read-only test/input changed externally')
                changed.append({'path':path,'before_sha256':self.original_hashes[path],'after_sha256':current})
        return changed

    def close(self):
        if self.closed:return
        self.closed=True
        if self.fd is not None:os.close(self.fd)
        if hasattr(self,'control_fd'):os.close(self.control_fd)

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
