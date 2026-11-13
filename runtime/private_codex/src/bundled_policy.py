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

# Scope: this newly authored file only; imported private source is not relicensed.
"""Versioned public policy snapshot; no legacy rewrite, Git or network probe."""
import hashlib
import json
import re
from pathlib import Path
from common_policy import PolicyError, BundledPolicyBundle, _json, _path, _utf8
from upstream_policy import FILES, assemble, checked_document

COMMIT='ddcaf63e50bed722d558d418deeef791f3029a6f'
SHA256='beccfd5c9aeb63d6a06f33ef39732e832e7f4e79b0a7915829ca993b55347236'
REPOSITORY='i-pp/ai-orchestration'
LICENSE_SHA256='df951a913ed6f5fe319b9e1b1a3cc2b6e396ea65b9d1687b9f735bd8f117ea9c'
NOTICE_SHA256='ca7477f0f5ab056d1fa5578df9379abddd5886899ba7aba32bce222831b58d21'

def _read(path):
 path=_path(path)
 if not 0<path.stat().st_size<=65536:raise PolicyError('Snapshot file exceeds its size bound')
 raw=path.read_bytes()
 if not 0<len(raw)<=65536:raise PolicyError('Snapshot file exceeds its size bound')
 return raw

def load_snapshot(project,config):
 if (set(config)!={'schema_version','source','bundle_root','expected_version','expected_sha256'}
     or config['schema_version']!=2 or config['source']!='bundled_snapshot'
     or config['bundle_root']!='policy-snapshot' or config['expected_version']!='git:'+COMMIT
     or config['expected_sha256']!=SHA256):raise PolicyError('Unsupported bundled policy selection')
 root=_path(Path(project)/'policy-snapshot',directory=True)
 pin=_json(root/'manifest.json',{'schema_version','repository','commit','version','sha256','files','license','notice'})
 if (pin['repository']!=REPOSITORY or pin['commit']!=COMMIT or pin['version']!='git:'+COMMIT
     or pin['sha256']!=SHA256 or type(pin['files']) is not dict or set(pin['files'])!=set(FILES)):
  raise PolicyError('Bundled policy manifest differs from its public pin')
 documents={}
 for name in FILES:
  entry=pin['files'][name]
  if (type(entry) is not dict or set(entry)!={'sha256','git_blob_sha'}
      or type(entry['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}',entry['sha256'])
      or type(entry['git_blob_sha']) is not str or not re.fullmatch('[0-9a-f]{40}',entry['git_blob_sha'])):raise PolicyError('Invalid bundled document provenance')
  documents[name]=checked_document(_read(root/name),entry)
 for field,wanted in (('license',LICENSE_SHA256),('notice',NOTICE_SHA256)):
  entry=pin[field];name='LICENSE' if field=='license' else 'NOTICE'
  if type(entry) is not dict or set(entry)!={'file','sha256','git_blob_sha'} or entry['file']!=name or entry['sha256']!=wanted:
   raise PolicyError('Bundled policy license provenance differs from its pin')
  checked_document(_read(root/name),{k:entry[k] for k in ('sha256','git_blob_sha')})
 text=assemble(documents)
 if hashlib.sha256(text.encode()).hexdigest()!=SHA256:raise PolicyError('Bundled policy content mismatch')
 provenance={'repository':REPOSITORY,'commit':COMMIT,'source_proof':'bundled_snapshot',
             'sha256':SHA256,'automatic_network':False,'git_checkout_attested':False,
             'files':pin['files'],'document_bytes':{name:len(documents[name].encode()) for name in FILES},'license_sha256':LICENSE_SHA256,'notice_sha256':NOTICE_SHA256}
 return BundledPolicyBundle(text,'git:'+COMMIT,SHA256,'https://github.com/'+REPOSITORY+'/tree/'+COMMIT,json.dumps(provenance,sort_keys=True))

def check_bundle(bundle):
 if (bundle.version!='git:'+COMMIT or bundle.sha256!=SHA256
     or bundle.source!='https://github.com/'+REPOSITORY+'/tree/'+COMMIT
     or hashlib.sha256(bundle.text.encode()).hexdigest()!=SHA256):raise PolicyError('Bundled policy content mismatch')
 try:meta=json.loads(bundle.provenance_json)
 except (ValueError,TypeError):raise PolicyError('Invalid bundled policy provenance') from None
 if (type(meta) is not dict or set(meta)!={'repository','commit','source_proof','sha256','automatic_network','git_checkout_attested','files','document_bytes','license_sha256','notice_sha256'}
     or meta.get('source_proof')!='bundled_snapshot' or meta.get('git_checkout_attested') is not False
     or meta.get('automatic_network') is not False or meta.get('commit')!=COMMIT
     or meta.get('repository')!=REPOSITORY or meta.get('sha256')!=SHA256
     or meta.get('license_sha256')!=LICENSE_SHA256 or meta.get('notice_sha256')!=NOTICE_SHA256):
  raise PolicyError('Invalid bundled policy provenance')

 files=meta.get('files');sizes=meta.get('document_bytes')
 if type(files) is not dict or set(files)!=set(FILES) or type(sizes) is not dict or set(sizes)!=set(FILES):
  raise PolicyError('Invalid bundled document provenance')
 raw=bundle.text.encode();position=0
 for name in FILES:
  entry=files[name];size=sizes[name];marker=('--- UPSTREAM '+name+' ---\n').encode()
  if (type(entry) is not dict or set(entry)!={'sha256','git_blob_sha'} or type(size) is not int or not 0<size<=65536
      or raw[position:position+len(marker)]!=marker):raise PolicyError('Invalid bundled document provenance')
  position+=len(marker);part=raw[position:position+size];checked_document(part,entry);position+=size
  if not part.endswith(b'\n'):
   if raw[position:position+1]!=b'\n':raise PolicyError('Invalid bundled section boundary')
   position+=1
 if position!=len(raw):raise PolicyError('Unexpected bundled policy content')
