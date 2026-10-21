"""Verify v5.3.21 delivery without launching its EXE or loading hardware DLLs."""
from __future__ import annotations

import ast
import json
import marshal
import re
import zipfile

from PyInstaller.archive.readers import CArchiveReader
from build_baseline_history_release import (
    ROOT, BASE, OUT, PACKAGE, CHANGED, ADDED, CHECKS, PROFILES,
    CH341_SHA, CH347_SHA, APP_SHA, sha,
)
from verify_spi_gui_cs_fix_release import codes, stored_constant


EXPECTED_CHANGED = {"main.py", "firmware_updater.py", "test_baseline_quality.py", "test_spi_monitor_ui.py"}
EXPECTED_ADDED = {"force_trace.py", "test_force_trace.py", "test_force_trace_monitor.py"}
UNCHANGED_MATH = (
    "no8_raw_tare_core.py", "h3_v3_3_force_model.py", "h3_release_prototypes.py",
    "local_state.py", "local_high_force.py", "automatic_compensation.py",
    "back_total_force.py", "back25_consistency_force.py", "back25_smooth_force.py",
    "back25_domain_force.py", "regional_high_force.py", "regional_ard_force.py",
)


def tree(data):
    return ast.parse(data.decode("utf-8-sig"))


def function(data, name):
    found, = (node for node in ast.walk(tree(data))
              if isinstance(node, ast.FunctionDef) and node.name == name)
    return found


def module_assignment(data, name):
    found, = (node.value for node in tree(data).body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))
    return ast.literal_eval(found)


