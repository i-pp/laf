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


# Added 2026-11-15: bounded non-admitting NPU calibration observations.

"""Read pinned sysfs/fdinfo only. Never open a device, SDK, model or provider.

DRM fields: https://www.kernel.org/doc/html/latest/accel/amdxdna/amdnpu.html
Owned client counters cannot prove global device ownership or physical release.
This module always reports admission_eligible=False, including empty/quiet data.
"""
from itertools import islice
import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from inference.laf_service.resource_admission_core import AdmissionWait, _scope_path

PCI=Path('/sys/bus/pci/devices')
PROC=Path('/proc')
CGROUP=Path('/sys/fs/cgroup')
ACCEL=Path('/sys/class/accel')
MAXIMUM=2**63-1


def policy(value):
    if (not isinstance(value,dict) or set(value)!={'schema','device','valid_until','evidence','max_age_seconds','controller'}
            or type(value['schema']) is not int or value['schema']!=1):raise ValueError('Exact NPU evidence policy required')
    device=value['device']
    if (not isinstance(device,dict) or set(device)!={'pci_bdf','vendor','device','driver','firmware'}
            or not isinstance(device['pci_bdf'],str) or not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]',device['pci_bdf'])
            or any(not isinstance(device[key],str) or not re.fullmatch(r'0x[0-9a-f]{4}',device[key]) for key in ('vendor','device'))
            or device['driver']!='amdxdna' or not isinstance(device['firmware'],str)
            or not re.fullmatch(r'[0-9]{1,10}(?:\.[0-9]{1,10}){3}',device['firmware'])):
        raise ValueError('Pinned NPU PCI/driver/firmware required')
    for key in ('valid_until','max_age_seconds'):
        if type(value[key]) not in (int,float) or not math.isfinite(value[key]) or value[key]<=0:
            raise ValueError('Finite NPU evidence timing required')
    if not .1<=value['max_age_seconds']<=5:raise ValueError('Bounded NPU observation age required')
    if not isinstance(value['evidence'],str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value['evidence']):
        raise ValueError('NPU evidence reference required')
    cap=value['controller']
    if (not isinstance(cap,dict) or set(cap)!={'cpu_millicores','ram_bytes','pids'}
            or any(type(n) is not int or not 0<n<=MAXIMUM for n in cap.values())
            or cap['ram_bytes']<32*1024**2 or not 8<=cap['pids']<=4096):
        raise ValueError('Explicit bounded observation controller required')
    return json.loads(json.dumps(value))


def _read(path,maximum=65536):
    with Path(path).open('r') as stream:raw=stream.read(maximum+1)
    if len(raw)>maximum:raise AdmissionWait('resource_observation_unknown')
    return raw


def _uint(raw):
    if not isinstance(raw,str) or not re.fullmatch('[0-9]+',raw.strip()):raise AdmissionWait('resource_usage_unknown')
    value=int(raw.strip())
    if value>MAXIMUM:raise AdmissionWait('resource_usage_unknown')
    return value


def _bytes(raw):
    if not isinstance(raw,str):raise AdmissionWait('resource_usage_unknown')
    fields=raw.split()
    if len(fields) not in (1,2) or len(fields)==2 and fields[1] not in ('KiB','MiB'):
        raise AdmissionWait('resource_usage_unknown')
    result=_uint(fields[0])*({'KiB':1024,'MiB':1024**2}[fields[1]] if len(fields)==2 else 1)
    if result>MAXIMUM:raise AdmissionWait('resource_usage_unknown')
    return result


def client(raw,device):
    if not isinstance(raw,str) or len(raw)>65536:raise AdmissionWait('resource_usage_unknown')
    fields={}
    for line in raw.splitlines():
        if ':' not in line:continue
        key,value=line.split(':',1)
        if key.startswith(('drm-','amdxdna_accel_driver-')):
            if key in fields:raise AdmissionWait('resource_usage_unknown')
            fields[key]=value.strip()
    if not fields:return None
    pdev=fields.get('drm-pdev')
    if (not isinstance(pdev,str) or not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]',pdev)
            or not fields.get('drm-driver')):raise AdmissionWait('resource_usage_unknown')
    if pdev!=device['pci_bdf']:return None
    if fields['drm-driver']!='amdxdna_accel_driver':raise AdmissionWait('resource_usage_unknown')
    engine=fields.get('drm-engine-npu','').split()
    if len(engine)!=2 or engine[1]!='ns':raise AdmissionWait('resource_usage_unknown')
    total,shared=_bytes(fields.get('drm-total-memory')),_bytes(fields.get('drm-shared-memory'))
    if shared>total:raise AdmissionWait('resource_usage_unknown')
    return _uint(fields.get('drm-client-id')),{'engine_ns':_uint(engine[0]),'allocated_bytes':total,'private_lower_bytes':total-shared}


