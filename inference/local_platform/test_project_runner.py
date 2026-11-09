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

"""Pure/mocked regressions only: never launch Docker or project/model code here."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import sys
import unittest
from unittest.mock import Mock, patch

try:
    from . import project_runner as runner
except ImportError:
    import project_runner as runner

IMAGE = "sha256:" + "a" * 64
CID = "b" * 64
DIGEST = "docker.io/library/python@sha256:" + "c" * 64


def encoded(value):
    return runner.CommandResult(0, json.dumps(value).encode())


def summary(token, identity=(1, 2), manifest_sha256="d" * 64, **changes):
    value = {"completed": True, "tests": 3, "errors": 0, "failures": 0,
             "skipped": 0, "successful": True, "snapshot_identity": list(identity),
             "snapshot_manifest_sha256": manifest_sha256}
    value.update(changes)
    return b"...\nRan 3 tests\nOK\n" + ("__LOCAL_TEST_SUMMARY_" + token + "__").encode() + json.dumps(value).encode() + b"\n"


class FakeDocker:
    """In-memory daemon/transport, never calls a subprocess or executes a test."""

    def __init__(self):
        self.calls = []
        self.info = {"OSType": "linux", "MemoryLimit": True, "SwapLimit": True,
                     "PidsLimit": True, "CpuCfsPeriod": True, "CpuCfsQuota": True,
                     "SecurityOptions": ["name=seccomp,profile=builtin"], "Runtimes": {"runc": {}}}
        self.image = {"Id": IMAGE, "Os": "linux", "RepoDigests": [DIGEST], "Config": {"Volumes": None}}
        self.obj = None
        self.start_result = None
        self.summary_changes = {}
        self.client_exit = 0
        self.container_exit = 0
        self.alter = None
        self.start_inspect_fails = False
        self.started = False
        self.create_result = None
        self.kill_result = None
        self.rm_result = None
        self.cleanup_inspect_failure = False
        self.keep_running = False

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if args[0] == "info":
            return encoded(self.info)
        if args[:2] == ["image", "inspect"]:
            return encoded([self.image])
        if args[0] == "create":
            options = dict(a[2:].split("=", 1) for a in args[1:] if a.startswith("--") and "=" in a)
            token = options["label"].split("=", 1)[1]
            self.token = token
            self.name = options["name"]
            source = options["mount"].split(",src=", 1)[1].split(",dst=", 1)[0]
            self.identity = (int(args[-3]), int(args[-2]))
            self.manifest_sha256 = hashlib.sha256(args[-1].encode("utf-8")).hexdigest()
            self.obj = {
                "Id": CID, "Name": "/" + self.name, "Image": IMAGE,
                "Config": {"Labels": {runner._LABEL: token}, "User": options["user"],
                           "WorkingDir": options["workdir"], "Entrypoint": [options["entrypoint"]],
                           "Cmd": args[args.index(IMAGE) + 1:], "Healthcheck": {"Test": ["NONE"]},
                           "OpenStdin": False, "Tty": False, "Volumes": None},
                "HostConfig": {"NetworkMode": "none", "IpcMode": "none", "Runtime": "runc",
                               "RestartPolicy": {"Name": "no"}, "Init": False, "ReadonlyRootfs": True,
                               "Privileged": False, "CapDrop": ["ALL"], "CapAdd": None,
                               "Devices": [], "DeviceRequests": None, "VolumesFrom": None,
                               "Binds": None, "PidMode": "", "UTSMode": "", "PortBindings": {},
                               "SecurityOpt": ["no-new-privileges:true"], "PidsLimit": 64,
                               "Memory": runner._MEMORY, "MemorySwap": runner._MEMORY,
                               "Ulimits": [{"Name": "core", "Soft": 0, "Hard": 0},
                                           {"Name": "nofile", "Soft": 128, "Hard": 128}],
                               "NanoCpus": 1000000000, "LogConfig": {"Type": "none"},
                               "Tmpfs": {"/tmp": runner._TMPFS}},
                "Mounts": [{"Type": "bind", "Source": source, "Destination": "/task",
                            "RW": False, "Propagation": "rprivate"}],
                "State": {"Running": False, "ExitCode": 0}}
            if self.alter:
                self.alter(self.obj)
            return self.create_result or runner.CommandResult(0, (CID + "\n").encode())
        if args[:2] == ["container", "inspect"]:
            if (self.started and self.start_inspect_fails) or self.cleanup_inspect_failure:
                return runner.CommandResult(None, failure="transport")
            if self.obj is None:
                return runner.CommandResult(1, b"Error: No such object: " + args[2].encode())
            return encoded([self.obj])
        if args[0] == "start":
            self.started = True
            self.obj["State"] = {"Running": self.keep_running or (self.start_result is not None and self.start_result.failure is not None),
                                  "ExitCode": self.container_exit}
            return self.start_result or runner.CommandResult(self.client_exit, summary(self.token, identity=self.identity, manifest_sha256=self.manifest_sha256, **self.summary_changes))
        if args[0] == "kill":
            self.obj["State"]["Running"] = False
            self.obj["State"]["ExitCode"] = 137
            return self.kill_result or runner.CommandResult(0, CID.encode())
        if args[0] == "rm":
            self.obj = None
            return self.rm_result or runner.CommandResult(0, CID.encode())
        raise AssertionError("unexpected command: " + repr(args))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="trusted-runner-test-")
        self.addCleanup(self.temp.cleanup)
        self.workspace = self.temp.name
        self.docker = FakeDocker()
        self.subject = runner.ContainerRunner(IMAGE)
        self.subject._call = self.docker
        self.which = patch.object(runner.shutil, "which", return_value="/usr/bin/docker")
        self.which.start()
        self.addCleanup(self.which.stop)
        self.popen = patch.object(runner.subprocess, "Popen", side_effect=AssertionError("real subprocess forbidden in these tests"))
        self.popen.start()
        self.addCleanup(self.popen.stop)

    def run_subject(self, **kwargs):
        return self.subject.run(self.workspace, **kwargs)

    def commands(self, verb):
        return [c for c in self.docker.calls if c[0] == verb]

    def test_constructor_rejects_tags_short_ids_commands_and_bad_timeouts(self):
        for image in ("python:3.12", "a" * 64, "sha256:" + "A" * 64, "--privileged", IMAGE + "\n"):
            with self.subTest(image=image), self.assertRaises(ValueError):
                runner.ContainerRunner(image)
        for seconds in (0, -1, 61, float("inf"), float("nan"), True, "30"):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                runner.ContainerRunner(IMAGE, timeout=seconds)

    def test_constructor_rejects_unsafe_or_empty_manifest(self):
        for manifest in ((), (("../tests", "test_*.py"),), ((".", "*.py"),), ((".", "test_;.py"),)):
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                runner.ContainerRunner(IMAGE, allowed_tests=manifest)
        with self.assertRaises(ValueError):
            runner.ContainerRunner(IMAGE, reviewed_image_ids=("python:latest",))

    def test_preflight_is_read_only_and_ready(self):
        self.assertEqual(self.subject.preflight()["status"], "ready")
        self.assertEqual([c[0] for c in self.docker.calls], ["info", "image"])

    def test_missing_docker_blocks_without_any_command_or_fallback(self):
        with patch.object(runner.shutil, "which", return_value=None):
            self.assertEqual(self.run_subject()["status"], "blocked")
        self.assertEqual(self.docker.calls, [])

    def test_root_caller_is_blocked(self):
        with patch.object(runner.os, "getuid", return_value=0):
            result = self.run_subject()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.commands("create"), [])

    def test_required_daemon_isolation_and_limits_fail_closed(self):
        for field, value in (("OSType", "windows"), ("MemoryLimit", False), ("SwapLimit", False),
                             ("PidsLimit", False), ("CpuCfsQuota", False), ("CpuCfsPeriod", False),
                             ("SecurityOptions", []), ("Runtimes", {"nvidia": {}})):
            with self.subTest(field=field):
                original = self.docker.info[field]
                self.docker.info[field] = value
                self.assertEqual(self.subject.preflight()["status"], "blocked")
                self.docker.info[field] = original
        self.assertEqual(self.commands("create"), [])

    def test_uncached_wrong_identity_nonofficial_or_volume_image_never_pulls(self):
        for changes in ({"RepoDigests": ["evil/python@sha256:" + "c" * 64]}, {"RepoDigests": []},
                        {"Id": "sha256:" + "d" * 64}, {"Os": "windows"},
                        {"Config": {"Volumes": {"/writable": {}}}}):
            with self.subTest(changes=changes):
                original = copy.deepcopy(self.docker.image)
                self.docker.image.update(changes)
                self.assertEqual(self.run_subject()["status"], "blocked")
                self.docker.image = original
        self.assertFalse(any(c[0] in ("pull", "run", "create", "build") for c in self.docker.calls))

    def test_cached_image_inspect_failure_blocks(self):
        real = self.subject._call
        self.subject._call = lambda args, **kw: runner.CommandResult(1, b"No such image") if args[:2] == ["image", "inspect"] else real(args, **kw)
        result = self.run_subject()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(self.commands("create"))

    def test_audited_exact_image_is_allowed_without_python_provenance(self):
        self.subject = runner.ContainerRunner(IMAGE, reviewed_image_ids=(IMAGE,))
        self.subject._call = self.docker
        self.docker.image["RepoDigests"] = []
        self.assertEqual(self.run_subject()["status"], "passed")

    def test_operator_can_select_ubuntu_interpreter_but_not_arbitrary_entrypoints(self):
        self.subject = runner.ContainerRunner(IMAGE, reviewed_image_ids=(IMAGE,), python_executable="/usr/bin/python3")
        self.subject._call = self.docker
        self.assertEqual(self.run_subject()["status"], "passed")
        self.assertIn("--entrypoint=/usr/bin/python3", self.commands("create")[0])
        for executable in ("python3", "/bin/bash", "/task/python3", "/usr/bin/python3 -c id"):
            with self.subTest(executable=executable), self.assertRaises(ValueError):
                runner.ContainerRunner(IMAGE, python_executable=executable)

    def test_trusted_script_has_fixed_snapshot_top_level_and_one_output_stream(self):
        self.assertIn('top_level_dir="/task"', runner._TEST_SCRIPT)
        self.assertIn('sys.path.insert(0, "/task")', runner._TEST_SCRIPT)
        self.assertIn('TextTestRunner(stream=sys.stdout', runner._TEST_SCRIPT)
        self.assertNotIn('top_level_dir=start', runner._TEST_SCRIPT)

    def test_audited_allowlist_does_not_allow_other_or_missing_images(self):
        self.subject = runner.ContainerRunner(IMAGE, reviewed_image_ids=("sha256:" + "f" * 64,))
        self.subject._call = self.docker
        self.docker.image["RepoDigests"] = []
        self.assertEqual(self.run_subject()["status"], "blocked")

    def test_success_requires_process_and_completed_nonzero_summary(self):
        result = self.run_subject()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["test_count"], 3)
        self.assertTrue(result["stop_confirmed"])
        self.assertGreaterEqual(result["duration"], 0)
        self.assertEqual(len(self.commands("start")), 1)
        self.assertEqual(len(self.commands("rm")), 1)
        self.assertEqual(self.commands("kill"), [])

    def test_create_command_is_fixed_and_fully_restricted(self):
        self.run_subject()
        command = self.commands("create")[0]
        required = ["--pull=never", "--network=none", "--ipc=none", "--runtime=runc", "--restart=no", "--init=false",
                    "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
                    "--user=%d:%d" % (os.getuid(), os.getgid()), "--pids-limit=64",
                    "--memory=536870912", "--memory-swap=536870912", "--cpus=1",
                    "--ulimit=core=0:0", "--ulimit=nofile=128:128",
                    "--tmpfs=/tmp:" + runner._TMPFS, "--log-driver=none", "--no-healthcheck",
                    "--entrypoint=/usr/local/bin/python3", IMAGE]
        for option in required:
            self.assertIn(option, command)
        self.assertEqual(command[command.index(IMAGE) + 1:command.index(IMAGE) + 5], ["-I", "-B", "-u", "-c"])
        self.assertIn(runner._TEST_SCRIPT, command)
        self.assertIn(runner._WATCHDOG_SCRIPT, command)
        self.assertEqual(command[command.index(IMAGE) + 5], runner._WATCHDOG_SCRIPT)
        self.assertEqual(command[-3:-1], [str(os.stat(self.workspace).st_dev), str(os.stat(self.workspace).st_ino)])
        self.assertEqual(json.loads(command[-1]), {})
        mount = next(a for a in command if a.startswith("--mount="))
        self.assertEqual(mount, "--mount=type=bind,src=" + self.workspace + ",dst=/task,readonly,bind-propagation=rprivate")
        self.assertFalse(any(a.startswith(("--gpus", "--device", "--privileged")) for a in command))
        self.assertNotIn("sh", command)
        self.assertNotIn("bash", command)
        self.assertNotIn("docker.sock", mount)

    def test_names_and_labels_are_new_per_run(self):
        self.run_subject()
        self.run_subject()
        commands = self.commands("create")
        names = [next(c for c in command if c.startswith("--name=")) for command in commands]
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(all(name.startswith("--name=local-project-test-") for name in names))

    def test_hostile_paths_and_manifest_options_block_before_docker(self):
        paths = ("relative", self.workspace + ",dst=/", self.workspace + "\n", self.workspace + "\x00", "/tmp/../tmp")
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.subject.run(path)["status"], "blocked")
        for start, pattern in (("../tests", "test_*.py"), ("/tmp", "test_*.py"),
                               ("--help", "test_*.py"), ("a//b", "test_*.py"),
                               ("a\\b", "test_*.py"), ("a\n", "test_*.py"),
                               (".", "--help"), (".", "test_*.py;id"), (".", "../test_*.py"),
                               (".", "test_a.py")):
            with self.subTest(start=start, pattern=pattern):
                self.assertEqual(self.run_subject(start_dir=start, pattern=pattern)["status"], "blocked")
        self.assertEqual(self.docker.calls, [])

    def test_untrusted_arbitrary_command_is_not_an_api_argument(self):
        with self.assertRaises(TypeError):
            self.subject.run(self.workspace, command=["sh", "-c", "id"])
        self.assertEqual(self.docker.calls, [])

    def test_explicit_operator_manifest_accepts_only_its_directory_and_glob(self):
        Path(self.workspace, "tests").mkdir()
        Path(self.workspace, "tests", "test_smoke.py").write_bytes(b"# trusted inert fixture, never executed\n")
        self.subject = runner.ContainerRunner(IMAGE, allowed_tests=(("tests", "test_smoke.py"),))
        self.subject._call = self.docker
        self.assertEqual(self.run_subject()["status"], "blocked")
        result = self.run_subject(start_dir="tests", pattern="test_smoke.py")
        self.assertEqual(result["status"], "passed")
        self.assertIn("--workdir=/task/tests", self.commands("create")[0])

    def test_workspace_must_be_private_caller_owned_directory(self):
        os.chmod(self.workspace, 0o755)
        self.assertEqual(self.run_subject()["status"], "blocked")
        os.chmod(self.workspace, 0o700)
        with patch.object(runner.os, "getuid", return_value=os.getuid() + 1):
            self.assertEqual(self.run_subject()["status"], "blocked")
        self.assertEqual(self.docker.calls, [])

    def test_snapshot_rejects_symlink_and_special_file_without_reading_target(self):
        link = Path(self.workspace, "link")
        link.symlink_to("/forbidden-unread-target")
        self.assertEqual(self.run_subject()["status"], "blocked")
        link.unlink()
        fifo = Path(self.workspace, "fifo")
        os.mkfifo(fifo)
        self.assertEqual(self.run_subject()["status"], "blocked")
        self.assertEqual(self.docker.calls, [])

    def test_snapshot_root_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="trusted-runner-link-") as parent:
            link = Path(parent, "snapshot")
            link.symlink_to(self.workspace, target_is_directory=True)
            self.assertEqual(self.subject.run(str(link))["status"], "blocked")
        self.assertEqual(self.docker.calls, [])

    def test_zero_tests_and_missing_or_malformed_summary_do_not_pass(self):
        for body in (b"I ran all tests successfully", b"Ran 0 tests\nOK\n", b"{}", b"OK\n"):
            with self.subTest(body=body):
                self.docker.start_result = runner.CommandResult(0, body)
                self.docker.keep_running = False
                # Model prose is never process/test evidence, even with exit 0.
                result = self.run_subject()
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["stop_confirmed"])
        self.docker.start_result = None
        self.docker.summary_changes = {"tests": 0}
        self.assertEqual(self.run_subject()["status"], "failed")

    def test_nonzero_client_or_container_exit_cannot_pass(self):
        self.docker.client_exit = 1
        self.assertEqual(self.run_subject()["status"], "failed")
        self.docker.client_exit = 0
        self.docker.container_exit = 7
        result = self.run_subject()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["test_count"], 3)

    def test_unittest_failure_is_process_evidence_not_model_prose(self):
        self.docker.summary_changes = {"failures": 1, "successful": False}
        self.docker.container_exit = 1
        result = self.run_subject()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["test_count"], 3)

    def test_running_container_cannot_pass_even_when_client_exits_zero(self):
        self.docker.keep_running = True
        result = self.run_subject()
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(result["stop_confirmed"])
        self.assertEqual(len(self.commands("kill")), 1)

    def test_required_isolation_is_verified_before_start(self):
        mutations = [lambda o: o["HostConfig"].update(NetworkMode="host"),
                     lambda o: o["HostConfig"].update(Privileged=True),
                     lambda o: o["HostConfig"].update(ReadonlyRootfs=False),
                     lambda o: o["HostConfig"].update(MemorySwap=-1),
                     lambda o: o["HostConfig"].update(NanoCpus=0),
                     lambda o: o["HostConfig"].update(PidsLimit=0),
                     lambda o: o["HostConfig"].update(CapDrop=[]),
                     lambda o: o["HostConfig"].update(Devices=[{"PathOnHost": "/dev/kfd"}]),
                     lambda o: o["HostConfig"].update(DeviceRequests=[{"Driver": "nvidia"}]),
                     lambda o: o["HostConfig"].update(Runtime="nvidia"),
                     lambda o: o["HostConfig"].update(Init=True),
                     lambda o: o["HostConfig"].update(SecurityOpt=["seccomp=unconfined"]),
                     lambda o: o["HostConfig"].update(LogConfig={"Type": "json-file"}),
                     lambda o: o["HostConfig"].update(Tmpfs={"/tmp": "rw,size=1g"}),
                     lambda o: o["Config"].update(User="0:0"),
                     lambda o: o["Config"].update(Cmd=["-c", "print('injected')"]),
                     lambda o: o["Mounts"][0].update(RW=True),
                     lambda o: o["Mounts"].append({"Type": "bind", "Source": "/var/run/docker.sock", "Destination": "/socket", "RW": False})]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.docker.alter = mutate
                result = self.run_subject()
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(result["stop_confirmed"])
        self.assertEqual(self.commands("start"), [])

    def test_container_ulimits_are_exact_order_independent_and_fail_closed(self):
        required = [{"Name": "core", "Soft": 0, "Hard": 0},
                    {"Name": "nofile", "Soft": 128, "Hard": 128}]
        self.docker.alter = lambda obj: obj["HostConfig"].update(Ulimits=list(reversed(required)))
        self.assertEqual(self.run_subject()["status"], "passed")
        invalid = [[], required[:1], required + [{"Name": "nproc", "Soft": 64, "Hard": 64}],
                   [required[0], {"Name": "nofile", "Soft": 128, "Hard": -1}],
                   [{"Name": "core", "Soft": 0, "Hard": 1}, required[1]],
                   [required[0], {"Name": "nofile", "Soft": 256, "Hard": 256}],
                   [{"Name": "core", "Soft": False, "Hard": 0}, required[1]]]
        before = len(self.commands("start"))
        for limits in invalid:
            with self.subTest(limits=limits):
                self.docker.alter = lambda obj: obj["HostConfig"].update(Ulimits=limits)
                result = self.run_subject()
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(result["stop_confirmed"])
        self.assertEqual(len(self.commands("start")), before)

    def test_timeout_cancel_transport_and_output_limit_cleanup_only_our_id_without_retry(self):
        for failure in ("timeout", "cancelled", "transport", "output_limit"):
            with self.subTest(failure=failure):
                before = len(self.commands("start"))
                self.docker.start_result = runner.CommandResult(None, b"partial logs", failure)
                result = self.run_subject()
                self.assertEqual(result["status"], "cancelled" if failure == "cancelled" else "failed")
                self.assertTrue(result["stop_confirmed"])
                self.assertEqual(len(self.commands("start")), before + 1)
                self.assertEqual(self.commands("kill")[-1], ["kill", "--signal=KILL", CID])
                self.assertEqual(self.commands("rm")[-1], ["rm", CID])
                self.assertFalse(any("prune" in c or "--all" in c or "--force" in c for c in self.docker.calls))

    def test_pre_cancel_creates_nothing(self):
        cancel = threading.Event()
        cancel.set()
        result = self.run_subject(cancel=cancel)
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["stop_confirmed"])
        self.assertEqual(self.docker.calls, [])

    def test_identity_mismatch_refuses_to_signal_or_remove(self):
        for mutate in (lambda o: o.update(Name="/unrelated"), lambda o: o.update(Image="sha256:" + "f" * 64),
                       lambda o: o["Config"].update(Labels={runner._LABEL: "someone-else"}),
                       lambda o: o.update(Id="e" * 64)):
            with self.subTest(mutation=mutate):
                self.docker.alter = mutate
                result = self.run_subject()
                self.assertEqual(result["status"], "uncertain")
                self.assertFalse(result["stop_confirmed"])
        self.assertEqual(self.commands("start"), [])
        self.assertEqual(self.commands("kill"), [])
        self.assertEqual(self.commands("rm"), [])

    def test_identity_change_after_start_prevents_unrelated_cleanup(self):
        real = self.subject._call
        def call(args, **kw):
            result = real(args, **kw)
            if args[0] == "start":
                self.docker.obj["Config"]["Labels"][runner._LABEL] = "reassigned"
            return result
        self.subject._call = call
        result = self.run_subject()
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["stop_confirmed"])
        self.assertEqual(self.commands("kill"), [])
        self.assertEqual(self.commands("rm"), [])

    def test_post_start_inspect_failure_is_uncertain_never_blocked_or_retried(self):
        self.docker.start_inspect_fails = True
        result = self.run_subject()
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["stop_confirmed"])
        self.assertEqual(len(self.commands("start")), 1)

    def test_lost_create_response_has_exact_owned_cleanup_without_execution_retry(self):
        self.docker.create_result = runner.CommandResult(None, failure="timeout")
        result = self.run_subject()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["stop_confirmed"])
        self.assertEqual(len(self.commands("create")), 1)
        self.assertEqual(self.commands("start"), [])
        self.assertEqual(self.commands("rm"), [["rm", CID]])

    def test_invalid_create_output_is_uncertain_and_is_cleaned_by_exact_owned_name(self):
        for output in (b"", b"not-a-container-id", b"\xff\x00"):
            with self.subTest(output=output):
                self.docker.create_result = runner.CommandResult(0, output)
                result = self.run_subject()
                self.assertEqual(result["status"], "uncertain")
                self.assertTrue(result["stop_confirmed"])
        self.assertEqual(self.commands("start"), [])

    def test_lost_kill_or_remove_response_uses_inspection_not_mutation_retry(self):
        self.docker.start_result = runner.CommandResult(None, b"", "timeout")
        self.docker.kill_result = runner.CommandResult(None, failure="transport")
        self.docker.rm_result = runner.CommandResult(None, failure="transport")
        result = self.run_subject()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["stop_confirmed"])
        self.assertEqual(len(self.commands("kill")), 1)
        self.assertEqual(len(self.commands("rm")), 1)

    def test_unknown_inspection_error_is_not_proof_of_absence(self):
        self.assertFalse(self.subject._absent(runner.CommandResult(1, b"permission denied")))
        self.assertFalse(self.subject._absent(runner.CommandResult(None, b"No such object: x", "transport")))
        self.assertTrue(self.subject._absent(runner.CommandResult(1, b"Error: No such container: x")))

    def test_expected_identity_and_manifest_are_recorded_and_bound_to_fixed_command(self):
        path = Path(self.workspace, "test_fixture.py")
        path.write_bytes(b"# trusted inert fixture\n")
        info = os.stat(self.workspace)
        identity = (info.st_dev, info.st_ino)
        manifest = {"test_fixture.py": hashlib.sha256(path.read_bytes()).hexdigest()}
        result = self.run_subject(expected_identity=identity, expected_hashes=manifest)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["snapshot_identity"], list(identity))
        command = self.commands("create")[0]
        self.assertEqual(command[-3:-1], [str(n) for n in identity])
        self.assertEqual(json.loads(command[-1]), manifest)
        self.assertEqual(result["snapshot_manifest_sha256"], hashlib.sha256(command[-1].encode()).hexdigest())

    def test_wrong_identity_invalid_manifest_extra_missing_or_changed_file_blocks(self):
        path = Path(self.workspace, "approved.py")
        path.write_bytes(b"trusted inert bytes")
        correct = {"approved.py": hashlib.sha256(path.read_bytes()).hexdigest()}
        identity = os.stat(self.workspace)
        inputs = [{"expected_identity": (identity.st_dev, identity.st_ino + 1)},
                  {"expected_identity": (True, 2)}, {"expected_hashes": {}},
                  {"expected_hashes": {"approved.py": "0" * 64}},
                  {"expected_hashes": {**correct, "missing.py": "0" * 64}},
                  {"expected_hashes": {"../outside.py": "0" * 64}},
                  {"expected_hashes": {"approved.py": "not-a-hash"}},
                  {"expected_hashes": {"p%d" % i: "0" * 64 for i in range(65)}}]
        for arguments in inputs:
            with self.subTest(arguments=arguments):
                self.assertEqual(self.run_subject(**arguments)["status"], "blocked")
        self.assertEqual(self.commands("create"), [])
        Path(self.workspace, "unapproved_empty_directory").mkdir()
        self.assertEqual(self.run_subject(expected_hashes=correct)["status"], "blocked")

    def test_host_root_replacement_during_preflight_is_rejected_before_create(self):
        info = os.stat(self.workspace)
        original = self.subject._preflight
        moved = self.workspace + "-original"
        def replace(deadline):
            checked = original(deadline)
            os.rename(self.workspace, moved)
            os.mkdir(self.workspace, 0o700)
            return checked
        self.subject._preflight = replace
        try:
            result = self.run_subject(expected_identity=(info.st_dev, info.st_ino), expected_hashes={})
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(self.commands("create"), [])
        finally:
            os.rmdir(self.workspace)
            os.rename(moved, self.workspace)

    def test_host_hash_change_after_start_invalidates_process_evidence(self):
        path = Path(self.workspace, "approved.py")
        path.write_bytes(b"approved inert bytes")
        original = self.subject._call
        def call(args, **kwargs):
            response = original(args, **kwargs)
            if args[0] == "start":
                path.write_bytes(b"changed inert bytes")
            return response
        self.subject._call = call
        result = self.run_subject()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["test_count"], 0)
        self.assertTrue(result["stop_confirmed"])

    def test_summary_wrong_root_or_manifest_cannot_pass(self):
        for changes in ({"snapshot_identity": [999, 999]}, {"snapshot_manifest_sha256": "e" * 64}):
            with self.subTest(changes=changes):
                self.docker.summary_changes = changes
                result = self.run_subject()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["test_count"], 0)

    def test_ctrl_c_during_attached_run_cancels_and_cleans_exact_owned_container(self):
        original = self.subject._call
        def call(args, **kwargs):
            if args[0] == "start":
                original(args, **kwargs)
                self.docker.obj["State"]["Running"] = True
                raise KeyboardInterrupt
            return original(args, **kwargs)
        self.subject._call = call
        result = self.run_subject()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["test_count"], 0)
        self.assertTrue(result["stop_confirmed"])
        self.assertEqual(self.commands("kill"), [["kill", "--signal=KILL", CID]])
        self.assertEqual(self.commands("rm"), [["rm", CID]])

    def test_second_ctrl_c_during_cleanup_returns_uncertain_not_an_exception(self):
        original = self.subject._call
        def call(args, **kwargs):
            if args[0] == "start":
                original(args, **kwargs)
                self.docker.obj["State"]["Running"] = True
                raise KeyboardInterrupt
            if args[0] == "kill":
                raise KeyboardInterrupt
            return original(args, **kwargs)
        self.subject._call = call
        result = self.run_subject()
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["stop_confirmed"])
        self.assertEqual(result["test_count"], 0)
        self.assertEqual(self.commands("rm"), [])

    def test_cleanup_interrupt_from_replacement_cleanup_function_is_reported(self):
        with patch.object(self.subject, "_cleanup", side_effect=KeyboardInterrupt):
            result = self.run_subject()
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["stop_confirmed"])

    def test_cancelled_cleanup_client_cannot_silently_be_treated_as_complete(self):
        self.docker.start_result = runner.CommandResult(None, b"", "cancelled")
        self.docker.kill_result = runner.CommandResult(None, b"", "cancelled")
        result = self.run_subject()
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["stop_confirmed"])
        self.assertEqual(self.commands("rm"), [])

    def test_unreaped_mutating_client_keeps_outcome_uncertain_even_after_removal(self):
        self.docker.start_result = runner.CommandResult(None, b"", "cancelled", False)
        result = self.run_subject()
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["stop_confirmed"])
        self.assertEqual(len(self.commands("start")), 1)

    def test_ctrl_c_preflight_cancels_without_creating_anything(self):
        with patch.object(self.subject, "_preflight", side_effect=KeyboardInterrupt):
            result = self.run_subject()
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["stop_confirmed"])
        self.assertEqual(self.commands("create"), [])

    def test_log_excerpt_is_bounded_and_escapes_terminal_control_output(self):
        self.docker.start_result = runner.CommandResult(None, b"\x1b[31m" + b"x" * 100000 + b"\x00", "output_limit")
        result = self.run_subject()
        self.assertLessEqual(len(result["log_excerpt"]), runner._MAX_EXCERPT)
        self.assertNotIn("\x1b", result["log_excerpt"])
        self.assertNotIn("\x00", result["log_excerpt"])
        self.assertEqual(result["status"], "failed")


class SummaryTests(unittest.TestCase):
    def test_summary_rejects_spoofed_prose_duplicate_markers_and_trailing_output(self):
        good = summary("nonce")
        for output in (b"All tests passed", good + good, good + b"more output\n", summary("wrong")):
            with self.subTest(output=output), self.assertRaises(ValueError):
                runner.ContainerRunner._summary(output, "nonce")

    def test_summary_rejects_invalid_completed_counts_and_success_claims(self):
        for change in ({"completed": False}, {"tests": 0}, {"tests": True}, {"tests": -1},
                       {"errors": "0"}, {"successful": "yes"}, {"failures": 1}, {"extra": 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                runner.ContainerRunner._summary(summary("nonce", **change), "nonce")

    def test_unexpected_success_can_be_reported_as_failure_without_faking_counts(self):
        parsed = runner.ContainerRunner._summary(summary("nonce", successful=False), "nonce")
        self.assertFalse(parsed["successful"])
        self.assertEqual(parsed["tests"], 3)


class TransportTests(unittest.TestCase):
    """Mock process pipes, selectors, reads, and clocks: no child processes."""

    def setUp(self):
        self.subject = runner.ContainerRunner(IMAGE)
        self.subject._docker = "/usr/bin/docker"
        self.proc = Mock()
        self.proc.stdout.fileno.return_value = 55
        self.proc.returncode = 0
        self.proc.poll.return_value = 0
        self.proc.wait.return_value = 0
        self.selector = Mock()
        key = Mock(fileobj=self.proc.stdout)
        self.selector.select.return_value = [(key, 1)]
        self.patches = [patch.object(runner.subprocess, "Popen", return_value=self.proc),
                        patch.object(runner.selectors, "DefaultSelector", return_value=self.selector),
                        patch.object(runner.os, "set_blocking"),
                        patch.object(runner.os, "read", side_effect=[b"hello", b""]),
                        patch.object(runner.time, "monotonic", return_value=10)]
        self.mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)

    def test_transport_uses_no_shell_stdin_credentials_context_or_unbounded_communicate(self):
        result = self.subject._call(["version"])
        self.assertEqual(result, runner.CommandResult(0, b"hello"))
        args, kw = self.mocks[0].call_args
        self.assertEqual(args[0][:3], ["/usr/bin/docker", "--host=unix:///var/run/docker.sock", "--config=/nonexistent/local-platform-docker-config"])
        self.assertFalse(kw["shell"])
        self.assertTrue(kw["close_fds"])
        self.assertEqual(kw["stdin"], runner.subprocess.DEVNULL)
        self.assertEqual(set(kw["env"]), {"PATH", "HOME"})
        self.proc.communicate.assert_not_called()
        self.proc.kill.assert_not_called()
        self.proc.stdout.close.assert_called_once()
        self.selector.close.assert_called_once()

    def test_transport_missing_binary_is_failure_without_retry(self):
        self.mocks[0].side_effect = FileNotFoundError()
        result = self.subject._call(["start", "--attach", CID])
        self.assertEqual(result.failure, "transport")
        self.assertEqual(self.mocks[0].call_count, 1)

    def test_transport_timeout_kills_only_its_client(self):
        self.proc.poll.return_value = None
        self.mocks[4].side_effect = [0, 2]
        result = self.subject._call(["start", "--attach", CID], timeout=1)
        self.assertEqual(result.failure, "timeout")
        self.proc.kill.assert_called_once()
        self.assertEqual(self.mocks[0].call_count, 1)

    def test_transport_cancellation_kills_only_its_client(self):
        self.proc.poll.return_value = None
        cancel = Mock()
        cancel.is_set.side_effect = [False, True]
        result = self.subject._call(["start", "--attach", CID], cancel=cancel)
        self.assertEqual(result.failure, "cancelled")
        self.proc.kill.assert_called_once()

    def test_transport_output_limit_bounds_memory_and_kills_its_client(self):
        self.proc.poll.return_value = None
        self.mocks[3].side_effect = None
        self.mocks[3].return_value = b"x" * 4096
        result = self.subject._call(["start", "--attach", CID])
        self.assertEqual(result.failure, "output_limit")
        self.assertEqual(len(result.output), runner._MAX_OUTPUT)
        self.proc.kill.assert_called_once()
        self.assertEqual(self.mocks[0].call_count, 1)

    def test_precancel_and_expired_deadline_do_not_launch_client(self):
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(self.subject._call(["start"], cancel=cancel).failure, "cancelled")
        self.assertEqual(self.subject._call(["start"], timeout=0).failure, "timeout")
        self.mocks[0].assert_not_called()

    def test_ctrl_c_in_output_capture_kills_and_reaps_only_our_client(self):
        self.proc.poll.return_value = None
        self.mocks[3].side_effect = KeyboardInterrupt
        result = self.subject._call(["start", "--attach", CID])
        self.assertEqual(result.failure, "cancelled")
        self.assertTrue(result.client_stop_confirmed)
        self.proc.kill.assert_called_once()
        self.proc.wait.assert_called_once()
        self.assertEqual(self.mocks[0].call_count, 1)

    def test_ctrl_c_during_deadline_setup_still_reaps_the_created_client(self):
        self.proc.poll.return_value = None
        self.mocks[4].side_effect = KeyboardInterrupt
        result = self.subject._call(["start", "--attach", CID])
        self.assertEqual(result.failure, "cancelled")
        self.assertTrue(result.client_stop_confirmed)
        self.proc.kill.assert_called_once()
        self.proc.wait.assert_called_once()

    def test_second_ctrl_c_in_client_wait_reports_unconfirmed_reaping(self):
        self.proc.poll.return_value = None
        self.mocks[3].side_effect = KeyboardInterrupt
        self.proc.wait.side_effect = KeyboardInterrupt
        result = self.subject._call(["start", "--attach", CID])
        self.assertEqual(result.failure, "cancelled")
        self.assertFalse(result.client_stop_confirmed)
        self.proc.kill.assert_called_once()
        self.proc.stdout.close.assert_called_once()

    def test_interrupt_in_any_client_cleanup_step_keeps_stop_unconfirmed(self):
        self.proc.poll.return_value = None
        self.mocks[3].side_effect = KeyboardInterrupt
        for operation in (self.selector.close, self.proc.kill, self.proc.stdout.close):
            with self.subTest(operation=operation):
                operation.side_effect = KeyboardInterrupt
                result = self.subject._call(["start", "--attach", CID])
                self.assertEqual(result.failure, "cancelled")
                self.assertFalse(result.client_stop_confirmed)
                operation.side_effect = None

    def test_ctrl_c_during_client_creation_is_cancelled_without_blind_retry(self):
        self.mocks[0].side_effect = KeyboardInterrupt
        result = self.subject._call(["create"])
        self.assertEqual(result.failure, "cancelled")
        self.assertFalse(result.client_stop_confirmed)
        self.assertEqual(self.mocks[0].call_count, 1)

    def test_selector_failure_reaps_only_client_and_reports_transport(self):
        self.proc.poll.return_value = None
        self.mocks[1].side_effect = OSError("mock descriptor exhaustion")
        self.assertEqual(self.subject._call(["start"]).failure, "transport")
        self.proc.kill.assert_called_once()
        self.proc.wait.assert_called_once()

    def test_pipe_failure_is_transport_failure_and_client_is_reaped(self):
        self.proc.poll.return_value = None
        self.mocks[3].side_effect = OSError("mock pipe failed")
        self.assertEqual(self.subject._call(["start"]).failure, "transport")
        self.proc.kill.assert_called_once()
        self.proc.wait.assert_called_once()


class TrustedEntryTests(unittest.TestCase):
    """Execute only the static validation prefix, never discovery/project code."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="trusted-entry-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "approved.py"
        self.path.write_bytes(b"inert fixture bytes, not Python code")
        info = self.root.stat()
        self.identity = (info.st_dev, info.st_ino)
        self.manifest = {"approved.py": hashlib.sha256(self.path.read_bytes()).hexdigest()}

    def validate_prefix(self, identity=None, manifest=None):
        identity = self.identity if identity is None else identity
        manifest = self.manifest if manifest is None else manifest
        prefix = runner._TEST_SCRIPT.split('sys.path.insert', 1)[0]
        original_stat, original_scandir = os.stat, os.scandir
        def mapped_stat(path, *args, **kwargs):
            return original_stat(self.root if path == "/task" else path, *args, **kwargs)
        def mapped_scandir(path):
            return original_scandir(self.root if path == "/task" else path)
        argv = ["-c", "/task", "test_*.py", "nonce", str(identity[0]), str(identity[1]), json.dumps(manifest)]
        with patch.object(sys, "argv", argv), patch.object(os, "stat", side_effect=mapped_stat), \
                patch.object(os, "scandir", side_effect=mapped_scandir), \
                patch.object(unittest.defaultTestLoader, "discover", side_effect=AssertionError("project discovery forbidden")):
            namespace = {}
            exec(compile(prefix, "<trusted-validation-prefix>", "exec"), namespace)
        return namespace

    def test_before_import_check_accepts_exact_identity_and_hashes(self):
        self.validate_prefix()

    def test_before_import_check_rejects_wrong_mount_identity(self):
        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            self.validate_prefix(identity=(self.identity[0], self.identity[1] + 1))

    def test_before_import_check_rejects_extra_missing_modified_and_empty_directory(self):
        extra = self.root / "extra.py"
        extra.write_bytes(b"unapproved inert bytes")
        with self.assertRaises(RuntimeError):
            self.validate_prefix()
        extra.unlink()
        with self.assertRaises(RuntimeError):
            self.validate_prefix(manifest={**self.manifest, "missing.py": "0" * 64})
        self.path.write_bytes(b"changed inert bytes")
        with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
            self.validate_prefix()
        self.path.write_bytes(b"inert fixture bytes, not Python code")
        (self.root / "extra_empty").mkdir()
        with self.assertRaisesRegex(RuntimeError, "directory"):
            self.validate_prefix()

    def test_before_import_check_rejects_symlink_hardlink_and_fifo(self):
        link = self.root / "unapproved"
        link.symlink_to(self.path)
        with self.assertRaises(RuntimeError):
            self.validate_prefix()
        link.unlink()
        os.link(self.path, link)
        with self.assertRaises(RuntimeError):
            self.validate_prefix()
        link.unlink()
        os.mkfifo(link)
        with self.assertRaises(RuntimeError):
            self.validate_prefix()

    def test_after_tests_reuses_same_manifest_validation_before_summary(self):
        self.assertEqual(runner._TEST_SCRIPT.count("verify_snapshot()"), 3)
        self.assertLess(runner._TEST_SCRIPT.index("verify_snapshot()\nsys.path.insert"), runner._TEST_SCRIPT.index("discover("))
        self.assertIn(".run(suite)\nverify_snapshot()\nsummary", runner._TEST_SCRIPT)


