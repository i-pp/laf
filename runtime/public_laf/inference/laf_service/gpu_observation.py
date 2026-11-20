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


# Added 2026-11-15: pinned host GPU evidence for local resource admission.
# Modified 2026-11-16: canonical KFD class and coherent device-node metadata.
# Modified 2026-11-16: retain procfs enumeration descriptors during bounded scans.

"""Read-only pinned AMD device evidence; no discovery fallback or GPU control."""
from itertools import islice
import os
from pathlib import Path
import re
import stat
import time

from .resource_admission_core import AdmissionWait, _scope_path, linux_observation, scope_stat

PCI = Path('/sys/bus/pci/devices')
PROC = Path('/proc')
DRM = Path('/sys/class/drm')
KFD = Path('/sys/class/kfd/kfd')
KFD_DEVICE = Path('/dev/kfd')


def _uint(text):
    if not isinstance(text,str) or not re.fullmatch(r'[0-9]+',text.strip()):
        raise AdmissionWait('resource_usage_unknown')
    number=int(text.strip())
    if number>2**63-1:raise AdmissionWait('resource_usage_unknown')
    return number


def _bounded(entries, maximum):
    result=list(islice(entries,maximum+1))
    if len(result)>maximum:raise AdmissionWait('resource_usage_unknown')
    return result


def device_sample(device):
    """Topology is approved evidence, never inferred from a VRAM sysfs file."""
    if device['valid_until']<=time.time():raise AdmissionWait('resource_gpu_topology_unknown')
    return release_sample(device)


def release_sample(device):
    """Pinned fresh kernel identity for release only; no admission/expiry credit."""
    path=PCI/device['pci_bdf']
    if (not path.exists() or (path/'vendor').read_text().strip()!=device['vendor']
            or (path/'device').read_text().strip()!=device['device']
            or (path/'driver').resolve().name!='amdgpu'):
        raise AdmissionWait('resource_gpu_topology_unknown')
    total=_uint((path/'mem_info_vram_total').read_text())
    used=_uint((path/'mem_info_vram_used').read_text())
    busy=_uint((path/'gpu_busy_percent').read_text())
    if not total or used>total or busy>100:raise AdmissionWait('resource_gpu_milliunits_unknown')
    return {'free':total-used,'total':total,'used':used,'idle':busy==0,'device':device['pci_bdf'],
            'topology':device['memory_topology']}


def _bytes(value):
    fields=value.split()
    if len(fields) not in (1,2) or len(fields)==2 and fields[1] not in ('KiB','MiB'):
        raise AdmissionWait('resource_usage_unknown')
    return _uint(fields[0])*({'KiB':1024,'MiB':1024**2}[fields[1]] if len(fields)==2 else 1)


def client_vram(raw, device, *, private=True):
    """Credit private resident bytes only; duplicate FD/client views use minimum."""
    if len(raw)>65536:raise AdmissionWait('resource_usage_unknown')
    fields={}
    for line in raw.splitlines():
        if ':' not in line:continue
        key,value=line.split(':',1)
        if key.startswith('drm-'):
            if key in fields:raise AdmissionWait('resource_usage_unknown')
            fields[key]=value.strip()
    if not fields:return None  # A normal non-DRM descriptor.
    pdev=fields.get('drm-pdev')
    if ('drm-driver' not in fields or not fields['drm-driver'] or not isinstance(pdev,str)
            or not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]',pdev)):
        raise AdmissionWait('resource_usage_unknown')
    if pdev!=device['pci_bdf']:return None
    if fields['drm-driver']!='amdgpu':raise AdmissionWait('resource_usage_unknown')
    ident=_uint(fields.get('drm-client-id'))
    resident=fields.get('drm-resident-vram',fields.get('drm-memory-vram'))
    shared=fields.get('drm-shared-vram')
    if resident is None or shared is None:raise AdmissionWait('resource_usage_unknown')
    # Shared BOs can be counted by several clients. No shared bytes are credited
    # as unique materialization; their outstanding promises stay charged.
    return ident,max(0,_bytes(resident)-_bytes(shared)) if private else _bytes(resident)


