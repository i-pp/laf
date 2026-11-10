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

import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from inference.laf_npu_service import service
from inference.laf_npu_service.verify_trace import verify


class InputTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(len(service.validate_input({"input": [0.0] * 3072})), 3072)

    def test_float32_normalization(self):
        values = service.validate_input({"input": [1e-100, 1.401298464324817e-45, 3.4028234663852886e38] + [0] * 3069})
        self.assertEqual(values[0], 0)
        self.assertGreater(values[1], 0)
        self.assertEqual(values[2], 3.4028234663852886e38)

    def test_invalid(self):
        for value in (None, [], {"input": []}, {"input": [True] * 3072},
                      {"input": [float("nan")] * 3072}, {"input": [float("inf")] * 3072},
                      {"input": [1e39] * 3072}, {"input": [10**1000] * 3072},
                      {"input": [0] * 3072, "extra": 1}, {"input": [0] * 3072, "model": "other"}):
            with self.subTest(value_type=type(value)), self.assertRaises(ValueError):
                service.validate_input(value)

    def test_profile_requires_real_provider_events(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "profile.json"
            for events in ([], [{"cat": "Node", "args": {"provider": "CPUExecutionProvider"}}],
                           [{"cat": "Node", "args": {"provider": p}} for p in ("VitisAIExecutionProvider", "CPUExecutionProvider")]):
                p.write_text(json.dumps(events))
                with self.assertRaises(ValueError):
                    service.profile_evidence(p, "VitisAI")
            p.write_text(json.dumps([{"cat": "Node", "args": {"provider": "VitisAIExecutionProvider"}}]))
            self.assertTrue(service.profile_evidence(p, "VitisAI")["execution_verified"])

    def test_trace_requires_successful_accel_commands_and_waits(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "trace"
            for text in ("", "ioctl(6</dev/accel/accel0>, DRM_IOCTL_AMDXDNA_EXEC_CMD, 0) = -1 EPERM\n",
                         "ioctl(6</dev/null>, DRM_IOCTL_AMDXDNA_EXEC_CMD, 0) = 0\n",
                         "ioctl(6</dev/accel/accel01>, DRM_IOCTL_AMDXDNA_EXEC_CMD, 0) = 0\n"
                         "ioctl(6</dev/accel/accel01>, DRM_IOCTL_SYNCOBJ_TIMELINE_WAIT, 0) = 0\n") :
                p.write_text(text)
                with self.assertRaises(ValueError):
                    verify(p)
            p.write_text("1 ioctl(6</dev/accel/accel0<char 261:0>>, DRM_IOCTL_AMDXDNA_EXEC_CMD, 0x123) = 0\n"
                         "1 ioctl(6</dev/accel/accel0<char 261:0>>, DRM_IOCTL_SYNCOBJ_TIMELINE_WAIT, 0x456) = 0\n")
            self.assertEqual(verify(p)["successful_exec_commands"], 1)


class ProbeTests(unittest.TestCase):
    def test_probe_decodes_worker_records_without_hardware(self):
        from types import SimpleNamespace
        from inference.laf_npu_service import probe_hardware
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'probe'
            def fake_run(*args, **kwargs):
                profile = target / 'profile.json'
                profile.write_text(json.dumps([{'cat': 'Node', 'args': {'provider': 'CPUExecutionProvider'}}]))
                for record in ({'profile': str(profile)}, {'logits': [0] * 10, 'class_index': 0}):
                    kwargs['stdout'].write('LAF_JSON ' + json.dumps(record) + '\n')
                return SimpleNamespace(returncode=0)
            with patch.object(probe_hardware.subprocess, 'run', side_effect=fake_run):
                result = probe_hardware.run(Path('/fake/worker'), Path('/fake/sdk'), target, 'CPU')
            self.assertEqual(result['logits'], [0] * 10)
            self.assertTrue(result['execution_verified'])


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=2)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class FakeWorker:
    status = "ready"
    error = None

    def health(self):
        return {"status": self.status, "execution_target": "cpu"}

    def classify(self, _):
        if self.error:
            raise self.error
        return {"logits": [0] * 10, "class_index": 0}


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.path = self.directory / "service.sock"
        self.worker = FakeWorker()
        self.server = service.serve(self.worker, self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, value=None, headers=None):
        conn = UnixConnection(str(self.path))
        data = None if value is None else json.dumps(value)
        conn.request(method, path, body=data, headers=headers or {})
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def test_socket_private(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.request("GET", "/health")[0], 200)

    def test_not_ready(self):
        self.worker.status = "not_ready"
        self.assertEqual(self.request("GET", "/health")[0], 503)

    def test_origin_rejected(self):
        self.assertEqual(self.request("GET", "/health", headers={"Origin": "https://example.test"})[0], 403)
        self.assertEqual(self.request("POST", "/v1/classify", headers={"Origin": "null"})[0], 403)

    def test_classify(self):
        self.assertEqual(self.request("POST", "/v1/classify", {"input": [0] * 3072}, {"Content-Type": "application/json"})[0], 200)

    def test_invalid_body(self):
        self.assertEqual(self.request("POST", "/v1/classify", {"input": []}, {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/v1/classify", {}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/v1/classify", {}, {"Content-Length": "100001"})[0], 413)

    def test_busy_and_unavailable(self):
        for error, code in ((BlockingIOError(), 429), (RuntimeError("private error"), 503)):
            self.worker.error = error
            status, body = self.request("POST", "/v1/classify", {"input": [0] * 3072}, {"Content-Type": "application/json"})
            self.assertEqual(status, code)
            self.assertNotIn("private", json.dumps(body))

    def test_existing_socket_not_removed(self):
        with self.assertRaises(OSError):
            service.serve(self.worker, self.path)
        self.assertTrue(self.path.exists())

    def test_public_parent_refused(self):
        d = self.directory / "public"
        d.mkdir(mode=0o755)
        # mkdir mode is filtered by umask; the negative fixture must be public.
        d.chmod(0o755)
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o755)
        with self.assertRaises(ValueError):
            service.serve(self.worker, d / "socket")
        self.assertFalse((d / "socket").exists())


class ParentPermissionTests(unittest.TestCase):
    def test_public_parent_fixture_is_independent_of_process_umask(self):
        # Exercise the same HTTP-test method without its socket-creating setUp.
        # The listener factory must never be reached for an actual 0755 parent.
        for mask in (0o022, 0o027, 0o077):
            with self.subTest(umask=oct(mask)), tempfile.TemporaryDirectory() as d:
                case = HttpTests('test_public_parent_refused')
                case.directory = Path(d)
                case.worker = FakeWorker()
                previous = os.umask(mask)
                try:
                    with patch.object(service, 'UnixHTTPServer', side_effect=AssertionError('public parent reached listener')) as listener:
                        case.test_public_parent_refused()
                    listener.assert_not_called()
                finally:
                    os.umask(previous)


class WorkerTests(unittest.TestCase):
    def test_early_exit_with_term_ignoring_descendant(self):
        for hold_stdout in (True, False):
            with self.subTest(hold_stdout=hold_stdout), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                model = root / "model"; model.write_bytes(b"fixture")
                child = root / "descendant.py"
                child.write_text("import os,signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n" +
                                 ("" if hold_stdout else "os.close(1)\n") +
                                 f"open({str(root / 'child.ready')!r},'w').write('ready')\n" + "time.sleep(60)\n")
                executable = root / "fake"
                executable.write_text("#!/usr/bin/python3\nimport json,sys,subprocess,os,time\n" +
                    f"child=subprocess.Popen(['/usr/bin/python3',{str(child)!r}])\n" +
                    f"open({str(root / 'child.pid')!r},'w').write(str(child.pid))\n" +
                    f"deadline=time.monotonic()+2\nwhile not os.path.exists({str(root / 'child.ready')!r}):\n if time.monotonic()>deadline: raise RuntimeError('child not ready')\n time.sleep(.001)\np=sys.argv[3]+'.json'\n" +
                    "open(p,'w').write(json.dumps([{'cat':'Node','args':{'provider':'CPUExecutionProvider'}}]))\n" +
                    "print('LAF_JSON '+json.dumps({'profile':p,'ort_version':'fixture'}),flush=True)\nos._exit(0)\n")
                executable.chmod(0o700)
                run = root / "run"; run.mkdir()
                with patch.object(service, "MODEL_SHA256", hashlib.sha256(b"fixture").hexdigest()):
                    w = service.Worker(executable, root, root, model, run, "CPU")
                try:
                    time.sleep(.05)
                    self.assertEqual(w.health()["status"], "not_ready")
                    begin = time.monotonic(); w.close()
                    self.assertLess(time.monotonic() - begin, 5.5)
                    pid = int((root / 'child.pid').read_text())
                    time.sleep(.05)
                    status = Path(f"/proc/{pid}/status")
                    if status.exists():
                        self.assertIn("State:\tZ", status.read_text())
                finally:
                    w.close()

    def test_worker_not_reading_stdin_obeys_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            model = root / "model"; model.write_bytes(b"fixture")
            executable = root / "fake"
            executable.write_text("#!/usr/bin/python3\nimport json,sys,time\np=sys.argv[3]+'.json'\n" +
                "open(p,'w').write(json.dumps([{'cat':'Node','args':{'provider':'CPUExecutionProvider'}}]))\n" +
                "print('LAF_JSON '+json.dumps({'profile':p,'ort_version':'fixture'}),flush=True)\ntime.sleep(60)\n")
            executable.chmod(0o700)
            run = root / "run"; run.mkdir()
            with patch.object(service, "MODEL_SHA256", hashlib.sha256(b"fixture").hexdigest()):
                w = service.Worker(executable, root, root, model, run, "CPU", timeout=.05)
            try:
                begin = time.monotonic()
                with self.assertRaises(RuntimeError): w.classify([.123456789] * 3072)
                self.assertLess(time.monotonic() - begin, 4.5)
                self.assertEqual(w.health()["status"], "not_ready")
            finally:
                w.close()

    def test_persistent_child_and_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            model = root / "model"; model.write_bytes(b"fixture")
            executable = root / "fake"
            executable.write_text('''#!/usr/bin/python3
import json,sys,time,os
assert "SECRET_FIXTURE" not in os.environ
p=sys.argv[3]+".json"
open(p,"w").write(json.dumps([{"cat":"Node","args":{"provider":"CPUExecutionProvider"}}]))
print("LAF_JSON "+json.dumps({"ready":True,"profile":p,"ort_version":"fixture"}),flush=True)
for line in sys.stdin:
 if line.startswith("1 "): time.sleep(2)
 print('LAF_JSON {"logits":[0,0,0,0,0,0,0,0,0,0],"class_index":0,"latency_ms":1}',flush=True)
''')
            executable.chmod(0o700)
            run = root / "run"; run.mkdir()
            with patch.object(service, "MODEL_SHA256", hashlib.sha256(b"fixture").hexdigest()), patch.dict(os.environ, {"SECRET_FIXTURE": "not_forwarded"}):
                w = service.Worker(executable, root, root, model, run, "CPU", timeout=0.05)
            try:
                pid = w.process.pid
                for _ in range(2):
                    self.assertEqual(w.classify([0] * 3072)["class_index"], 0)
                    self.assertEqual(w.process.pid, pid)
                self.assertTrue(w.lock.acquire(False))
                with self.assertRaises(BlockingIOError): w.classify([0] * 3072)
                w.lock.release()
                with self.assertRaises(RuntimeError): w.classify([1] * 3072)
                self.assertEqual(w.health()["status"], "not_ready")
                self.assertIsNotNone(w.process.poll())
            finally:
                w.close()


if __name__ == "__main__":
    unittest.main()
