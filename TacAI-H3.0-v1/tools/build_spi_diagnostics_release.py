"""Freeze the v5.3.3 calibration baseline with the SPI connection diagnostics."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pc/TfgMonitorPython_H3_v5.0"
OUTPUT = ROOT / "release/TacAI_H3_v5_3_4_SPI_Diagnostics_20261015"
NAME = "TacAI H3 v5.3.4 SPI Diagnostics Front Local Candidate"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = json.loads((ROOT / "release/TacAI_H3_v5_3_3_FrontLocalCandidate_20261015/source_sha256.json").read_text(encoding="utf-8"))
    for name in ("local_force_profile.json", "h3_v3_3_force_model.py", "local_high_force.py",
                 "local_state.py", "regional_ard_force.py", "regional_high_force.py"):
        if sha(SOURCE / name) != baseline[name]:
            raise RuntimeError(f"Calibration baseline changed: {name}")
    frozen = OUTPUT / "source"
    frozen.mkdir(parents=True, exist_ok=True)
    for path in SOURCE.glob("*.py"):
        shutil.copy2(path, frozen / path.name)
    shutil.copy2(SOURCE / "local_force_profile.json", frozen)
    shutil.copytree(SOURCE / "assets", frozen / "assets", dirs_exist_ok=True)
    dll = ROOT.parent / "SPI_预联调_20261014/references/sdk/CH347DLLA64.dll"
    shutil.copy2(dll, OUTPUT / dll.name)
    shutil.copy2(ROOT.parent / "SPI_预联调_20261015/driver/CH341PAR.EXE", OUTPUT / "CH341PAR.EXE")
    images = (ROOT / "release/TacAI_H3_App_v1_3_VTORFIX.tacai",
              ROOT / "release/spi_trial_20261014/TacAI_H3_App_SPI_v2.tacai")
    for path in images:
        shutil.copy2(path, OUTPUT / path.name)
    (OUTPUT / "先安装驱动_再连接.txt").write_text(
        "TacAI H3 v5.3.4 SPI 连接诊断版\n\n"
        "设备管理器中 UART+SPI+I2C+JTAG 位于其他设备、带黄色叹号，说明驱动未正常工作。\n"
        "请将 CH341PAR.EXE 复制到实际测试电脑安装，再拔插 USB。安装程序具有沁恒有效数字签名。\n"
        "CH347F 的 COM 串口与 SPI 同步接口使用不同驱动；只有 COM 口还不能使用 SPI。\n"
        "如果安装后仍有黄色叹号，请查看设备属性中的错误代码与硬件 ID。\n\n"
        "打开本目录的 v5.3.4 EXE，选择 CH347F SPI，点击检测设备。\n"
        "检测只打开/读取 USB 接口信息/关闭，不初始化 SPI、不发送传感器命令。\n"
        "检测到接口后按实际序号连接，首次联调时钟选 468750 Hz。\n"
        "USB 无法打开：检查驱动、USB 连接以及其他软件占用；此时尚未与传感器通信。\n"
        "USB 已打开但无有效快照：再检查传感器供电、共地、接线及 SPI 应用固件。\n"
        "采集需 TacAI_H3_App_SPI_v2.tacai；SPI 固件升级另需首次通过 SWD 部署双接口 Bootloader。\n"
        "本包不自动安装驱动或刷写固件；原 v5.3.3 安装驱动后也可直接重试连接。\n\n"
        "保留 v5.3.3 的模型与局部校准参数，局部校正默认关闭，现场标定验收仍待完成。\n"
        "硬件 SPI 采集和升级待实机验证。\n\n"
        "驱动来源：Waveshare USB-TO-UART-I2C-SPI-JTAG-Demo / Windows/Driver/CH341PAR.EXE\n"
        "原始资料：https://www.waveshare.com/wiki/USB_TO_UART/I2C/SPI/JTAG\n"
        "沁恒驱动说明：https://www.wch.cn/downloads/CH341PAR_ZIP.html\n"
        "驱动 SHA256: 765fb595605cca6f4cf75d9aecc33a04415488c019b54cdaac288ae0388ade5c\n",
        encoding="utf-8-sig")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--name", NAME, "--distpath", str(OUTPUT), "--workpath", str(OUTPUT / "build"),
               "--specpath", str(OUTPUT / "build"), "--exclude-module", "pandas",
               "--collect-data", "openpyxl", "--collect-submodules", "openpyxl",
               "--runtime-hook", str(frozen / "qt_platform_runtime_hook.py"),
               "--add-binary", f"{OUTPUT / dll.name};.",
               "--add-data", f"{frozen / 'assets'};assets",
               "--add-data", f"{frozen / 'local_force_profile.json'};."]
    for image in images:
        command.extend(("--add-data", f"{OUTPUT / image.name};."))
    command.append(str(frozen / "main.py"))
    with (OUTPUT / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=frozen, check=True, stdout=log, stderr=subprocess.STDOUT)
    with zipfile.ZipFile(OUTPUT / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in frozen.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(frozen))
    manifest = {
        "version": "5.3.4-spi-diagnostics-front-local-candidate", "hardware_validation": "pending",
        "calibration_baseline": "5.3.3", "profile_sha256": sha(frozen / "local_force_profile.json"),
        "source_hashes": {path.name: sha(path) for path in frozen.glob("*.py")},
        "files": {path.name: {"sha256": sha(path), "size": path.stat().st_size}
                  for path in OUTPUT.iterdir() if path.is_file() and path.name not in ("manifest.json", "build.log")},
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
