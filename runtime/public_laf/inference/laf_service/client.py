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

"""Explicit local client for LAF Router callers; no fallback or arbitrary URL."""
import argparse
import json
import os
from pathlib import Path
from .server import UnixHTTPConnection


def request(socket_path, method, path, body=None, timeout=310):
    conn = UnixHTTPConnection(socket_path, timeout=timeout)
    try:
        raw = None if body is None else json.dumps(body).encode()
        conn.request(method, path, raw, {'Content-Type':'application/json'})
        response = conn.getresponse()
        payload = response.read(2 * 1024**2 + 1)
        if len(payload) > 2 * 1024**2:
            raise RuntimeError('response too large')
        result = json.loads(payload)
        if response.status >= 400:
            raise RuntimeError(result.get('error') or result.get('fault') or 'service not ready')
        return result
    finally:
        conn.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--socket', default=str(Path(os.environ.get('XDG_RUNTIME_DIR','/run/user/'+str(os.getuid()))) / 'laf-inference/service.sock'))
    p.add_argument('--health', action='store_true')
    p.add_argument('--model',choices=['flash','coder-next','gpt-oss-120b'])
    p.add_argument('--prompt')
    p.add_argument('--max-tokens',type=int,default=256)
    a=p.parse_args()
    if a.health:
        out=request(a.socket,'GET','/health')
    else:
        if not a.model or not a.prompt:
            p.error('--model and --prompt required')
        out=request(a.socket,'POST','/v1/chat/completions',{'model':a.model,'messages':[{'role':'user','content':a.prompt}],'max_tokens':a.max_tokens})
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
