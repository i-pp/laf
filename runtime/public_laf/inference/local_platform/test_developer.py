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

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from .developer import develop,parse_action
from .cli import ClientError

CODE='def add(a,b):\n    return a-b\n'


def manifest():
    return {'version':1,'goal':'Repair arithmetic without changing tests.',
            'files':[{'path':'code.py','access':'write'},{'path':'tests/test_code.py','access':'read'}],
            'test':{'kind':'python-unittest','start_dir':'tests','pattern':'test_*.py'}}


def action(tool,**arguments):return {'job_id':'a'*32,'content':json.dumps({'tool':tool,'arguments':arguments})}


class Runner:
    timeout=1
    def __init__(self,outcomes=None,ready=True):
        self.outcomes=outcomes or [{'status':'failed','test_count':12,'exit_code':1,'stop_confirmed':True},
                                   {'status':'passed','test_count':12,'exit_code':0,'stop_confirmed':True}]
        self.calls=[];self.ready=ready
    def preflight(self):return {'status':'ready' if self.ready else 'blocked'}
    def run(self,*args,**kwargs):self.calls.append((args,kwargs));return self.outcomes.pop(0)


class DeveloperTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'source';self.source.mkdir();(self.source/'tests').mkdir()
        (self.source/'code.py').write_text(CODE)
        (self.source/'tests/test_code.py').write_text('# immutable operator tests\n')
    def tearDown(self):self.temp.cleanup()
    def run_actions(self,actions,runner=None):
        calls=[]
        def advise(*args,**kwargs):calls.append((args,kwargs));return actions.pop(0)
        runner=runner or Runner()
        result=develop('/unused',self.source,manifest(),self.root/'run',runner,adviser=advise)
        return result,calls,runner
    def edit(self,**kwargs):
        return action('replace_text',path='code.py',expected_sha256=hashlib.sha256(CODE.encode()).hexdigest(),
                      old='a-b',new='a+b',**kwargs)
    def test_structured_read_edit_test_finish_original_unchanged(self):
        out,calls,runner=self.run_actions([action('read_file',path='code.py'),self.edit(),
                                           action('run_tests'),action('finish',summary='candidate repaired')])
        self.assertEqual(out['status'],'completed');self.assertEqual(len(runner.calls),2)
        self.assertEqual(len(calls),2);self.assertTrue(out['turns'][-1]['automatic_test'])
        self.assertEqual(out['final_test']['test_count'],12)
        self.assertEqual(len(out['changes']),1);self.assertTrue(out['requires_diff_review'])
        self.assertEqual((self.source/'code.py').read_text(),CODE)
        self.assertEqual((self.root/'run/workspace/code.py').read_text(),CODE.replace('a-b','a+b'))
        self.assertTrue(all(len(args[0][2].encode())<=6000 for args in calls))
    def test_no_test_boundary_means_blocked_no_inference(self):
        out,calls,runner=self.run_actions([],Runner(ready=False))
        self.assertEqual(out['status'],'blocked');self.assertEqual(calls,[]);self.assertEqual(runner.calls,[])
    def test_fenced_and_duplicate_key_json_rejected(self):
        for value in ['```json\n{"tool":"run_tests","arguments":{}}\n```',
                      '{"tool":"exec","tool":"run_tests","arguments":{}}',
                      '{"tool":"read_file","arguments":{"path":"x","path":"y"}}']:
            with self.assertRaises(ValueError):parse_action(value)
    def test_passing_evidence_is_bound_to_current_hashes(self):
        root=self.root
        class TamperAfterTest(Runner):
            def run(self,*args,**kwargs):
                out=super().run(*args,**kwargs)
                if len(self.calls)==2:(root/'run/workspace/code.py').write_text('changed during test\n')
                return out
        out,_,_=self.run_actions([self.edit()],TamperAfterTest())
        self.assertNotEqual(out['status'],'completed')
        self.assertEqual(out['final_test']['status'],'uncertain')
    def test_integrity_failure_stops_inference_and_writes_terminal_record(self):
        calls=[]
        def advise(*a,**k):
            calls.append(a)
            p=self.root/'run/workspace/tests/test_code.py';p.chmod(0o600);p.write_text('external tamper')
            return action('finish',summary='PASS')
        out=develop('/unused',self.source,manifest(),self.root/'run',Runner(),adviser=advise)
        self.assertEqual(len(calls),1);self.assertEqual(out['status'],'failed')
        self.assertTrue(out['changes_unavailable']);self.assertTrue(out['artifact_written'])
        self.assertEqual(json.loads((self.root/'run/result.json').read_text())['status'],'failed')
    def test_uncertain_runner_evidence_survives_integrity_failure(self):
        root=self.root
        class UncertainTamper(Runner):
            def run(self,*args,**kwargs):
                if self.calls:
                    p=root/'run/workspace/tests/test_code.py';p.chmod(0o600);p.write_text('external tamper')
                    return {'status':'uncertain','test_count':0,'stop_confirmed':False,'reason':'owned stop unknown'}
                return super().run(*args,**kwargs)
        out,_,_=self.run_actions([self.edit(),action('run_tests')],UncertainTamper())
        self.assertEqual(out['status'],'uncertain');self.assertFalse(out['stop_confirmed'])
        self.assertEqual(out['final_test']['reason'],'owned stop unknown')
        self.assertFalse(out['final_test']['stop_confirmed']);self.assertTrue(out['changes_unavailable'])
        saved=json.loads((self.root/'run/result.json').read_text())
        self.assertEqual(saved['status'],'uncertain')
    def test_unexpected_runner_exception_does_not_reuse_baseline_evidence(self):
        class Broken(Runner):
            def run(self,*args,**kwargs):raise RuntimeError('transport contract failed')
        out,calls,_=self.run_actions([],Broken())
        self.assertEqual(out['status'],'uncertain');self.assertEqual(calls,[])
        self.assertFalse(out['final_test']['stop_confirmed'])
    def test_large_read_request_is_explicitly_clipped_not_authorized_more(self):
        out,calls,runner=self.run_actions([action('read_file',path='code.py',max_lines=200),self.edit(),
                                          action('run_tests'),action('finish',summary='candidate repaired')])
        self.assertEqual(out['status'],'completed')
        self.assertIn('"applied_max_lines": 60',calls[1][0][2])
        self.assertIn('"clipped_to_client_budget": true',calls[1][0][2])
    def test_no_host_or_shell_tool(self):
        with self.assertRaises(ValueError):parse_action(json.dumps({'tool':'exec','arguments':{'command':'true'}}))
        for name in ['run_tests','read_file','replace_text']:
            with self.assertRaises(ValueError):parse_action(json.dumps({'tool':name,'arguments':{'command':'true'}}))
    def test_finish_prose_without_test_not_accepted(self):
        runner=Runner([{'status':'failed','test_count':12,'stop_confirmed':True}]*2)
        out,calls,runner=self.run_actions([self.edit()]+[action('finish',summary='PASS')]*5,runner)
        self.assertEqual(out['status'],'failed');self.assertEqual(out['final_test']['status'],'failed')
        self.assertEqual(len(runner.calls),2)
    def test_test_count_decrease_not_accepted(self):
        runner=Runner([{'status':'failed','test_count':12,'stop_confirmed':True},
                       {'status':'passed','test_count':1,'stop_confirmed':True}])
        out,calls,_=self.run_actions([self.edit()]+[action('finish',summary='PASS')]*5,runner)
        self.assertEqual(out['status'],'failed');self.assertEqual(out['final_test']['status'],'failed')
    def test_infrastructure_failure_not_given_to_model(self):
        for result in [{'status':'failed','test_count':0,'stop_confirmed':True},
                       {'status':'uncertain','test_count':12,'stop_confirmed':False}]:
            with self.subTest(result=result):
                runner=Runner([result]);runner.calls=[]
                run=self.root/('run'+str(len(list(self.root.iterdir()))))
                def adviser(*a,**k):raise AssertionError('must not infer')
                out=develop('/unused',self.source,manifest(),run,runner,adviser=adviser)
                self.assertNotEqual(out['status'],'completed');self.assertEqual(out['turns'],[])
    def test_uncertain_model_outcome_preserves_id_never_retries(self):
        calls=[]
        def advise(*a,**k):calls.append(a);raise ClientError('outcome unknown','b'*32)
        out=develop('/unused',self.source,manifest(),self.root/'run',Runner(),adviser=advise)
        self.assertEqual(out['status'],'uncertain');self.assertEqual(out['job_id'],'b'*32)
        self.assertEqual(len(calls),1)
    def test_readonly_write_action_rejected(self):
        bad=action('replace_text',path='tests/test_code.py',expected_sha256='0'*64,old='#',new='')
        out,_,runner=self.run_actions([bad]+[action('finish',summary='PASS')]*5)
        self.assertEqual(out['status'],'failed');self.assertEqual(out['changes'],[])
        self.assertEqual(len(runner.calls),1)

if __name__=='__main__':unittest.main()
