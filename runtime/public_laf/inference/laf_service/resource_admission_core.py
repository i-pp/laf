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


"""Incremental durable host reservations; no service control or automatic replay.

Added 2026-11-15: atomic CPU/RAM/GPU/VRAM and provider-lease admission.
This module is original LAF project code under the repository KOL-v1 license.
"""

from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

class ResourceExceeded(RuntimeError):
    pass

DIMENSIONS = ('cpu_millicores', 'ram_bytes', 'gpu_milliunits', 'vram_bytes')
MAX_INTEGER = 2**63 - 1
DIRECTORY = None  # Tests may supply a private directory; production uses the OS account.
PASSWD = Path('/etc/passwd')


class AdmissionWait(ResourceExceeded):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def vector(value):
    if (not isinstance(value, dict) or set(value) != set(DIMENSIONS)
            or any(type(n) is not int or not 0 <= n <= MAX_INTEGER for n in value.values())):
        raise ValueError('Invalid resource vector')
    return dict(value)


def resource_only(value):
    return isinstance(value, dict) and value.get('schema') in (2,3) and value.get('admission_mode') == 'resources'


def state_directory():
    """One durable same-UID location, independent of HOME/XDG/CODEX_HOME."""
    info = PASSWD.stat()
    if (PASSWD.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_mode & 0o022 or info.st_size > 1024**2):
        raise AdmissionWait('resource_state_identity_unknown')
    matches = []
    for line in PASSWD.read_text().splitlines():
        fields = line.split(':')
        if len(fields) == 7 and fields[2].isascii() and fields[2].isdigit() and int(fields[2]) == os.getuid():
            matches.append(fields[5])
    if len(matches) != 1 or not Path(matches[0]).is_absolute():
        raise AdmissionWait('resource_state_identity_unknown')
    target = Path(matches[0]) / '.local/state/laf/resource-admission'
    legacy = Path.home() / '.local/state/laf/resource-admission'
    if legacy != target:
        try:(legacy / 'reservations.sqlite').stat()
        except FileNotFoundError:pass
        except OSError as error:raise AdmissionWait('resource_legacy_state_pending') from error
        else:raise AdmissionWait('resource_legacy_state_pending')
    return target


def owner_views(runtime):
    """Private operator metadata; never creates or adopts a ledger on read."""
    directory = Path(state_directory() if DIRECTORY is None else DIRECTORY).absolute()
    path = directory / 'reservations.sqlite'
    try:info=path.stat()
    except FileNotFoundError:return []
    except OSError as error:raise AdmissionWait('resource_owner_unknown') from error
    if any(p.is_symlink() for p in [directory, *directory.parents, path]):
        raise AdmissionWait('resource_owner_unknown')
    parent = directory.stat()
    if (info.st_uid != os.getuid() or parent.st_uid != os.getuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600 or stat.S_IMODE(parent.st_mode) != 0o700):
        raise AdmissionWait('resource_owner_unknown')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT id,role,phase,identity FROM admission_owners WHERE runtime=?',
                          (str(Path(runtime).absolute()),)).fetchall()
    return [{'owner': row[0], 'role': row[1], 'phase': row[2], 'live': owner_live(json.loads(row[3]))} for row in rows]


def owner_identity():
    try:
        fields = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
        start = fields[19]
    except (OSError, IndexError, ValueError) as exc:
        raise AdmissionWait('resource_owner_unknown') from exc
    return {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'pid_namespace': os.readlink('/proc/self/ns/pid'), 'uid': os.getuid(),
            'pid': os.getpid(), 'start': start}


