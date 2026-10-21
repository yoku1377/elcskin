"""Verify v26 protocol integration and its final standalone EXE without hardware."""
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
    assigned_constant, code_signature, methods, sha, stored_constant,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tmp/zp500n_gauge_20261020_build/source"
BASE_SOURCE = ROOT / "tmp/calibration_trace_20261020_build/source"
OUT = ROOT / "release/TacAI_H3_v5_3_26_ZP500NGauge_20261020"
REPORT = ROOT / "outputs/zp500n_gauge_20261020"
EXE = OUT / "TacAI H3 v5.3.26 ZP500N Gauge.exe"
VERSION = "5.3.26-zp500n-gauge"
CHANGED = {"main.py", "firmware_updater.py", "calibration_acquisition.py", "calibration_trajectory.py",
           "test_calibration_trajectory_monitor.py"}
ADDED = {"zp500n_protocol.py", "test_zp500n_protocol.py", "test_zp500n_gauge.py"}


def files(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes() for path in directory.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}


def assigned_ast(data, name):
    node, = [node.value for node in ast.parse(data.decode("utf-8-sig")).body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    return ast.dump(node)


def integration_checks(before, after):
    assert assigned_constant(after["main.py"], "MONITOR_VERSION") == VERSION
    assert assigned_constant(after["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.26"
    assert after["firmware_updater.py"].decode("utf-8-sig") == before["firmware_updater.py"].decode(
        "utf-8-sig").replace('UPGRADER_VERSION = "5.3.25"', 'UPGRADER_VERSION = "5.3.26"', 1)
    previous_test = before["test_calibration_trajectory_monitor.py"].decode("utf-8-sig")
    assert previous_test.count('"5.3.25-calibration-trace"') == 1
    assert after["test_calibration_trajectory_monitor.py"].decode("utf-8-sig") == previous_test.replace(
        '"5.3.25-calibration-trace"', "main.MONITOR_VERSION", 1)
    old_monitor, new_monitor = methods(before["main.py"]), methods(after["main.py"])
    assert set(old_monitor) == set(new_monitor)
    for name, node in old_monitor.items():
        if name not in {"Monitor.__init__", "Monitor._build_ui_v524"}:
            assert ast.dump(node) == ast.dump(new_monitor[name]), name
    old_dialog, new_dialog = methods(before["calibration_acquisition.py"]), methods(after["calibration_acquisition.py"])
    for name in ("_write_headers", "feed_frame", "_complete_capture", "pause_for_stream_gap",
                 "resume_after_stream_gap", "_begin_capture", "_previous", "_skip"):
        key = "CalibrationAcquisitionDialog." + name
        assert ast.dump(old_dialog[key]) == ast.dump(new_dialog[key]), key
    for name in ("CAPTURE_SECONDS", "MIN_CAPTURE_FRAMES"):
        assert assigned_constant(before["calibration_acquisition.py"], name) == assigned_constant(
            after["calibration_acquisition.py"], name)
    old_writer, new_writer = methods(before["calibration_trajectory.py"]), methods(after["calibration_trajectory.py"])
    assert set(old_writer) == set(new_writer)
    for name in old_writer:
        assert ast.dump(old_writer[name]) == ast.dump(new_writer[name]), name
    for name in ("SENSOR_COLUMNS", "EVENT_COLUMNS", "RAW_COLUMNS", "DELTA_COLUMNS"):
        assert assigned_ast(before["calibration_trajectory.py"], name) == assigned_ast(after["calibration_trajectory.py"], name), name
    protocol = after["zp500n_protocol.py"]
    for name, value in (("BAUD_RATE", 2400), ("DATA_BITS", 8), ("PARITY", "N"), ("STOP_BITS", 1),
                        ("IDENTITY_QUERY", b"\xfa"), ("START_NUMERIC_STREAM", b"\x25"),
                        ("RECEIVE_IDLE_S", .06), ("NUMERIC_RECEIVE_WINDOW_S", .06)):
        assert assigned_constant(protocol, name) == value, name
    imports = [node for node in ast.parse(protocol.decode("utf-8-sig")).body if isinstance(node, (ast.Import, ast.ImportFrom))]
    module_names = {name.name.split(".")[0] for node in imports if isinstance(node, ast.Import) for name in node.names}
    module_names |= {node.module.split(".")[0] for node in imports if isinstance(node, ast.ImportFrom)}
    assert module_names <= {"__future__", "collections", "dataclasses", "math", "re"}, module_names
    protocol_methods = methods(protocol)
    advance = ast.unparse(protocol_methods["ZP500NProtocol.advance"])
    assert "now - self._first_data_at < NUMERIC_RECEIVE_WINDOW_S" in advance
    assert "now - self._last_data_at < self.receive_idle" in advance
    feed = protocol_methods["ZP500NProtocol.feed"]
    advance_calls = [node for node in ast.walk(feed) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and node.func.attr == "advance"]
    empty_branch, = [node for node in feed.body if isinstance(node, ast.If)
                    and ast.dump(node.test) == ast.dump(ast.parse("not data", mode="eval").body)]
    assert len(advance_calls) == 1 and advance_calls[0] in list(ast.walk(empty_branch)), (
        "A data-bearing receive must not invent an idle boundary from a host scheduling gap")
    queued = [node.args[0].id for method in protocol_methods.values() for node in ast.walk(method)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append"
              and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "_writes"
              and len(node.args) == 1 and isinstance(node.args[0], ast.Name)]
    assert sorted(queued) == ["IDENTITY_QUERY", "START_NUMERIC_STREAM"]
    assert assigned_constant(protocol, "UNIT_SCALES") == {"N": 1.0, "kgf": 9.80665, "lbf": 4.4482216152605}
    ascii_reader = new_dialog["GaugeReader._run_ascii"]
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write"
                   for node in ast.walk(ascii_reader)), "Generic ASCII must never transmit to the instrument"
    zp_reader = ast.unparse(new_dialog["GaugeReader._run_zp500n"])
    assert "ready_at = opened_at + 0.05" in zp_reader
    assert "time.sleep(min(0.01, remaining))" in zp_reader
    reader_body = new_dialog["GaugeReader._run_zp500n"].body
    cancelled, = [node for node in reader_body if isinstance(node, ast.If)
                   and ast.dump(node.test) == ast.dump(ast.parse("not self.running", mode="eval").body)]
    assert len(cancelled.body) == 1 and isinstance(cancelled.body[0], ast.Return)
    protocol_start, = [node for node in reader_body if isinstance(node, ast.Expr)
                       and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                       and node.value.func.attr == "start"]
    assert reader_body.index(cancelled) < reader_body.index(protocol_start)
    assert "command not in (b'\\xfa', b'%')" in zp_reader
    assert "gauge.write(command) != len(command)" in zp_reader
    assert "gauge.readline" not in zp_reader
    reader_run = ast.unparse(new_dialog["GaugeReader.run"])
    assert "opened_at = time.monotonic()" in reader_run and "self._run_zp500n(gauge, opened_at)" in reader_run
    run_ast = new_dialog["GaugeReader.run"]
    unopened, = [node for node in ast.walk(run_ast) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "Serial"
                 and any(item.arg == "port" and isinstance(item.value, ast.Constant) and item.value.value is None
                         for item in node.keywords)]
    opened, = [node for node in ast.walk(run_ast) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr == "open"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "gauge"]
    for control in ("dtr", "rts"):
        setting, = [node for node in ast.walk(run_ast) if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Attribute) and target.attr == control
                            and isinstance(target.value, ast.Name) and target.value.id == "gauge"
                            for target in node.targets)]
        assert isinstance(setting.value, ast.Constant) and setting.value.value is False
        assert unopened.lineno < setting.lineno < opened.lineno, control
    port_assignment, = [node for node in ast.walk(run_ast) if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Attribute) and target.attr == "port"
                                and isinstance(target.value, ast.Name) and target.value.id == "gauge"
                                for target in node.targets)]
    assert unopened.lineno < port_assignment.lineno < opened.lineno
    first_try, = [node for node in run_ast.body if isinstance(node, ast.Try) and node.handlers
                   and any(isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
                           and item.func.attr == "Serial"
                           for item in ast.walk(ast.Module(body=node.body, type_ignores=[])))]
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "close"
               for handler in first_try.handlers for node in ast.walk(handler)), "Open/configuration failures must close the object"
    for setting in ("'write_timeout': 0.5", "'bytesize': serial.EIGHTBITS", "'parity': serial.PARITY_NONE",
                    "'stopbits': serial.STOPBITS_ONE", "'xonxoff': False", "'rtscts': False", "'dsrdtr': False"):
        assert setting in reader_run, setting
    reset = ast.unparse(new_dialog["CalibrationAcquisitionDialog._reset_gauge_reference"])
    assert "self.gauge_history.clear()" in reset and "self.gauge_connection_epoch += 1" in reset
    assert any(isinstance(node, ast.Attribute) and node.attr == "_accept_gauge_sender"
               for node in ast.walk(new_dialog["CalibrationAcquisitionDialog._on_gauge"]))
    gauge_columns = after["calibration_trajectory.py"].decode("utf-8-sig")
    for field in ("source_unit", "original_value", "raw_bytes_hex", "protocol_metadata_json", "gauge_connection_epoch"):
        assert field in gauge_columns, field


