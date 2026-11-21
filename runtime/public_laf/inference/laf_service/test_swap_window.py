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

# Changed 2026-11-13: fake-clock pressure-window tests.

"""Pure fake-clock tests; no service, model or system memory changes."""

import unittest
from .swap_window import SwapWindow, WindowPolicy, GIB


def sample(**changes):
    result = dict(
        mem_available=16 * GIB,
        vram_total=8 * GIB,
        vram_used=2 * GIB,
        swap_total=64 * GIB,
        swap_free=16 * GIB,
        pswpin=0,
        pswpout=0,
        psi_full_avg10=0,
    )
    result.update(changes)
    return result


def monitor():
    return SwapWindow(WindowPolicy(4, 1, 4096, GIB, 2, 3, 10, 4096))


class WindowTests(unittest.TestCase):
    def test_quiet_occupied_swap_admits(self):
        m = monitor()
        for t in range(5):
            result = m.observe(t, sample(), idle=True)
        self.assertEqual(result["action"], "admit")

    def test_both_io_directions_pause_and_unload_once(self):
        for key in ("pswpin", "pswpout"):
            m = monitor()
            m.observe(0, sample(), idle=True)
            s = sample(**{key: 2})
            self.assertEqual(m.observe(1, s, idle=False)["action"], "drain")
            self.assertEqual(m.observe(2, s, idle=True)["action"], "unload_owned")
            self.assertEqual(m.observe(3, s, idle=True)["action"], "wait_cleanup")
            for t in (4, 5, 6):
                result = m.observe(t, s, idle=True, cleanup_confirmed=True)
            self.assertEqual(result["action"], "owner_recovery_required")
            self.assertTrue(m.paused)

    def test_bad_telemetry_and_reserves_latch(self):
        for changes in (
            {"swap_free": 0},
            {"mem_available": 7 * GIB},
            {"psi_full_avg10": float("nan")},
            {"psi_full_avg10": 11},
            {"pswpin": -1},
            {"swap_free": 65 * GIB},
        ):
            m = monitor()
            self.assertEqual(
                m.observe(0, sample(**changes), idle=True)["action"], "stop"
            )
            self.assertEqual(m.observe(1, sample(), idle=True)["action"], "stop")

    def test_time_counter_and_topology_anomalies(self):
        for time, changes in (
            (0, {}),
            (2, {}),
            (1, {"pswpin": 0}),
            (1, {"swap_total": 65 * GIB}),
        ):
            m = monitor()
            m.observe(0, sample(pswpin=1), idle=True)
            self.assertEqual(
                m.observe(time, sample(pswpin=1) | changes, idle=True)["action"], "stop"
            )

    def test_window_ages_out_old_io_without_cumulative_failure(self):
        m = monitor()
        for t in range(13):
            result = m.observe(t, sample(pswpout=t // 4), idle=True)
        self.assertEqual(result["action"], "admit")

    def test_unconfirmed_cleanup_eventually_stops(self):
        m = monitor()
        m.observe(0, sample(), idle=True)
        for t in range(1, 13):
            result = m.observe(t, sample(psi_full_avg10=2), idle=True)
        self.assertEqual(result["action"], "stop")

    def test_invalid_owner_inputs_latch(self):
        for value in (None, [], 1, "bad"):
            m = monitor()
            self.assertEqual(m.observe(0, value, idle=True)["action"], "stop")
            self.assertIsNotNone(m.fault)
        self.assertEqual(monitor().observe(0, sample(), idle=1)["action"], "stop")

    def test_payload_not_retained_and_samples_bounded(self):
        m = monitor()
        m.observe(0, sample(extra_payload="unrelated"), idle=True)
        self.assertNotIn("extra_payload", m.samples[0][1])
        for t in range(1, 4097):
            result = m.observe(t / 10000, sample(), idle=True)
        self.assertEqual(result["action"], "stop")
        self.assertLessEqual(len(m.samples), 4096)

    def test_policy_validation(self):
        with self.assertRaises(ValueError):
            WindowPolicy(4, .25, 4096, GIB, 2, 3, 10, 4096)
        with self.assertRaises(ValueError):
            WindowPolicy(4, 5, 4096, GIB, 2, 3, 10, 4096)


if __name__ == "__main__":
    unittest.main()


class AuthorityTests(unittest.TestCase):
    def make(self):
        import os
        from dataclasses import asdict
        from .swap_window import HostPressureAuthority

        self.now = 0.0
        self.resources = sample(swap_used=48 * GIB)
        parameters = asdict(
            WindowPolicy(2, 1, 4096, GIB, 2, 3, 10, os.sysconf("SC_PAGE_SIZE"))
        )
        authority = HostPressureAuthority(
            parameters, lambda: dict(self.resources), lambda: self.now
        )
        return authority

    def warmup(self, authority):
        for t in (0.0, 0.5, 1.0, 1.5, 2.0):
            self.now = t
            authority.observe()

    def test_authority_warmup_fresh_admission_and_recovery(self):
        from .core import ServiceError

        a = self.make()
        with self.assertRaises(ServiceError):
            a.admission()
        for t in (0.5, 1.0, 1.5, 2.0):
            self.now = t
            a.observe()
        self.now = 2.5
        self.assertEqual(a.admission()["swap_used"], 48 * GIB)
        self.now = 3.0
        self.resources["pswpin"] = 2
        with self.assertRaises(ServiceError):
            a.admission()
        a.reset_after_owner_recovery()
        self.now = 3.5
        with self.assertRaises(ServiceError):
            a.admission()

    def test_provider_start_rechecks_shared_authority(self):
        from .lifecycle import HostLifecycle
        from .core import ServiceError
        from .test_lifecycle import Provider

        a = self.make()
        self.warmup(a)
        provider = Provider()
        base = lambda: {
            k: self.resources[k]
            for k in (
                "mem_available",
                "swap_used",
                "vram_total",
                "vram_used",
                "pswpin",
                "pswpout",
                "psi_full_avg10",
            )
        }
        host = HostLifecycle(provider, base, resource_authority=a)
        identity = {"epoch": host.epoch, "generation": host.generation}
        self.now = 2.5
        self.resources["pswpout"] = 2
        with self.assertRaises(ServiceError):
            host.dispatch({"operation": "ensure", **identity})
        self.assertFalse(provider.running())

    def test_host_backed_controller_has_no_duplicate_lifetime_swap_limit(self):
        from .core import Controller
        from .test_service import Fake

        a = self.make()
        self.warmup(a)
        self.now = 2.5
        controller = Controller(Fake(), a.admission, start=False, host_admission=True)
        # More than a legacy lifetime budget, but trickled below each window.
        for i in range(1, 12):
            self.now = 2.5 + i * 0.5
            self.resources["swap_used"] += 256 * 1024**2
            self.resources["swap_free"] -= 256 * 1024**2
            controller.safety()
        self.assertIsNone(controller.fault)

    def test_shared_contract_roundtrip_start_then_pressure_revocation(self):
        from .lifecycle import HostLifecycle, FIELDS
        from .lifecycle_client import LifecycleClient
        from .test_lifecycle import Provider
        from .core import ServiceError

        a = self.make()
        self.warmup(a)

        def advancing_clock():
            self.now += 0.1
            return self.now

        a.clock = advancing_clock
        provider = Provider()
        host = HostLifecycle(
            provider,
            lambda: {k: self.resources[k] for k in FIELDS},
            resource_authority=a,
        )
        client = LifecycleClient(lambda request, timeout: host.dispatch(request))
        policy = client.resource_policy()
        self.assertEqual(policy["policy_version"], 2)
        self.assertEqual(policy["rolling_swap_policy"], a.parameters)
        client.stop()
        client.ensure()
        self.assertTrue(provider.alive)
        calls = provider.calls.count("ensure")
        self.resources["swap_free"] = 0
        self.resources["swap_used"] = 64 * GIB
        with self.assertRaises(ServiceError):
            client.ensure()
        self.assertEqual(provider.calls.count("ensure"), calls)
        with self.assertRaises(ServiceError):
            client.read_resources()