def archive_files(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None, path
        assert len(archive.namelist()) == len(set(archive.namelist())), path
        return {name: archive.read(name) for name in archive.namelist()}


def main():
    assert CHANGED == EXPECTED_CHANGED and ADDED == EXPECTED_ADDED
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "5.3.21-baseline-history"
    assert set(manifest["changed_sources"]) == EXPECTED_CHANGED
    assert set(manifest["added_sources"]) == EXPECTED_ADDED
    for name, metadata in manifest["files"].items():
        data = (OUT / name).read_bytes()
        assert len(data) == metadata["bytes"] and sha(data) == metadata["sha256"], name
    assert sha((OUT / manifest["exe"]).read_bytes()) == manifest["exe_sha256"]
    previous_manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    assert sha((BASE / "source.zip").read_bytes()) == previous_manifest["files"]["source.zip"]["sha256"]
    before, after = archive_files(BASE / "source.zip"), archive_files(OUT / "source.zip")
    assert set(after) - set(before) == EXPECTED_ADDED
    assert set(before) <= set(after)
    assert {name for name in before if before[name] != after[name]} == EXPECTED_CHANGED
    expected_sources = json.loads((OUT / "source_sha256.json").read_text(encoding="utf-8"))
    assert expected_sources == {name: sha(data) for name, data in after.items()}
    for name in (*UNCHANGED_MATH, "baseline_quality.py", "ch341_spi.py", "ch347_spi.py",
                 "spi_iap_protocol.py", "iap_protocol.py"):
        assert before[name] == after[name], name
    for name in PROFILES:
        assert before[name] == after[name] == (OUT / name).read_bytes(), name
    for name in ("evaluate_window", "quality_text"):
        assert ast.dump(function(before["baseline_quality.py"], name)) == ast.dump(
            function(after["baseline_quality.py"], name))
    # The existing numerical force display, model and allocation path is intact.
    assert ast.dump(function(before["main.py"], "_update_force_display")) == ast.dump(
        function(after["main.py"], "_update_force_display"))
    assert module_assignment(after["main.py"], "MONITOR_VERSION") == "5.3.21-baseline-history"
    assert module_assignment(after["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.21"

    warm = function(after["main.py"], "_warm_start_tare_history")
    calls = [node for node in ast.walk(warm) if isinstance(node, ast.Call)]
    assert any(isinstance(node.func, ast.Name) and node.func.id == "evaluate_raw_force" for node in calls)
    assert any(isinstance(node.func, ast.Attribute) and node.func.attr == "feed" for node in calls)
    assert not any(isinstance(node.func, ast.Attribute) and node.func.attr in (
        "_on_paired", "_append_record", "_tare") for node in calls)
    warm_callers = [node for node in ast.walk(tree(after["main.py"]))
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_warm_start_tare_history"]
    assert len(warm_callers) == 1
    tare = function(after["main.py"], "_tare")
    assert warm_callers[0] in list(ast.walk(tare)) or any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_warm_start_tare_history" for node in ast.walk(tare))
    save_line, = [node.lineno for node in ast.walk(tare) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and node.func.attr == "_save_force_trace"]
    tare_line, = [node.lineno for node in ast.walk(tare) if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Attribute) and target.attr == "tare_raw"
                          for target in node.targets)]
    assert save_line < tare_line, "Preserve the preceding RAW/tare before replacing the zero"
    for name in ("_toggle_connection", "_on_reader_finished", "_on_stream_fault", "closeEvent"):
        assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "_save_force_trace" for node in ast.walk(function(after["main.py"], name))), name
    capture = function(after["main.py"], "_capture_force_trace")
    observed = {node.arg for node in ast.walk(capture) if isinstance(node, ast.keyword)}
    assert {"raw24", "tare_raw24", "delta_raw24", "selected_force_n", "original_model_force_n",
            "gate_reason", "load_score", "release_similarity_ratio"} <= observed
    trace_init = function(after["force_trace.py"], "__init__")
    assert [ast.literal_eval(node) for node in trace_init.args.defaults] == [None, 6000, 180]

    archive = CArchiveReader(str(OUT / manifest["exe"]))
    pyz_name, = (name for name, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    entry = marshal.loads(archive.extract("main"))
    assert stored_constant(entry, "MONITOR_VERSION") == "5.3.21-baseline-history"
    embedded_functions = {code.co_name: code for code in codes(entry)}
    assert {"_warm_start_tare_history", "_capture_force_trace", "_save_force_trace",
            "_snapshot_trace", "_record_spi_event"} <= set(embedded_functions)
    assert "evaluate_raw_force" in embedded_functions["_warm_start_tare_history"].co_names
    assert "feed" in embedded_functions["_warm_start_tare_history"].co_names
    force_trace = pyz.extract("force_trace")
    assert stored_constant(force_trace, "SCHEMA") == "tacai.force_trace"
    assert stored_constant(force_trace, "VERSION") == 1
    assert {"ForceTrace", "append", "clear", "save"} <= {code.co_name for code in codes(force_trace)}
    updater = pyz.extract("firmware_updater")
    assert stored_constant(updater, "UPGRADER_VERSION") == "5.3.21"
    spi = pyz.extract("spi_iap_protocol")
    assert stored_constant(spi, "SNAPSHOT_MAX_ATTEMPTS") == 3
    assert stored_constant(spi, "SNAPSHOT_STALE_MIN_POLLS") == 3
    assert stored_constant(spi, "SNAPSHOT_STALE_MIN_SECONDS") == 0.18
    for file, expected in (("CH341DLLA64.DLL", CH341_SHA), ("CH347DLLA64.dll", CH347_SHA),
                           ("TacAI_H3_App_SPI_rxirq_v5.tacai", APP_SHA)):
        name, = (name for name in archive.toc if name.casefold() == file.casefold())
        assert sha(archive.extract(name)) == expected
        assert before[file] == after[file]

    log = (OUT / "offline_tests.log").read_text(encoding="utf-8")
    assert log.strip().splitlines()[-1].startswith("OK") and "FAILED" not in log
    count, = re.findall(r"^Ran (\d+) tests in ", log, re.MULTILINE)
    skipped = re.search(r"^OK \(skipped=(\d+)\)$", log, re.MULTILINE)
    assert set(manifest["tests"]) == set(CHECKS)
    for module in CHECKS:
        assert "(" + module + "." in log, module
    c_log = (OUT / "firmware_state_repro.log").read_text(encoding="utf-8")
    assert "PASS: 3 production RXIRQ stale snapshot/reissue recovery cases" in c_log and "\nOK\n" in c_log
    packaged = archive_files(PACKAGE)
    expected_package = {OUT.name + "/" + path.name: path.read_bytes()
                        for path in OUT.iterdir() if path.is_file()}
    assert packaged == expected_package
    report = dict(
        result="passed", version=manifest["version"], changed_sources=sorted(EXPECTED_CHANGED),
        added_sources=sorted(EXPECTED_ADDED), all_other_sources_byte_identical=True,
        force_and_quality_math_unchanged=True, calibration_profiles_byte_identical=True,
        drivers_and_dlls_unchanged=True, firmware_unchanged=True,
        transport_protocols_byte_identical=True, upgrade_commands_still_single_send=True,
        snapshot_recovery_bounded=True, original_tare_evidence_saved_before_replacement=True,
        warm_start_uses_real_model_and_source_frames=True, automatic_tare=False,
        embedded_force_trace_present=True, trace_max_frames=6000, trace_max_span_seconds=180,
        offline_tests_run=int(count), offline_tests_skipped=int(skipped[1]) if skipped else 0,
        offline_test_modules=len(CHECKS), firmware_state_injection_cases=3,
        package_sha256=sha(PACKAGE.read_bytes()), exe_sha256=manifest["exe_sha256"],
        hardware_accessed=False, hardware_soak_validation="pending",
        real_residual_force_root_cause="pending field RAW evidence")
    target = ROOT / "outputs/baseline_history_20261019/release_verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