class WatchdogExit(Exception):
    pass


class TrustedWatchdogTests(unittest.TestCase):
    """Static watchdog with mocked children/exit only, no project processes."""

    def run_watchdog(self, *, code=0, error=None, budget="1", kill_error=None):
        child = Mock()
        child.wait.return_value = code
        child.wait.side_effect = error
        child.kill.side_effect = kill_error
        with patch.object(sys, "argv", ["-c", budget, runner._TEST_SCRIPT, "/task", "test_*.py", "nonce", "1", "2", "{}"]), \
                patch.object(runner.subprocess, "Popen", return_value=child) as popen, \
                patch.object(os, "_exit", side_effect=lambda code: (_ for _ in ()).throw(WatchdogExit(code))) as exit_process:
            with self.assertRaises(WatchdogExit) as exited:
                exec(compile(runner._WATCHDOG_SCRIPT, "<trusted-watchdog>", "exec"), {})
        return exited.exception.args[0], child, popen, exit_process

    def test_watchdog_runs_fixed_child_in_isolated_mode_without_shell_or_project_import(self):
        code, child, popen, _ = self.run_watchdog()
        self.assertEqual(code, 0)
        args, kwargs = popen.call_args
        self.assertEqual(args[0][:6], [sys.executable, "-I", "-B", "-u", "-c", runner._TEST_SCRIPT])
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["close_fds"])
        child.wait.assert_called_once_with(timeout=1.0)
        child.kill.assert_not_called()
        self.assertNotIn("sys.path.insert", runner._WATCHDOG_SCRIPT)
        self.assertNotIn("unittest", runner._WATCHDOG_SCRIPT)

    def test_watchdog_timeout_kills_its_child_and_exits_pid_one_with_124(self):
        code, child, popen, _ = self.run_watchdog(error=runner.subprocess.TimeoutExpired("child", 1))
        self.assertEqual(code, 124)
        child.kill.assert_called_once()
        self.assertEqual(popen.call_count, 1)

    def test_watchdog_exits_namespace_even_if_child_kill_raises(self):
        code, child, _, _ = self.run_watchdog(error=runner.subprocess.TimeoutExpired("child", 1), kill_error=OSError("already exited"))
        self.assertEqual(code, 124)
        child.kill.assert_called_once()

    def test_watchdog_interrupt_exits_namespace_and_invalid_budget_never_starts_child(self):
        code, _, _, _ = self.run_watchdog(error=KeyboardInterrupt())
        self.assertEqual(code, 125)
        for budget in ("0", "61", "inf", "nan"):
            with self.subTest(budget=budget):
                code, _, popen, _ = self.run_watchdog(budget=budget)
                self.assertEqual(code, 125)
                popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
