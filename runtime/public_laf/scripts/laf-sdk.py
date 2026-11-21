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

"""User-run SDK-only preparation/install. No adapter or service activation.

Added 2026-11-11; revised advisory diagnostics and private child umask 2026-11-11.
Only explicit prepare/install actions change state. Python and
pip must already exist. pip's bundled packaging parser is used without adding a
dependency to the host. All wheel acquisition uses verified official PyPI URLs;
pip is used only for offline installation into a new persistent venv.
"""
import sys
sys.dont_write_bytecode = True
if __name__ == "__main__" and not sys.flags.isolated:
    sys.stderr.write("laf-sdk: use python3 -I -B scripts/laf-sdk.py (isolated imports; no bytecode writes)\n")
    raise SystemExit(2)

import argparse
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import stat
import subprocess
import sysconfig
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

SDK_VERSION = "1.30.0"
PINS_PATH = Path(__file__).with_name("laf-sdk-pins.json")
MAX_JSON = 4 * 1024 * 1024
MAX_WHEEL = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_REVIEW = 16 * 1024 * 1024
MAX_LICENSE = 1024 * 1024
MAX_MEMBERS = 10000
SCHEMA = 1
LICENSES = {
    "MIT", "MIT-0", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "MPL-2.0",
    "PSF-2.0", "Python-2.0", "Apache-2.0 OR BSD-3-Clause",
}


class Refused(Exception):
    """Expected fail-closed input/prerequisite or verification failure."""


def canonical(name):
    require(isinstance(name, str), "Package name must be text")
    return re.sub(r"[-_.]+", "-", name).lower()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def load_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Refused("Duplicate JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique)
        require(isinstance(value, dict), "Expected a JSON object")
        return value
    except (ValueError, UnicodeError) as exc:
        raise Refused("Invalid JSON") from exc


def require(condition, message):
    if not condition:
        raise Refused(message)


def packaging_api():
    try:
        from pip._vendor.packaging.markers import default_environment
        from pip._vendor.packaging.requirements import Requirement
        from pip._vendor.packaging.specifiers import SpecifierSet
        from pip._vendor.packaging.tags import sys_tags, parse_tag
        from pip._vendor.packaging.utils import parse_wheel_filename
        from pip._vendor.packaging.version import Version
    except ImportError as exc:
        raise Refused("Existing pip with its bundled packaging parser is required; nothing was installed") from exc
    require(Version(importlib.metadata.version("pip")) >= Version("22.3"), "Existing pip >=22.3 is required for --python; no upgrade fallback")
    return {
        "environment": default_environment, "requirement": Requirement,
        "specifier": SpecifierSet, "tags": sys_tags,
        "wheel": parse_wheel_filename, "parse_tag": parse_tag, "version": Version,
    }


def environment():
    marker = Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED"
    modules = {name: importlib.util.find_spec(name) is not None for name in ("venv", "pip")}
    return {
        "python": platform.python_version(), "executable": sys.executable,
        "implementation": platform.python_implementation(), "system": platform.system(),
        "machine": platform.machine(), "platform": sysconfig.get_platform(),
        "prefix": sys.prefix, "base_prefix": sys.base_prefix,
        "pep668_marker_present": marker.exists(), "modules": modules,
    }


def check_environment(env):
    require(env["system"] == "Linux" and env["machine"] == "x86_64", "Only Linux x86_64 is supported by this installer")
    require(env["implementation"] == "CPython" and env["python"].split(".")[:2] in (["3", "12"], ["3", "14"]),
            "Use an existing CPython 3.12 or 3.14; other interpreters are unverified")
    require(all(env["modules"].values()), "Existing Python venv and pip are required; no apt/get-pip/install fallback")
    require(os.geteuid() != 0, "Run as your normal user, never with sudo/root")


def identity(env):
    return {key: env[key] for key in ("python", "implementation", "system", "machine", "platform")}


def absolute_path(value):
    path = Path(value)
    require(path.is_absolute() and not any(x in str(value) for x in ("\x00", "\n", "\r")), "Use an absolute single-line path")
    require(".." not in path.parts and str(path) == str(value), "Use a normalized path without '..' or trailing slash")
    require(path != Path("/"), "The filesystem root is not an install destination")
    return path


def trusted_parent(path):
    path = absolute_path(str(path))
    parent = path.parent
    require(parent.is_dir(), "Destination parent must already exist: " + str(parent))
    for ancestor in [*reversed(parent.parents), parent]:
        info = ancestor.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), "Symlink/non-directory ancestor is refused: " + str(ancestor))
        require(info.st_uid in (0, os.getuid()), "Foreign-owned ancestor is refused: " + str(ancestor))
        writable = info.st_mode & 0o022
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        require(not writable or (ancestor != parent and sticky_root), "Writable-by-others ancestor is refused: " + str(ancestor))
    info = parent.stat()
    require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "Destination parent must be owned by you and not writable by others")
    require(os.access(parent, os.W_OK | os.X_OK), "Destination parent is not writable/searchable")


