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

"""Official SDK protocol handler with a strictly bounded binary stdio boundary."""
import argparse
from contextlib import asynccontextmanager
import importlib.metadata
import logging
import os
from pathlib import Path
import sys
import threading
from . import __version__
from .contracts import ContractError,MAX_FRAME,compact,final_frame,read_frame,valid_model
from .application import Application
from .receipts import Receipts
from .router import Router

# Modified 2026-11-11: select the separately reviewed SDK 1.30.0; all runtime guards remain.
SDK_VERSION='1.30.0'
TOOLS={
 'laf_router_models':('List operator-allowed Router model IDs. Not installation or readiness.',{},[]),
 'laf_router_status':('Observe Router readiness and API capabilities without starting providers.',{},[]),
 'laf_router_submit_job':('Submit one local Router job. The Router MAY START OR SWITCH providers/models. Explicit model required; output is unverified. Never automatically repeat an uncertain call.',{
  'attempt_id':{'type':'string','pattern':'^[0-9a-f]{32}$','description':'Caller-generated random attempt nonce, retained across retry/reconnect; never regenerate for uncertain work.'},
  'model':{'type':'string','pattern':'^[a-z][a-z0-9-]{0,63}$'},
  'messages':{'type':'array','minItems':1,'maxItems':32,'items':{'type':'object','properties':{'role':{'enum':['system','user','assistant']},'content':{'type':'string'}},'required':['role','content'],'additionalProperties':False}},
  'max_tokens':{'type':'integer','minimum':1,'maximum':1024},
  'timeout_seconds':{'type':'integer','minimum':1,'maximum':300}},['attempt_id','model','messages']),
 'laf_router_get_job':('Observe only a job submitted by this adapter using its opaque receipt handle. Unknown/evicted jobs must not be replayed.',{'handle':{'type':'string','pattern':'^[0-9a-f]{32}$'}},['handle']),
 'laf_router_cancel_job':('Request cancellation once for this adapter own opaque receipt. Acknowledgment does NOT confirm physical stop. Never automatically repeat.',{'handle':{'type':'string','pattern':'^[0-9a-f]{32}$'}},['handle']),
}

def checked_rpc(raw):
    if raw.get('jsonrpc')!='2.0' or set(raw)-{'jsonrpc','method','params','id'}:
        raise ContractError('invalid RPC fields')
    method=raw.get('method')
    if method not in ('initialize','notifications/initialized','notifications/cancelled','ping','tools/list','tools/call'):
        raise ContractError('unsupported MCP method')
    if 'id' in raw:
        identifier=raw['id']
        if not ((type(identifier) is int and abs(identifier)<=2**53-1) or (isinstance(identifier,str) and 0<len(identifier.encode('utf-8'))<=128)):
            raise ContractError('invalid request ID')
        if method.startswith('notifications/'):raise ContractError('notification has ID')
    elif not method.startswith('notifications/'):
        raise ContractError('request ID required')
    return raw

@asynccontextmanager
async def bounded_stdio():
    import anyio
    from mcp import types
    from mcp.shared.message import SessionMessage
    read_send,read_receive=anyio.create_memory_object_stream(0)
    write_send,write_receive=anyio.create_memory_object_stream(0)
    pending={};seen=set();total=0
    async def reader():
        nonlocal total
        try:
            async with read_send:
                while True:
                    raw=await anyio.to_thread.run_sync(read_frame,sys.stdin.buffer,abandon_on_cancel=True)
                    if raw is None:break
                    total+=1
                    if total>1024:raise ContractError('session frame limit reached')
                    checked_rpc(raw)
                    # Transport cancellation cannot safely undo Router work. The explicit
                    # cancel tool is the only job-cancellation operation. Keep the bounded
                    # call alive to persist and return its receipt rather than letting the
                    # SDK emit an early error and discard its result/handle.
                    if raw.get('method')=='notifications/cancelled':continue
                    identifier=raw.get('id')
                    if identifier is not None:
                        # Distinguish integer and string IDs, cap cumulative memory too.
                        key=(type(identifier).__name__,identifier)
                        if key in seen:raise ContractError('reused request ID')
                        seen.add(key)
                        is_cancel=raw.get('method')=='tools/call' and isinstance(raw.get('params'),dict) and raw['params'].get('name')=='laf_router_cancel_job'
                        lane='cancel' if is_cancel else 'normal'
                        if sum(v==lane for v in pending.values()) >= (1 if is_cancel else 3):
                            error={'jsonrpc':'2.0','id':identifier,'result':{'content':[{'type':'text','text':'Adapter busy; no tool action dispatched.'}], 'structuredContent':{'error':'adapter_busy','outcome':'NOT_SUBMITTED','automatic_retry':False},'isError':True}}
                            await write_send.send(SessionMessage(types.JSONRPCMessage.model_validate(error)))
                            continue
                        pending[key]=lane
                    await read_send.send(SessionMessage(types.JSONRPCMessage.model_validate(raw)))
        except (ContractError,ValueError,UnicodeError):
            # No raw diagnostics or partial invalid frames are echoed.
            sys.stderr.write('laf-mcp: invalid or oversized protocol input; closing session\n')
        except (anyio.ClosedResourceError,anyio.BrokenResourceError):pass
    async def writer():
        async with write_receive:
            async for message in write_receive:
                raw=message.message.model_dump(mode='json',by_alias=True,exclude_none=True)
                frame=final_frame(raw)
                await anyio.to_thread.run_sync(sys.stdout.buffer.write,frame)
                await anyio.to_thread.run_sync(sys.stdout.buffer.flush)
                identifier=raw.get('id')
                if identifier is not None:pending.pop((type(identifier).__name__,identifier),None)
    async with anyio.create_task_group() as group:
        group.start_soon(reader);group.start_soon(writer)
        try:yield read_receive,write_send
        finally:group.cancel_scope.cancel()

