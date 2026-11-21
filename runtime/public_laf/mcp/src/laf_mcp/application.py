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

"""Five Router tools. No lifecycle, provider, shell or model-management authority."""
import hashlib
import json
from .contracts import ContractError,exact_fields,validate_submission,validate_catalog,HANDLE
from .receipts import ReceiptError

STATES={'queued','running','completed','failed','cancelled'}
TERMINAL={'completed','failed','cancelled'}

class Application:
    def __init__(self,router,receipts,allowed):
        self.router,self.receipts,self.allowed=router,receipts,frozenset(allowed)
    @staticmethod
    def _public(r,**extra):
        out={k:r[k] for k in ('handle','model','job_id','outcome','status','cancel_outcome')}
        return dict(out,receipt_durable=True,automatic_retry=False,physical_stop_confirmed=False,**extra)
    def _update(self,r,**changes):
        with self.receipts.locked():
            current=self.receipts.load(r['handle'])
            # Concurrent polls may complete out of order. Never regress a durable
            # terminal observation, or running back to queued.
            status=changes.get('status')
            if status is not None and ((current['status'] in TERMINAL and status!=current['status']) or (current['status']=='running' and status=='queued')):
                return current
            current.update(changes);self.receipts.save(current)
        return current
    def _models(self):
        code,raw=self.router.request('GET','/v1/models')
        if code!=200:raise ContractError('catalog unavailable')
        return validate_catalog(raw,self.allowed)
    def _owned(self,args,*,cancel=False):
        exact_fields(args,{'handle'})
        with self.receipts.locked():r=self.receipts.load(args['handle'])
        if r['model'] not in self.allowed:raise ContractError('receipt model no longer allowed')
        if r['job_id'] is not None:self.router.validate_endpoint(r['endpoint'],cancel=cancel)
        return r
    def call(self,name,args):
        try:
            if name=='laf_router_submit_job':return self.submit(args)
            if name=='laf_router_get_job':return self.get(args)
            if name=='laf_router_cancel_job':return self.cancel(args)
            if name=='laf_router_models':
                exact_fields(args,set());return dict(self._models(),outcome='OBSERVED')
            if name=='laf_router_status':
                exact_fields(args,set())
                code,health=self.router.request('GET','/health')
                if (code,health.get('status')) not in ((200,'ready'),(503,'not_ready')):raise ContractError('invalid health')
                cap_code,cap=self.router.request('GET','/v1/capabilities')
                if cap_code!=200 or type(cap.get('api_version')) is not int or cap['api_version']!=1:raise ContractError('unsupported API')
                npu=cap.get('npu',{})
                return {'outcome':'OBSERVED','status':health['status'],'api_version':1,'atomic':False,
                        'npu_configured':npu.get('configured') is True,'npu_readiness_verified':False,
                        'hardware_attested':False,'automatic_retry':False}
            raise ContractError('unknown tool')
        except Exception:
            # No raw input, backend response, file path or exception text leaves here.
            return {'error':'request_rejected','outcome':'NOT_SUBMITTED' if name=='laf_router_submit_job' else 'UNAVAILABLE','automatic_retry':False}
    def submit(self,args):
        exact_fields(args,{'attempt_id','model','messages'},{'max_tokens','timeout_seconds'})
        attempt=args['attempt_id']
        if not isinstance(attempt,str) or not HANDLE.fullmatch(attempt):raise ContractError('random attempt ID required')
        body=validate_submission({k:v for k,v in args.items() if k!='attempt_id'})
        digest=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')).hexdigest()
        # Reservation and fingerprint comparison use one durable cross-process lock.
        # No request-specific bytes or digest are returned in receipts or logs.
        try:
            with self.receipts.locked():
                r=self.receipts.find(attempt)
                existing=r is not None
                if not existing:
                    try:r=self.receipts.create(None,body['model'],attempt,digest)
                    except Exception:
                        return {'handle':attempt,'outcome':'NOT_SUBMITTED','error':'receipt_create_failed','receipt_durable':False,'automatic_retry':False}
        except Exception:
            # A repeated nonce can refer to admitted work even if its file cannot
            # be read now. No claim of historical non-submission is justified.
            return {'handle':attempt,'outcome':'UNKNOWN','error':'receipt_unavailable','receipt_durable':False,'automatic_retry':False}
        if existing:
            if r['request_hash']!=digest:return self._public(r,error='attempt_conflict')
            if r['model'] not in self.allowed:return self._public(r,error='attempt_model_not_allowed',resumed_existing_attempt=True)
            if r['endpoint'] is not None:
                try:self.router.validate_endpoint(r['endpoint'])
                except Exception:return self._public(r,error='attempt_endpoint_unavailable',resumed_existing_attempt=True)
            return self._public(r,resumed_existing_attempt=True)
        try:
            if body['model'] not in self.allowed:raise ContractError('model not allowed')
            catalog=self._models()
            if body['model'] not in catalog['models']:raise ContractError('model not configured')
            r=self._update(r,endpoint=self.router.endpoint_key)
        except Exception:
            try:r=self._update(r,outcome='NOT_SUBMITTED')
            except Exception:return dict(self._public(r,error='preflight_unconfirmed'),receipt_durable=False)
            return self._public(r,error='preflight_rejected')
        # Endpoint-bound UNKNOWN intent was fsynced before the only POST.
        try:
            code,ack=self.router.request('POST','/v1/jobs',body)
            if code in (400,503):
                r=self._update(r,outcome='NOT_SUBMITTED')
                return self._public(r,error='router_rejected')
            if code!=202 or not isinstance(ack.get('id'),str) or not HANDLE.fullmatch(ack['id']) or ack.get('status') not in STATES:
                raise ContractError('invalid admission')
            r.update(job_id=ack['id'],status=ack['status'])
            try:r=self._update(r,job_id=r['job_id'],status=r['status'],outcome='ADMITTED')
            except Exception:return dict(self._public(r,error='receipt_update_failed'),outcome='UNKNOWN',receipt_durable=False)
            return self._public(r,provider_may_start_or_switch=True)
        except Exception as exc:
            if getattr(exc,'sent',None) is False:
                try:r=self._update(r,outcome='NOT_SUBMITTED')
                except Exception:pass
            return self._public(r,error='submission_unconfirmed')
    def get(self,args):
        r=self._owned(args)
        if r['job_id'] is None:return self._public(r,error='no_acknowledged_job')
        try:
            code,raw=self.router.request('GET','/v1/jobs/'+r['job_id'])
            if code!=200 or raw.get('id')!=r['job_id'] or raw.get('model')!=r['model'] or raw.get('status') not in STATES:
                raise ContractError('job observation unavailable')
            status=raw['status'];result=raw.get('result');extra={}
            if status=='completed':
                if not isinstance(result,dict) or not isinstance(result.get('content'),str):raise ContractError('invalid completion')
                result['content'].encode('utf-8','strict')
                extra={'content':result['content'],'unverified_output':True,'code_executed':False}
                if result.get('finish_reason') in ('stop','length'):extra['finish_reason']=result['finish_reason']
                for key in ('prompt_tokens','completion_tokens'):
                    if type(result.get(key)) is int and 0<=result[key]<=2**53:extra[key]=result[key]
            r=self._update(r,status=status,outcome='TERMINAL' if status in TERMINAL else 'ADMITTED')
            if r['status']!=status:
                return self._public(r,error='stale_observation',observation_current=False)
            return self._public(r,**extra)
        except Exception:
            # Prior terminal observations remain historical, never proof of current endpoint state.
            return dict(self._public(r,error='job_observation_unavailable'),outcome='UNKNOWN')
    def cancel(self,args):
        r=self._owned(args,cancel=True)
        if r['job_id'] is None:return self._public(r,error='no_acknowledged_job')
        # Atomic durable latch across threads/processes; a repeated call only reports it.
        with self.receipts.locked():
            r=self.receipts.load(r['handle'])
            if r['cancel_outcome']!='NOT_REQUESTED':return self._public(r)
            r['cancel_outcome']='UNKNOWN';self.receipts.save(r)
        try:
            code,ack=self.router.request('DELETE','/v1/jobs/'+r['job_id'])
            if code!=202 or ack.get('status')!='cancellation_requested':raise ContractError('cancellation unconfirmed')
            r=self._update(r,cancel_outcome='REQUESTED')
            return self._public(r)
        except Exception:
            return self._public(r,error='cancellation_unconfirmed')
