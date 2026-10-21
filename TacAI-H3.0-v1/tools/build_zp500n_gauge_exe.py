"""Freeze the v26 ZP-500N adapter and build a single hardware-free EXE."""
from __future__ import annotations

import json
import shutil

import build_calibration_trace_exe as builder


ROOT = builder.ROOT


def main():
    builder.BASE_SOURCE = ROOT / "tmp/calibration_trace_20261020_build/source"
    builder.BASE_MANIFEST = ROOT / "outputs/calibration_trace_20261020/manifest.json"
    builder.BASE_VERSION = "5.3.25-calibration-trace"
    builder.OUT = ROOT / "release/TacAI_H3_v5_3_26_ZP500NGauge_20261020"
    builder.WORK = ROOT / "tmp/zp500n_gauge_20261020_build"
    builder.REPORT = ROOT / "outputs/zp500n_gauge_20261020"
    builder.NAME = "TacAI H3 v5.3.26 ZP500N Gauge"
    builder.VERSION = "5.3.26-zp500n-gauge"
    builder.CHANGED = {"main.py", "firmware_updater.py", "calibration_acquisition.py",
                       "calibration_trajectory.py", "test_calibration_trajectory_monitor.py"}
    builder.ADDED = {"zp500n_protocol.py", "test_zp500n_protocol.py", "test_zp500n_gauge.py"}
    builder.CHECKS = list(dict.fromkeys([*builder.CHECKS, "test_zp500n_protocol", "test_zp500n_gauge"]))
    builder.main()
    manifest_path = builder.REPORT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        zp500n_protocol=True, gauge_baud=2400, gauge_serial_format="8N1/no flow control",
        read_request_bytes=["FA", "25"], instrument_control_commands_implemented=False,
        gauge_open_settle_seconds=.05, gauge_initialization_timeout_seconds=3.0,
        gauge_metadata_receive_idle_seconds=.06, gauge_numeric_receive_window_seconds=.06,
        gauge_dtr=False, gauge_rts=False, gauge_control_lines_set_before_open=True,
        generic_ascii_sends_no_bytes=True,
        gauge_unit_confirmation_required=True, gauge_units_converted_to_n=True,
        gauge_raw_bytes_unit_and_original_value_audited=True,
        gauge_reconnect_discards_old_reference=True,
        framing_source="Static IL of the supplied vendor application; field raw reply not yet obtained",
        initial_field_probe="FA at COM13/2400/8N1 produced 0 bytes before the 50 ms open-settle change",
        field_validation="pending; a source-derived startup delay does not prove the field timeout is fixed",
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence = ROOT.parent / "tmp/zp500n_static_audit/protocol_evidence.json"
    assert evidence.is_file()
    shutil.copy2(evidence, builder.REPORT / "protocol_evidence.json")


if __name__ == "__main__":
    main()
