"""Package a bounded CH347/J-Link GPIO observation; never accesses hardware here."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release/spi_gpio_witness_20261016"
WORK = ROOT / "tmp/spi_gpio_witness_build"
DLL = ROOT / "release/TacAI_H3_v5_3_7_CH347F_DetectionFix_20261015/CH347DLLA64.dll"
EXE_NAME = "H3_SPI_GPIO_Witness"
RELEASE_ZIP = ROOT / "release/H3_SPI_GPIO_Witness_20261016.zip"
RELEASE_VERSION = 1
RELEASE_NOTES = ""
EXTRA_SOURCE_FILES = []
EXTRA_SELF_TESTS = []
EXTRA_RELEASE_FILES = []
README_OVERRIDE = None


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert sha(DLL) == "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "tools"), str(ROOT / "pc/TfgMonitorPython_H3_v5.0")))
    env["PYINSTALLER_CONFIG_DIR"] = str(WORK / "cache")
    for name in ("jlink_gpio_reader.py", "spi_gpio_witness.py", *EXTRA_SELF_TESTS):
        subprocess.run([sys.executable, str(ROOT / "tools" / name), "--self-test"], check=True, env=env)
    for pattern in ("test_jlink_gpio_reader.py", "test_spi_gpio_witness.py"):
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"),
                        "-p", pattern], check=True, env=env)
    with (WORK / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
            "--name", EXE_NAME, "--distpath", str(OUTPUT),
            "--workpath", str(WORK / "work"), "--specpath", str(WORK),
            "--paths", str(ROOT / "tools"), "--paths", str(ROOT / "pc/TfgMonitorPython_H3_v5.0"),
            "--add-binary", str(DLL) + os.pathsep + ".", str(ROOT / "tools/spi_gpio_witness.py"),
        ], check=True, env=env, stdout=log, stderr=subprocess.STDOUT)
    executable = OUTPUT / f"{EXE_NAME}.exe"
    subprocess.run([str(executable), "--self-test"], check=True, env=env)
    shutil.copy2(DLL, OUTPUT / DLL.name)
    readme = """H3 SPI 输入状态对照工具 — 2026-10-16

保持目前的v5固件、CH347 SPI接线和J-Link SWD接线，不用重新烧录或改线。
关闭J-Flash、J-Link Commander、Demo、UART日志工具和其他占用设备的软件。
解压后双击 H3_SPI_GPIO_Witness.exe，等待完成，把logs下本次目录整个压缩发来。
电脑需已安装SEGGER J-Link软件；使用其安装目录中的64位DLL，不附带或安装驱动。

工作方式：CH347在片选释放期间输出有限的全00/全FF对照段；J-Link同时读取
STM32 GPIO输入状态，观察PA4/MOSI是否跟随。不发送传感器协议命令，不进入IAP。
不擦写固件，不修改目标寄存器，不发复位、暂停或恢复运行命令。
若目标原本暂停，请先用原烧录软件正常复位运行，再关闭烧录软件运行本工具。
读取失败会保留错误，不能把失败读取当作0。未观察到某电平也不直接判定硬件故障。
日志记录原始采样、SDK调用窗口和配置，结论受USB/SWD异步采样时序限制。

此工具的源码与EXE仅通过模拟接口自检，实机结果待用户运行。
如不能自动找到J-Link DLL，可在命令行加 --jlink-dll "完整的JLink_x64.dll路径"。

方法依据：
ST AN4899（AF模式仍可读GPIO输入）：
https://www.st.com/resource/en/application_note/an4899-stm32-microcontroller-gpio-configuration-for-hardware-settings-and-lowpower-consumption-stmicroelectronics.pdf
SEGGER Cortex-M后台读取： https://kb.segger.com/Memory_accesses
禁止失败后复位重试： https://kb.segger.com/J-Link_Command_Strings#InhibitConnectRetries
"""
    if README_OVERRIDE is not None:
        readme = README_OVERRIDE
    readme = readme.replace("H3_SPI_GPIO_Witness.exe", f"{EXE_NAME}.exe")
    if RELEASE_NOTES:
        readme += "\n" + RELEASE_NOTES + "\n"
    (OUTPUT / "README_运行方法.txt").write_text(readme, encoding="utf-8-sig")
    for extra in EXTRA_RELEASE_FILES:
        shutil.copy2(extra, OUTPUT / extra.name)
    source_files = [Path(__file__), ROOT / "tools/spi_gpio_witness.py", ROOT / "tools/jlink_gpio_reader.py",
                    ROOT / "pc/TfgMonitorPython_H3_v5.0/ch347_spi.py", ROOT / "tests/test_jlink_gpio_reader.py",
                    ROOT / "tests/test_spi_gpio_witness.py"]
    source_files += EXTRA_SOURCE_FILES
    with zipfile.ZipFile(OUTPUT / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    manifest = {
        "tool_version": RELEASE_VERSION,
        "purpose": "bounded CH347 00/FF output with J-Link GPIO background observation",
        "firmware_flashes": 0, "sensor_protocol_commands": 0, "uart_commands": 0,
        "target_reset_commands": 0, "target_halt_commands": 0, "target_write_commands": 0,
        "cs_policy": "deasserted high throughout stimulus", "segger_dll_redistributed": False,
        "source_self_tests": "passed", "frozen_exe_self_test": "passed", "hardware_validation": "pending",
        "files": {p.name: {"size": p.stat().st_size, "sha256": sha(p)}
                  for p in sorted(OUTPUT.iterdir()) if p.is_file() and p.name != "manifest.json"},
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    target = RELEASE_ZIP
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for path in OUTPUT.iterdir():
            if path.is_file():
                assert archive.read(path.name) == path.read_bytes()
    print(json.dumps({"zip": str(target), "bytes": target.stat().st_size,
                      "hardware_accessed_by_build": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
