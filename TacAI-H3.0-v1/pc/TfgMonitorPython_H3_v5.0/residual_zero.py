"""Explicit software suppression of a stable low tail after a larger press.

The observed pattern cannot distinguish complete release from a steady partial
load.  This display filter changes only its returned value; RAW, tare and the
original force remain the caller's measurement evidence.
"""
from collections import deque
from dataclasses import dataclass, replace
import math

import numpy as np

from baseline_quality import DEFAULT_POLICY, evaluate_window


MAX_RESIDUAL_N = 2.0
ARM_PEAK_N = 2.5
DROP_RATIO = .60
MIN_STABLE_S = 1.0
EXIT_RAW_NORM = 350.0


@dataclass(frozen=True)
class ResidualZeroResult:
    force_n: float | None
    applied: bool
    reason: str
    peak_force_n: float


class ResidualZeroFilter:
    """Suppress only an armed, stable low-output tail; never infer true unload."""

    def __init__(self, *, max_residual_n=MAX_RESIDUAL_N, arm_peak_n=ARM_PEAK_N,
                 drop_ratio=DROP_RATIO, min_stable_s=MIN_STABLE_S,
                 exit_raw_norm=EXIT_RAW_NORM, policy=DEFAULT_POLICY):
        values = [max_residual_n, arm_peak_n, drop_ratio, min_stable_s, exit_raw_norm]
        if (not all(math.isfinite(float(value)) for value in values)
                or min(float(value) for value in values) <= 0
                or not 0 < drop_ratio < 1 or arm_peak_n <= max_residual_n):
            raise ValueError("Invalid residual suppression limits")
        self.max_residual_n = float(max_residual_n)
        self.arm_peak_n = float(arm_peak_n)
        self.drop_ratio = float(drop_ratio)
        self.min_stable_s = float(min_stable_s)
        self.exit_raw_norm = float(exit_raw_norm)
        self.policy = replace(policy, min_span_s=max(policy.min_span_s, self.min_stable_s))
        self.reset()

    def reset(self):
        self.peak_force_n = 0.
        self._candidate = deque()
        self._zero_raw = None
        self._release_samples = ()
        self._last_time = None
        self._last_raw = None
        self._last_force = None
        self._last_result = None
        self._epoch = None
        self._have_epoch = False

    @property
    def reference_raw24(self):
        """A detached copy of the detected release-window RAW median."""
        return None if self._zero_raw is None else self._zero_raw.copy()

    @property
    def release_samples(self):
        """Detached real-time samples that established the release reference."""
        return tuple((time, raw.copy()) for time, raw in self._release_samples)

    def _finish(self, force, applied, reason):
        result = ResidualZeroResult(force, applied, reason, float(self.peak_force_n))
        self._last_result = result
        return result

    def _restart_from_current(self, force):
        self._candidate.clear()
        self._zero_raw = None
        self._release_samples = ()
        self.peak_force_n = force if force >= self.arm_peak_n else 0.

    def feed(self, raw24, force_n, received_at, *, epoch=None, valid=True, enabled=True):
        if not valid:
            self.reset()
            return self._finish(None, False, "INVALID_FRAME")
        try:
            raw = np.asarray(raw24, dtype=float).reshape(-1)
            force, now = float(force_n), float(received_at)
            if (raw.shape != (24,) or not np.all(np.isfinite(raw))
                    or not math.isfinite(force) or force < 0 or not math.isfinite(now)):
                raise ValueError("Invalid accepted force sample")
        except (TypeError, ValueError, OverflowError):
            self.reset()
            return self._finish(None, False, "INVALID_INPUT")
        if not enabled:
            self.reset()
            return self._finish(force, False, "DISABLED")

        reset_reason = None
        if self._have_epoch and epoch != self._epoch:
            self.reset()
            reset_reason = "EPOCH_RESET"
        if self._last_time is not None:
            if now == self._last_time and force == self._last_force and np.array_equal(raw, self._last_raw):
                # Redisplay or a duplicated accepted frame cannot manufacture
                # another sample or advance the stable-time requirement.
                return self._last_result
            if now <= self._last_time:
                self.reset()
                reset_reason = "TIME_RESET"
            elif now - self._last_time > self.policy.max_gap_s + 1e-6:
                self.reset()
                reset_reason = "GAP_RESET"
        self._epoch, self._have_epoch = epoch, True
        self._last_time, self._last_force = now, force
        self._last_raw = raw.copy()

        if self._zero_raw is not None:
            if force > self.max_residual_n:
                self._restart_from_current(force)
                return self._finish(force, False, "ABOVE_RESIDUAL_RANGE")
            if float(np.linalg.norm(raw - self._zero_raw)) > self.exit_raw_norm:
                # A new magnetic response starts a new interaction.  Forget the
                # previous peak so a real new low load is not suppressed again.
                self._restart_from_current(force)
                return self._finish(force, False, "NEW_RAW_RESPONSE")
            return self._finish(0., True, "SOFTWARE_RESIDUAL_ZERO")

        if force >= self.arm_peak_n:
            self.peak_force_n = max(self.peak_force_n, force)
        if force > self.max_residual_n:
            self._candidate.clear()
            return self._finish(force, False, reset_reason or "PEAK_OR_LOAD_VISIBLE")
        if self.peak_force_n < self.arm_peak_n:
            self._candidate.clear()
            return self._finish(force, False, reset_reason or "NO_PRIOR_PEAK")
        if force > self.drop_ratio * self.peak_force_n:
            self._candidate.clear()
            return self._finish(force, False, "DROP_NOT_SUFFICIENT")

        self._candidate.append((now, raw.copy()))
        # Keep the shortest real-time suffix that can still meet both minimum
        # duration and sample count, including slower accepted sample rates.
        while (len(self._candidate) > self.policy.min_samples
               and now - self._candidate[1][0] >= self.policy.min_span_s - 1e-6):
            self._candidate.popleft()
        times = [sample[0] for sample in self._candidate]
        samples = [sample[1] for sample in self._candidate]
        quality = evaluate_window(samples, times, now=now, policy=self.policy)
        if quality["status"] != "READY":
            reason = ("WAITING_STABLE_RESIDUAL" if quality["status"] == "WAITING"
                      else "UNSTABLE_RESIDUAL_RAW")
            return self._finish(force, False, reason)
        self._zero_raw = np.median(np.stack(samples), axis=0)
        self._release_samples = tuple((time, raw.copy()) for time, raw in self._candidate)
        self._candidate.clear()
        return self._finish(0., True, "SOFTWARE_RESIDUAL_ZERO")
