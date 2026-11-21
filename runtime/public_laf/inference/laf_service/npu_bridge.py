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

"""Bounded, fail-closed forwarding to the independently owned NPU service."""
import http.client
import json
import math
import os
from pathlib import Path
import socket
import stat
import struct
import threading

MODEL = 'ryzenai-quicktest-10class'
MAX_BODY = 100_000
MAX_RESPONSE = 16_384


class NPUUnavailable(Exception):
    pass


class NPUBusy(Exception):
    pass


def validate_input(body):
    if not isinstance(body, dict) or set(body) - {'model','input'} or body.get('model',MODEL) != MODEL:
        raise ValueError('expected the supported classification model and input only')
    values = body.get('input')
    if not isinstance(values, list) or len(values) != 3072:
        raise ValueError('input must contain3072 finite float32 values')
    if any(type(v) not in {int,float} or abs(v)>3.4028234663852886e38 or not math.isfinite(v) for v in values):
        raise ValueError('input must contain3072 finite float32 values')
    # Match the pinned NPU contract before JSON serialization. This also bounds
    # decimal integer expansion; a float32 round-trip is idempotent upstream.
    rounded=list(struct.unpack('<3072f',struct.pack('<3072f',*values)))
    return {'model':MODEL,'input':rounded}


def checked_socket(path):
    if not isinstance(path,str) or not path:
        raise NPUUnavailable('NPU is not configured')
    entry = Path(path)
    if not entry.is_absolute() or any(p.is_symlink() for p in [entry,*entry.parents]):
        raise NPUUnavailable('invalid NPU socket boundary')
    parent, target = entry.parent.stat(), entry.lstat()
    if (parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700
            or target.st_uid != os.getuid() or stat.S_IMODE(target.st_mode) != 0o600
            or not stat.S_ISSOCK(target.st_mode)):
        raise NPUUnavailable('invalid NPU socket boundary')
    return entry


def validate_result(result):
    if (not isinstance(result,dict) or result.get('status') != 'ready'
            or result.get('backend') != 'ryzenai' or result.get('model') != MODEL
            or result.get('provider') != 'VitisAI' or result.get('execution_target') != 'npu'
            or result.get('execution_verified') is not True
            or not isinstance(result.get('execution_scope'),str)
            or result.get('execution_scope') not in {'mixed_npu_cpu','npu_compute'}
            or result.get('verification_kind') != 'ort_warmup_profile'):
        raise NPUUnavailable('NPU execution identity is not verified')
    logits = result.get('logits')
    index, latency = result.get('class_index'), result.get('latency_ms')
    if (not isinstance(logits,list) or len(logits) != 10
            or any(type(v) not in {int,float} or abs(v)>1.7976931348623157e308 or not math.isfinite(v) for v in logits)
            or type(index) is not int or not 0 <= index < 10
            or index != max(range(10),key=logits.__getitem__)
            or type(latency) not in {int,float} or not 0 <= latency <= 1e12 or not math.isfinite(latency)):
        raise NPUUnavailable('invalid classification output')
    # No upstream log paths, process identifiers or arbitrary error text escape.
    keys = ['status','backend','model','provider','execution_target','execution_verified',
            'execution_scope','verification_kind','logits','class_index','latency_ms']
    return {key:result[key] for key in keys}


def classify(path, body, timeout=12):
    """One attempt, no autoload/fallback/retry, timeout closes only this socket."""
    body = validate_input(body)
    connection = None
    timer = None
    transport = None
    response = None
    try:
        endpoint = checked_socket(path)
        # Connect has its own1s ceiling. Remaining response budget is timeout-1.
        transport = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            transport.settimeout(min(1,timeout))
            transport.connect(str(endpoint))
            _,uid,_ = struct.unpack('3i',transport.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid != os.getuid():
                raise NPUUnavailable('unexpected NPU peer UID')
        except BaseException:
            transport.close()
            raise
        connection = http.client.HTTPConnection('localhost',timeout=timeout)
        connection.sock = transport
        transport.settimeout(timeout)
        def expire():
            try: transport.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        timer = threading.Timer(max(.001,timeout-1),expire)
        timer.daemon = True
        timer.start()
        payload=json.dumps(body,allow_nan=False,separators=(',',':')).encode()
        if len(payload)>MAX_BODY:
            raise ValueError('serialized classification exceeds bound')
        connection.request('POST','/v1/classify',payload,
                           {'Content-Type':'application/json'})
        response = connection.getresponse()
        lengths=[v for k,v in response.getheaders() if k.lower()=='content-length']
        if (response.getheader('Transfer-Encoding') is not None or len(lengths)!=1
                or not lengths[0].isascii() or not lengths[0].isdecimal()):
            raise NPUUnavailable('invalid NPU response framing')
        declared=int(lengths[0])
        if not 0 < declared <= MAX_RESPONSE:
            raise NPUUnavailable('oversized NPU response')
        raw = response.read(declared+1)
        if len(raw)!=declared:
            raise NPUUnavailable('incomplete NPU response')
        if response.status == 429:
            raise NPUBusy('NPU classifier busy')
        if response.status != 200 or response.getheader('Content-Type','').split(';')[0] != 'application/json':
            raise NPUUnavailable('NPU classifier unavailable')
        return validate_result(json.loads(raw))
    except (OSError,ValueError,OverflowError,RecursionError,http.client.HTTPException) as exc:
        raise NPUUnavailable('NPU request failed or outcome unknown; not retried') from None
    finally:
        if timer: timer.cancel()
        if response: response.close()
        if connection: connection.close()
        if transport: transport.close()
