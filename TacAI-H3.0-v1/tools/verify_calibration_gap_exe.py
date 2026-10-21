"""Independently verify the v24 frozen EXE and run its no-connection self-test."""
from __future__ import annotations

import ast
import json
import marshal
import os
from pathlib import Path
import re
import subprocess

from PyInstaller.archive.readers import CArchiveReader
from verify_raw_baseline_exe import (
    BINARY_HASHES, PROFILES, UNCHANGED_MATH, UNCHANGED_TRANSPORT,
    assigned_constant, calls, code_signature, methods, sha, stored_constant,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tmp/calibration_gap_20261020_build/source"
BASE_SOURCE = ROOT / "tmp/raw_baseline_20261019_build/source"
OUT = ROOT / "release/TacAI_H3_v5_3_24_CalibrationRecovery_20261020"
REPORT = ROOT / "outputs/calibration_gap_20261020"
EXE = OUT / "TacAI H3 v5.3.24 Calibration Recovery.exe"
VERSION = "5.3.24-calibration-gap"
CHANGED = {"main.py", "firmware_updater.py", "calibration_acquisition.py", "test_spi_monitor_ui.py"}
ADDED = {"test_calibration_gap_dialog.py", "test_calibration_gap_monitor.py"}
MAIN_CHANGED = {
    "SpiForceReader._snapshot_trace", "Monitor.__init__", "Monitor._build_ui_v524",
    "Monitor._toggle_connection", "Monitor._interrupt_protocol", "Monitor._on_force_mode_changed",
    "Monitor._raw_baseline_updates_allowed", "Monitor._on_release_zero_changed",
    "Monitor._on_local_force_changed", "Monitor._on_high_force_correction_changed", "Monitor._tare",
    "Monitor._on_paired", "Monitor._check_data_timeout", "Monitor._record_base", "Monitor.closeEvent",
}
MAIN_ADDED = {"Monitor._calibration_session_active", "Monitor._on_stream_gap", "Monitor._accept_stream_gap_frame"}
DIALOG_CHANGED = {
    "CalibrationAcquisitionDialog.__init__", "CalibrationAcquisitionDialog._start_session",
    "CalibrationAcquisitionDialog._begin_capture", "CalibrationAcquisitionDialog.feed_frame",
    "CalibrationAcquisitionDialog._complete_capture", "CalibrationAcquisitionDialog._previous",
    "CalibrationAcquisitionDialog._skip", "CalibrationAcquisitionDialog._finish_session",
    "CalibrationAcquisitionDialog.closeEvent",
}
DIALOG_ADDED = {"CalibrationAcquisitionDialog.pause_for_stream_gap", "CalibrationAcquisitionDialog.resume_after_stream_gap"}


def files(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes() for path in directory.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}


def check_method_delta(before, after, expected_changed, expected_added):
    old, new = methods(before), methods(after)
    assert set(old) <= set(new), "An existing function was removed"
    assert set(new) - set(old) == expected_added
    actual_changed = {name for name in old if ast.dump(old[name]) != ast.dump(new[name])}
    assert actual_changed == expected_changed, actual_changed
    return new


def check_integration(before, after):
    assert assigned_constant(after["main.py"], "MONITOR_VERSION") == VERSION
    assert assigned_constant(after["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.24"
    assert after["firmware_updater.py"].decode("utf-8-sig") == before["firmware_updater.py"].decode(
        "utf-8-sig").replace('UPGRADER_VERSION = "5.3.23"', 'UPGRADER_VERSION = "5.3.24"', 1)
    monitor = check_method_delta(before["main.py"], after["main.py"], MAIN_CHANGED, MAIN_ADDED)
    dialog = check_method_delta(before["calibration_acquisition.py"], after["calibration_acquisition.py"],
                                DIALOG_CHANGED, DIALOG_ADDED)
    gap = monitor["Monitor._on_stream_gap"]
    assert calls(gap, "pause_for_stream_gap")
    assert not any(calls(gap, name) for name in ("_interrupt_protocol", "_invalidate_host_tare", "_reset_local_force", "_tare"))
    assert calls(monitor["Monitor._on_stream_fault"], "_interrupt_protocol")
    assert calls(monitor["Monitor._on_paired"], "_accept_stream_gap_frame")
    assert calls(monitor["Monitor._on_paired"], "resume_after_stream_gap")
    recovery = ast.unparse(monitor["Monitor._accept_stream_gap_frame"])
    for required in ("firmware_id", "baseline_epoch", "host_tare_epoch", "work_baseline_epoch",
                     "frame.device_ms", "time.time()", "PAIRED_DATA_TIMEOUT_S", "return None"):
        assert required in recovery, required
    assert "max(frame.received_at, time.time())" in recovery
    for name in ("_raw_baseline_updates_allowed", "_on_force_mode_changed", "_on_release_zero_changed",
                 "_on_local_force_changed", "_on_high_force_correction_changed", "_tare"):
        assert calls(monitor["Monitor." + name], "_calibration_session_active"), name
    pause = dialog["CalibrationAcquisitionDialog.pause_for_stream_gap"]
    assert not any(calls(pause, name) for name in ("_finish_session", "_complete_capture", "_show_step", "open"))
    resume = dialog["CalibrationAcquisitionDialog.resume_after_stream_gap"]
    assert not calls(resume, "_begin_capture")
    assert "self.capturing = False" in ast.unparse(resume)
    assert calls(dialog["CalibrationAcquisitionDialog.closeEvent"], "_finish_session")
    assert assigned_constant(after["calibration_acquisition.py"], "CAPTURE_SECONDS") == 3.0


def main():
    manifest = json.loads((REPORT / "manifest.json").read_text(encoding="utf-8"))
    baseline_manifest = json.loads((ROOT / "outputs/raw_baseline_20261019/manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION
    assert Path(manifest["exe"]).resolve() == EXE.resolve()
    assert manifest["standalone_exe"] is True and manifest["zip_created"] is False
    assert {path.name for path in OUT.iterdir()} == {EXE.name}, "Delivery must contain only the EXE"
    assert not OUT.with_suffix(".zip").exists()
    assert EXE.stat().st_size == manifest["exe_bytes"]
    assert sha(EXE.read_bytes()) == manifest["exe_sha256"]
    assert baseline_manifest["version"] == "5.3.23-raw-baseline"
    assert sha(Path(baseline_manifest["exe"]).read_bytes()) == baseline_manifest["exe_sha256"]
    before, after = files(BASE_SOURCE), files(SOURCE)
    assert {name: sha(data) for name, data in before.items()} == baseline_manifest["source_sha256"]
    assert {name: sha(data) for name, data in after.items()} == manifest["source_sha256"]
    assert set(after) == set(before) | ADDED
    assert {name for name in before if before[name] != after[name]} == CHANGED
    assert set(manifest["changed_sources"]) == CHANGED and set(manifest["added_sources"]) == ADDED
    for name in (*UNCHANGED_MATH, *UNCHANGED_TRANSPORT, "baseline_quality.py", "release_baseline.py",
                 "residual_zero.py", "recording_report.py", "folder_report.py", "force_trace.py", *PROFILES):
        assert before[name] == after[name], name
    for name, expected in BINARY_HASHES.items():
        assert before[name] == after[name] and sha(after[name]) == expected, name
    check_integration(before, after)

    archive = CArchiveReader(str(EXE))
    pyz_name, = (name for name, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    embedded = {"main.py": marshal.loads(archive.extract("main"))}
    embedded_names = {"calibration_acquisition.py", "firmware_updater.py", "release_baseline.py",
                      "residual_zero.py", "recording_report.py", "folder_report.py", "force_trace.py",
                      "baseline_quality.py", *UNCHANGED_MATH, *UNCHANGED_TRANSPORT}
    for name in sorted(embedded_names):
        embedded[name] = pyz.extract(name.removesuffix(".py"))
    for name, code in embedded.items():
        reviewed = compile(after[name], name, "exec", dont_inherit=True, optimize=0)
        assert code_signature(code) == code_signature(reviewed), f"Embedded logic differs: {name}"
    assert stored_constant(embedded["main.py"], "MONITOR_VERSION") == VERSION
    assert stored_constant(embedded["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.24"
    for name, expected in (("SNAPSHOT_MAX_ATTEMPTS", 3), ("SNAPSHOT_STALE_MIN_POLLS", 3),
                           ("SNAPSHOT_STALE_MIN_SECONDS", .18)):
        assert stored_constant(embedded["spi_iap_protocol.py"], name) == expected
    for name in (*PROFILES, *BINARY_HASHES):
        entry, = [candidate for candidate in archive.toc if candidate.casefold() == name.casefold()]
        assert archive.extract(entry) == after[name], name
    offline_log = (REPORT / "offline_tests.log").read_text(encoding="utf-8")
    assert offline_log.strip().endswith("OK (skipped=8)") and "FAILED" not in offline_log
    tests_run, = re.findall(r"^Ran (\d+) tests in ", offline_log, re.MULTILINE)
    assert len([line for line in offline_log.splitlines() if " ... skipped " in line]) == 8
    for module in manifest["tests"]:
        assert "(" + module + "." in offline_log, module
    c_log = (REPORT / "firmware_state_repro.log").read_text(encoding="utf-8")
    assert "PASS: 3 production RXIRQ stale snapshot/reissue recovery cases" in c_log and "\nOK\n" in c_log
    assert "Build complete!" in (REPORT / "build.log").read_text(encoding="utf-8")

    # The application self-test creates a Monitor and exits without connecting
    # any serial, SPI, USB or J-Link device. No hardware actions are requested.
    startup_path = REPORT / "startup_selftest.json"
    assert not startup_path.exists(), "Do not replace a previous startup witness"
    startup_cwd = REPORT / "startup_empty_cwd"
    startup_cwd.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1")
    startup = subprocess.run([str(EXE), "--self-test", "--self-test-json", str(startup_path)],
                             cwd=startup_cwd, env=env, timeout=60, capture_output=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    (REPORT / "startup_process.json").write_text(json.dumps(dict(
        exit_code=startup.returncode, command=[str(EXE), "--self-test", "--self-test-json", str(startup_path)],
        platform="offscreen", cwd=str(startup_cwd), stdout=startup.stdout.decode("utf-8", "replace"),
        stderr=startup.stderr.decode("utf-8", "replace")), ensure_ascii=False, indent=2), encoding="utf-8")
    assert startup.returncode == 0, startup.returncode
    startup_result = json.loads(startup_path.read_text(encoding="utf-8"))
    assert startup_result["version"] == VERSION
    assert startup_result["serial_connected"] is False
    assert startup_result["automatic_compensation_enabled"] is True
    assert set(startup_result["automatic_available_profiles"]) == {"front", "back"}
    assert not startup_result["parameter_load_error"]
    result = dict(result="passed", version=VERSION, exe=str(EXE), exe_sha256=manifest["exe_sha256"],
        exe_bytes=manifest["exe_bytes"], standalone_exe_only=True, zip_created=False,
        frozen_sources_match_manifest=True, embedded_code_matches_frozen_source=True,
        embedded_modules_compared=len(embedded), all_other_sources_byte_identical_to_v23=True,
        original_force_model_and_profiles_unchanged=True, manual_raw_tare_math_unchanged=True,
        release_baseline_detector_and_tracker_unchanged=True, wch_drivers_and_dlls_unchanged=True,
        iap_protocols_and_firmware_unchanged=True, snapshot_retry_bounds_unchanged=True,
        recoverable_gap_discards_current_window_only=True, recovery_requires_manual_recapture=True,
        session_zero_and_model_settings_frozen=True, queued_expired_frame_cannot_resume=True,
        paused_close_finishes_session=True, fatal_invalid_reset_or_timeout_ends_session=True,
        offline_tests_run=int(tests_run), offline_tests_skipped=8, offline_test_modules=len(manifest["tests"]),
        firmware_state_injection_cases=3, startup_exit_code=0, startup_offscreen_passed=True,
        startup_witness=str(startup_path), hardware_connected=False, field_validation="pending")
    (REPORT / "release_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
