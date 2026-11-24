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

# Modified 2026-11-21: check CLI selections against actual Router configuration validation.
# Modified 2026-11-24: verify the reviewed artifact catalog without local engine access.

"""Offline onboarding regressions using synthetic official metadata and engine state."""

from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ..laf_service.cpu_server import load_cpu_config
from . import cli, model_setup
from .model_management import read_engine_config

MODEL = "HuggingFaceTB/SmolLM2-135M-Instruct"
REFERENCE = "smollm2:135m-instruct-q4_K_M"
CARD = {"id": MODEL, "private": False, "gated": False, "sha": "a" * 40,
        "lastModified": "2026-11-12T06:50:41.000Z", "cardData": {"license": "apache-2.0"}}
BLOB = "sha256:" + "b" * 64
MODEL_BLOB = "sha256:" + "c" * 64
MANIFEST = json.dumps({
    "schemaVersion": 2, "mediaType": model_setup.MANIFEST,
    "config": {"mediaType": model_setup.CONFIG_TYPE, "digest": BLOB, "size": 46},
    "layers": [{"mediaType": model_setup.MODEL_LAYER, "digest": MODEL_BLOB, "size": 124}],
}).encode()
DIGEST = "sha256:" + sha256(MANIFEST).hexdigest()


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conf = self.root / "setup.json"
        self.lock = self.root / "generation.lock"
        self.lock.touch(mode=0o600)
        self.store = self.root / "selected"
        self.state = self.root / "registry"
        self.fixture_engine = "http://127.0.0.1:19432"

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli.main(["model", *map(str, args)])
        return rc, json.loads((stdout if rc == 0 else stderr).getvalue())

    def setup_custom(self):
        rc, view = self.invoke("--config", self.conf, "setup", "--engine-url", self.fixture_engine,
                               "--generation-lock", self.lock, "--model-store", self.store,
                               "--state-dir", self.state)
        self.assertEqual(rc, 0, view)
        self.assertFalse(view["engine_contacted"])
        return view

    @staticmethod
    def documents(host, path, media):
        if host == model_setup.HUB:
            data = [CARD] if path.startswith("/api/models?") else CARD
            return json.dumps(data).encode(), None
        if host == model_setup.REGISTRY and path.endswith("/135m-instruct-q4_K_M"):
            return MANIFEST, None
        raise AssertionError("unexpected provider read")

    def test_discovery_queries_only_official_model_metadata(self):
        unsupported = {**CARD, "id": "HuggingFaceTB/other", "lastModified": "2026-11-20T06:50:41Z"}
        unpublished = {**CARD, "id": "HuggingFaceTB/nonpublic", "private": True}
        raw = json.dumps([CARD, unsupported, unpublished]).encode()
        with patch.object(model_setup, "_get", return_value=(raw, None)) as request:
            rc, data = self.invoke("discover", "--publisher", "HuggingFaceTB", "--limit", 3)
        self.assertEqual(rc, 0, data)
        self.assertEqual(request.call_count, 1)
        host, path, _ = request.call_args.args
        self.assertEqual(host, "huggingface.co")
        self.assertIn("sort=lastModified", path)
        self.assertIn("author=HuggingFaceTB", path)
        self.assertEqual([v["id"] for v in data["public_models"]], [unsupported["id"], MODEL])
        self.assertFalse(data["public_models"][0]["reviewed_artifact_mapping"])
        self.assertFalse(self.conf.exists())

    def test_artifact_checks_scope_license_and_manifest(self):
        with patch.object(model_setup, "_get", side_effect=self.documents):
            rc, data = self.invoke("artifact", MODEL)
        self.assertEqual(rc, 0, data)
        self.assertEqual(data["artifact"]["reference"], REFERENCE)
        self.assertEqual(data["artifact"]["digest"], DIGEST)
        self.assertEqual(data["artifact"]["required_bytes"], len(MANIFEST) + 170)
        with patch.object(model_setup, "_get") as network:
            rc, _ = self.invoke("artifact", "HuggingFaceTB/unmapped")
        self.assertEqual(rc, 1)
        network.assert_not_called()
        with patch.object(model_setup, "_get", side_effect=[
                (json.dumps(CARD).encode(), None), (MANIFEST, BLOB)]):
            rc, _ = self.invoke("artifact", MODEL)
        self.assertEqual(rc, 1)
        with patch.object(model_setup, "_get", return_value=(json.dumps({**CARD, "gated": "auto"}).encode(), None)):
            rc, _ = self.invoke("artifact", MODEL)
        self.assertEqual(rc, 1)

    def test_reviewed_artifact_catalog_has_only_individually_verified_downloads(self):
        qwen = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
        qwen_tag = "1.5b-instruct-q4_K_M"
        calls = []
        def documents(host, path, media):
            calls.append((host, path, media))
            if host == model_setup.HUB and path.endswith(qwen):
                return json.dumps({**CARD, "id": qwen}).encode(), None
            if host == model_setup.REGISTRY and path.endswith("/" + qwen_tag):
                return MANIFEST, None
            return self.documents(host, path, media)
        with patch.object(model_setup, "_get", side_effect=documents), \
                patch.object(model_setup.OllamaHTTP, "__call__") as engine, \
                patch.object(cli, "discover_catalog") as router:
            rc, view = self.invoke("artifacts")
        self.assertEqual(rc, 0, view)
        self.assertEqual(view["catalog_status"], "metadata_verified")
        self.assertEqual(view["unavailable"], [])
        self.assertEqual([row["public_model"]["id"] for row in view["artifacts"]], [MODEL, qwen])
        item = view["artifacts"][0]
        self.assertEqual(item["artifact"]["reference"], REFERENCE)
        self.assertEqual(item["artifact"]["digest"], DIGEST)
        self.assertEqual(item["artifact"]["license"], "apache-2.0")
        self.assertEqual(item["artifact"]["source"], "https://ollama.com/library/" + REFERENCE)
        self.assertEqual(item["format"], "GGUF")
        self.assertEqual(item["quantization"], "Q4_K_M")
        self.assertEqual(item["model_layer"], {"digest": MODEL_BLOB, "size_bytes": 124})
        self.assertEqual(item["manifest_total_bytes"], len(MANIFEST) + 170)
        self.assertEqual([view[state] for state in ("installed", "selectable", "loaded")],
                         ["not_queried"] * 3)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all("/manifests/" in path or path.startswith("/api/models/")
                            for _, path, _ in calls))
        self.assertFalse(self.conf.exists())
        self.assertFalse(self.state.exists())
        engine.assert_not_called()
        router.assert_not_called()

    def test_mutable_tag_metadata_does_not_establish_a_historically_reviewed_digest(self):
        revised = json.loads(MANIFEST)
        revised["layers"][0]["digest"] = "sha256:" + "d" * 64
        revised_manifest = json.dumps(revised).encode()
        revised_digest = "sha256:" + sha256(revised_manifest).hexdigest()
        def documents(host, path, media):
            if host == model_setup.REGISTRY and path.endswith("/135m-instruct-q4_K_M"):
                return revised_manifest, revised_digest
            return self.documents(host, path, media)
        with patch.dict(model_setup.ARTIFACTS, {MODEL: model_setup.ARTIFACTS[MODEL]}, clear=True):
            with patch.object(model_setup, "_get", side_effect=self.documents):
                rc, before = self.invoke("artifacts")
            self.assertEqual(rc, 0, before)
            with patch.object(model_setup, "_get", side_effect=documents):
                rc, after = self.invoke("artifacts")
            self.assertEqual(rc, 0, after)
        old, new = before["artifacts"][0], after["artifacts"][0]
        self.assertEqual(old["artifact"]["reference"], new["artifact"]["reference"])
        self.assertEqual([row["artifact"]["digest"] for row in (old, new)], [DIGEST, revised_digest])
        for result in (before, after):
            self.assertEqual(result["catalog_status"], "metadata_verified")
            self.assertEqual(result["artifacts"][0]["artifact_identity"], {
                "manifest_digest_scope": "current_tag_response",
                "reviewed_manifest_digest": None,
                "quantization_source": "reviewed_tag_mapping",
            })
        self.setup_custom()
        with patch.object(model_setup, "_get", side_effect=documents), \
                patch.object(model_setup.OllamaHTTP, "__call__") as engine:
            rc, error = self.invoke("--config", self.conf, "download", MODEL, "--digest", DIGEST)
        self.assertEqual(rc, 1)
        self.assertIn("selected manifest changed", str(error))
        engine.assert_not_called()

    def test_reviewed_artifact_catalog_does_not_treat_failed_mappings_as_installable(self):
        qwen = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
        def documents(host, path, media):
            if path.endswith(qwen):
                return json.dumps({**CARD, "id": qwen, "gated": "auto"}).encode(), None
            return self.documents(host, path, media)
        with patch.object(model_setup, "_get", side_effect=documents) as request:
            rc, view = self.invoke("artifacts")
        self.assertEqual(rc, 0, view)
        self.assertEqual(view["catalog_status"], "partial")
        self.assertEqual([row["public_model"]["id"] for row in view["artifacts"]], [MODEL])
        self.assertEqual(view["unavailable"], [{"model": qwen, "status": "metadata_unavailable"}])
        self.assertEqual(request.call_count, 3)
        malformed = json.loads(MANIFEST)
        malformed["layers"][0]["mediaType"] = []
        with patch.object(model_setup, "_get", side_effect=[
                (json.dumps(CARD).encode(), None), (json.dumps(malformed).encode(), None),
                (json.dumps({**CARD, "id": qwen}).encode(), None), (MANIFEST, None)]):
            rc, view = self.invoke("artifacts")
        self.assertEqual(rc, 0, view)
        self.assertEqual(view["catalog_status"], "partial")
        self.assertEqual([row["public_model"]["id"] for row in view["artifacts"]], [qwen])
        self.assertEqual(view["unavailable"], [{"model": MODEL, "status": "metadata_unavailable"}])
        with patch.object(model_setup, "_get", side_effect=TimeoutError("/private-cannot-leak")) as request:
            rc, view = self.invoke("artifacts")
        self.assertEqual(rc, 0, view)
        self.assertEqual(view["catalog_status"], "unavailable")
        self.assertEqual(view["artifacts"], [])
        self.assertEqual(request.call_count, 2)
        self.assertNotIn("/private-cannot-leak", str(view))

    def test_default_storage_persists_and_custom_config_is_exclusive(self):
        with patch.dict(os.environ, {"HOME": str(self.root)}):
            rc, data = self.invoke("--config", self.conf, "setup",
                                   "--engine-url", self.fixture_engine, "--generation-lock", self.lock)
        self.assertEqual(rc, 0, data)
        self.assertEqual(Path(data["model_store"]), self.root / ".local/share/laf/models")
        self.assertTrue(Path(data["state_dir"]).is_dir())
        self.assertEqual(self.conf.stat().st_mode & 0o777, 0o600)
        recorded = self.conf.read_bytes()
        rc, _ = self.invoke("--config", self.conf, "setup", "--engine-url", self.fixture_engine,
                            "--generation-lock", self.lock, "--model-store", self.store)
        self.assertEqual(rc, 1)
        self.assertEqual(self.conf.read_bytes(), recorded)
        self.assertFalse(self.store.exists())

    def test_selected_install_reuses_registry_and_never_adopts_preexisting_store(self):
        self.setup_custom()
        self.assertEqual(read_engine_config(self.conf)["model_store"], str(self.store))
        wire = []
        installed = []
        def engine(method, path, body=None):
            wire.append((method, path, body))
            if method == "POST":
                installed[:] = [{"name": REFERENCE, "digest": DIGEST[7:], "size": 170}]
                return {"status": "success"}
            return {"models": [] if path == "/api/ps" else installed}
        with patch.object(model_setup, "_get", side_effect=self.documents), \
             patch.object(model_setup.OllamaHTTP, "__call__", side_effect=engine):
            rc, catalog = self.invoke("discover", "--publisher", "HuggingFaceTB")
            self.assertEqual(rc, 0, catalog)
            model, = catalog["public_models"]
            rc, choice = self.invoke("artifact", model["id"])
            self.assertEqual(rc, 0, choice)
            rc, stale = self.invoke("--config", self.conf, "download", model["id"], "--digest", BLOB)
            self.assertEqual(rc, 1, stale)
            self.assertEqual(wire, [])
            self.assertFalse((self.state / "models.json").exists())
            rc, listing = self.invoke("--config", self.conf, "inventory")
            self.assertEqual(rc, 0, listing)
            self.assertEqual(listing["installed_models"], [])
            rc, record = self.invoke("--config", self.conf, "download", model["id"],
                                     "--digest", choice["artifact"]["digest"])
            self.assertEqual(rc, 0, record)
            self.assertEqual(record["installed_artifact"]["digest"], DIGEST)
            rc, _ = self.invoke("--config", self.conf, "download", MODEL, "--digest", DIGEST)
            self.assertEqual(rc, 0)
            unmanaged = "existing-model:Q4_K_M"
            installed.append({"name": unmanaged, "digest": BLOB, "size": 46})
            rc, listing = self.invoke("--config", self.conf, "inventory")
            self.assertEqual(rc, 0, listing)
            present = next(row for row in listing["installed_models"] if row["reference"] == unmanaged)
            rc, selection = self.invoke("--config", self.conf, "use-installed",
                                        "--reference", present["reference"], "--digest", present["digest"],
                                        "--profile", "external")
            self.assertEqual(rc, 0, selection)
        self.assertEqual(selection["router_profile_identity"]["id"], "external")
        self.assertNotIn(b"external", (self.state / "models.json").read_bytes())
        self.assertEqual([row for row in wire if row[0] != "GET"],
                         [("POST", "/api/pull", {"model": REFERENCE, "stream": False})])

        runtime = self.root / "router"
        runtime.mkdir(mode=0o700)
        config_file = self.root / "router.json"
        config_file.touch(mode=0o600)
        installed_identity = {key: record["installed_artifact"][key] for key in ("id", "reference", "digest")}
        for identity in (installed_identity, selection["router_profile_identity"]):
            with self.subTest(configured_model=identity["id"]):
                config = {
                    "mode": "cpu-only", "runtime_dir": str(runtime), "model_store": str(self.store),
                    "generation_lock": str(self.lock),
                    "ollama_provider": {"image": BLOB, "version": "0.40.15", "memory_bytes": 1 << 30,
                                        "cpus": 1, "pids_limit": 96},
                    "ollama_profile": {**identity, "options": {"num_ctx": 8192, "num_thread": 1, "num_batch": 32,
                                                              "num_gpu": 0, "seed": 42, "temperature": 0}},
                }
                config_file.write_text(json.dumps(config))
                self.assertEqual(load_cpu_config(config_file), config)

    def test_invalid_router_identity_is_rejected_before_provider_access(self):
        config = {"model_store": str(self.root), "engine_url": self.fixture_engine}
        names = ("flash", "coder-next", "gpt-oss-120b", "test-fixture", "Upper", "bad_id", "a" * 65)
        cases = [(name, REFERENCE) for name in names]
        cases.extend(("external", reference) for reference in ("Mixed:tag", "model:" + "t" * 195))
        present = {"models": [{"name": REFERENCE, "digest": DIGEST, "size": 170}]}

        with (patch.object(model_setup, "read_engine_config", return_value=config) as read_config,
              patch.object(model_setup, "_get") as metadata,
              patch.object(model_setup.OllamaHTTP, "__call__", return_value=present) as provider):
            for name, reference in cases:
                with self.subTest(name=name, reference=reference):
                    rc, result = self.invoke("--config", self.conf, "use-installed",
                                             "--reference", reference, "--digest", DIGEST, "--profile", name)
                    self.assertEqual(rc, 1, result)
                    self.assertIn("profile", result["error"])
            read_config.assert_not_called()
            with patch.dict(model_setup.ARTIFACTS, {MODEL: ("flash", REFERENCE)}):
                rc, result = self.invoke("--config", self.conf, "download", MODEL, "--digest", DIGEST)
                self.assertEqual(rc, 1, result)
                self.assertIn("profile", result["error"])
        metadata.assert_not_called()
        provider.assert_not_called()
        self.assertFalse((self.state / "models.json").exists())

    def test_existing_gguf_checks_bytes_and_prepares_no_download(self):
        raw = b"GGUF" + (3).to_bytes(4, "little") + b"\0" * 24
        path = self.root / "existing weights.gguf"
        path.write_bytes(raw)
        path.chmod(0o600)
        config = self.root / "Modelfile"
        with patch.object(model_setup, "_get") as network:
            rc, _ = self.invoke("use-file", path, "--digest", BLOB, "--modelfile", config)
            self.assertEqual(rc, 1)
            self.assertFalse(config.exists())
            rc, data = self.invoke("use-file", path, "--digest", "sha256:" + sha256(raw).hexdigest(),
                                   "--modelfile", config)
        network.assert_not_called()
        self.assertEqual(rc, 0, data)
        self.assertTrue(data["ollama_import_required"])
        self.assertEqual(config.read_text(), f"FROM {path}\n")
        self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
