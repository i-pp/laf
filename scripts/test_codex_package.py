#!/usr/bin/env python3
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

# Added 2026-11-12: integrated source-tree archive preparation.
# Modified 2026-11-21 to cover the private workspace package.
# Modified 2026-11-21 to verify the shared Python support minimum.

"""Pure metadata contracts for Codex packaging; never installs or connects."""
import ast,io,json,runpy,struct,sys,tempfile,tomllib,unittest,zipfile
from contextlib import redirect_stderr,redirect_stdout
from pathlib import Path
from unittest.mock import patch
import build_workspace_plugin as workspace
import check_release_names as names
ROOT=Path(__file__).resolve().parents[1]
def read(name):return json.loads((ROOT/name).read_text())
class CodexPackage(unittest.TestCase):
 def test_package_python_floor_and_unsupported_frontend_startup(self):
  for manifest in ('pyproject.toml','runtime/public_laf/pyproject.toml','mcp/pyproject.toml'):
   with self.subTest(manifest=manifest):
    project=tomllib.loads((ROOT/manifest).read_text())['project']
    self.assertEqual(project['requires-python'],'>=3.12')
  for path in ('launcher.py','packaging/portable_launcher.py','runtime/integration/laf_stdio_cli.py','runtime/integration/laf_bootstrap.py'):
   with self.subTest(path=path):
    main=runpy.run_path(str(ROOT/path))['main']
    errors=io.StringIO()
    with patch.object(sys,'version_info',(3,11,9)),redirect_stderr(errors),redirect_stdout(io.StringIO()):
     self.assertEqual(main(['--help']),1)
    self.assertIn('Python 3.12 or later',errors.getvalue())
  self.assertEqual((ROOT/'launcher.py').read_bytes(),(ROOT/'packaging/portable_launcher.py').read_bytes())
 def test_release_identity_and_both_protocol_versions(self):
  self.assertEqual(read('plugin.json')['version'],'0.1.0')
  for name in ('mcp_adapter.py','legacy_tool_adapter.py'):
   tree=ast.parse((ROOT/'runtime/private_codex/src'/name).read_text())
   values=[ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SERVER_INFO' for t in n.targets)]
   self.assertEqual(len(values),1);self.assertEqual(values[0]['version'],'0.1.0')
 def test_codex_compatibility_manifest_matches_portable_identity(self):
  portable=read('plugin.json');compat=read('.codex-plugin/plugin.json')
  for key in ('name','version','description','author'):self.assertEqual(compat[key],portable[key])
  self.assertEqual(compat['interface'],portable['extensions']['com.openai']['interface'])
  self.assertEqual(portable['author']['name'],'I-plusplus Inc.')
  self.assertEqual(compat['interface']['developerName'],portable['author']['name'])
  icon=compat['interface']['logo']
  self.assertEqual(icon,'./assets/laf-neural-fabric.png')
  self.assertEqual(compat['interface']['composerIcon'],icon)
  image=(ROOT/icon).read_bytes()
  self.assertEqual(image[:8],b'\x89PNG\r\n\x1a\n')
  self.assertEqual(image[12:16],b'IHDR')
  self.assertEqual(struct.unpack('>IIBB',image[16:26]),(512,512,8,6))
  self.assertEqual(compat['mcpServers'],'./.mcp.json');self.assertEqual(compat['skills'],'./skills')
  self.assertNotIn('apps',compat)
 def test_model_setup_skill_is_packaged_in_both_forms(self):
  portable=read('plugin.json')['extensions']['com.openai']
  compat=read('.codex-plugin/plugin.json')
  skill='./skills/laf-model-setup/SKILL.md'
  self.assertEqual(portable['onboardingSkill'],skill)
  self.assertEqual(compat['onboardingSkill'],skill)
  self.assertEqual((ROOT/skill).read_bytes(),(ROOT/'runtime/public_laf'/skill).read_bytes())
  names={p for rule in read('.github/license-policy.json')['rules'] for p in rule['paths']}
  self.assertIn(Path(skill).as_posix(),names)
  bundled=ast.parse((ROOT/'runtime/public_laf/scripts/check-laf.py').read_text())
  modules=next(node.value for node in bundled.body if isinstance(node,ast.Assign)
               and any(isinstance(target,ast.Name) and target.id=='MODULES' for target in node.targets))
  self.assertIn('inference.local_platform.test_model_setup',ast.literal_eval(modules))
 def test_workspace_zip_kinds_and_version_match_without_direct_mcp(self):
  with tempfile.TemporaryDirectory() as tmp:
   directory=Path(tmp)
   binding=directory/'binding.json'
   binding.touch(mode=0o600)
   mapping={'apps':{'laf':{'id':'asdk_app_' + 'generated-fixture','required':True}}}
   binding.write_text(json.dumps(mapping))
   output=directory/'laf-workspace-0.0.1.zip'
   self.assertEqual(workspace.build(ROOT,binding,output),{'version':'0.0.1','files':10,'app_references':1})
   self.assertEqual(output.stat().st_mode & 0o777,0o600)
   self.assertEqual(names.check_zip(output),10)
   with zipfile.ZipFile(output) as archive:
    self.assertIsNone(archive.testzip())
    self.assertEqual(set(archive.namelist()),set(workspace.PACKAGED)|{'plugin.json','.codex-plugin/plugin.json','.app.json'})
    portable=json.loads(archive.read('plugin.json'));compat=json.loads(archive.read('.codex-plugin/plugin.json'))
    extension=portable['extensions']['com.openai']
    for key in ('name','version','description','author'):self.assertEqual(portable[key],compat[key])
    self.assertEqual(portable['version'],'0.0.1')
    self.assertEqual(extension['apps'],compat['apps'])
    self.assertEqual(extension['apps'],'./.app.json')
    self.assertEqual(extension['interface'],compat['interface'])
    self.assertEqual(extension['onboardingSkill'],compat['onboardingSkill'])
    self.assertNotIn('mcpServers',compat)
    self.assertEqual(json.loads(archive.read('.app.json')),mapping)
    for path in workspace.PACKAGED:self.assertEqual(archive.read(path),(ROOT/path).read_bytes())
   previous=output.read_bytes()
   with self.assertRaises(ValueError):workspace.build(ROOT,binding,output)
   self.assertEqual(output.read_bytes(),previous)
 def test_workspace_builder_rejects_ambiguous_and_untrusted_binding(self):
  with tempfile.TemporaryDirectory() as tmp:
   directory=Path(tmp)
   binding=directory/'binding.json'
   binding.touch(mode=0o600)
   output=directory/'laf-workspace.zip'
   for value in (
       '{"apps":{},"apps":{}}',
       json.dumps({'apps':{'laf':{'id':'<VERIFIED_APP_ID>','required':True}}}),
       json.dumps({'apps':{'laf':{'id':'tunnel_' + 'test','required':True}}}),
       json.dumps({'apps':{'laf':{'id':'asdk_app_' + 'sample','required':False}}}),
       json.dumps({'apps':{'laf':{'id':'asdk_app_' + 'sample','required':True},'second':{}}}),
   ):
    binding.write_text(value)
    with self.subTest(value=value),self.assertRaises(ValueError):workspace.build(ROOT,binding,output)
    self.assertFalse(output.exists())
   binding.write_text(json.dumps({'apps':{'laf':{'id':'asdk_app_' + 'sample','required':True}}}))
   link=directory/'alias.json'
   link.symlink_to(binding)
   with self.assertRaises(ValueError):workspace.build(ROOT,link,output)
   with self.assertRaises(ValueError):workspace.build(ROOT,binding,ROOT/'private-output.zip')
   binding.chmod(0o644)
   with self.assertRaises(ValueError):workspace.build(ROOT,binding,output)
   self.assertFalse(output.exists())
 def test_codex_mcp_is_local_stdio_without_auth_or_auto_install(self):
  self.assertEqual(read('.mcp.json'),{'mcpServers':{'laf':{'command':'python3','args':['-B','./launcher.py'],'cwd':'.'}}})
  self.assertTrue((ROOT/'launcher.py').is_file())
 def test_marketplace_source_contained_and_classified(self):
  market=read('.agents/plugins/marketplace.json')
  self.assertEqual(market['name'],'laf-experimental')
  self.assertEqual(len(market['plugins']),1)
  self.assertEqual(market['plugins'][0],{'name':'laf','source':{'source':'local','path':'.'},'policy':{'installation':'AVAILABLE','authentication':'ON_USE'},'category':'Developer Tools'})
  names=[p for rule in read('.github/license-policy.json')['rules'] for p in rule['paths']]
  self.assertIn(Path(read('.codex-plugin/plugin.json')['interface']['logo']).as_posix(),names)
  for p in ('.codex-plugin/plugin.json','.mcp.json','.agents/plugins/marketplace.json','docs/codex-install.md','scripts/test_codex_package.py'):self.assertIn(p,names)
 def test_release_gate_rejects_retired_visible_path_and_content(self):
  retired='aCe'+'Is'
  for path,payload in (
      (retired+'-inference.service',b'[Unit]'),
      ('.mcp.json',json.dumps({'tools':[retired+'_local_model_prepare']}).encode()),
      ('plugin.json',json.dumps({'displayName':retired}).encode()),
      ('laf.env',(retired.upper()+'_OAUTH_ISSUER=value').encode()),
      ('README.md',f'Running {retired} by default'.encode())):
   with self.subTest(path=path),self.assertRaises(names.NamingError):
    names.check_member(path,payload)
 def test_zip_requires_exact_attribution_and_laf_artifact_name(self):
  preserved=(ROOT/'NOTICE').read_bytes()
  retired='aCe'+'Is'
  with tempfile.TemporaryDirectory() as tmp:
   directory=Path(tmp)
   for number,(basename,notice,comment) in enumerate((
       ('laf-source.zip',preserved,b''),
       ('laf-changed.zip',preserved+b'changed',b''),
       (retired+'.zip',preserved,b''),
       ('laf-comment.zip',preserved,retired.encode()))):
    target=directory/basename
    with zipfile.ZipFile(target,'w') as archive:
     archive.comment=comment
     archive.writestr('NOTICE',notice)
     archive.writestr('launcher.py','pass')
    if number==0:self.assertEqual(names.check_zip(target),2)
    else:
     with self.assertRaises(names.NamingError):names.check_zip(target)
 def test_fresh_install_checks_names_and_symlink_targets(self):
  with tempfile.TemporaryDirectory() as tmp:
   prefix=Path(tmp)
   binary=prefix/'laf-server'
   binary.touch()
   unit=prefix/'laf-inference.service'
   unit.symlink_to(binary.name)
   self.assertEqual(names.check_tree(prefix,names_only=True),2)
   unit.unlink()
   unit.symlink_to('AcE'+'iS-inference.service')
   with self.assertRaises(names.NamingError):names.check_tree(prefix,names_only=True)
if __name__=='__main__':unittest.main(verbosity=2)