async def serve(app):
    import anyio
    from mcp import types
    from mcp.server.lowlevel import Server
    # Some SDK validation branches call logging.warning on the root logger.
    # This dedicated protocol process emits only our fixed stderr diagnostics.
    logging.disable(logging.CRITICAL)
    server=Server('laf',version=__version__)
    regular=threading.BoundedSemaphore(3)
    cancellation=threading.BoundedSemaphore(1)
    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=name,description=d,inputSchema={'type':'object','properties':p,'required':r,'additionalProperties':False},
            annotations=types.ToolAnnotations(readOnlyHint=name not in ('laf_router_submit_job','laf_router_cancel_job'),
             destructiveHint=name=='laf_router_cancel_job',idempotentHint=name not in ('laf_router_submit_job','laf_router_cancel_job'),openWorldHint=False))
             for name,(d,p,r) in TOOLS.items()]
    @server.call_tool(validate_input=False)
    async def call_tool(name,arguments):
        # Own strict validator avoids SDK exception messages echoing prompts/arguments.
        slot=cancellation if name=='laf_router_cancel_job' else regular
        if not slot.acquire(blocking=False):
            result={'error':'adapter_busy','outcome':'NOT_SUBMITTED','automatic_retry':False}
        else:
            try:
                with anyio.CancelScope(shield=True):
                    result=await anyio.to_thread.run_sync(app.call,name,arguments,abandon_on_cancel=False)
            finally:slot.release()
        return types.CallToolResult(content=[types.TextContent(type='text',text='LAF Router observation; see structured result.')],structuredContent=result,isError=bool(result.get('error')))
    async with bounded_stdio() as (read,write):
        await server.run(read,write,server.create_initialization_options())

def main(argv=None):
    parser=argparse.ArgumentParser(description='Local LAF MCP stdio adapter. Does not install/start a Router.')
    parser.add_argument('--version',action='version',version=__version__)
    parser.add_argument('--socket',default=os.environ.get('LOCAL_INFERENCE_SOCKET',f'/run/user/{os.getuid()}/laf-inference/service.sock'))
    parser.add_argument('--state-dir',default=os.environ.get('LAF_MCP_STATE_DIR'))
    parser.add_argument('--allow-model',action='append',default=None)
    args=parser.parse_args(argv)
    allowed=args.allow_model or [x for x in os.environ.get('LAF_MCP_MODELS','').split(',') if x]
    if not args.state_dir or not 1<=len(allowed)<=9 or len(set(allowed))!=len(allowed) or not all(valid_model(x) for x in allowed):
        sys.stderr.write('laf-mcp: configure an absolute state directory and explicit model allowlist\n');return 2
    receipts=None
    try:
        if importlib.metadata.version('mcp')!=SDK_VERSION:raise RuntimeError()
        import anyio
        # Library logs can contain parse/request data. No raw SDK logs are emitted.
        logger=logging.getLogger('mcp');logger.handlers=[logging.NullHandler()];logger.propagate=False;logger.setLevel(logging.CRITICAL+1)
        receipts=Receipts(args.state_dir)
        anyio.run(serve,Application(Router(args.socket),receipts,allowed))
        return 0
    except (KeyboardInterrupt,BrokenPipeError):return 0
    except Exception:
        sys.stderr.write('laf-mcp: startup or protocol session failed; no automatic retry\n');return 2
    finally:
        if receipts is not None:receipts.close()
if __name__=='__main__':raise SystemExit(main())
