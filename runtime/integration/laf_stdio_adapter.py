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
"""Preserve the existing stdio Adapter protocol and legacy dispatcher; add5.

This is an adapter foundation only. A bounded concurrent stdio transport must
be added before it can replace any live main-channel command. Do not pass it
to the old serial serve(), whose cleanup has no composite drain contract.
"""
import copy
import threading
import time
from legacy_tool_adapter import Adapter as LegacyAdapter, SERVER_INFO
from laf_shared_tools import CompositeSharedTools, DrainGate

class _LegacyDispatch:
    def __init__(self, owner):
        self.owner=owner
        self.tools=copy.deepcopy(owner.tools)
        self.audit=owner.audit
    def call_tool(self,name,arguments,rpc_id):
        # Preserve the old serialized audit/pipeline/helper dispatch. New Router
        # cancellation never needs this lock and has its own bounded lane.
        with self.owner._legacy_lock:
            return LegacyAdapter.call_tool(self.owner,name,arguments,rpc_id)

class StdioRouterAdapter(LegacyAdapter):
    def __init__(self,root,*,router_app,**legacy_options):
        self._request_gate=DrainGate()
        self._legacy_lock=threading.RLock()
        self._audit_lock=threading.RLock()
        self._protocol_lock=threading.RLock()
        try:
            super().__init__(root,**legacy_options)
            self.legacy_tools=copy.deepcopy(self.tools)
            self.shared=CompositeSharedTools(_LegacyDispatch(self),router_app,
                                              server_info=SERVER_INFO)
            self.tools=self.shared.tools
            self.router_names=self.shared.router_names
        except BaseException:
            try:
                audit=getattr(self,'audit',None)
                if audit is not None and not audit.closed:audit.close()
            finally:
                close=getattr(getattr(router_app,'receipts',None),'close',None)
                if callable(close):close()
            raise
    @property
    def closing(self):
        with self._request_gate.condition:return self._request_gate.closing
    def record(self,event,**facts):
        # Discovery/rejection records are outside legacy call_tool's lock too.
        with self._audit_lock:return LegacyAdapter.record(self,event,**facts)
    def call_tool(self,name,arguments,rpc_id):
        return self.shared.call_tool(name,arguments,rpc_id)
    def handle(self,message):
        if not self._request_gate.enter():
            if isinstance(message,dict) and 'id' not in message:return None
            identifier=message.get('id') if isinstance(message,dict) else None
            if not(type(identifier) is int or isinstance(identifier,str) and len(identifier)<=128):identifier=None
            return self.error(identifier,-32603,'Frontend shutting down; reconcile known attempts before retry')
        try:
            method=message.get('method') if isinstance(message,dict) else None
            if method in ('initialize','notifications/initialized','server/discover','tools/list'):
                with self._protocol_lock:return super().handle(message)
            return super().handle(message)
        finally:self._request_gate.leave()
    def close(self,timeout=12):
        started=time.monotonic()
        if not self._request_gate.drain(timeout):return False
        return self.shared.close(timeout=max(0,timeout-(time.monotonic()-started)))