def _valid(value):
    now=time.time()
    if type(now) not in (int,float) or not math.isfinite(now) or now<=0 or value['valid_until']<=now:
        raise AdmissionWait('resource_npu_device_unknown')


def _device(value):
    _valid(value)
    device=value['device'];path=PCI/device['pci_bdf']
    if (not path.exists() or _read(path/'vendor').strip()!=device['vendor']
            or _read(path/'device').strip()!=device['device'] or (path/'driver').resolve().name!=device['driver']
            or _read(path/'fw_version').strip()!=device['firmware']):raise AdmissionWait('resource_npu_device_unknown')


def _nodes(device):
    """Resolve kernel accelerator numbers, without opening a device node."""
    entries=list(islice(ACCEL.iterdir(),65))
    if len(entries)>64:raise AdmissionWait('resource_npu_device_unknown')
    result=set();target=(PCI/device['pci_bdf']).resolve(strict=True)
    for path in entries:
        if not re.fullmatch(r'accel[0-9]+',path.name):continue
        if (path/'device').resolve(strict=True)!=target:continue
        fields=_read(path/'dev').strip().split(':')
        if len(fields)!=2:raise AdmissionWait('resource_npu_device_unknown')
        number=os.makedev(*(_uint(field) for field in fields))
        if number in result:raise AdmissionWait('resource_npu_device_unknown')
        result.add(number)
    if not result:raise AdmissionWait('resource_npu_device_unknown')
    return result


def _fd(path):
    info=(path.parent.parent/'fd'/path.name).stat()
    return info.st_mode,info.st_dev,info.st_ino,info.st_rdev


def _group(raw):
    path=Path(raw)
    if (not path.is_absolute() or str(path)!=raw or '..' in path.parts or path==CGROUP
            or not path.is_relative_to(CGROUP) or any(parent.is_symlink() for parent in (path,*path.parents))):
        raise AdmissionWait('resource_scope_unknown')
    return path


def reader_scope(cap):
    relative=_read(PROC/'self/cgroup').strip().split('::')[1]
    group=CGROUP/relative.lstrip('/')
    _scope_path({'unit':group.name,'expected_cgroup':str(group)})
    # Docker/user service scopes are not accepted as this calibration CLI's
    # private controller; the observed target may be an existing flat service.
    if group.parent.name!='agentorchestrator.slice':raise AdmissionWait('resource_controller_required')
    quota,period=_read(group/'cpu.max').split();memory=_read(group/'memory.max').strip();pids=_read(group/'pids.max').strip()
    if (quota=='max' or memory=='max' or pids=='max' or _uint(period)==0 or _uint(quota)==0
            or (_uint(quota)*1000+_uint(period)-1)//_uint(period)>cap['cpu_millicores']
            or not 0<_uint(memory)<=cap['ram_bytes'] or not 0<_uint(pids)<=cap['pids']
            or _read(group/'memory.swap.max').strip()!='0'):
        raise AdmissionWait('resource_controller_required')


