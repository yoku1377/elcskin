"""Bounded, lossless diagnostic snapshots of accepted force measurements.

This recorder never interprets a force value or changes a measurement.  The
caller supplies accepted samples and decides when to save or clear a trace.
"""
from collections import deque
import copy
from datetime import datetime, timezone
import json
import math
from numbers import Integral, Real
import os
from pathlib import Path
import sys
import tempfile
import uuid


SCHEMA = "tacai.force_trace"
VERSION = 1


def _json_value(value):
    """Copy diagnostic data into JSON values, representing NaN/Inf as null."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, deque)):
        return [_json_value(item) for item in value]
    # NumPy arrays/scalars can occur in measurement metadata.  Keep this module
    # independent from NumPy so it also works in standalone diagnostic tools.
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return _json_value(tolist())
    if isinstance(value, Path):
        return str(value)
    # Unexpected metadata must not prevent collection of the numeric samples.
    return str(value)


def _sample_time(sample):
    value = sample.get("received_at")
    if isinstance(value, Real) and not isinstance(value, bool):
        value = float(value)
        if math.isfinite(value):
            return value
    return None


class ForceTrace:
    """Keep at most ``max_frames`` samples and ``max_span_s`` of host time."""

    def __init__(self, root=None, max_frames=6000, max_span_s=180):
        if (isinstance(max_frames, bool) or not isinstance(max_frames, Integral)
                or max_frames <= 0):
            raise ValueError("max_frames must be a positive integer")
        if not math.isfinite(float(max_span_s)) or float(max_span_s) <= 0:
            raise ValueError("max_span_s must be finite and positive")
        default_root = (Path(sys.executable).resolve().parent
                        if getattr(sys, "frozen", False)
                        else Path(__file__).resolve().parent) / "logs" / "force_trace"
        override = os.environ.get("TACAI_FORCE_TRACE_DIR")
        self.root = Path(override) if override else Path(root) if root is not None else default_root
        self.max_frames = int(max_frames)
        self.max_span_s = float(max_span_s)
        self.frames = deque()
        self._latest_received_at = None
        self._time_ordered = True

    def __len__(self):
        return len(self.frames)

    def append(self, sample: dict):
        """Preserve all accepted samples, including zero and residual force."""
        captured = _json_value(copy.deepcopy(sample))
        timestamp = _sample_time(captured)
        if timestamp is not None:
            if self._latest_received_at is not None and timestamp < self._latest_received_at:
                self._time_ordered = False
            self._latest_received_at = (timestamp if self._latest_received_at is None
                                        else max(timestamp, self._latest_received_at))
        self.frames.append(captured)
        while len(self.frames) > self.max_frames:
            self.frames.popleft()
        if self._latest_received_at is not None:
            cutoff = self._latest_received_at - self.max_span_s
            if self._time_ordered:
                # Normal accepted streams are monotonic: each expired sample
                # is removed once instead of scanning the whole rolling trace.
                untimed = []
                while self.frames:
                    first_time = _sample_time(self.frames[0])
                    if first_time is None:
                        untimed.append(self.frames.popleft())
                    elif first_time < cutoff:
                        self.frames.popleft()
                    else:
                        break
                # Untimed diagnostics retain their arrival order and are
                # bounded by count; a missing time is not invented or inferred.
                self.frames.extendleft(reversed(untimed))
            else:
                # Only a time-disordered buffer needs full filtering.  Once
                # those late samples expire, return to the constant-time path.
                retained = deque()
                previous = None
                ordered = True
                for item in self.frames:
                    item_time = _sample_time(item)
                    if item_time is not None and item_time < cutoff:
                        continue
                    retained.append(item)
                    if item_time is not None:
                        if previous is not None and item_time < previous:
                            ordered = False
                        previous = item_time
                self.frames = retained
                self._time_ordered = ordered

    def clear(self):
        self.frames.clear()
        self._latest_received_at = None
        self._time_ordered = True

    def save(self, reason, metadata: dict):
        """Atomically save a UTF-8 JSONL trace; storage errors preserve memory."""
        if not self.frames:
            return None
        frames = list(self.frames)
        times = [t for sample in frames if (t := _sample_time(sample)) is not None]
        header = dict(type="header", schema=SCHEMA, version=VERSION,
                      reason=_json_value(copy.deepcopy(reason)),
                      metadata=_json_value(copy.deepcopy(metadata)), count=len(frames),
                      span_s=max(times) - min(times) if times else None)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%fZ")
        destination = self.root / f"{stamp}_{uuid.uuid4().hex}.jsonl"
        temporary = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=self.root, prefix=".force_trace_",
                                             suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(header, ensure_ascii=False, allow_nan=False) + "\n")
                for sample in frames:
                    row = dict(sample)
                    row.setdefault("type", "sample")
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            temporary = None
            return destination
        except OSError:
            return None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
