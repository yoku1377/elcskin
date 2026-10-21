"""Freeze and compile v25 continuous calibration traces as one standalone EXE."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import build_spi_gui_cs_fix_release as dependencies_builder
from build_calibration_gap_exe import CHECKS as PREVIOUS_CHECKS
from build_uart_upgrade_fix_release import ROOT, CURRENT, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, sha, files


BASE_SOURCE = ROOT / "tmp/calibration_gap_20261020_build/source"
BASE_MANIFEST = ROOT / "outputs/calibration_gap_20261020/manifest.json"
BASE_VERSION = "5.3.24-calibration-gap"
OUT = ROOT / "release/TacAI_H3_v5_3_25_CalibrationTrace_20261020"
WORK = ROOT / "tmp/calibration_trace_20261020_build"
REPORT = ROOT / "outputs/calibration_trace_20261020"
NAME = "TacAI H3 v5.3.25 Calibration Trace"
VERSION = "5.3.25-calibration-trace"
CHANGED = {"main.py", "firmware_updater.py", "calibration_acquisition.py", "test_acquisition_diagnostics.py"}
ADDED = {"calibration_trajectory.py", "test_calibration_trajectory.py", "test_calibration_trajectory_monitor.py"}
CHECKS = list(dict.fromkeys([*PREVIOUS_CHECKS, "test_calibration_trajectory", "test_calibration_trajectory_monitor"]))


def main():
    assert not OUT.exists() and not WORK.exists(), "Never replace an existing EXE build"
    old = json.loads(BASE_MANIFEST.read_text(encoding="utf-8"))
    assert old["version"] == BASE_VERSION
    assert sha(Path(old["exe"]).read_bytes()) == old["exe_sha256"]
    baseline = {name: path.read_bytes() for name, path in files(BASE_SOURCE).items()}
    assert {name: sha(data) for name, data in baseline.items()} == old["source_sha256"]
    assert CHANGED <= set(baseline) and not ADDED & set(baseline)
    REPORT.mkdir(parents=True, exist_ok=True)
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
    print(f"Frozen {VERSION} source ready; checking continuous traces and calibration recovery", flush=True)
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
                    standalone_exe=True, zip_created=False, baseline=old["version"],
                    changed_sources=sorted(CHANGED), added_sources=sorted(ADDED),
                    source_sha256={name: sha(path.read_bytes()) for name, path in sorted(files(source).items())},
                    original_force_model_and_profiles_unchanged=True,
                    raw_baseline_detector_and_tracker_unchanged=True,
                    iap_protocols_dlls_and_firmware_unchanged=True,
                    full_session_trajectory=True, manual_entry_not_continuous_ground_truth=True,
                    gauge_prior_reference_max_age_s=1.0, sensor_and_gauge_clocks_are_host_arrival=True,
                    existing_three_second_plateau_schema_unchanged=True,
                    calibration_recovery_preserved=True, tests=CHECKS,
                    dependency_sources=copied, hardware_accessed=False, hardware_validation="pending")
    (REPORT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(exe=str(executable), sha256=manifest["exe_sha256"], bytes=manifest["exe_bytes"]),
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
