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

"""Offline regressions: no Docker, model, service or production access."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch
from . import build as m

ROOT = Path(__file__).resolve().parents[2]

class BuildTests(unittest.TestCase):
    def probe_result(self):
        rows=[]
        for name in m.CORE_FILES:
            relative='inference/laf_service/'+name
            raw=(ROOT/relative).read_bytes()
            rows.append({'path':relative,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'mode':'0o644'})
        return {'uid':1000,'gid':1000,'imports_ok':True,'no_host_authority_imported':True,'files':rows}

    def test_context_modes_and_hashes_are_independent_of_umask(self):
        originals={str(p):stat.S_IMODE(p.stat().st_mode) for p in (ROOT/'inference/laf_service').glob('*.py')}
        for mask in (0o022,0o077,0o027):
            with self.subTest(umask=oct(mask)), tempfile.TemporaryDirectory() as d:
                root=Path(d);before=stat.S_IMODE(root.stat().st_mode);old=os.umask(mask)
                try:m.prepare_context(ROOT,root)
                finally:os.umask(old)
                files=[p for p in root.rglob('*') if p.is_file()]
                self.assertEqual(len(files),len(m.CORE_FILES)+1)
                self.assertEqual(stat.S_IMODE(root.stat().st_mode),before)
                for p in files:
                    self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o644)
                    self.assertEqual(p.read_bytes(),(ROOT/p.relative_to(root)).read_bytes())
                for p in root.rglob('*'):
                    if p.is_dir():self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o755)
        self.assertEqual(originals,{str(p):stat.S_IMODE(p.stat().st_mode) for p in (ROOT/'inference/laf_service').glob('*.py')})

    def test_context_refuses_existing_content_and_symlink_destination(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);real=root/'real';real.mkdir();evidence=real/'private.txt';evidence.write_text('unchanged');evidence.chmod(0o600)
            alias=root/'alias';alias.symlink_to(real,target_is_directory=True)
            for destination in (real,alias):
                with self.subTest(destination=destination), self.assertRaises(ValueError):m.prepare_context(ROOT,destination)
            self.assertEqual(evidence.read_text(),'unchanged');self.assertEqual(stat.S_IMODE(evidence.stat().st_mode),0o600)

    def test_probe_uses_immutable_id_uid1000_and_no_host_access(self):
        value=self.probe_result()
        with patch.object(m.subprocess,'run',return_value=types.SimpleNamespace(stdout=json.dumps(value))) as run:
            self.assertEqual(m.image_readability_probe('sha256:'+'a'*64),value)
        args=run.call_args.args[0]
        for option in ['--rm','--pull=never','--network=none','--user=1000:1000','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges:true','--ipc=none','--memory=128m','--memory-swap=128m','--cpus=1','--pids-limit=32','--entrypoint=python3']:
            self.assertIn(option,args)
        self.assertFalse(any(x.startswith(('--device','--mount','--volume','--privileged','--network=host')) for x in args))
        self.assertTrue(any('signal.alarm(20)' in arg for arg in args))
        self.assertEqual(run.call_args.kwargs['timeout'],30)

    def test_probe_rejects_mutable_or_invalid_image_without_docker(self):
        with patch.object(m.subprocess,'run') as run:
            for value in ['laf-core-local:test','sha256:x','sha256:'+'a'*65,'-danger']:
                with self.subTest(value=value), self.assertRaises(ValueError):m.image_readability_probe(value)
            run.assert_not_called()

    def test_probe_fails_closed_on_runtime_uid_or_import_failure(self):
        for key,bad in [('uid',0),('gid',0),('imports_ok',False)]:
            value=self.probe_result();value[key]=bad
            with self.subTest(key=key), patch.object(m.subprocess,'run',return_value=types.SimpleNamespace(stdout=json.dumps(value))):
                with self.assertRaises(ValueError):m.image_readability_probe('sha256:'+'a'*64)

    def test_probe_source_check_accepts_exact_result(self):m.verify_probe_sources(self.probe_result(),ROOT)

    def test_probe_source_check_rejects_wrong_set_bytes_hash_mode_or_authority(self):
        mutations=[lambda x:x['files'].pop(),lambda x:x['files'].append(x['files'][0]),lambda x:x['files'][0].update(path='../../unrelated'),lambda x:x['files'][0].update(bytes=0),lambda x:x['files'][0].update(sha256='a'*64),lambda x:x['files'][0].update(mode='0o600'),lambda x:x.update(no_host_authority_imported=False)]
        for mutate in mutations:
            value=self.probe_result();mutate(value)
            with self.subTest(mutation=mutations.index(mutate)),self.assertRaises(ValueError):m.verify_probe_sources(value,ROOT)

    def test_readability_gate_failure_propagates_after_build(self):
        responses=[types.SimpleNamespace(stdout='[{}]'),types.SimpleNamespace(stdout=''),types.SimpleNamespace(stdout=json.dumps([{'Id':'sha256:'+'a'*64}]))]
        with patch('sys.argv',['build.py','--base','repo@sha256:'+'b'*64,'--tag','laf-core-local:test']),patch.object(m.subprocess,'run',side_effect=responses) as run,patch.object(m,'image_readability_probe',side_effect=ValueError('gate failed')) as probe:
            with self.assertRaisesRegex(ValueError,'gate failed'):m.main()
        self.assertEqual(run.call_count,3);probe.assert_called_once_with('sha256:'+'a'*64)

if __name__=='__main__':unittest.main()
