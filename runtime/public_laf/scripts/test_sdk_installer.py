#!/usr/bin/env python3
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

"""Synthetic wheels/fake HTTP/install runners, plus empty stdlib venv tests.

Never run pip, obtain a wheel, or invoke an SDK. Real subprocess tests use only
the existing Python and venv --without-pip in disposable test directories.
Added 2026-11-11; extended with advisory/closure and umask regressions 2026-11-11.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

SCRIPT = Path(__file__).with_name("laf-sdk.py")
SPEC = importlib.util.spec_from_file_location("laf_sdk_installer_test_target", SCRIPT)
sdk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sdk)

DEPENDENCIES = {
    "mcp": ["anyio>=4.5", "httpx-sse>=0.4", "httpx<1,>=0.27.1", "jsonschema>=4.20", "pydantic-settings>=2.5.2",
            "pydantic>=2.11,<3; python_version < '3.14'", "pydantic>=2.12,<3; python_version >= '3.14'",
            "pyjwt[crypto]>=2.10.1", "python-multipart>=0.0.9", "sse-starlette>=1.6.1",
            "starlette>=0.27; python_version < '3.14'", "starlette>=0.48; python_version >= '3.14'",
            "typing-extensions>=4.9", "typing-inspection>=0.4.1", "uvicorn>=0.31.1",
            "unrequested-cli-package; extra == 'cli'", "pywin32>=311; sys_platform == 'win32'"],
    "anyio": ["idna>=2.8", "typing-extensions>=4.5; python_version < '3.13'"],
    "httpx": ["anyio", "certifi", "httpcore==1.*", "idna"],
    "httpcore": ["certifi", "h11>=0.16"],
    "jsonschema": ["attrs>=22.2", "jsonschema-specifications>=2023.3.6", "referencing>=0.28.4", "rpds-py>=0.25"],
    "jsonschema-specifications": ["referencing>=0.31"],
    "referencing": ["attrs>=22.2", "rpds-py>=0.7", "typing-extensions>=4.4; python_version < '3.13'"],
    "pydantic": ["annotated-types>=0.6", "pydantic-core==2.46.4", "typing-extensions>=4.14.1", "typing-inspection>=0.4.2"],
    "pydantic-core": ["typing-extensions>=4.14.1"],
    "pydantic-settings": ["pydantic>=2.7", "python-dotenv>=0.21", "typing-inspection>=0.4"],
    "pyjwt": ["cryptography>=3.4; extra == 'crypto'"],
    "cryptography": ["cffi>=2.0; platform_python_implementation != 'PyPy'"],
    "cffi": ["pycparser; implementation_name != 'PyPy'"],
    "sse-starlette": ["starlette>=0.49.1", "anyio>=4.7"],
    "starlette": ["anyio>=3.6.2,<5", "typing-extensions>=4.10; python_version < '3.13'"],
    "typing-inspection": ["typing-extensions>=4.15"],
    "uvicorn": ["click>=7", "h11>=0.8"],
}


def fake_wheel(name, version, *, tag="py3-none-any", dist_version=None, license_value="MIT", requirements=None, extra_members=None):
    stream = io.BytesIO()
    root = name.replace("-", "_") + "-" + (dist_version or version) + ".dist-info"
    metadata = ["Metadata-Version: 2.4", "Name: " + name, "Version: " + version,
                "Requires-Python: >=3.10", "License-Expression: " + license_value]
    if name == "pyjwt":
        metadata.append("Provides-Extra: crypto")
    if name == "mcp":
        metadata.append("Provides-Extra: cli")
    metadata += ["Requires-Dist: " + req for req in (DEPENDENCIES.get(name, []) if requirements is None else requirements)]
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(root + "/METADATA", "\n".join(metadata) + "\n\n")
        archive.writestr(root + "/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: " + tag + "\n")
        archive.writestr(root + "/licenses/LICENSE", "SYNTHETIC UNIT TEST LICENSE. NOT AN UPSTREAM ARTIFACT.\n")
        for filename, value in (extra_members or {}).items():
            archive.writestr(filename, value)
    return stream.getvalue()


class FakeRunner:
    def __init__(self, pins, fail_at=None, bad_probe=None):
        self.pins, self.fail_at, self.bad_probe = pins, fail_at, bad_probe
        self.calls = []
        self.venv = None

    def __call__(self, command):
        self.calls.append(command)
        if "venv" in command:
            self.venv = Path(command[-1])
            self.venv.mkdir()
            (self.venv / "pyvenv.cfg").write_text("# SYNTHETIC, NOT A REAL VENV\ninclude-system-site-packages = false\n")
        if len(self.calls) == self.fail_at:
            raise sdk.Refused("SYNTHETIC command failure")
        if "-c" in command and "site.getsitepackages" in command[-1]:
            value = {"prefix": str(self.venv), "base_prefix": "/synthetic/base", "executable": str(self.venv / "bin/python"),
                     "user_site": False, "site_paths": [str(self.venv / "lib/python3.12/site-packages")],
                     "sys_path": ["/synthetic/stdlib", str(self.venv / "lib/python3.12/site-packages")]}
            value.update(self.bad_probe or {})
            return json.dumps(value)
        if "-c" in command:
            return json.dumps({"prefix": str(self.venv), "base_prefix": "/synthetic/base",
                               "packages": {name: pin["version"] for name, pin in self.pins.items()}})
        return ""


class Fixture:
    def __init__(self, root, version="3.12.14", pin_overrides=None):
        self.root, self.bundle, self.prefix = root, root / "bundle 雪 space", root / "SDK 永続 space"
        self.pins = copy.deepcopy(sdk.load_pins())
        for name, version_override in (pin_overrides or {}).items():
            self.pins[name]["version"] = version_override
        self.urls, self.calls = {}, []
        self.env = sdk.environment()
        self.env.update({"python": version, "system": "Linux", "machine": "x86_64", "implementation": "CPython",
                         "platform": "linux-x86_64", "modules": {"venv": True, "pip": True}})
        self.api = sdk.packaging_api()
        markers = self.api["environment"]()
        markers.update({"python_version": ".".join(version.split(".")[:2]), "python_full_version": version})
        self.api["environment"] = lambda: dict(markers)
        self.urls["https://pypi.org/pypi/mcp/json"] = sdk.json_bytes({"releases": {"1.29.0": [{"yanked": False}], "1.30.0": [{"yanked": False}], "2.0.0": [{"yanked": False}]}})
        for name, pin in self.pins.items():
            data = fake_wheel(name, pin["version"])
            filename = name.replace("-", "_") + "-" + pin["version"] + "-py3-none-any.whl"
            url = "https://files.pythonhosted.org/packages/synthetic/" + filename
            meta = {"info": {"name": name, "version": pin["version"], "requires_python": ">=3.10"}, "vulnerabilities": [],
                    "urls": [{"filename": filename, "url": url, "size": len(data), "digests": {"sha256": sdk.digest(data)},
                              "yanked": False, "packagetype": "bdist_wheel", "requires_python": ">=3.10"}]}
            self.urls[self.metadata_url(name)] = sdk.json_bytes(meta)
            self.urls[url] = data
            if name == "mcp":
                pin.update({"wheel_sha256": sdk.digest(data), "wheel_size": len(data)})
        self.runner = FakeRunner(self.pins)
        self.worker = sdk.Installer(pins=self.pins, fetcher=self.fetch, runner=self.runner, env=self.env, api=self.api)

    def metadata_url(self, name):
        return "https://pypi.org/pypi/" + name + "/" + self.pins[name]["version"] + "/json"

    def fetch(self, url, limit, artifact=False):
        self.calls.append((url, artifact))
        sdk.official_url(url, artifact)
        value = self.urls[url]
        sdk.require(len(value) <= limit, "SYNTHETIC fetch exceeded limit")
        return value

    def metadata(self, name, change):
        url = self.metadata_url(name)
        value = sdk.load_json(self.urls[url])
        change(value)
        self.urls[url] = sdk.json_bytes(value)

    def prepare(self):
        return self.worker.prepare(self.bundle)["review_sha256"]


class SDKInstallerTests(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch.object(sdk.urllib.request, "build_opener", side_effect=AssertionError("UNEXPECTED REAL NETWORK")),
            mock.patch.object(sdk.subprocess, "run", side_effect=AssertionError("UNEXPECTED REAL SUBPROCESS")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        clean_environment = {key: value for key, value in os.environ.items()
                             if key.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
        self.proxy_patch = mock.patch.dict(os.environ, clean_environment, clear=True)
        self.proxy_patch.start()
        self.temp = tempfile.TemporaryDirectory(prefix="laf-sdk-synthetic-")
        self.root = Path(self.temp.name)
        self.f = Fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()
        self.proxy_patch.stop()

    def official_fixture(self):
        path = SCRIPT.parent / "fixtures/laf-sdk-pypi-20261111.json"
        return json.loads(path.read_text())

    def assert_official_closure(self, python_version):
        snapshot = self.official_fixture()
        records = copy.deepcopy(snapshot["packages"])
        self.assertEqual(len(records), 29)
        self.assertEqual({name: value["version"] for name, value in records.items()},
                         {name: value["version"] for name, value in self.f.pins.items()})
        for name, record in records.items():
            self.assertEqual(record["vulnerabilities"], [], name)
            self.assertFalse(record["yanked"], name)
            self.assertTrue(self.f.api["specifier"](record["requires_python"]).contains(python_version), name)
            record["requires_dist"] = record["requires_dist"] or []
            record["provides_extra"] = record["provides_extra"] or []
        markers = {**self.f.worker.marker_environment, "python_version": ".".join(python_version.split(".")[:2]),
                   "python_full_version": python_version, "implementation_name": "cpython",
                   "platform_python_implementation": "CPython", "sys_platform": "linux",
                   "platform_system": "Linux", "platform_machine": "x86_64", "os_name": "posix"}
        extras = sdk.verify_closure(records, self.f.pins, self.f.api, markers)
        self.assertEqual(len(extras), 29)
        self.assertEqual({name: value for name, value in extras.items() if value}, {"pyjwt": ["crypto"]})
        self.assertTrue({"cryptography", "cffi", "pycparser"} <= extras.keys())
        self.assertEqual(self.f.calls, [])
        self.assertEqual(self.f.runner.calls, [])

    def test_pyjwt_pin_updated_without_other_version_changes(self):
        self.assertEqual(sdk.load_pins()["pyjwt"]["version"], "2.15.1")
        self.assertEqual({name: pin["version"] for name, pin in sdk.load_pins().items()},
                         {name: record["version"] for name, record in self.official_fixture()["packages"].items()})

    def test_official_affected_pyjwt_metadata_refused_before_artifact(self):
        affected = self.official_fixture()["pyjwt_releases"]["2.13.0"]
        self.assertTrue(affected["vulnerabilities"])
        self.assertIn("GHSA-gvp8-978c-rx2q", {item["id"] for item in affected["vulnerabilities"]})
        self.f.pins["pyjwt"]["version"] = "2.13.0"
        with self.assertRaisesRegex(sdk.Refused, r"advisory for pyjwt==2\.13\.0"):
            self.f.worker.release("pyjwt", sdk.json_bytes(affected))
        self.assertEqual(self.f.calls, [])
        self.assertEqual(self.f.runner.calls, [])
        self.assertFalse(self.f.bundle.exists())

    def test_official_fixed_pyjwt_metadata_selects_wheel_without_fetch(self):
        fixed = self.official_fixture()["pyjwt_releases"]["2.15.1"]
        self.assertEqual(fixed["vulnerabilities"], [])
        for version in ("3.12.14", "3.14.4"):
            with self.subTest(python=version):
                self.f.worker.env["python"] = version
                selected = self.f.worker.release("pyjwt", sdk.json_bytes(fixed))
                self.assertEqual(selected["filename"], "pyjwt-2.15.1-py3-none-any.whl")
                self.assertEqual(selected["size"], 33860)
        self.assertEqual(self.f.calls, [])
        self.assertEqual(self.f.runner.calls, [])
        self.assertFalse(self.f.bundle.exists())

    def test_fixed_pyjwt_with_new_advisory_still_refused(self):
        snapshot = self.official_fixture()
        fixed = copy.deepcopy(snapshot["pyjwt_releases"]["2.15.1"])
        fixed["vulnerabilities"] = snapshot["pyjwt_releases"]["2.13.0"]["vulnerabilities"][:1]
        with self.assertRaisesRegex(sdk.Refused, r"advisory for pyjwt==2\.15\.1"):
            self.f.worker.release("pyjwt", sdk.json_bytes(fixed))
        self.assertEqual(self.f.calls, [])

    def test_advisory_missing_malformed_or_nonempty_still_fails_closed(self):
        fixed = self.official_fixture()["pyjwt_releases"]["2.15.1"]
        for value in (None, {}, "", [{}], ["unrecognized record"]):
            with self.subTest(value=value):
                candidate = copy.deepcopy(fixed)
                candidate["vulnerabilities"] = value
                with self.assertRaises(sdk.Refused):
                    self.f.worker.release("pyjwt", sdk.json_bytes(candidate))
        del fixed["vulnerabilities"]
        with self.assertRaisesRegex(sdk.Refused, "vulnerability metadata is missing"):
            self.f.worker.release("pyjwt", sdk.json_bytes(fixed))
        self.assertEqual(self.f.calls, [])

    def test_official_metadata_closure_python312_includes_crypto(self):
        self.assert_official_closure("3.12.14")

    def test_official_metadata_closure_python314_includes_crypto(self):
        self.assert_official_closure("3.14.4")

    def test_official_metadata_missing_crypto_chain_is_refused(self):
        records = self.official_fixture()["packages"]
        for record in records.values():
            record["requires_dist"] = record["requires_dist"] or []
            record["provides_extra"] = record["provides_extra"] or []
        for missing in ("cryptography", "cffi", "pycparser"):
            with self.subTest(missing=missing):
                pins = {name: pin for name, pin in self.f.pins.items() if name != missing}
                reduced = {name: record for name, record in records.items() if name != missing}
                with self.assertRaisesRegex(sdk.Refused, "Missing transitive dependency: " + missing):
                    sdk.verify_closure(reduced, pins, self.f.api, self.f.worker.marker_environment)

    def test_old_partial_bundle_preserved_while_new_pin_prepares_fresh_path(self):
        old = Fixture(self.root, "3.14.4", pin_overrides={"pyjwt": "2.13.0"})
        advisory = self.official_fixture()["pyjwt_releases"]["2.13.0"]["vulnerabilities"]
        old.metadata("pyjwt", lambda value: value.update({"vulnerabilities": advisory}))
        with self.assertRaisesRegex(sdk.Refused, r"pyjwt==2\.13\.0.*NEW destination"):
            old.prepare()
        before = {str(p.relative_to(old.bundle)): p.read_bytes() for p in old.bundle.rglob("*") if p.is_file()}
        self.assertTrue(before)
        self.assertFalse((old.bundle / "review.json").exists())
        self.assertFalse((old.bundle / "installed.json").exists())
        calls = list(old.calls)
        with self.assertRaises(sdk.Refused):
            old.prepare()
        self.assertEqual(old.calls, calls)
        self.assertEqual(before, {str(p.relative_to(old.bundle)): p.read_bytes() for p in old.bundle.rglob("*") if p.is_file()})
        fresh = Fixture(self.root, "3.14.4")
        fresh.bundle = self.root / "bundle-pyjwt2151-new"
        sha = fresh.prepare()
        review = fresh.worker.verify_bundle(fresh.bundle, sha)
        self.assertEqual(review["packages"]["pyjwt"]["version"], "2.15.1")
        self.assertEqual(review["active_extras"]["pyjwt"], ["crypto"])
        self.assertEqual(before, {str(p.relative_to(old.bundle)): p.read_bytes() for p in old.bundle.rglob("*") if p.is_file()})
        self.assertEqual(old.runner.calls, [])
        self.assertEqual(fresh.runner.calls, [])
        self.assertFalse(fresh.prefix.exists())

    def test_pyjwt_crypto_requires_dist_unchanged_between_official_releases(self):
        releases = self.official_fixture()["pyjwt_releases"]
        before, after = releases["2.13.0"]["info"], releases["2.15.1"]["info"]
        self.assertEqual(before["requires_dist"], after["requires_dist"])
        self.assertEqual(before["provides_extra"], after["provides_extra"])
        self.assertIn("crypto", after["provides_extra"])
        self.assertEqual(after["license_expression"] or after["license"], "MIT")

    def test_fixed_pins_normalized_and_complete(self):
        self.assertEqual(len(sdk.load_pins()), 29)
        self.assertEqual(set(sdk.load_pins()), {sdk.canonical(name) for name in sdk.load_pins()})
        self.assertIn("cryptography", sdk.load_pins())

    def test_prepare_synthetic_29_wheels_no_subprocess(self):
        sha = self.f.prepare()
        review = self.f.worker.verify_bundle(self.f.bundle, sha)
        self.assertEqual(len(review["packages"]), 29)
        self.assertEqual(review["active_extras"]["pyjwt"], ["crypto"])
        self.assertEqual(self.f.runner.calls, [])
        self.assertEqual(len(self.f.calls), 59)
        self.assertEqual((self.f.bundle.stat().st_mode & 0o777), 0o700)

    def test_python314_marker_branch_synthetic(self):
        f = Fixture(self.root, "3.14.4")
        sha = f.prepare()
        self.assertEqual(f.worker.verify_bundle(f.bundle, sha)["marker_environment"]["python_version"], "3.14")

    def test_install_fake_only_exact_local_hashed_commands(self):
        sha = self.f.prepare()
        fetch_calls = len(self.f.calls)
        result = self.f.worker.install(self.f.bundle, self.f.prefix, sha)
        self.assertEqual(len(self.f.calls), fetch_calls)
        self.assertEqual(result["python"], str(self.f.prefix / "venv/bin/python"))
        self.assertEqual(len(self.f.runner.calls), 5)
        pip_install = self.f.runner.calls[2]
        for flag in ("--no-index", "--no-deps", "--require-hashes", "--only-binary=:all:", "--no-cache-dir", "--isolated"):
            self.assertIn(flag, pip_install)
        self.assertNotIn("--system-site-packages", self.f.runner.calls[0])
        self.assertIn(str(self.f.prefix / "venv/bin/python"), pip_install)
        self.assertTrue((self.f.prefix / "installed.json").exists())

    def test_each_subprocess_failure_retains_incomplete_prefix_no_ready_record(self):
        sha = self.f.prepare()
        for failure in range(1, 6):
            with self.subTest(failure=failure):
                prefix = self.root / ("failure-" + str(failure))
                self.f.worker.runner = FakeRunner(self.f.pins, fail_at=failure)
                with self.assertRaisesRegex(sdk.Refused, "incomplete directory retained"):
                    self.f.worker.install(self.f.bundle, prefix, sha)
                self.assertTrue(prefix.exists())
                self.assertFalse((prefix / "installed.json").exists())

    def test_wrong_prefix_probe_prevents_any_pip(self):
        sha = self.f.prepare()
        runner = FakeRunner(self.f.pins, bad_probe={"prefix": "/usr"})
        self.f.worker.runner = runner
        with self.assertRaisesRegex(sdk.Refused, "probe failed before pip"):
            self.f.worker.install(self.f.bundle, self.f.prefix, sha)
        self.assertEqual(len(runner.calls), 2)
        self.assertFalse(any("pip" in call for call in runner.calls))

    def test_user_site_and_outside_site_rejected_before_pip(self):
        sha = self.f.prepare()
        for index, bad in enumerate(({"user_site": True}, {"site_paths": ["/usr/lib/python3/dist-packages"]}, {"sys_path": ["/usr/lib/python3/dist-packages"]})):
            runner = FakeRunner(self.f.pins, bad_probe=bad)
            self.f.worker.runner = runner
            with self.assertRaises(sdk.Refused):
                self.f.worker.install(self.f.bundle, self.root / ("badsite" + str(index)), sha)
            self.assertEqual(len(runner.calls), 2)

    def test_existing_destinations_preserved(self):
        self.f.bundle.mkdir()
        sentinel = self.f.bundle / "foreign"
        sentinel.write_text("preserve")
        with self.assertRaises(sdk.Refused): self.f.prepare()
        self.assertEqual(sentinel.read_text(), "preserve")
        self.assertEqual(self.f.calls, [])

    def test_existing_prefix_preserved_before_bundle_read(self):
        self.f.prefix.mkdir()
        sentinel = self.f.prefix / "foreign"
        sentinel.write_text("preserve")
        with self.assertRaises(sdk.Refused): self.f.worker.install(self.f.bundle, self.f.prefix, "0" * 64)
        self.assertEqual(sentinel.read_text(), "preserve")
        self.assertEqual(self.f.runner.calls, [])

    def test_repeated_successful_install_refuses_no_new_subprocess(self):
        sha = self.f.prepare()
        self.f.worker.install(self.f.bundle, self.f.prefix, sha)
        calls = len(self.f.runner.calls)
        with self.assertRaises(sdk.Refused): self.f.worker.install(self.f.bundle, self.f.prefix, sha)
        self.assertEqual(len(self.f.runner.calls), calls)

    def test_bad_review_hash_never_creates_prefix(self):
        self.f.prepare()
        with self.assertRaisesRegex(sdk.Refused, "Review SHA256 mismatch"):
            self.f.worker.install(self.f.bundle, self.f.prefix, "0" * 64)
        self.assertFalse(self.f.prefix.exists())

    def test_wheel_tamper_rejected(self):
        sha = self.f.prepare()
        wheel = next((self.f.bundle / "wheels").glob("*.whl"))
        wheel.write_bytes(b"not a wheel")
        with self.assertRaisesRegex(sdk.Refused, "hash/size mismatch"):
            self.f.worker.install(self.f.bundle, self.f.prefix, sha)
        self.assertFalse(self.f.prefix.exists())

    def test_lock_tamper_rejected(self):
        sha = self.f.prepare()
        (self.f.bundle / "requirements.lock").write_text("malicious @ https://example.org/wheel.whl\n")
        with self.assertRaisesRegex(sdk.Refused, "Hash lock differs"):
            self.f.worker.install(self.f.bundle, self.f.prefix, sha)

    def test_symlink_wheel_and_writable_lock_rejected(self):
        sha = self.f.prepare()
        lock = self.f.bundle / "requirements.lock"
        lock.chmod(0o666)
        with self.assertRaisesRegex(sdk.Refused, "not writable by others"):
            self.f.worker.verify_bundle(self.f.bundle, sha)
        lock.chmod(0o600)
        wheel = next((self.f.bundle / "wheels").glob("*.whl"))
        outside = self.root / "outside"
        outside.write_bytes(wheel.read_bytes())
        wheel.unlink(); wheel.symlink_to(outside)
        with self.assertRaises(sdk.Refused): self.f.worker.verify_bundle(self.f.bundle, sha)
        self.assertTrue(outside.exists())

    def test_foreign_or_writable_destination_parent_refused(self):
        unsafe = self.root / "unsafe"
        unsafe.mkdir(mode=0o777); unsafe.chmod(0o777)
        with self.assertRaises(sdk.Refused): sdk.new_destination(unsafe / "new")

    def test_symlink_ancestor_refused(self):
        link = self.root / "alias"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(sdk.Refused): sdk.new_destination(link / "new")

    def test_direct_url_and_unknown_dependency_rejected(self):
        for requirement in ("bad @ https://example.org/a.whl", "missing>=1"):
            with self.subTest(requirement=requirement):
                records = {name: {"requires_dist": DEPENDENCIES.get(name, []), "provides_extra": ["crypto"] if name == "pyjwt" else []} for name in self.f.pins}
                records["mcp"]["requires_dist"] = list(records["mcp"]["requires_dist"]) + [requirement]
                with self.assertRaises(sdk.Refused): sdk.verify_closure(records, self.f.pins, self.f.api, self.f.worker.marker_environment)

    def test_unsatisfied_version_and_missing_crypto_extra_rejected(self):
        records = {name: {"requires_dist": DEPENDENCIES.get(name, []), "provides_extra": ["crypto"] if name == "pyjwt" else []} for name in self.f.pins}
        pins = copy.deepcopy(self.f.pins); pins["pydantic"]["version"] = "1.0.0"
        with self.assertRaises(sdk.Refused): sdk.verify_closure(records, pins, self.f.api, self.f.worker.marker_environment)
        records["pyjwt"]["provides_extra"] = []
        with self.assertRaises(sdk.Refused): sdk.verify_closure(records, self.f.pins, self.f.api, self.f.worker.marker_environment)

    def test_advisory_or_missing_metadata_stops_preparation(self):
        self.f.metadata("mcp", lambda value: value.update({"vulnerabilities": [{"id": "SYNTHETIC-ADVISORY"}]}))
        with self.assertRaisesRegex(sdk.Refused, "advisory"):
            self.f.prepare()
        self.assertFalse((self.f.bundle / "review.json").exists())

    def test_newer_supported_sdk_refuses_old_pin(self):
        self.f.urls["https://pypi.org/pypi/mcp/json"] = sdk.json_bytes({"releases": {"1.31.0": [{"yanked": False}]}})
        with self.assertRaisesRegex(sdk.Refused, "not the newest"):
            self.f.prepare()
        self.assertEqual(len(self.f.calls), 1)

    def test_official_hash_size_identity_fail_closed(self):
        self.f.metadata("mcp", lambda value: value["urls"][0]["digests"].update({"sha256": "0" * 64}))
        with self.assertRaisesRegex(sdk.Refused, "reviewed upstream identity"):
            self.f.prepare()

    def test_no_compatible_binary_wheel_fails_without_sdist(self):
        self.f.metadata("mcp", lambda value: value["urls"][0].update({"packagetype": "sdist"}))
        with self.assertRaisesRegex(sdk.Refused, "No compatible official binary"):
            self.f.prepare()

    def test_url_and_redirects_never_followed(self):
        for url in ("http://pypi.org/pypi/a/json", "https://pypi.org.evil/pypi/a/json", "https://user@pypi.org/pypi/a/json", "https://pypi.org:443/pypi/a/json", "https://files.pythonhosted.org/packages/a.whl?token=x"):
            with self.assertRaises(sdk.Refused): sdk.official_url(url)
        with self.assertRaises(sdk.Refused): sdk.NoRedirect().redirect_request(None, None, 302, None, None, "https://example.org")

    def test_no_inherited_proxy_handler(self):
        response = mock.MagicMock()
        response.status = 200; response.url = "https://pypi.org/pypi/mcp/json"
        response.headers = {}; response.read.return_value = b"{}"
        opener = mock.MagicMock(); opener.open.return_value.__enter__.return_value = response
        with mock.patch.object(sdk.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(sdk.fetch(response.url, 100), b"{}")
        self.assertEqual(build.call_args.args[0].proxies, {})

    def test_wheel_internal_tag_version_license_and_traversal(self):
        for kw in ({"tag": "cp314-cp314-win_amd64"}, {"dist_version": "9.9.9"}, {"license_value": "UNKNOWN"}, {"extra_members": {"../escape": b"bad"}}):
            with self.subTest(case=kw):
                data = fake_wheel("mcp", "1.30.0", **kw)
                with self.assertRaises(sdk.Refused): sdk.wheel_metadata(data, "mcp", "1.30.0", self.f.api, "3.12.14", "mcp-1.30.0-py3-none-any.whl")

    def test_resource_member_limit(self):
        data = fake_wheel("mcp", "1.30.0", extra_members={"empty" + str(i): b"" for i in range(11)})
        with mock.patch.object(sdk, "MAX_MEMBERS", 10):
            with self.assertRaisesRegex(sdk.Refused, "member count"):
                sdk.wheel_metadata(data, "mcp", "1.30.0", self.f.api, "3.12.14", "mcp-1.30.0-py3-none-any.whl")

    def test_platform_and_missing_modules_fail_before_fetch(self):
        for change in ({"python": "3.11.0"}, {"system": "Darwin"}, {"machine": "aarch64"}, {"modules": {"venv": False, "pip": True}}):
            original = dict(self.f.worker.env)
            self.f.worker.env.update(change)
            with self.assertRaises(sdk.Refused): self.f.prepare()
            self.f.worker.env = original
        self.assertEqual(self.f.calls, [])

    def test_bundle_bound_to_exact_interpreter(self):
        sha = self.f.prepare()
        self.f.worker.env["python"] = "3.14.4"
        with self.assertRaisesRegex(sdk.Refused, "does not match"):
            self.f.worker.verify_bundle(self.f.bundle, sha)

    def test_marker_failure_retains_new_directory_no_foreign_delete(self):
        real = sdk.write_new
        def fail(path, data, **kwargs):
            if str(path) == ".laf-sdk-owner.json": raise OSError("SYNTHETIC disk full")
            return real(path, data, **kwargs)
        with mock.patch.object(sdk, "write_new", side_effect=fail):
            with self.assertRaisesRegex(sdk.Refused, "incomplete directory retained"):
                self.f.prepare()
        self.assertTrue(self.f.bundle.exists())

    def test_rollback_never_deletes_replacement_directory(self):
        owned = sdk.OwnedDirectory(self.root / "owned", "synthetic")
        owned.create()
        moved = self.root / "moved"
        owned.path.rename(moved)
        owned.path.mkdir()
        sentinel = owned.path / "foreign"
        sentinel.write_text("preserve")
        owned.rollback(); owned.close()
        self.assertEqual(sentinel.read_text(), "preserve")
        self.assertTrue(moved.exists())

    def test_dry_run_no_fetch_no_runner_no_creation(self):
        args = ["prepare", "--dry-run", "--bundle", str(self.f.bundle), "--prefix", str(self.f.prefix)]
        with mock.patch.object(sdk, "fetch", side_effect=AssertionError("network")), mock.patch.object(sdk, "run_command", side_effect=AssertionError("process")), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sdk.main(args), 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "ready_for_explicit_action")
        self.assertFalse(self.f.bundle.exists()); self.assertFalse(self.f.prefix.exists())

    def test_dry_run_blocked_nonzero(self):
        self.f.bundle.mkdir()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sdk.main(["prepare", "--dry-run", "--bundle", str(self.f.bundle)]), 2)
        self.assertEqual(json.loads(out.getvalue())["status"], "blocked")

    def test_prepare_prints_quoted_complete_command(self):
        worker = mock.Mock()
        worker.prepare.return_value = {"review_sha256": "a" * 64, "status": "prepared_not_installed"}
        with mock.patch.object(sdk, "Installer", return_value=worker), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sdk.main(["prepare", "--bundle", str(self.f.bundle), "--prefix", str(self.f.prefix)]), 0)
        result = json.loads(out.getvalue())
        args = shlex.split(result["install_command"])
        self.assertEqual(args[args.index("--bundle") + 1], str(self.f.bundle))
        self.assertEqual(args[args.index("--review-sha256") + 1], "a" * 64)
        self.assertIn("--dry-run", shlex.split(result["install_dry_run_command"]))

    def test_malformed_json_and_duplicate_keys_refused(self):
        for data in (b"[]", b"null", b'{"x":1,"x":2}', b"not-json"):
            with self.assertRaises(sdk.Refused): sdk.load_json(data)

    def test_fifo_does_not_block_regular_read(self):
        fifo = self.root / "not-regular"
        os.mkfifo(fifo, 0o600)
        with self.assertRaisesRegex(sdk.Refused, "Not a bounded regular file"):
            sdk.read_regular(fifo, 100)

    def test_noncanonical_zip_paths_rejected(self):
        for path in ("pkg//x.py", "pkg/./x.py", "pkg/new\nline.py"):
            data = fake_wheel("mcp", "1.30.0", extra_members={path: b"SYNTHETIC"})
            with self.assertRaisesRegex(sdk.Refused, "Noncanonical"):
                sdk.wheel_metadata(data, "mcp", "1.30.0", self.f.api, "3.12.14", "mcp-1.30.0-py3-none-any.whl")

    def test_pip_uses_generated_lock_after_input_lock_changes(self):
        sha = self.f.prepare()
        fake = self.f.runner
        def mutate_input(command):
            output = fake(command)
            if "venv" in command:
                (self.f.bundle / "requirements.lock").write_text("unreviewed @ https://example.invalid/unreviewed.whl\n")
            if "install" in command:
                install_lock = Path(command[command.index("-r") + 1])
                self.assertEqual(install_lock, self.f.prefix / "requirements.lock")
                self.assertNotIn("https://", install_lock.read_text())
                self.assertEqual(install_lock.read_text().count(" @ file://"), 29)
                self.assertNotIn("--find-links", command)
                self.assertIn("%20", install_lock.read_text())
            return output
        self.f.worker.runner = mutate_input
        self.f.worker.install(self.f.bundle, self.f.prefix, sha)

    def test_partial_completion_write_never_publishes_marker(self):
        sha = self.f.prepare()
        real = sdk.write_new
        def disk_full(path, data, **kwargs):
            if str(path).startswith(".installed.json."):
                real(path, b"{", **kwargs)
                raise OSError("SYNTHETIC disk full while writing marker")
            return real(path, data, **kwargs)
        with mock.patch.object(sdk, "write_new", side_effect=disk_full):
            with self.assertRaisesRegex(sdk.Refused, "incomplete directory retained"):
                self.f.worker.install(self.f.bundle, self.f.prefix, sha)
        self.assertFalse((self.f.prefix / "installed.json").exists())
        self.assertTrue(list(self.f.prefix.glob(".installed.json.*.tmp")))

    def test_marker_publish_does_not_overwrite(self):
        path = self.root / "marker-test"
        path.mkdir(mode=0o700)
        (path / "installed.json").write_text("FOREIGN EXISTING MARKER")
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(FileExistsError): sdk.publish_new("installed.json", b"{}", fd)
        finally:
            os.close(fd)
        self.assertEqual((path / "installed.json").read_text(), "FOREIGN EXISTING MARKER")

    def test_configured_proxy_stops_before_request_or_directory_creation(self):
        for key in ("HTTPS_PROXY", "http_proxy", "ALL_PROXY", "hTtPs_PrOxY"):
            with self.subTest(key=key), mock.patch.dict(os.environ, {key: "http://SYNTHETIC_CREDENTIAL@example.invalid:8080"}), mock.patch.object(sdk.urllib.request, "build_opener", side_effect=AssertionError("No opener permitted")):
                with self.assertRaisesRegex(sdk.Refused, "Proxy configuration detected") as caught:
                    sdk.fetch("https://pypi.org/pypi/mcp/json", 100)
                self.assertNotIn("SYNTHETIC_CREDENTIAL", str(caught.exception))
                with self.assertRaisesRegex(sdk.Refused, "Proxy configuration detected"):
                    self.f.prepare()
                self.assertFalse(self.f.bundle.exists())

    def test_run_command_sets_private_child_umask_and_keeps_environment_guards(self):
        result = subprocess.CompletedProcess(["synthetic-command"], 0, stdout="SYNTHETIC output")
        with mock.patch.dict(os.environ, {"PIP_INDEX_URL": "SYNTHETIC", "PYTHONPATH": "SYNTHETIC", "VIRTUAL_ENV": "SYNTHETIC"}), \
                mock.patch.object(sdk.subprocess, "run", return_value=result) as run:
            self.assertEqual(sdk.run_command(["synthetic-command"]), "SYNTHETIC output")
        run.assert_called_once()
        options = run.call_args.kwargs
        self.assertEqual(options["umask"], 0o077)
        self.assertEqual(options["env"]["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(options["env"]["PYTHONDONTWRITEBYTECODE"], "1")
        for name in ("PIP_INDEX_URL", "PYTHONPATH", "VIRTUAL_ENV"):
            self.assertNotIn(name, options["env"])
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["timeout"], 300)

    def test_private_child_failure_does_not_retry_with_inherited_umask(self):
        result = subprocess.CompletedProcess(["synthetic-command"], 3, stdout="")
        with mock.patch.object(sdk.subprocess, "run", return_value=result) as run:
            with self.assertRaisesRegex(sdk.Refused, "Command failed \\(exit 3\\)"):
                sdk.run_command(["synthetic-command"])
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["umask"], 0o077)


# The outer subprocess supplies a representative caller umask without changing
# this test process's umask. No SDK installation path or package runner is used.
EMPTY_VENV_PROBE = r"""
import importlib.util, json, os, pathlib, stat, subprocess, sys
source, destination, runner = sys.argv[1:]
spec = importlib.util.spec_from_file_location('empty_venv_target', source)
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)
def current_umask():
    return next(line.split()[1] for line in pathlib.Path('/proc/self/status').read_text().splitlines() if line.startswith('Umask:'))
