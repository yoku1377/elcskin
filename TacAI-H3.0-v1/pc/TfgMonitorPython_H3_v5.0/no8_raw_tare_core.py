"""Core logic for the H3 single-port RAW re-tare validation tool.

The firmware emits two text records with the same ``device_ms`` value::

    RAW,<device_ms>,24 baseline-subtracted magnetic values
    FORCE,<device_ms>,Fx,Fy,Fz

This module pairs those records, rejects non-finite data, builds a stable
software-side RAW tare, and evaluates the V3-3 H3 force model against
``current_RAW - tare_RAW``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import math
import os
import re
from pathlib import Path
import tempfile
import time
from typing import Iterable

import numpy as np

from h3_v3_3_force_model import (
    REGION_NAMES,
    estimate_force_details,
)


RAW_COUNT = 24
CHANNELS = 8
TARE_FRAME_COUNT = 20
TARE_MIN_FRAMES = 10
TARE_MAX_FORCE_RANGE_N = 0.80
TARE_MAX_RAW_DEVIATION_NORM = 300.0
LOW_SIGNAL_NORM = 1500.0
MAGNETIC_ACTIVITY_WARNING = 1500.0
MAGNETIC_COHERENCE_WARNING = 0.85
SATURATION_WARNING_N = 34.95

CALIBRATED_POSITIONS = {
    "后按键中心",
    "前后按键交界中心",
    "前按键中心",
    "前按键尖端",
}

RAW_NAMES = [f"B{col}{row}_{axis}" for col in range(4) for row in range(2) for axis in "xyz"]
DELTA_NAMES = [f"dB{col}{row}_{axis}" for col in range(4) for row in range(2) for axis in "xyz"]


@dataclass(frozen=True)
class AcquisitionDiagnostic:
    device_ms: int
    received_at: float
    firmware_id: str
    valid_mask: int
    error_count: int
    baseline_epoch: int
    reset_reason: str = ""
    raw_line: str = ""

    @property
    def valid(self) -> bool:
        return self.valid_mask == 255 and self.baseline_epoch > 0


@dataclass(frozen=True)
class PairedFrame:
    device_ms: int
    received_at: float
    raw: np.ndarray
    force: np.ndarray
    diagnostic: AcquisitionDiagnostic | None = None
    reset_reason: str = ""


@dataclass(frozen=True)
class TareResult:
    raw: np.ndarray
    firmware_force: np.ndarray
    frame_count: int
    force_range_n: float
    raw_max_deviation_norm: float


@dataclass(frozen=True)
class ForceResult:
    fz_n: float
    delta_raw: np.ndarray
    delta_norm: float
    activity: float
    coherence: float
    confidence: float
    best_region: int
    region_weights: np.ndarray
    load_score: float
    soft_gate_rescue: bool
    regression_unclamped_n: float
    regression_clamped_n: float
    invariant_fallback_n: float
    release_similarity_ratio: float
    gate_reason: str
    flags: tuple[str, ...]


class StreamPairer:
    """Pair RAW and FORCE records using the firmware timestamp."""

    def __init__(self, max_pending: int = 64):
        self.max_pending = max_pending
        self.pending: dict[int, dict[str, object]] = {}
        self.ignored_lines = 0
        self.invalid_lines = 0
        self.dropped_unpaired = 0
        self.diagnostics_available = False
        self.last_diagnostic: AcquisitionDiagnostic | None = None
        self.last_emitted_device_ms: int | None = None
        self._reset_pending = ""

    def discard_pending(self):
        self.dropped_unpaired += sum("raw" in s or "force" in s for s in self.pending.values())
        self.pending.clear()

    def _feed_diagnostic(self, fields, received_at, line):
        self.diagnostics_available = True
        self.discard_pending()
        if len(fields) != 6 or not re.fullmatch(r"[A-Za-z0-9_.-]+", fields[2]):
            raise ValueError("DIAG requires timestamp, firmware ID, mask, error count and baseline epoch")
        if any(not re.fullmatch(r"[0-9]+", fields[i]) for i in (1, 3, 4, 5)):
            raise ValueError("DIAG numeric fields must be unsigned decimal integers")
        device_ms, mask, errors, epoch = (int(fields[i]) for i in (1, 3, 4, 5))
        if mask > 255 or any(v > 0xFFFFFFFF for v in (device_ms, errors, epoch)):
            raise ValueError("DIAG numeric field is out of range")
        old = self.last_diagnostic
        reasons = []
        if old is not None:
            if old.firmware_id != fields[2]: reasons.append("FIRMWARE_CHANGED")
            if old.baseline_epoch != epoch: reasons.append("BASELINE_CHANGED")
            if device_ms < old.device_ms: reasons.append("DEVICE_TIME_RESET")
            if errors < old.error_count: reasons.append("ERROR_COUNTER_RESET")
        elif self.last_emitted_device_ms is not None:
            reasons.append("DIAGNOSTICS_ENABLED")
        if reasons:
            self.last_emitted_device_ms = None
        diag = AcquisitionDiagnostic(device_ms, received_at, fields[2], mask, errors, epoch,
                                     "|".join(reasons), line.strip())
        self.last_diagnostic = diag
        if diag.valid and device_ms != self.last_emitted_device_ms:
            self.pending[device_ms] = {"received_at": received_at, "diagnostic": diag}

    @staticmethod
    def _finite(values: Iterable[float]) -> bool:
        return bool(np.all(np.isfinite(np.asarray(list(values), dtype=float))))

    def feed_line(self, line: str, received_at: float | None = None) -> PairedFrame | None:
        fields = [field.strip() for field in line.strip().split(",")]
        if not fields or fields[0] not in {"RAW", "FORCE", "DIAG"}:
            self.ignored_lines += 1
            return None

        now = time.time() if received_at is None else float(received_at)
        try:
            if fields[0] == "DIAG":
                self._feed_diagnostic(fields, now, line)
                return None
            if fields[0] == "RAW":
                if len(fields) != 26:
                    raise ValueError("RAW field count must be 26")
                device_ms = int(float(fields[1]))
                raw = np.asarray([float(value) for value in fields[2:]], dtype=float)
                if raw.size != RAW_COUNT or not np.all(np.isfinite(raw)):
                    raise ValueError("RAW must contain 24 finite values")
                payload_name, payload = "raw", raw
            else:
                if len(fields) != 5:
                    raise ValueError("FORCE field count must be 5")
                device_ms = int(float(fields[1]))
                force = np.asarray([float(value) for value in fields[2:5]], dtype=float)
                if force.size != 3 or not np.all(np.isfinite(force)):
                    raise ValueError("FORCE must contain three finite values")
                payload_name, payload = "force", force
        except (TypeError, ValueError, OverflowError):
            self.invalid_lines += 1
            self.discard_pending()
            raise

        if self.diagnostics_available:
            slot = self.pending.get(device_ms)
            if slot is None or "diagnostic" not in slot:
                self.ignored_lines += 1
                return None
        elif self.last_emitted_device_ms is not None and device_ms < self.last_emitted_device_ms:
            self.discard_pending()
            self.last_emitted_device_ms = None
            self._reset_pending = "DEVICE_TIME_RESET"
        if device_ms == self.last_emitted_device_ms:
            self.ignored_lines += 1
            return None
        slot = self.pending.setdefault(device_ms, {"received_at": now})
        slot[payload_name] = payload
        slot["received_at"] = max(float(slot["received_at"]), now)

        if "raw" in slot and "force" in slot:
            self.pending.pop(device_ms, None)
            self.last_emitted_device_ms = device_ms
            reset_reason, self._reset_pending = self._reset_pending, ""
            return PairedFrame(
                device_ms=device_ms,
                received_at=float(slot["received_at"]),
                raw=np.asarray(slot["raw"], dtype=float),
                force=np.asarray(slot["force"], dtype=float),
                diagnostic=slot.get("diagnostic"),
                reset_reason=reset_reason,
            )

        while len(self.pending) > self.max_pending:
            oldest = next(iter(self.pending))
            self.pending.pop(oldest, None)
            self.dropped_unpaired += 1
        return None


def build_tare(frames: Iterable[PairedFrame]) -> TareResult:
    selected = list(frames)[-TARE_FRAME_COUNT:]
    diagnostics = [frame.diagnostic for frame in selected if frame.diagnostic is not None]
    if any(not diag.valid for diag in diagnostics):
        raise ValueError("置零帧包含无效采集诊断；请完全卸载并等待有效采集恢复")
    if len({(diag.firmware_id, diag.baseline_epoch) for diag in diagnostics}) > 1:
        raise ValueError("置零帧跨越固件或基线变化；请重新收集连续有效帧")
    if len(selected) < TARE_MIN_FRAMES:
        raise ValueError(f"置零至少需要 {TARE_MIN_FRAMES} 个完整配对帧")

    raw_stack = np.stack([frame.raw for frame in selected])
    force_stack = np.stack([frame.force for frame in selected])
    if not np.all(np.isfinite(raw_stack)) or not np.all(np.isfinite(force_stack)):
        raise ValueError("置零帧包含 NaN/Inf")

    raw_median = np.median(raw_stack, axis=0)
    force_median = np.median(force_stack, axis=0)
    force_range = float(np.ptp(force_stack[:, 2]))
    raw_deviation = np.linalg.norm(raw_stack - raw_median, axis=1)
    raw_max_deviation = float(np.max(raw_deviation))

    if force_range > TARE_MAX_FORCE_RANGE_N:
        raise ValueError(
            f"最近帧固件 Fz 波动 {force_range:.3f} N，超过 {TARE_MAX_FORCE_RANGE_N:.2f} N；"
            "请完全卸载并保持稳定后重试"
        )
    if raw_max_deviation > TARE_MAX_RAW_DEVIATION_NORM:
        raise ValueError(
            f"最近帧 RAW 仍在变化（最大偏离 {raw_max_deviation:.1f}），超过 "
            f"{TARE_MAX_RAW_DEVIATION_NORM:.0f}；请完全卸载并保持稳定后重试"
        )

    return TareResult(
        raw=raw_median,
        firmware_force=force_median,
        frame_count=len(selected),
        force_range_n=force_range,
        raw_max_deviation_norm=raw_max_deviation,
    )


def evaluate_raw_force(
    raw: np.ndarray, tare_raw: np.ndarray, *,
    use_provisional_high_force_correction: bool = True,
) -> ForceResult:
    raw = np.asarray(raw, dtype=float).reshape(-1)
    tare_raw = np.asarray(tare_raw, dtype=float).reshape(-1)
    if raw.size != RAW_COUNT or tare_raw.size != RAW_COUNT:
        raise ValueError("RAW 与 tare_RAW 都必须包含 24 个值")
    if not np.all(np.isfinite(raw)) or not np.all(np.isfinite(tare_raw)):
        raise ValueError("RAW 与 tare_RAW 必须全部为有限数")

    delta = raw - tare_raw
    delta_norm = float(np.linalg.norm(delta))
    vectors = delta.reshape(CHANNELS, 3)
    magnitudes = np.linalg.norm(vectors, axis=1)
    activity = float(magnitudes.sum())
    coherence = 0.0 if activity <= 1e-12 else float(np.linalg.norm(vectors.sum(axis=0)) / activity)
    estimate = estimate_force_details(
        delta,
        use_provisional_high_force_correction=use_provisional_high_force_correction,
    )
    fz = estimate.fz_n
    weights = estimate.region_weights
    confidence = estimate.confidence
    best_region = estimate.best_region
    classifier_score = estimate.classifier_score
    soft_gate_rescue = estimate.rescued

    flags: list[str] = []
    if delta_norm < LOW_SIGNAL_NORM:
        flags.append("LOW_SIGNAL_THRESHOLD")
    if confidence < 0.35 and delta_norm >= LOW_SIGNAL_NORM:
        flags.append("MODEL_LOW_CONFIDENCE")
    if soft_gate_rescue:
        flags.append("MODEL_SOFT_GATE_RESCUE")
    if estimate.gate_reason == "DIRECTION_INVARIANT_FALLBACK":
        flags.append("MODEL_DIRECTION_INVARIANT_FALLBACK")
    if estimate.gate_reason == "CLASSIFIER_IGNORED_CONTINUITY":
        flags.append("MODEL_CLASSIFIER_IGNORED_CONTINUITY")
    if estimate.gate_reason == "KNOWN_RELEASE_PROTOTYPE":
        flags.append("MODEL_KNOWN_RELEASE_ZERO")
    if estimate.regression_unclamped_n <= 0.0 and fz <= 0.0:
        flags.append("MODEL_REGRESSION_NONPOSITIVE")
    if activity >= MAGNETIC_ACTIVITY_WARNING and coherence >= MAGNETIC_COHERENCE_WARNING:
        flags.append("MAGNETIC_COHERENCE")
    if fz >= SATURATION_WARNING_N:
        flags.append("UPPER_MODEL_SATURATED")

    return ForceResult(
        fz_n=float(fz),
        delta_raw=delta,
        delta_norm=delta_norm,
        activity=activity,
        coherence=coherence,
        confidence=float(confidence),
        best_region=int(best_region),
        region_weights=np.asarray(weights, dtype=float),
        load_score=float(classifier_score),
        soft_gate_rescue=soft_gate_rescue,
        regression_unclamped_n=float(estimate.regression_unclamped_n),
        regression_clamped_n=float(estimate.regression_clamped_n),
        invariant_fallback_n=float(estimate.invariant_fallback_n),
        release_similarity_ratio=float(estimate.release_similarity_ratio),
        gate_reason=estimate.gate_reason,
        flags=tuple(flags),
    )


CSV_HEADER = [
    "local_time_iso",
    "local_time_epoch_ms",
    "device_ms",
    "session_id",
    "segment_id",
    "device_id",
    "tester",
    "test_mode",
    "position",
    "cable_state",
    "magnetic_state",
    "tare_true_fz_n",
    "true_total_fz_n",
    "true_increment_fz_n",
    "upper_raw_tared_fz_n",
    "upper_regression_unclamped_n",
    "upper_regression_clamped_n",
    "upper_direction_invariant_fallback_n",
    "upper_load_score",
    "upper_release_similarity_ratio",
    "upper_gate_reason",
    "upper_error_n",
    "firmware_fx_n",
    "firmware_fy_n",
    "firmware_fz_n",
    "tare_firmware_fz_n",
    "raw_delta_norm",
    "raw_activity_sum",
    "magnetic_direction_coherence_0_1",
    "model_confidence_0_1",
    "model_best_region",
    "region_weight_back_center",
    "region_weight_between_buttons",
    "region_weight_front_center",
    "region_weight_front_tip",
    "quality_flags",
    "record_status",
    "invalid_reason",
    "note",
    *RAW_NAMES,
    *DELTA_NAMES,
]


SUMMARY_HEADER = [
    "session_id",
    "segment_id",
    "test_mode",
    "position",
    "tare_true_fz_n",
    "true_total_fz_n",
    "true_increment_fz_n",
    "duration_s",
    "paired_frames",
    "upper_fz_median_n",
    "upper_fz_std_n",
    "upper_error_median_n",
    "firmware_fz_median_n",
    "raw_delta_norm_median",
    "confidence_median",
    "quality_flags",
    "record_status",
    "invalid_reason",
    "note",
]


def record_row(
    frame: PairedFrame,
    result: ForceResult,
    *,
    session_id: str,
    segment_id: int,
    device_id: str,
    tester: str,
    test_mode: str,
    position: str,
    cable_state: str,
    magnetic_state: str,
    tare_true_fz_n: float,
    true_total_fz_n: float,
    tare_firmware_fz_n: float,
    note: str,
    extra_flags: Iterable[str] = (),
) -> list[object]:
    true_increment = float(true_total_fz_n) - float(tare_true_fz_n)
    flags = list(result.flags) + [flag for flag in extra_flags if flag]
    if position not in CALIBRATED_POSITIONS:
        flags.append("UNCALIBRATED_POSITION")
    if frame.force[2] >= SATURATION_WARNING_N:
        flags.append("FIRMWARE_SATURATED")
    flags = list(dict.fromkeys(flags))
    local_dt = datetime.fromtimestamp(frame.received_at)
    return [
        local_dt.isoformat(timespec="milliseconds"),
        int(round(frame.received_at * 1000.0)),
        frame.device_ms,
        session_id,
        segment_id,
        device_id,
        tester,
        test_mode,
        position,
        cable_state,
        magnetic_state,
        float(tare_true_fz_n),
        float(true_total_fz_n),
        true_increment,
        result.fz_n,
        result.regression_unclamped_n,
        result.regression_clamped_n,
        result.invariant_fallback_n,
        result.load_score,
        result.release_similarity_ratio,
        result.gate_reason,
        result.fz_n - true_increment,
        float(frame.force[0]),
        float(frame.force[1]),
        float(frame.force[2]),
        float(tare_firmware_fz_n),
        result.delta_norm,
        result.activity,
        result.coherence,
        result.confidence,
        REGION_NAMES[result.best_region],
        *result.region_weights.tolist(),
        "|".join(flags) if flags else "OK",
        "VALID",
        "",
        note,
        *frame.raw.tolist(),
        *result.delta_raw.tolist(),
    ]


def mark_segment_void(csv_path: str | Path, segment_id: int, reason: str) -> int:
    """Keep a bad segment for traceability but mark every matching row VOID."""

    path = Path(csv_path)
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        rows = list(csv.reader(source))
    if not rows:
        return 0

    header = rows[0]
    required = ("segment_id", "record_status", "invalid_reason")
    missing = [name for name in required if name not in header]
    if missing:
        raise ValueError(f"CSV缺少作废字段：{', '.join(missing)}")

    segment_index = header.index("segment_id")
    status_index = header.index("record_status")
    reason_index = header.index("invalid_reason")
    changed = 0
    for row in rows[1:]:
        if len(row) <= max(segment_index, status_index, reason_index):
            continue
        if row[segment_index] == str(int(segment_id)):
            row[status_index] = "VOID"
            row[reason_index] = reason
            changed += 1

    if not changed:
        return 0

    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8-sig",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as target:
            temp_name = target.name
            writer = csv.writer(target)
            writer.writerows(rows)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    return changed


def finite_or_none(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def planned_reference_force(
    start_force_n: float,
    end_force_n: float,
    elapsed_s: float,
    duration_s: float,
) -> float:
    """Return the time-based linear reference for an automated test phase."""

    start = float(start_force_n)
    end = float(end_force_n)
    duration = float(duration_s)
    if not all(math.isfinite(value) for value in (start, end, elapsed_s, duration)):
        raise ValueError("计划参考力参数必须为有限数")
    if duration <= 0.0:
        return end
    progress = min(1.0, max(0.0, float(elapsed_s) / duration))
    return start + (end - start) * progress
