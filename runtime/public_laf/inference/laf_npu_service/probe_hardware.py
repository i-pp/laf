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

"""One bounded CPU/NPU comparison with real driver submission evidence.

No SDK staging, installation, reset, service control, download, or implicit retry.
Run only when the NPU is idle and shared-memory headroom is sufficient.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from .service import MODEL_SHA256, profile_evidence
from .verify_trace import verify


def run(worker: Path, sdk: Path, target: Path, provider: str) -> dict:
    target.mkdir(mode=0o700)
    for leaf in ("home", "cache", "tmp"):
        (target / leaf).mkdir(mode=0o700)
    xrt = sdk / "xrt-2.25.37/opt/xilinx/xrt"
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": str(target / "home"),
           "TMPDIR": str(target / "tmp"), "XDG_CACHE_HOME": str(target / "cache"),
           "XILINX_XRT": str(xrt), "LD_LIBRARY_PATH": ":".join(map(str, [
               xrt / "lib", sdk / "cpp-test/sdk/onnxruntime/lib", sdk / "cpp-test/peano"]))}
    command = [str(worker), str(sdk / "cpp-test/samples/quicktest/test_model.onnx"), provider, str(target / "profile")]
    if provider == "VitisAI":
        command = [shutil.which("strace") or "/usr/bin/strace", "-f", "-yy", "-e", "trace=ioctl", "-o", str(target / "ioctl.trace"), *command]
    serialized = " ".join(format(float(i % 251) / 250, ".9g") for i in range(3072)) + "\n"
    with (target / "stdout.log").open("x") as out, (target / "stderr.log").open("x") as err:
        # GNU timeout bounds the complete traced tree, not only the strace parent.
        completed = subprocess.run(["/usr/bin/timeout", "--signal=TERM", "--kill-after=5s", "180s", *command],
                                   input=serialized, stdout=out, stderr=err, text=True, cwd=target, env=env, check=False)
    if completed.returncode:
        raise RuntimeError(f"{provider} probe exited {completed.returncode}; inspect private logs")
    records = [json.loads(line[len("LAF_JSON "):]) for line in (target / "stdout.log").read_text().splitlines() if line.startswith("LAF_JSON ")]
    if len(records) != 2:
        raise RuntimeError("expected exactly warmup readiness and one inference result")
    profile = Path(records[0]["profile"]).resolve(strict=True)
    if profile.parent != target.resolve():
        raise RuntimeError("profile escaped probe directory")
    return {**records[1], **profile_evidence(profile, provider)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new private directory")
    args = parser.parse_args()
    os.umask(0o077)
    sdk, worker = args.sdk.resolve(strict=True), args.worker.resolve(strict=True)
    model = sdk / "cpp-test/samples/quicktest/test_model.onnx"
    if hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError("model hash mismatch")
    args.output.mkdir(mode=0o700)
    root = args.output.resolve(strict=True)
    cpu = run(worker, sdk, root / "cpu", "CPU")
    npu = run(worker, sdk, root / "npu", "VitisAI")
    hardware = verify(root / "npu/ioctl.trace")
    difference = max(abs(a - b) for a, b in zip(cpu["logits"], npu["logits"], strict=True))
    result = {"model_sha256": MODEL_SHA256, "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
              "cpu": cpu, "npu": npu, "hardware": hardware, "max_absolute_logit_difference": difference,
              "numeric_equal_for_this_input": difference == 0, "accuracy_validated": False}
    (root / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if difference != 0:
        raise RuntimeError("outputs differ; no application tolerance has been approved for this probe")


if __name__ == "__main__":
    main()