def new_destination(path):
    trusted_parent(path)
    require(not path.exists() and not path.is_symlink(), "Destination already exists; nothing will be overwritten: " + str(path))


def write_new(path, data, dir_fd=None):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def publish_new(name, data, dir_fd):
    # A complete, fsynced private file becomes the completion marker in one
    # no-overwrite operation. Keep the private hard link; no cleanup is needed.
    temporary = "." + name + "." + uuid.uuid4().hex + ".tmp"
    write_new(temporary, data, dir_fd=dir_fd)
    os.link(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)


def read_regular(path, limit, dir_fd=None):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags, dir_fd=dir_fd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, "Not a bounded regular file: " + str(path))
            require(info.st_uid == os.getuid() and not info.st_mode & 0o022, "File must be owned by you and not writable by others: " + str(path))
            data = stream.read(limit + 1)
            require(len(data) <= limit, "File is too large: " + str(path))
            return data
    except OSError as exc:
        raise Refused("Cannot safely read: " + str(path)) from exc


class OwnedDirectory:
    def __init__(self, path, purpose):
        self.path, self.purpose = path, purpose
        self.token = uuid.uuid4().hex
        self.inode = None
        self.fd = None
        self.marked = False
        self.created = False

    def create(self):
        new_destination(self.path)
        self.path.mkdir(mode=0o700)
        self.created = True
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.inode = os.fstat(self.fd).st_ino
        write_new(".laf-sdk-owner.json", json_bytes({"token": self.token, "purpose": self.purpose, "schema": SCHEMA}), dir_fd=self.fd)
        self.marked = True

    def rollback(self):
        # Nothing was published outside the new prefix. Retain partial content
        # instead of recursively deleting a potentially replaced directory.
        # This is fail-closed isolation, not a complete filesystem rollback.
        # A retry needs a different new destination; cleanup is a manual choice.
        return

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def official_url(url, artifact=False):
    parsed = urllib.parse.urlsplit(url)
    host = "files.pythonhosted.org" if artifact else "pypi.org"
    start = "/packages/" if artifact else "/pypi/"
    require(parsed.scheme == "https" and parsed.netloc == host and parsed.path.startswith(start)
            and not parsed.query and not parsed.fragment and not parsed.username,
            "Only canonical official PyPI HTTPS URLs are allowed")
    return url


def check_proxy_configuration():
    configured = sorted(key for key, value in os.environ.items()
                        if key.lower() in ("http_proxy", "https_proxy", "all_proxy") and value)
    require(not configured, "Proxy configuration detected (" + ", ".join(configured)
            + "); fetching is blocked, no direct request or alternate route was attempted")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused("Network redirect refused; no alternate host or route will be tried")


