"""Freeze v5.3.18 SPI response recovery on delivered v5.3.17, with no USB access."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile

import build_spi_gui_cs_fix_release as dependencies_builder
from build_uart_upgrade_fix_release import ROOT, CURRENT, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, sha, files, TESTS

BASE = ROOT / "release/TacAI_H3_v5_3_17_UART_UpgradeFix_20261019"
OUT = ROOT / "release/TacAI_H3_v5_3_18_SPI_UpgradeFix_20261019"
PACKAGE = OUT.with_suffix(".zip")
WORK = ROOT / "tmp/spi_upgrade_fix_20261019_build"
NAME = "TacAI H3 v5.3.18 SPI Upgrade Fix"
CHANGED = {"main.py", "firmware_updater.py", "spi_iap_protocol.py", "test_spi_iap_protocol.py", "test_spi_monitor_ui.py", "test_uart_iap_protocol.py"}


def main():
    resume = sys.argv[1:] == ["--resume"]
    assert (not OUT.exists() and not WORK.exists()) or resume, "Never overwrite a release"
    assert not PACKAGE.exists()
    if resume:
        assert not (OUT / (NAME + ".exe")).exists() and not (OUT / "manifest.json").exists()
    manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    assert sha((BASE / manifest["exe"]).read_bytes()) == manifest["exe_sha256"]
    with zipfile.ZipFile(BASE / "source.zip") as archive:
        baseline = {n: archive.read(n) for n in archive.namelist()}
    OUT.mkdir(parents=True, exist_ok=resume)
    WORK.mkdir(parents=True, exist_ok=resume)
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
               PYTHONPATH=os.pathsep.join((str(source), str(deps))),
               PYINSTALLER_CONFIG_DIR=str(WORK / "pyinstaller_cache"))
    print(f"Frozen source: {source}; running SPI fault injection and UART regressions", flush=True)
    with (OUT / "offline_tests.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", *TESTS, "-v"], cwd=source, env=env,
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    print("Frozen offline tests passed; building EXE", flush=True)
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
    shutil.copy2(source / "TacAI_H3_App_SPI_rxirq_v5.tacai", OUT / "TacAI_H3_App_SPI_rxirq_v5.tacai")
    (OUT / "先看这里.txt").write_text(
        "TacAI H3 v5.3.18 SPI 升级响应修复 — 2026-10-19\n\n"
        "解压整个文件夹，运行 TacAI H3 v5.3.18 SPI Upgrade Fix.exe。\n"
        "主窗口及升级窗口标题均有v5.3.18，用于确认启动的是新程序。\n"
        "保持现有已确认的接线和传感器v5固件，不用先重新烧BIN。\n"
        "CH347F SPI → 检测实际设备 → 时钟468750Hz → 固件升级 → 选择本包v5 .tacai。\n"
        "升级时保持传感器供电和USB连接，不要打开Demo或其他占用CH347的工具。\n\n"
        "修复：首次读到非法TAI3帧头/CRC/填充时，改为在原超时内重读536字节响应。\n"
        "每条请求只发送一次，重读不重复BEGIN/DATA/END/BOOT、不自动重新擦除；有效响应仍严格校验CRC、序号和填充。\n"
        "USB错误、事务长度错误和设备明确拒绝仍立即停止；持续坏包到超时则报出命令/序号/计数/原始前32字节。\n"
        "新增SPI协议日志，含各命令和ACK、首个异常响应及最终超时信息。\n"
        "日志在EXE同目录logs\\upgrade，也会在升级窗口显示路径。若仍失败，请发这个.log文件。\n\n"
        "保留v5.3.17 UART升级修复、CH341A支持、全部补偿/标定/记录功能。\n"
        "传感器固件未改，默认镜像仍h3_spi_20261016_rxirq_v5。\n"
        "已通过离线故障注入和回归；没有执行实物升级，不能据此保证现场SPI升级已全部解决。\n", encoding="utf-8-sig")
    shutil.copy2(__file__, OUT / "build_spi_upgrade_fix_release.py")
    exe = OUT / (NAME + ".exe")
    release = dict(version="5.3.18-spi-upgrade-response-fix", date="2026-10-19", exe=exe.name,
                   exe_sha256=sha(exe.read_bytes()), baseline=BASE.name,
                   baseline_source_zip_sha256=sha((BASE / "source.zip").read_bytes()),
                   changed_sources=sorted(CHANGED), all_other_sources_byte_identical=True,
                   calibration_profiles_unchanged=True, sensor_firmware_changed=False,
                   hardware_accessed=False, hardware_validation="pending", no_automatic_flash_replay=True,
                   default_image_sha256=APP_SHA, offline_dependency_sources=copied, source_fake_tests=TESTS,
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
