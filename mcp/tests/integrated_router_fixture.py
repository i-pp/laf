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

"""Test-only SDK/Handler/Core bridge: synthetic provider, no Router listener.

The direct bridge exercises JSON bodies, Handler routes, Controller scheduling
and CoreBackends. It replaces HTTP/socket framing and endpoint authentication;
its endpoint key is deliberately synthetic, never evidence of a real peer.
"""
from email.message import Message
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inference.laf_service.core import Controller
from inference.laf_service.server import Handler
from inference.laf_service.test_configured_profiles import configured


class HandlerBridge:
    endpoint_key = 'd' * 64  # Test identity only; no peer was authenticated.

    def __init__(self, controller):
        self.controller = controller
        self.calls = []
        self.observations = []

    def validate_endpoint(self, expected_key=None, *, cancel=False):
        if expected_key is not None and expected_key != self.endpoint_key:
            raise ValueError('synthetic endpoint changed')
        return self.endpoint_key

    def request(self, method, path, body=None):
        # Roundtrip copies avoid sharing mutable production objects with callers.
        raw = b'' if body is None else json.dumps(body).encode('utf-8')
        self.calls.append((method, path, None if body is None else json.loads(raw)))
        handler = object.__new__(Handler)
        handler.server = SimpleNamespace(controller=self.controller, config={})
        handler.path = path
        handler.headers = Message()
        handler.headers['Host'] = 'localhost'
        if body is not None:
            handler.headers['Content-Type'] = 'application/json'
            handler.headers['Content-Length'] = str(len(raw))
        handler.rfile = BytesIO(raw)
        result = []
        handler.response = lambda code, value: result.append(
            (code, json.loads(json.dumps(value))))
        if method not in {'GET', 'POST', 'DELETE'}:
            raise AssertionError('unexpected bridge method')
        getattr(handler, 'do_' + method)()
        if len(result) != 1:
            raise AssertionError('one Handler response required')
        self.observations.append((method, path, *result[0]))
        return result[0]


class ConfiguredFixture:
    def __init__(self, start=False):
        self.backend, self.lifecycle, self.api = configured(flash=False)
        self.controller = Controller(self.backend, self.lifecycle.read_resources,
                                     start=False)
        self.router = HandlerBridge(self.controller)
        if start:
            self.start()

    def start(self):
        # The monitor is unnecessary here: Controller.cancelled still exercises
        # real resource admission using the explicitly synthetic reader.
        self.controller.thread.start()

    def close(self):
        self.controller.close()
        self.backend.process_factory.assert_not_called()


def main():
    import anyio
    from laf_mcp.application import Application
    from laf_mcp.receipts import Receipts
    from laf_mcp.server import serve

    fixture = ConfiguredFixture(start=os.environ.get('LAF_TEST_QUEUED') != '1')
    receipts = Receipts(os.environ['LAF_MCP_STATE_DIR'])
    try:
        anyio.run(serve, Application(fixture.router, receipts, fixture.backend.models))
    finally:
        receipts.close()
        fixture.close()


if __name__ == '__main__':
    main()
