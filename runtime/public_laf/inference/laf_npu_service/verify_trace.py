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

"""Verify bounded hardware-probe evidence. Never execute trace contents."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def verify(path: Path) -> dict:
    if path.stat().st_size > 10_000_000:
        raise ValueError("oversized trace")
    lines = path.read_text().splitlines()
    # -yy ties each ioctl to the physical accel device, not an arbitrary fd.
    submissions = sum(bool(re.search(r"ioctl\(\d+</dev/accel/accel0(?:<char \d+:\d+>)?>, DRM_IOCTL_AMDXDNA_EXEC_CMD,.*\) = 0$", line)) for line in lines)
    waits = sum(bool(re.search(r"ioctl\(\d+</dev/accel/accel0(?:<char \d+:\d+>)?>, DRM_IOCTL_SYNCOBJ_TIMELINE_WAIT,.*\) = 0$", line)) for line in lines)
    if not submissions or not waits:
        raise ValueError("no successful NPU command submission/completion wait")
    return {"hardware_execution_verified": True, "successful_exec_commands": submissions,
            "successful_completion_waits": waits, "trace_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.trace), indent=2))
