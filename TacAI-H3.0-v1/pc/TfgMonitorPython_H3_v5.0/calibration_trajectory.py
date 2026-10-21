"""Append-only calibration audit streams, independent of stable-point CSVs.

Host receipt timestamps are recorded for later alignment. They are not a claim
of hardware synchronization between the force gauge and the sensor.
"""

from __future__ import annotations

import csv
from datetime import datetime
import json
import math
from pathlib import Path
import re
import time
import uuid


RAW_COLUMNS = [f"S{sensor}_dB{axis}" for sensor in range(1, 9) for axis in "xyz"]
DELTA_COLUMNS = [f"S{sensor}_retared_dB{axis}" for sensor in range(1, 9) for axis in "xyz"]

SENSOR_COLUMNS = [
    "record_type", "schema_version", "trajectory_session_id", "host_received_at_unix_s", "host_received_time_iso",
    "host_written_monotonic_s", "device_ms", "device_sn", "silicone_batch",
    "magnet_assembly", "operator", "temperature_c", "note", "preset", "purpose",
    "step_index", "step_total", "repeat", "position", "direction", "target_n",
    "trajectory_phase", "capturing", "stream_paused", "reference_mode",
    "reference_force_n", "reference_provenance", "manual_plateau_reading_n",
    "gauge_host_received_at_unix_s", "gauge_age_s", "gauge_raw", "gauge_event_json",
    "clock_alignment", "upper_model_n", "upper_model_confidence",
    "upper_model_load_score", "upper_model_soft_gate_rescue",
    "upper_regression_unclamped_n", "upper_regression_clamped_n",
    "upper_direction_invariant_fallback_n", "upper_release_similarity_ratio",
    "upper_gate_reason", "firmware_fx", "firmware_fy", "firmware_fz",
    "host_tare_valid", "host_tare_epoch", "effective_baseline_epoch",
    "firmware_id", "firmware_baseline_epoch", "manual_tare_raw_json",
    "effective_tare_raw_json", "residual_offset_raw_json", "audit_context_json",
    *RAW_COLUMNS, *DELTA_COLUMNS,
]
GAUGE_COLUMNS = [
    "record_type", "schema_version", "trajectory_session_id", "host_received_at_unix_s", "host_received_time_iso",
    "host_written_monotonic_s", "force_n", "scaled_value_repr", "is_finite", "raw_text", "reference_mode",
    "gauge_port", "gauge_baud", "scale_to_n", "clock_alignment",
    "protocol", "source_unit", "original_value", "raw_bytes_hex",
    "protocol_metadata_json", "sample_context_json", "gauge_connection_epoch",
]
EVENT_COLUMNS = [
    "record_type", "schema_version", "trajectory_session_id", "host_event_at_unix_s", "host_event_time_iso",
    "host_written_monotonic_s", "event", "step_index", "target_n", "details_json",
]


def _json_value(value):
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def audit_json(value) -> str:
    return json.dumps(_json_value(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def received_time_iso(received_at: float) -> str:
    return datetime.fromtimestamp(received_at).isoformat(timespec="microseconds")


class CalibrationTrajectoryWriter:
    """Own the three uniquely named streams and flush at most one second apart."""

    def __init__(self, output_dir, device_sn: str):
        self.session_id = uuid.uuid4().hex
        safe_sn = re.sub(r"[^0-9A-Za-z_.-]+", "_", device_sn.strip())
        base = Path(output_dir) / f"H3_trajectory_{safe_sn}_{self.session_id}"
        self.sensor_path = base.with_name(base.name + "_sensor.csv")
        self.gauge_path = base.with_name(base.name + "_gauge.csv")
        self.events_path = base.with_name(base.name + "_events.csv")
        self.closed = False
        self._files = {}
        self._writers = {}
        self._last_flush = time.monotonic()
        try:
            for key, path, columns in (
                ("sensor", self.sensor_path, SENSOR_COLUMNS),
                ("gauge", self.gauge_path, GAUGE_COLUMNS),
                ("events", self.events_path, EVENT_COLUMNS),
            ):
                output = path.open("x", newline="", encoding="utf-8-sig")
                self._files[key] = output
                writer = csv.DictWriter(output, fieldnames=columns)
                self._writers[key] = writer
                writer.writeheader()
            self.flush()
        except OSError:
            for output in self._files.values():
                try:
                    output.close()
                except OSError:
                    pass
            self.closed = True
            raise

    def _write(self, stream: str, row: dict):
        if self.closed:
            raise OSError("连续轨迹文件已经关闭")
        item = {"record_type": f"CALIBRATION_TRAJECTORY_{stream.upper()}",
                "schema_version": 1, "trajectory_session_id": self.session_id, **row}
        item["host_written_monotonic_s"] = time.monotonic()
        self._writers[stream].writerow(item)
        if time.monotonic() - self._last_flush >= 1.0:
            self.flush()

    def sensor(self, row: dict):
        self._write("sensor", row)

    def gauge(self, row: dict):
        self._write("gauge", row)

    def event(self, event: str, *, step_index=None, target_n=None, details=None):
        now = time.time()
        self._write("events", {
            "host_event_at_unix_s": now,
            "host_event_time_iso": received_time_iso(now),
            "event": event, "step_index": step_index, "target_n": target_n,
            "details_json": audit_json(details or {}),
        })
        self.flush()

    def flush(self):
        if self.closed:
            return
        for output in self._files.values():
            output.flush()
        self._last_flush = time.monotonic()

    def close(self):
        if self.closed:
            return
        error = None
        try:
            self.flush()
        except OSError as failure:
            error = failure
        for output in self._files.values():
            try:
                output.close()
            except OSError as failure:
                error = error or failure
        self.closed = True
        if error is not None:
            raise error
