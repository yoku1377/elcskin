"""Package a read-only audit of the CH347 bridge's raw SPI configuration."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PC = ROOT / "pc/TfgMonitorPython_H3_v5.0"
OUTPUT = ROOT / "release/ch347_spi_config_audit_20261016"
WORK = ROOT / "tmp/ch347_spi_config_audit_build"
DLL = ROOT / "release/TacAI_H3_v5_3_7_CH347F_DetectionFix_20261015/CH347DLLA64.dll"
DLL_SHA256 = "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375"
TARGET = ROOT / "release/H3_SPI_ConfigAudit_20261016.zip"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_info(path):
    return {"size": path.stat().st_size, "sha256": sha(path)}


def verified_zip(target, entries):
    """Write only the listed files, then verify names, CRCs and exact contents."""
    names = [name for _, name in entries]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate ZIP member names")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, name in entries:
            archive.write(path, name)
    with zipfile.ZipFile(target) as archive:
        if archive.namelist() != names or archive.testzip() is not None:
            raise RuntimeError(f"ZIP integrity check failed: {target}")
        for path, name in entries:
            if archive.read(name) != path.read_bytes():
                raise RuntimeError(f"ZIP content mismatch: {name}")


def main():
    source = ROOT / "tools/ch347_spi_config_audit.py"
    sources = [source, Path(__file__).resolve(), PC / "ch347_spi.py"]
    for path in [DLL, *sources]:
        if not path.is_file():
            raise FileNotFoundError(path)
    if sha(DLL) != DLL_SHA256:
        raise RuntimeError("The CH347 DLL does not match the audited binary")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYINSTALLER_CONFIG_DIR"] = str(WORK / "cache")
    subprocess.run([sys.executable, str(source), "--self-test"], check=True, env=env)
    with (WORK / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
            "--name", "H3_SPI_ConfigAudit", "--distpath", str(OUTPUT),
            "--workpath", str(WORK / "work"), "--specpath", str(WORK),
            "--paths", str(PC), "--paths", str(ROOT / "tools"),
            "--add-binary", str(DLL) + os.pathsep + ".", str(source),
        ], check=True, env=env, stdout=log, stderr=subprocess.STDOUT)
    executable = OUTPUT / "H3_SPI_ConfigAudit.exe"
    subprocess.run([str(executable), "--self-test"], check=True, env=env)
    packaged_dll = OUTPUT / DLL.name
    shutil.copy2(DLL, packaged_dll)
    readme = OUTPUT / "README_先看这里.txt"
    readme.write_text("""CH347 SPI 原始配置只读检查 — 2026-10-16

保持当前供电及接线，不需要改线、重刷或使用 J-Link。
关闭占用 CH347 的其他工具后，双击 H3_SPI_ConfigAudit.exe。
请尽量保留刚才 SPI 测试后的状态，不拔插 CH347，不重新初始化 SPI。

本工具只读取 CH347 桥芯片的原始26字节 SPI 配置，并保存原始数据与解释。
它不调用 SPI_Init，不控制 CS，不产生 SCK，不向传感器发送目标命令，
不操作 Flash，也不打开或收发 UART。

完成后，把本次日志目录中的 summary.json 发回；若有错误，也保留该文件。
拿到原始配置后再判断 SPI 方向设置，不预先认定 RxOnly 或任何保留配置
就是本次全 FF 的原因。本工具不会修改设置，也不承诺修复全 FF。

发布流程要求源码 --self-test 和冻结 EXE --self-test 均通过；自检使用模拟
接口，不连接硬件。实物配置读取与故障原因验证仍待现场运行结果确认。
source.zip 保留工具源码、打包脚本及 ch347_spi.py；manifest.json 记录文件校验值。
""", encoding="utf-8-sig")
    source_archive = OUTPUT / "source.zip"
    verified_zip(source_archive, [(path, path.relative_to(ROOT).as_posix()) for path in sources])
    payloads = [executable, packaged_dll, readme, source_archive]
    manifest = {
        "tool": "H3_SPI_ConfigAudit",
        "purpose": "read-only raw 26-byte CH347 bridge SPI configuration audit",
        "dll_sha256": DLL_SHA256,
        "spi_initializations": 0,
        "cs_operations": 0,
        "spi_transfers": 0,
        "target_commands": 0,
        "flash_operations": 0,
        "uart_operations": 0,
        "source_self_test": "passed",
        "exe_self_test": "passed",
        "hardware_validation": "pending",
        "source_files": {path.relative_to(ROOT).as_posix(): file_info(path) for path in sources},
        "files": {path.name: file_info(path) for path in payloads},
    }
    manifest_path = OUTPUT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    verified_zip(TARGET, [(path, path.name) for path in [*payloads, manifest_path]])
    print(json.dumps({"zip": str(TARGET), **file_info(TARGET), "zip_validation": "passed"},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