def owner_live(identity):
    """None means unknown, never dead or reclaimable."""
    try:
        if identity['uid'] != os.getuid(): return None
        if identity['boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip(): return False
        if identity['pid_namespace'] != os.readlink('/proc/self/ns/pid'): return None
        if type(identity['pid']) is not int or identity['pid'] <= 0: return None
        try:
            fields = Path('/proc/' + str(identity['pid']) + '/stat').read_text().rsplit(')', 1)[1].split()
        except FileNotFoundError:
            return False
        # Permission errors and malformed counters are unknown, not death.
        return fields[19] == identity['start'] and fields[0] not in ('Z', 'X')
    except (OSError, ValueError, KeyError, TypeError, IndexError): return None


def policy(value):
    keys = {'schema', 'capacity', 'reserve', 'max_age_seconds', 'providers', 'workload'}
    if not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] not in (1, 2, 3):
        raise ValueError('Invalid resource admission policy')
    if value['schema'] in (2,3): keys.add('admission_mode')
    allowed = keys | ({'pipeline', 'local_inference'} if value['schema'] in (2,3) else set())
    if not keys <= set(value) or set(value) - allowed or value['schema'] in (2,3) and value['admission_mode'] != 'resources':
        raise ValueError('Invalid resource admission fields or mode')
    capacity, reserve = vector(value['capacity']), vector(value['reserve'])
    if not capacity['cpu_millicores'] or not capacity['ram_bytes']:
        raise ValueError('CPU and RAM budgets must be positive')
    if any(reserve[k] > capacity[k] for k in DIMENSIONS): raise ValueError('Resource reserve exceeds capacity')
    age = value['max_age_seconds']
    if type(age) not in (int, float) or not math.isfinite(age) or not 0 < age <= 5:
        raise ValueError('Resource observation age must be finite and at most five seconds')
    if value['workload'] != 'cloud_api':
        raise ValueError('Ordinary integration supports explicitly classified cloud API work only')
    providers = value['providers']
    if not isinstance(providers, dict) or len(providers) > 32: raise ValueError('Invalid provider quota evidence')
    for name, quota in providers.items():
        if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',name) or not isinstance(quota,dict):
            raise ValueError('Invalid provider contract')
        if value['schema']==3 and quota.get('mode')=='feedback':
            required={'mode','account_sha256','backoff_seconds'}
            if (not required<=set(quota) or set(quota)-required-{'usage_account_sha256'}
                    or type(quota['backoff_seconds']) not in (int,float)
                    or not math.isfinite(quota['backoff_seconds']) or not 1<=quota['backoff_seconds']<=3600
                    or not isinstance(quota['account_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',quota['account_sha256'])
                    or 'usage_account_sha256' in quota and (not isinstance(quota['usage_account_sha256'],str)
                        or not re.fullmatch('[a-f0-9]{64}',quota['usage_account_sha256']))):
                raise ValueError('Explicit account-bound provider feedback contract required')
            continue
        if value['schema']==3:raise ValueError('Schema3 providers require explicit feedback mode')
        quota_keys={'limit','valid_until','evidence'}|({'account_sha256'} if value['schema']==2 else set())
        if (set(quota)!=quota_keys
                or type(quota['limit']) is not int or not 1<=quota['limit']<=MAX_INTEGER
                or type(quota['valid_until']) not in (int,float) or not math.isfinite(quota['valid_until']) or quota['valid_until']<=0
                or not isinstance(quota['evidence'],str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,128}',quota['evidence'])
                or value['schema'] in (2,3) and (not isinstance(quota['account_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',quota['account_sha256']))):
            raise ValueError('Provider quota needs finite explicit limits and account evidence')
    if 'pipeline' in value:
        child = vector(value['pipeline'])
        if not child['cpu_millicores'] or not child['ram_bytes'] or child['gpu_milliunits'] or child['vram_bytes']:
            raise ValueError('Pipeline local client demand must be finite CPU/RAM only')
    if 'local_inference' in value:
        local_policy(value['local_inference'])
        if capacity['gpu_milliunits'] != 1000:
            raise ValueError('Only one exclusive pinned GPU is supported; partitions are unverified')
    return json.loads(json.dumps(value, sort_keys=True))


def local_policy(value):
    if not isinstance(value, dict) or set(value) != {'device', 'controllers', 'models'}:
        raise ValueError('Explicit local device, controller and model profiles required')
    device = value['device']
    if (not isinstance(device, dict) or set(device) != {'pci_bdf', 'vendor', 'device', 'memory_topology', 'valid_until', 'evidence'}
            or not isinstance(device['pci_bdf'], str) or not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', device['pci_bdf'])
            or device['vendor'] != '0x1002' or not isinstance(device['device'], str)
            or not re.fullmatch(r'0x[0-9a-f]{4}', device['device'])
            or device['memory_topology'] not in ('discrete', 'unified')
            or type(device['valid_until']) not in (int, float) or not math.isfinite(device['valid_until']) or device['valid_until'] <= 0
            or not isinstance(device['evidence'], str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', device['evidence'])):
        raise ValueError('Pinned AMD device and finite topology evidence required')
    controllers = value['controllers']
    if not isinstance(controllers, dict) or set(controllers) != {'host', 'core', 'ollama'}:
        raise ValueError('All local execution controllers must be accounted')
    for raw in controllers.values():
        v = vector(raw)
        if not v['cpu_millicores'] or not v['ram_bytes'] or v['gpu_milliunits'] or v['vram_bytes']:
            raise ValueError('Local controllers require finite CPU/RAM only')
    models = value['models']
    if not isinstance(models, dict) or not 1 <= len(models) <= 32: raise ValueError('Bounded model profiles required')
    for name, model in models.items():
        if (not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name)
                or not isinstance(model, dict) or set(model) != ({'engine', 'artifact_sha256', 'residency', 'execution'} | ({'loading'} if model.get('engine') == 'core' else set()))
                or model['engine'] not in ('core', 'ollama') or not isinstance(model['artifact_sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', model['artifact_sha256'])):
            raise ValueError('Immutable local model profile required')
        if model['engine'] == 'core' and name != 'flash': raise ValueError('Only the owned Flash Core model is supported')
        for phase in (('residency', 'execution', 'loading') if model['engine'] == 'core' else ('residency', 'execution')):
            v = vector(model[phase])
            if (not v['cpu_millicores'] or not v['ram_bytes'] or not v['vram_bytes']
                    or v['gpu_milliunits'] != (0 if phase == 'residency' else 1000)):
                raise ValueError('Residency and exclusive GPU execution need distinct finite profiles')
    return value


def effective_resources(demand, device):
    result = dict(demand['resources'])
    if device:
        # Conservative coupling: the same physical UMA bytes never become two
        # independent pools. Discrete topology is not natively attested yet, so
        # all configured GPU bytes are also charged against host RAM.
        result['ram_bytes'] += result['vram_bytes']
        if result['ram_bytes'] > MAX_INTEGER: raise AdmissionWait('resource_ram_bytes_budget')
    return result


def account_fingerprint(result):
    """Private digest only. Raw identity never enters task/MCP diagnostics."""
    account = result.get('account') if isinstance(result, dict) else None
    if (not isinstance(account, dict) or account.get('type') != 'chatgpt'
            or not isinstance(account.get('email'), str) or not account['email'].strip()
            or not isinstance(account.get('planType'), str) or not account['planType']):
        return None
    value = {'provider': 'chatgpt', 'email': account['email'].strip().casefold(), 'plan': account['planType']}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def generation(value):
    validated = policy(value)
    # Protocol13 pins host capture uncertainty and ordinary delivery authority; schema2 keeps protocol5.
    # Protocol 5 pins descriptor-bound release and strict positive absence proofs.
    # Counterless DRM/KFD descriptors cannot prove physical release. Earlier
    # unpublished participants must not join this stricter resource generation.
    encoded = {'protocol': 13 if validated['schema']==3 else 5, 'policy': validated} if resource_only(validated) else validated
    return hashlib.sha256(json.dumps(encoded, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()



def provider_feedback(quota):
    return isinstance(quota,dict) and quota.get('mode')=='feedback'


def provider_identity(quota,account,wall=None):
    if (not isinstance(quota,dict) or quota.get('account_sha256')!=account
            or not provider_feedback(quota) and quota.get('valid_until',0)<=(time.time() if wall is None else wall)):
        raise AdmissionWait('resource_provider_account_unknown')


def provider_snapshot(value):
    """Narrow usage facts, never capacity, payment or concurrency entitlement."""
    if not isinstance(value,dict) or not isinstance(value.get('rateLimits'),dict):raise ValueError('Invalid usage snapshot')
    allowed=value.get('ordinaryUsageAllowed')
    if allowed is not None and type(allowed) is not bool:raise ValueError('Invalid ordinary usage permission')
    account=value.get('accountId')
    if account is not None and (not isinstance(account,str) or not 0<len(account)<=4096):raise ValueError('Invalid usage account')
    usage=hashlib.sha256(account.encode()).hexdigest() if account is not None else None
    legacy=value['rateLimits'];limit=legacy.get('limitId')
    if limit is not None and (not isinstance(limit,str) or not 0<len(limit)<=128):raise ValueError('Invalid usage bucket id')
    buckets=[('limit:'+limit if limit is not None else 'legacy',legacy)];many=value.get('rateLimitsByLimitId')
    if many is not None:
        if not isinstance(many,dict) or len(many)>32 or any(not isinstance(k,str) or not 0<len(k)<=128 or not isinstance(v,dict) for k,v in many.items()):raise ValueError('Invalid usage buckets')
        buckets += [('limit:'+key,bucket) for key,bucket in many.items()]
    denied=set();clear=set();meaningful=False
    if allowed is False:denied.add('provider')
    for ident,bucket in buckets:
        declared=bucket.get('limitId')
        if declared is not None:
            if not isinstance(declared,str) or not 0<len(declared)<=128:raise ValueError('Invalid nested usage bucket id')
            if ident!='limit:'+declared:raise ValueError('Contradictory usage bucket identity')
        spent=bucket.get('spendControlReached')
        if spent is not None and type(spent) is not bool:raise ValueError('Invalid spending feedback')
        if spent is True:denied.add(ident+'/spend')
        elif spent is False:clear.add(ident+'/spend')
        reached=bucket.get('rateLimitReachedType')
        if reached is not None:
            if not isinstance(reached,str) or not 0<len(reached)<=128:raise ValueError('Invalid reached feedback')
            denied.add(ident+'/reached')
        elif 'rateLimitReachedType' in bucket:clear.add(ident+'/reached')
        for name in ('primary','secondary'):
            window=bucket.get(name)
            if window is None:continue
            if not isinstance(window,dict) or type(window.get('usedPercent')) is not int or not 0<=window['usedPercent']<=2**31-1:raise ValueError('Invalid usage window')
            for field in ('resetsAt','windowDurationMins'):
                if window.get(field) is not None and (type(window[field]) is not int or not 0<=window[field]<=2**63-1):raise ValueError('Invalid usage window time')
            meaningful=True
            if window['usedPercent']>=100:denied.add(ident+'/'+name)
            else:clear.add(ident+'/'+name)
    if allowed is True and meaningful and not denied:clear.add('provider')
    return {'decision':'block' if denied else 'allow' if 'provider' in clear else 'unavailable',
            'usage_account_sha256':usage,'denied_scopes':sorted(denied),'clear_scopes':sorted(clear)}


def provider_restriction(code=None,data=None):
    """Only structured correlated codes; message strings are never parsed."""
    if type(code) is int and code==429:return True
    if not isinstance(data,dict):return False
    info=data.get('codexErrorInfo')
    if isinstance(info,str):return info in ('rateLimitExceeded','usageLimitExceeded')
    if isinstance(info,dict) and len(info)==1:
        key=next(iter(info));frame=info[key]
        return (key in ('httpConnectionFailed','responseStreamConnectionFailed','responseStreamDisconnected','responseTooManyFailedAttempts')
                and isinstance(frame,dict) and type(frame.get('httpStatusCode')) is int and frame['httpStatusCode']==429)
    return False


def require_ordinary_provider(settings):
    """Ordinary feedback requires the explicit resource-only schema3 contract."""
    selected=settings.get('resource_admission',{})
    if provider_feedback(selected.get('providers',{}).get('chatgpt')) and not (resource_only(selected) and selected.get('schema')==3):
        raise AdmissionWait('resource_provider_feedback_unsupported')


def ordinary_request(settings, *, worker):
    """Reserve actual unchanged cgroup ceilings for the locally supervised tree."""
    require_ordinary_provider(settings)
    limits = [settings['runner_resource_policy']]
    if worker: limits.append(settings['worker'])
    controlled = resource_only(settings.get('resource_admission'))
    return {'kind': 'local_control' if controlled else 'cloud_api',
            'provider': None if controlled and provider_feedback(settings['resource_admission']['providers'].get('chatgpt')) else 'chatgpt',
            'resources': {'cpu_millicores': sum(v['cpu_percent'] * 10 for v in limits),
                          'ram_bytes': sum(v['memory_bytes'] for v in limits),
                          'gpu_milliunits': 0, 'vram_bytes': 0}}


def request(value):
    if not isinstance(value, dict) or set(value) != {'kind', 'provider', 'resources'}:
        raise ValueError('Invalid execution demand')
    result = {'kind': value['kind'], 'provider': value['provider'], 'resources': vector(value['resources'])}
    resources = result['resources']
    if not resources['cpu_millicores'] or not resources['ram_bytes']:
        raise ValueError('Execution CPU/RAM demand cannot be unknown or zero')
    if result['kind'] == 'cloud_api':
        if (not isinstance(result['provider'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', result['provider'])
                or resources['gpu_milliunits'] or resources['vram_bytes']):
            raise ValueError('Cloud work cannot claim local inference resources')
    elif result['kind'] in ('local_control', 'reconciliation'):
        if result['kind'] == 'reconciliation' and result['provider'] is not None:
            raise ValueError('Reconciliation cannot request a provider turn')
        if (result['provider'] is not None and (not isinstance(result['provider'], str)
                or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', result['provider']))
                or resources['gpu_milliunits'] or resources['vram_bytes']):
            raise ValueError('Local control requires zero local inference demand')
    elif result['kind'] in ('local_inference', 'model_residency', 'model_loading'):
        if (result['provider'] is not None or not resources['vram_bytes']
                or result['kind'] in ('local_inference', 'model_loading') and not resources['gpu_milliunits']
                or result['kind'] == 'model_residency' and resources['gpu_milliunits']):
            raise ValueError('Local inference requires explicit GPU/VRAM demand')
    else:
        raise ValueError('Workload kind is unknown')
    return result


def _identifier(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Expected canonical reservation identity')
    return value


class Ledger:
    def __init__(self, value, directory=None):
        self.policy = policy(value)
        self.generation = generation(value)
        selected = directory if directory is not None else DIRECTORY
        self.directory = Path(state_directory() if selected is None else selected).absolute()
        for parent in (self.directory, *self.directory.parents):
            if parent.is_symlink(): raise ValueError('Unsafe admission ledger ancestry')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Admission ledger directory must be private and owner-controlled')
        self.path = self.directory / 'reservations.sqlite'
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise ValueError('Unsafe admission ledger file')
        finally:
            os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=.05)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA synchronous=FULL')
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('CREATE TABLE IF NOT EXISTS admission_meta(id INTEGER PRIMARY KEY CHECK(id=1), generation TEXT, observation TEXT)')
                self.db.execute('CREATE TABLE IF NOT EXISTS reservations(token TEXT PRIMARY KEY, runtime TEXT NOT NULL, task TEXT NOT NULL, generation TEXT NOT NULL, demand TEXT NOT NULL, scopes TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN (\'HELD\',\'RELEASED\')), created REAL NOT NULL, released REAL, UNIQUE(runtime,task))')
                self.db.execute("CREATE TABLE IF NOT EXISTS admission_owners(id TEXT PRIMARY KEY,runtime TEXT NOT NULL,role TEXT NOT NULL,identity TEXT NOT NULL,generation TEXT NOT NULL,phase TEXT NOT NULL CHECK(phase IN ('HELD','RELEASED')))")
                self.db.execute('CREATE TABLE IF NOT EXISTS reservation_owners(token TEXT PRIMARY KEY REFERENCES reservations(token),owner TEXT NOT NULL REFERENCES admission_owners(id))')
                self.db.execute('CREATE TABLE IF NOT EXISTS resource_reconciliations(id TEXT PRIMARY KEY,token TEXT NOT NULL,evidence TEXT NOT NULL,created REAL NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS provider_feedback(provider TEXT NOT NULL,account TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(provider,account))')
                self.db.execute('CREATE TABLE IF NOT EXISTS reservation_parents(token TEXT PRIMARY KEY,parent TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS local_instances(token TEXT PRIMARY KEY,model TEXT NOT NULL,engine TEXT NOT NULL,owner TEXT NOT NULL,artifact TEXT NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS local_references(token TEXT PRIMARY KEY,instance TEXT NOT NULL)')
                old = self.db.execute('SELECT generation FROM admission_meta WHERE id=1').fetchone()
                if old and old[0] != self.generation:
                    if (self.db.execute("SELECT 1 FROM reservations WHERE phase='HELD' LIMIT 1").fetchone()
                            or self.db.execute("SELECT 1 FROM admission_owners WHERE phase='HELD' LIMIT 1").fetchone()):
                        raise AdmissionWait('resource_generation_held')
                    self.db.execute('UPDATE admission_meta SET generation=?,observation=NULL WHERE id=1', (self.generation,))
                elif not old:
                    self.db.execute('INSERT INTO admission_meta VALUES(1,?,NULL)', (self.generation,))
        except BaseException:
            self.db.close()
            raise

    def close(self): self.db.close()

    def held(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM reservations WHERE phase='HELD'")]

    def reserve(self, runtime, task, token, demand, observe, *, now=None, wall=None, scopes=(), owner=None, within=None,
                local_model=None, local_instance=None):
        """Commit all dimensions/provider quota, or only a telemetry warmup record.

        Observation runs under the same bounded SQLite write admission as the
        reservation. Old reservations never expire and never imply replay rights.
        """
        demand = request(demand)
        local = demand['kind'] in ('local_inference', 'model_residency', 'model_loading')
        if resource_only(self.policy) and demand['kind'] not in ('local_control', 'reconciliation') and not local:
            raise AdmissionWait('resource_provider_account_unknown')
        model = self.policy.get('local_inference', {}).get('models', {}).get(local_model)
        if resource_only(self.policy) and local:
            phase = ('residency' if demand['kind'] == 'model_residency' else
                     'loading' if demand['kind'] == 'model_loading' else 'execution')
            if not model or demand['resources'] != model.get(phase): raise AdmissionWait('resource_model_profile_unknown')
            if phase == 'residency' and local_instance is not None: raise AdmissionWait('resource_reservation_retained')
        scopes = list(scopes)
        requested_paths = [_scope_credit(item) for item in scopes]
        if len(set(requested_paths)) != len(requested_paths):
            raise AdmissionWait('resource_scope_unknown')
        _identifier(task); _identifier(token)
        runtime = str(Path(runtime).absolute())
        requested_now = now
        requested_wall = wall
        denied = None
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            pinned = self.db.execute('SELECT generation,observation FROM admission_meta WHERE id=1').fetchone()
            if pinned['generation'] != self.generation: raise AdmissionWait('resource_generation_held')
            old = self.db.execute('SELECT * FROM reservations WHERE runtime=? AND task=?', (runtime, task)).fetchone()
            if old:
                raise AdmissionWait('resource_reservation_retained')
            if resource_only(self.policy): self._owner(owner, runtime)
            if resource_only(self.policy) and demand['kind'] in ('local_inference', 'model_loading'):
                instance = self.db.execute("SELECT i.*,r.runtime,r.phase FROM local_instances i JOIN reservations r ON r.token=i.token WHERE i.token=?", (local_instance,)).fetchone()
                if (not instance or instance['phase'] != 'HELD' or instance['runtime'] != runtime
                        or instance['owner'] != owner or instance['model'] != local_model
                        or instance['artifact'] != model['artifact_sha256']):
                    raise AdmissionWait('resource_model_instance_unknown')
            rows = self.held()
            occupied = {_scope_credit(item) for row in rows for item in json.loads(row['scopes'])}
            if occupied.intersection(requested_paths): raise AdmissionWait('resource_scope_unknown')
            try:
                sample, state = observe(rows, json.loads(pinned['observation'] or 'null'))
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                raise AdmissionWait('resource_observation_unknown') from exc
            self.db.execute('UPDATE admission_meta SET observation=? WHERE id=1', (json.dumps(state),))
            decision_wall = time.time() if requested_wall is None else requested_wall
            try:
                self._admit(demand, rows, sample, time.monotonic() if requested_now is None else requested_now, decision_wall)
                if within is not None: self._container(runtime, owner, within, demand, sample)
            except AdmissionWait as exc:
                denied = exc
            if denied is None:
                self.db.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?,'HELD',?,NULL)",
                    (token, runtime, task, self.generation, json.dumps(demand), json.dumps(list(scopes)), decision_wall))
                if owner is not None:
                    self.db.execute('INSERT INTO reservation_owners VALUES(?,?)', (token, owner))
                if within is not None:
                    self.db.execute('INSERT INTO reservation_parents VALUES(?,?)', (token, within))
                if resource_only(self.policy) and local:
                    if demand['kind'] == 'model_residency':
                        self.db.execute('INSERT INTO local_instances VALUES(?,?,?,?,?)',
                            (token, local_model, model['engine'], owner, model['artifact_sha256']))
                    else:
                        self.db.execute('INSERT INTO local_references VALUES(?,?)', (token, local_instance))
        if denied: raise denied
        return token

    def _admit(self, demand, rows, sample, now, wall, *, existing=None):
        if (not isinstance(sample, dict) or type(sample.get('at')) not in (int, float)
                or not math.isfinite(sample['at']) or not 0 <= now - sample['at'] <= self.policy['max_age_seconds']):
            raise AdmissionWait('resource_observation_stale')
        free = sample.get('available', {})
        usage = sample.get('usage', {})
        device = self.policy.get('local_inference', {}).get('device')
        if demand['kind'] in ('local_inference', 'model_residency', 'model_loading') and resource_only(self.policy):
            if (not device or device['valid_until'] <= wall or sample.get('gpu_device') != device['pci_bdf']
                    or sample.get('gpu_memory_topology') != device['memory_topology']):
                raise AdmissionWait('resource_gpu_topology_unknown')
        wanted_resources = effective_resources(demand, device)
        for key in DIMENSIONS:
            wanted = wanted_resources[key]
            if not wanted: continue  # Explicit cloud GPU zero is classification, not fabricated telemetry.
            observed = free.get(key)
            if type(observed) is not int or not 0 <= observed <= MAX_INTEGER:
                raise AdmissionWait('resource_' + key + '_unknown')
            held = 0
            future = 0
            for row in rows:
                committed = effective_resources(json.loads(row['demand']), device)[key]
                used = usage.get(row['token'], {}).get(key)
                if type(used) is not int or not 0 <= used <= MAX_INTEGER:
                    raise AdmissionWait('resource_usage_unknown')
                held += committed
                future += max(0, committed - used)
            floor = 0 if demand['kind'] == 'reconciliation' else self.policy['reserve'][key]
            if key == 'cpu_millicores':
                cpu_at = sample.get('cpu_observed_at', sample['at'])
                if type(cpu_at) not in (int, float) or not math.isfinite(cpu_at) or not 0 <= now - cpu_at <= self.policy['max_age_seconds']:
                    raise AdmissionWait('resource_cpu_millicores_unknown')
            if wanted + held + floor > self.policy['capacity'][key]:
                raise AdmissionWait('resource_' + key + '_budget')
            growth = wanted
            if existing is not None:
                materialized = usage.get(existing, {}).get(key)
                if type(materialized) is not int or materialized < 0: raise AdmissionWait('resource_usage_unknown')
                growth = max(0, wanted - materialized)
            if growth + future + floor > observed:
                raise AdmissionWait('resource_' + key + '_headroom')
        if demand['kind'] == 'local_inference' and not resource_only(self.policy) and sample.get('gpu_memory_topology') != 'discrete':
            raise AdmissionWait('resource_gpu_topology_unknown')
        if demand['provider'] is not None:
            quota = self.policy['providers'].get(demand['provider'])
            if quota is None or not provider_feedback(quota) and quota['valid_until'] <= wall:
                raise AdmissionWait('resource_provider_quota_unknown')
            if provider_feedback(quota):
                if existing is None:self._provider_available(demand['provider'],quota)
            else:
                active = sum(json.loads(row['demand'])['provider'] == demand['provider'] for row in rows)
                if active >= quota['limit']: raise AdmissionWait('resource_provider_quota')

    def _container(self, runtime, owner, within, demand, sample, *, excluding=None):
        parent_task = self.db.execute('SELECT task FROM reservations WHERE token=?', (within,)).fetchone()
        if not parent_task: raise AdmissionWait('resource_container_unknown')
        parent = self.join(runtime, parent_task[0], within)
        association = self.db.execute('SELECT owner FROM reservation_owners WHERE token=?', (within,)).fetchone()
        if not association or association[0] != owner: raise AdmissionWait('resource_owner_unknown')
        parent_demand = json.loads(parent['demand'])
        if parent_demand['kind'] != 'local_control' or parent_demand['provider'] is not None:
            raise AdmissionWait('resource_container_unknown')
        children = self.db.execute("SELECT r.token,r.demand FROM reservations r JOIN reservation_parents p ON p.token=r.token WHERE p.parent=? AND r.phase='HELD'", (within,)).fetchall()
        available = sample.get('containers', {}).get(within, {})
        for key in ('cpu_millicores', 'ram_bytes'):
            headroom = available.get(key)
            if type(headroom) is not int or headroom < 0: raise AdmissionWait('resource_container_unknown')
            promised = sum(json.loads(child['demand'])['resources'][key] for child in children if child['token'] != excluding)
            if demand['resources'][key] + promised > min(parent_demand['resources'][key], headroom):
                raise AdmissionWait('resource_container_' + key + '_headroom')

    def recheck(self, runtime, task, token, observe):
        denied = None
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.join(runtime, task, token)
            rows = self.held()
            previous = self.db.execute('SELECT observation FROM admission_meta WHERE id=1').fetchone()[0]
            try: sample, state = observe(rows, json.loads(previous or 'null'))
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                raise AdmissionWait('resource_observation_unknown') from exc
            self.db.execute('UPDATE admission_meta SET observation=? WHERE id=1', (json.dumps(state),))
            try:
                demand = json.loads(row['demand'])
                self._admit(demand, [r for r in rows if r['token'] != token], sample,
                            time.monotonic(), time.time(), existing=token)
                parent = self.db.execute('SELECT parent FROM reservation_parents WHERE token=?', (token,)).fetchone()
                if parent:
                    owner = self.db.execute('SELECT owner FROM reservation_owners WHERE token=?', (token,)).fetchone()
                    self._container(runtime, owner[0], parent[0], demand, sample, excluding=token)
            except AdmissionWait as exc: denied = exc
        if denied: raise denied

    def register_owner(self, runtime, role):
        if role not in ('daemon', 'standalone', 'standalone_controller', 'pipeline', 'router', 'router_recovery'): raise ValueError('Unknown execution owner role')
        ident = str(uuid.uuid4()); identity = owner_identity()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("INSERT INTO admission_owners VALUES(?,?,?,?,?,'HELD')",
                            (ident, str(Path(runtime).absolute()), role, json.dumps(identity), self.generation))
        return ident

    def _owner(self, ident, runtime):
        row = self.db.execute("SELECT * FROM admission_owners WHERE id=? AND runtime=? AND generation=? AND phase='HELD'",
                              (ident, str(Path(runtime).absolute()), self.generation)).fetchone()
        if not row or owner_live(json.loads(row['identity'])) is not True:
            raise AdmissionWait('resource_owner_unknown')
        return dict(row)

    def close_owner(self, ident):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM admission_owners WHERE id=?', (ident,)).fetchone()
            if not row or json.loads(row['identity']) != owner_identity(): return False
            if self.db.execute("SELECT 1 FROM reservation_owners o JOIN reservations r ON r.token=o.token WHERE o.owner=? AND r.phase='HELD' LIMIT 1", (ident,)).fetchone(): return False
            self.db.execute("UPDATE admission_owners SET phase='RELEASED' WHERE id=?", (ident,))
        return True

    def reconcile_owner(self, ident, *, apply=False):
        row = self.db.execute("SELECT * FROM admission_owners WHERE id=? AND phase='HELD'", (ident,)).fetchone()
        if not row or owner_live(json.loads(row['identity'])) is not False:
            raise AdmissionWait('resource_owner_unknown')
        records = list(self.db.execute("SELECT r.* FROM reservations r JOIN reservation_owners o ON o.token=r.token WHERE o.owner=? AND r.phase='HELD'", (ident,)))
        clear = []
        for grant in records:
            demand = json.loads(grant['demand'])
            if demand['provider'] is not None: continue  # Account outcomes need exact task reconciliation.
            if demand['kind'] in ('model_residency','local_inference','model_loading'):
                continue  # Empty cgroups do not prove device/physical release.
            scopes = json.loads(grant['scopes'])
            if not scopes: raise AdmissionWait('resource_scope_unknown')
            for descriptor in scopes:
                scope = _scope_path(descriptor)
                if not scope_empty(scope):raise AdmissionWait('resource_scope_unknown')
            clear.append(grant['token'])
        if apply:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                if owner_live(json.loads(row['identity'])) is not False: raise AdmissionWait('resource_owner_unknown')
                for token in clear:
                    proof = str(uuid.uuid4())
                    self.db.execute('INSERT INTO resource_reconciliations VALUES(?,?,?,?)',
                        (proof, token, json.dumps({'owner': ident, 'local_scope_empty': True, 'provider_unused': True}), time.time()))
                    self.db.execute("UPDATE reservations SET phase='RELEASED',released=? WHERE token=?", (time.time(), token))
                held = self.db.execute("SELECT 1 FROM reservations r JOIN reservation_owners o ON o.token=r.token WHERE o.owner=? AND r.phase='HELD' LIMIT 1", (ident,)).fetchone()
                if not held: self.db.execute("UPDATE admission_owners SET phase='RELEASED' WHERE id=?", (ident,))
        return {'owner': ident, 'status': 'APPLIED' if apply else 'READY',
                'local_grants_releasable': len(clear),
                'provider_grants_retained': sum(json.loads(r['demand'])['provider'] is not None for r in records),
                'local_gpu_grants_retained': sum(json.loads(r['demand'])['kind'] in ('model_residency','local_inference','model_loading') for r in records),
                'automatic_retry': False}

    def router_snapshot(self, runtime, ident):
        """Immutable local recovery candidate, never a right to stop or replay."""
        _identifier(ident)
        runtime = str(Path(runtime).absolute())
        row = self.db.execute("SELECT * FROM admission_owners WHERE id=? AND runtime=? AND generation=? AND role='router' AND phase='HELD'",
                              (ident, runtime, self.generation)).fetchone()
        if not row or owner_live(json.loads(row['identity'])) is not False:
            raise AdmissionWait('resource_owner_unknown')
        grants = [dict(r) for r in self.db.execute("SELECT r.* FROM reservations r JOIN reservation_owners o ON o.token=r.token WHERE o.owner=? AND r.phase='HELD' ORDER BY r.token", (ident,))]
        if any(r['runtime'] != runtime or r['generation'] != self.generation for r in grants):
            raise AdmissionWait('resource_generation_held')
        instances = [dict(r) for r in self.db.execute("SELECT i.* FROM local_instances i JOIN reservations r ON r.token=i.token WHERE i.owner=? AND r.phase='HELD' ORDER BY i.token", (ident,))]
        references = [dict(r) for r in self.db.execute("SELECT f.* FROM local_references f JOIN reservations r ON r.token=f.token JOIN reservation_owners o ON o.token=r.token WHERE o.owner=? AND r.phase='HELD' ORDER BY f.token", (ident,))]
        models = {i['token']: i for i in instances}
        refs = {r['token']: r['instance'] for r in references}
        held = {r['token']: r for r in grants}
        for item in instances:
            grant = held.get(item['token'])
            profile = self.policy.get('local_inference', {}).get('models', {}).get(item['model'])
            if (not grant or not profile or item['engine'] != profile['engine']
                    or item['artifact'] != profile['artifact_sha256']
                    or json.loads(grant['demand']) != {'kind': 'model_residency', 'provider': None, 'resources': profile['residency']}):
                raise AdmissionWait('resource_model_instance_unknown')
            if not json.loads(grant['scopes']): raise AdmissionWait('resource_scope_unknown')
            # A reference owned by somebody else cannot be discarded by this
            # owner's cleanup, even if both native containers are now stopped.
            all_refs = [r[0] for r in self.db.execute("SELECT f.token FROM local_references f JOIN reservations r ON r.token=f.token WHERE f.instance=? AND r.phase='HELD'", (item['token'],))]
            if any(token not in refs for token in all_refs): raise AdmissionWait('resource_model_references_held')
        for grant in grants:
            demand = json.loads(grant['demand'])
            if demand['provider'] is not None: continue  # Provider uncertainty is never cleared here.
            if demand['kind'] == 'model_residency' and grant['token'] not in models:
                raise AdmissionWait('resource_model_instance_unknown')
            if demand['kind'] in ('local_inference', 'model_loading'):
                instance = models.get(refs.get(grant['token']))
                if not instance: raise AdmissionWait('resource_model_instance_unknown')
                profile = self.policy['local_inference']['models'][instance['model']]
                phase = 'loading' if demand['kind'] == 'model_loading' else 'execution'
                if demand['resources'] != profile.get(phase): raise AdmissionWait('resource_model_profile_unknown')
            elif demand['kind'] not in ('model_residency', 'local_control'):
                raise AdmissionWait('resource_reservation_retained')
            for descriptor in json.loads(grant['scopes']): _scope_path(descriptor)
        snapshot = {'owner': dict(row), 'grants': grants, 'instances': instances, 'references': references}
        fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return snapshot, fingerprint

    def reconcile_router_owner(self, runtime, ident, fingerprint, verify, *, apply=False):
        """Host-only positive physical proof under the unchanged ownership fences.

        The trusted host verifier runs again inside the atomic commit. It must
        perform fresh kernel checks, not Docker RPCs or a cached boolean. This
        private method has no Core, MCP, expiry or automatic-retry capability.
        """
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            snapshot, current = self.router_snapshot(runtime, ident)
            if current != fingerprint: raise AdmissionWait('resource_reservation_retained')
            proof = verify(snapshot)
            if (not isinstance(proof, dict) or proof.get('kind') != 'owned_router_exit'
                    or proof.get('owner') != ident or proof.get('generation') != self.generation
                    or proof.get('providers_stopped') is not True or proof.get('scopes_empty') is not True
                    or proof.get('paired_idle') is not True):
                raise AdmissionWait('resource_scope_unknown')
            clear = [r for r in snapshot['grants'] if json.loads(r['demand'])['provider'] is None]
            operation = str(uuid.uuid4())
            if apply:
                for grant in clear:
                    evidence = {**proof, 'operation': operation}
                    self.db.execute('INSERT INTO resource_reconciliations VALUES(?,?,?,?)',
                        (str(uuid.uuid4()), grant['token'], json.dumps(evidence), time.time()))
                    self.db.execute("UPDATE reservations SET phase='RELEASED',released=? WHERE token=? AND phase='HELD'", (time.time(), grant['token']))
                remaining = self.db.execute("SELECT 1 FROM reservations r JOIN reservation_owners o ON o.token=r.token WHERE o.owner=? AND r.phase='HELD' LIMIT 1", (ident,)).fetchone()
                if not remaining: self.db.execute("UPDATE admission_owners SET phase='RELEASED' WHERE id=?", (ident,))
        return {'owner': ident, 'status': 'APPLIED' if apply else 'READY',
                'local_grants_releasable': len(clear),
                'provider_grants_retained': len(snapshot['grants']) - len(clear), 'automatic_retry': False}

    def _feedback_state(self,provider,account):
        row=self.db.execute('SELECT body FROM provider_feedback WHERE provider=? AND account=?',(provider,account)).fetchone()
        state=json.loads(row[0]) if row else {'revision':0,'blocked':False,'reason':None,'not_before':0,'not_before_mono':0,'boot':None,'mono':None,'wall':None,'denied_scopes':[],'restriction_usage_account_sha256':None,'restriction_generation':None}
        keys={'revision','blocked','reason','not_before','not_before_mono','boot','mono','wall','denied_scopes','restriction_usage_account_sha256','restriction_generation'}
        if (not isinstance(state,dict) or set(state)!=keys or type(state['revision']) is not int or not 0<=state['revision']<MAX_INTEGER
                or type(state['blocked']) is not bool or state['reason'] not in (None,'resource_provider_backoff','resource_provider_feedback_unknown')
                or any(state[k] is not None and (not isinstance(state[k],str) or not re.fullmatch('[a-f0-9]{64}',state[k])) for k in ('restriction_usage_account_sha256','restriction_generation'))
                or state['blocked']!=(state['reason'] is not None)
                or any(type(state[k]) not in (int,float) or not math.isfinite(state[k]) or state[k]<0 for k in ('not_before','not_before_mono'))
                or any(state[k] is not None and (type(state[k]) not in (int,float) or not math.isfinite(state[k])) for k in ('mono','wall'))
                or state['boot'] is not None and (not isinstance(state['boot'],str) or len(state['boot'])>64)
                or type(state['denied_scopes']) is not list or len(state['denied_scopes'])>200
                or any(not isinstance(x,str) or not 0<len(x)<=160 for x in state['denied_scopes'])):
            raise AdmissionWait('resource_provider_feedback_unknown')
        return state

    def _provider_available(self,provider,quota):
        state=self._feedback_state(provider,quota['account_sha256'])
        if state['blocked']:raise AdmissionWait(state['reason'])

    def provider_revision(self,runtime,task,token,provider):
        self.join(runtime,task,token)
        quota=self.policy['providers'].get(provider)
        if not provider_feedback(quota):return 0
        return self._feedback_state(provider,quota['account_sha256'])['revision']

    def observe_provider(self,runtime,task,token,provider,account,observation,*,kind,revision=None,fresh=False,observed_at=None,delivery_receipt=None,authority=None):
        """Charged trusted host only. Feedback never releases a grant or retries."""
        if kind not in ('snapshot','notification','rejection','unknown'):raise ValueError('Invalid provider observation kind')
        if not isinstance(observation,dict) or set(observation)!={'decision','usage_account_sha256','denied_scopes','clear_scopes'} or observation['decision'] not in ('allow','block','unknown','unavailable'):
            raise ValueError('Invalid provider observation')
        for field in ('denied_scopes','clear_scopes'):
            scopes=observation[field]
            if (type(scopes) is not list or len(scopes)>200 or len(set(scopes))!=len(scopes)
                    or any(not isinstance(x,str) or not 0<len(x)<=160 for x in scopes)):
                raise ValueError('Bounded provider restriction scopes required')
        usage=observation['usage_account_sha256']
        if usage is not None and (not isinstance(usage,str) or not re.fullmatch('[a-f0-9]{64}',usage)):raise ValueError('Invalid usage account pin')
        receipt_id=receipt_evidence=None
        if delivery_receipt is not None:
            event=delivery_receipt.get('event') if isinstance(delivery_receipt,dict) else None
            fields={'binding','thread','turn','generation','event'}|({'event_id'} if event in ('live_error','capture_unknown') else set())
            allowed=(kind=='unknown' and observation['decision']=='unknown') if event=='capture_unknown' else (kind=='rejection' and observation['decision'] in (('block','unknown') if event=='rpc_rejection' else ('block',)))
            thread=delivery_receipt.get('thread') if isinstance(delivery_receipt,dict) else None
            turn=delivery_receipt.get('turn') if isinstance(delivery_receipt,dict) else None
            native=(thread is None and turn is None) if event=='capture_unknown' else (isinstance(thread,str) and 0<len(thread)<=512 and (event=='rpc_rejection' and turn is None or isinstance(turn,str) and 0<len(turn)<=512))
            if (not allowed or not isinstance(delivery_receipt,dict) or set(delivery_receipt)!=fields
                    or event not in ('terminal','preack_error','live_error','rpc_rejection','capture_unknown')
                    or delivery_receipt['generation']!=self.generation
                    or not isinstance(delivery_receipt['binding'],str) or not re.fullmatch('[a-f0-9]{64}',delivery_receipt['binding'])
                    or not native):
                raise ValueError('Exact bounded turn or host capture receipt required')
            if event in ('live_error','capture_unknown'):_identifier(delivery_receipt['event_id'])
            _identifier(task);_identifier(token)
            quota=self.policy['providers'].get(provider)
            if not provider_feedback(quota):raise ValueError('Terminal receipts require provider feedback')
            receipt_evidence={'kind':{'terminal':'provider_terminal_restriction','preack_error':'provider_preack_restriction','live_error':'provider_live_restriction','rpc_rejection':'provider_rpc_restriction','capture_unknown':'provider_capture_unknown'}[event],'runtime':str(Path(runtime).absolute()),
                'task':task,'token':token,'generation':self.generation,'binding':delivery_receipt['binding'],
                'provider':provider,'account':quota['account_sha256'],
                'thread_sha256':hashlib.sha256(delivery_receipt['thread'].encode()).hexdigest() if delivery_receipt['thread'] is not None else None,
                'turn_sha256':hashlib.sha256(delivery_receipt['turn'].encode()).hexdigest() if delivery_receipt['turn'] is not None else None}
            if event in ('live_error','capture_unknown'):receipt_evidence['event_id']=delivery_receipt['event_id']
            receipt_id=str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps(receipt_evidence,sort_keys=True,separators=(',',':'))))
        if authority is not None and (receipt_id is None or not isinstance(authority,dict) or set(authority)!={'task','token'}):
            raise ValueError('Only receipted negative facts accept charged probe authority')
        authority_evidence=None
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if receipt_id is not None:
                old=self.db.execute('SELECT token,evidence FROM resource_reconciliations WHERE id=?',(receipt_id,)).fetchone()
                if old:
                    if old['token']!=token or json.loads(old['evidence'])!=receipt_evidence:
                        raise AdmissionWait('resource_provider_feedback_unknown')
                    return False  # Immutable fact already delivered, even after release/recovery.
            if authority is not None:
                permit=self.join(runtime,authority['task'],authority['token'])
                demand=json.loads(permit['demand']);scopes=json.loads(permit['scopes'])
                owner=self.db.execute('SELECT owner FROM reservation_owners WHERE token=?',(authority['token'],)).fetchone()
                actor=self._owner(owner['owner'],runtime) if owner else None
                if (not actor or actor['role']!='standalone' or json.loads(actor['identity'])!=owner_identity()
                        or demand['kind']!='reconciliation' or demand['provider'] is not None or not scopes):
                    raise AdmissionWait('resource_controller_required')
                for descriptor in scopes:_scope_credit(descriptor)
                if event=='capture_unknown':
                    subject=self.db.execute('SELECT 1 FROM reservations WHERE runtime=? AND task=? AND token=? AND generation=?',
                        (str(Path(runtime).absolute()),task,token,self.generation)).fetchone()
                    if not subject:raise AdmissionWait('resource_reservation_missing')
                else:self.join(runtime,task,token,require_owner=False)
                authority_evidence={'kind':'provider_feedback_authority','receipt':receipt_id,
                    'subject_task':task,'subject_token':token,'authority_task':authority['task'],
                    'authority_token':authority['token'],'generation':self.generation}
            else:self.join(runtime,task,token)
            quota=self.policy['providers'].get(provider)
            if not provider_feedback(quota):return
            expected=quota['account_sha256'];state=self._feedback_state(provider,expected)
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            now=time.monotonic();wall=time.time()
            if state['blocked'] and state['boot']!=boot:
                # A prior boot's monotonic value is not an elapsed deadline.
                state['not_before_mono']=now+quota['backoff_seconds']
                state['not_before']=max(state['not_before'],wall+quota['backoff_seconds'])
            decision=observation['decision']
            identity=account==expected and (usage is None or quota.get('usage_account_sha256') is None or usage==quota['usage_account_sha256'])
            if not identity:
                decision='unavailable' if decision=='unavailable' or kind=='notification' and decision=='allow' else 'unknown'
            if kind in ('rejection','unknown') or decision in ('block','unknown'):
                if not state['blocked']:
                    state['restriction_usage_account_sha256']=quota.get('usage_account_sha256')
                    state['restriction_generation']=self.generation
                elif state['restriction_usage_account_sha256']!=quota.get('usage_account_sha256'):
                    state['denied_scopes']=sorted(set(state['denied_scopes'])|{'unclassified'})
                state['denied_scopes']=sorted(set(state.get('denied_scopes',[]))|set(observation['denied_scopes'])|({'provider'} if kind in ('rejection','unknown') or decision=='unknown' else set()))
                if len(state['denied_scopes'])>200:
                    state['denied_scopes']=['unclassified'];decision='unknown'
                state.update(blocked=True,reason='resource_provider_backoff' if decision=='block' else 'resource_provider_feedback_unknown',
                             not_before=max(state['not_before'],wall+quota['backoff_seconds']),
                             not_before_mono=max(state['not_before_mono'],now+quota['backoff_seconds']))
            elif (kind=='snapshot' and decision=='allow' and fresh is True and revision==state['revision']
                    and type(observed_at) in (int,float) and math.isfinite(observed_at)
                    and 0<=now-observed_at<=self.policy['max_age_seconds']
                    and quota.get('usage_account_sha256') is not None and usage==quota['usage_account_sha256']
                    and set(state.get('denied_scopes',[]))<=set(observation['clear_scopes'])
                    and (not state['blocked'] or state['restriction_usage_account_sha256'] is not None
                         and state['restriction_usage_account_sha256']==usage)
                    and (not state['blocked'] or state['boot']==boot and state['mono'] is not None and now>=state['mono']
                         and wall>=state['wall'] and wall>=state['not_before'] and now>=state['not_before_mono'])):
                state.update(blocked=False,reason=None,not_before=0,not_before_mono=0,denied_scopes=[],restriction_usage_account_sha256=None,restriction_generation=None)
            # Every delivered event advances the durable cross-process read epoch.
            state.update(revision=state['revision']+1,boot=boot,mono=now,wall=wall)
            self.db.execute('INSERT INTO provider_feedback VALUES(?,?,?) ON CONFLICT(provider,account) DO UPDATE SET body=excluded.body',
                            (provider,expected,json.dumps(state)))
            if receipt_id is not None:
                self.db.execute('INSERT INTO resource_reconciliations VALUES(?,?,?,?)',
                    (receipt_id,token,json.dumps(receipt_evidence,sort_keys=True),now))
                if authority_evidence is not None:
                    self.db.execute('INSERT INTO resource_reconciliations VALUES(?,?,?,?)',
                        (str(uuid.uuid4()),authority['token'],json.dumps(authority_evidence,sort_keys=True),now))
        return True

    def authorize_provider(self, runtime, task, token, provider, account_sha256):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.join(runtime, task, token)
            demand = json.loads(row['demand'])
            quota = self.policy['providers'].get(provider)
            provider_identity(quota,account_sha256)
            if provider_feedback(quota):self._provider_available(provider,quota)
            if demand['kind'] == 'cloud_api':
                if demand['provider'] != provider: raise AdmissionWait('resource_provider_account_unknown')
                return
            if demand['kind'] != 'local_control': raise AdmissionWait('resource_provider_account_unknown')
            active = sum(json.loads(other['demand'])['provider'] == provider for other in self.held())
            if not provider_feedback(quota) and active + (demand['provider'] != provider) > quota['limit']:
                raise AdmissionWait('resource_provider_quota')
            demand.update(kind='cloud_api', provider=provider)
            self.db.execute('UPDATE reservations SET demand=? WHERE token=?', (json.dumps(demand), token))

    def reconcile_release(self, runtime, task, token, reconciliation_id, evidence):
        """Caller supplies validated durable source proof, never a timeout verdict."""
        _identifier(reconciliation_id)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.join(runtime, task, token, require_owner=False)
            self._model_unused(token)
            self.db.execute('INSERT INTO resource_reconciliations VALUES(?,?,?,?)',
                            (reconciliation_id, token, json.dumps(evidence, sort_keys=True), time.time()))
            self.db.execute("UPDATE reservations SET phase='RELEASED',released=? WHERE token=?", (time.time(), token))

    def join(self, runtime, task, token, *, require_owner=True):
        row = self.db.execute("SELECT * FROM reservations WHERE token=? AND runtime=? AND task=? AND generation=? AND phase='HELD'",
                              (token, str(Path(runtime).absolute()), task, self.generation)).fetchone()
        if not row: raise AdmissionWait('resource_reservation_missing')
        if resource_only(self.policy) and require_owner:
            binding = self.db.execute('SELECT owner FROM reservation_owners WHERE token=?', (token,)).fetchone()
            if not binding: raise AdmissionWait('resource_owner_unknown')
            self._owner(binding['owner'], runtime)
        return dict(row)

    def add_scope(self, runtime, task, token, descriptor):
        self.join(runtime, task, token)
        expected = descriptor.get('expected_cgroup')
        if not expected:
            relative = Path('/proc/self/cgroup').read_text().strip().split('::')[1]
            parent = (Path('/sys/fs/cgroup') / relative.lstrip('/')).parent
            if parent.name != 'agentorchestrator.slice': raise AdmissionWait('resource_scope_unknown')
            expected = str(parent / descriptor['unit'])
            descriptor['expected_cgroup'] = expected
        _scope_path(descriptor)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.join(runtime, task, token)
            scopes = json.loads(row['scopes'])
            target = _scope_credit(descriptor)
            occupied = {_scope_credit(item) for other in self.held() if other['token'] != token
                        for item in json.loads(other['scopes'])}
            if target in occupied: raise AdmissionWait('resource_scope_unknown')
            if target not in {_scope_credit(item) for item in scopes}: scopes.append(descriptor)
            self.db.execute('UPDATE reservations SET scopes=? WHERE token=?', (json.dumps(scopes), token))

    def _model_unused(self, token):
        if self.db.execute("SELECT 1 FROM local_references f JOIN reservations r ON r.token=f.token WHERE f.instance=? AND r.phase='HELD' LIMIT 1", (token,)).fetchone():
            raise AdmissionWait('resource_model_references_held')

    def release_local_engine(self, runtime, owner, engine, *, physical_released):
        """Only the owning host's positive process/physical stop barrier."""
        if physical_released is not True or engine not in ('core', 'ollama'):
            raise AdmissionWait('resource_scope_unknown')
        runtime = str(Path(runtime).absolute())
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._owner(owner, runtime)
            instances = list(self.db.execute("SELECT i.token FROM local_instances i JOIN reservations r ON r.token=i.token WHERE i.owner=? AND i.engine=? AND r.runtime=? AND r.phase='HELD'", (owner, engine, runtime)))
            for instance in instances:
                tokens = [r[0] for r in self.db.execute("SELECT f.token FROM local_references f JOIN reservations r ON r.token=f.token WHERE f.instance=? AND r.phase='HELD'", (instance[0],))] + [instance[0]]
                for token in tokens:
                    self.db.execute('INSERT INTO resource_reconciliations VALUES(?,?,?,?)',
                        (str(uuid.uuid4()), token, json.dumps({'engine': engine, 'owned_physical_stop': True}), time.time()))
                    self.db.execute("UPDATE reservations SET phase='RELEASED',released=? WHERE token=?", (time.time(), token))

    def release(self, runtime, task, token, *, local_released, provider_released):
        if local_released is not True or provider_released is not True: return False
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.join(runtime, task, token)
            self._model_unused(token)
            self.db.execute("UPDATE reservations SET phase='RELEASED',released=? WHERE token=? AND phase='HELD'", (time.time(), token))
        return True


def _scope_path(descriptor):
    unit = descriptor.get('unit')
    path = Path(descriptor.get('expected_cgroup', ''))
    ordinary = (isinstance(unit, str) and re.fullmatch(r'agentorchestrator-[0-9a-f-]{36}\.scope', unit)
                and path.parent.name == 'agentorchestrator.slice')
    container = (isinstance(unit, str) and re.fullmatch(r'docker-[0-9a-f]{64}\.scope', unit)
                 and path.parent == Path('/sys/fs/cgroup/system.slice'))
    if (not (ordinary or container) or not path.is_absolute() or '..' in path.parts or path.name != unit
            or descriptor.get('accounting', 'cpu_ram') not in ('cpu_ram', 'gpu')
            or not path.is_relative_to('/sys/fs/cgroup') or any(p.is_symlink() for p in [path, *path.parents])):
        raise AdmissionWait('resource_scope_unknown')
    return path


def scope_stat(path):
    """Only ENOENT proves absence; denied/invalid kernel metadata is unknown."""
    try:info=Path(path).stat()
    except FileNotFoundError:return None
    except OSError as error:raise AdmissionWait('resource_scope_unknown') from error
    if not stat.S_ISDIR(info.st_mode):raise AdmissionWait('resource_scope_unknown')
    return info


def scope_empty(path):
    """Positive empty/absent scope evidence without exists() error suppression."""
    path=Path(path);before=scope_stat(path)
    if before is None:return True
    try:
        raw=(path/'cgroup.events').read_text()
        if len(raw)>4096:raise AdmissionWait('resource_scope_unknown')
        pairs=[line.split() for line in raw.splitlines()]
        if any(len(pair)!=2 for pair in pairs):raise AdmissionWait('resource_scope_unknown')
        fields=dict(pairs)
        if len(fields)!=len(pairs) or fields.get('populated') not in ('0','1'):
            raise AdmissionWait('resource_scope_unknown')
    except FileNotFoundError as error:
        if scope_stat(path) is None:return True
        raise AdmissionWait('resource_scope_unknown') from error
    except (OSError,ValueError) as error:raise AdmissionWait('resource_scope_unknown') from error
    after=scope_stat(path)
    if after is None:return True
    if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):raise AdmissionWait('resource_scope_unknown')
    return fields['populated']=='0'


def _scope_credit(descriptor):
    return (str(_scope_path(descriptor)), descriptor.get('accounting', 'cpu_ram'))


def _memory_headroom(parent):
    rows = [line.split() for line in Path('/proc/meminfo').read_text().splitlines()
            if line.startswith('MemAvailable:')]
    if (len(rows) != 1 or len(rows[0]) != 3 or rows[0][2] != 'kB'
            or not rows[0][1].isascii() or not rows[0][1].isdigit()):
        raise AdmissionWait('resource_ram_bytes_unknown')
    free = int(rows[0][1]) * 1024
    for group in [parent, *parent.parents]:
        if group == Path('/sys/fs/cgroup') or not group.is_relative_to('/sys/fs/cgroup'): break
        limit = (group / 'memory.max').read_text().strip()
        if limit != 'max':
            free = min(free, max(0, int(limit) - int((group / 'memory.current').read_text())))
    return free


def _owned_usage(rows):
    usage, counters = {}, {}
    for row in rows:
        memory = 0
        seen = set()
        for descriptor in json.loads(row['scopes']):
            if descriptor.get('accounting') == 'gpu': continue
            path = _scope_path(descriptor)
            if str(path) in seen: continue
            seen.add(str(path))
            if scope_stat(path) is None:continue  # Unmaterialized grant stays fully charged.
            memory += int((path / 'memory.current').read_text())
            values = dict(line.split() for line in (path / 'cpu.stat').read_text().splitlines())
            consumed = int(values['usage_usec'])
            if consumed < 0: raise AdmissionWait('resource_usage_unknown')
            counters[str(path)] = consumed
        usage[row['token']] = memory
    return usage, counters


def _sample(rows, now, cpu, cpu_at, ram, before, after, rates):
    usage, containers = {}, {}
    for row in rows:
        paths = {str(_scope_path(descriptor)) for descriptor in json.loads(row['scopes']) if descriptor.get('accounting') != 'gpu'}
        used_cpu = sum(rates.get(path, 0) for path in paths)
        claim = json.loads(row['demand'])['resources']
        containers[row['token']] = {'cpu_millicores': max(0, claim['cpu_millicores'] - used_cpu),
                                    'ram_bytes': max(0, claim['ram_bytes'] - max(before[row['token']], after[row['token']]))}
        usage[row['token']] = {'cpu_millicores': used_cpu, 'ram_bytes': min(before[row['token']], after[row['token']]),
                              'gpu_milliunits': 0, 'vram_bytes': 0}
    return {'at': now, 'cpu_observed_at': cpu_at,
            'available': {'cpu_millicores': cpu, 'ram_bytes': ram, 'gpu_milliunits': None, 'vram_bytes': None},
            'usage': usage, 'containers': containers}


def linux_observation(rows, previous, *, host=False):
    """Paired RAM samples and CPU windows; no GPU or ancestor-quota fallback."""
    now = time.monotonic()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    cpus = sorted(os.sched_getaffinity(0))
    relative = Path('/proc/self/cgroup').read_text().strip().split('::')[1]
    parent = Path('/sys/fs/cgroup') if host else (Path('/sys/fs/cgroup') / relative.lstrip('/')).parent
    if not host and parent.name != 'agentorchestrator.slice': raise AdmissionWait('resource_scope_unknown')
    memory_before = _memory_headroom(parent)
    owned_before, _ = _owned_usage(rows)
    stats = {}
    labels = {'cpu' + str(n) for n in cpus}
    for line in Path('/proc/stat').read_text().splitlines():
        fields = line.split()
        if fields and fields[0] in labels:
            values = [int(n) for n in fields[1:9]]
            if len(values) != 8: raise AdmissionWait('resource_cpu_millicores_unknown')
            stats[fields[0]] = values
    if len(stats) != len(cpus) or not cpus: raise AdmissionWait('resource_cpu_millicores_unknown')
    state = {'at': now, 'boot': boot, 'cpus': cpus, 'stats': stats,
             'scope_cpu': {}, 'ancestor_cpu': {}}
    previous = previous if isinstance(previous, dict) else {}
    dt = now - previous.get('at', now)
    same = previous.get('boot') == boot and previous.get('cpus') == cpus
    comparable = same and .05 <= dt <= 5
    if same and 0 <= dt < .05:
        cpu = previous.get('cpu_available')
        cpu_at = previous.get('cpu_observed_at')
        for group in [parent, *parent.parents]:
            if group == Path('/sys/fs/cgroup') or not group.is_relative_to('/sys/fs/cgroup'): break
            quota, period = (group / 'cpu.max').read_text().split()
            if quota == 'max': continue
            used = previous.get('ancestor_rates', {}).get(str(group))
            if type(used) is not int: cpu = None
            elif cpu is not None: cpu = min(cpu, max(0, int(quota) * 1000 // int(period) - used))
        after, _ = _owned_usage(rows)
        ram = min(memory_before, _memory_headroom(parent))
        return _sample(rows, now, cpu, cpu_at, ram, owned_before, after, previous.get('scope_rates', {})), previous
    cpu = None
    if comparable:
        cpu = 0
        for key, values in stats.items():
            old = previous.get('stats', {}).get(key)
            if not isinstance(old, list) or len(old) != 8: raise AdmissionWait('resource_cpu_millicores_unknown')
            deltas = [a - b for a, b in zip(values, old)]
            if min(deltas) < 0 or sum(deltas) <= 0: raise AdmissionWait('resource_cpu_millicores_unknown')
            cpu += deltas[3] * 1000 // sum(deltas)
    state['ancestor_rates'] = {}
    for group in [parent, *parent.parents]:
        if group == Path('/sys/fs/cgroup') or not group.is_relative_to('/sys/fs/cgroup'): break
        quota, period = (group / 'cpu.max').read_text().split()
        if quota == 'max': continue
        quota, period = int(quota), int(period)
        if quota <= 0 or period <= 0: raise AdmissionWait('resource_cpu_millicores_unknown')
        counts = dict(line.split() for line in (group / 'cpu.stat').read_text().splitlines())
        consumed = int(counts['usage_usec'])
        if consumed < 0: raise AdmissionWait('resource_cpu_millicores_unknown')
        state['ancestor_cpu'][str(group)] = consumed
        old = previous.get('ancestor_cpu', {}).get(str(group))
        if not comparable or type(old) is not int:
            cpu = None
        elif consumed < old:
            raise AdmissionWait('resource_cpu_millicores_unknown')
        elif cpu is not None:
            used = math.ceil((consumed - old) / (dt * 1000))
            state['ancestor_rates'][str(group)] = used
            remaining = max(0, quota * 1000 // period - used)
            cpu = min(cpu, remaining)
    owned_after, scope_cpu = _owned_usage(rows)
    memory_after = _memory_headroom(parent)
    state['scope_cpu'] = scope_cpu
    rates = {}
    for path, consumed in scope_cpu.items():
        old = previous.get('scope_cpu', {}).get(path)
        if comparable and type(old) is int:
            if consumed < old: raise AdmissionWait('resource_usage_unknown')
            rates[path] = int((consumed - old) / (dt * 1000))
    state.update(scope_rates=rates, cpu_available=cpu, cpu_observed_at=now if cpu is not None else None)
    return _sample(rows, now, cpu, state['cpu_observed_at'], min(memory_before, memory_after),
                   owned_before, owned_after, rates), state