def _pid_snapshot(pid, scope):
    path=PROC/str(pid)
    stat=(path/'stat').read_text().rsplit(')',1)[1].split()
    status=dict(line.split(':',1) for line in (path/'status').read_text().splitlines() if ':' in line)
    if set(status.get('Uid','').split())!={str(os.getuid())}:raise AdmissionWait('resource_usage_unknown')
    relative=(path/'cgroup').read_text().strip().split('::')[1]
    if Path('/sys/fs/cgroup')/relative.lstrip('/') != scope:raise AdmissionWait('resource_usage_unknown')
    return stat[19]


def _device_nodes(device, check):
    """Kernel character-device identities only; never open a DRM/KFD node."""
    check();target=(PCI/device['pci_bdf']).resolve(strict=True);nodes=set()
    for path in _bounded(DRM.iterdir(),128):
        check()
        if not re.fullmatch(r'(?:card|renderD)[0-9]+',path.name):continue
        if (path/'device').resolve(strict=True)!=target:continue
        fields=(path/'dev').read_text().strip().split(':')
        if len(fields)!=2:raise AdmissionWait('resource_gpu_topology_unknown')
        number=os.makedev(*(_uint(field) for field in fields))
        if number in nodes:raise AdmissionWait('resource_gpu_topology_unknown')
        nodes.add(number)
    if not nodes:raise AdmissionWait('resource_gpu_topology_unknown')
    kfd=None
    try:kfd_info=KFD.stat()
    except FileNotFoundError:kfd_info=None
    if kfd_info is not None:
        if not stat.S_ISDIR(kfd_info.st_mode):raise AdmissionWait('resource_gpu_topology_unknown')
        fields=(KFD/'dev').read_text().strip().split(':')
        if len(fields)!=2:raise AdmissionWait('resource_gpu_topology_unknown')
        kfd=os.makedev(*(_uint(field) for field in fields))
        if kfd in nodes:raise AdmissionWait('resource_gpu_topology_unknown')
    try:device_info=KFD_DEVICE.stat()
    except FileNotFoundError:device_info=None
    if (kfd_info is None)!=(device_info is None):
        raise AdmissionWait('resource_gpu_topology_unknown')
    if device_info is not None:
        if (not stat.S_ISCHR(device_info.st_mode) or kfd is None
                or device_info.st_rdev!=kfd):
            raise AdmissionWait('resource_gpu_topology_unknown')
    check();return nodes,kfd


def _fd(entry):
    info=(entry.parent.parent/'fd'/entry.name).stat()
    return info.st_mode,info.st_dev,info.st_ino,info.st_rdev


