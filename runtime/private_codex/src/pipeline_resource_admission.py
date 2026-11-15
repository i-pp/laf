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


# Added 2026-11-15: resource-only admission with durable ownership.

"""One-use pipeline resource grants beneath a verified daemon controller."""
from contextlib import closing
import hashlib
import json
import time
import uuid

from resource_admission import AdmissionWait, Ledger, linux_observation


class PipelineAdmission:
    def __init__(self, gateway, settings, controller):
        if not callable(controller): raise AdmissionWait('resource_owner_unknown')
        self.gateway, self.settings, self.controller = gateway, settings, controller
        self.policy = settings['resource_admission']
        self.reap_cursor = None
        if 'pipeline' not in self.policy: raise AdmissionWait('resource_container_unknown')

    def ready(self):
        import scheduler
        scheduler.require_pool_generation(self.settings, codex_pipeline=True)
        from daemon import verify_self_limits
        verify_self_limits(allow_codex_pipeline=True)
        owner, token = self.controller()
        if not owner or not token: raise AdmissionWait('resource_owner_unknown')
        return owner, token

    @staticmethod
    def binding(task):
        value = [task['id'], task['thread_id'], task['selection_sha256']]
        return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()

    def reserve(self, task):
        owner, parent = self.ready()
        token = task.get('resource_token')
        with closing(Ledger(self.policy)) as ledger:
            if token:
                row = ledger.join(self.gateway.root, task['id'], token)
                association = ledger.db.execute('SELECT owner FROM reservation_owners WHERE token=?', (token,)).fetchone()
                if not association or association[0] != owner or task.get('resource_binding') != self.binding(task):
                    raise AdmissionWait('resource_reservation_retained')
                if json.loads(row['demand'])['resources'] != self.policy['pipeline']:
                    raise AdmissionWait('resource_generation_held')
                return token
            token = str(uuid.uuid4())
            from resource_admission import provider_feedback
            demand = {'kind': 'local_control', 'provider': None if provider_feedback(self.policy['providers'].get('chatgpt')) else 'chatgpt', 'resources': self.policy['pipeline']}
            ledger.reserve(self.gateway.root, task['id'], token, demand, linux_observation,
                           owner=owner, within=parent)
        return token

    def account(self, *, timeout=None):
        self.gateway.rpc('account/read', {'refreshToken': False}, **({'timeout': timeout} if timeout is not None else {}))
        client = self.gateway.protocol
        observed = getattr(client, '_resource_account_observed_at', None)
        digest = getattr(client, '_resource_account_sha256', None)
        if (type(observed) not in (int, float) or not 0 <= time.monotonic() - observed <= self.policy['max_age_seconds']
                or digest != self.policy['providers'].get('chatgpt', {}).get('account_sha256')):
            raise AdmissionWait('resource_provider_account_unknown')
        return digest

    def authorize(self, task):
        import provider_control
        if not self.flush_feedback():raise AdmissionWait('resource_provider_feedback_unknown')
        provider_control.refresh(self.settings,self.gateway.root,task['id'],task['resource_token'],self.gateway.rpc,client=lambda:self.gateway.protocol)
        digest = self.account()
        with closing(Ledger(self.policy)) as ledger:
            ledger.recheck(self.gateway.root, task['id'], task['resource_token'], linux_observation)
            ledger.authorize_provider(self.gateway.root, task['id'], task['resource_token'], 'chatgpt', digest)

    def before_turn(self, task_id):
        if not self.flush_feedback():raise AdmissionWait('resource_provider_feedback_unknown')
        client = self.gateway.protocol
        observed = getattr(client, '_resource_account_observed_at', None)
        if (type(observed) not in (int, float) or not 0 <= time.monotonic() - observed <= self.policy['max_age_seconds']
                or getattr(client, '_resource_account_sha256', None) != self.policy['providers']['chatgpt']['account_sha256']):
            raise AdmissionWait('resource_provider_account_unknown')
        task = self.gateway.pipeline._mutate(lambda s: dict(s['tasks'][task_id]))
        if task.get('resource_binding') != self.binding(task): raise AdmissionWait('resource_reservation_missing')
        with closing(Ledger(self.policy)) as ledger:
            row = ledger.join(self.gateway.root, task_id, task.get('resource_token'))
            if json.loads(row['demand'])['kind'] != 'cloud_api': raise AdmissionWait('resource_provider_account_unknown')
            ledger.authorize_provider(self.gateway.root,task_id,task['resource_token'],'chatgpt',client._resource_account_sha256)

    def feedback_scope(self,task=None):
        if task is not None:return task['id'],task['resource_token']
        _,token=self.controller()
        with closing(Ledger(self.policy)) as ledger:
            row=ledger.db.execute('SELECT task FROM reservations WHERE token=? AND runtime=?',(token,str(self.gateway.root.absolute()))).fetchone()
            if not row:raise AdmissionWait('resource_controller_required')
            return row[0],token

    def notification(self,message):
        import provider_control
        if not provider_control.enabled(self.settings):return
        task,token=self.feedback_scope()
        provider_control.notification(self.settings,self.gateway.root,task,token,message)

    def rejected(self,task_id,error):
        import provider_control
        if not provider_control.enabled(self.settings):return
        task=self.gateway.pipeline._mutate(lambda s:dict(s['tasks'][task_id]))
        provider_control.rejection(self.settings,self.gateway.root,*self.feedback_scope(task),error)

    def turn_error(self,task,error):
        import provider_control
        if not provider_control.enabled(self.settings):return
        provider_control.turn_error(self.settings,self.gateway.root,*self.feedback_scope(task),error)

    def feedback_record(self, state, task, turn, *, event='terminal'):
        from resource_admission import generation
        thread=state['threads'][task['thread_id']].get('remote_id')
        native=turn.get('id');terminal=turn.get('status') if event=='terminal' else None
        if (not isinstance(thread,str) or not 0<len(thread)<=512 or not isinstance(native,str) or not 0<len(native)<=512
                or task.get('remote_turn')!=native or task.get('resource_binding')!=self.binding(task)
                or event not in ('terminal','preack_error')
                or event=='terminal' and terminal not in ('completed','failed','interrupted')):
            raise AdmissionWait('resource_provider_feedback_unknown')
        return {'schema':1,'runtime':str(self.gateway.root.absolute()),'task':task['id'],
            'token':task['resource_token'],'generation':generation(self.policy),'binding':self.binding(task),
            'thread':thread,'turn':native,'terminal':terminal,'event':event,'restriction':True}

    def terminal_record(self, state, task, turn):
        return self.feedback_record(state,task,turn)

    def stage_preack_error(self, state, task, turn):
        record=self.feedback_record(state,task,turn,event='preack_error')
        previous=task.get('provider_error_pending')
        if previous is not None and previous!=record:raise AdmissionWait('resource_provider_feedback_unknown')
        task['provider_error_pending']=record

    def stage_terminal(self, state, task, turn, *, history=False):
        """Called only inside the canonical Pipeline transaction; no shared DB I/O."""
        import provider_control
        from resource_admission import provider_restriction
        if not provider_control.enabled(self.settings):return
        record=self.terminal_record(state,task,turn)
        previous=task.get('provider_feedback_pending')
        if previous is not None and previous!=record:raise AdmissionWait('resource_provider_feedback_unknown')
        negative=turn.get('provider_restriction') is True or provider_restriction(data=turn.get('error'))
        if previous is not None or negative:task['provider_feedback_pending']=record
        complete='error' in turn and provider_control.terminal_error_complete(turn['error'])
        if turn.get('provider_error_unknown') is True or 'error' in turn and not complete:
            task['provider_feedback_uncertain']=True
        if history and complete:
            task.pop('provider_feedback_uncertain',None)
            # A terminal null cannot disprove a lost earlier retrying error.
            if negative:task.pop('provider_error_uncertain',None)

    def flush_feedback(self):
        """Snapshot, shared atomic delivery, exact local ack; never overlap writers."""
        import provider_control
        if not provider_control.enabled(self.settings):return True
        state=self.gateway.pipeline._mutate(lambda s:json.loads(json.dumps(s)))
        pending=(('provider_error_pending','preack_error'),('provider_feedback_pending','terminal'))
        for ident,task in state['tasks'].items():
            for field,event in pending:
                record=task.get(field)
                if record is None:continue
                try:
                    expected=self.feedback_record(state,task,{'id':record.get('turn'),'status':record.get('terminal')},event=event)
                    if record!=expected:raise AdmissionWait('resource_provider_feedback_unknown')
                    receipt={key:record[key] for key in ('generation','binding','thread','turn','event')}
                    provider_control.record(self.settings,self.gateway.root,ident,record['token'],None,
                        provider_control.observation('block'),kind='rejection',delivery_receipt=receipt)
                    def acknowledge(s):
                        current=s['tasks'][ident]
                        if current.get(field)!=record or self.feedback_record(s,current,{'id':record['turn'],'status':record['terminal']},event=event)!=record:
                            raise AdmissionWait('resource_provider_feedback_unknown')
                        current.pop(field)
                        if (current.get('reason_code')=='PROVIDER_FEEDBACK_PERSISTENCE_UNKNOWN'
                                and not any(current.get(key) for key in ('provider_feedback_uncertain','provider_error_uncertain','provider_error_pending','provider_feedback_pending'))):
                            if event=='terminal' and current['state']=='UNKNOWN':
                                current['state']={'completed':'COMPLETED','failed':'FAILED','interrupted':'CANCELLED'}[record['terminal']]
                            if current['state']!='UNKNOWN':current.pop('reason_code')
                    self.gateway.pipeline._mutate(acknowledge)
                except Exception:
                    def uncertain(s):
                        current=s['tasks'][ident]
                        current.update(state='UNKNOWN',reason_code='PROVIDER_FEEDBACK_PERSISTENCE_UNKNOWN')
                    self.gateway.pipeline._mutate(uncertain)
                    return False
        return self.gateway.pipeline._mutate(lambda s:not any(
            any(task.get(key) for key in ('provider_feedback_pending','provider_error_pending','provider_feedback_uncertain','provider_error_uncertain')) for task in s['tasks'].values()))

    def pending(self, thread_id):
        state = self.gateway.pipeline._mutate(lambda s: json.loads(json.dumps(s)))
        with closing(Ledger(self.policy)) as ledger:
            return any(row['task'] in state['tasks'] and state['tasks'][row['task']]['thread_id'] == thread_id
                       for row in ledger.held() if row['runtime'] == str(self.gateway.root.absolute()))

    def reap(self):
        """Correlated provider history or definitive pre-turn failure only."""
        if not self.flush_feedback():return
        state = self.gateway.pipeline._mutate(lambda s: json.loads(json.dumps(s)))
        with closing(Ledger(self.policy)) as ledger:
            rows = [r for r in ledger.held() if r['runtime'] == str(self.gateway.root.absolute())]
            history = sorted(r['token'] for r in rows if r['task'] in state['tasks']
                             and state['tasks'][r['task']]['state'] in ('COMPLETED', 'FAILED', 'CANCELLED')
                             and state['tasks'][r['task']].get('remote_turn'))
            eligible = next((token for token in history if self.reap_cursor is None or token > self.reap_cursor),
                            history[0] if history else None)
            if eligible is not None: self.reap_cursor = eligible
            for row in rows:
                task = state['tasks'].get(row['task'])
                if not task or task.get('resource_token') != row['token']: continue
                if task.get('resource_binding') != self.binding(task): continue
                operation = state['ops'].get('dispatch:' + task['id'])
                unused = (not task.get('remote_turn') and
                    ((operation is None and task['state'] in ('BLOCKED', 'CANCELLED', 'FAILED'))
                     or (operation and operation['status'] == 'FAILED'
                         and task['state'] == 'FAILED'
                         and task.get('reasoning_effort_evidence', {}).get('turn_transport') == 'rejected')))
                ended = False
                if row['token'] == eligible:
                    try:
                        self.account(timeout=1)
                        thread = state['threads'][task['thread_id']]
                        reply = self.gateway.rpc('thread/read', {'threadId': thread['remote_id'], 'includeTurns': True}, timeout=1)['thread']
                        expected = {'COMPLETED': 'completed', 'FAILED': 'failed', 'CANCELLED': 'interrupted'}
                        terminal=next((turn for turn in reply.get('turns',[]) if turn.get('id')==task['remote_turn']
                            and turn.get('status')==expected[task['state']]),None)
                        ended=reply.get('id')==thread['remote_id'] and terminal is not None
                        if ended:
                            def stage(s):
                                current=s['tasks'][task['id']]
                                if current.get('resource_token')!=row['token'] or self.binding(current)!=task.get('resource_binding'):
                                    raise AdmissionWait('resource_reservation_retained')
                                self.stage_terminal(s,current,terminal,history=True)
                                if current.get('provider_feedback_uncertain') or current.get('provider_error_uncertain'):
                                    current.update(state='UNKNOWN',reason_code='PROVIDER_FEEDBACK_PERSISTENCE_UNKNOWN')
                            self.gateway.pipeline._mutate(stage)
                            if not self.flush_feedback():return
                    except Exception:
                        ended = False
                if unused or ended:
                    # The physical shared process remains charged to its parent
                    # controller; retained contexts reduce measured parent headroom.
                    ledger.reconcile_release(self.gateway.root, task['id'], row['token'], str(uuid.uuid4()),
                        {'kind': 'pipeline_terminal' if ended else 'pipeline_never_dispatched',
                         'task': task['id'], 'binding': self.binding(task), 'provider_closed': True})
