"""Build v5.3.17 UART upgrade fix on frozen v5.3.16; no hardware access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import build_spi_gui_cs_fix_release as dependencies_builder
from build_ch341_gui_release import ROOT, CURRENT, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, sha, files

BASE = ROOT / "release/TacAI_H3_v5_3_16_CH341A_20261019"
OUT = ROOT / "release/TacAI_H3_v5_3_17_UART_UpgradeFix_20261019"
PACKAGE = OUT.with_suffix(".zip")
WORK = ROOT / "tmp/uart_upgrade_fix_20261019_build"
NAME = "TacAI H3 v5.3.17 UART Upgrade Fix"
CHANGED = {"main.py", "firmware_updater.py", "iap_protocol.py", "test_spi_monitor_ui.py"}
ADDED = {"test_uart_upgrade_recovery.py", "test_uart_iap_protocol.py"}
TESTS = ["test_ch341_spi", "test_ch347_spi", "test_spi_iap_protocol", "test_spi_monitor_ui",
         "test_uart_upgrade_recovery", "test_uart_iap_protocol"]


def main():
    assert not OUT.exists() and not WORK.exists() and not PACKAGE.exists(), "Never replace a delivered release"
    old_manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    assert sha((BASE / old_manifest["exe"]).read_bytes()) == old_manifest["exe_sha256"]
    with zipfile.ZipFile(BASE / "source.zip") as archive:
        baseline = {name: archive.read(name) for name in archive.namelist()}
    assert not (ADDED & set(baseline))
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
    changed = {n for n, data in baseline.items() if (source / n).read_bytes() != data}
    assert changed == CHANGED, changed
    for name, expected in (("CH341DLLA64.DLL", CH341_SHA), ("CH347DLLA64.dll", CH347_SHA),
                           ("TacAI_H3_App_SPI_rxirq_v5.tacai", APP_SHA)):
        assert sha((source / name).read_bytes()) == expected
    for name in PROFILES:
        assert (BASE / name).read_bytes() == baseline[name]
        shutil.copy2(BASE / name, OUT / name)
    dependencies_builder.WORK = WORK
    dependencies, copied = dependencies_builder.isolated_optional_dependencies()
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
               TACAI_UPGRADE_LOG_DIR=str(WORK / "fake_upgrade_logs"),
               PYTHONPATH=os.pathsep.join((str(source), str(dependencies))),
               PYINSTALLER_CONFIG_DIR=str(WORK / "pyinstaller_cache"))
    print(f"Frozen source: {source}; checking UART faults and all adapters with fakes", flush=True)
    with (OUT / "offline_tests.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", *TESTS, "-v"], cwd=source, env=env,
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    print("Frozen offline tests passed; building EXE", flush=True)
    command = [sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--name", NAME, "--distpath", str(OUT), "--workpath", str(WORK / "work"),
               "--specpath", str(WORK), "--paths", str(source), "--paths", str(dependencies),
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
    hashes = {name: sha(path.read_bytes()) for name, path in sorted(files(source).items())}
    (OUT / "source_sha256.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    with zipfile.ZipFile(OUT / "source.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files(source).items()):
            archive.write(path, name)
    shutil.copy2(source / "TacAI_H3_App_SPI_rxirq_v5.tacai", OUT / "TacAI_H3_App_SPI_rxirq_v5.tacai")
    (OUT / "先看这里.txt").write_text(
        "TacAI H3 v5.3.17 UART 升级修复 — 2026-10-19\n\n"
        "解压整个文件夹，运行 TacAI H3 v5.3.17 UART Upgrade Fix.exe。\n"
        "本版修复 UART 升级失败自动回到 BEGIN、反复擦除写入的问题。\n"
        "仅串口连接/HELLO 同步允许自动重试；一旦进入擦写，任何写入、校验、启动确认或关闭错误都会停止。\n"
        "接收延迟旧帧或CRC错误帧时，在原请求时限内继续寻找匹配ACK，不重发Flash命令。\n"
        "失败原因和完成状态持续显示，并保存完整阶段日志；不再被确认口令提示覆盖。\n\n"
        "UART：选实际串口→固件升级→核对默认v5镜像→按窗口确认后启动。\n"
        "开始时保持USB连接；仅未同步阶段可重新上电传感器。\n"
        "出现擦除/写入进度后保持供电及USB，等待本次完成或明确报错，不要反复拔插。\n"
        "若失败，请提供升级窗口下面的日志文件；日志也在EXE同目录logs\\upgrade。\n"
        "若提示镜像已校验但未收到启动确认，请先重新连接检查数据，勿盲目重复刷写。\n\n"
        "默认UART/SPI镜像均为已交付的 h3_spi_20261016_rxirq_v5（容器可通过UART或SPI传送）。\n"
        "传感器v5固件未更改；本版保留CH341A/CH347F SPI、标定、补偿与记录逻辑。\n"
        "已通过离线错误注入、协议和GUI回归；未在真实设备上执行本次升级。\n", encoding="utf-8-sig")
    shutil.copy2(Path(__file__), OUT / Path(__file__).name)
    exe = OUT / (NAME + ".exe")
    release = dict(version="5.3.17-uart-upgrade-fix", date="2026-10-19", exe=exe.name,
                   exe_sha256=sha(exe.read_bytes()), baseline=BASE.name,
                   baseline_zip_sha256=sha((BASE / "source.zip").read_bytes()),
                   changed_sources=sorted(CHANGED), added_sources=sorted(ADDED),
                   all_other_sources_byte_identical=True, calibration_profiles_unchanged=True,
                   sensor_firmware_changed=False, hardware_accessed=False, hardware_validation="pending",
                   default_uart_and_spi_image_sha256=APP_SHA, offline_dependency_sources=copied,
                   no_automatic_flash_replay=True, source_fake_tests=TESTS,
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
