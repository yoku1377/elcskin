"""Inspect the standalone v5.3.23 EXE without launching it or loading DLLs."""
from __future__ import annotations

import ast
import copy
import dis
import hashlib
import json
import marshal
from pathlib import Path
import re
import types
import zipfile

from PyInstaller.archive.readers import CArchiveReader


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "release/TacAI_H3_v5_3_22_ResidualZero_20261019"
OUT = ROOT / "release/TacAI_H3_v5_3_23_RAWBaseline_20261019"
SOURCE = ROOT / "tmp/raw_baseline_20261019_build/source"
REPORT = ROOT / "outputs/raw_baseline_20261019"
EXE = OUT / "TacAI H3 v5.3.23 RAW Baseline.exe"
VERSION = "5.3.23-raw-baseline"
CHANGED = {"main.py", "firmware_updater.py", "residual_zero.py", "recording_report.py",
           "folder_report.py", "test_baseline_quality.py"}
ADDED = {"release_baseline.py", "test_release_baseline.py", "test_release_baseline_monitor.py",
         "test_release_baseline_replay.py"}
PROFILES = ("local_force_profile.json", "back_force_profile.json", "back25_consistency_profile.json",
            "back25_smooth_profile.json", "back25_domain_profile.json")
UNCHANGED_MATH = (
    "no8_raw_tare_core.py", "h3_v3_3_force_model.py", "h3_release_prototypes.py",
    "local_state.py", "local_high_force.py", "automatic_compensation.py",
    "back_total_force.py", "back25_consistency_force.py", "back25_smooth_force.py",
    "back25_domain_force.py", "regional_high_force.py", "regional_ard_force.py",
)
UNCHANGED_TRANSPORT = ("ch341_spi.py", "ch347_spi.py", "spi_iap_protocol.py", "iap_protocol.py")
BINARY_HASHES = {
    "CH341DLLA64.DLL": "0eeed57d53f0ff9b6daaaab87207ff90c2e03762441aba5ed0904b3f7d5f4737",
    "CH347DLLA64.dll": "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375",
    "TacAI_H3_App_SPI_rxirq_v5.tacai": "c5e09fd9a30a4da4916a774da45c7b94ee9ba88f511b7c7a2b04b90b4f944d42",
}
RESIDUAL_LIMITS = {"MAX_RESIDUAL_N": 2., "ARM_PEAK_N": 2.5, "DROP_RATIO": .6,
                   "MIN_STABLE_S": 1., "EXIT_RAW_NORM": 350.}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tree(data):
    return ast.parse(data.decode("utf-8-sig"))


def methods(data):
    result = {}
    for node in tree(data).body:
        if isinstance(node, ast.ClassDef):
            result.update({node.name + "." + method.name: method for method in node.body
                           if isinstance(method, ast.FunctionDef)})
        elif isinstance(node, ast.FunctionDef):
            result[node.name] = node
    return result


def calls(node, name):
    return [item for item in ast.walk(node) if isinstance(item, ast.Call)
            and ((isinstance(item.func, ast.Name) and item.func.id == name)
                 or (isinstance(item.func, ast.Attribute) and item.func.attr == name))]


def same_expression(node, expression):
    return ast.dump(node) == ast.dump(ast.parse(expression, mode="eval").body)


