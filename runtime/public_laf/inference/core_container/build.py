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

"""Offline-only source-image build wrapper. No deployment, pull or host change."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import shutil
import stat
import tempfile


def validate_base(value):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._/:\-]*@sha256:[0-9a-f]{64}', value):
        raise ValueError('inspected immutable base reference required')
    return value



CORE_FILES = (
    '__init__.py', 'core.py', 'server.py', 'npu_bridge.py', 'lifecycle.py',
    'lifecycle_client.py', 'lifecycle_uds.py', 'core_backends.py',
    'core_flash_worker.py', 'core_server.py', 'swap_window.py',
)


def prepare_context(root, destination):
    root, destination = Path(root), Path(destination)
    if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
        raise ValueError('fresh empty non-symlink build-context directory required')
    allowed = [Path('inference/laf_service') / name for name in CORE_FILES]
    dockerfile = Path('inference/core_container/Dockerfile')
    copies = [line.split()[1:] for line in (root / dockerfile).read_text().splitlines()
              if line.startswith('COPY ')]
    if copies != [[str(path), str(path)] for path in allowed]:
        raise ValueError('Core Dockerfile COPY allowlist changed')
    for path in [*allowed, dockerfile]:
        source = root / path; info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('regular unlinked Core build source required')
        target = destination / path
        # Only newly created, allowlisted context paths get normalized. Leave
        # original source, private evidence and the temporary root unchanged.
        for parent in reversed(path.parents):
            if parent == Path('.'):
                continue
            directory = destination / parent
            if not directory.exists():
                directory.mkdir(mode=0o755)
                directory.chmod(0o755)
        shutil.copyfile(source, target)
        target.chmod(0o644)
    return destination / dockerfile



def image_readability_probe(image):
    """Read/import code as runtime UID in a disposable, offline, device-free container."""
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('immutable local image ID required for readability preflight')
    paths = ['inference/laf_service/' + name for name in CORE_FILES]
    script = """import hashlib,importlib,json,os,pathlib,signal,stat,sys
signal.alarm(20)
assert (os.getuid(),os.getgid()) == (1000,1000), 'runtime UID/GID required'
sys.path.insert(0,'/app')
rows=[]
for relative in json.loads(sys.argv[1]):
    p=pathlib.Path('/app')/relative
    st=p.lstat()
    assert stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode)==0o644, 'unexpected source type/mode'
    raw=p.read_bytes()
    compile(raw,str(p),'exec')
    rows.append({'path':relative,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'mode':oct(stat.S_IMODE(st.st_mode))})
for row in rows:
    name=row['path'][:-3].replace('/','.')
    if name.endswith('.__init__'): name=name[:-9]
    importlib.import_module(name)
assert not {'inference.laf_service.backends','inference.laf_service.lifecycle_host','inference.laf_service.host_supervisor'} & set(sys.modules), 'host authority imported'
print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'files':rows,'imports_ok':True,'no_host_authority_imported':True}))
"""
    result = subprocess.run([
        'docker', '--host=unix:///var/run/docker.sock', 'run', '--rm', '--pull=never',
        '--network=none', '--user=1000:1000', '--read-only', '--cap-drop=ALL',
        '--security-opt=no-new-privileges:true', '--ipc=none', '--memory=128m',
        '--memory-swap=128m', '--cpus=1', '--pids-limit=32', '--entrypoint=python3',
        image, '-I', '-B', '-c', script, json.dumps(paths),
    ], check=True, capture_output=True, text=True, timeout=30)
    value = json.loads(result.stdout)
    if value.get('uid') != 1000 or value.get('gid') != 1000 or value.get('imports_ok') is not True:
        raise ValueError('unprivileged source preflight failed')
    return value


def verify_probe_sources(value, root):
    if (value.get('uid'), value.get('gid')) != (1000, 1000) or value.get('imports_ok') is not True or value.get('no_host_authority_imported') is not True:
        raise ValueError('unprivileged import verification failed')
    expected = {'inference/laf_service/' + name for name in CORE_FILES}
    rows = value.get('files', [])
    if len(rows) != len(expected) or {row.get('path') for row in rows} != expected:
        raise ValueError('readability probe source set differs from reviewed Core')
    for row in rows:
        raw = (Path(root) / row['path']).read_bytes()
        if row.get('bytes') != len(raw) or row.get('sha256') != hashlib.sha256(raw).hexdigest() or row.get('mode') != '0o644':
            raise ValueError('readability probe source bytes/mode mismatch')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    base = validate_base(args.base)
    if not re.fullmatch(r'laf-core-local:[a-zA-Z0-9_.-]+', args.tag):
        raise ValueError('explicit laf-core-local test tag required')
    root = Path(__file__).resolve().parents[2]
    # inspect is read-only and fails if this exact reference is not already local.
    result = subprocess.run(['docker', '--host=unix:///var/run/docker.sock', 'image', 'inspect', base], check=True, capture_output=True, text=True)
    if not json.loads(result.stdout):
        raise ValueError('base image unavailable locally')
    with tempfile.TemporaryDirectory(prefix='laf-core-build-') as directory:
        dockerfile = prepare_context(root, directory)
        subprocess.run(['docker', '--host=unix:///var/run/docker.sock', 'build', '--pull=false', '--network=none', '--build-arg',
                        'LAF_CORE_BASE=' + base, '-f', str(dockerfile),
                        '-t', args.tag, directory], check=True)
    # Refuse an unreadable candidate before any maintenance caller can proceed.
    inspected = subprocess.run(['docker', '--host=unix:///var/run/docker.sock', 'image',
                                'inspect', args.tag], check=True, capture_output=True, text=True)
    image = json.loads(inspected.stdout)[0]['Id']
    probe = image_readability_probe(image)
    verify_probe_sources(probe, root)
    print(json.dumps({'result': 'PASS_UNPRIVILEGED_SOURCE_PREFLIGHT', 'image': image, 'probe': probe}))


if __name__ == '__main__': main()
