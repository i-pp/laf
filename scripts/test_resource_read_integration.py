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


# Added 2026-11-15: public rejected-read behavior under shared resource grants.

"""Real pipeline/protocol with synthetic native peer and temporary shared ledger."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime/private_codex/src')]
import test_resource_pipeline as resource


def setUpModule():
    global GUARDS
    GUARDS=[patch(name,side_effect=AssertionError('No external effect in integration fixtures'))
        for name in ('subprocess.Popen','socket.socket.bind','socket.socket.connect','threading.Thread.start')]
    for guard in GUARDS:guard.start()


def tearDownModule():
    for guard in reversed(GUARDS):guard.stop()


class ResourceReadIntegration(unittest.TestCase):
    def setUp(self):
        self.f=resource.ResourceGateway();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.thread=self.f.start('integrated-thread');self.task=self.f.submit(self.thread,'integrated-task')['id']
        self.remote=self.state()['threads'][self.thread]['remote_id']
        self.assertEqual(len(self.f.held()),2)

    def state(self):return self.f.gateway.pipeline._mutate(lambda s:copy.deepcopy(s))

    def completion(self,status='completed'):
        turn=self.f.peer.threads[self.remote]['turns'][-1]
        turn.update(status=status,items=[])
        self.f.gateway.protocol.receive({'method':'turn/completed','params':{'threadId':self.remote,'turn':copy.deepcopy(turn)}})

    def rejecting(self,during,*,lost=False):
        transport=self.f.transports[-1];original=transport.peer;self.blocked=True;fired=False
        def exchange(request,timeout):
            nonlocal fired
            if request['method']=='thread/read' and self.blocked:
                if not fired:fired=True;during()
                if lost:raise OSError('Synthetic lost correlated read')
                return {'id':request['id'],'error':{'code':-32601,'message':'Synthetic rejected read'}}
            return original(request,timeout)
        transport.peer=exchange

    def assert_once(self):self.assertEqual(self.f.peer.count('turn/start'),1)

    def test_current_completion_does_not_release_provider_grant_without_exact_history(self):
        self.rejecting(self.completion)
        result=self.f.gateway.pipeline.refresh(self.task)
        self.assertEqual(result['state'],'COMPLETED');self.assertEqual(result['reconciliation'],'UNKNOWN')
        self.f.gateway.resource_gate.reap();self.assertEqual(len(self.f.held()),2)
        self.blocked=False;self.f.gateway.resource_gate.reap()
        self.assertEqual([row['token'] for row in self.f.held()],[self.f.parent]);self.assert_once()
        self.assertNotIn('resource_token',json.dumps(result));self.assertNotIn('resource_binding',json.dumps(result))

    def test_current_interruption_keeps_grant_until_exact_interrupted_turn(self):
        self.rejecting(lambda:self.completion('interrupted'))
        result=self.f.gateway.pipeline.refresh(self.task)
        self.assertEqual(result['state'],'CANCELLED');self.f.gateway.resource_gate.reap()
        self.assertEqual(len(self.f.held()),2)
        self.blocked=False;self.f.gateway.resource_gate.reap()
        self.assertEqual(len(self.f.held()),1);self.assert_once()

    def test_changed_account_after_interleaved_completion_cannot_release_grant(self):
        self.rejecting(self.completion);self.f.gateway.pipeline.refresh(self.task);self.blocked=False
        original=self.f.account
        self.f.account={'account':{'type':'chatgpt','email':'other@example.test','planType':'synthetic'}}
        self.f.gateway.resource_gate.reap();self.assertEqual(len(self.f.held()),2)
        self.f.account=original;self.f.gateway.resource_gate.reap()
        self.assertEqual(len(self.f.held()),1);self.assert_once()

    def test_lost_read_keeps_failed_gateway_and_resource_uncertainty_without_replay(self):
        self.rejecting(self.completion,lost=True)
        result=self.f.gateway.pipeline.refresh(self.task)
        self.assertEqual(result['state'],'COMPLETED');self.assertTrue(self.f.gateway.failed)
        self.f.gateway.resource_gate.reap();self.assertEqual(len(self.f.held()),2);self.assert_once()

    def test_interleaved_approval_stays_held_and_never_becomes_reclaimable_history(self):
        def approval():
            turn=self.state()['tasks'][self.task]['remote_turn']
            self.f.gateway.protocol.receive({'id':'approval','method':'item/commandExecution/requestApproval',
                'params':{'threadId':self.remote,'turnId':turn}})
        self.rejecting(approval);result=self.f.gateway.pipeline.refresh(self.task)
        self.assertEqual(result['state'],'WAITING_APPROVAL');self.f.gateway.resource_gate.reap()
        self.assertEqual(len(self.f.held()),2);self.assert_once()


if __name__=='__main__':unittest.main()
