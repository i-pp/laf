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

# Modified 2026-11-21: require Router-compatible identity before selecting or downloading a model.
# Modified 2026-11-24: report verified registry bytes for reviewed artifact mappings.
# Modified 2026-11-28: share GGUF verification between Modelfile preparation and registry import.

"""Owner-side model selection for LAF onboarding."""

import hashlib
import http.client
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import quote, urlencode

from ..laf_service.core_backends import valid_configured_profile_id, valid_profile_reference
from .model_management import (
    DIGEST, MAX_JSON, ModelError, ModelManager, OllamaAdapter,
    OllamaHTTP, checked_directory, checked_file, gguf_path, read_engine_config, strict_json,
)

HUB = "huggingface.co"
REGISTRY = "registry.ollama.ai"
PUBLISHERS = ("HuggingFaceTB", "Qwen")
ARTIFACTS = {
    "HuggingFaceTB/SmolLM2-135M-Instruct": ("smollm2-135m", "smollm2:135m-instruct-q4_K_M"),
    "Qwen/Qwen2.5-Coder-1.5B-Instruct": ("qwen25-coder-15b", "qwen2.5-coder:1.5b-instruct-q4_K_M"),
}
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")
REVISION = re.compile(r"[a-f0-9]{40}\Z")
MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
MODEL_LAYER = "application/vnd.ollama.image.model"
LAYER_TYPES = {MODEL_LAYER} | {
    "application/vnd.ollama.image." + part for part in ("system", "template", "license", "params", "messages")
}
CONFIG_TYPE = "application/vnd.docker.container.image.v1+json"
LICENSE = "apache-2.0"
LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0"


def _get(host, path, media):
    connection = http.client.HTTPSConnection(host, timeout=5)
    try:
        connection.request("GET", path, headers={"Accept": media, "User-Agent": "Local-AI-Fabric"})
        response = connection.getresponse()
        if response.status != 200 or response.getheader("Content-Type", "").split(";", 1)[0] != media:
            raise ModelError("official model metadata unavailable")
        body = response.read(MAX_JSON + 1)
        if len(body) > MAX_JSON:
            raise ModelError("official model metadata exceeds response limit")
        return body, response.getheader("Docker-Content-Digest")
    finally:
        connection.close()


def _card(raw, publisher):
    if type(raw) is not dict or raw.get("private") is not False:
        raise ModelError("expected a public model from the selected publisher")
    identifier, revision, updated = raw.get("id"), raw.get("sha"), raw.get("lastModified")
    if (not isinstance(identifier, str) or not REPO.fullmatch(identifier)
            or identifier.split("/", 1)[0] != publisher
            or not isinstance(revision, str) or not REVISION.fullmatch(revision)
            or not isinstance(updated, str) or len(updated) > 48):
        raise ModelError("incomplete publisher model identity")
    try:
        updated_at = datetime.fromisoformat(updated)
        if updated_at.utcoffset() is None:
            raise ValueError()
    except ValueError:
        raise ModelError("invalid publisher model update time") from None
    card_data = raw.get("cardData")
    license_name = card_data.get("license") if isinstance(card_data, dict) else None
    if not isinstance(license_name, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,80}", license_name):
        license_name = None
    access = raw.get("gated")
    return {
        "id": identifier, "publisher": publisher, "model_card": f"https://{HUB}/{identifier}",
        "revision": revision, "updated_at": updated_at.astimezone(timezone.utc).isoformat(),
        "license": license_name, "ungated": access is False,
        "reviewed_artifact_mapping": identifier in ARTIFACTS,
    }


def discover(publisher, limit, search=None):
    if publisher not in PUBLISHERS or type(limit) is not int or not 1 <= limit <= 30:
        raise ModelError("select a supported publisher and 1..30 results")
    query = [("author", publisher), ("sort", "lastModified"), ("direction", -1), ("limit", limit)]
    if search is not None:
        if not isinstance(search, str) or not re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", search):
            raise ModelError("invalid model search term")
        query.append(("search", search))
    query.extend(("expand[]", field) for field in ("sha", "lastModified", "gated", "private", "cardData"))
    raw, _ = _get(HUB, "/api/models?" + urlencode(query), "application/json")
    rows = strict_json(raw)
    if not isinstance(rows, list) or len(rows) > limit:
        raise ModelError("invalid publisher model list")
    models = [_card(row, publisher) for row in rows if isinstance(row, dict) and row.get("private") is False]
    if len({item["id"] for item in models}) != len(models):
        raise ModelError("duplicate publisher model identity")
    return {
        "publisher": publisher, "source": f"https://{HUB}/{publisher}/models",
        "ordered_by": "most recently updated on Hugging Face",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "public_models": sorted(models, key=lambda card: card["updated_at"], reverse=True),
        "reviewed_artifact_mappings": [
            {"model": model, "engine": "ollama", "reference": reference}
            for model, (_, reference) in ARTIFACTS.items() if model.split("/", 1)[0] == publisher
        ],
    }


