"""Build v5.3.22 from the immutable v5.3.21 source; no hardware access."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile

import build_spi_gui_cs_fix_release as dependencies_builder
from build_uart_upgrade_fix_release import (
    ROOT, CURRENT, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, sha, files, TESTS,
)


BASE = ROOT / "release/TacAI_H3_v5_3_21_BaselineHistory_20261019"
OUT = ROOT / "release/TacAI_H3_v5_3_22_ResidualZero_20261019"
PACKAGE = OUT.with_suffix(".zip")
WORK = ROOT / "tmp/residual_zero_20261019_build"
NAME = "TacAI H3 v5.3.22 Residual Zero"
CHANGED = {"main.py", "firmware_updater.py"}
ADDED = {"residual_zero.py", "test_residual_zero.py", "test_residual_zero_monitor.py"}
CHECKS = [
    *TESTS, "test_baseline_quality", "test_recording_availability",
    "test_force_trace", "test_force_trace_monitor", "test_residual_zero", "test_residual_zero_monitor", "test_h3_v3_3_force_model",
    "test_acquisition_diagnostics", "test_local_state", "test_local_high_force",
    "test_local_force_monitor", "test_automatic_compensation",
    "test_high_force_protocol", "test_high_force_monitor",
    "test_back_total_force", "test_back25_consistency_force",
    "test_back25_smooth_force", "test_back25_domain_force",
    "test_regional_high_force", "test_regional_ard_force", "test_six_hall_force",
]


def main():
    assert not OUT.exists() and not WORK.exists() and not PACKAGE.exists(), "Never overwrite a release"
    old_manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    assert old_manifest["version"] == "5.3.21-baseline-history"
    assert sha((BASE / old_manifest["exe"]).read_bytes()) == old_manifest["exe_sha256"]
    assert sha((BASE / "source.zip").read_bytes()) == old_manifest["files"]["source.zip"]["sha256"]
    with zipfile.ZipFile(BASE / "source.zip") as archive:
        baseline = {name: archive.read(name) for name in archive.namelist()}
    assert CHANGED <= set(baseline)
    assert not (ADDED & set(baseline))
    for name in CHANGED | ADDED:
        assert (CURRENT / name).is_file(), f"Missing reviewed source: {name}"
    OUT.mkdir(parents=True)
    WORK.mkdir(parents=True)
    source = OUT / "source"
    for name, data in baseline.items():
        target = (source / name).resolve()
        assert target.is_relative_to(source.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for name in CHANGED | ADDED:
        shutil.copy2(CURRENT / name, source / name)
    changed = {name for name, data in baseline.items() if (source / name).read_bytes() != data}
    assert changed == CHANGED, changed
    assert set(files(source)) == set(baseline) | ADDED
    for name, expected in (("CH341DLLA64.DLL", CH341_SHA), ("CH347DLLA64.dll", CH347_SHA),
                           ("TacAI_H3_App_SPI_rxirq_v5.tacai", APP_SHA)):
        assert sha((source / name).read_bytes()) == expected
    for name in PROFILES:
        assert (BASE / name).read_bytes() == baseline[name]
        shutil.copy2(BASE / name, OUT / name)

    dependencies_builder.WORK = WORK
    deps, copied = dependencies_builder.isolated_optional_dependencies()
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
               TACAI_UPGRADE_LOG_DIR=str(WORK / "fake_upgrade_logs"),
               TACAI_STREAM_LOG_DIR=str(WORK / "fake_stream_logs"),
               TACAI_FORCE_TRACE_DIR=str(WORK / "fake_force_traces"),
               PYTHONPATH=os.pathsep.join((str(source), str(deps))),
               PYINSTALLER_CONFIG_DIR=str(WORK / "pyinstaller_cache"))
    print(f"Frozen source: {source}; checking residual suppression, tare history, traces and transport regressions", flush=True)
    with (OUT / "offline_tests.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", *CHECKS, "-v"], cwd=source, env=env,
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    with (OUT / "firmware_state_repro.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p",
                        "test_spi_snapshot_stale_firmware.py", "-v"], cwd=ROOT, env=env,
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    assert "PASS: 3 production RXIRQ stale snapshot/reissue recovery cases" in (
        OUT / "firmware_state_repro.log").read_text(encoding="utf-8")
    print("Frozen offline and production-C tests passed; building EXE", flush=True)
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
    with (OUT / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=source, env=env, check=True, stdout=log, stderr=subprocess.STDOUT)

    for name, data in baseline.items():
        assert name in CHANGED or (source / name).read_bytes() == data, name
    assert set(files(source)) == set(baseline) | ADDED
    (OUT / "source_sha256.json").write_text(json.dumps(
        {name: sha(path.read_bytes()) for name, path in sorted(files(source).items())}, indent=2), encoding="utf-8")
    with zipfile.ZipFile(OUT / "source.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files(source).items()):
            archive.write(path, name)
    (OUT / "先看这里.txt").write_text('TacAI H3 v5.3.22 松手残余归零 — 2026-10-19\n\n解压整个文件夹，运行 TacAI H3 v5.3.22 Residual Zero.exe。\nUART、CH341、CH347共用此处理，只换上位机，不需要重刷固件。\n先完全卸载并空载置零，保持默认勾选“松手残余归零（≤2 N）”。\n\n本版按要求新增软件松手归零：观察到至少2.5N的按压峰值后，\n当读数降到峰值60%以内且不高于2N，连续RAW稳定至少1秒、10帧，\n将当前显示合力与八区力值归零。覆盖本次报告的0.2～1.74N残余。\n持续恒载不会仅因保持时间长而自动清零；第一次小载荷未经过上述\n卸载过程时保持原读数。检测到新的明显RAW变化或力值超过2N时\n退出归零，以后重新识别新的按压/卸载过程。\n\n这是基于软件变化模式的归零，不能独立证明手已完全松开。\n例如5N部分卸到1N后稳定，也可能显示0；测量卸载末段小载荷时\n请关闭此开关。原始RAW、原模型读数与零点均保留，归零只作用于\n选定的显示/导出合力和区域值，并在CSV和日志中单独标记。\n记录或自动测试中不能中途切换该开关，避免混合两种处理方式。\n\n保留空载历史修复、SPI采集恢复和原有升级处理。基线稳定性阈值、\n原力模型和标定参数均未更改；传感器固件和WCH DLL也不变。\n无需手工开记录，最近180秒/最多6000个接受帧自动缓存；再次置零、\n断开、关闭或采集故障会保存到 logs\\force_trace，包含归零前后读数。\n若实机残余仍未归零，请在发生后关闭连接并提供最新.jsonl文件，\n无需反复刷固件或更换接线。软件归零的现场效果仍需验证。\n', encoding="utf-8-sig")
    shutil.copy2(__file__, OUT / "build_residual_zero_release.py")
    exe = OUT / (NAME + ".exe")
    release = dict(
        version="5.3.22-residual-zero", date="2026-10-19", exe=exe.name,
        exe_sha256=sha(exe.read_bytes()), baseline=BASE.name,
        changed_sources=sorted(CHANGED), added_sources=sorted(ADDED),
        all_other_sources_byte_identical=True, calibration_profiles_unchanged=True,
        original_force_model_unchanged=True, selected_force_postprocessing_changed=True, baseline_threshold_unchanged=True, wch_dlls_unchanged=True,
        sensor_firmware_changed=False, hardware_accessed=False, hardware_validation="pending",
        tare_history=dict(real_accepted_frames_only=True, original_timestamps_and_tokens=True,
                          model_outputs_not_fabricated=True, cross_epoch_replay=False,
                          automatic_tare=False),
        residual_force_root_cause="physical cause unconfirmed; host output suppression per user request",
        residual_zero=dict(default_enabled=True, max_residual_n=2.0, minimum_peak_n=2.5, maximum_peak_ratio=.6, quiet_span_s=1., max_exit_raw_norm=350., original_raw_and_model_preserved=True, partial_unload_may_be_zeroed=True),
        force_trace=dict(log="logs/force_trace", max_span_seconds=180, max_accepted_frames=6000,
                         manual_recording_required=False,
                         save_triggers=["before_tare", "disconnect", "close", "stream_fault"]),
        snapshot_recovery=old_manifest["snapshot_recovery"], upgrade_commands_single_send=True,
        diagnostic_log="logs/stream", offline_dependency_sources=copied, tests=CHECKS,
        files={path.name: dict(bytes=path.stat().st_size, sha256=sha(path.read_bytes()))
               for path in sorted(OUT.iterdir()) if path.is_file()})
    (OUT / "manifest.json").write_text(json.dumps(release, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(PACKAGE, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUT.iterdir()):
            if path.is_file():
                archive.write(path, OUT.name + "/" + path.name)
    print(json.dumps(dict(package=str(PACKAGE), sha256=sha(PACKAGE.read_bytes()),
                          exe_sha256=release["exe_sha256"]), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
