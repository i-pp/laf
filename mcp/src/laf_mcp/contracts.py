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

"""Strict public request validation and byte-bounded protocol encoding."""
import json
import math
import re

MAX_FRAME = 65536
MAX_DEPTH = 32
MODEL = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')
HANDLE = re.compile(r'[0-9a-f]{32}\Z')

class ContractError(ValueError):
    pass

def strict_json(raw, max_bytes=MAX_FRAME):
    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise ContractError('JSON byte bound exceeded')
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError()
            out[key] = value
        return out
    def bad(_):
        raise ValueError()
    def finite(s):
        n = float(s)
        if not math.isfinite(n):
            raise ValueError()
        return n
    try:
        text = raw.decode('utf-8', 'strict')
        depth = 0
        quoted = escaped = False
        for c in text:
            if quoted:
                if escaped: escaped = False
                elif c == '\\': escaped = True
                elif c == '"': quoted = False
            elif c == '"': quoted = True
            elif c in '[{':
                depth += 1
                if depth > MAX_DEPTH: raise ValueError()
            elif c in ']}': depth -= 1
        out = json.loads(text, object_pairs_hook=pairs, parse_constant=bad, parse_float=finite)
        if not isinstance(out, dict): raise ValueError()
        # JSON permits lone surrogate escapes, but the wire/text contract does not.
        json.dumps(out, ensure_ascii=False, allow_nan=False).encode('utf-8', 'strict')
        return out
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise ContractError('invalid bounded JSON') from None

def read_frame(stream):
    raw = stream.readline(MAX_FRAME + 1)
    if not raw: return None
    if len(raw) > MAX_FRAME or not raw.endswith(b'\n'):
        raise ContractError('invalid or oversized MCP frame')
    return strict_json(raw)

def valid_model(value):
    return isinstance(value, str) and MODEL.fullmatch(value) is not None

def exact_fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ContractError('invalid argument fields')

def validate_submission(args):
    exact_fields(args, {'model', 'messages'}, {'max_tokens', 'timeout_seconds'})
    if not valid_model(args['model']): raise ContractError('explicit model ID required')
    messages = args['messages']
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise ContractError('1 to 32 text messages required')
    size = 0
    for m in messages:
        exact_fields(m, {'role', 'content'})
        if m['role'] not in ('user', 'system', 'assistant') or not isinstance(m['content'], str):
            raise ContractError('text-only messages required')
        try: size += len(m['content'].encode('utf-8', 'strict'))
        except UnicodeError: raise ContractError('invalid text encoding') from None
    n, timeout = args.get('max_tokens', 256), args.get('timeout_seconds', 180)
    if type(n) is not int or not 1 <= n <= 1024 or type(timeout) is not int or not 1 <= timeout <= 300:
        raise ContractError('invalid output or deadline bound')
    if not 1 <= size <= 24576 or size + 64 * len(messages) + n + 256 > 8192:
        raise ContractError('request exceeds context budget')
    out = dict(args, max_tokens=n, timeout_seconds=timeout)
    if len(json.dumps(out, ensure_ascii=True, allow_nan=False).encode()) > 32768:
        raise ContractError('request exceeds wire byte bound')
    return out

def validate_catalog(out, allowed):
    if not isinstance(out, dict): raise ContractError('invalid catalog')
    limits = {'context_limit':8192, 'max_output_tokens':1024, 'max_large_models_loaded':1}
    if any(type(out.get(k)) is not int or out[k] != v for k,v in limits.items()):
        raise ContractError('unsupported catalog limits')
    models = out.get('models')
    if not isinstance(models, list) or not 1 <= len(models) <= 9 or not all(valid_model(m) for m in models) or len(set(models)) != len(models):
        raise ContractError('invalid catalog models')
    if 'default_model' in out and out['default_model'] is not None and out['default_model'] not in models:
        raise ContractError('invalid catalog default')
    if 'flash_enabled' in out and (type(out['flash_enabled']) is not bool or out['flash_enabled'] != ('flash' in models)):
        raise ContractError('invalid catalog Flash state')
    return {'models': sorted(set(models) & set(allowed)), **limits, 'explicit_model_required':True}

def compact(out):
    return json.dumps(out, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')

def final_frame(message):
    """Cap the complete JSON-RPC envelope, including duplicated content and newline."""
    # SDK errors are not allowed to echo rejected input or library diagnostics.
    if 'error' in message:
        message = {'jsonrpc':'2.0', 'id':message.get('id'),
                   'error':{'code':-32600, 'message':'MCP request rejected'}}
    raw = compact(message) + b'\n'
    if len(raw) <= MAX_FRAME: return raw
    result = message.get('result', {})
    data = result.get('structuredContent', {}) if isinstance(result, dict) else {}
    keep = {k:data[k] for k in ('handle','job_id','model','outcome','status','cancel_outcome','receipt_durable','physical_stop_confirmed') if k in data}
    keep.update(error='response_too_large', automatic_retry=False)
    replacement = {'jsonrpc':'2.0', 'id':message.get('id'), 'result':{
        'content':[{'type':'text','text':'Response exceeds the byte limit; preserved receipt/outcome metadata.'}],
        'structuredContent':keep, 'isError':True}}
    raw = compact(replacement) + b'\n'
    if len(raw) > MAX_FRAME: raise ContractError('response metadata exceeds bound')
    return raw
