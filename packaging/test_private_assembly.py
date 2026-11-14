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

# Added 2026-11-12: original portable integrated package preparation.

"""Synthetic ZIP/files only. No imported runtime is executed."""
import hashlib,json,os
from pathlib import Path
import tempfile,unittest,zipfile
import assemble_private_plugin as p
class Assembly(unittest.TestCase):
 def test_stable_stdio_manifest(self):
  m=p.mcp_manifest()['mcpServers']['laf'];self.assertEqual(m['type'],'stdio');self.assertEqual(m['args'],['-B','${PLUGIN_ROOT}/launcher.py']);self.assertNotIn('url',m)
 def test_no_private_app_binding(self):
  m=p.manifest();self.assertNotIn('apps',m);self.assertNotIn('com.openai.apps',m['extensions'])
 def test_complete_both_entrypoints_required(self):
  self.assertIn('integration/launch.py',p.REQUIRED);self.assertIn('integration/laf_stdio_cli.py',p.REQUIRED)
 def test_runtime_and_evidence_excluded(self):
  for path in ['private_codex/.runtime/key.json','evidence/result.json','private_codex/tests/real.py','integration/laf_shadow_child.py','public_laf/models/model.bin','public_laf/mcp/.venv/bin/python']:
   self.assertFalse(p.selected(path),path)
 def test_source_and_licenses_included(self):
  for path in p.REQUIRED:self.assertTrue(p.selected(path),path)
 def test_core_router_cli_skills_required(self):
  for path in ('inference/laf_service/core.py','inference/laf_service/server.py','inference/laf_service/host_supervisor.py','inference/local_platform/cli.py','laf','skills/laf-router/SKILL.md'):
   self.assertIn('public_laf/'+path,p.REQUIRED);self.assertTrue(p.selected('public_laf/'+path))
 def test_private_bindings_binaries_and_owner_test_excluded(self):
  for path in ('public_laf/.app.json','public_laf/inference/evidence/log.json','public_laf/inference/weights.bin','public_laf/models/a.py','private_codex/tests/test_runtime_scope.py'):
   self.assertFalse(p.selected(path),path)
 def test_only_public_executable_allowlist(self):
  for name in ('runtime/public_laf/laf','runtime/public_laf/inference/laf_npu_service/build.sh'):
   self.assertEqual(p.archive_mode(name),0o100755)
  for name in ('launcher.py','runtime/integration/launch.py','config.json','runtime/public_laf/inference/other.sh','other/laf'):
   self.assertEqual(p.archive_mode(name),0o100600)
 def test_archive_metadata_preserves_staging_modes_and_bytes(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=Path(d);stage=root/'stage';stage.mkdir()
   names=list(p.EXECUTABLE_ARCHIVE_PATHS)+['operator.json','launcher.py']
   for name in names:
    f=stage/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b'synthetic');f.chmod(0o600)
   out=root/'candidate.zip';p.archive_private_candidate(stage,out)
   with zipfile.ZipFile(out) as z:
    for name in names:
     self.assertEqual(z.getinfo(name).external_attr>>16,p.archive_mode(name));self.assertEqual(z.read(name),b'synthetic');self.assertEqual((stage/name).stat().st_mode & 0o777,0o600)
 def fixture(self,folder,extra=None,missing=None):
  z=folder/'source.zip'
  with zipfile.ZipFile(z,'w') as f:
   for path in p.REQUIRED:
    if path!=missing:f.writestr('stdio/'+path,'# synthetic source only\n')
   if extra:f.writestr(extra,'# no code\n')
  return z,hashlib.sha256(z.read_bytes()).hexdigest()
 def test_digest_mismatch_refused(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   z,_=self.fixture(Path(d))
   with self.assertRaises(p.PackageError):p.source_members(z,'0'*64)
 def test_missing_runtime_refused(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   z,h=self.fixture(Path(d),missing='integration/launch.py')
   with self.assertRaises(p.PackageError):p.source_members(z,h)
 def test_traversal_refused_before_selection(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   z,h=self.fixture(Path(d),extra='unselected/../../bad')
   with self.assertRaises(p.PackageError):p.source_members(z,h)
 def test_duplicate_member_refused(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   z,h=self.fixture(Path(d),extra='stdio/integration/launch.py')
   with self.assertRaises(p.PackageError):p.source_members(z,h)
 def test_actual_git_worktree_output_refused(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=Path(d);z,h=self.fixture(root);(root/'.git').write_text('gitdir: /outside')
   with self.assertRaises(p.PackageError):p.assemble(z,h,root/'out',root)
 def test_actual_git_directory_output_refused(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=Path(d);z,h=self.fixture(root);(root/'.git').mkdir();(root/'.git/HEAD').write_text('ref: refs/heads/main')
   with self.assertRaises(p.PackageError):p.assemble(z,h,root/'out',root)
 def test_synthetic_private_assembly_and_existing_output_retained(self):
  with tempfile.TemporaryDirectory(dir=Path.home()) as d:
   root=Path(d);z,h=self.fixture(root);templates=root/'templates';templates.mkdir();(templates/'portable_launcher.py').write_text('# synthetic');(templates/'INTEGRATED_PLUGIN_GUIDE.md').write_text('not ready');(templates/'upstream-orchestrator').mkdir();[(templates/'upstream-orchestrator'/n).write_text('synthetic notice') for n in ('LICENSE','NOTICE')];[(templates/n).write_text('synthetic changes') for n in ('CHANGES.md','SOURCE_ORIGIN.md')]
   out=root/'assembled';r=p.assemble(z,h,out,templates)
   self.assertFalse(r['submission_ready']);self.assertEqual(json.loads((out/'mcp.json').read_text()),p.mcp_manifest());self.assertEqual((out/'runtime/integration/launch.py').read_text(),'# synthetic source only\n')
   with self.assertRaises(p.PackageError):p.assemble(z,h,out,templates)
   self.assertTrue((out/'ASSEMBLY_STATUS.json').exists())
 # Bootstrap assets are optional for historical inputs, complete when selected.
 def test_bootstrap_selection_includes_policy_not_runtime_state(self):
  for name in ('integration/laf_bootstrap.py','private_codex/src/backend_state.py','private_codex/policy-snapshot/docs/ai/orchestration.md','private_codex/policy-snapshot/LICENSE','private_codex/policy-snapshot/manifest.json'):
   self.assertTrue(p.selected(name),name)
  for name in ('private_codex/.runtime/auth.json','private_codex/policy-snapshot/auth.json'):
   self.assertFalse(p.selected(name),name)
if __name__=='__main__':unittest.main(verbosity=2)