def main():
    manifest = json.loads((REPORT / "manifest.json").read_text(encoding="utf-8"))
    baseline_manifest = json.loads((ROOT / "outputs/calibration_trace_20261020/manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION and baseline_manifest["version"] == "5.3.25-calibration-trace"
    assert Path(manifest["exe"]).resolve() == EXE.resolve()
    assert manifest["standalone_exe"] is True and manifest["zip_created"] is False
    assert {path.name for path in OUT.iterdir()} == {EXE.name}
    assert not OUT.with_suffix(".zip").exists()
    assert EXE.stat().st_size == manifest["exe_bytes"] and sha(EXE.read_bytes()) == manifest["exe_sha256"]
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
    embedded_names = {"calibration_acquisition.py", "calibration_trajectory.py", "zp500n_protocol.py", "firmware_updater.py",
                      "release_baseline.py", "residual_zero.py", "recording_report.py", "folder_report.py",
                      "force_trace.py", "baseline_quality.py", *UNCHANGED_MATH, *UNCHANGED_TRANSPORT}
    for name in sorted(embedded_names):
        embedded[name] = pyz.extract(name.removesuffix(".py"))
    for name, code in embedded.items():
        assert code_signature(code) == code_signature(compile(after[name], name, "exec", dont_inherit=True, optimize=0)), name
    assert stored_constant(embedded["main.py"], "MONITOR_VERSION") == VERSION
    assert stored_constant(embedded["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.26"
    for name, value in (("BAUD_RATE", 2400), ("IDENTITY_QUERY", b"\xfa"), ("START_NUMERIC_STREAM", b"\x25"),
                        ("RECEIVE_IDLE_S", .06), ("NUMERIC_RECEIVE_WINDOW_S", .06)):
        assert stored_constant(embedded["zp500n_protocol.py"], name) == value
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
        embedded_modules_compared=len(embedded), all_other_sources_byte_identical_to_v25=True,
        original_force_model_and_profiles_unchanged=True, model_runtime_and_raw_compensation_unchanged=True,
        wch_drivers_and_dlls_unchanged=True, iap_protocols_and_firmware_unchanged=True,
        sensor_calibration_recovery_unchanged=True, plateau_csv_schema_unchanged=True,
        zp500n_commands_whitelisted_as_fa_25=True, generic_ascii_transmits_no_commands=True,
        zp500n_post_open_delay_ms=50, stopped_during_open_delay_sends_no_command=True,
        zp500n_dtr_and_rts_false_before_port_open=True, open_failure_cleanup_present=True,
        metadata_last_byte_idle_seconds=.06, numeric_first_byte_collection_window_seconds=.06,
        numeric_submission_requires_observed_empty_read=True,
        gauge_unit_confirmation_required=True, gauge_reconnect_clears_reference_history=True,
        raw_gauge_unit_conversion_and_metadata_audited=True,
        offline_tests_run=int(tests_run), offline_tests_skipped=8, offline_test_modules=len(manifest["tests"]),
        firmware_state_injection_cases=3, startup_exit_code=0, startup_offscreen_passed=True,
        startup_witness=str(startup_path), hardware_connected=False,
        framing_evidence="vendor static code plus synthetic tests; real packet capture pending",
        field_validation="pending")
    (REPORT / "release_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
