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


# Added 2026-11-15: provider-owned admission feedback, not inferred concurrency.
"""Bounded native metadata reads outside SQLite transactions; no model/retry."""
from contextlib import closing
import time
from resource_admission import (Ledger,AdmissionWait,account_fingerprint,provider_feedback,
    provider_identity,provider_snapshot,provider_restriction)


def enabled(settings):
    return provider_feedback(settings.get('resource_admission',{}).get('providers',{}).get('chatgpt'))


def observation(decision):
    return {'decision':decision,'usage_account_sha256':None,'denied_scopes':['provider'] if decision in ('block','unknown') else [],'clear_scopes':[]}


def terminal_error_complete(value):
    """Bounded native TurnError shape; never infer codes from message text."""
    if value is None:return True
    if (type(value) is not dict or not {'message'}<=set(value)
            or set(value)-{'message','codexErrorInfo','additionalDetails','misalignment'}
            or not isinstance(value['message'],str) or len(value['message'])>4096
            or value.get('misalignment') is not None):return False
    details=value.get('additionalDetails')
    if details is not None and (not isinstance(details,str) or len(details)>4096):return False
    info=value.get('codexErrorInfo')
    if info is None:return True
    if isinstance(info,str):return 0<len(info)<=128
    if type(info) is not dict or len(info)!=1:return False
    name=next(iter(info));frame=info[name]
    if name not in ('httpConnectionFailed','responseStreamConnectionFailed','responseStreamDisconnected','responseTooManyFailedAttempts') or type(frame) is not dict or set(frame)-{'httpStatusCode'}:return False
    code=frame.get('httpStatusCode')
    return code is None or type(code) is int and 0<=code<=65535


def record(settings,root,task,token,account,value,*,kind,revision=None,fresh=False,observed_at=None,delivery_receipt=None):
    if not enabled(settings):return
    with closing(Ledger(settings['resource_admission'])) as ledger:
        return ledger.observe_provider(root,task,token,'chatgpt',account,value,kind=kind,revision=revision,fresh=fresh,observed_at=observed_at,delivery_receipt=delivery_receipt)


def notification(settings,root,task,token,message):
    if not enabled(settings) or not isinstance(message,dict):return
    method=message.get('method');params=message.get('params')
    if not isinstance(method,str) or not method.startswith('account/'):return
    kind='notification'
    try:value=provider_snapshot(params) if method=='account/rateLimits/updated' else observation('unknown')
    except (ValueError,TypeError):value=observation('unknown')
    # Notifications can restrict/invalidate, never recover; no auth claim here.
    record(settings,root,task,token,None,value,kind=kind)


def rejection(settings,root,task,token,error):
    decision='block' if getattr(error,'provider_restriction',False) is True else 'unknown'
    record(settings,root,task,token,None,observation(decision),kind='rejection')


def turn_error(settings,root,task,token,error):
    if provider_restriction(data=error):
        record(settings,root,task,token,None,observation('block'),kind='rejection')



def refresh(settings,root,task,token,rpc,*,client=None):
    if not enabled(settings):return
    policy=settings['resource_admission'];quota=policy['providers']['chatgpt']
    with closing(Ledger(policy)) as ledger:revision=ledger.provider_revision(root,task,token,'chatgpt')
    began=time.monotonic()
    def account():
        result=rpc('account/read',{'refreshToken':False})
        current=client() if callable(client) else client
        digest=getattr(current,'_resource_account_sha256',None) if current is not None else account_fingerprint(result)
        provider_identity(quota,digest);return digest
    try:
        first=account()
        raw=rpc('account/rateLimits/read',{})
        value=raw if client is not None and set(raw)=={'decision','usage_account_sha256','denied_scopes','clear_scopes'} else provider_snapshot(raw)
        last=account()
        fresh=first==last and 0<=time.monotonic()-began<=policy['max_age_seconds']
        if not fresh:value=observation('unknown')
        record(settings,root,task,token,last,value,kind='snapshot',revision=revision,fresh=fresh,observed_at=began)
    except AdmissionWait:raise
    except Exception as error:
        # The explicitly unsupported read is unavailable capacity, not a turn
        # rejection. It cannot clear any already persisted restriction.
        if getattr(error,'code',None)==-32601:
            record(settings,root,task,token,None,observation('unavailable'),kind='snapshot',revision=revision)
        else:
            record(settings,root,task,token,None,observation('unknown'),kind='unknown')
            raise AdmissionWait('resource_provider_feedback_unknown') from error
