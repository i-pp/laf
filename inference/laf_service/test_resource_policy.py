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

# Changed 2026-11-13: bounded swap and explicit recovery regression coverage.

"""Regression tests for bounded configurable swap admission."""

import unittest
from .core import Controller, GIB, ServiceError, check_resources
from .test_service import good, Fake


class PolicyTests(unittest.TestCase):
    def test_default_retains_limit(self):
        s = good()
        s["swap_used"] = GIB
        with self.assertRaises(ServiceError):
            check_resources(s, good())

    def test_explicit_two_gib_allows_moderate_swap(self):
        s = good()
        s["swap_used"] = GIB
        check_resources(s, good(), 2048)
        s["swap_used"] = 2 * GIB + 1
        with self.assertRaises(ServiceError):
            check_resources(s, good(), 2048)

    def test_io_limit_is_bounded(self):
        import os

        s = good()
        s["pswpout"] = 2 * GIB // os.sysconf("SC_PAGE_SIZE") + 1
        with self.assertRaises(ServiceError):
            check_resources(s, good(), 2048)

    def test_absolute_protections_unchanged(self):
        for key, value in [
            ("mem_available", 7 * GIB),
            ("vram_used", 63 * GIB),
            ("psi_full_avg10", 11),
        ]:
            s = good()
            s[key] = value
            with self.assertRaises(ServiceError):
                check_resources(s, good(), 2048)

    def test_invalid_policy_rejected(self):
        for value in [True, 0, 511, 2049, "2048", 2048.0]:
            with self.assertRaises(ValueError):
                Controller(Fake(), good, start=False, swap_limit_mib=value)

    def test_quiet_recovery_rebases_old_cumulative_io(self):
        from unittest.mock import patch

        sample = good()
        ctl = Controller(Fake(), lambda: dict(sample), start=False)
        ctl.fault = "old swap I/O"
        sample["pswpout"] = 1000000
        with patch("inference.laf_service.core.time.sleep"):
            ctl.recover()
        self.assertIsNone(ctl.fault)
        self.assertEqual(ctl.baseline["pswpout"], 1000000)

    def test_busy_recovery_preserves_fault_and_baseline(self):
        from unittest.mock import patch

        sample = good()
        ctl = Controller(Fake(), lambda: dict(sample), start=False)
        ctl.fault = "old fault"
        original = dict(ctl.baseline)

        def tick(_):
            sample["pswpout"] += 1

        with patch("inference.laf_service.core.time.sleep", side_effect=tick):
            with self.assertRaises(ServiceError):
                ctl.recover()
        self.assertEqual(ctl.fault, "old fault")
        self.assertEqual(ctl.baseline, original)

    def test_recovery_preserves_absolute_reserves(self):
        from unittest.mock import patch

        sample = good()
        ctl = Controller(Fake(), lambda: dict(sample), start=False)
        ctl.fault = "old fault"
        sample["mem_available"] = 7 * GIB
        with patch("inference.laf_service.core.time.sleep"):
            with self.assertRaises(ServiceError):
                ctl.recover()
        self.assertEqual(ctl.fault, "old fault")

    def test_recovery_rejects_pending_jobs(self):
        ctl = Controller(Fake(), good, start=False)
        ctl.submit(
            {"model": "flash", "messages": [{"role": "user", "content": "test"}]}
        )
        with self.assertRaises(ServiceError):
            ctl.recover()

    def test_abort_failure_preserves_latch(self):
        from unittest.mock import patch

        ctl = Controller(Fake(), good, start=False)
        ctl.fault = "old fault"
        with patch.object(
            ctl.backend, "abort", side_effect=RuntimeError("stop failed")
        ):
            with self.assertRaises(RuntimeError):
                ctl.recover()
        self.assertEqual(ctl.fault, "old fault")

    def test_shutdown_during_quiet_window_does_not_clear_fault(self):
        from unittest.mock import patch

        ctl = Controller(Fake(), good, start=False)
        ctl.fault = "old fault"
        with patch(
            "inference.laf_service.core.time.sleep",
            side_effect=lambda _: ctl.stop.set(),
        ):
            with self.assertRaises(ServiceError):
                ctl.recover()
        self.assertEqual(ctl.fault, "old fault")

    def test_psi_must_be_quiet_not_just_below_emergency_limit(self):
        from unittest.mock import patch

        sample = good()
        ctl = Controller(Fake(), lambda: dict(sample), start=False)
        ctl.fault = "old fault"
        sample["psi_full_avg10"] = 2
        with patch("inference.laf_service.core.time.sleep"):
            with self.assertRaises(ServiceError):
                ctl.recover()
        self.assertEqual(ctl.fault, "old fault")

    def test_watch_latches_abort_failure_even_with_healthy_resources(self):
        from unittest.mock import patch
        import threading, time

        ctl = Controller(Fake(), good, start=False)
        job = ctl.submit(
            {"model": "flash", "messages": [{"role": "user", "content": "test"}]}
        )
        ctl.active = job
        job.deadline = 0
        calls = []

        def fail_abort():
            calls.append(1)
            ctl.stop.set()
            raise RuntimeError("provider stop failed")

        with patch.object(ctl.backend, "abort", side_effect=fail_abort):
            thread = threading.Thread(target=ctl.watch)
            thread.start()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIn("provider stop unconfirmed", ctl.fault)
        self.assertTrue(job.abort_requested)
        self.assertTrue(calls)

    def test_recovery_does_not_invert_backend_lifecycle_lock(self):
        import threading
        from unittest.mock import patch

        class LifecycleBackend(Fake):
            def __init__(self):
                super().__init__()
                self.lifecycle_lock = threading.RLock()
                self.aborting = threading.Event()

            def abort(self):
                self.aborting.set()
                with self.lifecycle_lock:
                    super().abort()

        backend = LifecycleBackend()
        ctl = Controller(backend, good, start=False)
        owned = threading.Event()
        go = threading.Event()
        errors = []

        def loader():
            try:
                with backend.lifecycle_lock:
                    owned.set()
                    go.wait(2)
                    ctl.safety()
            except Exception as exc:
                errors.append(exc)

        def recover():
            try:
                ctl.recover()
            except Exception as exc:
                errors.append(exc)

        load = threading.Thread(target=loader, daemon=True)
        reset = threading.Thread(target=recover, daemon=True)
        with patch("inference.laf_service.core.time.sleep"):
            load.start()
            self.assertTrue(owned.wait(2))
            reset.start()
            self.assertTrue(backend.aborting.wait(2))
            go.set()
            load.join(2)
            reset.join(2)
        self.assertFalse(load.is_alive())
        self.assertFalse(reset.is_alive())
        self.assertEqual(errors, [])

    def test_core_config_validates_optional_swap_limit(self):
        import json, tempfile
        from pathlib import Path
        from .core_server import load_config
        from .test_configured_profiles import selection

        config = {
            "state_dir": "/state",
            "runtime_dir": "/runtime",
            "lifecycle_runtime_dir": "/lifecycle",
            **selection(),
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(config))
            path.chmod(0o600)
            self.assertNotIn("swap_limit_mib", load_config(path))
            config["swap_limit_mib"] = 2048
            path.write_text(json.dumps(config))
            self.assertEqual(load_config(path)["swap_limit_mib"], 2048)
            for value in [True, "2048", 511, 2049]:
                config["swap_limit_mib"] = value
                path.write_text(json.dumps(config))
                with self.assertRaises(ServiceError):
                    load_config(path)

    def test_core_rejects_host_policy_mismatch_before_backend_construction(self):
        import json, tempfile
        from pathlib import Path
        from unittest.mock import Mock
        from .core_server import run_core
        from .test_configured_profiles import selection
        config={'state_dir':'/state','runtime_dir':'/runtime','lifecycle_runtime_dir':'/lifecycle',**selection(),'swap_limit_mib':2048}
        client=Mock();client.resource_policy.return_value={'epoch':'a'*32,'swap_limit_mib':512}
        factory=Mock()
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'config.json';path.write_text(json.dumps(config));path.chmod(0o600)
            with self.assertRaisesRegex(ServiceError,'policy mismatch'):
                run_core(path,backend_factory=factory,client_factory=lambda _:client,install_signals=False)
        factory.assert_not_called()

    def test_client_binds_readonly_policy_to_host_epoch(self):
        from .lifecycle import HostLifecycle
        from .lifecycle_client import LifecycleClient
        from .test_lifecycle import Provider, sample
        port=HostLifecycle(Provider(),sample,swap_limit_mib=2048)
        client=LifecycleClient(lambda req,timeout:port.dispatch(req))
        self.assertEqual(client.resource_policy()['swap_limit_mib'],2048)
        client.status()
        port.epoch='b'*32
        with self.assertRaises(ServiceError):client.status()
        with self.assertRaises(ServiceError):client.resource_policy()
