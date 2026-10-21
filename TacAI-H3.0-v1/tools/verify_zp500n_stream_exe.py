"""Independently verify v28 stream framing and its standalone EXE offline.

The field log proves one constant value and descriptor/packet grammar. It does
not constitute a varying-load or zero-load hardware acceptance test.
"""
from __future__ import annotations

import ast
import copy
import json
import marshal
import os
from pathlib import Path
import re
import runpy
import subprocess

from PyInstaller.archive.readers import CArchiveReader
from verify_raw_baseline_exe import (
    BINARY_HASHES, PROFILES, UNCHANGED_MATH, UNCHANGED_TRANSPORT,
    assigned_constant, code_signature, methods, sha, stored_constant,
)
from verify_spi_ack_guard_exe import files


ROOT = Path(__file__).resolve().parents[1]
BASE_SOURCE = ROOT / "tmp/spi_ack_guard_20261020_build/source"
SOURCE = ROOT / "tmp/zp500n_stream_20261020_build/source"
OUT = ROOT / "release/TacAI_H3_v5_3_28_ZP500NStream_20261020"
REPORT = ROOT / "outputs/zp500n_stream_20261020"
EXE = OUT / "TacAI H3 v5.3.28 ZP500N Stream.exe"
VERSION = "5.3.28-zp500n-stream"
CHANGED = {"main.py", "firmware_updater.py", "zp500n_protocol.py", "test_zp500n_protocol.py",
           "calibration_acquisition.py", "test_zp500n_gauge.py"}
OPTIONAL_ADDED = {"test_zp500n_live_replay.py", "test_zp500n_backlog.py"}
FIELD_CAPTURE = Path(r"C:\Users\yoku\.codex\attachments\3c46e543-dce5-41d5-8e33-9d943b52c089\已粘贴的文本.txt")
FIELD_CAPTURE_SHA256 = "a13677835af04af51ddb66353653cbd19ee853dd9ff3038bcd3602d0ac2284e7"


def normalized(data):
    return data.decode("utf-8-sig").replace("\r\n", "\n")


