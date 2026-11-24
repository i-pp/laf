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
# Modified 2026-11-21: expose owner-side publisher discovery and model setup.
# Modified 2026-11-23: add an owner-local observational model registry.
# Modified 2026-11-24: reject installs against a mismatched registry scope.

"""Opt-in, persistent model management, separate from Router load/unload.

No Docker access, engine startup, login, legal agreement or inference is performed.
Mutations require a pre-existing private Router generation lock and an idle engine.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import time
from urllib.parse import urlsplit

MAX_JSON = 1024 * 1024
FIELDS = {'id', 'engine', 'reference', 'source', 'license', 'license_url',
          'version', 'required_bytes', 'digest', 'user_setup_required'}
MODEL_ID = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')
REGISTRY_LIMIT = 128
REGISTRY_SNAPSHOT = re.compile(r'snapshot-[a-f0-9]{32}\Z')
REGISTRY_FIELDS = {'schema_version', 'id', 'engine', 'reference', 'digest',
                   'size_bytes', 'ownership', 'scope_sha256'}
DIGEST = re.compile(r'sha256:[a-f0-9]{64}\Z')
REFERENCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?(?::[A-Za-z0-9][A-Za-z0-9._-]*)?\Z')


class ModelError(ValueError):
    pass


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ModelError('duplicate JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ModelError('non-finite JSON number')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ModelError('invalid model metadata JSON') from None


def text_field(value):
    return isinstance(value, str) and 0 < len(value) <= 2048 and not any(ord(c) < 32 for c in value)


def https_url(value):
    if not text_field(value):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == 'https' and bool(parsed.hostname) and
                parsed.username is None and parsed.password is None and not parsed.fragment)
    except ValueError:
        return False


def validate_catalog(entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 128:
        raise ModelError('bounded nonempty model catalog required')
    seen, references = set(), set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != FIELDS:
            raise ModelError('unexpected or missing catalog fields')
        identifier = entry['id']
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', identifier) or identifier in seen:
            raise ModelError('invalid or duplicate model ID')
        seen.add(identifier)
        if not isinstance(entry['engine'],str) or entry['engine'] not in {'ollama','strata'} or not isinstance(entry['reference'],str) or not REFERENCE.fullmatch(entry['reference']):
            raise ModelError('unsupported engine or invalid model reference')
        if entry['engine']=='ollama' and ':' not in entry['reference']:
            raise ModelError('explicit Ollama model tag required')
        identity=(entry['engine'],entry['reference'])
        if identity in references: raise ModelError('duplicate engine reference')
        references.add(identity)
        for field in ('license','version'):
            if entry[field] is not None and not text_field(entry[field]):
                raise ModelError('invalid catalog text')
        for field in ('source','license_url'):
            if entry[field] is not None and not https_url(entry[field]):
                raise ModelError('HTTPS provenance URL without credentials required')
        if entry['required_bytes'] is not None and (type(entry['required_bytes']) is not int or not 1 <= entry['required_bytes'] <= 2**60):
            raise ModelError('invalid capacity estimate')
        if entry['digest'] is not None and (not isinstance(entry['digest'],str) or not DIGEST.fullmatch(entry['digest'])):
            raise ModelError('invalid engine manifest digest')
        if type(entry['user_setup_required']) is not bool:
            raise ModelError('explicit user setup requirement required')
    return copy.deepcopy(entries)


def checked_directory(path, create=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ModelError('absolute model-state path required')
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ModelError('symlink model-state paths refused')
    if create:
        # The selected parent must already exist. No broad directory tree creation.
        try:
            path.mkdir(mode=0o700)
            parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try: os.fsync(parent)
            finally: os.close(parent)
        except FileExistsError: pass
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ModelError('owner-only model directory0700 required')
    return path


def checked_file(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise ModelError('owner-only regular single-link file0600 required')
    return info


@contextmanager
def private_lock(directory, name, create=False):
    directory = checked_directory(directory, create=create)
    if name not in {'models.lock','generation.lock'}:
        raise ModelError('invalid lock identity')
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    if create: flags |= os.O_CREAT
    fd = os.open(directory/name, flags, 0o600)
    try:
        checked_file(fd)
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ModelError('model operation or Router is active; no mutation attempted') from None
        yield
    finally:
        os.close(fd)


def read_private_json(path, *, retry_replaced=False):
    path = Path(path)
    checked_directory(path.parent)
    attempts = 4 if retry_replaced else 1
    for attempt in range(attempts):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            try:
                info = checked_file(fd)
            except ModelError:
                # A concurrent replace may unlink the opened inode before fstat.
                # Reopen the name; never accept a non-single-link descriptor.
                if attempt+1 < attempts and os.fstat(fd).st_nlink == 0:
                    continue
                raise
            if info.st_size > MAX_JSON: raise ModelError('metadata exceeds bound')
            with os.fdopen(fd,'rb',closefd=False) as stream: raw = stream.read(MAX_JSON+1)
            if len(raw) > MAX_JSON: raise ModelError('metadata exceeds bound')
            return strict_json(raw)
        finally:
            os.close(fd)


def read_catalog(path=None):
    if path is None:
        return validate_catalog([{
            'id': key, 'engine': 'strata' if key == 'flash' else 'ollama',
            'reference': 'flash' if key == 'flash' else 'laf-'+key+':latest',
            'source': None, 'license': None, 'license_url': None,
            'version': None, 'required_bytes': None, 'digest': None,
            'user_setup_required': True,
        } for key in ('flash','coder-next','gpt-oss-120b')])
    return validate_catalog(read_private_json(path))


def installable_catalog(entries):
    """Filter owner catalog entries by metadata eligibility, without checking artifacts."""
    return [entry for entry in validate_catalog(entries) if
            entry['engine'] == 'ollama' and not entry['user_setup_required']
            and all(entry[field] is not None for field in FIELDS)]


def read_engine_config(path):
    if not path:
        raise ModelError('explicit private configuration required for model engine operations')
    config = read_private_json(path)
    required = {'state_dir', 'model_store', 'generation_lock', 'engine_url'}
    if not isinstance(config, dict) or set(config) != required or not all(isinstance(v, str) for v in config.values()):
        raise ModelError('invalid model manager configuration')
    return config


class OllamaHTTP:
    """Explicit numeric-loopback endpoint only. No proxies, redirects or retries."""
    def __init__(self, url):
        try:
            p = urlsplit(url)
            if (p.scheme != 'http' or p.hostname not in {'127.0.0.1','::1'} or p.username is not None
                    or p.password is not None or p.path not in {'','/'} or p.query or p.fragment
                    or not p.port):
                raise ValueError()
            self.host,self.port = p.hostname,p.port
            self.identity='ollama:http://'+('['+self.host+']' if ':' in self.host else self.host)+':'+str(self.port)
        except (ValueError,TypeError):
            raise ModelError('explicit numeric-loopback HTTP engine URL and port required') from None
    def __call__(self, method, path, body=None):
        allowed = {('GET','/api/tags'),('GET','/api/ps'),('POST','/api/pull'),('DELETE','/api/delete')}
        if (method,path) not in allowed: raise ModelError('unsupported engine management request')
        connection = http.client.HTTPConnection(self.host,self.port,timeout=900 if path=='/api/pull' else 5)
        try:
            connection.request(method,path,None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json'})
            response = connection.getresponse()
            raw = response.read(MAX_JSON+1)
            if response.status != 200 or len(raw)>MAX_JSON:
                raise ModelError('engine management response rejected')
            return {} if not raw else strict_json(raw)
        finally:
            connection.close()


class OllamaAdapter:
    def __init__(self, request): self.request = request
    @property
    def identity(self): return getattr(self.request,'identity',None)
    def inventory(self, path):
        out = self.request('GET',path)
        if not isinstance(out,dict) or not isinstance(out.get('models'),list) or len(out['models'])>1024:
            raise ModelError('invalid engine inventory')
        result,seen = [],set()
        for model in out['models']:
            if not isinstance(model,dict): raise ModelError('invalid engine model')
            reference,digest,size = model.get('name'),model.get('digest'),model.get('size')
            if isinstance(digest,str) and re.fullmatch(r'[a-f0-9]{64}',digest): digest='sha256:'+digest
            if (not isinstance(reference,str) or not REFERENCE.fullmatch(reference) or reference in seen
                    or not isinstance(digest,str) or not DIGEST.fullmatch(digest) or type(size) is not int or size<0):
                raise ModelError('invalid engine model identity')
            seen.add(reference)
            result.append({'reference':reference,'digest':digest,'size_bytes':size})
        return result
    def installed(self): return self.inventory('/api/tags')
    def running(self): return self.inventory('/api/ps')
    def install(self, reference):
        out = self.request('POST','/api/pull',{'model':reference,'stream':False})
        if not isinstance(out,dict) or out.get('status')!='success' or 'error' in out:
            raise ModelError('engine pull did not confirm success')
    def remove(self, reference):
        out = self.request('DELETE','/api/delete',{'model':reference})
        if not isinstance(out,dict) or 'error' in out:
            raise ModelError('engine removal did not confirm success')


class ModelManager:
    def __init__(self, state_dir, catalog, adapter=None, generation_lock=None, model_store=None):
        self.directory = Path(state_dir)
        self.catalog = {entry['id']:entry for entry in validate_catalog(catalog)}
        self.adapter,self.generation_lock,self.model_store = adapter,generation_lock,model_store
    def _read(self):
        if not self.directory.exists():
            if self.directory.is_symlink(): raise ModelError('symlink state refused')
            return {'schema_version':1,'scope':None,'models':{},'pending':None,'history':[]}
        checked_directory(self.directory)
        try: value = read_private_json(self.directory/'models.json')
        except FileNotFoundError: return {'schema_version':1,'scope':None,'models':{},'pending':None,'history':[]}
        if (not isinstance(value,dict) or set(value)!={'schema_version','scope','models','pending','history'}
                or type(value['schema_version']) is not int or value['schema_version']!=1
                or not isinstance(value['models'],dict) or len(value['models'])>128
                or not isinstance(value['history'],list) or len(value['history'])>64):
            raise ModelError('invalid persisted model state')
        scope=value['scope']
        if scope is not None and (not isinstance(scope,dict) or set(scope)!={'engine','model_store','generation_lock'} or not all(text_field(v) for v in scope.values())):
            raise ModelError('invalid persisted management scope')
        if (value['models'] or value['pending'] is not None) and scope is None:
            raise ModelError('managed models require bound engine scope')
        for identifier, entry in value['models'].items():
            if not isinstance(entry,dict) or set(entry)!=FIELDS|{'status','size_bytes'}:
                raise ModelError('invalid installed model record')
            validate_catalog([{k:entry[k] for k in FIELDS}])
            if (entry['id']!=identifier or entry['status']!='installed' or entry['engine']!='ollama'
                    or entry['user_setup_required'] or any(entry[k] is None for k in FIELDS)
                    or type(entry['size_bytes']) is not int or entry['size_bytes']<0):
                raise ModelError('invalid installed model state')
        records = value['history'] + ([] if value['pending'] is None else [value['pending']])
        for record in records:
            if (not isinstance(record,dict) or set(record)!={'action','id','reference','started_at'}
                    or not isinstance(record['action'],str) or record['action'] not in {'install','remove'} or not isinstance(record['id'],str)
                    or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',record['id'])
                    or not isinstance(record['reference'],str) or not REFERENCE.fullmatch(record['reference'])
                    or type(record['started_at']) is not int):
                raise ModelError('invalid operation journal')
        return value
    def _write(self, value):
        checked_directory(self.directory)
        target = self.directory/'models.json'
        if target.exists() or target.is_symlink():
            read_private_json(target)  # Refuse symlinks, hardlinks and public state.
        raw = (json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
        if len(raw)>MAX_JSON: raise ModelError('state exceeds bound')
        temporary = self.directory/('.models-'+secrets.token_hex(12))
        fd = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary,target)
            parent = os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try: os.fsync(parent)
            finally: os.close(parent)
        finally:
            if temporary.exists(): temporary.unlink()
    def list(self):
        return [copy.deepcopy(entry) for _,entry in sorted(self._read()['models'].items())]
    def _registry_rows(self):
        path = self.directory/'registry'
        if not path.exists() and not path.is_symlink():
            return {}
        checked_directory(path)
        try:
            pointer = read_private_json(path/'active.json', retry_replaced=True)
        except FileNotFoundError:
            return {}  # No commit yet; ignore any interrupted staging snapshots.
        if (not isinstance(pointer, dict) or set(pointer) != {'schema_version', 'snapshot'}
                or type(pointer['schema_version']) is not int or pointer['schema_version'] != 1
                or not isinstance(pointer['snapshot'], str) or not REGISTRY_SNAPSHOT.fullmatch(pointer['snapshot'])):
            raise ModelError('invalid local model registry snapshot pointer')
        snapshot = checked_directory(path/pointer['snapshot'])
        result = {}
        references = set()
        for entry in snapshot.iterdir():
            identifier = entry.stem
            if (len(result) == REGISTRY_LIMIT or entry.name != identifier+'.json'
                    or not MODEL_ID.fullmatch(identifier)):
                raise ModelError('invalid or oversized local model registry')
            record = read_private_json(entry)
            if (not isinstance(record, dict) or record.keys() != REGISTRY_FIELDS
                    or type(record['schema_version']) is not int or record['schema_version'] != 1
                    or record['id'] != identifier or record['engine'] != 'ollama'
                    or record['ownership'] not in ('imported', 'managed')
                    or not isinstance(record['reference'], str) or not REFERENCE.fullmatch(record['reference'])
                    or record['reference'] in references
                    or not isinstance(record['digest'], str) or not DIGEST.fullmatch(record['digest'])
                    or type(record['size_bytes']) is not int or record['size_bytes'] < 0
                    or not isinstance(record['scope_sha256'], str)
                    or not re.fullmatch(r'[a-f0-9]{64}', record['scope_sha256'])):
                raise ModelError('invalid local model registry record')
            result[identifier] = record
            references.add(record['reference'])
        return result

    @staticmethod
    def _registry_scope(scope):
        return hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def _registry_rows_for_scope(self, scope_sha256):
        rows = self._registry_rows()
        if any(row['scope_sha256'] != scope_sha256 for row in rows.values()):
            raise ModelError('local model registry scope changed; refusing operation')
        return rows

    @staticmethod
    def _registry_public(record):
        return {key: value for key, value in record.items() if key != 'scope_sha256'}

    def registry(self):
        value = self._read()
        scope = self._registry_scope(value['scope'])
        records = []
        for identifier, record in sorted(self._registry_rows().items()):
            if record['ownership'] == 'managed':
                current = value['models'].get(identifier)
                if (current is None or record['scope_sha256'] != scope
                        or any(current[key] != record[key] for key in ('reference', 'digest'))):
                    continue
            records.append(self._registry_public(record))
        return {'records': records, 'installed': 'not_queried',
                'selectable': 'not_queried', 'loaded': 'not_queried'}

    def _record_registry(self, entries, value, *, replace_managed=False):
        scope = self._registry_scope(value['scope'])
        current = self._registry_rows_for_scope(scope)
        merged = {key: row for key, row in current.items() if not replace_managed or row['ownership'] == 'imported'}
        by_reference = {row['reference']: row['id'] for row in merged.values()}
        records = []
        for identifier, actual, ownership in entries:
            record = {'schema_version': 1, 'id': identifier, 'engine': 'ollama',
                      **actual, 'ownership': ownership, 'scope_sha256': scope}
            prior = merged.get(identifier)
            if (by_reference.get(actual['reference'], identifier) != identifier
                    or (prior is not None and prior != record)):
                raise ModelError('local model registry identity conflicts with verified asset')
            by_reference[actual['reference']] = identifier
            merged[identifier] = record
            records.append(self._registry_public(record))
        if len(merged) > REGISTRY_LIMIT:
            raise ModelError('local model registry capacity reached')
        if merged != current:
            self._publish_registry_snapshot(merged)
        return records

    @staticmethod
    def _write_registry_json(path, value):
        raw = (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()
        if len(raw) > MAX_JSON:
            raise ModelError('model registry metadata exceeds bound')
        fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            checked_file(stream.fileno())
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _sync_registry_dir(path):
        fd = os.open(path, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _publish_registry_snapshot(self, rows):
        # All writers hold models.lock. Readers only resolve committed,
        # immutable snapshots, which remain available after pointer changes.
        directory = checked_directory(self.directory/'registry', create=True)
        token = secrets.token_hex(16)
        snapshot = directory/('snapshot-'+token)
        snapshot.mkdir(mode=0o700)
        temporary = directory/('.active-'+token)
        committing = False
        try:
            checked_directory(snapshot)
            for identifier, record in sorted(rows.items()):
                self._write_registry_json(snapshot/(identifier+'.json'), record)
            self._sync_registry_dir(snapshot)
            self._write_registry_json(temporary, {'schema_version': 1, 'snapshot': snapshot.name})
            self._sync_registry_dir(directory)
            # The only publication point. After attempting it, never remove the
            # snapshot, even if durability is uncertain due to an I/O failure.
            committing = True
            os.replace(temporary, directory/'active.json')
            self._sync_registry_dir(directory)
        finally:
            if not committing:
                temporary.unlink(missing_ok=True)
                shutil.rmtree(snapshot)

    @staticmethod
    def _confirmed_registry_asset(inventory, reference, digest):
        selected = inventory.get(reference)
        if selected is None or selected['digest'] != digest:
            raise ModelError('selected installed model identity was not confirmed')
        return {'reference': reference, 'digest': digest, 'size_bytes': selected['size_bytes']}

    def _check_registry_location(self):
        # Match setup's storage boundary before mutation can create its state lock.
        for location in (self.directory, self.model_store):
            if location is None:
                continue
            parts = Path(location).parts
            if any(parts[i:i+2] in (('plugins', 'cache'), ('plugins', 'marketplaces'))
                   for i in range(len(parts)-1)):
                raise ModelError('keep model data outside the plugin installation and cache')

    def register_existing(self, identifier, reference, digest):
        if (not isinstance(identifier, str) or not MODEL_ID.fullmatch(identifier)
                or not isinstance(reference, str) or not REFERENCE.fullmatch(reference)
                or not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
            raise ModelError('provide a stable model ID, exact engine tag and full digest')
        self._check_registry_location()
        with self._mutation() as value:
            if (identifier in value['models']
                    or any(row['reference'] == reference for row in value['models'].values())):
                raise ModelError('existing LAF-managed model; use migrate-registry')
            inventory = {row['reference']: row for row in self.adapter.installed()}
            actual = self._confirmed_registry_asset(inventory, reference, digest)
            return self._record_registry([(identifier, actual, 'imported')], value)[0]

    def migrate_registry(self):
        self._check_registry_location()
        with self._mutation() as value:
            inventory = {row['reference']: row for row in self.adapter.installed()} if value['models'] else {}
            entries = [(identifier, self._confirmed_registry_asset(inventory, row['reference'], row['digest']),
                        'managed') for identifier, row in sorted(value['models'].items())]
            return self._record_registry(entries, value, replace_managed=True)

    def status(self):
        value = self._read()
        return {'installed_count':len(value['models']),'pending':value['pending'],
                'loaded_state':'not_queried','automatic_retry':False}
    def inspect(self, identifier):
        """GET-only engine observation; never acquire locks or create state."""
        entry=self.catalog.get(identifier)
        if entry is None: raise ModelError('unknown catalog model')
        if entry['engine']!='ollama' or self.adapter is None:
            raise ModelError('explicit supported engine required for inspection')
        identity=getattr(self.adapter,'identity',None)
        if not text_field(identity): raise ModelError('explicit engine identity required')
        value=self._read()
        scope={'engine':identity,'model_store':str(self.model_store),'generation_lock':str(self.generation_lock)}
        scope_matches=value['scope']==scope
        saved=value['models'].get(identifier)
        installed=next((m for m in self.adapter.installed() if m['reference']==entry['reference']),None)
        running=self.adapter.running()
        registered=bool(scope_matches and saved and saved['reference']==entry['reference'] and saved['digest']==entry['digest'])
        return {'id':identifier,'installed':installed is not None,
                'engine_manifest_digest':None if installed is None else installed['digest'],
                'catalog_manifest_digest':entry['digest'],
                'digest_matches':None if installed is None or entry['digest'] is None else installed['digest']==entry['digest'],
                'loaded':any(m['reference']==entry['reference'] for m in running),
                'registry_record_matches':registered,
                'managed':bool(registered and installed and installed['digest']==saved['digest']),
                'management_scope_matches':scope_matches,'pending_action':value['pending'],
                'compatibility':'not_evaluated','weight_hashes':'not_recomputed',
                'storage_mapping':'not_verified','mutation_performed':False,'automatic_retry':False}
    @contextmanager
    def _mutation(self):
        if self.adapter is None or self.generation_lock is None or self.model_store is None:
            raise ModelError('explicit engine, persistent store and existing generation lock required')
        checked_directory(self.model_store)
        identity=getattr(self.adapter,'identity',None)
        if not text_field(identity): raise ModelError('explicit engine management identity required')
        lock = Path(self.generation_lock)
        if lock.name != 'generation.lock': raise ModelError('existing Router generation.lock required')
        with private_lock(lock.parent,lock.name), private_lock(self.directory,'models.lock',create=True):
            value = self._read()
            scope={'engine':identity,'model_store':str(Path(self.model_store)),'generation_lock':str(lock)}
            if value['scope'] is not None and value['scope']!=scope:
                raise ModelError('model management scope changed; refusing operation')
            if value['pending'] is not None:
                raise ModelError('previous operation uncertain; inspect engine and journal, do not retry')
            value['scope']=scope
            yield value
    def _idle(self):
        if self.adapter.running(): raise ModelError('engine has loaded models; no mutation attempted')
    def _find(self, reference):
        return next((entry for entry in self.adapter.installed() if entry['reference']==reference),None)
    def _begin(self, value, action, entry):
        value['pending'] = {'action':action,'id':entry['id'],'reference':entry['reference'],'started_at':int(time.time())}
        self._write(value)
    def _finish(self, value):
        value['history']=(value['history']+[value['pending']])[-64:]
        value['pending']=None
        self._write(value)
    def install(self, identifier):
        entry = self.catalog.get(identifier)
        if entry is None: raise ModelError('unknown catalog model')
        if entry['user_setup_required']: raise ModelError('user must complete model license/authentication setup outside LAF')
        if entry['engine']!='ollama': raise ModelError('engine installation adapter not available')
        if any(entry[field] is None for field in FIELDS): raise ModelError('model provenance, digest and capacity must be verified before installation')
        with self._mutation() as value:
            rows = self._registry_rows_for_scope(self._registry_scope(value['scope']))
            prior=value['models'].get(identifier)
            if prior is None:
                if len(value['models'])>=128:
                    raise ModelError('managed model registry capacity reached')
                if any(row['ownership'] == 'imported' and
                       (row['id'] == identifier or row['reference'] == entry['reference'])
                       for row in rows.values()):
                    raise ModelError('catalog ID or engine reference conflicts with an imported model; no installation attempted')
            self._idle()
            present=self._find(entry['reference'])
            if present and (present['digest']!=entry['digest'] or prior is None):
                raise ModelError('existing unmanaged or differing engine model; refusing adoption or replacement')
            if prior and any(prior[k]!=entry[k] for k in FIELDS):
                raise ModelError('managed model differs from catalog; refusing replacement')
            if not present and shutil.disk_usage(self.model_store).free<entry['required_bytes']:
                raise ModelError('persistent model store has insufficient free capacity')
            self._begin(value,'install',entry)
            try:
                if not present: self.adapter.install(entry['reference'])
                actual=self._find(entry['reference'])
                if actual is None or actual['digest']!=entry['digest']:
                    raise ModelError('installed model digest not confirmed')
                record=dict(entry,status='installed',size_bytes=actual['size_bytes'])
                value['models'][identifier]=record
                self._finish(value)
                return copy.deepcopy(record)
            except (Exception,KeyboardInterrupt):
                raise ModelError('installation outcome uncertain; inspect engine and journal, not automatically retried') from None
    def remove(self, identifier):
        # No engine calls for unmanaged models, including a current catalog name.
        if identifier not in self._read()['models']: raise ModelError('model is not LAF-managed')
        with self._mutation() as value:
            entry=value['models'].get(identifier)
            if entry is None: raise ModelError('model is no longer managed')
            self._idle()
            actual=self._find(entry['reference'])
            if actual and actual['digest']!=entry['digest']:
                raise ModelError('engine model identity changed; refusing removal')
            self._begin(value,'remove',entry)
            try:
                if actual: self.adapter.remove(entry['reference'])
                if self._find(entry['reference']) is not None:
                    raise ModelError('engine removal not confirmed')
                del value['models'][identifier]
                self._finish(value)
                return {'id':identifier,'status':'removed','automatic_retry':False}
            except (Exception,KeyboardInterrupt):
                raise ModelError('removal outcome uncertain; inspect engine and journal, not automatically retried') from None


def local_status():
    directory=os.environ.get('LAF_MODEL_STATE_DIR',str(Path.home()/'.local/state/laf-models'))
    return ModelManager(directory,read_catalog()).status()

def main(argv=None):
    from . import model_setup

    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',help='private JSON catalog containing verified model metadata')
    parser.add_argument('--config',help='private JSON configuration; required for mutations')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('catalog');sub.add_parser('list');sub.add_parser('status')
    sub.add_parser('installable', help='show owner catalog entries eligible by metadata only')
    sub.add_parser('registry', help='show local records without querying the engine or Router')
    sub.add_parser('migrate-registry', help='copy verified legacy records without rewriting models.json')
    register = sub.add_parser('register-existing', help='record an exact installed model without adopting it')
    register.add_argument('model')
    register.add_argument('--reference', required=True)
    register.add_argument('--digest', required=True)
    for action in ('install','remove','inspect'):
        sub.add_parser(action).add_argument('model')
    discover = sub.add_parser('discover', help='read the official publisher model list')
    discover.add_argument('--publisher', choices=model_setup.PUBLISHERS, required=True)
    discover.add_argument('--limit', type=int, default=12)
    discover.add_argument('--search')
    sub.add_parser('artifact', help='verify metadata of one reviewed Ollama artifact').add_argument('model')
    setup = sub.add_parser('setup', help='write a new private configuration without contacting an engine')
    setup.add_argument('--engine-url', required=True)
    setup.add_argument('--generation-lock', type=Path, required=True)
    setup.add_argument('--model-store', type=Path)
    setup.add_argument('--state-dir', type=Path)
    download = sub.add_parser('download', help='install the selected artifact and verify its full digest')
    download.add_argument('model')
    download.add_argument('--digest', required=True)
    sub.add_parser('inventory', help='read exact model tags and digests from the configured local engine')
    existing = sub.add_parser('use-installed', help='select a verified model without adopting it into the registry')
    existing.add_argument('--reference', required=True)
    existing.add_argument('--digest', required=True)
    existing.add_argument('--profile', required=True)
    existing_file = sub.add_parser('use-file', help='verify a downloaded GGUF and prepare a local Ollama Modelfile')
    existing_file.add_argument('path', type=Path)
    existing_file.add_argument('--digest', required=True)
    existing_file.add_argument('--modelfile', type=Path, required=True)
    args=parser.parse_args(argv)
    try:
        if args.command in {'discover', 'artifact', 'setup', 'download', 'inventory', 'use-installed', 'use-file'}:
            if args.catalog:
                raise ModelError('the setup commands do not accept a replacement catalog')
            if args.command == 'discover':
                out = model_setup.discover(args.publisher, args.limit, args.search)
            elif args.command == 'artifact':
                out = model_setup.artifact(args.model)
            elif args.command == 'setup':
                if not args.config:
                    raise ModelError('choose a new private destination with --config')
                out = model_setup.setup(args.config, args.engine_url, args.generation_lock, args.model_store, args.state_dir)
            elif args.command == 'download':
                out = model_setup.download(args.config, args.model, args.digest)
            elif args.command == 'inventory':
                out = model_setup.installed(args.config)
            elif args.command == 'use-installed':
                out = model_setup.installed(args.config, args.reference, args.digest, args.profile)
            else:
                out = model_setup.prepare_existing_file(args.path, args.digest, args.modelfile)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        catalog=read_catalog(args.catalog)
        engine_actions = {'install', 'remove', 'inspect', 'register-existing', 'migrate-registry'}
        if args.command=='catalog': out={'catalog':catalog,'automatic_retry':False}
        elif args.command=='installable':
            out = {'installable': installable_catalog(catalog), 'installation_performed': False}
        else:
            config = read_engine_config(args.config) if args.config or args.command in engine_actions else {}
            state=config.get('state_dir',os.environ.get('LAF_MODEL_STATE_DIR',str(Path.home()/'.local/state/laf-models')))
            engine=OllamaAdapter(OllamaHTTP(config['engine_url'])) if args.command in engine_actions else None
            manager=ModelManager(state,catalog,engine,config.get('generation_lock'),config.get('model_store'))
            if args.command=='list': out={'models':manager.list(),'loaded_state':'not_queried'}
            elif args.command=='status': out=manager.status()
            elif args.command=='registry': out=manager.registry()
            elif args.command=='register-existing':
                out=manager.register_existing(args.model,args.reference,args.digest)
            elif args.command=='migrate-registry':
                out={'records':manager.migrate_registry()}
            else: out=getattr(manager,args.command)(args.model)
        print(json.dumps(out,ensure_ascii=False,indent=2));return 0
    except ModelError as exc:
        print(json.dumps({'status':'blocked_or_uncertain','error':str(exc),'automatic_retry':False}),file=sys.stderr)
        return 1
    except (OSError,ValueError,http.client.HTTPException):
        print(json.dumps({'status':'blocked_or_uncertain','error':'model operation not confirmed; inspect private configuration, metadata and engine state','automatic_retry':False}),file=sys.stderr)
        return 1


if __name__=='__main__': raise SystemExit(main())