def _descriptor(value, media_types):
    if (not isinstance(value, dict) or not isinstance(value.get("mediaType"), str)
            or value["mediaType"] not in media_types
            or not isinstance(value.get("digest"), str) or not DIGEST.fullmatch(value["digest"])
            or type(value.get("size")) is not int or not 1 <= value["size"] <= 2**50
            or "urls" in value or "data" in value):
        raise ModelError("unsupported registry blob descriptor")
    return value["digest"], value["size"]


def artifact(identifier):
    mapping = ARTIFACTS.get(identifier)
    if mapping is None:
        raise ModelError("no reviewed Ollama tag mapping for this publisher model")
    profile, reference = mapping
    if not valid_configured_profile_id(profile) or not valid_profile_reference(reference):
        raise ModelError("reviewed artifact needs a valid nonreserved Router profile")
    publisher = identifier.split("/", 1)[0]
    hub_bytes, _ = _get(HUB, "/api/models/" + quote(identifier, safe="/"), "application/json")
    card = _card(strict_json(hub_bytes), publisher)
    if card["id"] != identifier or not card["ungated"] or card["license"] != LICENSE:
        raise ModelError("model identity, public access or license needs review")
    model, tag = reference.split(":", 1)
    manifest_bytes, advertised = _get(REGISTRY, f"/v2/library/{model}/manifests/{tag}", MANIFEST)
    digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    if advertised is not None and advertised != digest:
        raise ModelError("registry manifest disagrees with its advertised digest")
    manifest = strict_json(manifest_bytes)
    if (type(manifest) is not dict or type(manifest.get("schemaVersion")) is not int
            or manifest["schemaVersion"] != 2 or manifest.get("mediaType") != MANIFEST
            or not isinstance(manifest.get("layers"), list) or not 1 <= len(manifest["layers"]) <= 16):
        raise ModelError("unsupported registry manifest")
    sizes = dict([_descriptor(manifest.get("config"), {CONFIG_TYPE})])
    model_layers = []
    for layer in manifest["layers"]:
        layer_digest, size = _descriptor(layer, LAYER_TYPES)
        if layer_digest in sizes and sizes[layer_digest] != size:
            raise ModelError("conflicting registry blob sizes")
        sizes[layer_digest] = size
        if layer["mediaType"] == MODEL_LAYER:
            model_layers.append((layer_digest, size))
    if len(model_layers) != 1:
        raise ModelError("this setup requires one local Ollama model layer")
    model_digest, model_size = model_layers[0]
    total = sum(sizes.values()) + len(manifest_bytes)
    entry = {
        "id": profile, "engine": "ollama", "reference": reference,
        "source": f"https://ollama.com/library/{reference}", "license": LICENSE,
        "license_url": LICENSE_URL, "version": tag, "digest": digest,
        "required_bytes": total, "user_setup_required": False,
    }
    return {"public_model": card, "artifact": entry, "format": "GGUF", "quantization": "Q4_K_M",
            "model_layer": {"digest": model_digest, "size_bytes": model_size}, "manifest_total_bytes": total,
            "artifact_identity": {
                "manifest_digest_scope": "current_tag_response",
                "reviewed_manifest_digest": None,
                "quantization_source": "reviewed_tag_mapping",
            }, "runtime_compatibility": "requires local installation and Router validation"}


def artifacts():
    """Check only the reviewed mappings; never turn failed metadata into an offer."""
    verified, unavailable = [], []
    for identifier in sorted(ARTIFACTS):
        try:
            verified.append(artifact(identifier))
        except (ModelError, OSError, http.client.HTTPException):
            unavailable.append({"model": identifier, "status": "metadata_unavailable"})
    status = "metadata_verified" if verified and not unavailable else "partial" if verified else "unavailable"
    return {"catalog_status": status, "artifacts": verified, "unavailable": unavailable,
            "installed": "not_queried", "selectable": "not_queried", "loaded": "not_queried"}


def _absolute(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or path == path.parent:
        raise ModelError("choose an absolute local setup path")
    return path


def _protected_tree(path):
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()):
            raise ModelError("protected, symlink-free setup path required")
        if info.st_mode & 0o022 and not (ancestor == Path("/tmp") and info.st_mode & stat.S_ISVTX):
            raise ModelError("setup path is writable by another user")


def _persistent_directory(path):
    path = _absolute(path)
    parts = path.parts
    if any(parts[i:i+2] in (("plugins", "cache"), ("plugins", "marketplaces"))
           for i in range(len(parts) - 1)):
        raise ModelError("keep model data outside the plugin installation and cache")
    missing = []
    current = path
    while not os.path.lexists(current):
        missing.append(current)
        current = current.parent
    _protected_tree(current)
    for directory in reversed(missing):
        checked_directory(directory, create=True)
    _protected_tree(path)
    return checked_directory(path)


