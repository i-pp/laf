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

from . import cli, model_setup
from .model_management import read_engine_config

MODEL = "HuggingFaceTB/SmolLM2-135M-Instruct"
REFERENCE = "smollm2:135m-instruct-q4_K_M"
CARD = {"id": MODEL, "private": False, "gated": False, "sha": "a" * 40,
        "lastModified": "2026-11-12T06:50:41.000Z", "cardData": {"license": "apache-2.0"}}
BLOB = "sha256:" + "b" * 64
MANIFEST = json.dumps({
    "schemaVersion": 2, "mediaType": model_setup.MANIFEST,
    "config": {"mediaType": model_setup.CONFIG_TYPE, "digest": BLOB, "size": 46},
    "layers": [{"mediaType": model_setup.MODEL_LAYER, "digest": "sha256:" + "c" * 64, "size": 124}],
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
            return json.dumps(CARD).encode(), None
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
            rc, stale = self.invoke("--config", self.conf, "download", MODEL, "--digest", BLOB)
            self.assertEqual(rc, 1, stale)
            self.assertEqual(wire, [])
            self.assertFalse((self.state / "models.json").exists())
            rc, listing = self.invoke("--config", self.conf, "inventory")
            self.assertEqual(rc, 0, listing)
            self.assertEqual(listing["installed_models"], [])
            rc, record = self.invoke("--config", self.conf, "download", MODEL, "--digest", DIGEST)
            self.assertEqual(rc, 0, record)
            self.assertEqual(record["installed_artifact"]["digest"], DIGEST)
            rc, _ = self.invoke("--config", self.conf, "download", MODEL, "--digest", DIGEST)
            self.assertEqual(rc, 0)
            rc, selection = self.invoke("--config", self.conf, "use-installed",
                                        "--reference", REFERENCE, "--digest", DIGEST, "--profile", "external")
            self.assertEqual(rc, 0, selection)
        self.assertEqual(selection["router_profile_identity"]["id"], "external")
        self.assertNotIn(b"external", (self.state / "models.json").read_bytes())
        self.assertEqual([row for row in wire if row[0] != "GET"],
                         [("POST", "/api/pull", {"model": REFERENCE, "stream": False})])

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
