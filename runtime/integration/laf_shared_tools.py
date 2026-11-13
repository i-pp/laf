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
"""Compose existing MCP tools with public LAF Router tools; no new transport."""
import copy
import json
import re
import threading
import math
import time
from legacy_tool_adapter import Invalid
from mcp_adapter import Adapter
from laf_mcp.server import TOOLS, checked_rpc
from laf_mcp.contracts import MAX_FRAME

SERVER_META = 'io.modelcontextprotocol/serverInfo'
HANDLE = re.compile(r'^[0-9a-f]{32}$')
KEEP = ('handle', 'job_id', 'model', 'outcome', 'status', 'cancel_outcome',
        'receipt_durable', 'physical_stop_confirmed')

class DrainGate:
    """Stop admission and wait; a timeout never closes descriptors in use."""
    def __init__(self):
        self.condition = threading.Condition()
        self.active = 0
        self.closing = False
    def enter(self):
        with self.condition:
            if self.closing: return False
            self.active += 1
            return True
    def leave(self):
        with self.condition:
            self.active -= 1
            self.condition.notify_all()
    def drain(self, timeout):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError('Bounded drain timeout required')
        deadline = time.monotonic() + timeout
        with self.condition:
            self.closing = True
            while self.active:
                remaining = deadline - time.monotonic()
                if remaining <= 0: return False
                self.condition.wait(remaining)
            return True

class CompositeSharedTools:
    """Keep legacy contracts unchanged; Router-managed jobs have distinct names."""
    def __init__(self, legacy, router_app=None, *, server_info):
        if (not isinstance(getattr(legacy, 'tools', None), list)
                or not callable(getattr(legacy, 'call_tool', None))):
            raise ValueError('Existing shared adapter is required')
        if router_app is not None and not callable(getattr(router_app, 'call', None)):
            raise ValueError('Explicit Router application is required')
        if (not isinstance(server_info, dict) or set(server_info) != {'name', 'version'}
                or any(not isinstance(v, str) or not 0 < len(v.encode('utf-8')) <= 128
                       for v in server_info.values())):
            raise ValueError('Bounded server identity is required')
        self.legacy, self.app = legacy, router_app
        self._gate = DrainGate()
        self._close_lock = threading.Lock()
        self._resources_closed = False
        self.server_info = copy.deepcopy(server_info)
        self.audit = legacy.audit
        self.tools = copy.deepcopy(legacy.tools)
        names = [t.get('name') for t in self.tools]
        if (any(not isinstance(n, str) for n in names) or len(names) != len(set(names))
                or set(names) & set(TOOLS)):
            raise ValueError('Invalid or overlapping existing tools')
        self.legacy_names = frozenset(names)
        self.router_names = frozenset(TOOLS) if router_app is not None else frozenset()
        if router_app is not None:
            for name, (description, properties, required) in TOOLS.items():
                write = name in ('laf_router_submit_job', 'laf_router_cancel_job')
                self.tools.append({'name': name, 'description': description,
                    'inputSchema': {'type': 'object', 'properties': copy.deepcopy(properties),
                        'required': list(required), 'additionalProperties': False},
                    'annotations': {'readOnlyHint': not write,
                        'destructiveHint': name == 'laf_router_cancel_job',
                        'idempotentHint': not write, 'openWorldHint': False}})
        self.regular = threading.BoundedSemaphore(3)
        self.cancellation = threading.BoundedSemaphore(1)

    @staticmethod
    def _handle(args):
        if isinstance(args, dict):
            value = args.get('attempt_id', args.get('handle'))
            if isinstance(value, str) and HANDLE.fullmatch(value): return value
        return None

    def _unknown(self, args, error, *, dispatched):
        data = {'error': error, 'outcome': 'UNKNOWN', 'automatic_retry': False,
                'dispatch_started': dispatched}
        handle = self._handle(args)
        if handle is not None: data['handle'] = handle
        return data

    def _size(self, result, rpc_id):
        # Standard stdio adds spaces/newline; HTTP is compact. Include modern
        # metadata here so neither outer codec can discard a committed handle.
        envelope = {'jsonrpc': '2.0', 'id': rpc_id, 'result': {
            **result, 'resultType': 'complete',
            '_meta': {SERVER_META: self.server_info}}}
        return len(json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode('utf-8')) + 1

    def _result(self, data, rpc_id, args):
        if not isinstance(data, dict): data = self._unknown(args, 'router_result_unconfirmed', dispatched=True)
        result = {'content': [{'type': 'text', 'text': 'LAF Router observation; see structured result.'}],
                  'structuredContent': data, 'isError': bool(data.get('error'))}
        try:
            if self._size(result, rpc_id) <= MAX_FRAME: return result
        except (TypeError, ValueError, UnicodeError):
            data = self._unknown(args, 'router_result_unconfirmed', dispatched=True)
        # The public application validates these bounded identity/outcome fields.
        # Never truncate model text silently or turn UNKNOWN into non-execution.
        keep = {k: data[k] for k in KEEP if k in data}
        keep.update(error='response_too_large', automatic_retry=False)
        result = {'content': [{'type': 'text', 'text': 'Response exceeds the byte limit; preserved receipt/outcome metadata.'}],
                  'structuredContent': keep, 'isError': True}
        try:
            if self._size(result, rpc_id) <= MAX_FRAME: return result
        except (TypeError, ValueError, UnicodeError): pass
        result['structuredContent'] = self._unknown(args, 'response_metadata_unavailable', dispatched=True)
        return result

    def close(self, timeout=12):
        if not self._gate.drain(timeout): return False
        with self._close_lock:
            if self._resources_closed: return True
            try:
                try:
                    if not self.audit.closed: self.audit.close()
                finally:
                    close = getattr(getattr(self.app, 'receipts', None), 'close', None)
                    if callable(close): close()
            except OSError:
                return False
            self._resources_closed = True
            return True

    def call_tool(self, name, arguments, rpc_id):
        if not self._gate.enter():
            raise Invalid('Frontend shutting down; reconcile known attempts before retry')
        try:
            return self._call_tool(name, arguments, rpc_id)
        finally:
            self._gate.leave()

    def _call_tool(self, name, arguments, rpc_id):
        if name in self.legacy_names:
            return self.legacy.call_tool(name, arguments, rpc_id)
        if name not in self.router_names: raise Invalid('Tool is not enabled')
        try:
            checked_rpc({'jsonrpc': '2.0', 'id': rpc_id, 'method': 'tools/call'})
        except (ValueError, UnicodeError, TypeError):
            raise Invalid('Invalid Router request identifier') from None
        slot = self.cancellation if name == 'laf_router_cancel_job' else self.regular
        if not slot.acquire(blocking=False):
            return self._result(self._unknown(arguments, 'adapter_busy', dispatched=False), rpc_id, arguments)
        try:
            try: data = self.app.call(name, arguments)
            except Exception:
                data = self._unknown(arguments, 'router_operation_unconfirmed', dispatched=True)
            return self._result(data, rpc_id, arguments)
        finally:
            slot.release()

