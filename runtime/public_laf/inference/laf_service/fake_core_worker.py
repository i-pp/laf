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

"""Test fixture only. Excluded from the Core image allowlist."""
import json
import os
import subprocess
import sys

child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3600)'])
print(json.dumps({'starting': True, 'engine_pid': child.pid}), flush=True)
print(json.dumps({'ready': True, 'engine_pid': child.pid}), flush=True)
if os.environ.get('LAF_TEST_WRAPPER_EXIT') == '1':
    raise SystemExit(0)
for line in sys.stdin:
    body = json.loads(line)
    print(json.dumps({'content': 'fake Flash', 'finish_reason': 'stop',
                      'prompt_tokens': 1, 'completion_tokens': 1}), flush=True)
