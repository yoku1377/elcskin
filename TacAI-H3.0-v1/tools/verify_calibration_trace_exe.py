"""Inspect v25 calibration trace EXE and run only its no-connection startup."""
from __future__ import annotations

import ast
import copy
import json
import marshal
import os
from pathlib import Path
import re
import subprocess

from PyInstaller.archive.readers import CArchiveReader
from verify_raw_baseline_exe import (
    BINARY_HASHES, PROFILES, UNCHANGED_MATH, UNCHANGED_TRANSPORT,
    assigned_constant, code_signature, methods, sha, stored_constant,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tmp/calibration_trace_20261020_build/source"
BASE_SOURCE = ROOT / "tmp/calibration_gap_20261020_build/source"
OUT = ROOT / "release/TacAI_H3_v5_3_25_CalibrationTrace_20261020"
REPORT = ROOT / "outputs/calibration_trace_20261020"
EXE = OUT / "TacAI H3 v5.3.25 Calibration Trace.exe"
VERSION = "5.3.25-calibration-trace"
CHANGED = {"main.py", "firmware_updater.py", "calibration_acquisition.py", "test_acquisition_diagnostics.py"}
ADDED = {"calibration_trajectory.py", "test_calibration_trajectory.py", "test_calibration_trajectory_monitor.py"}
UNCHANGED_MONITOR_METHODS = (
    "_update_force_display", "_effective_tare_raw", "_update_raw_baseline", "_raw_baseline_updates_allowed",
    "_reset_release_zero", "_reset_local_force", "_tare", "_warm_start_tare_history",
    "_on_release_zero_changed", "_on_force_mode_changed", "_on_local_force_changed",
    "_on_high_force_correction_changed", "_on_stream_gap", "_accept_stream_gap_frame",
    "_on_diagnostic", "_on_stream_fault", "_check_data_timeout",
    "_record_base", "_csv_header", "_calibration_session_active", "_capture_force_trace",
)


def files(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes() for path in directory.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}


class StripTrajectoryErrorGuard(ast.NodeTransformer):
    """Only permit the new guard that preserves an explicit write-failure UI."""
    removed = 0

    def visit_If(self, node):
        expected = ast.parse("not getattr(dialog, 'trajectory_error', None)", mode="eval").body
        if ast.dump(node.test) == ast.dump(expected):
            assert not node.orelse
            self.removed += 1
            return node.body
        return self.generic_visit(node)


def integration_checks(before, after):
    assert assigned_constant(after["main.py"], "MONITOR_VERSION") == VERSION
    assert assigned_constant(after["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.25"
    assert after["firmware_updater.py"].decode("utf-8-sig") == before["firmware_updater.py"].decode(
        "utf-8-sig").replace('UPGRADER_VERSION = "5.3.24"', 'UPGRADER_VERSION = "5.3.25"', 1)
    fixture_before = "dialog=Mock();dialog.capturing=True;self.w.calibration_dialog=dialog"
    fixture_after = "dialog=Mock();dialog.capturing=True;dialog.trajectory_error=None;self.w.calibration_dialog=dialog"
    old_fixture = before["test_acquisition_diagnostics.py"].decode("utf-8-sig")
    assert old_fixture.count(fixture_before) == 1
    assert after["test_acquisition_diagnostics.py"].decode("utf-8-sig") == old_fixture.replace(
        fixture_before, fixture_after, 1), "Only the new real-dialog attribute may be added to this Mock fixture"
    old_monitor, new_monitor = methods(before["main.py"]), methods(after["main.py"])
    for method in UNCHANGED_MONITOR_METHODS:
        key = "Monitor." + method
        assert ast.dump(old_monitor[key]) == ast.dump(new_monitor[key]), key
    guard = StripTrajectoryErrorGuard()
    normalized_interrupt = guard.visit(copy.deepcopy(new_monitor["Monitor._interrupt_protocol"]))
    assert guard.removed == 1
    assert ast.dump(normalized_interrupt) == ast.dump(old_monitor["Monitor._interrupt_protocol"])
    # Transport implementation and retry/recovery dispatch are preserved.
    for name, node in old_monitor.items():
        if name.startswith(("RawForceReader.", "SpiForceReader.", "CH341ForceReader.")):
            assert ast.dump(node) == ast.dump(new_monitor[name]), name
    old_dialog, new_dialog = methods(before["calibration_acquisition.py"]), methods(after["calibration_acquisition.py"])
    header = "CalibrationAcquisitionDialog._write_headers"
    assert ast.dump(old_dialog[header]) == ast.dump(new_dialog[header]), "Plateau CSV schema changed"
    assert assigned_constant(after["calibration_acquisition.py"], "CAPTURE_SECONDS") == 3.0
    assert assigned_constant(after["calibration_acquisition.py"], "MIN_CAPTURE_FRAMES") == assigned_constant(
        before["calibration_acquisition.py"], "MIN_CAPTURE_FRAMES")
    gauge = ast.unparse(new_dialog["CalibrationAcquisitionDialog._gauge_reference"])
    assert "sample['received_at'] <= frame_received_at" in gauge
    assert "max(prior, key=" in gauge
    assert "age > 1.0" in gauge and "np.isfinite(sample['value_n'])" in gauge
    feed = new_dialog["CalibrationAcquisitionDialog.feed_frame"]
    trajectory_updates = [node for node in ast.walk(feed) if isinstance(node, ast.Call)
                          and isinstance(node.func, ast.Attribute) and node.func.attr == "update"
                          and isinstance(node.func.value, ast.Name) and node.func.value.id == "trajectory_row"
                          and node.args and isinstance(node.args[0], ast.Dict)]
    metadata, = [node.args[0] for node in trajectory_updates
                 if any(isinstance(key, ast.Constant) and key.value == "reference_provenance"
                        for key in node.args[0].keys)]
    trajectory_fields = {key.value: value for key, value in zip(metadata.keys, metadata.values)
                         if isinstance(key, ast.Constant)}
    manual_truth = trajectory_fields["reference_force_n"]
    assert isinstance(manual_truth, ast.IfExp) and isinstance(manual_truth.orelse, ast.Constant)
    assert manual_truth.orelse.value is None, "Manual entry must not masquerade as continuous force truth"
    assert isinstance(trajectory_fields["manual_plateau_reading_n"], ast.IfExp)
    assert "audit_context=self._calibration_audit_context(frame)" in after["main.py"].decode("utf-8-sig")
    audit = ast.unparse(new_monitor["Monitor._calibration_audit_context"])
    for field in ("manual_tare_raw24", "effective_tare_raw24", "release_offset_raw24",
                  "host_tare_epoch", "effective_baseline_epoch", "selected_force_n", "firmware_baseline_epoch"):
        assert field in audit, field
    writer = after["calibration_trajectory.py"].decode("utf-8-sig")
    for field in ("reference_provenance", "manual_plateau_reading_n", "gauge_age_s", "trajectory_phase",
                  "manual_tare_raw_json", "effective_tare_raw_json", "residual_offset_raw_json", "audit_context_json"):
        assert field in writer, field
    assert 'path.open("x"' in writer, "Trace files must not overwrite earlier sessions"
    assert "allow_nan=False" in writer


def main():
    manifest = json.loads((REPORT / "manifest.json").read_text(encoding="utf-8"))
    baseline_manifest = json.loads((ROOT / "outputs/calibration_gap_20261020/manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION
    assert Path(manifest["exe"]).resolve() == EXE.resolve()
    assert manifest["standalone_exe"] is True and manifest["zip_created"] is False
    assert {path.name for path in OUT.iterdir()} == {EXE.name}
    assert not OUT.with_suffix(".zip").exists()
    assert EXE.stat().st_size == manifest["exe_bytes"]
    assert sha(EXE.read_bytes()) == manifest["exe_sha256"]
    assert baseline_manifest["version"] == "5.3.24-calibration-gap"
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
    integration_checks(before, after)

    archive = CArchiveReader(str(EXE))
    pyz_name, = (name for name, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    embedded = {"main.py": marshal.loads(archive.extract("main"))}
    embedded_names = {"calibration_acquisition.py", "calibration_trajectory.py", "firmware_updater.py",
                      "release_baseline.py", "residual_zero.py", "recording_report.py", "folder_report.py",
                      "force_trace.py", "baseline_quality.py", *UNCHANGED_MATH, *UNCHANGED_TRANSPORT}
    for name in sorted(embedded_names):
        embedded[name] = pyz.extract(name.removesuffix(".py"))
    for name, code in embedded.items():
        assert code_signature(code) == code_signature(compile(after[name], name, "exec", dont_inherit=True, optimize=0)), name
    assert stored_constant(embedded["main.py"], "MONITOR_VERSION") == VERSION
    assert stored_constant(embedded["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.25"
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
    assert startup_result["version"] == VERSION and startup_result["serial_connected"] is False
    assert startup_result["automatic_compensation_enabled"] is True
    assert set(startup_result["automatic_available_profiles"]) == {"front", "back"}
    assert not startup_result["parameter_load_error"]
    result = dict(result="passed", version=VERSION, exe=str(EXE), exe_sha256=manifest["exe_sha256"],
        exe_bytes=manifest["exe_bytes"], standalone_exe_only=True, zip_created=False,
        frozen_sources_match_manifest=True, embedded_code_matches_frozen_source=True,
        embedded_modules_compared=len(embedded), all_other_sources_byte_identical_to_v24=True,
        original_force_model_and_profiles_unchanged=True, model_runtime_and_raw_compensation_unchanged=True,
        wch_drivers_and_dlls_unchanged=True, iap_protocols_and_firmware_unchanged=True,
        snapshot_retry_and_calibration_recovery_unchanged=True, plateau_csv_schema_unchanged=True,
        continuous_sensor_gauge_and_event_streams=True,
        offline_tests_run=int(tests_run), offline_tests_skipped=8, offline_test_modules=len(manifest["tests"]),
        firmware_state_injection_cases=3, startup_exit_code=0, startup_offscreen_passed=True,
        startup_witness=str(startup_path), hardware_connected=False, field_validation="pending")
    (REPORT / "release_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
