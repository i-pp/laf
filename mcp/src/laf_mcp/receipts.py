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

"""Metadata-only durable attempt records; never replay, prune or import a job."""
import contextlib
import fcntl
import os
from pathlib import Path
import stat
import threading
import time
import uuid
from .contracts import strict_json,compact,HANDLE

class ReceiptError(ValueError):
    pass

class Receipts:
    MAX_RECORDS = 128
    FIELDS = {'version','handle','endpoint','model','created','job_id','outcome','status','cancel_outcome','request_hash'}
    def __init__(self, directory):
        self.path = Path(directory)
        self._lock = threading.RLock()
        self.fd = None
        try:
            if not self.path.is_absolute(): raise ReceiptError('absolute receipt directory required')
            for p in [self.path, *self.path.parents]:
                if p.is_symlink(): raise ReceiptError('symlink receipt directory rejected')
            for p in self.path.parents:
                ancestor=p.lstat()
                sticky_tmp=(str(p)=='/tmp' and ancestor.st_uid==0 and bool(ancestor.st_mode & stat.S_ISVTX))
                if not stat.S_ISDIR(ancestor.st_mode) or ancestor.st_uid not in (0,os.getuid()) or ((ancestor.st_mode & 0o022) and not sticky_tmp):
                    raise ReceiptError('untrusted receipt ancestor')
            if not self.path.exists():
                self.path.mkdir(mode=0o700)
                parent_fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try:os.fsync(parent_fd)
                finally:os.close(parent_fd)
            st = self.path.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
                raise ReceiptError('private receipt directory required')
            self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            if (os.fstat(self.fd).st_dev,os.fstat(self.fd).st_ino) != (st.st_dev,st.st_ino): raise ReceiptError('receipt directory changed')
            self.lock_fd = os.open('.lock', os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
            self._check_file(self.lock_fd)
        except (OSError,ValueError):
            self.close()
            raise ReceiptError('receipt storage unavailable') from None
    def close(self):
        for name in ('lock_fd','fd'):
            fd=getattr(self,name,None)
            if fd is not None:
                os.close(fd);setattr(self,name,None)
    @staticmethod
    def _check_file(fd):
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_nlink!=1:
            raise ReceiptError('private receipt file required')
    @contextlib.contextmanager
    def locked(self):
        with self._lock:
            try: fcntl.flock(self.lock_fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except (OSError,TypeError): raise ReceiptError('receipt store busy') from None
            try: yield
            finally: fcntl.flock(self.lock_fd,fcntl.LOCK_UN)
    def _validate(self,r):
        if not isinstance(r,dict) or set(r)!=self.FIELDS or r.get('version')!=1 or not isinstance(r.get('handle'),str) or not HANDLE.fullmatch(r['handle']):
            raise ReceiptError('invalid receipt')
        if r['outcome'] not in ('UNKNOWN','ADMITTED','TERMINAL','NOT_SUBMITTED') or r['cancel_outcome'] not in ('NOT_REQUESTED','UNKNOWN','REQUESTED'):
            raise ReceiptError('invalid receipt state')
        if r['endpoint'] is not None and (not isinstance(r['endpoint'],str) or len(r['endpoint'])!=64):raise ReceiptError('invalid endpoint identity')
        if not isinstance(r['model'],str) or not isinstance(r['request_hash'],str) or len(r['request_hash'])!=64:raise ReceiptError('invalid receipt identity')
        if r['job_id'] is not None and (not isinstance(r['job_id'],str) or not HANDLE.fullmatch(r['job_id'])):raise ReceiptError('invalid job identity')
        return r
    def records(self):
        return [self.load(name[:-5]) for name in os.listdir(self.fd) if name.endswith('.json')]
    def find(self,handle):
        if not isinstance(handle,str) or not HANDLE.fullmatch(handle):raise ReceiptError('invalid owned handle')
        try:os.stat(handle+'.json',dir_fd=self.fd,follow_symlinks=False)
        except FileNotFoundError:return None
        return self.load(handle)
    def create(self,endpoint,model,handle,request_hash):
        # Caller holds the process/file lock until the mutation is settled.
        if sum(1 for x in os.listdir(self.fd) if x.endswith('.json') or x.startswith('.tmp-')) >= self.MAX_RECORDS:
            raise ReceiptError('receipt capacity reached; no automatic pruning')
        r={'version':1,'handle':handle,'request_hash':request_hash,'endpoint':endpoint,'model':model,'created':int(time.time()),'job_id':None,'outcome':'UNKNOWN','status':None,'cancel_outcome':'NOT_REQUESTED'}
        self._write(r,exclusive=True)
        return r
    def load(self,handle):
        if not isinstance(handle,str) or not HANDLE.fullmatch(handle):raise ReceiptError('invalid owned handle')
        fd=None
        try:
            fd=os.open(handle+'.json',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=self.fd);self._check_file(fd)
            raw=os.read(fd,4097)
            if len(raw)>4096:raise ReceiptError('receipt exceeds bound')
            r=self._validate(strict_json(raw,max_bytes=4096))
            if r['handle']!=handle:raise ReceiptError('receipt identity mismatch')
            return r
        except (OSError,ValueError):raise ReceiptError('owned receipt unavailable') from None
        finally:
            if fd is not None:os.close(fd)
    def save(self,r):self._write(r)
    def _write(self,r,exclusive=False):
        self._validate(r)
        tmp='.tmp-'+uuid.uuid4().hex
        fd=None
        try:
            data=compact(r)
            if len(data)>4096:raise ReceiptError('receipt exceeds bound')
            if exclusive:
                fd=os.open(r['handle']+'.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.fd)
            else:
                if sum(1 for x in os.listdir(self.fd) if x.startswith('.tmp-'))>=4:
                    raise ReceiptError('unresolved temporary receipt capacity reached')
                # Refuse replacing an unsafe existing record.
                self.load(r['handle'])
                fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.fd)
            self._check_file(fd)
            offset=0
            while offset<len(data):
                written=os.write(fd,data[offset:])
                if written<=0:raise ReceiptError('receipt write made no progress')
                offset+=written
            os.fsync(fd);os.close(fd);fd=None
            if not exclusive:os.replace(tmp,r['handle']+'.json',src_dir_fd=self.fd,dst_dir_fd=self.fd)
            os.fsync(self.fd)
        except (OSError,ValueError):raise ReceiptError('receipt persistence failed') from None
        finally:
            if fd is not None:os.close(fd)
            if not exclusive:
                try:os.unlink(tmp,dir_fd=self.fd)
                except FileNotFoundError:pass
