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

# Changed 2026-11-13: pure pressure-window admission prototype.

"""Pure rolling-pressure admission prototype; not enabled by runtime configuration.

The owner supplies calibrated thresholds. This module never unloads a provider,
clears a safety latch, modifies swap, or grants permission to replay a job.
"""

from collections import deque
from dataclasses import dataclass
import math

GIB = 1 << 30


@dataclass(frozen=True)
class WindowPolicy:
    window_seconds: float
    max_gap_seconds: float
    io_limit_bytes: int
    min_free_swap_bytes: int
    quiet_seconds: float
    cooldown_seconds: float
    max_pause_seconds: float
    page_bytes: int

    def __post_init__(self):
        for name in (
            "window_seconds",
            "max_gap_seconds",
            "quiet_seconds",
            "cooldown_seconds",
            "max_pause_seconds",
        ):
            value = getattr(self, name)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(name)
        if self.max_gap_seconds < 1:
            raise ValueError("max_gap_seconds must allow the half-second host sampler")
        if self.max_gap_seconds > self.window_seconds:
            raise ValueError("sampling gap exceeds window")
        for name in ("io_limit_bytes", "min_free_swap_bytes", "page_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(name)


class SwapWindow:
    """Return admission guidance under an external owner's serialized control."""

    def __init__(self, policy):
        self.policy = policy
        self.samples = deque()
        self.fault = None
        self.paused = False
        self.unload_requested = False
        self.quiet_since = None
        self.paused_since = None

    def stop(self, reason):
        self.fault = reason
        return {"action": "stop", "reason": reason}

    def observe(self, now, sample, *, idle, cleanup_confirmed=False):
        """Latch invalid/hard-pressure observations; never reset a fault."""
        if self.fault:
            return {"action": "stop", "reason": self.fault}
        if (
            type(sample) is not dict
            or type(idle) is not bool
            or type(cleanup_confirmed) is not bool
        ):
            return self.stop("invalid owner observation")
        fields = (
            "mem_available",
            "vram_total",
            "vram_used",
            "swap_total",
            "swap_free",
            "pswpin",
            "pswpout",
        )
        if type(now) not in (int, float) or not math.isfinite(now):
            return self.stop("invalid time")
        if any(type(sample.get(k)) is not int or sample[k] < 0 for k in fields):
            return self.stop("invalid telemetry")
        psi = sample.get("psi_full_avg10")
        if (
            type(psi) not in (int, float)
            or not math.isfinite(psi)
            or not 0 <= psi <= 100
        ):
            return self.stop("invalid pressure telemetry")
        if (
            sample["swap_free"] > sample["swap_total"]
            or sample["vram_used"] > sample["vram_total"]
        ):
            return self.stop("inconsistent telemetry")
        if (
            sample["mem_available"] < 8 * GIB
            or sample["vram_total"] - sample["vram_used"] < 2 * GIB
        ):
            return self.stop("memory reserve")
        if psi > 10 or sample["swap_free"] < self.policy.min_free_swap_bytes:
            return self.stop("pressure or swap reserve")
        previous = self.samples[-1] if self.samples else None
        if previous:
            elapsed = now - previous[0]
            if elapsed <= 0 or elapsed > self.policy.max_gap_seconds:
                return self.stop("stale or non-monotonic sampling")
            if sample["swap_total"] != previous[1]["swap_total"] or any(
                sample[k] < previous[1][k] for k in ("pswpin", "pswpout")
            ):
                return self.stop("counter or swap topology changed")
        if len(self.samples) >= 4096:
            return self.stop("sampling capacity exceeded")
        self.samples.append((now, {k: sample[k] for k in (*fields, "psi_full_avg10")}))
        # Keep the sample immediately preceding the cutoff: conservative bounds.
        while (
            len(self.samples) > 1
            and self.samples[1][0] <= now - self.policy.window_seconds
        ):
            self.samples.popleft()
        first = self.samples[0]
        # Each direction has its own limit; this is not combined disk traffic.
        traffic = (
            max(sample[k] - first[1][k] for k in ("pswpin", "pswpout"))
            * self.policy.page_bytes
        )
        if traffic > self.policy.io_limit_bytes or psi > 1:
            if not self.paused:
                self.paused_since = now
            self.paused = True
        if self.paused:
            if now - self.paused_since > self.policy.max_pause_seconds:
                return self.stop("pressure pause deadline exceeded")
            if not idle:
                self.quiet_since = None
                return {"action": "drain", "reason": "pressure pause"}
            if not self.unload_requested:
                self.unload_requested = True
                self.quiet_since = None
                return {"action": "unload_owned", "reason": "pressure pause"}
            if not cleanup_confirmed:
                self.quiet_since = None
                return {
                    "action": "wait_cleanup",
                    "reason": "owned provider stop unconfirmed",
                }
            quiet = (
                previous is not None
                and psi <= 1
                and all(
                    sample[k] == previous[1][k]
                    for k in ("pswpin", "pswpout", "swap_free")
                )
            )
            self.quiet_since = (
                (self.quiet_since if self.quiet_since is not None else now)
                if quiet
                else None
            )
            if (
                self.quiet_since is not None
                and now - self.quiet_since >= self.policy.quiet_seconds
                and now - self.paused_since >= self.policy.cooldown_seconds
            ):
                return {
                    "action": "owner_recovery_required",
                    "reason": "quiet after confirmed cleanup",
                }
            return {"action": "wait_quiet", "reason": "pressure pause"}
        if now - first[0] < self.policy.window_seconds:
            return {"action": "warmup", "reason": "incomplete window"}
        return {"action": "admit", "reason": "reserves and rolling pressure acceptable"}


class HostPressureAuthority:
    """Single host-owned rolling verdict shared by monitoring and provider gates.

    Callers must retain their epoch/generation fences. This object owns no
    provider and cannot clear a host fault or initiate recovery.
    """

    def __init__(self, parameters, read_resources, clock):
        import os
        import threading
        from dataclasses import asdict

        if type(parameters) is not dict:
            raise ValueError("explicit rolling policy required")
        self.policy = WindowPolicy(**parameters)
        if self.policy.page_bytes != os.sysconf("SC_PAGE_SIZE"):
            raise ValueError("rolling policy page size differs from host")
        self.parameters = asdict(self.policy)
        self.read_resources, self.clock = read_resources, clock
        self.lock = threading.RLock()
        self.window = SwapWindow(self.policy)
        self.last_time = None
        self.latest = None
        self.decision = {"action": "warmup", "reason": "no host sample"}

    def observe(self):
        # Every observation is fresh, never a cached execution permit.
        with self.lock:
            now = self.clock()
            sample = self.read_resources()
            self.decision = self.window.observe(now, sample, idle=False)
            if self.decision["action"] != "stop":
                from .lifecycle import FIELDS, validate_sample
                from .core import ServiceError

                try:
                    validate_sample({key: sample[key] for key in FIELDS})
                    if (
                        sample["swap_used"]
                        != sample["swap_total"] - sample["swap_free"]
                    ):
                        raise ValueError("swap accounting inconsistent")
                except (KeyError, ValueError, TypeError, ServiceError):
                    self.decision = self.window.stop("invalid host capacity accounting")
            self.last_time, self.latest = now, sample
            return dict(self.decision)

    def admission(self):
        from .core import ServiceError
        from .lifecycle import FIELDS, validate_sample

        with self.lock:
            decision = self.observe()
            if decision["action"] != "admit":
                raise ServiceError(
                    "host rolling admission refused: " + decision["reason"]
                )
            return validate_sample({key: self.latest[key] for key in FIELDS})

    def recovery_sample(self):
        """Validate fresh capacity-aware quiet telemetry without clearing state."""
        from .core import ServiceError
        from .lifecycle import FIELDS, validate_sample

        with self.lock:
            sample = self.read_resources()
            decision = SwapWindow(self.policy).observe(self.clock(), sample, idle=True)
            if decision["action"] != "warmup":
                raise ServiceError("rolling recovery reserves or pressure invalid")
            validate_sample({key: sample[key] for key in FIELDS})
            if sample["swap_used"] != sample["swap_total"] - sample["swap_free"]:
                raise ServiceError("rolling recovery swap accounting inconsistent")
            return dict(sample)

    def reset_after_owner_recovery(self):
        # Called only inside the existing verified-stop/quiet/fence transaction.
        # Start a new warmup; never manufacture an immediate admission permit.
        with self.lock:
            self.window = SwapWindow(self.policy)
            self.last_time = self.latest = None
            self.decision = {"action": "warmup", "reason": "owner recovery warmup"}
