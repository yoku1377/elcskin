"""Inspect v5.3.22 artifacts without launching the EXE or accessing hardware."""
from __future__ import annotations

import ast
import copy
import json
import marshal
import re
import zipfile

from PyInstaller.archive.readers import CArchiveReader

from build_residual_zero_release import (
    ROOT, BASE, OUT, PACKAGE, CHANGED, ADDED, CHECKS, PROFILES,
    CH341_SHA, CH347_SHA, APP_SHA, sha,
)
from verify_spi_gui_cs_fix_release import codes, stored_constant


EXPECTED_CHANGED = {"main.py", "firmware_updater.py"}
EXPECTED_ADDED = {"residual_zero.py", "test_residual_zero.py", "test_residual_zero_monitor.py"}
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


def named_calls(node, name):
    return [item for item in ast.walk(node) if isinstance(item, ast.Call)
            and ((isinstance(item.func, ast.Name) and item.func.id == name)
                 or (isinstance(item.func, ast.Attribute) and item.func.attr == name))]


def method_map(data):
    result = {}
    for node in tree(data).body:
        if isinstance(node, ast.ClassDef):
            result.update({node.name + "." + method.name: method for method in node.body
                           if isinstance(method, ast.FunctionDef)})
        elif isinstance(node, ast.FunctionDef):
            result[node.name] = node
    return result


def call_statement(node, name):
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == name)


def assignment_name(node, name):
    return (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name)


def strip_call_statement(node, name):
    original = copy.deepcopy(node)
    original.body = [item for item in original.body if not call_statement(item, name)]
    return original


