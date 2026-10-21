"""Verify the snapshot-only v27 ACK drain and its standalone EXE offline."""
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
BASE_SOURCE = ROOT / "tmp/zp500n_gauge_20261020_build/source"
SOURCE = ROOT / "tmp/spi_ack_guard_20261020_build/source"
OUT = ROOT / "release/TacAI_H3_v5_3_27_SPIAckGuard_20261020"
REPORT = ROOT / "outputs/spi_ack_guard_20261020"
EXE = OUT / "TacAI H3 v5.3.27 SPI ACK Guard.exe"
VERSION = "5.3.27-spi-ack-guard"
CHANGED = {"main.py", "firmware_updater.py", "spi_iap_protocol.py"}
ADDED = {"test_spi_snapshot_ack_guard.py"}


def files(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes() for path in directory.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}


def expression(source):
    return ast.dump(ast.parse(source, mode="eval").body)


class StripAckGuard(ast.NodeTransformer):
    """Remove only the pending-ACK bookkeeping and the one preceding guard."""
    def __init__(self):
        self.assignments = 0
        self.guards = []

    def visit_Assign(self, node):
        if any(isinstance(target, ast.Attribute) and target.attr == "_pending_snapshot_ack"
               and isinstance(target.value, ast.Name) and target.value.id == "self"
               for target in node.targets):
            self.assignments += 1
            return None
        return self.generic_visit(node)

    def visit_If(self, node):
        if ast.dump(node.test) == expression("self._pending_snapshot_ack is not None"):
            self.guards.append(node)
            return None
        return self.generic_visit(node)


def integration_checks(before, after):
    assert assigned_constant(after["main.py"], "MONITOR_VERSION") == VERSION
    assert assigned_constant(after["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.27"
    assert after["firmware_updater.py"].decode("utf-8-sig").replace("\r\n", "\n") == before["firmware_updater.py"].decode(
        "utf-8-sig").replace("\r\n", "\n").replace('UPGRADER_VERSION = "5.3.26"', 'UPGRADER_VERSION = "5.3.27"', 1)
    old, new = methods(before["spi_iap_protocol.py"]), methods(after["spi_iap_protocol.py"])
    assert set(old) == set(new)
    init_strip = StripAckGuard()
    normalized_init = init_strip.visit(copy.deepcopy(new["SPIIapClient.__init__"]))
    assert init_strip.assignments == 1 and not init_strip.guards
    assert ast.dump(normalized_init) == ast.dump(old["SPIIapClient.__init__"])
    snapshot_strip = StripAckGuard()
    normalized_snapshot = snapshot_strip.visit(copy.deepcopy(new["SPIIapClient.snapshot"]))
    assert len(snapshot_strip.guards) == 1 and snapshot_strip.assignments == 1
    assert ast.dump(normalized_snapshot) == ast.dump(old["SPIIapClient.snapshot"]), (
        "Snapshot decoding, bounded recovery and deadlines must stay identical outside the ACK guard")
    for name in old:
        if name not in {"SPIIapClient.__init__", "SPIIapClient.snapshot"}:
            assert ast.dump(old[name]) == ast.dump(new[name]), name
    # In particular, public command, private request/poll, ENTER, BEGIN/DATA/END
    # upgrade delegation and BOOT retain the single-send implementation.
    guard = snapshot_strip.guards[0]
    exchanges = [node for node in ast.walk(guard) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "_exchange"]
    assert len(exchanges) == 1 and len(exchanges[0].args) == 1
    assert ast.dump(exchanges[0].args[0]) == expression("b'\\xff' * SPI_FRAME_BYTES")
    assert not any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(guard))
    assert any(isinstance(node, ast.ExceptHandler) and any(isinstance(item, ast.Raise) for item in node.body)
               for node in ast.walk(guard)), "A guard transport failure must be raised immediately"
    assert "snapshot_ack_guard" in ast.unparse(guard)
    snapshot = new["SPIIapClient.snapshot"]
    guard_index = next(index for index,node in enumerate(snapshot.body) if isinstance(node, ast.If)
                       and ast.dump(node.test) == expression("self._pending_snapshot_ack is not None"))
    deadline_index = next(index for index,node in enumerate(snapshot.body) if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "deadline" for target in node.targets))
    assert deadline_index < guard_index
    assert not any(isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "deadline"
                   for target in node.targets) for node in ast.walk(guard))
    for constant in ("SPI_FRAME_BYTES", "SNAPSHOT_MAX_ATTEMPTS", "SNAPSHOT_STALE_MIN_POLLS", "SNAPSHOT_STALE_MIN_SECONDS"):
        assert assigned_constant(before["spi_iap_protocol.py"], constant) == assigned_constant(after["spi_iap_protocol.py"], constant)

    # The entire monitor is byte-identical after exactly the version, window
    # title, brand text and two fields added to existing event records. No new
    # normal-frame writes or per-snapshot logger calls are allowed here.
    expected_main = before["main.py"].decode("utf-8-sig")
    replacements = (
        ('MONITOR_VERSION = "5.3.26-zp500n-gauge"', 'MONITOR_VERSION = "5.3.27-spi-ack-guard"'),
        ('TacAI H3 v5.3.26 ZP500N Gauge', 'TacAI H3 v5.3.27 SPI ACK Guard'),
        ('v5.3.26 ZP-500N 测力计适配 · UART / CH341 / CH347 · 原值追溯',
         'v5.3.27 SPI 应答确认增强 · UART / CH341 / CH347 · 原值追溯'),
        ('                                              detail=record)',
         '                                              monitor_version=MONITOR_VERSION,\n'
         '                                              snapshot_ack_guard=True,\n'
         '                                              detail=record)'),
    )
    # Decode preserves CRLF, so normalize both strings only for newline style.
    expected_main = expected_main.replace("\r\n", "\n")
    for old, new in replacements:
        assert expected_main.count(old) == 1, old
        expected_main = expected_main.replace(old, new, 1)
    assert after["main.py"].decode("utf-8-sig").replace("\r\n", "\n") == expected_main


