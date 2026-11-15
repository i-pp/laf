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
        provider_control.refresh(self.settings,self.gateway.root,task['id'],task['resource_token'],self.gateway.rpc,client=lambda:self.gateway.protocol)
        digest = self.account()
        with closing(Ledger(self.policy)) as ledger:
            ledger.recheck(self.gateway.root, task['id'], task['resource_token'], linux_observation)
            ledger.authorize_provider(self.gateway.root, task['id'], task['resource_token'], 'chatgpt', digest)

    def before_turn(self, task_id):
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

    def pending(self, thread_id):
        state = self.gateway.pipeline._mutate(lambda s: json.loads(json.dumps(s)))
        with closing(Ledger(self.policy)) as ledger:
            return any(row['task'] in state['tasks'] and state['tasks'][row['task']]['thread_id'] == thread_id
                       for row in ledger.held() if row['runtime'] == str(self.gateway.root.absolute()))

    def reap(self):
        """Correlated provider history or definitive pre-turn failure only."""
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
                        ended = (reply.get('id') == thread['remote_id'] and any(
                            turn.get('id') == task['remote_turn'] and turn.get('status') == expected[task['state']]
                            for turn in reply.get('turns', [])))
                    except Exception:
                        ended = False
                if unused or ended:
                    # The physical shared process remains charged to its parent
                    # controller; retained contexts reduce measured parent headroom.
                    ledger.reconcile_release(self.gateway.root, task['id'], row['token'], str(uuid.uuid4()),
                        {'kind': 'pipeline_terminal' if ended else 'pipeline_never_dispatched',
                         'task': task['id'], 'binding': self.binding(task), 'provider_closed': True})