def _new_file(path, content):
    path = _absolute(path)
    _protected_tree(path.parent)
    checked_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".laf-setup-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        os.unlink(temporary)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def setup(config, engine_url, generation_lock, store=None, state=None):
    config = _absolute(config)
    if os.path.lexists(config):
        raise ModelError("existing model config preserved; choose a new path")
    _protected_tree(config.parent)
    checked_directory(config.parent)
    engine = OllamaHTTP(engine_url)
    lock = _absolute(generation_lock)
    if lock.name != "generation.lock":
        raise ModelError("select the existing Router generation.lock")
    _protected_tree(lock.parent)
    checked_directory(lock.parent)
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        checked_file(fd)
    finally:
        os.close(fd)
    home = Path.home()
    selected_store = _absolute(store or home / ".local/share/laf/models")
    selected_state = _absolute(state or home / ".local/state/laf-models")
    if (selected_store.is_relative_to(selected_state) or selected_state.is_relative_to(selected_store)
            or config.is_relative_to(selected_store) or lock.is_relative_to(selected_store)):
        raise ModelError("keep the model store separate from setup, registry and Router state")
    model_store = _persistent_directory(selected_store)
    state_dir = _persistent_directory(selected_state)
    result = {"state_dir": str(state_dir), "model_store": str(model_store),
              "generation_lock": str(lock), "engine_url": engine.identity.removeprefix("ollama:")}
    _new_file(config, (json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
    return {"status": "configured", "config": str(config), **result, "storage_mapping": "operator_verify",
            "engine_contacted": False, "downloaded": False}


def download(config_path, identifier, expected_digest):
    if not DIGEST.fullmatch(expected_digest):
        raise ModelError("provide the full digest from the selected artifact")
    config = read_engine_config(config_path)
    candidate = artifact(identifier)
    entry = candidate["artifact"]
    if entry["digest"] != expected_digest:
        raise ModelError("selected manifest changed; refresh and select again")
    manager = ModelManager(
        config["state_dir"], [entry], OllamaAdapter(OllamaHTTP(config["engine_url"])),
        config["generation_lock"], config["model_store"],
    )
    return {"installed_artifact": manager.install(entry["id"]), "public_model": candidate["public_model"],
            "router_configuration_required": True}


def installed(config_path, reference=None, digest=None, profile=None):
    if reference is not None and (not valid_configured_profile_id(profile) or not valid_profile_reference(reference)
                                  or not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
        raise ModelError("provide a nonreserved Router profile ID, exact installed tag and full digest")
    config = read_engine_config(config_path)
    checked_directory(config["model_store"])
    models = OllamaAdapter(OllamaHTTP(config["engine_url"])).installed()
    result = {"model_store": config["model_store"], "storage_mapping": "operator_verify",
              "registry_changed": False, "downloaded": False}
    if reference is None:
        return {**result, "installed_models": models}
    if not any(row["reference"] == reference and row["digest"] == digest for row in models):
        raise ModelError("selected installed tag and digest are not present")
    return {**result, "router_profile_identity": {"id": profile, "reference": reference, "digest": digest},
            "router_configuration_required": True}


def verify_existing_file(path, expected_digest):
    path = gguf_path(path)
    if not isinstance(expected_digest, str) or not DIGEST.fullmatch(expected_digest):
        raise ModelError("provide the full SHA-256 of the selected GGUF")
    _protected_tree(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_mode & 0o022 or before.st_size < 24):
            raise ModelError("private owner-controlled GGUF file required")
        header = stream.read(8)
        if header[:4] != b"GGUF" or int.from_bytes(header[4:], "little") not in (2, 3):
            raise ModelError("unsupported GGUF header")
        hasher = hashlib.sha256(header)
        remaining = before.st_size - len(header)
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise ModelError("GGUF changed during verification")
            remaining -= len(block)
            hasher.update(block)
        after = path.lstat()
        def identity(info):
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
        if identity(before) != identity(os.fstat(stream.fileno())) or identity(before) != identity(after):
            raise ModelError("GGUF changed during verification")
    if "sha256:" + hasher.hexdigest() != expected_digest:
        raise ModelError("GGUF does not match the selected SHA-256")
    return {"source_path": str(path), "file_sha256": expected_digest, "size_bytes": before.st_size}


def prepare_existing_file(path, expected_digest, modelfile):
    verified = verify_existing_file(path, expected_digest)
    path = verified["source_path"]
    _new_file(modelfile, f"FROM {path}\n".encode())
    return {"source_path": str(path), "file_sha256": expected_digest, "modelfile": str(modelfile),
            "engine_contacted": False, "downloaded": False, "ollama_import_required": True}
