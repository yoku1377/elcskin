"""Freeze v28's field-observed ZP-500N stream framing; no device access."""
from __future__ import annotations

import json

import build_calibration_trace_exe as builder


ROOT = builder.ROOT


def main():
    builder.BASE_SOURCE = ROOT / "tmp/spi_ack_guard_20261020_build/source"
    builder.BASE_MANIFEST = ROOT / "outputs/spi_ack_guard_20261020/manifest.json"
    builder.BASE_VERSION = "5.3.27-spi-ack-guard"
    builder.OUT = ROOT / "release/TacAI_H3_v5_3_28_ZP500NStream_20261020"
    builder.WORK = ROOT / "tmp/zp500n_stream_20261020_build"
    builder.REPORT = ROOT / "outputs/zp500n_stream_20261020"
    builder.NAME = "TacAI H3 v5.3.28 ZP500N Stream"
    builder.VERSION = "5.3.28-zp500n-stream"
    builder.CHANGED = {"main.py", "firmware_updater.py", "calibration_acquisition.py",
                       "zp500n_protocol.py", "test_zp500n_protocol.py", "test_zp500n_gauge.py"}
    builder.ADDED = {"test_zp500n_live_replay.py", "test_zp500n_backlog.py"}
    baseline = json.loads(builder.BASE_MANIFEST.read_text(encoding="utf-8"))
    builder.CHECKS = [*baseline["tests"], "test_zp500n_live_replay", "test_zp500n_backlog"]
    field_audit = json.loads((builder.REPORT / "fieldlog_audit.json").read_text(encoding="utf-8"))
    builder.main()
    manifest_path = builder.REPORT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        zp500n_protocol_changed=True, gauge_read_requests=["FA", "25"],
        zp500n_field_descriptor_bytes=36, zp500n_descriptor_repetitions_required=3,
        zp500n_field_numeric_bytes=6, zp500n_field_units="N only; other numeric formats unverified",
        zp500n_metadata_idle_not_required=True,
        zp500n_transition_metadata_tail_consumed_by_phase=True,
        zp500n_raw_preserved=True, zp500n_backlog_not_fresh_reference=True,
        field_observed_value_n=16.1, field_observed_dynamic_values=False,
        field_observed_zero_negative_full_range=False,
        source_timestamps_available=False, clock_alignment="host receipt only",
        reference_format_failure_cancels_incomplete_capture=True,
        spi_snapshot_ack_guard_preserved=True,
        iap_protocols_dlls_and_firmware_unchanged=True,
        original_force_model_and_profiles_unchanged=True,
        hardware_validation="field raw replay plus fake serial tests; new EXE dynamic field validation pending",
        field_capture_sha256=field_audit["sha256"],
        field_after25_numeric_samples=field_audit["numeric"]["sample_count_after25"],
        field_numeric_distinct_values=field_audit["numeric"]["distinct_values"],
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
