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

"""Bounded unittest execution in an already-cached, operator-approved Python image.

No shell, pulls, host Python fallback, worktree writeback, or arbitrary command API.
The caller supplies a private, sealed snapshot and a trusted test manifest. Docker
is a containment boundary, not a cheating-proof grader or a hostile-kernel sandbox.
Real-container acceptance is separate from the pure/mocked unit tests for this file.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import stat
import subprocess
import time
import uuid
import unicodedata


_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{64}\Z")
_OFFICIAL = re.compile(r"(?:(?:docker\.io|index\.docker\.io)/)?(?:library/)?python@sha256:[0-9a-f]{64}\Z")
_LABEL = "local.platform.test-owner"
_MEMORY = 512 * 1024 * 1024
_TMPFS = "rw,noexec,nosuid,nodev,size=67108864,mode=1777"
_MAX_OUTPUT = 65536
_MAX_EXCERPT = 8192

# This script is trusted runner code, never supplied by the model or the project.
# Imports precede the project path. -I/-B ignore Python environment and bytecode.
_TEST_SCRIPT = r'''
import hashlib, json, os, stat, sys, unittest
start, pattern, nonce, expected_dev, expected_ino, manifest_json = sys.argv[1:]
identity = (int(expected_dev), int(expected_ino))
approved = json.loads(manifest_json)
manifest_sha256 = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
def verify_snapshot():
    root = os.stat("/task", follow_symlinks=False)
    if not stat.S_ISDIR(root.st_mode) or (root.st_dev, root.st_ino) != identity:
        raise RuntimeError("approved snapshot root identity mismatch")
    directories = {prefix for path in approved for prefix in
                   ("/".join(path.split("/")[:i]) for i in range(1, len(path.split("/"))))}
    seen_files, seen_dirs = set(), set()
    stack = [("", "/task")]
    while stack:
        relative, directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = relative + "/" + entry.name if relative else entry.name
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if path not in directories:
                        raise RuntimeError("unapproved snapshot directory")
                    seen_dirs.add(path)
                    stack.append((path, entry.path))
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and path in approved:
                    with os.fdopen(os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
                        opened = os.fstat(source.fileno())
                        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                            raise RuntimeError("snapshot file changed type")
                        digest = hashlib.sha256()
                        for chunk in iter(lambda: source.read(65536), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != approved[path]:
                        raise RuntimeError("approved snapshot file hash mismatch")
                    seen_files.add(path)
                else:
                    raise RuntimeError("unapproved snapshot entry or file type")
    if seen_files != set(approved) or seen_dirs != directories:
        raise RuntimeError("approved snapshot entries are missing")
verify_snapshot()
sys.path.insert(0, "/task")
suite = unittest.defaultTestLoader.discover(start, pattern=pattern, top_level_dir="/task")
result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)
verify_snapshot()
summary = {"completed": True, "tests": result.testsRun,
           "failures": len(result.failures), "errors": len(result.errors),
           "skipped": len(result.skipped), "successful": result.wasSuccessful(),
           "snapshot_identity": list(identity), "snapshot_manifest_sha256": manifest_sha256}
print("__LOCAL_TEST_SUMMARY_" + nonce + "__" + json.dumps(summary, sort_keys=True), flush=True)
sys.exit(0 if result.wasSuccessful() and result.testsRun > 0 else 1)
'''


# This is PID 1. It never imports project code. Even a setsid() descendant is
# killed by the Linux PID namespace when this bounded watchdog exits. This is
# an independent wall bound if the host controller disappears, not a grader.
_WATCHDOG_SCRIPT = r'''
import math, os, subprocess, sys
budget = float(sys.argv[1])
if not math.isfinite(budget) or not 0 < budget <= 60:
    os._exit(125)
child = subprocess.Popen([sys.executable, "-I", "-B", "-u", "-c", *sys.argv[2:]],
                         stdin=subprocess.DEVNULL, shell=False, close_fds=True)
try:
    code = child.wait(timeout=budget)
except subprocess.TimeoutExpired:
    try:
        child.kill()
    finally:
        os._exit(124)
except BaseException:
    os._exit(125)
os._exit(code if 0 <= code <= 255 else 1)
'''


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    output: bytes = b""
    failure: str | None = None
    client_stop_confirmed: bool = True


def _safe_text(value: str) -> bool:
    return not any(unicodedata.category(char).startswith("C") for char in value)


def _test_spec(start_dir: str, pattern: str) -> tuple[str, str]:
    if not isinstance(start_dir, str) or not isinstance(pattern, str):
        raise ValueError("test selection must be strings")
    if start_dir != ".":
        parts = start_dir.split("/")
        if (not start_dir or start_dir.startswith("/") or "\\" in start_dir
                or not _safe_text(start_dir)
                or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", p)
                       or p in (".", "..") for p in parts)):
            raise ValueError("unsafe relative test directory")
    if not re.fullmatch(r"test_[A-Za-z0-9_*?]*\.py", pattern):
        raise ValueError("only bounded test_*.py-style unittest patterns are allowed")
    return start_dir, pattern


class ContainerRunner:
    """One-shot runner. image_id and allowed_tests are trusted operator settings.

    reviewed_image_ids may allow explicitly audited purpose-built local images;
    it must never be populated from model/tool input. Otherwise official Python
    RepoDigest provenance is required.

    allowed_tests is an explicit allowlist of (relative directory, unittest glob),
    not a model-selected command. The default manifest allows only the root's
    test_*.py files. No original-worktree writes are ever enabled.
    """

    def __init__(self, image_id: str, timeout: float = 30, *,
                 allowed_tests=((".", "test_*.py"),), reviewed_image_ids=(),
                 python_executable="/usr/local/bin/python3"):
        if not isinstance(image_id, str) or not _IMAGE.fullmatch(image_id):
            raise ValueError("image_id must be an exact lowercase sha256 image ID")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 60):
            raise ValueError("timeout must be positive and at most 60 seconds")
        manifest = frozenset(_test_spec(*entry) for entry in allowed_tests)
        if not manifest or len(manifest) > 32:
            raise ValueError("an operator manifest of 1..32 test selections is required")
        reviewed = frozenset(reviewed_image_ids)
        if any(not isinstance(i, str) or not _IMAGE.fullmatch(i) for i in reviewed):
            raise ValueError("reviewed images must be exact operator-audited sha256 image IDs")
        if python_executable not in ("/usr/local/bin/python3", "/usr/bin/python3"):
            raise ValueError("only an operator-selected standard Python 3 interpreter path is allowed")
        self.python_executable = python_executable
        self.reviewed_image_ids = reviewed
        self.image_id = image_id
        self.timeout = float(timeout)
        self.allowed_tests = manifest
        self._docker = None

    def _call(self, arguments, *, timeout=5, cancel=None):
        """Bounded attached transport; kill only our Docker *client* on a limit.

        Container termination is separately verified by _cleanup. Never use
        communicate() with unbounded project output or inherit the user's Docker
        context, credentials, environment, stdin, shell, or remote endpoint.
        """
        if timeout <= 0:
            return CommandResult(None, failure="timeout")
        if cancel is not None and cancel.is_set():
            return CommandResult(None, failure="cancelled")
        argv = [self._docker, "--host=unix:///var/run/docker.sock",
                "--config=/nonexistent/local-platform-docker-config", *arguments]
        output = bytearray()
        failure = None
        selector = None
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    shell=False, close_fds=True,
                                    env={"PATH": os.defpath, "HOME": "/nonexistent"})
        except KeyboardInterrupt:
            return CommandResult(None, failure="cancelled", client_stop_confirmed=False)
        except OSError:
            return CommandResult(None, failure="transport")
        try:
            deadline = time.monotonic() + timeout
            selector = selectors.DefaultSelector()
            os.set_blocking(proc.stdout.fileno(), False)
            selector.register(proc.stdout, selectors.EVENT_READ)
            eof = False
            while True:
                if cancel is not None and cancel.is_set():
                    failure = "cancelled"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = "timeout"
                    break
                for key, _ in selector.select(min(0.05, remaining)):
                    try:
                        chunk = os.read(key.fileobj.fileno(), 4096)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(proc.stdout)
                        eof = True
                    else:
                        room = _MAX_OUTPUT - len(output)
                        output.extend(chunk[:room])
                        if len(chunk) > room:
                            failure = "output_limit"
                            break
                if failure or (eof and proc.poll() is not None):
                    break
        except KeyboardInterrupt:
            failure = "cancelled"
        except (OSError, ValueError):
            failure = "transport"
        finally:
            # A second Ctrl-C must not skip the structured uncertainty report.
            cleanup_interrupted = False
            try:
                if selector is not None:
                    selector.close()
            except KeyboardInterrupt:
                failure = "cancelled"
                cleanup_interrupted = True
            except (OSError, ValueError):
                failure = failure or "transport"
            try:
                if failure and proc.poll() is None:
                    proc.kill()  # Only this Popen child, never a process group.
            except KeyboardInterrupt:
                failure = "cancelled"
                cleanup_interrupted = True
            except OSError:
                failure = failure or "transport"
            client_stopped = False
            try:
                proc.wait(timeout=1)
                client_stopped = True
            except KeyboardInterrupt:
                failure = "cancelled"
                cleanup_interrupted = True
            except (OSError, subprocess.TimeoutExpired):
                failure = failure or "transport"
            try:
                proc.stdout.close()
            except KeyboardInterrupt:
                failure = "cancelled"
                cleanup_interrupted = True
            except OSError:
                failure = failure or "transport"
        return CommandResult(proc.returncode, bytes(output), failure, client_stopped and not cleanup_interrupted)

    @staticmethod
    def _object(result):
        if result.failure == "cancelled":
            raise KeyboardInterrupt
        if result.failure or not result.client_stop_confirmed or result.returncode != 0:
            raise ValueError("Docker command failed or exceeded its transport limit")
        value = json.loads(result.output)
        if isinstance(value, list):
            if len(value) != 1:
                raise ValueError("expected exactly one Docker object")
            value = value[0]
        if not isinstance(value, dict):
            raise ValueError("expected a Docker object")
        return value

    @staticmethod
    def _remaining(deadline):
        return max(0, min(5, deadline - time.monotonic()))

    def _preflight(self, deadline):
        if os.name != "posix" or not hasattr(os, "getuid"):
            return {"status": "blocked", "reason": "local Linux Docker and caller UID/GID required"}
        if os.getuid() == 0 or os.getgid() == 0:
            return {"status": "blocked", "reason": "a non-root caller UID and GID are required"}
        self._docker = shutil.which("docker")
        if not self._docker or not os.path.isabs(self._docker):
            return {"status": "blocked", "reason": "Docker executable is unavailable; no host fallback"}
        try:
            info = self._object(self._call(["info", "--format", "{{json .}}"],
                                          timeout=self._remaining(deadline)))
            if (info.get("OSType") != "linux"
                    or not all(info.get(field) is True for field in
                               ("MemoryLimit", "SwapLimit", "PidsLimit", "CpuCfsPeriod", "CpuCfsQuota"))
                    or "runc" not in info.get("Runtimes", {})
                    or not any(str(opt).startswith("name=seccomp,") or opt == "name=seccomp"
                               for opt in info.get("SecurityOptions", []))):
                raise ValueError("Docker cannot verify the required Linux isolation and resource limits")
            image = self._object(self._call(["image", "inspect", self.image_id],
                                           timeout=self._remaining(deadline)))
            if (image.get("Id") != self.image_id or image.get("Os") != "linux"
                    or not (self.image_id in self.reviewed_image_ids or
                            any(isinstance(d, str) and _OFFICIAL.fullmatch(d)
                                for d in image.get("RepoDigests", [])))
                    or image.get("Config", {}).get("Volumes")):
                raise ValueError("exact cached official Python or operator-audited image with no volumes required")
        except (ValueError, TypeError, AttributeError) as exc:
            return {"status": "blocked", "reason": str(exc)}
        return {"status": "ready", "image_id": self.image_id}

    def preflight(self):
        """Read-only checks. Never pulls, creates a container, or runs project code."""
        try:
            return self._preflight(time.monotonic() + min(10, self.timeout))
        except KeyboardInterrupt:
            return {"status": "blocked", "reason": "preflight cancelled before execution"}

    def _workspace(self, workspace, start_dir, pattern):
        spec = _test_spec(start_dir, pattern)
        if spec not in self.allowed_tests:
            raise ValueError("test selection is not in the operator manifest")
        if not isinstance(workspace, (str, os.PathLike)):
            raise ValueError("workspace must be an absolute private snapshot directory")
        raw = os.fspath(workspace)
        if not isinstance(raw, str) or not _safe_text(raw) or "," in raw:
            raise ValueError("workspace paths cannot contain commas or control characters")
        root = Path(raw)
        if (not root.is_absolute() or ".." in root.parts
                or root.resolve(strict=True) != root or root.is_symlink()):
            raise ValueError("workspace must be canonical, absolute, and not a symlink")
        mode = root.stat()
        if (not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.getuid()
                or mode.st_mode & 0o077):
            raise ValueError("workspace must be caller-owned and private (mode 0700 or stricter)")
        # Refuse sockets (including Docker sockets), devices, FIFOs and links in
        # the snapshot. Parent must keep this trusted snapshot sealed during run.
        entries = 0
        stack = [root]
        while stack:
            current = stack.pop()
            with os.scandir(current) as children:
                for child in children:
                    entries += 1
                    if entries > 10000:
                        raise ValueError("snapshot exceeds 10000 entries")
                    child_mode = child.stat(follow_symlinks=False).st_mode
                    if stat.S_ISDIR(child_mode):
                        stack.append(Path(child.path))
                    elif not stat.S_ISREG(child_mode):
                        raise ValueError("snapshot may contain only regular files and directories")
        start = root if start_dir == "." else root.joinpath(*PurePosixPath(start_dir).parts)
        if not start.is_dir():
            raise ValueError("manifest test directory does not exist in snapshot")
        return str(root), "/task" if start_dir == "." else "/task/" + start_dir

    @staticmethod
    def _snapshot(workspace, expected_identity=None, expected_hashes=None):
        """Detect deterministic replacement/extra entries, not same-UID races."""
        root = Path(workspace)
        if root.resolve(strict=True) != root or root.is_symlink():
            raise ValueError("snapshot path is no longer canonical")
        info = root.stat(follow_symlinks=False)
        identity = (info.st_dev, info.st_ino)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise ValueError("snapshot is no longer a caller-owned private directory")
        if expected_identity is not None:
            if (not isinstance(expected_identity, (tuple, list)) or len(expected_identity) != 2
                    or any(type(n) is not int or n < 0 for n in expected_identity)):
                raise ValueError("expected_identity must contain the trusted root device and inode")
            if identity != tuple(expected_identity):
                raise ValueError("approved snapshot root identity mismatch")
        if expected_hashes is not None:
            if not isinstance(expected_hashes, dict) or len(expected_hashes) > 64:
                raise ValueError("expected_hashes must be an operator manifest of at most 64 files")
            for path, digest in expected_hashes.items():
                if (not isinstance(path, str) or not path or path.startswith("/")
                        or "\\" in path or not _safe_text(path)
                        or any(p in ("", ".", "..") for p in path.split("/"))
                        or not isinstance(digest, str) or not _ID.fullmatch(digest)):
                    raise ValueError("invalid approved snapshot file path or hash")
        actual = {}
        directories = set()
        stack = [("", root)]
        while stack:
            relative, directory = stack.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = relative + "/" + entry.name if relative else entry.name
                    mode = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(mode.st_mode):
                        directories.add(path)
                        stack.append((path, Path(entry.path)))
                    elif stat.S_ISREG(mode.st_mode) and mode.st_nlink == 1:
                        if len(actual) >= 64:
                            raise ValueError("snapshot exceeds 64 approved files")
                        if expected_hashes is not None and path not in expected_hashes:
                            raise ValueError("unapproved snapshot file")
                        digest = hashlib.sha256()
                        with os.fdopen(os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
                            opened = os.fstat(source.fileno())
                            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                                raise ValueError("snapshot file changed type")
                            for chunk in iter(lambda: source.read(65536), b""):
                                digest.update(chunk)
                        actual[path] = digest.hexdigest()
                    else:
                        raise ValueError("snapshot may contain only single-link regular files and non-symlink directories")
        prefixes = {prefix for path in actual for prefix in
                    ("/".join(path.split("/")[:i]) for i in range(1, len(path.split("/"))))}
        if directories != prefixes or expected_hashes is not None and actual != expected_hashes:
            raise ValueError("approved snapshot directories/files/hashes do not match")
        manifest = json.dumps(actual, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if len(manifest.encode("utf-8")) > 1048576:
            raise ValueError("snapshot manifest exceeds 1 MiB")
        current = root.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity or root.resolve(strict=True) != root:
            raise ValueError("approved snapshot root changed during verification")
        return identity, manifest

    def _command(self, start, pattern, token, identity, manifest):
        return ["-I", "-B", "-u", "-c", _WATCHDOG_SCRIPT, str(self.timeout),
                _TEST_SCRIPT, start, pattern, token, str(identity[0]), str(identity[1]), manifest]

    def _create_args(self, workspace, start, pattern, token, name, identity, manifest):
        return ["create", "--pull=never", "--name=" + name,
                "--label=" + _LABEL + "=" + token,
                "--network=none", "--ipc=none", "--runtime=runc", "--restart=no", "--init=false",
                "--read-only", "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true", "--user=%d:%d" % (os.getuid(), os.getgid()),
                "--pids-limit=64", "--memory=536870912", "--memory-swap=536870912", "--cpus=1",
                "--ulimit=core=0:0", "--ulimit=nofile=128:128",
                "--tmpfs=/tmp:" + _TMPFS, "--log-driver=none", "--no-healthcheck",
                "--mount=type=bind,src=" + workspace + ",dst=/task,readonly,bind-propagation=rprivate",
                "--workdir=" + start, "--entrypoint=" + self.python_executable, self.image_id,
                *self._command(start, pattern, token, identity, manifest)]

    def _owned(self, obj, name, token, cid=None):
        return (isinstance(obj.get("Id"), str) and bool(_ID.fullmatch(obj["Id"]))
                and (cid is None or obj["Id"] == cid)
                and obj.get("Name") == "/" + name and obj.get("Image") == self.image_id
                and obj.get("Config", {}).get("Labels", {}).get(_LABEL) == token)

    def _isolation(self, obj, workspace, start, pattern, token, identity, manifest):
        h = obj.get("HostConfig", {})
        c = obj.get("Config", {})
        ulimits = h.get("Ulimits", [])
        limits_ok = (isinstance(ulimits, list) and len(ulimits) == 2
                     and all(isinstance(limit, dict) and set(limit) == {"Name", "Soft", "Hard"}
                             and isinstance(limit["Name"], str)
                             and type(limit["Soft"]) is int and type(limit["Hard"]) is int
                             for limit in ulimits)
                     and {(limit["Name"], limit["Soft"], limit["Hard"]) for limit in ulimits}
                     == {("core", 0, 0), ("nofile", 128, 128)})
        mounts = obj.get("Mounts", [])
        bind = [m for m in mounts if m.get("Type") == "bind"]
        allowed_mounts = all(m.get("Type") == "bind" or
                             (m.get("Type") == "tmpfs" and m.get("Destination") == "/tmp")
                             for m in mounts)
        return (h.get("NetworkMode") == "none" and h.get("ReadonlyRootfs") is True
                and h.get("Privileged") is False and h.get("CapDrop") == ["ALL"]
                and not h.get("CapAdd") and not h.get("Devices") and not h.get("DeviceRequests")
                and not h.get("VolumesFrom") and not h.get("Binds")
                and h.get("PidMode", "") == "" and h.get("IpcMode") == "none"
                and h.get("Runtime") == "runc" and h.get("RestartPolicy", {}).get("Name") == "no"
                and h.get("Init") is False
                and h.get("UTSMode", "") == "" and not h.get("PortBindings")
                and h.get("SecurityOpt") == ["no-new-privileges:true"]
                and limits_ok and h.get("PidsLimit") == 64 and h.get("Memory") == _MEMORY
                and h.get("MemorySwap") == _MEMORY and h.get("NanoCpus") == 1000000000
                and h.get("LogConfig", {}).get("Type") == "none"
                and h.get("Tmpfs") == {"/tmp": _TMPFS}
                and c.get("User") == "%d:%d" % (os.getuid(), os.getgid())
                and c.get("WorkingDir") == start and c.get("Entrypoint") == [self.python_executable]
                and c.get("Cmd") == self._command(start, pattern, token, identity, manifest)
                and c.get("OpenStdin") is False and c.get("Tty") is False
                and c.get("Healthcheck", {}).get("Test") == ["NONE"]
                and not c.get("Volumes") and allowed_mounts and len(bind) == 1
                and bind[0].get("Source") == workspace and bind[0].get("Destination") == "/task"
                and bind[0].get("RW") is False and bind[0].get("Propagation") == "rprivate")

    @staticmethod
    def _absent(result):
        return (not result.failure and result.client_stop_confirmed and result.returncode == 1
                and re.search(rb"(?:No such object|No such container):", result.output) is not None)

    def _inspect(self, target, deadline):
        return self._call(["container", "inspect", target], timeout=self._remaining(deadline))

    def _cleanup(self, name, token, cid):
        """No broad cleanup. Verify identity immediately before kill and remove."""
        deadline = time.monotonic() + 10
        try:
            inspected = self._inspect(cid or name, deadline)
            if self._absent(inspected):
                return True
            obj = self._object(inspected)
            if not self._owned(obj, name, token, cid):
                return False
            cid = obj["Id"]
            if obj.get("State", {}).get("Running") is True:
                result = self._call(["kill", "--signal=KILL", cid], timeout=self._remaining(deadline))
                if result.failure == "cancelled" or not result.client_stop_confirmed:
                    return False
                # A racing normal exit can make kill fail. Inspect, never retry kill.
                obj = self._object(self._inspect(cid, deadline))
                if not self._owned(obj, name, token, cid) or obj.get("State", {}).get("Running") is not False:
                    return False
            elif obj.get("State", {}).get("Running") is not False:
                return False
            # This exact immutable ID has just been verified stopped and owned.
            result = self._call(["rm", cid], timeout=self._remaining(deadline))
            if result.failure == "cancelled" or not result.client_stop_confirmed:
                return False
            # The removal response can be lost; absence is the decisive evidence.
            return self._absent(self._inspect(cid, deadline))
        except (KeyboardInterrupt, OSError, ValueError, TypeError, AttributeError):
            return False

    @staticmethod
    def _summary(output, token, identity=None, manifest_sha256=None):
        marker = ("__LOCAL_TEST_SUMMARY_" + token + "__").encode()
        lines = [line[len(marker):] for line in output.splitlines() if line.startswith(marker)]
        if len(lines) != 1 or not output.rstrip().splitlines()[-1].startswith(marker):
            raise ValueError("missing or ambiguous completed unittest summary")
        result = json.loads(lines[0])
        keys = {"completed", "tests", "failures", "errors", "skipped", "successful",
                "snapshot_identity", "snapshot_manifest_sha256"}
        if (not isinstance(result, dict) or set(result) != keys or result["completed"] is not True
                or type(result["successful"]) is not bool
                or any(type(result[k]) is not int or result[k] < 0
                       for k in ("tests", "failures", "errors", "skipped"))
                or result["tests"] == 0
                or result["successful"] and (result["failures"] != 0 or result["errors"] != 0)):
            raise ValueError("invalid or zero-test unittest summary")
        bound_identity = result["snapshot_identity"]
        if (not isinstance(bound_identity, list) or len(bound_identity) != 2
                or any(type(n) is not int or n < 0 for n in bound_identity)
                or not isinstance(result["snapshot_manifest_sha256"], str)
                or not _ID.fullmatch(result["snapshot_manifest_sha256"])
                or identity is not None and bound_identity != list(identity)
                or manifest_sha256 is not None and result["snapshot_manifest_sha256"] != manifest_sha256):
            raise ValueError("unittest summary is not bound to the approved snapshot")
        return result

    def run(self, workspace, start_dir=".", pattern="test_*.py", cancel=None, *,
            expected_identity=None, expected_hashes=None):
        """Return process/test evidence separately from any model-authored prose.

        Status is passed/failed/cancelled/uncertain, or blocked before execution.
        stop_confirmed requires verified removal or proof no container was created.
        expected_identity/expected_hashes are trusted caller evidence, never
        model tool arguments. If omitted they are derived from this snapshot.
        An uncertain run is never retried automatically.
        """
        began = time.monotonic()
        deadline = began + self.timeout
        result = {"status": "blocked", "exit_code": None, "test_count": 0,
                  "log_excerpt": "", "stop_confirmed": True, "duration": 0.0, "reason": "",
                  "snapshot_identity": None, "snapshot_manifest_sha256": None}
        name = token = cid = None
        created = False
        started = False
        output = b""
        identity = manifest = approved_hashes = None
        client_uncertain = False
        try:
            if cancel is not None and cancel.is_set():
                result.update(status="cancelled", reason="cancelled before container creation")
                return result
            workspace, start = self._workspace(workspace, start_dir, pattern)
            identity, manifest = self._snapshot(workspace, expected_identity, expected_hashes)
            approved_hashes = json.loads(manifest)
            result["snapshot_identity"] = list(identity)
            result["snapshot_manifest_sha256"] = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
            check = self._preflight(deadline)
            if check["status"] != "ready":
                result["reason"] = check["reason"]
                return result
            if cancel is not None and cancel.is_set():
                result.update(status="cancelled", reason="cancelled before container creation")
                return result
            self._snapshot(workspace, identity, approved_hashes)
            token = uuid.uuid4().hex
            name = "local-project-test-" + token
            created = True  # The following request may create even if transport is lost.
            result["stop_confirmed"] = False
            made = self._call(self._create_args(workspace, start, pattern, token, name, identity, manifest),
                              timeout=self._remaining(deadline), cancel=cancel)
            client_uncertain = not made.client_stop_confirmed
            if made.failure or made.returncode != 0 or client_uncertain:
                result.update(status="cancelled" if made.failure == "cancelled" else "failed",
                              reason="container create " + (made.failure or "failed"))
                return result
            candidate = made.output.strip()
            if re.fullmatch(rb"[0-9a-f]{64}", candidate) is None:
                cid = None
                result.update(status="uncertain", reason="container create returned no immutable ID")
                return result
            cid = candidate.decode("ascii")
            obj = self._object(self._inspect(cid, deadline))
            if not self._owned(obj, name, token, cid) or not self._isolation(obj, workspace, start, pattern, token, identity, manifest):
                result.update(status="blocked", reason="required container identity or isolation not verified")
                return result
            if cancel is not None and cancel.is_set():
                result.update(status="cancelled", reason="cancelled before container start")
                return result
            self._snapshot(workspace, identity, approved_hashes)
            started = True  # Treat a lost start response as possibly executing.
            result["status"] = "failed"
            attached = self._call(["start", "--attach", cid], timeout=max(0, deadline - time.monotonic()),
                                  cancel=cancel)
            output = attached.output
            client_uncertain = client_uncertain or not attached.client_stop_confirmed
            if attached.failure or client_uncertain:
                result.update(status="cancelled" if attached.failure == "cancelled" else "failed",
                              reason="attached transport " + (attached.failure or "client stop unconfirmed"))
                return result
            self._snapshot(workspace, identity, approved_hashes)
            final = self._object(self._inspect(cid, deadline))
            if not self._owned(final, name, token, cid) or final.get("State", {}).get("Running") is not False:
                result.update(status="uncertain", reason="container completion not verified")
                return result
            exit_code = final.get("State", {}).get("ExitCode")
            if type(exit_code) is not int:
                raise ValueError("container exit status is unavailable")
            result["exit_code"] = exit_code
            summary = self._summary(output, token, identity, result["snapshot_manifest_sha256"])
            result["test_count"] = summary["tests"]
            passed = attached.returncode == 0 and exit_code == 0 and summary["successful"]
            result.update(status="passed" if passed else "failed",
                          reason="completed unittest evidence" if passed else "process or unittest failure")
        except KeyboardInterrupt:
            result.update(status="cancelled", test_count=0, reason="interrupted by Ctrl-C")
        except (ValueError, OSError, TypeError, AttributeError) as exc:
            result.update(status="failed" if started else "blocked", test_count=0, reason=str(exc))
        finally:
            if created:
                try:
                    stopped = self._cleanup(name, token, cid)
                except KeyboardInterrupt:
                    stopped = False
                result["stop_confirmed"] = stopped and not client_uncertain
                if not result["stop_confirmed"]:
                    result.update(status="uncertain", test_count=0,
                                  reason=result["reason"] + "; owned-container/client stop/removal unconfirmed")
            if identity is not None:
                try:
                    self._snapshot(workspace, identity, approved_hashes)
                except KeyboardInterrupt:
                    result.update(status="uncertain", test_count=0, reason="snapshot completion verification interrupted")
                except (ValueError, OSError, TypeError, AttributeError) as exc:
                    if result["status"] != "uncertain":
                        result["status"] = "failed" if started else "blocked"
                    result.update(test_count=0, reason=str(exc))
            text = output[:_MAX_EXCERPT].decode("utf-8", "replace")
            result["log_excerpt"] = "".join(c for c in text if c in "\n\t" or _safe_text(c))
            result["duration"] = round(time.monotonic() - began, 3)
        return result
