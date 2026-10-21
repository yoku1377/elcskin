"""Track an explicit software work baseline in RAW coordinates after release.

The release detector recognizes a software pattern, not physical zero truth.
Its scalar zero is never used as a measurement: the caller recomputes its full
force model with ``manual_tare24 + offset_raw24`` after a reference commit.
"""
from dataclasses import dataclass

import numpy as np

from residual_zero import ResidualZeroFilter


@dataclass(frozen=True)
class ReleaseBaselineResult:
    changed: bool
    reason: str
    baseline_epoch: int
    offset_raw24: np.ndarray


def _raw24(value):
    raw = np.asarray(value, dtype=float).reshape(-1)
    if raw.shape != (24,) or not np.all(np.isfinite(raw)):
        raise ValueError("A baseline requires 24 finite RAW coordinates")
    return raw


class ReleaseBaselineTracker:
    """Keep the work offset separate from the user's unchanged manual tare."""

    def __init__(self):
        self._detector = ResidualZeroFilter()
        self._offset_raw24 = np.zeros(24, dtype=float)
        self._last_commit_frames = ()
        self.baseline_epoch = 0
        self.last_reason = "NO_RELEASE_REFERENCE"
        self._epoch = None
        self._have_epoch = False
        self._manual_tare24 = None

    @property
    def offset_raw24(self):
        return self._offset_raw24.copy()

    @property
    def last_commit_frames(self):
        return tuple((time, raw.copy()) for time, raw in self._last_commit_frames)

    def effective_tare(self, manual_tare24):
        return _raw24(manual_tare24).copy() + self._offset_raw24

    def _result(self, changed, reason):
        self.last_reason = str(reason)
        return ReleaseBaselineResult(bool(changed), self.last_reason,
                                     self.baseline_epoch, self.offset_raw24)

    def reset_cycle(self):
        """Discard a pending interaction while preserving an established zero."""
        self._detector.reset()
        self.last_reason = "CYCLE_RESET"

    def reset_reference(self):
        """Restore the manual coordinate system and clear release evidence."""
        changed = bool(np.any(self._offset_raw24 != 0.))
        self._offset_raw24 = np.zeros(24, dtype=float)
        if changed:
            self.baseline_epoch += 1
        self._last_commit_frames = ()
        self._detector.reset()
        self._have_epoch = False
        self._epoch = None
        self._manual_tare24 = None
        self.last_reason = "REFERENCE_RESET"
        return changed

    def feed(self, raw24, work_force_n, received_at, *, manual_tare24,
             epoch=None, valid=True, enabled=True, allow_update=True):
        # Never carry a work offset across a changed acquisition/manual epoch,
        # including an invalid first frame in the new epoch.
        epoch_changed = self._have_epoch and epoch != self._epoch
        reference_changed = self.reset_reference() if epoch_changed else False
        self._epoch, self._have_epoch = epoch, True
        if not enabled:
            reference_changed = self.reset_reference() or reference_changed
            return self._result(reference_changed, "DISABLED_REFERENCE_RESET")
        if not valid:
            self.reset_cycle()
            return self._result(reference_changed, "INVALID_FRAME")
        try:
            manual = _raw24(manual_tare24)
        except (TypeError, ValueError, OverflowError):
            self.reset_cycle()
            return self._result(reference_changed, "INVALID_MANUAL_TARE")
        if self._manual_tare24 is not None and not np.array_equal(manual, self._manual_tare24):
            reference_changed = self.reset_reference() or reference_changed
            self._epoch, self._have_epoch = epoch, True
            epoch_changed = True
        self._manual_tare24 = manual.copy()
        if not allow_update:
            self.reset_cycle()
            return self._result(reference_changed, "UPDATES_FROZEN")
        if reference_changed:
            # This force was computed in the old coordinates.  The caller must
            # first recompute against the cleared manual reference.
            return self._result(True, "EPOCH_REFERENCE_RESET")
        detected = self._detector.feed(raw24, work_force_n, received_at,
                                       epoch=epoch, valid=valid, enabled=True)
        if not detected.applied:
            return self._result(False, detected.reason)
        reference = self._detector.reference_raw24
        frames = self._detector.release_samples
        if reference is None or not frames:
            self.reset_cycle()
            return self._result(False, "MISSING_RELEASE_REFERENCE")
        # Direct RAW assignment is essential: never add an old offset again and
        # never subtract a scalar N value from a nonlinear force model output.
        self._offset_raw24 = reference - manual
        self._last_commit_frames = tuple((time, raw.copy()) for time, raw in frames)
        self.baseline_epoch += 1
        self._detector.reset()
        return self._result(True, "RELEASE_RAW_BASELINE_UPDATED")
