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

# Modified 2026-11-09: reuse bounded JSON jobs for interactive chat.

"""Bounded standalone CLI for a private local inference service.

It does not depend on an external daemon/MCP and exposes no network listener.
The transport is the owner-only LAF Router socket.
"""
import argparse
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time

MODELS = ['flash', 'coder-next', 'gpt-oss-120b']
MAX_RESPONSE = 2 * 1024**2
MAX_JSON_DEPTH = 64


class ClientError(Exception):
    def __init__(self, message, identifier=None):
        super().__init__(message)
        self.job_id = identifier


class ClientCancelled(KeyboardInterrupt):
    def __init__(self, identifier):
        self.job_id = identifier


class Connection(http.client.HTTPConnection):
    def __init__(self, path, timeout=3):
        super().__init__('localhost', timeout=timeout)
        self.path = path
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


def checked_socket(path):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in [path, *path.parents]):
        raise ClientError('absolute non-symlink socket path required')
    parent, entry = path.parent.stat(), path.lstat()
    if (parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700
            or entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o600
            or not stat.S_ISSOCK(entry.st_mode)):
        raise ClientError('owner-only directory0700/socket0600 required')
    return path


def response_json(raw):
    """Decode the UTF-8 wire contract without ambiguous keys or non-finite values."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('non-finite JSON number')
    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError('non-finite JSON number')
        return result
    try:
        # Bound nesting before decoding, including Python versions with iterative parsers.
        depth = 0
        quoted = escaped = False
        for byte in raw:
            if quoted:
                if escaped: escaped = False
                elif byte == 92: escaped = True
                elif byte == 34: quoted = False
            elif byte == 34: quoted = True
            elif byte in (123, 91):
                depth += 1
                if depth > MAX_JSON_DEPTH:
                    raise ValueError('JSON nesting exceeds bound')
            elif byte in (125, 93):
                depth -= 1
        out = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                         parse_constant=constant, parse_float=number)
    except (ValueError, RecursionError):
        raise ClientError('invalid service JSON response') from None
    if not isinstance(out, dict):
        raise ClientError('invalid service response')
    return out


def request(path, method, route, body=None, timeout=3):
    conn = Connection(checked_socket(path),timeout=timeout)
    try:
        conn.request(method, route, None if body is None else json.dumps(body).encode(),
                     {'Content-Type':'application/json'})
        response = conn.getresponse()
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ClientError('response exceeds bound')
        out = response_json(raw)
        if response.status >= 400:
            raise ClientError(str(out.get('error') or 'service unavailable'))
        return out
    finally:
        conn.close()


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ClientError('invalid job identifier')
    return value


def checked_job(out, identifier, model=None):
    if (not isinstance(out, dict) or out.get('id') != identifier
            or out.get('model') not in MODELS
            or model is not None and out.get('model') != model):
        raise ClientError('job identity mismatch', identifier)
    if out.get('status') not in ['queued','running','completed','failed','cancelled']:
        raise ClientError('unknown job state', identifier)
    if out.get('status') == 'completed':
        generated = out.get('result')
        if not isinstance(generated,dict) or not isinstance(generated.get('content'),str):
            raise ClientError('invalid completion', identifier)
    return out


def ask(path, model, text, max_tokens=512, timeout=180, call=request, sleep=time.sleep):
    if model not in MODELS or type(max_tokens) is not int or not 1 <= max_tokens <= 1024:
        raise ClientError('invalid model/output bound')
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ClientError('timeout must be1..300')
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 8192:
        raise ClientError('input must be1..8192 UTF-8 bytes and fit the server context budget')
    if len(text.encode("utf-8")) + 64 + max_tokens + 256 > 8192:
        raise ClientError('input exceeds server context budget')
    return converse(path, model, [{'role':'user','content':text}], max_tokens,
                    timeout, call=call, sleep=sleep)


def converse(path, model, messages, max_tokens=512, timeout=180, call=None, sleep=time.sleep):
    """One bounded JSON chat job. Never replay an uncertain submission."""
    from inference.laf_service.core import validate_request
    body = {'model':model,'messages':messages,
            'max_tokens':max_tokens,'timeout_seconds':timeout}
    try:
        validate_request(body)
    except (ValueError, UnicodeError) as exc:
        raise ClientError(str(exc)) from None
    call = request if call is None else call
    # A submission failure might follow accepted work. Never replay it.
    try:
        submitted = call(path,'POST','/v1/jobs',body)
        identifier = job_id(submitted.get('id'))
    except (ClientError,OSError,ValueError,http.client.HTTPException) as exc:
        raise ClientError('submission outcome unknown; do not automatically resubmit',
                          getattr(exc,'job_id',None)) from None
    start = time.monotonic()
    try:
        while time.monotonic() - start < timeout + 5:
            out = call(path,'GET','/v1/jobs/'+identifier)
            checked_job(out, identifier, model)
            status = out.get('status')
            if status == 'completed':
                generated = out.get('result')
                return {'model':model,'job_id':identifier,'status':'completed',
                        'content':generated['content'],'finish_reason':generated.get('finish_reason'),
                        'prompt_tokens':generated.get('prompt_tokens'),
                        'completion_tokens':generated.get('completion_tokens'),
                        'unverified_advice':True,'code_executed':False,'automatic_retry':False}
            if status in ['failed','cancelled']:
                raise ClientError('job '+status)
            if status not in ['queued','running']:
                raise ClientError('unknown job state')
            sleep(.2)
        raise ClientError('local wait deadline exceeded', identifier)
    except (ClientError,OSError,ValueError,http.client.HTTPException) as exc:
        if isinstance(exc,ClientError):
            if exc.job_id is None:exc.job_id=identifier
            raise
        raise ClientError('job outcome unknown; query or cancel the received job ID',identifier) from None
    except KeyboardInterrupt:
        # Exactly one best-effort cancellation; do not claim stop confirmation.
        try:call(path,'DELETE','/v1/jobs/'+identifier)
        except (OSError,ValueError,ClientError,http.client.HTTPException):pass
        raise ClientCancelled(identifier) from None


def main(argv=None):
    # Model storage/lifecycle is opt-in and independent of Router generation.
    actual = list(sys.argv[1:] if argv is None else argv)
    if actual and actual[0] == 'model':
        from .model_management import main as model_main
        return model_main(actual[1:])
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--socket',default=os.environ.get('LOCAL_INFERENCE_SOCKET',
        '/run/user/'+str(os.getuid())+'/laf-inference/service.sock'),
        help='operator-chosen existing private UDS; no TCP or credentials created')
    sub=p.add_subparsers(dest='command',required=True)
    model=sub.add_parser('model',help='persistent catalog/install/list/remove/inspect; use model --help',add_help=False)
    model.add_argument('model_args',nargs=argparse.REMAINDER)
    sub.add_parser('status')
    sub.add_parser('models')
    sub.add_parser('capabilities')
    classify=sub.add_parser('classify',help='independent NPU plumbing classifier; not application accuracy')
    classify.add_argument('--input',required=True,help='JSON model/input file, at most100000 bytes')
    r=sub.add_parser('repair',help='bounded pure-function repair; no arbitrary shell or original-file writes')
    r.add_argument('--source',required=True);r.add_argument('--cases',required=True)
    r.add_argument('--workdir',required=True);r.add_argument('--model',choices=MODELS,default='gpt-oss-120b')
    r.add_argument('--attempts',type=int,default=2)
    get=sub.add_parser('get');get.add_argument('job_id')
    cancel=sub.add_parser('cancel');cancel.add_argument('job_id')
    q=sub.add_parser('ask');q.add_argument('--model',choices=MODELS,default='gpt-oss-120b')
    source=q.add_mutually_exclusive_group(required=True);source.add_argument('--prompt');source.add_argument('--input',help='UTF-8 file, or - for stdin')
    q.add_argument('--max-tokens',type=int,default=512);q.add_argument('--timeout',type=int,default=180)
    q.add_argument('--output',help='create a new JSON file; never overwrite')
    chat=sub.add_parser('chat',help='interactive conversation; optional bounded workspace filename search')
    chat.add_argument('--model',choices=MODELS,default='gpt-oss-120b')
    chat.add_argument('--max-tokens',type=int,default=512)
    chat.add_argument('--timeout',type=int,default=180)
    chat.add_argument('--workspace',help='absolute owned directory; permits filename-only search, no shell or file contents')
    args=p.parse_args(argv)
    if args.command=='chat':
        from .chat import run_chat
        return run_chat(args.socket,args.model,args.max_tokens,args.timeout,args.workspace)
    if args.command=='model':
        from .model_management import main as model_main
        return model_main(args.model_args)
    if args.command=='repair':
        from .repair import main as repair_main
        return repair_main(['--socket',args.socket,'--source',args.source,'--cases',args.cases,
                            '--workdir',args.workdir,'--model',args.model,'--attempts',str(args.attempts)])
    try:
        if args.command=='status':
            out=request(args.socket,'GET','/health')
            # Keep endpoint paths, container IDs and private telemetry out of CLI status.
            gpu, npu = out.get('gpu',{}), out.get('npu',{})
            if not isinstance(gpu,dict) or not isinstance(npu,dict):
                raise ClientError('invalid service health response')
            out={'status':out.get('status'),'models':MODELS,
                 'flash_resident':gpu.get('flash_resident'),
                 'selected_model':gpu.get('ollama_selected'),
                 'queue_depth':out.get('queue_depth'),'npu_status':npu.get('status')}
            from .model_management import local_status
            out['model_storage']=local_status()
        elif args.command=='classify':
            with open(args.input,'rb') as f:data=f.read(100001)
            if len(data)>100000:raise ClientError('classification input exceeds bound')
            from inference.laf_service.npu_bridge import validate_input, validate_result, NPUUnavailable
            payload=validate_input(json.loads(data))
            try:out=validate_result(request(args.socket,'POST','/v1/classify',payload,timeout=13))
            except NPUUnavailable:raise ClientError('classification identity/output invalid; not retried') from None
            out['application_accuracy_verified']=False
            out['automatic_retry']=False
        elif args.command=='capabilities':
            out=request(args.socket,'GET','/v1/capabilities')
        elif args.command=='models':
            out=request(args.socket,'GET','/v1/models')
        elif args.command=='get':
            identifier=job_id(args.job_id)
            raw=request(args.socket,'GET','/v1/jobs/'+identifier)
            checked_job(raw, identifier)
            out={k:raw.get(k) for k in ['id','model','status','error']}
            generated=raw.get('result')
            if isinstance(generated,dict):
                out['result']={k:generated.get(k) for k in ['content','finish_reason','prompt_tokens','completion_tokens']}
            out['unverified_advice']=True;out['code_executed']=False
        elif args.command=='cancel':
            identifier=job_id(args.job_id)
            raw=request(args.socket,'DELETE','/v1/jobs/'+identifier)
            if raw.get('status') != 'cancellation_requested':
                raise ClientError('invalid cancellation acknowledgement', identifier)
            out={'job_id':identifier,'status':raw.get('status'),'stop_confirmed':False,'automatic_retry':False}
        else:
            if args.output and os.path.lexists(args.output):
                raise ClientError('output already exists; request was not submitted')
            if args.prompt is not None:text=args.prompt
            elif args.input=='-':text=sys.stdin.buffer.read(8193).decode('utf-8')
            else:
                with open(args.input,'rb') as f:text=f.read(8193).decode('utf-8')
            out=ask(args.socket,args.model,text,args.max_tokens,args.timeout)
        raw=json.dumps(out,ensure_ascii=False,indent=2)+'\n'
        if args.command=='ask' and args.output:
            with open(args.output,'x',encoding='utf-8') as f:f.write(raw)
        else:sys.stdout.write(raw)
        return 0
    except (ClientError,OSError,ValueError,http.client.HTTPException) as exc:
        print(json.dumps({'status':'error_or_uncertain','error':str(exc)[:180],
                          'job_id':getattr(exc,'job_id',None) or locals().get('identifier') or (locals().get('out') or {}).get('job_id'),
                          'automatic_retry':False}),file=sys.stderr)
        return 1
    except KeyboardInterrupt as exc:
        print(json.dumps({'status':'client_wait_interrupted' if args.command=='classify' else 'cancellation_requested_or_uncertain',
                          'job_id':getattr(exc,'job_id',None),'stop_confirmed':False,'automatic_retry':False}),file=sys.stderr)
        return 130

if __name__=='__main__':raise SystemExit(main())
