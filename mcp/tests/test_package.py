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

"""Build/install only our wheel offline; dependencies are not silently installed."""
import json,os,pathlib,subprocess,sys,tempfile,unittest,zipfile,shutil
ROOT=pathlib.Path(__file__).resolve().parents[2]
class PackageTests(unittest.TestCase):
 def test_manifest(self):subprocess.run([sys.executable,'-B',str(ROOT/'scripts/check-laf-plugin.py')],check=True,capture_output=True)
 def test_offline_wheel_and_isolated_entrypoint(self):
  with tempfile.TemporaryDirectory(prefix='laf mcp 雪 ') as tmp:
   tmp=pathlib.Path(tmp)
   env=dict(os.environ);env.pop('PYTHONPATH',None)
   source=tmp/'source';shutil.copytree(ROOT/'mcp',source,ignore=shutil.ignore_patterns('build','*.egg-info','__pycache__'))
   subprocess.run([sys.executable,'-m','pip','--isolated','wheel','--no-index','--no-deps','--no-build-isolation','--no-cache-dir','--wheel-dir',str(tmp/'wheels'),str(source)],check=True,capture_output=True,env=env)
   wheels=list((tmp/'wheels').glob('*.whl'));self.assertEqual(len(wheels),1)
   with zipfile.ZipFile(wheels[0]) as z:
    names=z.namelist();metadata=z.read(next(x for x in names if x.endswith('/METADATA'))).decode()
    self.assertIn('Requires-Python: >=3.10',metadata);self.assertIn('Requires-Dist: mcp==1.30.0',metadata)
    self.assertIn('License-Expression: LicenseRef-KOL-v1',metadata)
    self.assertTrue(any(x.endswith('/licenses/LICENSE') for x in names));self.assertTrue(any(x.endswith('/licenses/licenses/MCP-SDK-LICENSE.txt') for x in names))
    self.assertFalse(any('/tests/' in x or 'baseline-tree' in x or '.venv' in x for x in names))
   subprocess.run([sys.executable,'-m','venv','--without-pip',str(tmp/'venv')],check=True,capture_output=True,env=env)
   py=tmp/'venv/bin/python';exe=tmp/'venv/bin/laf-mcp'
   subprocess.run([sys.executable,'-m','pip','--isolated','--python',str(py),'install','--no-index','--no-deps',str(wheels[0])],check=True,capture_output=True,env=env)
   env=dict(os.environ);env.pop('PYTHONPATH',None)
   r=subprocess.run([str(exe),'--version'],cwd=tmp,env=env,check=True,capture_output=True,text=True);self.assertEqual(r.stdout.strip(),'0.1.0')
   r=subprocess.run([str(exe),'--help'],cwd=tmp,env=env,check=True,capture_output=True,text=True);self.assertIn('Does not install/start',r.stdout)
   r=subprocess.run([str(exe),'--allow-model','flash','--state-dir',str(tmp/'state')],cwd=tmp,env=env,capture_output=True,text=True)
   self.assertEqual(r.returncode,2);self.assertFalse((tmp/'state').exists());self.assertNotIn(str(tmp),r.stderr)
if __name__=='__main__':unittest.main()
