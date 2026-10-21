"""Build v5.3.21 from the immutable v5.3.20 source; no hardware access."""
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


BASE = ROOT / "release/TacAI_H3_v5_3_20_SPI_Recovery_20261019"
OUT = ROOT / "release/TacAI_H3_v5_3_21_BaselineHistory_20261019"
PACKAGE = OUT.with_suffix(".zip")
WORK = ROOT / "tmp/baseline_history_20261019_build"
NAME = "TacAI H3 v5.3.21 Baseline History Fix"
CHANGED = {"main.py", "firmware_updater.py", "test_baseline_quality.py", "test_spi_monitor_ui.py"}
ADDED = {"force_trace.py", "test_force_trace.py", "test_force_trace_monitor.py"}
CHECKS = [
    *TESTS, "test_baseline_quality", "test_recording_availability",
    "test_force_trace", "test_force_trace_monitor", "test_h3_v3_3_force_model",
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
    assert old_manifest["version"] == "5.3.20-spi-snapshot-recovery"
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
    print(f"Frozen source: {source}; checking tare history, traces, force and transport regressions", flush=True)
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
    (OUT / "先看这里.txt").write_text(
        "TacAI H3 v5.3.21 空载历史修复与残余力自动取证 — 2026-10-19\n\n"
        "解压整个文件夹，运行 TacAI H3 v5.3.21 Baseline History Fix.exe。\n"
        "只换上位机 EXE，无需重刷传感器固件；UART、CH341和CH347均适用。\n"
        "保留v5.3.20的SPI只读快照有界恢复；升级擦写和启动命令仍不自动重发。\n\n"
        "本版修复一个已复现的软件问题：手动空载置零成功后，补偿运行时的\n"
        "已验证静置历史被清空，立刻按压就可能一直提示“静置基线不足”。\n"
        "现在用本次真实、连续、同设备状态的空载帧初始化新历史；逐帧使用\n"
        "新零点下的实际模型输出，不伪造0 N、不重复采集或写入测量记录。\n"
        "状态或诊断epoch发生变化、历史不连续、置零检查失败时，均不跨越\n"
        "这些异常沿用旧历史；未通过的置零不会替换已有零点。\n\n"
        "长按松手后的真实残余力，目前缺少现场RAW恢复数据，尚不能宣称根治。\n"
        "空载置零只建立当时的参考点，长按后若信号未回到该参考，仍可能显示残余。\n"
        "确认已经完全卸载并稳定后，点击原来的“空载置零”可建立当前新零点；\n"
        "静置不会自动改变零点，也不会按时间把持续载荷清为0。\n"
        "本版不扩大归零死区，不修改力模型、标定/补偿参数或基线阈值。\n\n"
        "无需手工开启记录：程序自动保留最近180秒、最多6000个已接受的配对帧。\n"
        "点击空载置零前会先保存此段证据，再次置零、断开、关闭和采集故障时\n"
        "也会保存；文件位于EXE同目录 logs\\force_trace。\n"
        "若再次出现残余力，请先保持完全卸载几秒，再点击“空载置零”，\n"
        "把 logs\\force_trace 中最新的 .jsonl 文件发回来。\n"
        "日志包含RAW、旧零点、力值与模型判据，可区分真实信号恢复和模型误判；\n"
        "不要只用RAW方差数字或静置提示来认定真实力值变化。\n"
        "若显示自动证据保存失败，请同时提供运行日志中的具体路径和错误。\n\n"
        "WCH DLL、传感器应用固件和升级协议与v5.3.20逐字节相同。\n"
        "本版通过离线基线/力模型/记录/GUI/串口升级/SPI回归和生产C状态机测试；\n"
        "长按残余与长时间采集的实机效果仍待现场验证。\n",
        encoding="utf-8-sig")
    shutil.copy2(__file__, OUT / "build_baseline_history_release.py")
    exe = OUT / (NAME + ".exe")
    release = dict(
        version="5.3.21-baseline-history", date="2026-10-19", exe=exe.name,
        exe_sha256=sha(exe.read_bytes()), baseline=BASE.name,
        changed_sources=sorted(CHANGED), added_sources=sorted(ADDED),
        all_other_sources_byte_identical=True, calibration_profiles_unchanged=True,
        force_math_unchanged=True, baseline_threshold_unchanged=True, wch_dlls_unchanged=True,
        sensor_firmware_changed=False, hardware_accessed=False, hardware_validation="pending",
        tare_history=dict(real_accepted_frames_only=True, original_timestamps_and_tokens=True,
                          model_outputs_not_fabricated=True, cross_epoch_replay=False,
                          automatic_tare=False),
        residual_force_root_cause="pending field RAW evidence; no claim of complete correction",
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
