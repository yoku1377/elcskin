"""Freeze and build a standalone v5.3.24 EXE, without hardware access or ZIP output."""
from __future__ import annotations

from pathlib import Path

import json
import os
import shutil
import subprocess
import sys
import zipfile

import build_spi_gui_cs_fix_release as dependencies_builder
from build_raw_baseline_exe import CHECKS as PREVIOUS_CHECKS
from build_uart_upgrade_fix_release import ROOT, CURRENT, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, sha, files


BASE = ROOT / "release/TacAI_H3_v5_3_23_RAWBaseline_20261019"
BASE_SOURCE = ROOT / "tmp/raw_baseline_20261019_build/source"
BASE_MANIFEST = ROOT / "outputs/raw_baseline_20261019/manifest.json"
OUT = ROOT / "release/TacAI_H3_v5_3_24_CalibrationRecovery_20261020"
WORK = ROOT / "tmp/calibration_gap_20261020_build"
REPORT = ROOT / "outputs/calibration_gap_20261020"
NAME = "TacAI H3 v5.3.24 Calibration Recovery"
VERSION = "5.3.24-calibration-gap"
CHANGED = {"main.py", "firmware_updater.py", "calibration_acquisition.py", "test_spi_monitor_ui.py"}
ADDED = {"test_calibration_gap_dialog.py", "test_calibration_gap_monitor.py"}
CHECKS = [*PREVIOUS_CHECKS,"test_calibration_acquisition","test_calibration_gap_dialog","test_calibration_gap_monitor"]


def main():
    assert not OUT.exists() and not WORK.exists(), "Never replace an existing EXE build"
    REPORT.mkdir(parents=True, exist_ok=True)
    old_manifest = json.loads(BASE_MANIFEST.read_text(encoding="utf-8"))
    assert old_manifest["version"] == "5.3.23-raw-baseline"
    assert sha(Path(old_manifest["exe"]).read_bytes()) == old_manifest["exe_sha256"]
    baseline = {name:path.read_bytes() for name,path in files(BASE_SOURCE).items()}
    assert {name:sha(data) for name,data in baseline.items()} == old_manifest["source_sha256"]
    assert CHANGED <= set(baseline) and not ADDED & set(baseline)
    source = WORK / "source"
    source.mkdir(parents=True)
    OUT.mkdir(parents=True)
    for name, data in baseline.items():
        path = (source / name).resolve()
        assert path.is_relative_to(source.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name in CHANGED | ADDED:
        shutil.copy2(CURRENT / name, source / name)
    assert {name for name, data in baseline.items() if (source / name).read_bytes() != data} == CHANGED
    assert set(files(source)) == set(baseline) | ADDED
    for name, expected in (("CH341DLLA64.DLL", CH341_SHA), ("CH347DLLA64.dll", CH347_SHA),
                           ("TacAI_H3_App_SPI_rxirq_v5.tacai", APP_SHA)):
        assert sha((source / name).read_bytes()) == expected
    for name in PROFILES:
        assert (source / name).read_bytes() == baseline[name]

    dependencies_builder.WORK = WORK
    deps, copied = dependencies_builder.isolated_optional_dependencies()
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
               TACAI_UPGRADE_LOG_DIR=str(WORK / "fake_upgrade_logs"),
               TACAI_STREAM_LOG_DIR=str(WORK / "fake_stream_logs"),
               TACAI_FORCE_TRACE_DIR=str(WORK / "fake_force_traces"),
               PYTHONPATH=os.pathsep.join((str(source), str(deps))),
               PYINSTALLER_CONFIG_DIR=str(WORK / "pyinstaller_cache"))
    print("Frozen source ready; checking calibration pause/retry, valid recovery, RAW baseline and transport regressions", flush=True)
    with (REPORT / "offline_tests.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", *CHECKS, "-v"],
                       cwd=source, env=env, check=True, stdout=log, stderr=subprocess.STDOUT)
    with (REPORT / "firmware_state_repro.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p",
                        "test_spi_snapshot_stale_firmware.py", "-v"],
                       cwd=ROOT, env=env, check=True, stdout=log, stderr=subprocess.STDOUT)
    assert "PASS: 3 production RXIRQ stale snapshot/reissue recovery cases" in (
        REPORT / "firmware_state_repro.log").read_text(encoding="utf-8")
    print("Frozen regression checks passed; compiling standalone EXE", flush=True)
    command = [sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--name", NAME, "--distpath", str(OUT), "--workpath", str(WORK / "work"),
               "--specpath", str(WORK), "--paths", str(source), "--paths", str(deps),
               "--exclude-module", "pandas", "--exclude-module", "scipy", "--collect-data", "openpyxl",
               "--collect-submodules", "openpyxl", "--runtime-hook", str(source / "qt_platform_runtime_hook.py"),
               "--add-binary", f"{source / 'CH347DLLA64.dll'};.",
               "--add-binary", f"{source / 'CH341DLLA64.DLL'};.",
               "--add-data", f"{source / 'assets'};assets"]
    for name in (*PROFILES, "TacAI_H3_App_SPI_rxirq_v5.tacai"):
        command += ["--add-data", f"{source / name};."]
    command.append(str(source / "main.py"))
    with (REPORT / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=source, env=env, check=True, stdout=log, stderr=subprocess.STDOUT)

    for name, data in baseline.items():
        assert name in CHANGED or (source / name).read_bytes() == data
    executable = OUT / (NAME + ".exe")
    manifest = dict(version=VERSION, date="2026-10-20", exe=str(executable),
                    exe_sha256=sha(executable.read_bytes()), exe_bytes=executable.stat().st_size,
                    standalone_exe=True, zip_created=False, baseline=BASE.name,
                    changed_sources=sorted(CHANGED), added_sources=sorted(ADDED),
                    source_sha256={name: sha(path.read_bytes()) for name, path in sorted(files(source).items())},
                    original_force_model_and_profiles_unchanged=True, manual_tare_preserved=True,
                    raw_work_reference_compensation=True, scalar_force_clipping=False,
                    calibration_and_recording_reference_frozen=True,
                    entire_calibration_session_reference_frozen=True,
                    recoverable_gap_discards_only_current_capture=True,
                    recovery_requires_manual_recapture=True,
                    fatal_device_change_invalid_or_timeout_ends_session=True,
                    stopped_progress_no_false_100_percent=True,
                    dlls_and_firmware_unchanged=True, snapshot_recovery=old_manifest["snapshot_recovery"],
                    tests=CHECKS, excluded_obsolete_test="test_residual_zero_monitor (v22 display-clipping behavior)",
                    dependency_sources=copied, hardware_accessed=False, hardware_validation="pending",
                    release_recognition="software inference; does not independently prove full physical unload")
    (REPORT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(exe=str(executable), sha256=manifest["exe_sha256"], bytes=manifest["exe_bytes"]),
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