def fetch(url, limit, artifact=False):
    official_url(url, artifact)
    check_proxy_configuration()
    request = urllib.request.Request(url, headers={"User-Agent": "laf-sdk-installer/1", "Accept": "application/octet-stream" if artifact else "application/json"})
    try:
        # No inherited proxy, redirect, alternate index or fallback route.
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
            require(response.status == 200 and response.url == url, "Unexpected HTTP response")
            length = response.headers.get("Content-Length")
            require(length is None or int(length) <= limit, "Remote object exceeds size limit")
            data = response.read(limit + 1)
            require(len(data) <= limit, "Remote object exceeds size limit")
            return data
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise Refused("Official PyPI request failed; no retry/fallback: " + url) from exc


def run_command(args):
    env = {key: val for key, val in os.environ.items() if not key.startswith(("PIP_", "PYTHON")) and key != "VIRTUAL_ENV"}
    env.update({"PIP_CONFIG_FILE": os.devnull, "PYTHONDONTWRITEBYTECODE": "1"})
    # Children must create private files even when the caller uses umask 0002.
    # POSIX subprocess umask changes only the child, not this process or shell.
    result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300, umask=0o077)
    require(result.returncode == 0, "Command failed (exit " + str(result.returncode) + "): " + " ".join(args))
    return result.stdout


