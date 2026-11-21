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

import json
from pathlib import Path
import tempfile
import unittest
from .bounded_patch import canonical_source, validated_replacement
from .repair import checked_cases, repair, run_cases
from .cli import ClientError

ORIGINAL='def calculate(a, b):\n    return a - b\n'
GOOD='def calculate(a, b):\n    return a + b\n'
CASES=[{'function':'calculate','args':[2,3],'expected':5}]


class TestGrammar(unittest.TestCase):
    def test_does_not_encode_correct_answer(self):
        self.assertIn('a - b',canonical_source(ORIGINAL,ORIGINAL))
        self.assertIn('a + b',canonical_source(GOOD,ORIGINAL))
        self.assertIn('a * b',canonical_source(GOOD.replace('+','*'),ORIGINAL))
    def test_no_capability_expansion(self):
        bad=['import os\n'+GOOD, GOOD+'\nprint(1)',
             GOOD.replace('a + b','a.__class__'), GOOD.replace('a + b','eval(a)'),
             GOOD.replace('a + b','calculate(a,b)'), GOOD.replace('a + b','a ** b'),
             GOOD.replace('calculate(a, b)','calculate(a, b=1)'),
             GOOD.replace('calculate(a, b)','calculate(a, min)'),
             'def calculate(a,b):\n    min = a\n    return min(b)\n',
             '@staticmethod\n'+GOOD,GOOD.replace('calculate','other'),
             'def calculate(a,b):\n    while True: pass\n',
             GOOD.replace('a + b','open(a)'), GOOD.replace('a + b','globals()'),
             GOOD.replace('a + b','[x for x in a]'),
             GOOD.replace('a + b','1e999'),
             'def calculate(a,b):\n    def min(a):\n        return min(a)\n    return min(a)\n',
             'def calculate(a,b):\n    @min\n    def nested(x):\n        return x\n    return a\n',
             'def calculate(a,b):\n    def nested(x=min(a,b)):\n        return x\n    return a\n']
        for source in bad:
            with self.subTest(source=source):
                with self.assertRaises(ValueError):canonical_source(source,ORIGINAL)
    def test_if_and_local_assignment(self):
        source='def calculate(a,b):\n    c = a + b\n    if c > 10:\n        return 10\n    return c\n'
        self.assertIn('if c > 10:',canonical_source(source,ORIGINAL))
    def test_bad_cases(self):
        for cases in [[],[{'function':[],'args':[],'expected':1}],
                      [{'function':'calculate','args':[1],'expected':2}],
                      [{'function':'calculate','args':[1,2],'expected':float('nan')}]]:
            with self.assertRaises(ValueError):checked_cases(cases,ORIGINAL)
    def test_json_schema(self):
        for value in [{}, {'replacement':GOOD,'explanation':[]},
                      {'replacement':GOOD,'explanation':'ok','extra':1}]:
            with self.assertRaises(ValueError):validated_replacement(json.dumps(value),ORIGINAL)


class TestRepair(unittest.TestCase):
    def test_failed_test_then_model_fix(self):
        calls=[]
        def adviser(*args,**kwargs):
            calls.append(args[2])
            source=ORIGINAL if len(calls)==1 else GOOD
            return {'job_id':'a'*32,'content':json.dumps({'replacement':source,'explanation':'candidate'})}
        with tempfile.TemporaryDirectory() as parent:
            root=Path(parent)/'new'
            out=repair('/unused',ORIGINAL,CASES,root,attempts=2,adviser=adviser)
            self.assertEqual(out['before']['exit_code'],1)
            self.assertTrue(out['tests_passed'])
            self.assertEqual(len(out['attempts']),2)
            self.assertEqual(out['attempts'][0]['tests']['exit_code'],1)
            self.assertIn('expected value mismatch',calls[1])
            self.assertEqual((root/'source.before.py.txt').read_text(),canonical_source(ORIGINAL))
            self.assertEqual((root/'candidate.py').read_text(),canonical_source(GOOD))
    def test_unknown_generation_does_not_retry(self):
        calls=[]
        def adviser(*a,**k):
            calls.append(a);raise ClientError('unknown','b'*32)
        with tempfile.TemporaryDirectory() as parent:
            out=repair('/unused',ORIGINAL,CASES,Path(parent)/'new',adviser=adviser)
            self.assertEqual(len(calls),1)
            self.assertFalse(out['tests_passed'])
            self.assertEqual(out['attempts'][0]['job_id'],'b'*32)
    def test_never_overwrites_existing_directory(self):
        with tempfile.TemporaryDirectory() as parent:
            with self.assertRaises(FileExistsError):repair('/unused',ORIGINAL,CASES,parent)
    def test_resource_limits_memory_bomb_is_failure(self):
        # This is pure grammar but exceeds the child-only 256MiB address space.
        source='def calculate(a,b):\n    return [0] * 100000000\n'
        with tempfile.TemporaryDirectory() as parent:
            root=Path(parent)
            (root/'candidate.py').write_text(canonical_source(source))
            (root/'cases.json').write_text(json.dumps(CASES))
            out=run_cases(root,'bounded')
            self.assertEqual(out['exit_code'],1)
            self.assertEqual(out['report']['failures'][0]['error'],'MemoryError')
    def test_initially_passing_needs_no_model(self):
        with tempfile.TemporaryDirectory() as parent:
            def adviser(*a,**k):raise AssertionError('must not call')
            out=repair('/unused',GOOD,CASES,Path(parent)/'new',adviser=adviser)
            self.assertTrue(out['tests_passed']);self.assertEqual(out['attempts'],[])

if __name__=='__main__':unittest.main()
