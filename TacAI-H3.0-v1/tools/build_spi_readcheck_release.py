"""Package read-only SPI snapshots with simultaneous UART evidence."""
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
OUTPUT = ROOT / "release/spi_readcheck_v2_20261016"
WORK = ROOT / "tmp/spi_readcheck_v2_build"
DLL = ROOT / "release/TacAI_H3_v5_3_7_CH347F_DetectionFix_20261015/CH347DLLA64.dll"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    assert sha(DLL) == "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375"
    source = ROOT / "tools/spi_readcheck.py"
    subprocess.run([sys.executable, str(source), "--self-test"], check=True)
    env = os.environ.copy()
    env["PYINSTALLER_CONFIG_DIR"] = str(WORK / "cache")
    with (WORK / "build.log").open("w", encoding="utf-8") as log:
        result = subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
            "--name", "H3_SPI_ReadCheck_v2", "--distpath", str(OUTPUT),
            "--workpath", str(WORK / "work"), "--specpath", str(WORK),
            "--paths", str(PC), "--paths", str(ROOT / "tools"),
            "--add-binary", str(DLL) + os.pathsep + ".", str(source),
        ], env=env, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"PyInstaller failed; see {WORK / 'build.log'}")
    executable = OUTPUT / "H3_SPI_ReadCheck_v2.exe"
    subprocess.run([str(executable), "--self-test"], check=True)
    shutil.copy2(DLL, OUTPUT / DLL.name)
    readme = """H3 SPI 只读快照验证 v2 — 2026-10-16

前提：当前完整采集 v4 的 UART 已证实正常，本工具验证 SPI 的完整数据路径。
保持传感器供电及现有 SPI/UART 接线；关闭 CH347Demo 和其他 CH347 接收程序，释放设备。
解压后双击 H3_SPI_ReadCheck_v2.exe，自动匹配SPI接口和UART1。
SPI默认CS0（SDK称CS1），Mode0、468750Hz、MSB、8bit、低有效。
CS0/SCS0接传感器NSS PA8；若接的是另一片选，使用 --cs 1 或改用原定CS0接线。

程序只发cmd=0x20的快照请求，每次请求与536字节FF读取分开片选，自动检查CRC/序号。
尝试3个新序号，比较设备时间和采集帧计数；不会把重读同一旧响应当作持续采样。
UART1以115200接收，同时保存SPI操作前后的SPI_DIAG计数。
UART接收使用独立后台进程，与SPI隔离DLL缓存，避免同进程UART误用SPI设备路径。
不发送UART命令，不进入IAP，不擦写固件，不需要J-Link，也不需要重新上电。

完成后，把程序旁logs下本次目录里的summary.json和UART日志发回。
原始SPI事务也会保存，便于区分片选、收包、CRC、序号或快照状态的问题。
如果UART不可打开，工具仍会完成SPI检查，并保留UART错误说明。

本工具复用已修正CS电平的后端（传输开始为0，结束为1）。
已通过软件自检及模拟接口验证；实物SPI验收以本次运行结果为准。
"""
    (OUTPUT / "README_双击验证SPI.txt").write_text(readme, encoding="utf-8-sig")
    files = [source, ROOT / "tools/ch347_uart_backend.py", Path(__file__)]
    files += [PC / name for name in ("ch347_spi.py", "spi_iap_protocol.py", "iap_protocol.py")]
    files += [ROOT / "tools/tacai_image.py"]
    with zipfile.ZipFile(OUTPUT / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    manifest = {"tool_version": 2, "uart_isolation": "separate subprocess",
                "purpose": "cmd20 snapshots only; UART reception",
                "firmware_writes": 0, "uart_commands_sent": 0,
                "source_self_test": "passed", "exe_self_test": "passed",
                "hardware_validation": "pending",
                "files": {p.name: {"size": p.stat().st_size, "sha256": sha(p)}
                          for p in sorted(OUTPUT.iterdir()) if p.is_file() and p.name != "manifest.json"}}
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    target = ROOT / "release/H3_SPI_ReadCheck_v2_20261016.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    print(json.dumps({"zip": str(target), "bytes": target.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