class IntegratedAdapter(Adapter):
    """Expose only independently bounded Router calls outside the HTTP lock."""
    def __init__(self, *args, shared_tool_adapter, **kwargs):
        if not isinstance(shared_tool_adapter, CompositeSharedTools):
            raise ValueError('Explicit composed shared adapter is required')
        super().__init__(*args, shared_tool_adapter=shared_tool_adapter, **kwargs)
        self.bounded_independent_tools = shared_tool_adapter.router_names
        self._request_gate = DrainGate()

    @property
    def closing(self):
        with self._request_gate.condition: return self._request_gate.closing

    def handle(self, message, **kwargs):
        if not self._request_gate.enter():
            identifier = message.get('id') if isinstance(message, dict) else None
            if not (type(identifier) is int or isinstance(identifier, str) and len(identifier) <= 128): identifier = None
            return self.error(identifier, -32603, 'Frontend shutting down; reconcile known attempts before retry')
        try:
            return super().handle(message, **kwargs)
        finally:
            self._request_gate.leave()

    def close(self, timeout=12):
        started = time.monotonic()
        if not self._request_gate.drain(timeout): return False
        if not self.shared_tool_adapter.close(timeout=max(0, timeout-(time.monotonic()-started))): return False
        if not self.audit.closed: self.audit.close()
        return True

def build_router_application(*, socket_path, state_directory, models):
    """Operator-only startup configuration; never sourced from tool arguments."""
    from pathlib import Path
    from laf_mcp.application import Application
    from laf_mcp.router import Router
    from laf_mcp.receipts import Receipts
    from laf_mcp.contracts import valid_model
    if (not isinstance(models, (list, tuple)) or not models
            or any(not valid_model(model) for model in models)
            or len(set(models)) != len(models)):
        raise ValueError('Explicit unique Router model allowlist required')
    for path in (socket_path, state_directory):
        if (not isinstance(path, (str, Path)) or not Path(path).is_absolute()
                or '..' in Path(path).parts):
            raise ValueError('Explicit absolute Router socket/state paths required')
    # The original owner/root/socket/receipt guards are used unchanged. This does
    # not start a provider, make a job request, repair permissions or install SDKs.
    return Application(Router(str(socket_path)), Receipts(state_directory), models)


def close_shared_adapter(shared, timeout=12):
    """Close only drained frontend handles, never cancel provider jobs."""
    if not isinstance(shared, CompositeSharedTools):
        raise ValueError('Explicit composed shared adapter required')
    return shared.close(timeout=timeout)


def run_integrated_http(adapter, serve, **options):
    """Cooperative SIGTERM/SIGINT stop; retain handlers through bounded drain."""
    import signal
    stop = threading.Event()
    previous = {}
    requested = False
    closed = False
    def request_stop(_signum, _frame):
        nonlocal requested
        # Do not raise inside active request/receipt/condition cleanup. A second
        # signal must not interrupt draining or reenter Event.set's lock.
        if not requested:
            requested = True
            stop.set()
    try:
        if threading.current_thread() is not threading.main_thread():
            raise ValueError('Signal lifecycle requires the main frontend thread')
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, request_stop)
        return_code = serve(adapter, stop_event=stop, **options)
    finally:
        try:
            closed = adapter.close(timeout=12)
        finally:
            for signum, handler in previous.items(): signal.signal(signum, handler)
    if not closed:
        import sys
        print('Frontend drain incomplete; retained state must be reconciled before retry.', file=sys.stderr)
        return 1
    return 0 if return_code is None else return_code