def _scan(group,device,deadline,nodes=None):
    if nodes is None:nodes=_nodes(device)
    budget=16384
    def step():
        nonlocal budget
        budget-=1
        if budget<0 or time.monotonic()>deadline:raise AdmissionWait('resource_observation_stale')
    entries=list(islice(group.iterdir(),129))
    if len(entries)>128 or any(path.is_dir() for path in entries):raise AdmissionWait('resource_scope_unknown')
    def pids():
        result=sorted({_uint(n) for n in _read(group/'cgroup.procs').splitlines()})
        if len(result)>4096 or any(n==0 for n in result):raise AdmissionWait('resource_usage_unknown')
        return result
    def identity(pid):
        path=PROC/str(pid);fields=_read(path/'stat').rsplit(')',1)[1].split()
        status=dict(line.split(':',1) for line in _read(path/'status').splitlines() if ':' in line)
        if (set(status.get('Uid','').split())!={str(os.getuid())}
                or CGROUP/_read(path/'cgroup').strip().split('::')[1].lstrip('/')!=group):
            raise AdmissionWait('resource_usage_unknown')
        return fields[19]
    before=pids();clients={}
    for pid in before:
        step();start=identity(pid)
        directory=PROC/str(pid)/'fdinfo'
        for index,path in enumerate(directory.iterdir()):
            step()
            if index>=4096:raise AdmissionWait('resource_usage_unknown')
            before_fd=_fd(path);record=client(_read(path),device)
            if _fd(path)!=before_fd:raise AdmissionWait('resource_usage_unknown')
            is_npu=stat.S_ISCHR(before_fd[0]) and before_fd[3] in nodes
            if is_npu and record is None:raise AdmissionWait('resource_npu_counters_unknown')
            if record is not None and not is_npu:raise AdmissionWait('resource_usage_unknown')
            if record is None:continue
            ident,info=record
            old=clients.get(ident)
            clients[ident]={'engine_low':min(old['engine_low'],info['engine_ns']) if old else info['engine_ns'],
                'engine_high':max(old['engine_high'],info['engine_ns']) if old else info['engine_ns'],
                'allocated_upper':max(old['allocated_upper'],info['allocated_bytes']) if old else info['allocated_bytes']}
        if identity(pid)!=start:raise AdmissionWait('resource_usage_unknown')
    if pids()!=before:raise AdmissionWait('resource_usage_unknown')
    return clients


def capture(value,scope, *, sleep=time.sleep):
    value=policy(value);began=time.monotonic();deadline=began+value['max_age_seconds']
    try:
        group=_group(scope);namespace=os.readlink(PROC/'self/ns/pid')
        _device(value);nodes=_nodes(value['device']);first=_scan(group,value['device'],deadline,nodes)
        sleep(.06)
        last=_scan(group,value['device'],deadline,nodes);_device(value)
        if _nodes(value['device'])!=nodes:raise AdmissionWait('resource_npu_device_unknown')
        current_namespace=os.readlink(PROC/'self/ns/pid')
        _valid(value)  # Sysfs/namespace reads may cross the evidence expiry.
        elapsed=time.monotonic()-began
        if (not .05<=elapsed<=value['max_age_seconds'] or current_namespace!=namespace
                or set(first)!=set(last) or any(last[key]['engine_low']<first[key]['engine_high'] for key in first)):
            raise AdmissionWait('resource_observation_stale')
        allocated=sum(item['allocated_upper'] for item in last.values())
        engine=sum(last[key]['engine_high']-first[key]['engine_low'] for key in first)
        if allocated>MAXIMUM or engine>MAXIMUM:raise AdmissionWait('resource_usage_unknown')
        return {'schema':1,'scope':'owned_npu_calibration_only','device':value['device']['pci_bdf'],
            'firmware':value['device']['firmware'],'observed_seconds':elapsed,'owned_client_count':len(last),
            'owned_allocated_bytes_upper':allocated,
            'owned_engine_ns_delta_upper':engine,
            'global_idle_verified':False,'admission_eligible':False,'wait_reason':'resource_npu_global_ownership_unknown'}
    except AdmissionWait:raise
    except (OSError,ValueError,KeyError,TypeError,IndexError,OverflowError) as error:raise AdmissionWait('resource_observation_unknown') from error


def _load(path):
    path=Path(path)
    if any(parent.is_symlink() for parent in (path,*path.parents)):raise ValueError('Private NPU evidence policy required')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd) as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600
                or info.st_nlink!=1 or info.st_size>16384):raise ValueError('Private bounded NPU evidence policy required')
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError('Duplicate NPU evidence policy field')
                result[key]=value
            return result
        raw=stream.read(16385)
        if len(raw)>16384:raise ValueError('Bounded NPU evidence policy required')
        return policy(json.loads(raw,object_pairs_hook=unique))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy',required=True);parser.add_argument('--scope',required=True)
    args=parser.parse_args(argv)
    try:
        value=_load(args.policy);reader_scope(value['controller'])
        print(json.dumps(capture(value,args.scope),sort_keys=True));return 0
    except (OSError,ValueError,KeyError,TypeError,IndexError,AdmissionWait) as error:
        print(json.dumps({'error':getattr(error,'code','resource_npu_observation_refused'),'admission_eligible':False}));return 1


if __name__=='__main__':raise SystemExit(main())