before = current_umask()
command = [sys.executable, '-I', '-B', '-m', 'venv', '--without-pip', destination]
if runner == 'inherited-baseline':
    subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300, check=True)
else:
    sdk.run_command(command)
venv = pathlib.Path(destination)
config = venv / 'pyvenv.cfg'
try:
    sdk.read_regular(config, sdk.MAX_JSON)
    accepted, refusal = True, None
except sdk.Refused as exc:
    accepted, refusal = False, str(exc)
print(json.dumps({'caller_umask_before': before, 'caller_umask_after': current_umask(),
                  'config_mode': stat.S_IMODE(config.stat().st_mode),
                  'venv_mode': stat.S_IMODE(venv.stat().st_mode),
                  'owner_matches': config.stat().st_uid == os.getuid(),
                  'accepted': accepted, 'refusal': refusal,
                  'system_site_packages_false': 'include-system-site-packages = false' in config.read_text(),
                  'pip_present': bool(list(venv.glob('lib/python*/site-packages/pip*'))),
                  'python_version': sys.version.split()[0]}))
"""


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux-only installer umask regression")
class EmptyVenvUmaskTests(unittest.TestCase):
    def probe(self, mask, runner="private-installer"):
        parent_before = next(line for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("Umask:"))
        with tempfile.TemporaryDirectory(prefix="laf-empty-venv-test-") as temporary:
            command = [sys.executable, "-I", "-B", "-c", EMPTY_VENV_PROBE, str(SCRIPT), str(Path(temporary) / "empty venv 雪"), runner]
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300, umask=mask)
            self.assertEqual(result.returncode, 0, result.stderr)
            facts = json.loads(result.stdout)
        parent_after = next(line for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("Umask:"))
        self.assertEqual(parent_before, parent_after)
        self.assertEqual(facts["caller_umask_before"], format(mask, "04o"))
        self.assertEqual(facts["caller_umask_after"], format(mask, "04o"))
        self.assertTrue(facts["owner_matches"])
        self.assertTrue(facts["system_site_packages_false"])
        self.assertFalse(facts["pip_present"])
        return facts

    def check_private_venv(self, mask):
        facts = self.probe(mask)
        self.assertEqual(facts["config_mode"], 0o600)
        self.assertEqual(facts["venv_mode"], 0o700)
        self.assertTrue(facts["accepted"], facts["refusal"])

    def test_real_venv_private_under_caller_umask_0002(self):
        self.check_private_venv(0o002)

    def test_real_venv_private_under_caller_umask_0022(self):
        self.check_private_venv(0o022)

    def test_real_venv_private_under_caller_umask_0077(self):
        self.check_private_venv(0o077)

    def test_inherited_0002_reproduces_original_config_rejection(self):
        facts = self.probe(0o002, "inherited-baseline")
        self.assertEqual(facts["config_mode"], 0o664)
        self.assertEqual(facts["venv_mode"], 0o775)
        self.assertFalse(facts["accepted"])
        self.assertIn("not writable by others", facts["refusal"])


if __name__ == "__main__":
    unittest.main()