def main():
    assert CHANGED == EXPECTED_CHANGED and ADDED == EXPECTED_ADDED
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "5.3.22-residual-zero"
    assert set(manifest["changed_sources"]) == EXPECTED_CHANGED
    assert set(manifest["added_sources"]) == EXPECTED_ADDED
    for name, metadata in manifest["files"].items():
        data = (OUT / name).read_bytes()
        assert len(data) == metadata["bytes"] and sha(data) == metadata["sha256"], name
    assert sha((OUT / manifest["exe"]).read_bytes()) == manifest["exe_sha256"]
    previous_manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    assert previous_manifest["version"] == "5.3.21-baseline-history"
    assert sha((BASE / "source.zip").read_bytes()) == previous_manifest["files"]["source.zip"]["sha256"]
    before, after = archive_files(BASE / "source.zip"), archive_files(OUT / "source.zip")
    assert set(after) - set(before) == EXPECTED_ADDED
    assert set(before) <= set(after)
    assert {name for name in before if before[name] != after[name]} == EXPECTED_CHANGED
    expected_sources = json.loads((OUT / "source_sha256.json").read_text(encoding="utf-8"))
    assert expected_sources == {name: sha(data) for name, data in after.items()}
    for name in (*UNCHANGED_MATH, "baseline_quality.py", "ch341_spi.py", "ch347_spi.py",
                 "spi_iap_protocol.py", "iap_protocol.py", "force_trace.py"):
        assert before[name] == after[name], name
    for name in PROFILES:
        assert before[name] == after[name] == (OUT / name).read_bytes(), name
    assert module_assignment(after["main.py"], "MONITOR_VERSION") == "5.3.22-residual-zero"
    assert module_assignment(after["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.22"
    assert manifest["residual_zero"]["default_enabled"] is True
    assert manifest["residual_zero"]["max_residual_n"] == 2.0
    assert manifest["residual_zero"]["minimum_peak_n"] == 2.5
    assert manifest["residual_zero"]["maximum_peak_ratio"] == .6
    assert manifest["residual_zero"]["quiet_span_s"] == 1.
    assert manifest["residual_zero"]["partial_unload_may_be_zeroed"] is True
    residual_limits = {"MAX_RESIDUAL_N": 2., "ARM_PEAK_N": 2.5, "DROP_RATIO": .6,
                       "MIN_STABLE_S": 1., "EXIT_RAW_NORM": 350.}
    for name, expected in residual_limits.items():
        assert module_assignment(after["residual_zero.py"], name) == expected, name

    # Version-only updater changes must not alter any UART/SPI erase/write path.
    expected_updater = before["firmware_updater.py"].decode("utf-8-sig").replace(
        'UPGRADER_VERSION = "5.3.21"', 'UPGRADER_VERSION = "5.3.22"', 1)
    assert expected_updater == after["firmware_updater.py"].decode("utf-8-sig")
    # Remaining integration assertions are intentionally against frozen sources.
    verify_integration(before, after)

    archive = CArchiveReader(str(OUT / manifest["exe"]))
    pyz_name, = (name for name, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    entry = marshal.loads(archive.extract("main"))
    assert stored_constant(entry, "MONITOR_VERSION") == "5.3.22-residual-zero"
    embedded_functions = {code.co_name: code for code in codes(entry)}
    assert {"_warm_start_tare_history", "_capture_force_trace", "_save_force_trace",
            "_snapshot_trace", "_record_spi_event"} <= set(embedded_functions)
    residual = pyz.extract("residual_zero")
    assert {"ResidualZeroResult", "ResidualZeroFilter", "feed", "reset"} <= {
        code.co_name for code in codes(residual)}
    for name, expected in residual_limits.items():
        assert stored_constant(residual, name) == expected, name
    updater = pyz.extract("firmware_updater")
    assert stored_constant(updater, "UPGRADER_VERSION") == "5.3.22"
    spi = pyz.extract("spi_iap_protocol")
    assert stored_constant(spi, "SNAPSHOT_MAX_ATTEMPTS") == 3
    assert stored_constant(spi, "SNAPSHOT_STALE_MIN_POLLS") == 3
    assert stored_constant(spi, "SNAPSHOT_STALE_MIN_SECONDS") == 0.18
    for file, expected in (("CH341DLLA64.DLL", CH341_SHA), ("CH347DLLA64.dll", CH347_SHA),
                           ("TacAI_H3_App_SPI_rxirq_v5.tacai", APP_SHA)):
        name, = (name for name in archive.toc if name.casefold() == file.casefold())
        assert sha(archive.extract(name)) == expected
        assert before[file] == after[file]
    assert stored_constant(pyz.extract("force_trace"), "VERSION") == 1

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
    readme = (OUT / "先看这里.txt").read_text(encoding="utf-8-sig")
    assert "部分卸" in readme and "原始RAW" in readme and "不需要重刷固件" in readme
    report = dict(
        result="passed", version=manifest["version"], changed_sources=sorted(EXPECTED_CHANGED),
        added_sources=sorted(EXPECTED_ADDED), all_other_sources_byte_identical=True,
        original_force_model_and_thresholds_unchanged=True, selected_force_postprocessing_changed=True,
        calibration_profiles_byte_identical=True, drivers_and_dlls_unchanged=True,
        firmware_unchanged=True, transport_protocols_byte_identical=True,
        upgrade_commands_still_single_send=True, snapshot_recovery_bounded=True,
        original_raw_model_and_tare_preserved=True, tare_history_fix_preserved=True,
        force_trace_module_byte_identical=True, residual_suppression_default_enabled=True,
        residual_maximum_n=2., residual_minimum_peak_n=2.5, residual_maximum_peak_ratio=.6,
        residual_quiet_span_s=1., partial_unload_may_be_zeroed=True,
        no_claim_of_proven_complete_physical_unload=True,
        offline_tests_run=int(count), offline_tests_skipped=int(skipped[1]) if skipped else 0,
        offline_test_modules=len(CHECKS), firmware_state_injection_cases=3,
        package_sha256=sha(PACKAGE.read_bytes()), exe_sha256=manifest["exe_sha256"],
        hardware_accessed=False, field_residual_validation="pending",
        physical_residual_root_cause="unconfirmed; host output suppression per user request")
    target = ROOT / "outputs/residual_zero_20261019/release_verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


def verify_integration(before, after):
    """Reject unintended edits to the underlying force, RAW and tare algorithms."""
    old, new = method_map(before["main.py"]), method_map(after["main.py"])
    changed = {name for name in old if ast.dump(old[name]) != ast.dump(new[name])}
    assert changed == {
        "Monitor.__init__", "Monitor._build_ui_v524", "Monitor._reset_local_force",
        "Monitor._on_local_force_changed", "Monitor._update_force_display",
        "Monitor._capture_force_trace", "Monitor._render", "Monitor._record_base",
        "Monitor._csv_header", "Monitor._save_force_trace",
    }, changed
    assert set(new) - set(old) == {
        "Monitor._apply_residual_zero", "Monitor._current_release_zero_result",
        "Monitor._on_release_zero_changed", "Monitor._release_zero_csv_row",
        "Monitor._release_zero_epoch", "Monitor._reset_release_zero",
    }
    # This checks all original calibration, pairing, RAW, tare, protocol and
    # recording functions, not merely a hand-picked list of numerical methods.
    for name in set(old) - changed:
        assert ast.dump(old[name]) == ast.dump(new[name]), name
    for name in ("_update_force_display", "_reset_local_force", "_on_local_force_changed"):
        helper = "_apply_residual_zero" if name == "_update_force_display" else "_reset_release_zero"
        node = new["Monitor." + name]
        assert len(named_calls(node, helper)) == 1
        assert ast.dump(strip_call_statement(node, helper)) == ast.dump(old["Monitor." + name]), name
    display = new["Monitor._update_force_display"]
    apply_line, = [item.lineno for item in display.body if call_statement(item, "_apply_residual_zero")]
    animation_line, = [item.lineno for item in display.body if assignment_name(item, "animation_total")]
    assert apply_line < animation_line

    integration = new["Monitor._apply_residual_zero"]
    feed, = named_calls(integration, "feed")
    frame_guard, = [item for item in integration.body if isinstance(item, ast.If)
                    and ast.dump(item.test) == ast.dump(ast.parse("paired_frame is not None", mode="eval").body)]
    assert feed in list(ast.walk(frame_guard)), "Redisplay must not feed accepted-frame state"
    key_guard, = [item for item in ast.walk(frame_guard) if isinstance(item, ast.If)
                  and ast.dump(item.test) == ast.dump(ast.parse(
                      "key != self.release_zero_sample_key", mode="eval").body)]
    assert feed in list(ast.walk(key_guard)), "Duplicate accepted frames must not feed twice"
    assert ast.dump(feed.args[0]) == ast.dump(ast.parse("self.raw", mode="eval").body)
    assert ast.dump(feed.args[1]) == ast.dump(ast.parse("self.device_fz", mode="eval").body)
    for item in ast.walk(integration):
        if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                    assert target.attr in {
                        "release_zero_input_force_n", "release_zero_result", "release_zero_sample_key",
                        "device_fz", "numeric_zone_forces", "quality_flags", "force_display_source",
                    }, target.attr
    assert not named_calls(integration, "_tare")
    assert not named_calls(integration, "_invalidate_host_tare")
    initialized = [item.value for item in ast.walk(new["Monitor.__init__"])
                   if isinstance(item, ast.Assign) and any(isinstance(target, ast.Attribute)
                   and target.attr == "release_zero_enabled" for target in item.targets)]
    assert len(initialized) == 1 and ast.literal_eval(initialized[0]) is True
    toggled = new["Monitor._on_release_zero_changed"]
    assert isinstance(toggled.body[0], ast.If)
    assert ast.dump(toggled.body[0].test) == ast.dump(ast.parse(
        "self.recording or self.protocol.active", mode="eval").body)
    assert any(isinstance(item, ast.Return) for item in toggled.body[0].body)
    assert named_calls(toggled.body[0], "QSignalBlocker")
    assert named_calls(toggled.body[0], "setChecked")

    # Preserve each original trace field; only add explicit suppression evidence.
    capture = copy.deepcopy(new["Monitor._capture_force_trace"])
    capture.body = [item for item in capture.body if not assignment_name(item, "release_zero")]
    trace_dict, = [item for item in ast.walk(capture) if isinstance(item, ast.Call)
                   and isinstance(item.func, ast.Name) and item.func.id == "dict"]
    additions = {item.arg for item in trace_dict.keywords if item.arg.startswith("release_zero_")}
    assert additions == {"release_zero_enabled", "release_zero_applied", "release_zero_reason",
                         "release_zero_threshold_n", "release_zero_peak_force_n", "release_zero_input_force_n"}
    trace_dict.keywords = [item for item in trace_dict.keywords if not item.arg.startswith("release_zero_")]
    assert ast.dump(capture) == ast.dump(old["Monitor._capture_force_trace"])
    save = copy.deepcopy(new["Monitor._save_force_trace"])
    metadata, = [item for item in ast.walk(save) if isinstance(item, ast.Call)
                 and isinstance(item.func, ast.Name) and item.func.id == "dict"]
    save_added = {item.arg for item in metadata.keywords if item.arg.startswith("release_zero_")
                  or item.arg == "automatic_zero_scope"}
    assert save_added == {"release_zero_enabled", "release_zero_scope", "automatic_zero_scope"}
    scopes = {item.arg: ast.literal_eval(item.value) for item in metadata.keywords
              if item.arg in {"release_zero_scope", "automatic_zero_scope"}}
    assert scopes == {"automatic_zero_scope": "RAW_TARE", "release_zero_scope": "SELECTED_FORCE_ONLY"}
    metadata.keywords = [item for item in metadata.keywords if item.arg not in save_added]
    assert ast.dump(save) == ast.dump(old["Monitor._save_force_trace"])

    # Numeric export columns keep their original positions; flags are appended.
    record = copy.deepcopy(new["Monitor._record_base"])
    returned, = [item for item in record.body if isinstance(item, ast.Return)]
    assert isinstance(returned.value, ast.BinOp) and isinstance(returned.value.op, ast.Add)
    assert isinstance(returned.value.right, ast.Call)
    assert returned.value.right.func.attr == "_release_zero_csv_row"
    returned.value = returned.value.left
    assert ast.dump(record) == ast.dump(old["Monitor._record_base"])
    header = copy.deepcopy(new["Monitor._csv_header"])
    appended, = [item for item in header.body if isinstance(item, ast.AugAssign)
                 and isinstance(item.value, ast.Name) and item.value.id == "RELEASE_ZERO_CSV_COLUMNS"]
    header.body.remove(appended)
    assert ast.dump(header) == ast.dump(old["Monitor._csv_header"])
    assert module_assignment(after["main.py"], "RELEASE_ZERO_CSV_COLUMNS") == [
        "release_zero_enabled", "release_zero_applied", "release_zero_reason", "release_zero_threshold_n"]


if __name__ == "__main__":
    main()
