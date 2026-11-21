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


# Added 2026-11-15: opt-in shared Router host resource ownership.

"""Host-only bridge from fixed Router lifecycle operations to the common ledger."""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

from .resource_admission_core import (AdmissionWait, Ledger, _scope_path, generation,
    policy as validate_policy, resource_only, scope_empty)
from .gpu_observation import HostObservation, device_sample, scope_clients


def _id(value):
    if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError('Canonical resource identity required')
    return value


def container_scope(info):
    """Called only after the existing fixed Docker owner/security validation."""
    cid=info.get('Id');pid=info.get('State',{}).get('Pid')
    if (not isinstance(cid,str) or not re.fullmatch(r'[0-9a-f]{64}',cid)
            or info['State'].get('Running') is not True or type(pid) is not int or pid<=0):
        raise AdmissionWait('resource_scope_unknown')
    first=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]
    relative=Path('/proc/'+str(pid)+'/cgroup').read_text().strip().split('::')[1]
    group=Path('/sys/fs/cgroup')/relative.lstrip('/')
    unit='docker-'+cid+'.scope'
    descriptor={'unit':unit,'expected_cgroup':str(group)}
    if group.name!=unit or Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]!=first:
        raise AdmissionWait('resource_scope_unknown')
    _scope_path(descriptor)
    return descriptor


