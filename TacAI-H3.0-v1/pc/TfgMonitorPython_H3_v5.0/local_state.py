# Causal state selection copied from independently replayed offline modules.
# No fitting, file I/O, labels, target force, or per-round correction.
"""Causal low-output history selection; no force target or labels available."""
from collections import deque
from dataclasses import dataclass
import numpy as np
from baseline_quality import evaluate_window


@dataclass(frozen=True)
class LatchedState:
    raw_median24: np.ndarray | None
    sample_count: int = 0
    first_time: float | None = None
    last_time: float | None = None
    source_first: int | None = None
    source_last: int | None = None
    variance_sum24: float | None = None
    reason: str = 'NO_PAST_STATE'
    quality: dict | None = None


class CausalStateLatch:
    def __init__(self, baseline_policy=None):
        self.baseline_policy=baseline_policy
        self.reset()

    def reset(self):
        self.history=deque()
        self.last_time=None
        self.loaded=False
        self.state=LatchedState(None)
        self.low_started=None
        self.pending_low=deque()

    def preview_quality(self):
        """Most recent continuous low-output second, never an old quiet island."""
        if self.baseline_policy is None:return None
        history=list(self.pending_low if self.loaded and self.pending_low else self.history)
        if not history:return evaluate_window(np.empty((0,24)),[],policy=self.baseline_policy)
        newest=history[-1][0]
        recent=[r for r in history if newest-r[0]<=1.2+1e-6]
        return evaluate_window([r[1] for r in recent],[r[0] for r in recent],
                               now=self.last_time,policy=self.baseline_policy)

    def feed(self,now,raw,original_force,source_token=0):
        now=float(now);raw=np.asarray(raw,float).reshape(24);force=float(original_force)
        if not np.isfinite(now) or not np.isfinite(force) or not np.all(np.isfinite(raw)):
            self.reset();self.state=LatchedState(None,reason='INVALID_INPUT')
            return self.state
        if self.last_time is not None and now<=self.last_time:
            self.reset();self.last_time=now;self.state=LatchedState(None,reason='NONMONOTONIC_HISTORY')
            return self.state
        if self.last_time is not None and now-self.last_time>.25:
            self.history.clear()
            self.pending_low.clear();self.low_started=None
            self.state=LatchedState(None,reason='HISTORY_GAP')
        self.last_time=now
        while self.history and self.history[0][0]<now-10:
            self.history.popleft()
        if force>.05:
            self.low_started=None;self.pending_low.clear()
            if not self.loaded:
                self.state=self._select(now)
                self.loaded=True
                self.history.clear()
            return self.state
        if self.loaded:
            # A one-frame model-zero flicker cannot erase the latched history.
            # Original force remains untouched; this only governs stored state.
            if self.low_started is None:self.low_started=now
            self.pending_low.append((now,raw.copy(),int(source_token)))
            if now-self.low_started<1.0:return self.state
            self.loaded=False
            self.history=deque(self.pending_low)
            self.pending_low.clear();self.low_started=None
            self.state=LatchedState(None,reason='UNLOADED_STATE_NOT_LATCHED')
            return self.state
        self.history.append((now,raw.copy(),int(source_token)))
        return self.state

    def _select(self, now=None):
        # RAW starts changing before the force model leaves zero. Inspect the
        # selected PRE-load window, not the moving tail of the loading ramp.
        # The existing ten-second causal lookback bounds its freshness.
        if len(self.history)<10:
            return LatchedState(None,reason='INSUFFICIENT_RECORDED_HISTORY',quality=self.preview_quality())
        history=list(self.history);times=np.array([r[0] for r in history]);data=np.stack([r[1] for r in history])
        best=None
        for end in range(9,len(history)):
            if self.baseline_policy is not None and now-times[end]>self.baseline_policy.max_age_s+1e-6:continue
            for start in range(end-9,-1,-1):
                span=times[end]-times[start]
                if span>1.2:break
                if span<1.0 or np.max(np.diff(times[start:end+1]))>.25:continue
                score=float(np.var(data[start:end+1],axis=0).sum())
                key=(score,-times[end],-times[start])
                if best is None or key<best[0]:best=(key,start,end)
        if best is None:return LatchedState(None,reason='NO_CONTINUOUS_QUIET_WINDOW',quality=self.preview_quality())
        (score,_,_),start,end=best
        quality=(evaluate_window(data[start:end+1],times[start:end+1],now=now,policy=self.baseline_policy)
                 if self.baseline_policy is not None else None)
        if quality is not None and quality['status']!='READY':
            return LatchedState(None,reason=quality['reason'],quality=quality)
        return LatchedState(np.median(data[start:end+1],axis=0),end-start+1,float(times[start]),float(times[end]),history[start][2],history[end][2],score,'SOFTWARE_LOW_OUTPUT_HISTORY',quality)


"""Causal magnetic response at a crossing of the ORIGINAL model output.

15 N names the original output coordinate, not a measured force. No labels,
nominal test load, file name, elapsed hold time, or future frame is an input.
This reusable input module has no fitting, labels, or file I/O.
"""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class RisingState:
    quiet_raw24: np.ndarray | None = None
    rising_raw24: np.ndarray | None = None
    response24: np.ndarray | None = None
    available_time: float | None = None
    first_source: int | None = None
    second_source: int | None = None
    interpolation_fraction: float | None = None
    reason: str = 'NO_PAST_RISING_STATE'


