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

"""Private Unix-socket HTTP adapter for a persistent, single-session ORT worker."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import selectors
import struct
import socket
from socketserver import ThreadingMixIn
import stat
import subprocess
import threading
import time
from typing import Any

MODEL = "ryzenai-quicktest-10class"
MODEL_SHA256 = "7a78c7e85bac3a0681e3d2f77e69093a761e1c8f62f7fc65ff5ce3e1b82c5ba3"
MAX_BODY = 100_000


def validate_input(data: Any) -> list[float]:
    if not isinstance(data, dict) or set(data) - {"model", "input"}:
        raise ValueError("expected model and input only")
    if data.get("model", MODEL) != MODEL:
        raise ValueError("unknown model")
    values = data.get("input")
    if not isinstance(values, list) or len(values) != 3072:
        raise ValueError("input must contain exactly 3072 finite float32 values")
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or abs(v) > 3.4028234663852886e38 or not math.isfinite(v) for v in values):
        raise ValueError("input must contain exactly 3072 finite float32 values")
    return list(struct.unpack("<3072f", struct.pack("<3072f", *values)))


def profile_evidence(path: Path, provider: str) -> dict[str, Any]:
    """EP registration is insufficient: require warmup compute events on the EP."""
    if path.stat().st_size > 20_000_000:
        raise ValueError("oversized ORT profile")
    events = json.loads(path.read_text())
    counts = Counter(e.get("args", {}).get("provider") for e in events
                     if e.get("cat") == "Node" and e.get("args", {}).get("provider"))
    expected = "VitisAIExecutionProvider" if provider == "VitisAI" else "CPUExecutionProvider"
    if not counts.get(expected) or set(counts) != {expected}:
        raise ValueError(f"unverified execution provider: {dict(counts)}")
    return {"execution_verified": True, "verification_kind": "ort_warmup_profile",
            "profile_node_events": dict(counts),
            "profile_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class Worker:
    def __init__(self, executable: Path, sdk: Path, xrt: Path, model: Path,
                 run_dir: Path, provider: str, timeout: float = 10, startup_timeout: float = 180):
        if hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("model hash mismatch")
        self.provider = provider
        self.timeout = timeout
        self.lock = threading.Lock()
        self.close_lock = threading.Lock()
        self.responses: queue.Queue[Any] = queue.Queue(maxsize=2)
        self.evidence: dict[str, Any] = {}
        self.closed = False
        for leaf in ("home", "cache", "tmp"):
            (run_dir / leaf).mkdir(mode=0o700)
        self.log = (run_dir / "worker.log").open("x")
        env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": str(run_dir / "home"),
               "TMPDIR": str(run_dir / "tmp"), "XDG_CACHE_HOME": str(run_dir / "cache"),
               "XILINX_XRT": str(xrt),
               "LD_LIBRARY_PATH": ":".join(map(str, [xrt / "lib", sdk / "cpp-test/sdk/onnxruntime/lib", sdk / "cpp-test/peano"]))}
        self.process = subprocess.Popen([str(executable), str(model), provider, str(run_dir / "ort-profile")],
                                        cwd=run_dir, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1, start_new_session=True)
        assert self.process.stdin
        os.set_blocking(self.process.stdin.fileno(), False)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            ready = self._receive(startup_timeout)
            profile = Path(ready["profile"]).resolve(strict=True)
            if profile.parent != run_dir.resolve():
                raise ValueError("profile escaped private run directory")
            self.evidence = profile_evidence(profile, provider)
            self.evidence["ort_version"] = ready["ort_version"]
            if provider == "VitisAI":
                self.log.flush()
                compiler_log = (run_dir / "worker.log").read_text(errors="replace")
                dpu = re.findall(r"DPU subgraph number (\d+)", compiler_log)
                cpu = re.findall(r"CPU subgraph number (\d+)", compiler_log)
                if not dpu or int(dpu[-1]) < 1 or not cpu:
                    raise ValueError("NPU/CPU compiler partitions not evidenced")
                self.evidence.update(npu_subgraphs=int(dpu[-1]), internal_cpu_subgraphs=int(cpu[-1]),
                                     execution_scope="mixed_npu_cpu" if int(cpu[-1]) else "npu_compute",
                                     ort_cpu_fallback_disabled=True)
            else:
                self.evidence.update(execution_scope="cpu_only", ort_cpu_fallback_disabled=False)
        except BaseException:
            self.close()
            raise

    def _read(self) -> None:
        assert self.process.stdout
        try:
            while True:
                line = self.process.stdout.readline(MAX_BODY + 1)
                if not line:
                    break
                if len(line) > MAX_BODY:
                    raise ValueError("oversized worker response")
                if line.startswith("ACEIS_JSON "):
                    self.responses.put_nowait(json.loads(line[11:]))
                else:
                    self.log.write(line)
                    self.log.flush()
        except Exception:
            pass
        finally:
            self.process.stdout.close()
            try:
                self.responses.put_nowait(None)
            except queue.Full:
                pass

    def _receive(self, timeout: float) -> dict[str, Any]:
        try:
            value = self.responses.get(timeout=timeout)
        except queue.Empty as exc:
            raise RuntimeError("worker deadline exceeded") from exc
        if not isinstance(value, dict):
            raise RuntimeError("worker stopped")
        return value

    def _running(self) -> bool:
        # WNOWAIT retains the leader PID until close has signalled its whole group.
        # Popen.poll would reap an early-exited leader and allow PGID reuse.
        if self.closed:
            return False
        try:
            return os.waitid(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
        except ChildProcessError:
            return False

    def _write(self, values: list[float], deadline: float) -> None:
        assert self.process.stdin
        payload = memoryview((" ".join(format(v, ".9g") for v in values) + "\n").encode("ascii"))
        fd = self.process.stdin.fileno()
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_WRITE)
            while payload:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise RuntimeError("worker write deadline exceeded")
                try:
                    count = os.write(fd, payload)
                except BlockingIOError:
                    continue
                payload = payload[count:]

    def health(self) -> dict[str, Any]:
        ready = self._running() and bool(self.evidence)
        return {"status": "ready" if ready else "not_ready", "backend": "ryzenai",
                "model": MODEL, "model_sha256": MODEL_SHA256,
                "provider": self.provider, "execution_target": "npu" if self.provider == "VitisAI" else "cpu",
                # This is ORT provider execution evidence, not a numeric accuracy guarantee.
                **self.evidence}

    def classify(self, values: list[float]) -> dict[str, Any]:
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError("classifier busy")
        try:
            if self.health()["status"] != "ready":
                raise RuntimeError("worker unavailable")
            assert self.process.stdin
            try:
                deadline = time.monotonic() + self.timeout
                self._write(values, deadline)
                result = self._receive(max(0, deadline - time.monotonic()))
                logits = result.get("logits")
                if not isinstance(logits, list) or len(logits) != 10 or any(
                        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in logits):
                    raise RuntimeError("invalid worker output")
                if result.get("class_index") != max(range(10), key=logits.__getitem__):
                    raise RuntimeError("invalid class index")
                latency = result.get("latency_ms")
                if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0:
                    raise RuntimeError("invalid latency")
                return {**self.health(), **result}
            except Exception:
                self.close()  # No hidden retry or CPU fallback after a timeout/failure.
                raise
        finally:
            self.lock.release()

    def close(self) -> None:
        with self.close_lock:
            self._close()

    def _close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # The leader can exit while an SDK compiler descendant still owns stdout.
        # Signal only the new session/process group created for this worker.
        def signal_group(sig: int) -> None:
            try:
                os.killpg(self.process.pid, sig)
            except ProcessLookupError:
                pass
        signal_group(signal.SIGTERM)
        self.reader.join(timeout=3)
        # The unreaped leader pins this PGID even if descendants close stdout.
        # Finish group signalling before Popen.wait is allowed to reap the PID.
        signal_group(signal.SIGKILL)
        self.reader.join(timeout=1)
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            raise RuntimeError("owned worker did not terminate")
        if self.process.stdin:
            self.process.stdin.close()
        # The reader owns stdout.close; never close a stream locked in readline.
        if not self.reader.is_alive():
            self.log.close()


class Handler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_: Any) -> None:
        pass  # Inputs and model outputs are not request-logged.

    def respond(self, code: int, value: Any) -> None:
        body = json.dumps(value, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.headers.get("Origin") is not None:
            return self.respond(403, {"error": "browser origins are not allowed"})
        if self.path != "/health":
            return self.respond(404, {"error": "not found"})
        health = self.server.worker.health()
        self.respond(200 if health["status"] == "ready" else 503, health)

    def do_POST(self) -> None:
        if self.headers.get("Origin") is not None:
            return self.respond(403, {"error": "browser origins are not allowed"})
        if self.path != "/v1/classify":
            return self.respond(404, {"error": "not found"})
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            return self.respond(400, {"error": "one Content-Length required"})
        try:
            length = int(self.headers["Content-Length"])
            if not 0 < length <= MAX_BODY:
                return self.respond(413, {"error": "request too large"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.respond(415, {"error": "application/json required"})
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete request")
            values = validate_input(json.loads(raw))
        except (ValueError, OverflowError):
            return self.respond(400, {"error": "invalid classification request"})
        try:
            self.respond(200, self.server.worker.classify(values))
        except BlockingIOError:
            self.respond(429, {"error": "classifier busy"})
        except Exception:
            self.respond(503, {"error": "classifier unavailable"})


class UnixHTTPServer(ThreadingMixIn, HTTPServer):
    address_family = socket.AF_UNIX
    daemon_threads = True
    request_queue_size = 4

    def __init__(self, *args: Any, **kwargs: Any):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_bind(self) -> None:
        # HTTPServer.server_bind assumes an IP tuple; this server is Unix-only.
        super(HTTPServer, self).server_bind()
        self.server_name = "localhost"
        self.server_port = 0


def serve(worker: Worker, path: Path) -> UnixHTTPServer:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("socket parent must be an owned, private 0700 directory")
    # Never remove a preexisting socket: it may belong to a running instance.
    old_umask = os.umask(0o177)
    try:
        server = UnixHTTPServer(str(path), Handler)
    finally:
        os.umask(old_umask)
    server.worker = worker
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    for arg in ("worker", "sdk", "xrt", "model", "run-dir", "socket"):
        parser.add_argument("--" + arg, type=Path, required=True)
    parser.add_argument("--provider", choices=("CPU", "VitisAI"), default="VitisAI")
    args = parser.parse_args()
    def stop(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    os.umask(0o077)
    args.run_dir.mkdir(mode=0o700)  # A fresh directory for every launch.
    worker = Worker(args.worker.resolve(strict=True), args.sdk.resolve(strict=True),
                    args.xrt.resolve(strict=True), args.model.resolve(strict=True), args.run_dir.resolve(), args.provider)
    server = None
    monitor_stop = threading.Event()
    exit_code = 0
    try:
        server = serve(worker, args.socket.absolute())
        socket_identity = args.socket.stat()
        def monitor() -> None:
            # Surface owned-worker failure to a bounded external supervisor.
            while not monitor_stop.wait(0.25):
                if worker.health()["status"] != "ready":
                    server.shutdown()
                    return
        threading.Thread(target=monitor, daemon=True).start()
        print(json.dumps(worker.health()), flush=True)
        server.serve_forever(poll_interval=0.2)
        if worker.health()["status"] != "ready":
            exit_code = 1
    except KeyboardInterrupt:
        pass
    finally:
        monitor_stop.set()
        if server is not None:
            server.server_close()
            try:
                current = args.socket.lstat()
                if (current.st_dev, current.st_ino) == (socket_identity.st_dev, socket_identity.st_ino):
                    args.socket.unlink()
            except FileNotFoundError:
                pass
        worker.close()
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
