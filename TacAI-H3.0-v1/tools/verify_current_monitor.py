"""Verify a current-source v5.3.28 rebuild; never open physical hardware.

Usage: python tools/verify_current_monitor.py --date 20261021
Use --manifest and --baseline-manifest to select explicit build evidence.
Startup runs once per build; --reuse-startup-witness rechecks an existing
hash-bound witness without launching the EXE again. This verifier never runs
the regression suite, initializes a bridge, or stages/commits Git files.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import marshal
import os
from pathlib import Path
import re
import subprocess

from PyInstaller.archive.readers import CArchiveReader
from verify_raw_baseline_exe import (
    BINARY_HASHES, PROFILES, UNCHANGED_MATH, UNCHANGED_TRANSPORT,
    assigned_constant, code_signature, sha, stored_constant,
)


ROOT = Path(__file__).resolve().parents[1]
VERSION = "5.3.28-zp500n-stream"
NAME = "TacAI H3 v5.3.28 ZP500N Stream"
EXCLUDED_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".git", ".venv", "venv",
                           "logs", "build", "dist"})
DATA_TREES = frozenset({"assets", "testdata", "models"})
POLICY = {
    "recursive_code_and_models": ["*.py", "*.json"],
    "complete_data_trees": sorted(DATA_TREES),
    "root_files": ["requirements.txt", "CH347DLLA64.dll", "CH341DLLA64.DLL", "TacAI_H3_App_SPI_rxirq_v5.tacai"],
    "excluded_directories": sorted(EXCLUDED_DIRS),
    "excluded_extensions": [".pyc", ".pyo", ".spec"],
    "other_dlls_and_tacai_images": "excluded",
}
EMBEDDED_MODULES = {
    "main.py", "firmware_updater.py", "calibration_acquisition.py", "calibration_trajectory.py",
    "zp500n_protocol.py", "baseline_quality.py", "release_baseline.py", "residual_zero.py",
    "recording_report.py", "folder_report.py", "force_trace.py", *UNCHANGED_MATH, *UNCHANGED_TRANSPORT,
}


def source_files(directory):
    """Independently implement the documented current-source selection policy."""
    directory = Path(directory).resolve(strict=True)
    selected = {}
    for path in directory.rglob("*"):
        relative = path.relative_to(directory)
        if any(part.casefold() in EXCLUDED_DIRS for part in relative.parts) or not path.is_file():
            continue
        assert not path.is_symlink() and path.resolve().is_relative_to(directory), path
        suffix = path.suffix.casefold()
        if suffix in {".pyc", ".pyo", ".spec"}:
            continue
        name = relative.as_posix()
        if suffix in {".dll", ".tacai"}:
            if name not in BINARY_HASHES:
                continue
        elif suffix not in {".py", ".json"} and name != "requirements.txt" and relative.parts[0] not in DATA_TREES:
            continue
        selected[name] = path
    return dict(sorted(selected.items()))


def hashes(paths):
    return {name: sha(path.read_bytes()) for name, path in paths.items()}


def archive_code(executable):
    archive = CArchiveReader(str(executable))
    pyz_name, = (name for name, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    code = {"main.py": marshal.loads(archive.extract("main"))}
    for name in sorted(EMBEDDED_MODULES - {"main.py"}):
        code[name] = pyz.extract(name.removesuffix(".py"))
    return archive, code


def unittest_result(path):
    text = path.read_text(encoding="utf-8")
    run, = re.findall(r"^Ran (\d+) tests? in ", text, re.MULTILINE)
    result, = re.findall(r"^OK(?: \(skipped=(\d+)\))?\s*$", text, re.MULTILINE)
    assert "FAILED" not in text and "Traceback (most recent call last)" not in text
    return text, {"run": int(run), "skipped": int(result or 0)}


def startup_check(executable, executable_hash, report, *, reuse=False):
    witness, process_path = report / "startup_selftest.json", report / "startup_process.json"
    if reuse:
        assert witness.is_file() and process_path.is_file(), "No complete startup witness to reuse"
        process = json.loads(process_path.read_text(encoding="utf-8"))
        assert process["exe_sha256"] == executable_hash
        assert process["command"] == [str(executable), "--self-test", "--self-test-json", str(witness)]
    else:
        assert not witness.exists() and not process_path.exists(), "Startup already witnessed; use --reuse-startup-witness"
        empty_cwd = report / "startup_empty_cwd"
        empty_cwd.mkdir(exist_ok=True)
        assert not any(empty_cwd.iterdir()), "Startup requires an empty working directory"
        env = os.environ.copy()
        env.update(QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                   TACAI_STREAM_LOG_DIR=str(report / "startup_logs/stream"),
                   TACAI_FORCE_TRACE_DIR=str(report / "startup_logs/force_trace"),
                   TACAI_UPGRADE_LOG_DIR=str(report / "startup_logs/upgrade"))
        command = [str(executable), "--self-test", "--self-test-json", str(witness)]
        run = subprocess.run(command, cwd=empty_cwd, env=env, timeout=60, capture_output=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        process = dict(exit_code=run.returncode, exe_sha256=executable_hash, command=command,
                       cwd=str(empty_cwd), platform="offscreen", hardware_connected=False,
                       stdout=run.stdout.decode("utf-8", "replace"), stderr=run.stderr.decode("utf-8", "replace"))
        with process_path.open("x", encoding="utf-8") as output:
            json.dump(process, output, ensure_ascii=False, indent=2)
    assert process["exit_code"] == 0 and process["platform"] == "offscreen"
    result = json.loads(witness.read_text(encoding="utf-8"))
    assert result["version"] == VERSION and result["serial_connected"] is False
    assert result["automatic_compensation_enabled"] is True
    assert set(result["automatic_available_profiles"]) == {"front", "back"}
    assert result["runtime_profile"] == "auto" and not result["parameter_load_error"]
    return witness, result


def verify(manifest_path, baseline_path, *, reuse_startup=False):
    manifest_path, baseline_path = manifest_path.resolve(strict=True), baseline_path.resolve(strict=True)
    report = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert manifest["version"] == baseline["version"] == VERSION
    assert manifest["build_kind"] == "current_source_rebuild" and manifest["source_files_policy"] == POLICY
    assert manifest["force_zero_scale_and_models_changed_by_builder"] is False
    assert manifest["hardware_accessed"] is False and manifest["gui_executable_run_by_builder"] is False
    current = Path(manifest["source_root"]).resolve(strict=True)
    frozen = Path(manifest["frozen_source"]).resolve(strict=True)
    assert current == (ROOT / "pc/TfgMonitorPython_H3_v5.0").resolve()
    current_inputs, frozen_inputs = source_files(current), source_files(frozen)
    assert hashes(current_inputs) == manifest["current_source_sha256"]
    assert hashes(frozen_inputs) == manifest["source_sha256"]
    assert set(frozen_inputs) == set(current_inputs) | BINARY_HASHES.keys()
    for name, path in current_inputs.items():
        assert path.read_bytes() == frozen_inputs[name].read_bytes(), name
    assert set(manifest["source_origins"]) == set(frozen_inputs)
    for name, origin in manifest["source_origins"].items():
        assert sha(Path(origin).read_bytes()) == manifest["source_sha256"][name], name
        if name not in BINARY_HASHES:
            assert Path(origin).resolve() == current_inputs[name].resolve(), name
    assert manifest["resource_sha256"] == BINARY_HASHES
    for name, expected in BINARY_HASHES.items():
        assert sha(frozen_inputs[name].read_bytes()) == expected, name
    assert manifest["profile_sha256"] == {name: manifest["source_sha256"][name] for name in PROFILES}
    # Every selected source, test, profile and resource matches the accepted v28
    # input. Only old unused images/specs may fall outside the new policy.
    baseline_frozen = Path(baseline.get("frozen_source", ROOT / "tmp/zp500n_stream_20261020_build/source"))
    baseline_inputs = source_files(baseline_frozen)
    assert hashes(baseline_inputs) == {name: baseline["source_sha256"][name] for name in baseline_inputs}
    assert hashes(baseline_inputs) == hashes(frozen_inputs), "New build differs from accepted v28 source"
    assert assigned_constant(frozen_inputs["main.py"].read_bytes(), "MONITOR_VERSION") == VERSION
    assert assigned_constant(frozen_inputs["firmware_updater.py"].read_bytes(), "UPGRADER_VERSION") == "5.3.28"
    executable, baseline_executable = Path(manifest["exe"]).resolve(strict=True), Path(baseline["exe"]).resolve(strict=True)
    assert executable.name == NAME + ".exe" and executable != baseline_executable
    assert sha(executable.read_bytes()) == manifest["exe_sha256"] and executable.stat().st_size == manifest["exe_bytes"]
    assert sha(baseline_executable.read_bytes()) == baseline["exe_sha256"]
    assert set(executable.parent.iterdir()) == {executable} and not executable.parent.with_suffix(".zip").exists()
    assert manifest["standalone_exe"] is True and manifest["zip_created"] is False
    archive, embedded = archive_code(executable)
    baseline_archive, baseline_embedded = archive_code(baseline_executable)
    assert len(embedded) == 27
    for name, code in embedded.items():
        expected = compile(frozen_inputs[name].read_bytes(), name, "exec", dont_inherit=True, optimize=0)
        assert code_signature(code) == code_signature(expected), name
        assert code_signature(code) == code_signature(baseline_embedded[name]), name
    assert stored_constant(embedded["main.py"], "MONITOR_VERSION") == VERSION
    assert stored_constant(embedded["firmware_updater.py"], "UPGRADER_VERSION") == "5.3.28"
    for name in (*PROFILES, *BINARY_HASHES):
        entry, = (candidate for candidate in archive.toc if candidate.casefold() == name.casefold())
        old_entry, = (candidate for candidate in baseline_archive.toc if candidate.casefold() == name.casefold())
        assert archive.extract(entry) == baseline_archive.extract(old_entry) == frozen_inputs[name].read_bytes(), name
    offline_text, offline_result = unittest_result(report / "offline_tests.log")
    _, firmware_result = unittest_result(report / "firmware_state_repro.log")
    assert offline_result == manifest["offline_tests"] == {"run": 543, "skipped": 8}
    assert firmware_result == manifest["firmware_injection_tests"] == {"run": 2, "skipped": 0}
    assert len(manifest["tests"]) == len(set(manifest["tests"])) == 43
    assert set(manifest["tests"]) == set(baseline["tests"])
    assert len([line for line in offline_text.splitlines() if " ... skipped " in line]) == 8
    for module in manifest["tests"]:
        assert "(" + module + "." in offline_text, module
    firmware_text = (report / "firmware_state_repro.log").read_text(encoding="utf-8")
    for marker in ("PASS: 3 production RXIRQ stale snapshot/reissue recovery cases",
                   "PASS: 7 production RXIRQ/mailbox/snapshot ACK-drain guard cases"):
        assert marker in firmware_text, marker
    for name, expected in manifest["firmware_injection_source_sha256"].items():
        assert sha((ROOT / name).read_bytes()) == expected, name
        if name in baseline.get("firmware_injection_source_sha256", {}):
            assert baseline["firmware_injection_source_sha256"][name] == expected, name
    assert "Build complete!" in (report / "build.log").read_text(encoding="utf-8")
    assert sha((ROOT / "tools/build_current_monitor.py").read_bytes()) == manifest["builder_sha256"]
    witness, startup = startup_check(executable, manifest["exe_sha256"], report, reuse=reuse_startup)
    result = dict(result="passed", version=VERSION, date=manifest["date"], manifest=str(manifest_path),
        exe=str(executable), exe_sha256=manifest["exe_sha256"], exe_bytes=manifest["exe_bytes"],
        standalone_exe_only=True, zip_created=False, current_source_files=len(current_inputs), frozen_source_files=len(frozen_inputs),
        current_and_frozen_hashes_match_manifest=True, all_selected_sources_byte_identical_to_accepted_v28=True,
        embedded_modules_compared=len(embedded), embedded_code_matches_frozen_source_and_accepted_v28=True,
        force_models_zero_scale_raw_baseline_gauge_spi_ack_guard_and_iap_unchanged=True,
        profiles_dlls_and_firmware_bytes_unchanged=True, baseline_exe=str(baseline_executable),
        baseline_exe_sha256=baseline["exe_sha256"], offline_tests_run=543, offline_tests_skipped=8,
        offline_test_modules=43, firmware_state_injection_tests=2, firmware_state_injection_cases=10,
        regression_suite_rerun_by_verifier=False, startup_exit_code=0, startup_offscreen_passed=True,
        startup_witness=str(witness), startup_profiles=startup["automatic_available_profiles"],
        hardware_connected=False, physical_port_usb_and_flash_operations=0, git_stage_and_commit_operations=0,
        verification_tool_sha256=sha(Path(__file__).read_bytes()),
        field_validation="Operator accepted that the instrument reading follows unloading; no new physical measurements were taken by this verifier.")
    (report / "release_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--baseline-manifest", type=Path, default=ROOT / "outputs/zp500n_stream_20261020/manifest.json")
    parser.add_argument("--reuse-startup-witness", action="store_true")
    args = parser.parse_args(argv)
    datetime.strptime(args.date, "%Y%m%d")
    verify(args.manifest or ROOT / f"outputs/current_monitor_{args.date}/manifest.json", args.baseline_manifest,
           reuse_startup=args.reuse_startup_witness)


if __name__ == "__main__":
    main()