def load_pins():
    value = load_json(read_regular(PINS_PATH, MAX_JSON))
    require(value.get("schema") == SCHEMA and isinstance(value.get("packages"), dict), "Invalid candidate pin manifest")
    pins = value["packages"]
    require(all(isinstance(item, dict) for item in pins.values()), "Invalid pin records")
    require(len(pins) == 29 and pins.get("mcp", {}).get("version") == SDK_VERSION, "Unexpected candidate pin set")
    for name, item in pins.items():
        require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is not None and name == canonical(name), "Pins must use normalized unique names")
        require(re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", item.get("version", "")) is not None, "Pins must use exact release versions")
    return pins


def wheel_metadata(data, name, version, api, python_version, filename):
    try:
        import io
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = archive.infolist()
            require(len(files) <= MAX_MEMBERS, "Wheel member count exceeds limit")
            names = [item.filename for item in files]
            require(len(names) == len(set(names)), "Duplicate wheel member")
            require(sum(item.file_size for item in files) <= MAX_EXPANDED, "Expanded wheel exceeds limit")
            for item in files:
                member = PurePosixPath(item.filename)
                require(not member.is_absolute() and ".." not in member.parts and "\\" not in item.filename,
                        "Unsafe wheel member")
                require(str(member) + ("/" if item.is_dir() else "") == item.filename
                        and not any(ord(char) < 32 or ord(char) == 127 for char in item.filename),
                        "Noncanonical/control-character wheel member refused")
                require(not stat.S_ISLNK(item.external_attr >> 16), "Symlink wheel member refused")
            candidates = [key for key in names if re.fullmatch(r"[^/]+\.dist-info/METADATA", key)]
            require(len(candidates) == 1, "Wheel must contain one top-level METADATA")
            root = candidates[0].split("/")[0]
            dist_name, dist_version = root[:-10].rsplit("-", 1)
            require(canonical(dist_name) == name and dist_version == version, "Wrong dist-info distribution/version")
            wheel_info = root + "/WHEEL"
            require(wheel_info in names and archive.getinfo(wheel_info).file_size <= MAX_JSON, "Missing/big WHEEL metadata")
            wheel = BytesParser(policy=default).parsebytes(archive.read(wheel_info))
            require(wheel.get("Wheel-Version") in ("1.0", "1.1") and wheel.get("Root-Is-Purelib") in ("true", "false"), "Unsupported WHEEL metadata")
            internal_tags = set()
            for tag in wheel.get_all("Tag", []):
                internal_tags.update(api["parse_tag"](tag))
            _, _, _, filename_tags = api["wheel"](filename)
            require(internal_tags == set(filename_tags), "Internal WHEEL tags differ from filename")
            info = archive.getinfo(candidates[0])
            require(info.file_size <= MAX_JSON, "Wheel metadata exceeds limit")
            metadata = BytesParser(policy=default).parsebytes(archive.read(info))
            require(canonical(metadata.get("Name", "")) == name and metadata.get("Version") == version, "Wheel identity differs from pin")
            require(api["specifier"](metadata.get("Requires-Python", "")).contains(python_version), "Wheel Requires-Python excludes this interpreter")
            license_value = metadata.get("License-Expression") or metadata.get("License") or ""
            license_value = " ".join(license_value.split())
            require(license_value in LICENSES, "Missing/unrecognized wheel license declaration: " + name)
            license_names = [key for key in names if key.startswith(root + "/") and not key.endswith("/")
                             and re.match(r"(?i)(license|licence|copying|notice)(?:[._-]|$)", PurePosixPath(key).name)]
            require(license_names, "No license/notice file found in wheel: " + name)
            require(sum(archive.getinfo(key).file_size for key in license_names) <= MAX_LICENSE, "Aggregate license text exceeds limit")
            licenses = []
            for key in sorted(license_names):
                require(archive.getinfo(key).file_size <= MAX_JSON, "License file exceeds limit")
                body = archive.read(key)
                licenses.append({"path": key, "sha256": digest(body), "text": body.decode("utf-8", errors="replace")})
            requires = metadata.get_all("Requires-Dist", [])
            require(len(requires) <= 2000, "Too many dependency declarations")
            for raw in requires:
                requirement = api["requirement"](raw)
                require(requirement.url is None, "Direct URL dependency refused: " + name)
            return {"requires_dist": requires, "requires_python": metadata.get("Requires-Python", ""),
                    "provides_extra": sorted(metadata.get_all("Provides-Extra", [])),
                    "license": license_value, "license_files": licenses}
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise Refused("Invalid wheel metadata: " + name) from exc


def verify_closure(records, pins, api, marker_environment):
    require(set(records) == set(pins), "The prepared package set differs from candidate pins")
    extras, pending, visited = {"mcp": set()}, ["mcp"], set()
    while pending:
        name = pending.pop(0)
        visited.add(name)
        record = records[name]
        require(extras[name] <= set(record["provides_extra"]), "Requested dependency extra is not provided: " + name)
        for raw in record["requires_dist"]:
            req = api["requirement"](raw)
            require(req.url is None, "Direct URL requirement refused")
            if req.marker and not any(req.marker.evaluate({**marker_environment, "extra": extra}) for extra in [""] + sorted(extras[name])):
                continue
            child = canonical(req.name)
            require(child in pins, "Missing transitive dependency: " + child)
            require(req.specifier.contains(pins[child]["version"], prereleases=False), "Unsatisfied dependency constraint: " + str(req))
            wanted = {canonical(extra) for extra in req.extras}
            if child not in extras:
                extras[child] = wanted
                pending.append(child)
            elif not wanted <= extras[child]:
                extras[child].update(wanted)
                pending.append(child)
    require(visited == set(pins), "Candidate pins contain unused packages; revise the closure before installing")
    require(extras.get("pyjwt") == {"crypto"}, "Required pyjwt[crypto] closure is absent")
    require(not extras["mcp"], "Unrequested MCP extras are forbidden")
    return {key: sorted(value) for key, value in sorted(extras.items())}


class Installer:
    def __init__(self, pins=None, fetcher=fetch, runner=run_command, env=None, api=None):
        self.pins = load_pins() if pins is None else pins
        self.fetcher, self.runner = fetcher, runner
        self.env = environment() if env is None else env
        self.api = packaging_api() if api is None else api
        self.tags = list(self.api["tags"]())
        self.marker_environment = self.api["environment"]()

    def current_sdk(self, raw):
        project = load_json(raw)
        require(isinstance(project.get("releases"), dict), "Missing PyPI releases metadata")
        versions = []
        for version, files in project["releases"].items():
            require(isinstance(files, list) and all(isinstance(file, dict) for file in files), "Invalid PyPI release-file metadata")
            parsed = self.api["version"](version)
            if parsed.major == 1 and not parsed.is_prerelease and not parsed.is_devrelease and any(not file.get("yanked", False) for file in files):
                versions.append(parsed)
        require(versions and str(max(versions)) == SDK_VERSION, "Pinned SDK is not the newest official stable 1.x release; stop and review a new version")

    def release(self, name, raw):
        value = load_json(raw)
        info = value.get("info", {})
        require(isinstance(info, dict) and isinstance(value.get("urls"), list) and all(isinstance(item, dict) for item in value["urls"]), "Invalid PyPI release shape")
        version = self.pins[name]["version"]
        require(canonical(info.get("name", "")) == name and info.get("version") == version, "PyPI package identity differs from pin")
        require("vulnerabilities" in value and isinstance(value["vulnerabilities"], list), "PyPI vulnerability metadata is missing")
        require(not value["vulnerabilities"], "PyPI reports an advisory for " + name + "==" + version + "; stop for applicability review")
        require(self.api["specifier"](info.get("requires_python") or "").contains(self.env["python"]), "PyPI Requires-Python excludes this interpreter: " + name)
        candidates = []
        rank = {tag: index for index, tag in enumerate(self.tags)}
        for item in value.get("urls", []):
            if item.get("packagetype") != "bdist_wheel" or item.get("yanked", False):
                continue
            filename = item.get("filename", "")
            try:
                wheel_name, wheel_version, _, tags = self.api["wheel"](filename)
            except ValueError:
                continue
            if canonical(str(wheel_name)) != name or str(wheel_version) != version:
                continue
            matching = [rank[tag] for tag in tags if tag in rank]
            if not matching or not self.api["specifier"](item.get("requires_python") or "").contains(self.env["python"]):
                continue
            url = official_url(item.get("url", ""), artifact=True)
            require(urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]) == filename,
                    "Wheel filename does not match official URL")
            sha = item.get("digests", {}).get("sha256", "")
            size = item.get("size")
            require(re.fullmatch(r"[0-9a-f]{64}", sha) and type(size) is int and 0 < size <= MAX_WHEEL, "Invalid official wheel hash/size")
            candidates.append((min(matching), filename, {"filename": filename, "sha256": sha, "size": size, "url": url}))
        require(candidates, "No compatible official binary wheel: " + name + "==" + version)
        chosen = min(candidates)[2]
        pin = self.pins[name]
        require(not pin.get("wheel_sha256") or chosen["sha256"] == pin["wheel_sha256"], "SDK wheel differs from reviewed upstream identity")
        require(not pin.get("wheel_size") or chosen["size"] == pin["wheel_size"], "SDK wheel size differs from reviewed identity")
        return chosen

    def prepare(self, bundle):
        check_environment(self.env)
        check_proxy_configuration()
        owned = OwnedDirectory(bundle, "prepared-sdk-wheels")
        try:
            owned.create()
            (bundle / "wheels").mkdir(mode=0o700)
            (bundle / "metadata").mkdir(mode=0o700)
            project = self.fetcher("https://pypi.org/pypi/mcp/json", MAX_JSON)
            self.current_sdk(project)
            write_new(bundle / "metadata/mcp-project.json", project)
            records = {}
            for name, pin in sorted(self.pins.items()):
                metadata_url = "https://pypi.org/pypi/" + name + "/" + pin["version"] + "/json"
                raw = self.fetcher(metadata_url, MAX_JSON)
                artifact = self.release(name, raw)
                data = self.fetcher(artifact["url"], artifact["size"], artifact=True)
                require(len(data) == artifact["size"] and digest(data) == artifact["sha256"], "Official wheel hash/size mismatch: " + name)
                facts = wheel_metadata(data, name, pin["version"], self.api, self.env["python"], artifact["filename"])
                records[name] = {"version": pin["version"], **artifact, **facts, "metadata_url": metadata_url,
                                 "metadata_sha256": digest(raw), "pypi_vulnerabilities": []}
                write_new(bundle / "metadata" / (name + ".json"), raw)
                write_new(bundle / "wheels" / artifact["filename"], data)
            active_extras = verify_closure(records, self.pins, self.api, self.marker_environment)
            review = {"schema": SCHEMA, "sdk_version": SDK_VERSION, "prepared_at": datetime.now(timezone.utc).isoformat(),
                      "target": identity(self.env), "marker_environment": self.marker_environment,
                      "sdk_project_sha256": digest(project), "active_extras": active_extras, "packages": records,
                      "scope": "SDK only; no LAF adapter installed, no service/catalog/config activation",
                      "audit_limits": ["Official PyPI vulnerability snapshot is not exhaustive security clearance.",
                                       "License declarations/text are preserved for review, not a legal approval.",
                                       "Native wheel compatibility and SDK runtime are untested until explicit install.",
                                       "The LAF adapter is not installed or upgraded by this SDK-only installer."]}
            data = json_bytes(review)
            require(len(data) <= MAX_REVIEW, "Review exceeds bounded size")
            write_new(bundle / "requirements.lock", self.lock(records))
            write_new(bundle / "review.json", data)
            return {"review_sha256": digest(data), "bundle": str(bundle), "packages": len(records), "status": "prepared_not_installed"}
        except BaseException as exc:
            owned.rollback()
            if owned.created and isinstance(exc, Exception):
                raise Refused(str(exc) + "; incomplete directory retained unchanged, use a NEW destination: " + str(bundle)) from exc
            raise
        finally:
            owned.close()

    def lock(self, records):
        return ("# Target-specific, official artifact hashes; no source-file inventory.\n" + "".join(
            name + "==" + record["version"] + " --hash=sha256:" + record["sha256"] + "\n"
            for name, record in sorted(records.items()))).encode()

    def verify_bundle(self, bundle, review_hash):
        trusted_parent(bundle)
        info = bundle.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
                "Bundle must be an owner-only real directory")
        for subdir in (bundle / "metadata", bundle / "wheels"):
            info = subdir.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
                    "Bundle subdirectory must be owner-only and not a symlink")
        require(re.fullmatch(r"[0-9a-f]{64}", review_hash or "") is not None, "Provide the exact review SHA256 printed by prepare after inspecting review.json")
        raw = read_regular(bundle / "review.json", MAX_REVIEW)
        require(digest(raw) == review_hash, "Review SHA256 mismatch; nothing installed")
        review = load_json(raw)
        require(review.get("schema") == SCHEMA and review.get("sdk_version") == SDK_VERSION, "Invalid review schema/SDK")
        require(review.get("target") == identity(self.env) and review.get("marker_environment") == self.marker_environment,
                "Prepared bundle does not match this exact Python/platform; prepare again on this host")
        records = review.get("packages", {})
        require(isinstance(records, dict) and all(isinstance(item, dict) for item in records.values()), "Invalid reviewed package records")
        require(set(records) == set(self.pins), "Unexpected package set in review")
        project = read_regular(bundle / "metadata/mcp-project.json", MAX_JSON)
        require(digest(project) == review.get("sdk_project_sha256"), "SDK project metadata changed")
        self.current_sdk(project)
        expected_files = set()
        checked = {}
        for name, record in sorted(records.items()):
            metadata = read_regular(bundle / "metadata" / (name + ".json"), MAX_JSON)
            require(digest(metadata) == record.get("metadata_sha256"), "PyPI metadata changed: " + name)
            artifact = self.release(name, metadata)
            require(all(record.get(key) == value for key, value in artifact.items()), "Artifact identity changed: " + name)
            data = read_regular(bundle / "wheels" / artifact["filename"], MAX_WHEEL)
            require(len(data) == artifact["size"] and digest(data) == artifact["sha256"], "Prepared wheel hash/size mismatch: " + name)
            facts = wheel_metadata(data, name, self.pins[name]["version"], self.api, self.env["python"], artifact["filename"])
            require(all(record.get(key) == value for key, value in facts.items()), "Wheel metadata differs from review: " + name)
            require(record.get("version") == self.pins[name]["version"] and record.get("pypi_vulnerabilities") == [], "Package version/advisory changed")
            expected_files.add(artifact["filename"])
            checked[name] = record
        require({path.name for path in (bundle / "wheels").iterdir()} == expected_files, "Unexpected wheelhouse contents")
        require(verify_closure(checked, self.pins, self.api, self.marker_environment) == review.get("active_extras"), "Dependency extras differ from review")
        require(read_regular(bundle / "requirements.lock", MAX_JSON) == self.lock(checked), "Hash lock differs from reviewed wheel closure")
        return review

    def install(self, bundle, prefix, review_hash):
        check_environment(self.env)
        require(bundle != prefix and bundle not in prefix.parents and prefix not in bundle.parents, "Bundle and prefix must be separate non-overlapping directories")
        new_destination(prefix)
        review = self.verify_bundle(bundle, review_hash)
        owned = OwnedDirectory(prefix, "persistent-sdk-runtime")
        try:
            owned.create()
            venv = prefix / "venv"
            python = venv / "bin/python"
            self.runner([sys.executable, "-I", "-B", "-m", "venv", "--without-pip", str(venv)])
            config = read_regular(venv / "pyvenv.cfg", MAX_JSON).decode()
            require(re.search(r"(?im)^include-system-site-packages\s*=\s*false\s*$", config) is not None,
                    "Venv must exclude system site packages")
            probe = "import sys,site,json; print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,'executable':sys.executable,'user_site':site.ENABLE_USER_SITE,'site_paths':site.getsitepackages(),'sys_path':sys.path}))"
            observed = load_json(self.runner([str(python), "-I", "-B", "-c", probe]))
            require(observed.get("prefix") == str(venv) and observed.get("base_prefix") != str(venv)
                    and observed.get("executable") == str(python) and observed.get("user_site") is False,
                    "Venv isolation probe failed before pip; nothing installed")
            sites = observed.get("site_paths")
            require(isinstance(sites, list) and sites and all(Path(site).is_relative_to(venv) for site in sites),
                    "Venv site paths escaped the new prefix; nothing installed")
            require(all(Path(path).is_relative_to(venv) for path in observed.get("sys_path", [])
                        if path.endswith(("site-packages", "dist-packages"))), "System/user site path leaked into venv; nothing installed")
            # Do not hand pip the mutable input lock after its validation.
            # Recreate pinned, hash-only requirements from verified memory.
            install_lock = prefix / "requirements.lock"
            local_lock = "".join(name + " @ " + (bundle / "wheels" / record["filename"]).as_uri()
                                 + " --hash=sha256:" + record["sha256"] + "\n"
                                 for name, record in sorted(review["packages"].items()))
            write_new(install_lock, local_lock.encode())
            pip = [sys.executable, "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-cache-dir", "--python", str(python)]
            self.runner(pip + ["install", "--no-index", "--only-binary=:all:", "--no-deps", "--require-hashes", "-r", str(install_lock)])
            self.runner(pip + ["check"])
            code = "import importlib.metadata as m,json,sys; from mcp.server.lowlevel import Server; from mcp.client.stdio import stdio_client; print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,'packages':{m.metadata(d)['Name']:m.version(d) for d in " + repr(sorted(self.pins)) + "}}))"
            result = load_json(self.runner([str(python), "-I", "-B", "-c", code]))
            require(result.get("prefix") == str(venv) and result.get("base_prefix") != str(venv), "Runtime did not use the isolated venv")
            actual = {canonical(name): version for name, version in result.get("packages", {}).items()}
            require(actual == {name: pin["version"] for name, pin in self.pins.items()}, "Installed package versions differ from reviewed closure")
            result = {"schema": SCHEMA, "status": "sdk_installed", "sdk_version": SDK_VERSION, "python": str(python),
                      "review_sha256": review_hash, "target": review["target"], "checks": ["pip check", "SDK lowlevel/stdin-client imports", "exact package versions", "isolated prefix"],
                      "adapter": "Not installed or upgraded. Verify the separately selected adapter source and runtime.",
                      "unchanged": ["system Python/packages", "PATH/shell startup", "LAF services/models", "MCP client/plugin/catalog configuration"]}
            publish_new("installed.json", json_bytes(result), owned.fd)
            return result
        except BaseException as exc:
            owned.rollback()
            if owned.created and isinstance(exc, Exception):
                raise Refused(str(exc) + "; incomplete directory retained unchanged, use a NEW destination: " + str(prefix)) from exc
            raise
        finally:
            owned.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect, prepare or install MCP SDK 1.30.0 into a NEW persistent user-owned venv. No adapter, global pip, PATH edit or service activation.")
    parser.add_argument("action", nargs="?", choices=("inspect", "prepare", "install"), default="inspect")
    parser.add_argument("--dry-run", action="store_true", help="Print the concrete plan only; no writes, subprocesses or network")
    parser.add_argument("--bundle", default=str(Path.home() / "laf-sdk-1.30.0-bundle"), help="New directory for official wheels, license/advisory review and hash lock")
    parser.add_argument("--prefix", default=str(Path.home() / ".local/share/laf-sdk-1.30.0"), help="New persistent SDK installation prefix; parent must already exist")
    parser.add_argument("--review-sha256", help="Exact review hash retained from prepare; inspect review.json before install")
    args = parser.parse_args(argv)
    try:
        bundle, prefix = absolute_path(args.bundle), absolute_path(args.prefix)
        pins, env = load_pins(), environment()
        if args.action == "inspect" or args.dry_run:
            errors = []
            try:
                check_environment(env)
                packaging_api()
                if args.action != "install":
                    check_proxy_configuration()
            except Refused as exc:
                errors.append(str(exc))
            destinations = (bundle,) if args.action == "prepare" else (prefix,) if args.action == "install" else (bundle, prefix)
            for path in destinations:
                try:
                    new_destination(path)
                except (Refused, OSError) as exc:
                    errors.append(str(exc))
            if args.action == "install" and not errors:
                try:
                    require(bundle != prefix and bundle not in prefix.parents and prefix not in bundle.parents,
                            "Bundle and prefix must be separate non-overlapping directories")
                    Installer(pins=pins, env=env).verify_bundle(bundle, args.review_sha256)
                except (Refused, OSError, ValueError, TypeError, KeyError) as exc:
                    errors.append(str(exc))
            print(json.dumps({"action": args.action, "dry_run": True, "status": "blocked" if errors else "ready_for_explicit_action", "environment": env, "sdk_version": SDK_VERSION,
                              "candidate_packages": {name: pin["version"] for name, pin in sorted(pins.items())},
                              "bundle": str(bundle), "prefix": str(prefix), "future_python": str(prefix / "venv/bin/python"),
                              "plan": ["prepare: official PyPI metadata and binary wheels only, full crypto extra closure, hashes/licenses/advisories, no installation",
                                       "install: verify retained review hash, create final-path venv without system packages, offline hash-locked install, pip check/import/version checks",
                                       "no PATH/shell/global Python/adapter/plugin/catalog/service/model changes"],
                              "notes": ["SDK only; adapter source/runtime verification is separate", "This dry-run does not verify SDK runtime or host installation", "This is not a complete LAF runtime Quick Start"],
                              "preflight_issues": errors}, indent=2))
            return 2 if errors else 0
        worker = Installer(pins=pins, env=env)
        result = worker.prepare(bundle) if args.action == "prepare" else worker.install(bundle, prefix, args.review_sha256)
        if args.action == "prepare":
            command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "install", "--bundle", str(bundle),
                       "--prefix", str(prefix), "--review-sha256", result["review_sha256"]]
            result.update({"review_path": str(bundle / "review.json"), "next": "Inspect review.json, including licenses and limits, before running either command.",
                           "install_dry_run_command": shlex.join(command + ["--dry-run"]), "install_command": shlex.join(command)})
        print(json.dumps(result, indent=2))
        return 0
    except (Refused, OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, AttributeError) as exc:
        sys.stderr.write("laf-sdk: " + str(exc) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

