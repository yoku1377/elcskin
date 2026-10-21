"""Build v5.3.20 read-only snapshot recovery, without running hardware tools."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile

import build_spi_gui_cs_fix_release as dependencies_builder
from build_uart_upgrade_fix_release import ROOT, CURRENT, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, sha, files, TESTS

BASE = ROOT / "release/TacAI_H3_v5_3_19_Upgrade_CH341Fix_20261019"
OUT = ROOT / "release/TacAI_H3_v5_3_20_SPI_Recovery_20261019"
PACKAGE = OUT.with_suffix(".zip")
WORK = ROOT / "tmp/spi_snapshot_recovery_20261019_build"
NAME = "TacAI H3 v5.3.20 SPI Snapshot Recovery"
CHANGED = {"main.py", "firmware_updater.py", "spi_iap_protocol.py", "test_spi_iap_protocol.py",
           "test_spi_monitor_ui.py", "baseline_quality.py", "test_baseline_quality.py"}
CHECKS = [*TESTS, "test_baseline_quality", "test_recording_availability"]


def main():
    assert not OUT.exists() and not WORK.exists() and not PACKAGE.exists(), "Never overwrite a release"
    old_manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    assert sha((BASE / old_manifest["exe"]).read_bytes()) == old_manifest["exe_sha256"]
    with zipfile.ZipFile(BASE / "source.zip") as archive:
        baseline = {n: archive.read(n) for n in archive.namelist()}
    OUT.mkdir(parents=True)
    WORK.mkdir(parents=True)
    source = OUT / "source"
    for name, data in baseline.items():
        target = (source / name).resolve()
        assert target.is_relative_to(source.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for name in CHANGED:
        shutil.copy2(CURRENT / name, source / name)
    assert {n for n, data in baseline.items() if (source / n).read_bytes() != data} == CHANGED
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
               PYTHONPATH=os.pathsep.join((str(source), str(deps))),
               PYINSTALLER_CONFIG_DIR=str(WORK / "pyinstaller_cache"))
    print(f"Frozen source: {source}; checking capture recovery, quality and upgrade regressions", flush=True)
    with (OUT / "offline_tests.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", *CHECKS, "-v"], cwd=source, env=env,
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    with (OUT / "firmware_state_repro.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p",
                        "test_spi_snapshot_stale_firmware.py", "-v"], cwd=ROOT, env=env,
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    c_log = (OUT / "firmware_state_repro.log").read_text(encoding="utf-8")
    assert "PASS: 3 production RXIRQ stale snapshot/reissue recovery cases" in c_log
    print("Offline and production-C state tests passed; building EXE", flush=True)
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
        assert name in CHANGED or (source / name).read_bytes() == data
    (OUT / "source_sha256.json").write_text(json.dumps(
        {n: sha(p.read_bytes()) for n, p in sorted(files(source).items())}, indent=2), encoding="utf-8")
    with zipfile.ZipFile(OUT / "source.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files(source).items()):
            archive.write(path, name)
    (OUT / "先看这里.txt").write_text(
        "TacAI H3 v5.3.20 SPI采集恢复 — 2026-10-19\n\n"
        "解压整个文件夹，运行 TacAI H3 v5.3.20 SPI Snapshot Recovery.exe。\n"
        "这次只换上位机，采集修复不需要重刷传感器固件；CH341和CH347均适用。\n"
        "按原步骤检测设备→连接设备→空载置零。\n\n"
        "已复现的软件问题：偶发请求未被接收时，固件一直保留上一条完整ACK；\n"
        "旧上位机只重复读旧响应，0.6秒后结束采集。\n"
        "本版在连续至少3次相同旧快照且持续至少180ms时，用新序号重请求只读快照；\n"
        "最多3次请求，仍受原总超时约束，不接受旧序号或旧样本作为新的测量。\n"
        "恢复期间当前力值暂不可用，数据间断会记录；连续测试或标定会停止，避免跨间断计算均值。\n"
        "收到新的有效样本后恢复显示，保留USB连接。真实USB错误或持续失败仍停止并显示原因。\n"
        "采集异常日志位于EXE同目录logs\\stream，可用于分析现场最初的接收异常。\n"
        "恢复仅适用于只读0x20采集，升级擦写/启动/进入Bootloader命令仍禁止自动重发。\n\n"
        "基线提示已注明RAW方差和非力值。它是原始信号统计，置零后仍随窗口变化；\n"
        "总力及八区读数为0.00N时，不能用这项方差的变化认定力值在跳。\n"
        "基线门限10100、标定/补偿参数、力模型、固件及WCH DLL均未调整。\n"
        "本版通过离线协议/驱动/GUI/质量/记录回归和生产C状态机故障注入，实机长时间运行仍待确认。\n",
        encoding="utf-8-sig")
    shutil.copy2(__file__, OUT / "build_spi_snapshot_recovery_release.py")
    exe = OUT / (NAME + ".exe")
    release = dict(version="5.3.20-spi-snapshot-recovery", date="2026-10-19", exe=exe.name,
                   exe_sha256=sha(exe.read_bytes()), baseline=BASE.name, changed_sources=sorted(CHANGED),
                   all_other_sources_byte_identical=True, calibration_profiles_unchanged=True,
                   force_math_unchanged=True, baseline_threshold_unchanged=True, wch_dlls_unchanged=True,
                   sensor_firmware_changed=False, hardware_accessed=False, hardware_validation="pending",
                   snapshot_recovery=dict(command="0x20", max_attempts=3, repeated_old_polls=3,
                                          minimum_stale_seconds=0.18, total_deadline_unchanged=True),
                   upgrade_commands_single_send=True, diagnostic_log="logs/stream",
                   offline_dependency_sources=copied, tests=CHECKS,
                   files={p.name: dict(bytes=p.stat().st_size, sha256=sha(p.read_bytes()))
                          for p in sorted(OUT.iterdir()) if p.is_file()})
    (OUT / "manifest.json").write_text(json.dumps(release, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(PACKAGE, "x", zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(OUT.iterdir()):
            if p.is_file():
                archive.write(p, OUT.name + "/" + p.name)
    print(json.dumps(dict(package=str(PACKAGE), sha256=sha(PACKAGE.read_bytes()),
                          exe_sha256=release["exe_sha256"]), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
