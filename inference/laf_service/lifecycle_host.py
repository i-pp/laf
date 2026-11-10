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

"""Host-only adapter. Never package this or legacy backends in a Core image."""
from __future__ import annotations
import re
from .backends import Backends
from .core import ServiceError, snapshot
from .lifecycle import HostLifecycle


class OwnedOllama:
    """Reuse fixed image/mount/network/ownership checks from the accepted backend.

    Paths and policy come only from trusted host configuration. There is no
    arbitrary command, container, image, mount or model-import wire operation.
    """
    def __init__(self, config, config_path):
        self.backend = Backends(config, config_path)

    def _reconcile(self):
        if not self.backend.cid:
            cid = self.backend.docker('ps', '-aq', '--filter',
                                      'name=^/' + self.backend.config['container_name'] + '$')
            if cid:
                if not re.fullmatch(r'[0-9a-f]{12,64}', cid):
                    raise ServiceError('owned container identity ambiguous')
                self.backend.cid = cid
                self.backend.inspect_owned()

    def ensure(self, check):
        self.backend.start_ollama(check)

    def running(self):
        self._reconcile()
        if not self.backend.cid:
            return False
        info = self.backend.inspect_owned()
        if type(info['State']['Running']) is not bool:
            raise ServiceError('owned container state unconfirmed')
        return info['State']['Running']

    def stop(self):
        backend = self.backend
        self._reconcile()
        if backend.cid:
            info = backend.inspect_owned()
            if info['State']['Running']:
                backend.docker('stop', '-t', '3', backend.cid, timeout=10)
            # The CLI exit code alone is not this port's stop acknowledgement.
            if backend.inspect_owned()['State']['Running'] is not False:
                raise ServiceError('owned container stop unconfirmed')
        backend.active_model = None
        # Exact legacy two-reading physical GPU-idle barrier. Flash must first
        # have been stopped by its separate owning provider, not by PID guessing.
        backend.wait_idle()


def fixed_host_lifecycle(config, config_path):
    provider = OwnedOllama(config, config_path)
    return HostLifecycle(provider, snapshot, read_endpoint=lambda: provider.backend.address)