class CausalRisingStateLatch:
    def __init__(self, baseline_policy=None):
        self.quiet = CausalStateLatch(baseline_policy)
        self.reset()

    def reset(self):
        self.quiet.reset()
        self.previous = None
        self.baseline_key = None
        self.state = RisingState()

    def feed(self, now, raw, original_force, source_token=0, *, valid=True):
        now = float(now)
        raw = np.asarray(raw, dtype=float).reshape(24)
        force = float(original_force)
        if not valid or not np.isfinite(now) or not np.isfinite(force) or not np.all(np.isfinite(raw)):
            self.reset()
            self.state = RisingState(reason='INVALID_INPUT')
            return self.state
        quiet = self.quiet.feed(now, raw, force, source_token)
        if quiet.raw_median24 is None or not self.quiet.loaded:
            self.previous = None
            self.baseline_key = None
            self.state = RisingState(reason=quiet.reason)
            return self.state
        baseline_key = (quiet.source_first, quiet.source_last, quiet.first_time, quiet.last_time)
        if baseline_key != self.baseline_key:
            self.previous = None
            self.baseline_key = baseline_key
            self.state = RisingState(quiet_raw24=quiet.raw_median24.copy(), reason='AWAITING_RISING_CROSSING')
        if self.state.response24 is None and self.previous is not None:
            before_t, before_raw, before_force, before_token = self.previous
            if 0 < now-before_t <= .25 and before_force < 15. <= force:
                fraction = (15.-before_force)/(force-before_force)
                anchor = before_raw + fraction*(raw-before_raw)
                self.state = RisingState(
                    quiet_raw24=quiet.raw_median24.copy(), rising_raw24=anchor,
                    response24=anchor-quiet.raw_median24, available_time=now,
                    first_source=before_token, second_source=int(source_token),
                    interpolation_fraction=float(fraction), reason='ORIGINAL_OUTPUT_CROSSING_STATE',
                )
        self.previous = (now, raw.copy(), force, int(source_token))
        return self.state


@dataclass(frozen=True)
class DualRisingState(RisingState):
    """Two observed output-coordinate crossings from the same past quiet state.

    Inherited rising/response/time/source fields refer to the20 crossing. The
    explicit15 fields remain available while the later crossing is still pending.
    Neither coordinate is an independent measured-force reference.
    """
    raw15: np.ndarray | None = None
    response15: np.ndarray | None = None
    available_time15: float | None = None
    first_source15: int | None = None
    second_source15: int | None = None
    interpolation_fraction15: float | None = None


class CausalDualRisingStateLatch:
    """Freeze causal15/20 magnetic crossings; no labels, fitting or future input."""

    def __init__(self, baseline_policy=None):
        self.quiet = CausalStateLatch(baseline_policy)
        self.reset()

    def reset(self):
        self.quiet.reset()
        self.previous = None
        self.baseline_key = None
        self.state = DualRisingState()

    def feed(self, now, raw, original_force, source_token=0, *, valid=True):
        try:
            now = float(now)
            raw = np.asarray(raw, dtype=float).reshape(24)
            force = float(original_force)
            token = int(source_token)
        except (TypeError, ValueError, OverflowError):
            self.reset()
            self.state = DualRisingState(reason='INVALID_INPUT')
            return self.state
        if not valid or not np.isfinite(now) or not np.isfinite(force) or not np.all(np.isfinite(raw)):
            self.reset()
            self.state = DualRisingState(reason='INVALID_INPUT')
            return self.state

        quiet = self.quiet.feed(now, raw, force, token)
        if quiet.raw_median24 is None or not self.quiet.loaded:
            self.previous = None
            self.baseline_key = None
            self.state = DualRisingState(reason=quiet.reason)
            return self.state
        baseline_key = (quiet.source_first, quiet.source_last, quiet.first_time, quiet.last_time)
        if baseline_key != self.baseline_key:
            self.previous = None
            self.baseline_key = baseline_key
            self.state = DualRisingState(quiet_raw24=quiet.raw_median24.copy(), reason='AWAITING_RISING15_CROSSING')

        if self.previous is not None and self.state.response24 is None:
            before_t, before_raw, before_force, before_token = self.previous
            if 0 < now-before_t <= .25:
                state = self.state
                if state.response15 is None and before_force < 15. <= force:
                    fraction15 = (15.-before_force)/(force-before_force)
                    raw15 = before_raw + fraction15*(raw-before_raw)
                    state = DualRisingState(
                        quiet_raw24=quiet.raw_median24.copy(), raw15=raw15,
                        response15=raw15-quiet.raw_median24, available_time15=now,
                        first_source15=before_token, second_source15=token,
                        interpolation_fraction15=float(fraction15), reason='AWAITING_RISING20_CROSSING',
                    )
                if state.response15 is not None and before_force < 20. <= force:
                    fraction20 = (20.-before_force)/(force-before_force)
                    raw20 = before_raw + fraction20*(raw-before_raw)
                    state = DualRisingState(
                        quiet_raw24=quiet.raw_median24.copy(), rising_raw24=raw20,
                        response24=raw20-quiet.raw_median24, available_time=now,
                        first_source=before_token, second_source=token,
                        interpolation_fraction=float(fraction20),
                        raw15=state.raw15, response15=state.response15,
                        available_time15=state.available_time15,
                        first_source15=state.first_source15, second_source15=state.second_source15,
                        interpolation_fraction15=state.interpolation_fraction15,
                        reason='ORIGINAL_OUTPUT_DUAL_CROSSING_STATE',
                    )
                self.state = state
        self.previous = (now, raw.copy(), force, token)
        return self.state
