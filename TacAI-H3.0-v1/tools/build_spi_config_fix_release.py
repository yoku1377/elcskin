"""Build the SPI readback fix on the complete v5.3.5 calibration baseline."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pc/TfgMonitorPython_H3_v5.0"
OUTPUT = ROOT / "release/TacAI_H3_v5_3_6_SPI_ConfigFix_20261015"
NAME = "TacAI H3 v5.3.6 SPI Config Fix Front Back Candidate"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = json.loads((ROOT / "release/TacAI_H3_v5_3_5_FrontBackCandidate_20261015/source_sha256.json").read_text(encoding="utf-8"))
    changed = {"main.py", "ch347_spi.py", "test_ch347_spi.py"}
    frozen = OUTPUT / "source"
    frozen.mkdir(parents=True, exist_ok=True)
    for path in SOURCE.glob("*.py"):
        shutil.copy2(path, frozen / path.name)
    shutil.copy2(SOURCE / "requirements.txt", frozen)
    for name in ("local_force_profile.json", "back_force_profile.json"):
        shutil.copy2(SOURCE / name, frozen)
        shutil.copy2(SOURCE / name, OUTPUT)
    shutil.copytree(SOURCE / "assets", frozen / "assets", dirs_exist_ok=True)
    dll = ROOT.parent / "SPI_预联调_20261014/references/sdk/CH347DLLA64.dll"
    shutil.copy2(dll, OUTPUT / dll.name)
    shutil.copy2(ROOT.parent / "SPI_预联调_20261015/driver/CH341PAR.EXE", OUTPUT / "CH341PAR.EXE")
    images = (ROOT / "release/TacAI_H3_App_v1_3_VTORFIX.tacai",
              ROOT / "release/spi_trial_20261014/TacAI_H3_App_SPI_v2.tacai")
    for image in images:
        shutil.copy2(image, OUTPUT / image.name)
    for name, expected in baseline.items():
        path = frozen / name if (frozen / name).is_file() else OUTPUT / name
        if name not in changed and sha(path) != expected:
            raise RuntimeError(f"Unexpected baseline change: {name}")
    shutil.copy2(ROOT / "docs/CH347_DLL_120_READBACK.md", OUTPUT)
    (OUTPUT / "先看这里.txt").write_text(
        "TacAI H3 v5.3.6 SPI 参数回读修复版\n\n"
        "修复随包 WCH DLL 1.20 初始化与回读的 MSB 编码不同造成的误报。\n"
        "原先请求 MSB=1，但 DLL 回读 MSB=0，旧版会报 mode/clock/MSB 不一致。\n"
        "已按随包 DLL 的 SHA256 精确识别并解析，真实模式、时钟、位序错误仍会拦截。\n"
        "此修复对 SPI 采集和 SPI 固件升级都生效。原 v5.3.4/5.3.5 也包含旧判断。\n\n"
        "请在实际测试电脑解压后打开本目录 v5.3.6 EXE，关闭占用 CH347 的其他软件。\n"
        "选择 CH347F SPI，点击检测设备，选择检测到的序号；首次时钟选 468750 Hz。\n"
        "连接后日志会记录请求值、回读值、位序解析方式及 DLL SHA256。\n"
        "若仍失败，复制这次新的完整日志，以区分配置问题与传感器无响应。\n"
        "如果还未安装同步接口驱动，可在测试电脑运行 CH341PAR.EXE 后重新插拔 USB。\n\n"
        "前后区候选校正与 v5.3.5 一致，两份参数均保留，默认关闭；现场验收仍待完成。\n"
        "无需为了这条参数回读报错刷写传感器；传感器固件仍需支持 SPI 才能实际采集。\n"
        "本次没有安装驱动或刷写硬件，SPI 实机采集和 IAP 待现场验证。\n",
        encoding="utf-8-sig")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--name", NAME, "--distpath", str(OUTPUT), "--workpath", str(OUTPUT / "build"),
               "--specpath", str(OUTPUT / "build"), "--exclude-module", "pandas",
               "--collect-data", "openpyxl", "--collect-submodules", "openpyxl",
               "--runtime-hook", str(frozen / "qt_platform_runtime_hook.py"),
               "--add-binary", f"{OUTPUT / dll.name};.",
               "--add-data", f"{frozen / 'assets'};assets"]
    for name in ("local_force_profile.json", "back_force_profile.json", *(p.name for p in images)):
        command.extend(("--add-data", f"{OUTPUT / name};."))
    command.append(str(frozen / "main.py"))
    with (OUTPUT / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=frozen, check=True, stdout=log, stderr=subprocess.STDOUT)
    with zipfile.ZipFile(OUTPUT / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in frozen.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(frozen))
    manifest = {
        "version": "5.3.6-spi-config-fix-front-back-candidate", "hardware_validation": "pending",
        "calibration_baseline": "5.3.5", "baseline_changes": sorted(changed),
        "all_other_baseline_files_verified_unchanged": True,
        "front_profile_sha256": sha(frozen / "local_force_profile.json"),
        "back_profile_sha256": sha(frozen / "back_force_profile.json"),
        "source_hashes": {path.name: sha(path) for path in frozen.glob("*.py")},
        "files": {path.name: {"sha256": sha(path), "size": path.stat().st_size}
                  for path in OUTPUT.iterdir() if path.is_file() and path.name not in ("manifest.json", "build.log")},
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