class LocalAdmission:
    def __init__(self, value, runtime, core, provider, *, observe=None):
        self.policy=validate_policy(value)
        if not resource_only(self.policy) or 'local_inference' not in self.policy:
            raise ValueError('Complete resource-only local inference policy required')
        self.generation=generation(self.policy)
        self.runtime=Path(runtime).absolute();self.core,self.provider=core,provider
        self.observe=observe or HostObservation(self.policy)
        self.profiles=self.policy['local_inference'];self.controllers={}
        self._verify_profiles()
        descriptor=self._host_scope()
        with closing(Ledger(self.policy)) as ledger:self.owner=ledger.register_owner(self.runtime,'router')
        try:
            self._controller('host',descriptor)
            self.prepare_core()
        except BaseException:
            # A successful partial controller grant remains charged. An owner
            # with no grant can be closed by this same known process.
            with closing(Ledger(self.policy)) as ledger:ledger.close_owner(self.owner)
            raise

    def _verify_profiles(self):
        limits=self.core.policy
        expected={'cpu_millicores':math.ceil(limits['nano_cpus']/10**6),'ram_bytes':limits['memory'],
                  'gpu_milliunits':0,'vram_bytes':0}
        if self.profiles['controllers']['core']!=expected:raise ValueError('Core controller ceiling mismatch')
        limits=self.provider.backend.provider
        expected.update(cpu_millicores=limits['cpus']*1000,ram_bytes=limits['memory_bytes'])
        if self.profiles['controllers']['ollama']!=expected:raise ValueError('Ollama controller ceiling mismatch')

    def _host_scope(self):
        relative=Path('/proc/self/cgroup').read_text().strip().split('::')[1]
        group=Path('/sys/fs/cgroup')/relative.lstrip('/')
        descriptor={'unit':group.name,'expected_cgroup':str(group)}
        _scope_path(descriptor)
        quota,period=(group/'cpu.max').read_text().split()
        memory=(group/'memory.max').read_text().strip()
        cap=self.profiles['controllers']['host']
        pids=(group/'pids.max').read_text().strip()
        if (quota=='max' or memory=='max' or int(quota)<=0 or int(period)<=0
                or math.ceil(int(quota)*1000/int(period))>cap['cpu_millicores'] or int(memory)>cap['ram_bytes']
                or int(memory)<=0 or pids=='max' or not 0<int(pids)<=4096
                or (group/'memory.swap.max').read_text().strip()!='0'):
            raise AdmissionWait('resource_controller_required')
        return descriptor

    def _controller(self, engine, descriptor=None):
        old=self.controllers.get(engine)
        with closing(Ledger(self.policy)) as ledger:
            if old:
                ledger.join(self.runtime,old['task'],old['token'])
                return old
            task,token=str(uuid.uuid4()),str(uuid.uuid4())
            deadline=time.monotonic()+1
            for attempt in range(17):
                try:
                    ledger.reserve(self.runtime,task,token,
                        {'kind':'local_control','provider':None,'resources':self.profiles['controllers'][engine]},
                        self.observe,owner=self.owner,scopes=[descriptor] if descriptor else ())
                    break
                except AdmissionWait as error:
                    if error.code!='resource_cpu_millicores_unknown' or attempt==16 or time.monotonic()>=deadline:
                        raise
                    # No provider has started. Keep the saved baseline and the
                    # same uncommitted identity for a bounded CPU window.
                    time.sleep(.06)
        self.controllers[engine]={'task':task,'token':token}
        return self.controllers[engine]

    def prepare_core(self):self._controller('core')

    def _engine_scope(self, engine):
        if engine=='core':
            found=self.core.inspect()
            if not found:raise AdmissionWait('resource_scope_unknown')
            info=found[1]  # inspect already validates immutable owner and limits.
        else:
            info=self.provider.backend.inspect_owned()
            if not info:raise AdmissionWait('resource_scope_unknown')
        return container_scope(info)

    def ready_core(self):
        controller=self.controllers.get('core')
        if not controller:raise AdmissionWait('resource_controller_required')
        descriptor=self._engine_scope('core')
        with closing(Ledger(self.policy)) as ledger:
            ledger.add_scope(self.runtime,controller['task'],controller['token'],descriptor)

    def before_ensure(self):
        self.ready_core();self._controller('ollama')

    def after_ensure(self):
        controller=self.controllers.get('ollama')
        if not controller:raise AdmissionWait('resource_controller_required')
        with closing(Ledger(self.policy)) as ledger:
            ledger.add_scope(self.runtime,controller['task'],controller['token'],self._engine_scope('ollama'))

    def model_reserve(self, model, artifact, request_id):
        _id(request_id);self.ready_core()
        profile=self.profiles['models'].get(model)
        if not profile or profile['artifact_sha256']!=artifact:raise AdmissionWait('resource_model_profile_unknown')
        descriptor=self._engine_scope(profile['engine']);descriptor['accounting']='gpu'
        token=str(uuid.uuid4())
        with closing(Ledger(self.policy)) as ledger:
            ledger.reserve(self.runtime,request_id,token,
                {'kind':'model_residency','provider':None,'resources':profile['residency']},self.observe,
                owner=self.owner,scopes=[descriptor],local_model=model)
        return {'instance':token}

    def loading_reserve(self, model, instance, request_id):
        return self._execution_reserve(model,instance,request_id,loading=True)

    def execution_reserve(self, model, instance, request_id):
        return self._execution_reserve(model,instance,request_id,loading=False)

    def _execution_reserve(self, model, instance, request_id, *, loading):
        _id(instance);_id(request_id);self.ready_core()
        profile=self.profiles['models'].get(model)
        phase='loading' if loading else 'execution'
        if not profile or phase not in profile:raise AdmissionWait('resource_model_profile_unknown')
        token=str(uuid.uuid4())
        with closing(Ledger(self.policy)) as ledger:
            ledger.reserve(self.runtime,request_id,token,
                {'kind':'model_loading' if loading else 'local_inference','provider':None,'resources':profile[phase]},self.observe,
                owner=self.owner,local_model=model,local_instance=instance)
        return {'token':token}

    def _instance(self, ledger, instance):
        _id(instance)
        item=ledger.db.execute('SELECT i.*,r.task FROM local_instances i JOIN reservations r ON r.token=i.token WHERE i.token=? AND i.owner=? AND r.runtime=? AND r.phase=\'HELD\'',
                               (instance,self.owner,str(self.runtime))).fetchone()
        if not item:raise AdmissionWait('resource_model_instance_unknown')
        return dict(item)

    def _idle_upper(self, row):
        device=self.profiles['device'];first=device_sample(device)
        scopes=[_scope_path(d) for d in json.loads(row['scopes']) if d.get('accounting')=='gpu']
        if not scopes:raise AdmissionWait('resource_scope_unknown')
        # Resident total is an upper bound, including shared buffers. It is
        # deliberately different from private-byte credit during admission.
        clients={}
        for scope in scopes:
            for client,used in scope_clients(scope,device,private=False).items():
                clients[client]=max(clients.get(client,used),used)
        last=device_sample(device)
        if not first['idle'] or not last['idle']:raise AdmissionWait('resource_gpu_milliunits_headroom')
        return sum(clients.values())

    def loading_finish(self, instance, request_id, token):
        return self._execution_finish(instance,request_id,token,loading=True)

    def execution_finish(self, instance, request_id, token):
        return self._execution_finish(instance,request_id,token,loading=False)

    def _execution_finish(self, instance, request_id, token, *, loading):
        _id(request_id);_id(token);self.ready_core()
        with closing(Ledger(self.policy)) as ledger:
            model=self._instance(ledger,instance)
            grant=ledger.join(self.runtime,request_id,token)
            if json.loads(grant['demand'])['kind']!=('model_loading' if loading else 'local_inference'):
                raise AdmissionWait('resource_reservation_retained')
            reference=ledger.db.execute('SELECT instance FROM local_references WHERE token=?',(token,)).fetchone()
            if not reference or reference[0]!=instance:raise AdmissionWait('resource_model_instance_unknown')
            resident=ledger.join(self.runtime,model['task'],instance)
            if self._idle_upper(resident)>self.profiles['models'][model['model']]['residency']['vram_bytes']:
                raise AdmissionWait('resource_reservation_retained')
            ledger.reconcile_release(self.runtime,request_id,token,str(uuid.uuid4()),
                {'kind':'owned_local_terminal','instance':instance,'provider_terminal':True,'resident_bound_confirmed':True})
        return {'released':True}

    def model_release(self, instance):
        self.ready_core()
        with closing(Ledger(self.policy)) as ledger:
            model=self._instance(ledger,instance)
            grant=ledger.join(self.runtime,model['task'],instance)
            ledger._model_unused(instance)
            if self._idle_upper(grant)!=0:raise AdmissionWait('resource_reservation_retained')
            ledger.reconcile_release(self.runtime,model['task'],instance,str(uuid.uuid4()),
                {'kind':'owned_model_unloaded','owned_gpu_bytes':0})
        return {'released':True}

    def core_models_stopped(self):
        """Core has stopped its owned group; independently prove no GPU clients.

        The live Core controller stays charged. No PID or process-group
        authority is accepted from Core, and no native model is stopped here.
        """
        self.ready_core()
        with closing(Ledger(self.policy)) as ledger:
            rows=list(ledger.db.execute("SELECT r.* FROM reservations r JOIN local_instances i ON i.token=r.token WHERE i.owner=? AND i.engine='core' AND r.phase='HELD'",(self.owner,)))
            for row in rows:
                if self._idle_upper(row)!=0:raise AdmissionWait('resource_reservation_retained')
            ledger.release_local_engine(self.runtime,self.owner,'core',physical_released=True)
        return {'released':True}

    def stopped(self, engine):
        # Caller has completed the existing verified container + physical idle
        # barrier. Independently require every recorded scope to be empty too.
        with closing(Ledger(self.policy)) as ledger:
            for item in ledger.db.execute("SELECT r.scopes FROM reservations r JOIN local_instances i ON i.token=r.token WHERE i.owner=? AND i.engine=? AND r.phase='HELD'",(self.owner,engine)):
                for descriptor in json.loads(item[0]):
                    scope=_scope_path(descriptor)
                    if not scope_empty(scope):
                        raise AdmissionWait('resource_scope_unknown')
            ledger.release_local_engine(self.runtime,self.owner,engine,physical_released=True)
            controller=self.controllers.get(engine)
            if controller:
                row=ledger.join(self.runtime,controller['task'],controller['token'])
                for descriptor in json.loads(row['scopes']):
                    scope=_scope_path(descriptor)
                    if not scope_empty(scope):
                        raise AdmissionWait('resource_scope_unknown')
                ledger.reconcile_release(self.runtime,controller['task'],controller['token'],str(uuid.uuid4()),
                    {'kind':'owned_controller_stopped','engine':engine,'physical_barrier':True})
                self.controllers.pop(engine,None)