def scope_clients(scope, device, *, private=True, deadline=None, budget=None):
    scope_identity=scope_stat(scope)
    if scope_identity is None:return {}
    deadline=time.monotonic()+1 if deadline is None else deadline
    budget=[16384] if budget is None else budget
    def bounded_work():
        budget[0]-=1
        if budget[0]<0 or time.monotonic()>deadline:raise AdmissionWait('resource_observation_stale')
    # The fixed supported Docker/systemd scopes are flat. Unknown delegated
    # descendants require a separately reviewed accounting profile.
    if any(p.is_dir() for p in _bounded(scope.iterdir(),128)):
        raise AdmissionWait('resource_scope_unknown')
    def pids():
        raw=(scope/'cgroup.procs').read_text()
        if len(raw)>65536:raise AdmissionWait('resource_usage_unknown')
        result=sorted({_uint(n) for n in raw.splitlines()})
        if len(result)>4096:raise AdmissionWait('resource_usage_unknown')
        return result
    nodes,kfd=_device_nodes(device,bounded_work)
    before=pids();result={}
    for pid in before:
        bounded_work()
        start=_pid_snapshot(pid,scope)
        # Keep the enumeration FD alive while inspecting each entry. Eager
        # Path.iterdir() includes this reader's directory FD, then closes it
        # before _fd() can inspect it when this process belongs to the scope.
        with os.scandir(PROC/str(pid)/'fdinfo') as entries:
            for index,item in enumerate(entries):
                if index>=4096:raise AdmissionWait('resource_usage_unknown')
                entry=Path(item.path)
                bounded_work()
                first_fd=_fd(entry)
                raw=entry.read_text()
                if _fd(entry)!=first_fd:raise AdmissionWait('resource_usage_unknown')
                character=stat.S_ISCHR(first_fd[0])
                # KFD can allocate on the pinned GPU without per-device DRM fdinfo.
                # No positive attribution/release contract is implemented for KFD.
                if character and first_fd[3]==kfd:raise AdmissionWait('resource_gpu_ownership_unknown')
                record=client_vram(raw,device,private=private)
                matched=character and first_fd[3] in nodes
                if matched and record is None:raise AdmissionWait('resource_gpu_counters_unknown')
                if record is not None and not matched:raise AdmissionWait('resource_usage_unknown')
                if record is not None:
                    client,used=record
                    result[client]=(min if private else max)(result.get(client,used),used)
        if _pid_snapshot(pid,scope)!=start:raise AdmissionWait('resource_usage_unknown')
    if before!=pids():raise AdmissionWait('resource_usage_unknown')
    if _device_nodes(device,bounded_work)!=(nodes,kfd):raise AdmissionWait('resource_gpu_topology_unknown')
    current=scope_stat(scope)
    if current is None or (current.st_dev,current.st_ino)!=(scope_identity.st_dev,scope_identity.st_ino):
        raise AdmissionWait('resource_scope_unknown')
    bounded_work()
    return result


def owned_vram(rows,device, *, deadline=None):
    result={};owners={};budget=[16384]
    for row in rows:
        clients={}
        import json
        for descriptor in json.loads(row['scopes']):
            if descriptor.get('accounting')!='gpu':continue
            for client,used in scope_clients(_scope_path(descriptor),device,deadline=deadline,budget=budget).items():
                if client in owners and owners[client]!=row['token']:raise AdmissionWait('resource_usage_unknown')
                owners[client]=row['token'];clients[client]=min(clients.get(client,used),used)
        result[row['token']]=sum(clients.values())
    return result


class HostObservation:
    def __init__(self, policy):
        self.device=policy['local_inference']['device'];self.max_age=policy['max_age_seconds']

    def __call__(self, rows, previous):
        try:
            began=time.monotonic()
            first=device_sample(self.device);owned_before=owned_vram(rows,self.device,deadline=began+self.max_age)
            sample,state=linux_observation(rows,previous,host=True)
            owned_after=owned_vram(rows,self.device,deadline=began+self.max_age);last=device_sample(self.device)
            if first['device']!=last['device'] or first['topology']!=last['topology']:
                raise AdmissionWait('resource_gpu_topology_unknown')
            sample.update(at=began,gpu_device=last['device'],gpu_memory_topology=last['topology'])
            sample['available']['gpu_milliunits']=1000 if first['idle'] and last['idle'] else 0
            sample['available']['vram_bytes']=min(first['free'],last['free'])
            # Discrete topology is not established by these counters. Until a
            # kernel/attestation-backed discrete path exists, couple all device
            # memory to RAM even if the operator classified it as discrete.
            sample['available']['vram_bytes']=min(sample['available']['vram_bytes'],sample['available']['ram_bytes'])
            for row in rows:
                sample['usage'][row['token']]['vram_bytes']=min(owned_before[row['token']],owned_after[row['token']])
            # Age begins before the paired GPU/CPU/RAM collection, never at its end.
            return sample,state
        except AdmissionWait:raise
        except (OSError,ValueError,KeyError,TypeError,IndexError,OverflowError) as exc:
            raise AdmissionWait('resource_observation_unknown') from exc
