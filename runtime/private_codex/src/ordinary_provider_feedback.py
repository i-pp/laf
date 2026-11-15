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


# Added 2026-11-15: durable ordinary provider facts and exact execution lineage.

"""Durable ordinary-turn negatives; no new execution attempts or model calls."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

import provider_control
from resource_admission import AdmissionWait,Ledger,generation,provider_restriction

TABLE='ordinary_provider_outbox'
TERMINAL=('completed','failed','interrupted')


def opaque(value):
    return isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',value) is not None


def binding(task,execution):
    value=[task['id'],execution['id'],task['workspace_id'],task['role'],task['access'],task['resume_of']]
    return hashlib.sha256(json.dumps(value,separators=(',',':')).encode()).hexdigest()


def initialize(store):
    with store.db:
        store.db.execute("CREATE TABLE IF NOT EXISTS ordinary_provider_outbox(id TEXT PRIMARY KEY,execution_id TEXT NOT NULL REFERENCES executions(id),body TEXT NOT NULL,phase TEXT NOT NULL CHECK(phase IN ('CANDIDATE','PENDING','ACKED','IGNORED','UNKNOWN')))")
        if tuple(row[1] for row in store.db.execute('PRAGMA table_info(ordinary_provider_outbox)'))!=('id','execution_id','body','phase'):
            raise AdmissionWait('resource_provider_feedback_unknown')


class DeliveryPending(AdmissionWait):
    pass


def host_record(settings,root,body,ident,*,authority=None):
    receipt={key:body[key] for key in ('generation','binding','thread','turn','event')};receipt['event_id']=ident
    value=provider_control.observation('unknown')
    if body.get('overflow') is True:value['denied_scopes']=['unclassified']
    return provider_control.record(settings,root,body['task'],body['token'],None,value,
        kind='unknown',delivery_receipt=receipt,authority=authority)


def materialize_capture_markers(root,store,settings):
    # Only IDs may be snapshotted outside the write transaction. Another owner
    # can commit a shared receipt and clear its marker before this writer runs.
    for ident_row in store.db.execute('SELECT id FROM executions').fetchall():
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            row=store.execution(ident_row['id']);task=store.get(row['task_id'])
            owner=json.loads(row['owner']);markers=owner.get('provider_capture_unknown',{})
            if not isinstance(markers,dict) or len(markers)>33:
                raise AdmissionWait('resource_provider_feedback_unknown')
            markers=dict(markers);status=row['codex_status'];fallback=[]
            if status.startswith('PROVIDER_CAPTURE_UNKNOWN:'):
                fallback=json.loads(status.split(':',1)[1])
                if not isinstance(fallback,list) or not 0<len(fallback)<=33:
                    raise AdmissionWait('resource_provider_feedback_unknown')
            if task['state']=='UNKNOWN' and row['phase']=='UNKNOWN' and row['finished'] is None and (row['dispatched'] or row['launch_pending'] or bool(json.loads(row['processes'] or '{}'))) and not fallback and not markers:
                fallback=[str(uuid.uuid5(uuid.NAMESPACE_URL,row['id']+':unfinished-owner-loss'))]
            if fallback:
                with closing(Ledger(settings['resource_admission'])) as ledger:
                    grant=ledger.db.execute('SELECT token FROM reservations WHERE runtime=? AND task=? AND generation=?',
                        (str(Path(root).absolute()),task['id'],ledger.generation)).fetchone()
                    if not grant:raise AdmissionWait('resource_reservation_missing')
                for ident in fallback:
                    if str(uuid.UUID(ident))!=ident:raise AdmissionWait('resource_provider_feedback_unknown')
                    prior=store.db.execute('SELECT * FROM ordinary_provider_outbox WHERE id=?',(ident,)).fetchone()
                    body={'schema':1,'task':task['id'],'execution':row['id'],'token':grant['token'],
                        'generation':generation(settings['resource_admission']),'binding':binding(task,row),
                        'thread':None,'turn':None,'event':'capture_unknown','decision':'unknown'}
                    if ident==str(uuid.uuid5(uuid.NAMESPACE_URL,row['id']+':capture-overflow')):body['overflow']=True
                    if prior and prior['phase']=='ACKED':
                        # An overflow is intentionally not recoverable from a
                        # positive account sample; retain its exact marker.
                        old=json.loads(prior['body'])
                        if prior['execution_id']!=row['id'] or old!=body:
                            raise AdmissionWait('resource_provider_feedback_unknown')
                        if old.get('overflow'):markers.setdefault(ident,old)
                        continue
                    markers.setdefault(ident,body)
            for ident,body in list(markers.items()):
                if (not isinstance(body,dict) or body.get('event')!='capture_unknown' or body.get('thread') is not None or body.get('turn') is not None
                        or task['active_execution_id']!=row['id'] or body.get('execution')!=row['id'] or body.get('task')!=task['id']
                        or body.get('generation')!=generation(settings['resource_admission']) or body.get('binding')!=binding(task,row)):
                    raise AdmissionWait('resource_provider_feedback_unknown')
                encoded=json.dumps(body,sort_keys=True)
                prior=store.db.execute('SELECT * FROM ordinary_provider_outbox WHERE id=?',(ident,)).fetchone()
                if prior:
                    if prior['execution_id']!=row['id'] or prior['body']!=encoded or prior['phase'] not in ('PENDING','ACKED'):
                        raise AdmissionWait('resource_provider_feedback_unknown')
                    if prior['phase']=='ACKED' and not body.get('overflow'):markers.pop(ident)
                else:
                    store.db.execute('INSERT INTO ordinary_provider_outbox VALUES(?,?,?,?)',(ident,row['id'],encoded,'PENDING'))
            if markers!=owner.get('provider_capture_unknown',{}):
                owner['provider_capture_unknown']=markers
                store.db.execute('UPDATE executions SET owner=? WHERE id=?',(json.dumps(owner),row['id']))
            if status.startswith('PROVIDER_CAPTURE_UNKNOWN:') and all(
                    (r:=store.db.execute('SELECT phase FROM ordinary_provider_outbox WHERE id=?',(ident,)).fetchone()) and r['phase']=='ACKED' for ident in fallback):
                store.db.execute("UPDATE executions SET codex_status='UNKNOWN' WHERE id=?",(row['id'],))
    return True


def flush(root,store,settings,*,authority=None):
    if not provider_control.enabled(settings):return True
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)).fetchone():return True
    try:
        if not materialize_capture_markers(root,store,settings):return False
    except Exception:return False
    rows=[dict(row) for row in store.db.execute("SELECT * FROM ordinary_provider_outbox WHERE phase IN ('PENDING','CANDIDATE','UNKNOWN') ORDER BY rowid")]
    for row in rows:
        if row['phase']!='PENDING':continue
        body=json.loads(row['body'])
        fields={'schema','task','execution','token','generation','binding','thread','turn','event','decision'}|({'rpc_code'} if body.get('event')=='rpc_rejection' else set())|({'overflow'} if body.get('event')=='capture_unknown' and 'overflow' in body else set())
        if set(body)!=fields or body.get('schema')!=1 or body.get('execution')!=row['execution_id']:return False
        task=store.get(body['task']);execution=store.execution(row['execution_id'])
        if (task['active_execution_id']!=execution['id'] or execution['task_id']!=task['id']
                or binding(task,execution)!=body['binding'] or body['event']!='capture_unknown' and (execution['thread_id']!=body['thread'] or execution['turn_id']!=body['turn'])
                or generation(settings['resource_admission'])!=body['generation']):return False
        try:
            receipt={key:body[key] for key in ('generation','binding','thread','turn','event')}
            if body['event']=='capture_unknown':host_record(settings,root,body,row['id'],authority=authority)
            else:
                if body['event']=='live_error':receipt['event_id']=row['id']
                value=provider_control.observation(body['decision'])
                provider_control.record(settings,root,task['id'],body['token'],None,value,kind='rejection',
                    delivery_receipt=receipt,authority=authority)
            with store.db:
                store.db.execute('BEGIN IMMEDIATE')
                current=store.db.execute('SELECT * FROM ordinary_provider_outbox WHERE id=?',(row['id'],)).fetchone()
                now_task=store.get(task['id']);now_execution=store.execution(execution['id'])
                if (not current or current['phase']!='PENDING' or current['body']!=row['body']
                        or now_task['active_execution_id']!=execution['id'] or binding(now_task,now_execution)!=body['binding']
                        or body['event']!='capture_unknown' and (now_execution['thread_id']!=body['thread'] or now_execution['turn_id']!=body['turn'])):
                    raise AdmissionWait('resource_provider_feedback_unknown')
                store.db.execute("UPDATE ordinary_provider_outbox SET phase='ACKED' WHERE id=?",(row['id'],))
                if body['event']=='capture_unknown' and not body.get('overflow'):
                    owner=json.loads(now_execution['owner']);markers=owner.get('provider_capture_unknown',{})
                    if markers.get(row['id'])!=body:raise AdmissionWait('resource_provider_feedback_unknown')
                    markers.pop(row['id']);owner['provider_capture_unknown']=markers
                    store.db.execute('UPDATE executions SET owner=? WHERE id=?',(json.dumps(owner),execution['id']))
                    status=now_execution['codex_status']
                    if status.startswith('PROVIDER_CAPTURE_UNKNOWN:'):
                        ids=json.loads(status.split(':',1)[1])
                        if all((r:=store.db.execute('SELECT phase FROM ordinary_provider_outbox WHERE id=?',(ident,)).fetchone()) and r['phase']=='ACKED' for ident in ids):
                            store.db.execute("UPDATE executions SET codex_status='UNKNOWN' WHERE id=?",(execution['id'],))
        except Exception:return False
    return not store.db.execute("SELECT 1 FROM ordinary_provider_outbox WHERE phase IN ('PENDING','CANDIDATE','UNKNOWN') LIMIT 1").fetchone() and not any(json.loads(row[0]).get('provider_capture_unknown') for row in store.db.execute('SELECT owner FROM executions'))


def provider_closed(store,execution,token):
    if not execution['dispatched']:return True
    terminal=json.loads(execution.get('terminal') or 'null')
    if not isinstance(terminal,dict) or terminal.get('status') not in TERMINAL:return False
    if execution['turn_id']:
        return terminal.get('id')==execution['turn_id'] and ('error' not in terminal or provider_control.terminal_error_complete(terminal['error']))
    error=terminal.get('error')
    if (not isinstance(error,dict) or error.get('kind')!='correlated_rpc_rejection'
            or error.get('method')!='turn/start' or type(error.get('code')) is not int):return False
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)).fetchone():return False
    for row in store.db.execute("SELECT body FROM ordinary_provider_outbox WHERE execution_id=? AND phase='ACKED'",(execution['id'],)):
        body=json.loads(row['body'])
        if (body['event']=='rpc_rejection' and body['token']==token and body['thread']==execution['thread_id']
                and body['turn'] is None and body['rpc_code']==error['code']):return True
    return False


class OrdinaryFeedback:
    def __init__(self,root,store,settings,task_id,token=None,*,authority=None):
        self.root,self.store,self.settings=Path(root),store,settings
        task=store.get(task_id)
        if not task['active_execution_id']:raise AdmissionWait('resource_reservation_missing')
        self.task_id,self.execution_id=task_id,task['active_execution_id']
        self.authority=authority
        if token is None:
            with closing(Ledger(settings['resource_admission'])) as ledger:
                row=ledger.db.execute('SELECT token FROM reservations WHERE runtime=? AND task=? AND generation=?',
                    (str(self.root.absolute()),task_id,ledger.generation)).fetchone()
                if not row:raise AdmissionWait('resource_reservation_missing')
                token=row['token']
        self.token=token
        self.capture_faults={}
        execution=store.execution(self.execution_id)
        self.capture_base={'schema':1,'task':task_id,'execution':self.execution_id,'token':token,'generation':generation(self.settings['resource_admission']),
            'binding':binding(task,execution),'thread':None,'turn':None,'event':'capture_unknown','decision':'unknown'}
        initialize(store)

    def current(self):
        task=self.store.get(self.task_id);execution=self.store.execution(self.execution_id)
        if task['active_execution_id']!=execution['id'] or execution['task_id']!=task['id']:
            raise AdmissionWait('resource_provider_feedback_unknown')
        return task,execution

    def body(self,event,turn,**extra):
        task,execution=self.current()
        return {'schema':1,'task':task['id'],'execution':execution['id'],'token':self.token,
            'generation':generation(self.settings['resource_admission']),'binding':binding(task,execution),
            'thread':execution['thread_id'],'turn':turn,'event':event,'decision':'block',**extra}

    def put(self,event,turn,*,uncertain=None,decision='block',rpc_code=None):
        body=self.body(event,turn,decision=decision)
        if event=='rpc_rejection':
            if type(rpc_code) is not int:raise AdmissionWait('resource_provider_feedback_unknown')
            body['rpc_code']=rpc_code
        if not opaque(body['thread']) or not (event=='rpc_rejection' and turn is None or opaque(turn) or event=='uncertain' and turn=='*'):
            raise AdmissionWait('resource_provider_feedback_unknown')
        if uncertain is not None:body['uncertain']=uncertain
        ident=str(uuid.uuid4()) if event=='live_error' else str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps([self.execution_id,event,turn,uncertain],separators=(',',':'))))
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            task,execution=self.current()
            phase='UNKNOWN' if event=='uncertain' else 'CANDIDATE' if execution['turn_id'] is None and event!='rpc_rejection' else 'PENDING'
            if phase=='CANDIDATE':
                turns={json.loads(row[0])['turn'] for row in self.store.db.execute("SELECT body FROM ordinary_provider_outbox WHERE execution_id=? AND phase='CANDIDATE'",(self.execution_id,))}
                if turn not in turns and len(turns)>=32:
                    body=self.body('uncertain','*',uncertain='terminal_overflow' if event=='terminal' else 'retry_overflow')
                    ident=str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps([self.execution_id,body['uncertain']])))
                    phase='UNKNOWN'
            encoded=json.dumps(body,sort_keys=True)
            old=self.store.db.execute('SELECT body FROM ordinary_provider_outbox WHERE id=?',(ident,)).fetchone()
            if old and old['body']!=encoded:raise AdmissionWait('resource_provider_feedback_unknown')
            self.store.db.execute('INSERT OR IGNORE INTO ordinary_provider_outbox VALUES(?,?,?,?)',(ident,self.execution_id,encoded,phase))

    def bind(self,turn):
        native=turn.get('id')
        if not opaque(native):raise AdmissionWait('resource_provider_feedback_unknown')
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            task,execution=self.current()
            if execution['turn_id'] is not None or not execution['dispatched']:
                raise AdmissionWait('resource_provider_feedback_unknown')
            if self.store.db.execute('SELECT 1 FROM executions WHERE thread_id=? AND turn_id=? AND id<>?',(execution['thread_id'],native,self.execution_id)).fetchone():
                raise AdmissionWait('resource_provider_feedback_unknown')
            self.store.db.execute('UPDATE executions SET turn_id=?,updated=? WHERE id=?',(native,time.time(),self.execution_id))
            self.store.db.execute('UPDATE tasks SET turn_id=?,updated=? WHERE id=?',(native,time.time(),self.task_id))
            candidates=[dict(row) for row in self.store.db.execute("SELECT * FROM ordinary_provider_outbox WHERE execution_id=? AND phase='CANDIDATE'",(self.execution_id,))]
            matched=any(json.loads(row['body'])['turn']==native for row in candidates)
            for row in candidates:
                phase='PENDING' if json.loads(row['body'])['turn']==native else 'IGNORED'
                self.store.db.execute('UPDATE ordinary_provider_outbox SET phase=? WHERE id=?',(phase,row['id']))
            if matched:
                for row in self.store.db.execute("SELECT id,body FROM ordinary_provider_outbox WHERE execution_id=? AND phase='UNKNOWN'",(self.execution_id,)).fetchall():
                    if json.loads(row['body']).get('uncertain') in ('retry_overflow','terminal_overflow'):
                        self.store.db.execute("UPDATE ordinary_provider_outbox SET phase='IGNORED' WHERE id=?",(row['id'],))
        if turn.get('status') in TERMINAL:self.terminal(turn)
        elif provider_restriction(data=turn.get('error')):self.put('live_error',native)
        if not self.flush():raise DeliveryPending('resource_provider_feedback_unknown')

    def terminal(self,turn,*,history=False):
        _,execution=self.current();native=turn.get('id')
        if not opaque(native) or execution['turn_id'] is not None and native!=execution['turn_id']:return
        valid=turn.get('status') in TERMINAL
        complete='error' in turn and provider_control.terminal_error_complete(turn['error'])
        if not valid or 'error' in turn and not complete:self.put('uncertain',native,uncertain='malformed_terminal')
        negative=provider_restriction(data=turn.get('error'))
        if negative:self.put('terminal' if valid else 'live_error',native)
        if history and valid and complete and native==execution['turn_id']:
            with self.store.db:
                for row in self.store.db.execute("SELECT id,body FROM ordinary_provider_outbox WHERE execution_id=? AND phase='UNKNOWN'",(self.execution_id,)).fetchall():
                    body=json.loads(row['body'])
                    if (body['turn'] in (native,'*') and (body.get('uncertain') in ('terminal_overflow','malformed_terminal') or negative)):
                        self.store.db.execute("UPDATE ordinary_provider_outbox SET phase='IGNORED' WHERE id=?",(row['id'],))

    def capture_failure(self):
        ident=str(uuid.uuid4());body=dict(self.capture_base)
        if len(self.capture_faults)>=32:
            ident=str(uuid.uuid5(uuid.NAMESPACE_URL,self.execution_id+':capture-overflow'));body['overflow']=True
        self.capture_faults[ident]=body
        self.retry_capture_failures()

    def retry_capture_failures(self):
        for ident,body in list(self.capture_faults.items()):
            try:
                with self.store.db:
                    self.store.db.execute('BEGIN IMMEDIATE');task,execution=self.current()
                    if binding(task,execution)!=body['binding']:raise AdmissionWait('resource_provider_feedback_unknown')
                    owner=json.loads(execution['owner']);markers=owner.setdefault('provider_capture_unknown',{})
                    if len(markers)>=32 and ident not in markers:
                        ident=str(uuid.uuid5(uuid.NAMESPACE_URL,self.execution_id+':capture-overflow'));body={**self.capture_base,'overflow':True}
                    markers[ident]=body
                    self.store.db.execute('UPDATE executions SET owner=? WHERE id=?',(json.dumps(owner),self.execution_id))
                if flush(self.root,self.store,self.settings,authority=self.authority):self.capture_faults.pop(ident,None)
            except Exception:
                # A private-store failure must not discard a charged host fact.
                # The same event UUID is retained for later local acknowledgement.
                try:host_record(self.settings,self.root,body,ident,authority=self.authority)
                except Exception:pass

    def capture(self,message):
        try:return self._capture(message)
        except DeliveryPending:raise
        except Exception:
            self.capture_failure()
            raise

    def _capture(self,message):
        scope=self.authority or {'task':self.task_id,'token':self.token}
        provider_control.notification(self.settings,self.root,scope['task'],scope['token'],message)
        if not isinstance(message,dict) or 'id' in message or not isinstance(message.get('params'),dict):return
        _,execution=self.current();params=message['params'];method=message.get('method')
        if not execution['dispatched'] and method in ('error','turn/completed','item/completed','thread/tokenUsage/updated'):return False
        if params.get('threadId')!=execution['thread_id'] or not execution['thread_id']:return
        if method=='error' and type(params.get('willRetry')) is bool:
            native=params.get('turnId')
            if opaque(native) and (execution['turn_id'] is None or native==execution['turn_id']) and provider_restriction(data=params.get('error')):
                self.put('preack_error' if execution['turn_id'] is None else 'live_error',native)
        elif method=='turn/completed' and isinstance(params.get('turn'),dict):self.terminal(params['turn'])
        if execution['turn_id'] is not None and not self.flush():raise DeliveryPending('resource_provider_feedback_unknown')

    def rejected(self,error):
        try:return self._rejected(error)
        except DeliveryPending:raise
        except Exception:
            self.capture_failure()
            raise

    def observe_history(self,turn):
        try:self.terminal(turn,history=True)
        except Exception:
            self.capture_failure()
            raise

    def _rejected(self,error):
        if getattr(error,'method',None)!='turn/start' or getattr(error,'correlated',False) is not True:
            raise AdmissionWait('resource_provider_feedback_unknown')
        self.put('rpc_rejection',None,decision='block' if error.provider_restriction else 'unknown',rpc_code=error.code)
        if not self.flush():raise DeliveryPending('resource_provider_feedback_unknown')

    def flush(self):
        return flush(self.root,self.store,self.settings,authority=self.authority)