def assigned_constant(data, name):
    value, = [node.value for node in tree(data).body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    return ast.literal_eval(value)


def stored_constant(code, name):
    instructions = list(dis.get_instructions(code))
    result = []
    for index, instruction in enumerate(instructions):
        if instruction.opname in ("STORE_NAME", "STORE_GLOBAL") and instruction.argval == name:
            assert index and instructions[index - 1].opname == "LOAD_CONST", name
            result.append(instructions[index - 1].argval)
    assert len(result) == 1, (name, result)
    return result[0]


def code_signature(value):
    """Compare embedded executable logic with reviewed source, ignoring paths."""
    if isinstance(value, types.CodeType):
        return (value.co_code, tuple(code_signature(item) for item in value.co_consts),
                value.co_names, value.co_varnames, value.co_freevars, value.co_cellvars,
                value.co_argcount, value.co_posonlyargcount, value.co_kwonlyargcount, value.co_flags)
    if isinstance(value, tuple):
        return tuple(code_signature(item) for item in value)
    return value


def archive_files(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None, path
        assert len(archive.namelist()) == len(set(archive.namelist())), path
        return {name: archive.read(name) for name in archive.namelist()}


class StripReferenceEvidence(ast.NodeTransformer):
    """Remove v23 readonly reference accessors; old detector logic must match."""
    def visit_FunctionDef(self, node):
        if node.name in {"reference_raw24", "release_samples"}:
            assert len(node.decorator_list) == 1 and isinstance(node.decorator_list[0], ast.Name)
            assert node.decorator_list[0].id == "property"
            return None
        return self.generic_visit(node)

    def visit_Assign(self, node):
        if any(isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
               and target.value.id == "self" and target.attr == "_release_samples"
               for target in node.targets):
            return None
        return self.generic_visit(node)


def verify_integration(before, after):
    assert assigned_constant(after["main.py"], "MONITOR_VERSION") == VERSION
    assert assigned_constant(after["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.23"
    expected_updater = before["firmware_updater.py"].decode("utf-8-sig").replace(
        'UPGRADER_VERSION = "5.3.22"', 'UPGRADER_VERSION = "5.3.23"', 1)
    assert expected_updater == after["firmware_updater.py"].decode("utf-8-sig")
    for name in (*UNCHANGED_MATH, *UNCHANGED_TRANSPORT, "baseline_quality.py", "force_trace.py", *PROFILES):
        assert before[name] == after[name], name
    # Adding observable copies of a detector reference must not change v22's
    # guarded release recognition, threshold values or duplicate/time handling.
    stripped_detector = StripReferenceEvidence().visit(copy.deepcopy(tree(after["residual_zero.py"])))
    assert ast.dump(stripped_detector) == ast.dump(tree(before["residual_zero.py"]))
    for name, expected in RESIDUAL_LIMITS.items():
        assert assigned_constant(after["residual_zero.py"], name) == expected

    tracker = methods(after["release_baseline.py"])
    effective = tracker["ReleaseBaselineTracker.effective_tare"]
    returned, = [item for item in effective.body if isinstance(item, ast.Return)]
    assert same_expression(returned.value, "_raw24(manual_tare24).copy() + self._offset_raw24")
    feed = tracker["ReleaseBaselineTracker.feed"]
    offset_assignments = [item for item in ast.walk(feed) if isinstance(item, ast.Assign)
                          and any(isinstance(target, ast.Attribute) and target.attr == "_offset_raw24"
                                  for target in item.targets)]
    assert len(offset_assignments) == 1
    assert same_expression(offset_assignments[0].value, "reference - manual")
    assert not any(isinstance(item, ast.AugAssign) and isinstance(item.target, ast.Attribute)
                   and item.target.attr == "_offset_raw24" for item in ast.walk(feed))
    cycle_reset = tracker["ReleaseBaselineTracker.reset_cycle"]
    assert not any(isinstance(item, ast.Attribute) and item.attr == "_offset_raw24"
                   for item in ast.walk(cycle_reset))
    assert calls(cycle_reset, "reset")

    monitor = methods(after["main.py"])
    assert "Monitor._apply_residual_zero" not in monitor, "v22 scalar display clipping remains"
    assert "Monitor._current_release_zero_result" not in monitor
    helper = monitor["Monitor._effective_tare_raw"]
    effective_call, = calls(helper, "effective_tare")
    assert len(effective_call.args) == 1 and same_expression(effective_call.args[0], "self.tare_raw")
    reference_epoch = monitor["Monitor._raw_baseline_reference_epoch"]
    assert not any(isinstance(node, ast.Attribute) and node.attr == "baseline_epoch"
                   and isinstance(node.value, ast.Attribute) and node.value.attr == "raw_baseline_tracker"
                   for node in ast.walk(reference_epoch)), "Work epoch must not clear its own offset"
    reset = monitor["Monitor._reset_release_zero"]
    assert calls(reset, "reset_cycle") and not calls(reset, "reset_reference")
    update = monitor["Monitor._update_raw_baseline"]
    update_feed, = calls(update, "feed")
    assert [ast.dump(arg) for arg in update_feed.args] == [ast.dump(ast.parse(expr, mode="eval").body)
        for expr in ("paired_frame.raw", "work_result.fz_n", "paired_frame.received_at")]
    assert any(keyword.arg == "manual_tare24" and same_expression(keyword.value, "self.tare_raw")
               for keyword in update_feed.keywords)
    for node in ast.walk(update):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            assert not any(isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                           and target.value.id == "self" and target.attr in {"raw", "tare_raw"}
                           for target in targets), "The automatic work baseline must retain raw/manual tare"
    assert not calls(update, "_tare") and not calls(update, "_invalidate_host_tare")
    allowed = ast.unparse(monitor["Monitor._raw_baseline_updates_allowed"])
    assert "not self.recording" in allowed and "not self.protocol.active" in allowed
    assert "calibration_dialog" in allowed and "capturing" in allowed
    display = monitor["Monitor._update_force_display"]
    assert calls(display, "_update_raw_baseline") and calls(display, "_effective_tare_raw")
    work_evaluations = [call for call in calls(display, "evaluate_raw_force")
                        if len(call.args) > 1 and same_expression(call.args[1], "effective_tare")]
    assert len(work_evaluations) >= 2, "Commit must recompute the complete nonlinear model"
    no_tare, = [node for node in display.body if isinstance(node, ast.If)
                and same_expression(node.test, "self.tare_raw is None")]
    zero_output = [node for node in ast.walk(display) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Attribute) and target.attr == "device_fz"
                           for target in node.targets)
                   and isinstance(node.value, ast.Constant) and node.value.value == 0.]
    assert all(node in list(ast.walk(ast.Module(body=no_tare.body, type_ignores=[])))
               for node in zero_output), "A scalar force-zero clamp remains outside the missing-tare branch"
    local_feed, = [call for call in calls(display, "feed")
                   if isinstance(call.func.value, ast.Attribute) and call.func.value.attr == "local_force_runtime"]
    assert same_expression(local_feed.args[1], "effective_tare")
    spatial, = calls(display, "spatial_map_for_total")
    assert same_expression(spatial.args[1], "effective_tare")
    warm = monitor["Monitor._warm_start_tare_history"]
    assert calls(warm, "_effective_tare_raw")
    assert not calls(warm, "_update_raw_baseline"), "History replay may not observe release twice"

    capture = monitor["Monitor._capture_force_trace"]
    payload, = [call for call in calls(capture, "dict") if any(k.arg == "raw24" for k in call.keywords)]
    trace_fields = {item.arg: item.value for item in payload.keywords}
    expected_fields = {"raw24", "tare_raw24", "delta_raw24", "manual_tare_raw24", "manual_delta_raw24",
                       "manual_reference_model_n", "effective_tare_raw24", "release_offset_raw24",
                       "effective_baseline_epoch", "raw_baseline_update_allowed", "raw_baseline_changed",
                       "raw_baseline_reason", "original_model_force_n", "selected_force_n"}
    assert expected_fields <= set(trace_fields)
    assert same_expression(trace_fields["raw24"], "frame.raw")
    assert same_expression(trace_fields["manual_tare_raw24"], "self.tare_raw")
    assert same_expression(trace_fields["release_offset_raw24"], "self.raw_baseline_tracker.offset_raw24")
    assert ast.literal_eval(trace_fields["release_zero_applied"]) is False
    row = monitor["Monitor._record_base"]
    assert calls(row, "_effective_tare_raw") and calls(row, "_raw_baseline_csv_row")
    csv_row = ast.unparse(monitor["Monitor._raw_baseline_csv_row"])
    assert "self.tare_raw.tolist()" in csv_row and "self.raw - self.tare_raw" in csv_row
    assert "self.raw_baseline_tracker.offset_raw24.tolist()" in csv_row
    assert "RAW_BASELINE_CSV_COLUMNS" in ast.unparse(monitor["Monitor._csv_header"])
    assert "effective_baseline_epoch" in after["recording_report.py"].decode("utf-8-sig")
    assert "audit_effective_baseline" in after["folder_report.py"].decode("utf-8-sig")


def main():
    manifest = json.loads((REPORT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION
    assert Path(manifest["exe"]).resolve() == EXE.resolve()
    assert set(manifest["changed_sources"]) == CHANGED and set(manifest["added_sources"]) == ADDED
    assert EXE.is_file() and EXE.stat().st_size == manifest["exe_bytes"]
    assert sha(EXE.read_bytes()) == manifest["exe_sha256"]
    assert manifest["standalone_exe"] is True and manifest["zip_created"] is False
    assert {path.name for path in OUT.iterdir()} == {EXE.name}, "The delivery must contain only the requested EXE"
    assert not OUT.with_suffix(".zip").exists(), "No new ZIP was requested"
    previous_manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    assert previous_manifest["version"] == "5.3.22-residual-zero"
    assert sha((BASE / "source.zip").read_bytes()) == previous_manifest["files"]["source.zip"]["sha256"]
    before = archive_files(BASE / "source.zip")
    after = {path.relative_to(SOURCE).as_posix(): path.read_bytes() for path in SOURCE.rglob("*")
             if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}
    assert set(after) == set(before) | ADDED
    assert {name for name, data in before.items() if data != after[name]} == CHANGED
    assert manifest["source_sha256"] == {name: sha(data) for name, data in after.items()}
    for name, expected in BINARY_HASHES.items():
        assert before[name] == after[name] and sha(after[name]) == expected
    verify_integration(before, after)

    # No subprocess, EXE invocation, FFI, serial connection or USB operation is
    # performed here. CArchiveReader only parses the executable's stored data.
    archive = CArchiveReader(str(EXE))
    pyz_name, = (name for name, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    embedded = {"main.py": marshal.loads(archive.extract("main"))}
    embedded_names = ("release_baseline.py", "residual_zero.py", "firmware_updater.py",
                      "recording_report.py", "folder_report.py", "force_trace.py",
                      "baseline_quality.py", *UNCHANGED_MATH, *UNCHANGED_TRANSPORT)
    for name in embedded_names:
        embedded[name] = pyz.extract(name.removesuffix(".py"))
    for name, code in embedded.items():
        reviewed = compile(after[name], name, "exec", dont_inherit=True, optimize=0)
        assert code_signature(code) == code_signature(reviewed), f"Embedded code differs from frozen source: {name}"
    assert stored_constant(embedded["main.py"], "MONITOR_VERSION") == VERSION
    assert stored_constant(embedded["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.23"
    for name, expected in RESIDUAL_LIMITS.items():
        assert stored_constant(embedded["residual_zero.py"], name) == expected
    spi = embedded["spi_iap_protocol.py"]
    assert stored_constant(spi, "SNAPSHOT_MAX_ATTEMPTS") == 3
    assert stored_constant(spi, "SNAPSHOT_STALE_MIN_POLLS") == 3
    assert stored_constant(spi, "SNAPSHOT_STALE_MIN_SECONDS") == .18
    for name in (*PROFILES, *BINARY_HASHES):
        archived_name, = [candidate for candidate in archive.toc if candidate.casefold() == name.casefold()]
        assert archive.extract(archived_name) == after[name], name

    expected_tests = [name for name in previous_manifest["tests"] if name != "test_residual_zero_monitor"] + [
        "test_release_baseline", "test_release_baseline_monitor", "test_release_baseline_replay",
        "test_recording_report", "test_folder_report", "test_markdown_report", "test_consistency_report"]
    assert manifest["tests"] == expected_tests and len(expected_tests) == 33
    log = (REPORT / "offline_tests.log").read_text(encoding="utf-8")
    assert log.strip().splitlines()[-1] == "OK (skipped=8)" and "FAILED" not in log
    count, = re.findall(r"^Ran (\d+) tests in ", log, re.MULTILINE)
    skip_lines = [line for line in log.splitlines() if " ... skipped " in line]
    assert len(skip_lines) == 8 and all("private" in line.lower() or "recorded regression" in line.lower()
                                        for line in skip_lines)
    for module in expected_tests:
        assert "(" + module + "." in log, module
    c_log = (REPORT / "firmware_state_repro.log").read_text(encoding="utf-8")
    assert "PASS: 3 production RXIRQ stale snapshot/reissue recovery cases" in c_log and "\nOK\n" in c_log
    build_log = (REPORT / "build.log").read_text(encoding="utf-8")
    assert "Build complete!" in build_log
    result = dict(
        result="passed", version=VERSION, exe=str(EXE), exe_sha256=manifest["exe_sha256"],
        exe_bytes=manifest["exe_bytes"], standalone_exe_only=True, zip_created=False,
        changed_sources=sorted(CHANGED), added_sources=sorted(ADDED),
        frozen_sources_match_manifest=True, embedded_code_matches_frozen_source=True,
        embedded_modules_compared=len(embedded), all_other_sources_byte_identical=True,
        original_force_model_and_thresholds_unchanged=True, calibration_profiles_byte_identical=True,
        wch_drivers_and_dlls_unchanged=True, firmware_unchanged=True,
        transport_protocols_byte_identical=True, snapshot_recovery_bounded=True,
        release_recognition_behavior_preserved=True, scalar_force_clipping_removed=True,
        full_model_recomputed_from_raw_work_zero=True, manual_tare_and_original_raw_preserved=True,
        work_offset_assigned_directly_not_accumulated=True, normal_interaction_reset_preserves_offset=True,
        working_reference_frozen_during_recording_tests_calibration=True,
        raw_coordinate_and_manual_coordinate_audit_in_csv_and_trace=True,
        reports_reject_mixed_working_baselines=True, partial_unload_can_be_rebased=True,
        no_claim_of_proven_complete_physical_unload=True,
        offline_tests_run=int(count), offline_tests_skipped=8, offline_test_modules=len(expected_tests),
        firmware_state_injection_cases=3, hardware_accessed=False, field_validation="pending")
    target = REPORT / "release_verification.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