def integration_checks(before, after):
    assert assigned_constant(after["main.py"], "MONITOR_VERSION") == VERSION
    assert assigned_constant(after["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.28"
    assert normalized(after["firmware_updater.py"]) == normalized(before["firmware_updater.py"]).replace(
        'UPGRADER_VERSION = "5.3.27"', 'UPGRADER_VERSION = "5.3.28"', 1)
    old_main, new_main = methods(before["main.py"]), methods(after["main.py"])
    assert set(old_main) == set(new_main)
    for name in old_main:
        if name not in {"Monitor.__init__", "Monitor._build_ui_v524"}:
            assert ast.dump(old_main[name]) == ast.dump(new_main[name]), name
    expected_main = normalized(before["main.py"])
    for old, new in (
        ('MONITOR_VERSION = "5.3.27-spi-ack-guard"', 'MONITOR_VERSION = "5.3.28-zp500n-stream"'),
        ('TacAI H3 v5.3.27 SPI ACK Guard', 'TacAI H3 v5.3.28 ZP500N Stream'),
        ('v5.3.27 SPI 应答确认增强 · UART / CH341 / CH347 · 原值追溯',
         'v5.3.28 ZP-500N 连续数据适配 · UART / CH341 / CH347 · 原值追溯'),
    ):
        assert expected_main.count(old) == 1, old
        expected_main = expected_main.replace(old, new, 1)
    assert normalized(after["main.py"]) == expected_main
    # SPI guard and every transport/upgrade method remain the accepted v27 code.
    for name in (*UNCHANGED_TRANSPORT, "calibration_trajectory.py", "test_spi_snapshot_ack_guard.py"):
        assert before[name] == after[name], name
    old_dialog, new_dialog = methods(before["calibration_acquisition.py"]), methods(after["calibration_acquisition.py"])
    for name in ("_write_headers", "_complete_capture", "pause_for_stream_gap", "resume_after_stream_gap"):
        key = "CalibrationAcquisitionDialog." + name
        assert ast.dump(old_dialog[key]) == ast.dump(new_dialog[key]), key
    for name in ("CAPTURE_SECONDS", "MIN_CAPTURE_FRAMES"):
        assert assigned_constant(before["calibration_acquisition.py"], name) == assigned_constant(
            after["calibration_acquisition.py"], name), name
    protocol = after["zp500n_protocol.py"]
    for name, value in (("BAUD_RATE", 2400), ("DATA_BITS", 8), ("PARITY", "N"), ("STOP_BITS", 1),
                        ("IDENTITY_QUERY", b"\xfa"), ("START_NUMERIC_STREAM", b"\x25"),
                        ("DESCRIPTOR_BYTES", 36), ("DESCRIPTOR_REPEATS", 3), ("NUMERIC_FIELD_BYTES", 6),
                        ("DESCRIPTOR_DECIMAL_OFFSETS", (15, 21, 27, 34))):
        assert assigned_constant(protocol, name) == value, name
    protocol_methods = methods(protocol)
    queued = [node.args[0].id for method in protocol_methods.values() for node in ast.walk(method)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "append" and isinstance(node.func.value, ast.Attribute)
              and node.func.value.attr == "_writes"]
    assert sorted(queued) == ["IDENTITY_QUERY", "START_NUMERIC_STREAM"]
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in {"sleep", "readline", "read", "write"}
                   for node in ast.walk(ast.parse(protocol.decode("utf-8-sig"))))
    permitted_methods = {
        "GaugeReader._run_zp500n", "GaugeReader.run", "CalibrationAcquisitionDialog._toggle_gauge",
        "CalibrationAcquisitionDialog._apply_gauge_controls", "CalibrationAcquisitionDialog._on_gauge",
    }
    added_method = "CalibrationAcquisitionDialog._on_gauge_reference_invalidated"
    assert set(new_dialog) == set(old_dialog) | {added_method}
    for name in old_dialog:
        if name not in permitted_methods:
            assert ast.dump(old_dialog[name]) == ast.dump(new_dialog[name]), name
    # The open/close/settings code is unchanged; only its old premature success
    # message is now a ZP-open/info message until a valid measurement arrives.
    run = copy.deepcopy(new_dialog["GaugeReader.run"])
    changed_status, = [node for node in run.body if isinstance(node, ast.If)
                       and isinstance(node.test, ast.Compare)
                       and ast.unparse(node.test) == "self.protocol == 'zp500n'"
                       and len(node.body) == 1 and isinstance(node.body[0], ast.Expr)]
    assert ast.unparse(changed_status.body[0]) == (
        "self.status.emit(f'测力计串口已打开 {self.port} @ {self.baud}，正在识别数据格式', 'info')")
    assert len(changed_status.orelse) == 1
    status_index = run.body.index(changed_status)
    run.body[status_index:status_index+1] = changed_status.orelse
    assert ast.dump(run) == ast.dump(old_dialog["GaugeReader.run"]), (
        "DTR/RTS-before-open, ASCII constructor and open failure cleanup changed")
    toggle = copy.deepcopy(new_dialog["CalibrationAcquisitionDialog._toggle_gauge"])
    connection, = [node for node in toggle.body if isinstance(node, ast.Expr)
                    and ast.unparse(node) == "self.gauge_reader.reference_invalidated.connect(self._on_gauge_reference_invalidated)"]
    toggle.body.remove(connection)
    assert ast.dump(toggle) == ast.dump(old_dialog["CalibrationAcquisitionDialog._toggle_gauge"])
    controls = ast.unparse(old_dialog["CalibrationAcquisitionDialog._apply_gauge_controls"])
    assert ast.unparse(new_dialog["CalibrationAcquisitionDialog._apply_gauge_controls"]) == controls.replace(
        "ZP-500N 按读取的仪器单位自动换算为N，此手动倍率不参与计算。",
        "ZP-500N 读取本次已验证的N单位格式；其他单位或格式尚待确认，手动倍率不参与计算。", 1)
    on_gauge = copy.deepcopy(new_dialog["CalibrationAcquisitionDialog._on_gauge"])
    usable_checks = []
    for node in ast.walk(on_gauge):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            for item in list(node.values):
                if ast.unparse(item) == "context.get('reference_usable') is False":
                    usable_checks.append(item); node.values.remove(item)
    assert len(usable_checks) == 1
    assert ast.dump(on_gauge) == ast.dump(old_dialog["CalibrationAcquisitionDialog._on_gauge"])
    assert ast.dump(old_dialog["CalibrationAcquisitionDialog._gauge_reference"]) == ast.dump(
        new_dialog["CalibrationAcquisitionDialog._gauge_reference"]), "Past-only / 1 second reference policy changed"


def runtime_protocol_checks():
    """Replay the actual raw bytes independently of the product's test fixtures."""
    raw = FIELD_CAPTURE.read_bytes()
    assert sha(raw) == FIELD_CAPTURE_SHA256
    namespace = runpy.run_path(str(SOURCE / "zp500n_protocol.py"))
    protocol_class, protocol_error = namespace["ZP500NProtocol"], namespace["ZPProtocolError"]
    chunks = []
    for match in re.finditer(r"RX_CHUNK t_ms=([\d.]+) phase=(\w+) len=(\d+) HEX=([0-9A-F-]+) TXT=(.*)",
                             raw.decode("utf-8-sig")):
        data = bytes.fromhex(match[4].replace("-", " "))
        assert len(data) == int(match[3]) and data.decode("ascii") == match[5].strip("\r")
        chunks.append((float(match[1])/1000, match[2], data))
    descriptor = b"130271400760500.00000.00500.030980.0"
    protocol = protocol_class()
    protocol.start(1.1145)
    commands = protocol.drain_writes()
    received = []
    for timestamp, phase, data in chunks:
        if phase not in {"AFTER_FA", "AFTER_25"}:
            continue
        samples = protocol.feed(data, timestamp, received_at=timestamp+1000)
        commands.extend(protocol.drain_writes())
        if phase == "AFTER_FA":
            assert samples == [], "Descriptor bytes were promoted to numeric references"
        received.extend(samples)
    assert commands == [b"\xfa", b"\x25"]
    assert protocol.metadata_raw == descriptor and protocol.unit == "N"
    assert len(received) == 149 and all(item.raw == b"0016.1" and item.force_n == 16.1 for item in received)
    assert protocol.pending_numeric_bytes == 0

    def start_aligned(extra=b""):
        instance = protocol_class()
        instance.start(0)
        assert instance.drain_writes() == [b"\xfa"]
        assert instance.feed(descriptor*3+extra, .1) == []
        assert instance.drain_writes() == [b"\x25"]
        return instance

    # Every split of the remaining descriptor tail and a numeric boundary.
    packet = descriptor[10:]+b"0000.00500.0"
    for split in range(1, len(packet)):
        instance = start_aligned(descriptor[:10])
        samples = instance.feed(packet[:split], .2, received_at=100)
        samples += instance.feed(packet[split:], .3, received_at=101)
        assert [item.force_n for item in samples] == [0, 500]
        assert instance.pending_numeric_bytes == 0
    # No numeric 0500.0 may be mistaken for the 500.0 substring in a descriptor.
    instance = start_aligned()
    assert [item.force_n for item in instance.feed(b"0500.00016.1", .2)] == [500, 16.1]
    # Unsupported units and malformed fields stop rather than guess a phase,
    # convert an unproven unit, or extract a conveniently placed final number.
    for code in b"124579":
        altered = bytearray(descriptor); altered[10] = code
        instance = protocol_class(); instance.start(0); instance.drain_writes()
        try:
            instance.feed(bytes(altered)*3, .1)
        except protocol_error:
            pass
        else:
            raise AssertionError("Unsupported metadata unit accepted")
        assert instance.drain_writes() == []
    for numeric in (b"0500.1", b"9999.9", b"-016.1", b"+016.1", b"1e00.0", b"0016,1"):
        instance = start_aligned()
        try:
            instance.feed(numeric, .2)
        except protocol_error:
            pass
        else:
            raise AssertionError("Unsupported numeric grammar or range accepted: " + repr(numeric))
    for repeats in (1, 2):
        instance = protocol_class(); instance.start(0); instance.drain_writes()
        assert instance.feed(descriptor*repeats, .1) == [] and instance.drain_writes() == []
        assert instance.advance(.2) == [] and instance.drain_writes() == []
    return dict(field_capture_sha256=FIELD_CAPTURE_SHA256, replayed_numeric_frames=149,
                replayed_distinct_value_n=[16.1], replayed_commands_hex=["FA", "25"],
                all_transition_split_boundaries_passed=True, unsupported_units_and_numeric_formats_rejected=True,
                repeated_descriptor_requirement_and_numeric_500_boundary_checked=True)


def main():
    manifest = json.loads((REPORT / "manifest.json").read_text(encoding="utf-8"))
    baseline_manifest = json.loads((ROOT / "outputs/spi_ack_guard_20261020/manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION and baseline_manifest["version"] == "5.3.27-spi-ack-guard"
    assert Path(manifest["exe"]).resolve() == EXE.resolve()
    assert manifest["standalone_exe"] is True and manifest["zip_created"] is False
    assert {path.name for path in OUT.iterdir()} == {EXE.name} and not OUT.with_suffix(".zip").exists()
    assert EXE.stat().st_size == manifest["exe_bytes"] and sha(EXE.read_bytes()) == manifest["exe_sha256"]
    assert sha(Path(baseline_manifest["exe"]).read_bytes()) == baseline_manifest["exe_sha256"]
    before, after = files(BASE_SOURCE), files(SOURCE)
    assert {name: sha(data) for name, data in before.items()} == baseline_manifest["source_sha256"]
    assert {name: sha(data) for name, data in after.items()} == manifest["source_sha256"]
    added = set(after) - set(before)
    assert added <= OPTIONAL_ADDED and not set(before) - set(after)
    assert {name for name in before if before[name] != after[name]} == CHANGED
    assert set(manifest["changed_sources"]) == CHANGED and set(manifest["added_sources"]) == added
    assert set(manifest["tests"]) == set(baseline_manifest["tests"]) | {
        "test_zp500n_live_replay", "test_zp500n_backlog"}
    unchanged = {*UNCHANGED_MATH, *UNCHANGED_TRANSPORT, "baseline_quality.py", "release_baseline.py", "residual_zero.py",
                 "recording_report.py", "folder_report.py", "force_trace.py", "calibration_trajectory.py", *PROFILES}
    for name in unchanged:
        assert before[name] == after[name], name
    for name, expected in BINARY_HASHES.items():
        assert before[name] == after[name] and sha(after[name]) == expected, name
    integration_checks(before, after)
    runtime_checks = runtime_protocol_checks()
    source_review = json.loads((REPORT / "source_review.json").read_text(encoding="utf-8"))
    assert source_review["result"] == "passed" and source_review["strict_frozen_delta_verified"] is True
    assert source_review["independent_tests_run"] == 47 and source_review["independent_tests_failed"] == 0
    for name, expected in source_review["source_sha256"].items():
        assert sha(after[name]) == expected, name
    field_audit = json.loads((REPORT / "fieldlog_audit.json").read_text(encoding="utf-8"))
    assert field_audit["sha256"] == FIELD_CAPTURE_SHA256
    assert field_audit["descriptor"]["raw_ascii"] == "130271400760500.00000.00500.030980.0"
    assert field_audit["numeric"]["sample_count_after25"] == 149
    archive = CArchiveReader(str(EXE))
    pyz_name, = (name for name, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    embedded = {"main.py": marshal.loads(archive.extract("main"))}
    embedded_names = {name for name in unchanged if name.endswith(".py")} | {
        "zp500n_protocol.py", "calibration_acquisition.py", "firmware_updater.py"}
    for name in sorted(embedded_names):
        embedded[name] = pyz.extract(name.removesuffix(".py"))
    for name, code in embedded.items():
        assert code_signature(code) == code_signature(compile(after[name], name, "exec", dont_inherit=True, optimize=0)), name
    assert stored_constant(embedded["main.py"], "MONITOR_VERSION") == VERSION
    assert stored_constant(embedded["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.28"
    for name, value in (("BAUD_RATE", 2400), ("IDENTITY_QUERY", b"\xfa"), ("START_NUMERIC_STREAM", b"\x25")):
        assert stored_constant(embedded["zp500n_protocol.py"], name) == value, name
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
    assert "PASS: 7 production RXIRQ/mailbox/snapshot ACK-drain guard cases" in c_log
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
    assert startup_result["automatic_compensation_enabled"] is True and not startup_result["parameter_load_error"]
    result = dict(result="passed", version=VERSION, exe=str(EXE), exe_sha256=manifest["exe_sha256"], exe_bytes=manifest["exe_bytes"],
        standalone_exe_only=True, zip_created=False, frozen_sources_match_manifest=True,
        embedded_code_matches_frozen_source=True, embedded_modules_compared=len(embedded),
        all_other_sources_byte_identical_to_v27=True, force_models_raw_baseline_and_profiles_unchanged=True,
        sensor_transports_iap_spi_ack_guard_dlls_and_firmware_unchanged=True, plateau_csv_schema_unchanged=True,
        independent_runtime_protocol_checks=runtime_checks,
        independent_source_tests_run=47, backlog_suppression_and_session_retention_reviewed=True,
        offline_tests_run=int(tests_run), offline_tests_skipped=8, offline_test_modules=len(manifest["tests"]),
        firmware_state_injection_tests=2, firmware_state_injection_cases=10,
        startup_exit_code=0, startup_offscreen_passed=True, startup_witness=str(startup_path), hardware_connected=False,
        field_packet_evidence=str(REPORT / "fieldlog_audit.json"),
        field_validation="Constant 16.1 N packet capture only; varying loads and zero remain unverified on hardware")
    (REPORT / "release_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
