"""Freeze and build CH347F detection fixes without changing calibration."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pc/TfgMonitorPython_H3_v5.0"
PREVIOUS = ROOT / "release/TacAI_H3_v5_3_6_SPI_ConfigFix_20261015"
OUTPUT = ROOT / "release/TacAI_H3_v5_3_7_CH347F_DetectionFix_20261015"
NAME = "TacAI H3 v5.3.7 CH347F Detection Fix Front Back Candidate"
ALLOWED = {"main.py", "ch347_spi.py", "spi_iap_protocol.py", "test_ch347_spi.py",
           "test_spi_monitor_ui.py", "test_spi_iap_protocol.py"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = json.loads((PREVIOUS / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in baseline["source_hashes"].items():
        if name not in ALLOWED and sha(SOURCE / name) != expected:
            raise RuntimeError(f"Unexpected baseline change: {name}")
    for name in ("local_force_profile.json", "back_force_profile.json"):
        if sha(SOURCE / name) != baseline["files"][name]["sha256"]:
            raise RuntimeError(f"Calibration profile changed: {name}")
    frozen = OUTPUT / "source"
    frozen.mkdir(parents=True, exist_ok=True)
    for path in SOURCE.glob("*.py"):
        shutil.copy2(path, frozen / path.name)
    shutil.copy2(SOURCE / "requirements.txt", frozen)
    for name in ("assets", "testdata"):
        shutil.copytree(SOURCE / name, frozen / name, dirs_exist_ok=True)
    resources = ("local_force_profile.json", "back_force_profile.json", "CH347DLLA64.dll",
                 "CH341PAR.EXE", "TacAI_H3_App_SPI_v2.tacai", "TacAI_H3_App_v1_3_VTORFIX.tacai")
    for name in resources:
        shutil.copy2(PREVIOUS / name, OUTPUT / name)
        assert sha(OUTPUT / name) == baseline["files"][name]["sha256"]
    for name in ("local_force_profile.json", "back_force_profile.json"):
        shutil.copy2(OUTPUT / name, frozen / name)
    for name in ("CH347_DLL_120_ENUMERATION.md", "CH347_DLL_120_READBACK.md"):
        shutil.copy2(ROOT / "docs" / name, OUTPUT)
    (OUTPUT / "先看这里.txt").write_text(
        "TacAI H3 v5.3.7 CH347F 接口检测修复版\n\n"
        "修复正常 CH347F 接口被旧检测规则误排除的问题：\n"
        "随包 DLL 对复合 SPI 接口返回 FuncType=3、Mode=0/1、IF=4/1，旧规则漏识别。\n"
        "v5.3.6 的 MSB 参数回读修复保留；前后校准模型及参数完全保留，默认关闭。\n\n"
        "解压后运行本目录 v5.3.7 EXE，选 CH347F SPI，点检测设备，选择检测到的序号。\n"
        "初次联调使用 468750 Hz。运行日志会记录每个接口的原始类型与接受/排除原因。\n"
        "检测只查询 USB 元数据，不发 SPI 采集、升级或 Flash 命令。\n\n"
        "若提示 USB 已打开但没有有效 SPI 快照，说明已进入传感器通信阶段：\n"
        "1. 先确认实际测试的 H3 已刷 TacAI_H3_App_SPI_v2.tacai；原 UART 应用不响应 SPI。\n"
        "2. 再核对传感器供电、共地、CS0→PA8、SCK→PA0、MISO→PA3、MOSI→PA4。\n"
        "3. 超时日志新增轮询次数、全 FF 次数和旧帧次数；复制新版完整日志用于继续定位。\n"
        "采集只要求 SPI 应用，SPI 升级另需首次通过 SWD 部署双接口 Bootloader。\n\n"
        "本次没有安装驱动或刷写硬件，SPI 采集/IAP 及校准现场验收仍待实机验证。\n",
        encoding="utf-8-sig")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--name", NAME, "--distpath", str(OUTPUT), "--workpath", str(OUTPUT / "build"),
               "--specpath", str(OUTPUT / "build"), "--exclude-module", "pandas",
               "--collect-data", "openpyxl", "--collect-submodules", "openpyxl",
               "--runtime-hook", str(frozen / "qt_platform_runtime_hook.py"),
               "--add-binary", f"{OUTPUT / 'CH347DLLA64.dll'};.",
               "--add-data", f"{frozen / 'assets'};assets"]
    for name in resources:
        if name.endswith((".json", ".tacai")):
            command.extend(("--add-data", f"{OUTPUT / name};."))
    command.append(str(frozen / "main.py"))
    with (OUTPUT / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=frozen, check=True, stdout=log, stderr=subprocess.STDOUT)
    with zipfile.ZipFile(OUTPUT / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in frozen.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(frozen))
    manifest = {
        "version": "5.3.7-spi-interface-detection-fix-front-back-candidate",
        "hardware_validation": "pending", "baseline": "5.3.6", "changed_files": sorted(ALLOWED),
        "calibration_profiles_and_all_other_baseline_python_sources_verified_unchanged": True,
        "source_hashes": {p.name: sha(p) for p in frozen.glob("*.py")},
        "files": {p.name: {"size": p.stat().st_size, "sha256": sha(p)}
                  for p in OUTPUT.iterdir() if p.is_file() and p.name not in ("manifest.json", "build.log")},
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
