"""Freeze the CH341A GUI addition on the immutable delivered v5.3.15 baseline.

Uses offline fakes only; never opens hardware, runs a supplied driver installer,
or launches the output EXE. Existing calibration/model files stay byte identical.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import build_spi_gui_cs_fix_release as previous_builder

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "release/TacAI_H3_v5_3_15_SPI_CSFix_20261019"
CURRENT = ROOT / "pc/TfgMonitorPython_H3_v5.0"
OUT = ROOT / "release/TacAI_H3_v5_3_16_CH341A_20261019"
WORK = ROOT / "tmp/ch341_gui_20261019_build"
PACKAGE = OUT.with_suffix(".zip")
NAME = "TacAI H3 v5.3.16 CH341A Support"
CHANGED = {"main.py", "firmware_updater.py", "test_spi_monitor_ui.py"}
ADDED = {"ch341_spi.py", "test_ch341_spi.py", "CH341DLLA64.DLL", "TacAI_H3_App_SPI_rxirq_v5.tacai"}
PROFILES = previous_builder.PROFILES
CH341_SHA = "0eeed57d53f0ff9b6daaaab87207ff90c2e03762441aba5ed0904b3f7d5f4737"
CH347_SHA = previous_builder.EXPECTED_DLL
APP_SHA = "c5e09fd9a30a4da4916a774da45c7b94ee9ba88f511b7c7a2b04b90b4f944d42"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def files(directory):
    return {p.relative_to(directory).as_posix(): p for p in directory.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def main():
    assert not OUT.exists() and not WORK.exists() and not PACKAGE.exists(), "Never overwrite a release"
    manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    assert sha((BASE / manifest["exe"]).read_bytes()) == manifest["exe_sha256"]
    with zipfile.ZipFile(BASE / "source.zip") as archive:
        baseline = {name: archive.read(name) for name in archive.namelist()}
    assert not (set(baseline) & ADDED)
    OUT.mkdir(parents=True)
    WORK.mkdir(parents=True)
    frozen = OUT / "source"
    for name, data in baseline.items():
        target = (frozen / name).resolve()
        assert target.is_relative_to(frozen.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for name in CHANGED | (ADDED - {"TacAI_H3_App_SPI_rxirq_v5.tacai"}):
        shutil.copy2(CURRENT / name, frozen / name)
    shutil.copy2(ROOT / "release/spi_rxirq_v5_20261016/TacAI_H3_App_SPI_rxirq_v5.tacai",
                 frozen / "TacAI_H3_App_SPI_rxirq_v5.tacai")
    assert sha((frozen / "CH341DLLA64.DLL").read_bytes()) == CH341_SHA
    assert sha((frozen / "CH347DLLA64.dll").read_bytes()) == CH347_SHA
    assert sha((frozen / "TacAI_H3_App_SPI_rxirq_v5.tacai").read_bytes()) == APP_SHA
    comparison = {}
    for name, data in baseline.items():
        actual = (frozen / name).read_bytes()
        comparison[name] = dict(before=sha(data), after=sha(actual), changed=actual != data)
        assert name in CHANGED or actual == data, f"Unexpected source change: {name}"
    assert {n for n, v in comparison.items() if v["changed"]} == CHANGED
    (OUT / "baseline_comparison.json").write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    for name in PROFILES:
        assert (BASE / name).read_bytes() == baseline[name]
        shutil.copy2(BASE / name, OUT / name)
    previous_builder.WORK = WORK
    dependencies, copied = previous_builder.isolated_optional_dependencies()
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=os.pathsep.join((str(frozen), str(dependencies))),
               PYINSTALLER_CONFIG_DIR=str(WORK / "pyinstaller_cache"))
    print(f"Frozen: {frozen}; fake backend, protocol and offscreen GUI checks", flush=True)
    with (OUT / "offline_tests.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", "test_ch341_spi", "test_ch347_spi",
                        "test_spi_iap_protocol", "test_spi_monitor_ui", "-v"], cwd=frozen, env=env,
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    print("Offline checks passed. Building EXE...", flush=True)
    command = [sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--name", NAME, "--distpath", str(OUT), "--workpath", str(WORK / "work"),
               "--specpath", str(WORK), "--paths", str(frozen), "--paths", str(dependencies),
               "--exclude-module", "pandas", "--exclude-module", "scipy", "--collect-data", "openpyxl",
               "--collect-submodules", "openpyxl", "--runtime-hook", str(frozen / "qt_platform_runtime_hook.py"),
               "--add-binary", f"{frozen / 'CH347DLLA64.dll'};.",
               "--add-binary", f"{frozen / 'CH341DLLA64.DLL'};.",
               "--add-data", f"{frozen / 'assets'};assets"]
    for name in (*PROFILES, "TacAI_H3_App_SPI_rxirq_v5.tacai", "TacAI_H3_App_v1_3_VTORFIX.tacai"):
        command += ["--add-data", f"{frozen / name};."]
    command.append(str(frozen / "main.py"))
    with (OUT / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=frozen, env=env, check=True, stdout=log, stderr=subprocess.STDOUT)
    for name, data in baseline.items():
        assert name in CHANGED or (frozen / name).read_bytes() == data, name
    for name in PROFILES:
        assert (OUT / name).read_bytes() == baseline[name]
    source_hashes = {name: sha(path.read_bytes()) for name, path in sorted(files(frozen).items())}
    (OUT / "source_sha256.json").write_text(json.dumps(source_hashes, indent=2), encoding="utf-8")
    with zipfile.ZipFile(OUT / "source.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files(frozen).items()):
            archive.write(path, name)
    shutil.copy2(frozen / "TacAI_H3_App_SPI_rxirq_v5.tacai", OUT / "TacAI_H3_App_SPI_rxirq_v5.tacai")
    (OUT / "先看这里.txt").write_text(
        "TacAI H3 v5.3.16 CH341A 支持版 — 2026-10-19\n\n"
        "解压整个文件夹，运行 TacAI H3 v5.3.16 CH341A Support.exe。\n"
        "传感器继续使用已烧录的 STM32G031G8 v5 固件，无需重刷。\n"
        "关闭占用适配器的其他程序。CH341A 模块切到 I2C/SPI；此 LC 模块两组电压跳帽均设 3V3。\n"
        "CH341A MOSI→PA4、MISO→PA3、SCK→PA0、CS0→PA8、GND共地。不要接到TXD/RXD。\n"
        "传感器保持现有供电，不并接两路电源。\n"
        "通信接口选择 CH341A SPI → 检测设备 → 选择实际序号 → 连接设备。\n"
        "SPI 固定 Mode0 / MSB / 8bit / CS0；CH341A 时钟由硬件固定，界面不可调。\n"
        "若检测不到设备，安装 WCH 官方 CH341PAR 同步接口驱动（下载页见下方），重新插入并检测。\n"
        "官方：https://www.wch.cn/downloads/CH341PAR_ZIP.html\n\n"
        "CH347F SPI 和 UART 原有选项保留；CH347 片选修复、标定、补偿、记录模型保留。\n"
        "SPI 升级默认选择本包 v5 .tacai，需传感器已有双接口 Bootloader；首次部署仍用 J-Link。\n"
        "上位机新增CH341驱动，不修改传感器协议或固件。已做离线驱动、协议及GUI回归；\n"
        "CH341A 实物采集和SPI升级尚待现场验证，离线测试不代表实物验收。\n", encoding="utf-8-sig")
    shutil.copy2(Path(__file__), OUT / Path(__file__).name)
    exe = OUT / (NAME + ".exe")
    release = dict(version="5.3.16-ch341a-support", date="2026-10-19", exe=exe.name,
                   exe_sha256=sha(exe.read_bytes()), baseline=BASE.name,
                   baseline_zip_sha256=sha((BASE / "source.zip").read_bytes()),
                   changed_sources=sorted(CHANGED), added_sources=sorted(ADDED),
                   all_other_sources_byte_identical=True, calibration_profiles_unchanged=True,
                   sensor_firmware_changed=False, hardware_accessed=False, hardware_validation="pending",
                   dll_sha256={"CH341DLLA64.DLL": CH341_SHA, "CH347DLLA64.dll": CH347_SHA},
                   spi_default_image_sha256=APP_SHA, offline_dependency_sources=copied,
                   source_fake_tests="test_ch341_spi + test_ch347_spi + test_spi_iap_protocol + test_spi_monitor_ui",
                   files={p.name: dict(bytes=p.stat().st_size, sha256=sha(p.read_bytes()))
                          for p in sorted(OUT.iterdir()) if p.is_file()})
    (OUT / "manifest.json").write_text(json.dumps(release, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(PACKAGE, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUT.iterdir()):
            if path.is_file():
                archive.write(path, OUT.name + "/" + path.name)
    print(json.dumps(dict(package=str(PACKAGE), sha256=sha(PACKAGE.read_bytes()), exe_sha256=release["exe_sha256"]), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
