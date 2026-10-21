"""Build v5.3.19 CH341 revision fix with separately programmed resident fix."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile

import build_spi_gui_cs_fix_release as dependencies_builder
from build_uart_upgrade_fix_release import ROOT, CURRENT, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, sha, files, TESTS
from build_bootloader_uart_rxfix_v6 import OUT as FIRMWARE, FULL_NAME

BASE = ROOT / "release/TacAI_H3_v5_3_18_SPI_UpgradeFix_20261019"
OUT = ROOT / "release/TacAI_H3_v5_3_19_Upgrade_CH341Fix_20261019"
PACKAGE = OUT.with_suffix(".zip")
WORK = ROOT / "tmp/upgrade_ch341_revision_20261019_build"
NAME = "TacAI H3 v5.3.19 Upgrade and CH341 Fix"
CHANGED = {"main.py", "firmware_updater.py", "ch341_spi.py", "test_ch341_spi.py", "test_spi_monitor_ui.py"}
FULL_SHA = "fc738513dbc949b69e68643792e1fd22ce9d10fb68ac3d69a821f4322743eae5"


def main():
    assert not OUT.exists() and not WORK.exists() and not PACKAGE.exists(), "Never overwrite a release"
    prior = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    assert sha((BASE / prior["exe"]).read_bytes()) == prior["exe_sha256"]
    assert sha((FIRMWARE / FULL_NAME).read_bytes()) == FULL_SHA
    fw_manifest = json.loads((FIRMWARE / "manifest.json").read_text(encoding="utf-8"))
    assert fw_manifest["app_and_metadata_byte_identical"] and fw_manifest["bootloader_version"] == 6
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
               PYTHONPATH=os.pathsep.join((str(source), str(deps))),
               PYINSTALLER_CONFIG_DIR=str(WORK / "pyinstaller_cache"))
    print(f"Frozen source: {source}; running all adapter/protocol/UI regressions", flush=True)
    with (OUT / "offline_tests.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", *TESTS, "-v"], cwd=source, env=env,
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    print("Offline tests passed; building EXE", flush=True)
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
    for name in (FULL_NAME, "烧录说明.txt", "uart_rx_phase_tests.log"):
        shutil.copy2(FIRMWARE / name, OUT / name)
    shutil.copy2(FIRMWARE / "source.zip", OUT / "Bootloader_v6_source.zip")
    shutil.copy2(FIRMWARE / "manifest.json", OUT / "Bootloader_v6_manifest.json")
    (OUT / "先看这里.txt").write_text(
        "TacAI H3 v5.3.19 升级与CH341芯片兼容修复 — 2026-10-19\n\n"
        "一、CH341检测提示未识别SPI类型\n"
        "运行 TacAI H3 v5.3.19 Upgrade and CH341 Fix.exe，选择CH341A SPI并检测。\n"
        "本版接受已确认的IC0x32且PID5512设备；旧程序在SPI初始化前将其排除，与MOSI/MISO线序无关。\n"
        "非5512、无PID的0x32和未知芯片版本仍拒绝；未扩大到任意USB设备。\n"
        "CH341模块仍需I2C/SPI、3.3V模式，按模块真实MOSI/MISO引脚对接传感器。\n\n"
        "二、UART已经写入2048字节后DATA无响应\n"
        "已复现常驻Bootloader的2ms帧头截止缺陷。只换上位机不能更新该段固件，需J-Link/SWD部署一次本包完整BIN：\n"
        + FULL_NAME + "，起始0x08000000，目标STM32G031G8，烧录后Verify、Reset/Run。\n"
        "详见烧录说明.txt。需要保留设备配置时按页擦本镜像范围，不要整片擦除配置末页。\n"
        "应用仍为原v5，采集/力计算和有效启动元数据逐字节一致；变化位于常驻Bootloader。\n"
        "完整BIN必须通过SWD部署，不能在上位机中改后缀当.tacai使用。\n\n"
        "三、升级操作\n"
        "不是每次都要热插拔：只在未同步Bootloader时才可能需重置/重新上电传感器。\n"
        "出现擦除/写入进度后保持USB与供电。失败任务停止，日志保存在EXE同目录logs\\upgrade。\n"
        "SPI坏回包重读、UART禁止自动重擦、标定补偿和记录逻辑均保留。\n\n"
        "已通过离线GUI/协议/驱动回归及生产C帧头相位测试和ARM构建；实际UART升级、CH341收发仍待现场验证。\n",
        encoding="utf-8-sig")
    shutil.copy2(__file__, OUT / "build_upgrade_ch341_revision_release.py")
    exe = OUT / (NAME + ".exe")
    release = dict(version="5.3.19-upgrade-ch341-revision-fix", date="2026-10-19", exe=exe.name,
                   exe_sha256=sha(exe.read_bytes()), baseline=BASE.name,
                   changed_sources=sorted(CHANGED), all_other_sources_byte_identical=True,
                   calibration_profiles_unchanged=True, default_application_unchanged=True,
                   ch341_32_requires_pid="5512", resident_firmware_fix_requires_swd=True,
                   resident_full_bin=FULL_NAME, resident_full_sha256=FULL_SHA,
                   hardware_accessed=False, hardware_validation="pending", offline_dependency_sources=copied,
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