def main():
    manifest = json.loads((REPORT / "manifest.json").read_text(encoding="utf-8"))
    baseline_manifest = json.loads((ROOT / "outputs/zp500n_gauge_20261020/manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION and baseline_manifest["version"] == "5.3.26-zp500n-gauge"
    assert Path(manifest["exe"]).resolve() == EXE.resolve()
    assert manifest["standalone_exe"] is True and manifest["zip_created"] is False
    assert {path.name for path in OUT.iterdir()} == {EXE.name} and not OUT.with_suffix(".zip").exists()
    assert EXE.stat().st_size == manifest["exe_bytes"] and sha(EXE.read_bytes()) == manifest["exe_sha256"]
    assert sha(Path(baseline_manifest["exe"]).read_bytes()) == baseline_manifest["exe_sha256"]
    before, after = files(BASE_SOURCE), files(SOURCE)
    assert {name: sha(data) for name, data in before.items()} == baseline_manifest["source_sha256"]
    assert {name: sha(data) for name, data in after.items()} == manifest["source_sha256"]
    assert set(after) == set(before) | ADDED
    assert {name for name in before if before[name] != after[name]} == CHANGED
    assert set(manifest["changed_sources"]) == CHANGED and set(manifest["added_sources"]) == ADDED
    unchanged = {*UNCHANGED_MATH, *UNCHANGED_TRANSPORT, "baseline_quality.py", "release_baseline.py", "residual_zero.py",
                 "recording_report.py", "folder_report.py", "force_trace.py", "calibration_acquisition.py",
                 "calibration_trajectory.py", "zp500n_protocol.py", *PROFILES} - {"spi_iap_protocol.py"}
    for name in unchanged:
        assert before[name] == after[name], name
    for name, expected in BINARY_HASHES.items():
        assert before[name] == after[name] and sha(after[name]) == expected, name
    integration_checks(before, after)
    archive = CArchiveReader(str(EXE))
    pyz_name, = (name for name, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    embedded = {"main.py": marshal.loads(archive.extract("main"))}
    embedded_names = {name for name in unchanged if name.endswith(".py")} | {"spi_iap_protocol.py", "firmware_updater.py"}
    for name in sorted(embedded_names):
        embedded[name] = pyz.extract(name.removesuffix(".py"))
    for name, code in embedded.items():
        assert code_signature(code) == code_signature(compile(after[name], name, "exec", dont_inherit=True, optimize=0)), name
    assert stored_constant(embedded["main.py"], "MONITOR_VERSION") == VERSION
    assert stored_constant(embedded["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.27"
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
    assert re.search(r"^Ran 2 tests in ", c_log, re.MULTILINE)
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
        all_other_sources_byte_identical_to_v26=True, model_raw_calibration_and_gauge_unchanged=True,
        wch_drivers_dlls_and_firmware_unchanged=True, public_iap_and_upgrade_request_methods_unchanged=True,
        strict_response_decoder_unchanged=True, existing_snapshot_recovery_unchanged_outside_ack_guard=True,
        guard_one_dummy_ff_536_transaction_before_next_snapshot=True, guard_counts_against_existing_total_deadline=True,
        guard_transport_or_short_transfer_error_is_fatal=True,
        existing_spi_event_records_include_version_and_guard_mode=True, no_new_normal_frame_logger_calls=True,
        offline_tests_run=int(tests_run), offline_tests_skipped=8, offline_test_modules=len(manifest["tests"]),
        firmware_state_injection_tests=2, firmware_state_injection_cases=10,
        startup_exit_code=0, startup_offscreen_passed=True,
        startup_witness=str(startup_path), hardware_connected=False,
        field_validation="pending; source/state reproduction does not prove the initiating field fault")
    (REPORT / "release_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
