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

# Modified 2026-11-15: explicit resource-owned Flash identity and source boundary.

"""Offline pinned Strata tokenizer/protocol adapter. No network or tool use."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import uuid


def _pinned_config(path):
    """Read the same bounded owner-only configuration contract as Core."""
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid()
                or stat.S_IMODE(before.st_mode)!=0o600 or before.st_nlink!=1 or before.st_size>32768):
            raise ValueError('Private bounded Flash configuration required')
        raw=stream.read(32769)
        after=os.fstat(stream.fileno());current=Path(path).lstat()
        identity=lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        if len(raw)>32768 or identity(before)!=identity(after) or identity(after)!=identity(current):
            raise ValueError('Flash configuration changed during read')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate Flash configuration field')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=unique)


def main():
    if len(sys.argv) not in (2,3):raise ValueError('Fixed Flash worker arguments required')
    artifact = sys.argv[2] if len(sys.argv)==3 else None
    config = _pinned_config(sys.argv[1]) if artifact else json.loads(Path(sys.argv[1]).read_text())
    if bool(artifact) != ('resource_admission_generation' in config):
        raise ValueError('Flash resource artifact binding required')
    if artifact:
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
        from inference.laf_service.flash_resources import manifest
        if manifest(config,verify=True)!=artifact:
            raise ValueError('Flash worker artifact changed before launch')
    sys.path.insert(0, config['strata_source'])
    from tools.strata_tokenizer import Tokenizer
    from serve.frontend import ChatTemplate
    command = config['strata_command']
    tokenizer = Tokenizer.from_gguf(command[command.index('--native') + 1])
    template = ChatTemplate(Path(command[command.index('--pack') + 1]) / 'tokenizer/chat_template.jinja')
    stops = [tokenizer.encode(t, parse_special=True) for t in ['<|im_end|>', '<|endoftext|>']]
    if sorted(x[0] for x in stops if len(x) == 1) != [248044, 248046]:
        raise ValueError('pinned stop tokens do not match')
    env = {**os.environ, 'OMP_NUM_THREADS': '8', 'STRATA_IO_PREFETCH': '0',
           'STRATA_PREFILL_WARM': '0', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}
    engine = subprocess.Popen(command, cwd=config['strata_source'], env=env,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    try:
        # Capture the child identity before waiting for model readiness.
        print(json.dumps({'starting': True, 'engine_pid': engine.pid, **({'artifact_sha256':artifact} if artifact else {})}), flush=True)
        for line in engine.stdout:
            if line.startswith('READY'):
                print(json.dumps({'ready': True, 'engine_pid': engine.pid, **({'artifact_sha256':artifact} if artifact else {})}), flush=True)
                break
        else:
            raise RuntimeError('Strata exited before READY')
        for line in sys.stdin:
            body = json.loads(line)
            identity = {}
            if artifact:
                request_id=body.get('resource_request_id') if isinstance(body,dict) else None
                if (not isinstance(body,dict) or set(body)!={'messages','max_tokens','resource_request_id'}
                        or not isinstance(request_id,str) or str(uuid.UUID(request_id))!=request_id):
                    raise ValueError('Flash one-use request binding required')
                identity={'resource_request_id':request_id}
            rendered = template.render(body['messages'], tools=None, add_generation_prompt=True, enable_thinking=False)
            ids = tokenizer.encode(rendered, parse_special=True)
            n = body['max_tokens']
            if len(ids) + n + 8 > 8192:
                print(json.dumps({'error': 'context budget exceeded', **identity}), flush=True)
                continue
            engine.stdin.write('GEN ' + str(n) + ' temperature=0 ' + ','.join(map(str, ids)) + '\n')
            engine.stdin.flush()
            tokens = []
            for output in engine.stdout:
                if output.startswith('T '):
                    tokens.append(int(output.split()[1]))
                    if len(tokens) > n:
                        raise RuntimeError('Strata output exceeded token limit')
                elif output.startswith('ERR'):
                    raise RuntimeError('Strata generation error')
                elif output.startswith('DONE '):
                    fields = output.split()
                    finish = fields[5]
                    if int(fields[1]) != len(tokens) or finish not in {'stop', 'length'}:
                        raise RuntimeError('invalid terminal protocol')
                    if any(x in [248044, 248046] for x in tokens[:-1]):
                        raise RuntimeError('embedded terminal token')
                    presentation = tokens[:]
                    if finish == 'stop':
                        if not presentation or presentation.pop() not in [248044, 248046]:
                            raise RuntimeError('stop without stop token')
                    print(json.dumps({'content': tokenizer.decode(presentation, errors='strict'),
                                      'finish_reason': finish, 'prompt_tokens': len(ids),
                                      'completion_tokens': len(tokens), **identity}), flush=True)
                    break
            else:
                raise RuntimeError('Strata exited during generation')
    finally:
        if engine.poll() is None:
            engine.terminate()
            try:
                engine.wait(timeout=3)
            except subprocess.TimeoutExpired:
                engine.kill()
                engine.wait()

if __name__ == '__main__':
    main()
