"""Package the bounded request-path probe without changing sensor firmware."""
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
OUTPUT = ROOT / "release/spi_request_probe_v3_20261016"
WORK = ROOT / "tmp/spi_request_probe_v3_build"
DLL = ROOT / "release/TacAI_H3_v5_3_7_CH347F_DetectionFix_20261015/CH347DLLA64.dll"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    assert sha(DLL) == "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375"
    source = ROOT / "tools/spi_request_probe.py"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYINSTALLER_CONFIG_DIR"] = str(WORK / "cache")
    subprocess.run([sys.executable, str(source), "--self-test"], check=True, env=env)
    with (WORK / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
            "--name", "H3_SPI_RequestProbe_v3", "--distpath", str(OUTPUT),
            "--workpath", str(WORK / "work"), "--specpath", str(WORK),
            "--paths", str(PC), "--paths", str(ROOT / "tools"),
            "--add-binary", str(DLL) + os.pathsep + ".", str(source),
        ], check=True, env=env, stdout=log, stderr=subprocess.STDOUT)
    executable = OUTPUT / "H3_SPI_RequestProbe_v3.exe"
    subprocess.run([str(executable), "--self-test"], check=True, env=env)
    shutil.copy2(DLL, OUTPUT / DLL.name)
    readme = """H3 SPI 请求路径检查工具 v3 — 2026-10-16

工具版本为 v3，传感器仍使用 h3_spi_20261016_audit_v4，不需要烧录。
保持传感器供电、当前 SPI/UART 接线；关闭 Demo 和其他 CH347 接收工具。
解压后双击 H3_SPI_RequestProbe_v3.exe，等待自动检查完成。
参数仍为 SPI Mode0 / 468750 Hz / MSB / 8bit / CS0。

三组对照：手动片选 C2、SDK 自动片选 C2、C4 发送请求+C3读取响应。
每组只发送一次合法 cmd=0x20 快照请求；之后停顿，让 UART 单独记录这次
16字节请求的接收长度和头部，再进行少量响应读取。
UART在独立后台进程以115200连续接收，隔离DLL的SPI/UART路径缓存。
不擦写、不进入IAP、不复位、不发送UART命令；无需J-Link。

完成后提供 logs 下本次目录的 summary.json。
summary.json 保留各阶段诊断与原始数据文件，区分没有观察到请求时钟、
收到16字节但全FF、有效TAI3请求和响应返回等情况。
若UART未能记录某个阶段，会明确标为证据不足，不能当作请求接收失败。

软件自检包含SDK函数参数、发送字节、请求停顿、响应CRC/序号、阶段诊断，
以及冻结EXE启动独立UART模拟子进程；实物SPI结果以运行记录为准。
"""
    (OUTPUT / "README_请求路径检查.txt").write_text(readme, encoding="utf-8-sig")
    files = [source, ROOT / "tools/spi_readcheck.py", ROOT / "tools/ch347_uart_backend.py",
             Path(__file__), ROOT / "tools/tacai_image.py"]
    files += [PC / name for name in ("ch347_spi.py", "spi_iap_protocol.py", "iap_protocol.py")]
    with zipfile.ZipFile(OUTPUT / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    manifest = {"tool_version": 3, "sensor_firmware": "h3_spi_20261016_audit_v4",
                "purpose": "bounded request-path comparison with UART observation before response polls",
                "uart_isolation": "separate subprocess", "allowed_sensor_commands": [32],
                "firmware_writes": 0, "uart_commands_sent": 0,
                "source_self_test": "passed", "exe_self_test": "passed", "hardware_validation": "pending",
                "files": {p.name: {"size": p.stat().st_size, "sha256": sha(p)}
                          for p in sorted(OUTPUT.iterdir()) if p.is_file() and p.name != "manifest.json"}}
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    target = ROOT / "release/H3_SPI_RequestProbe_v3_20261016.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    print(json.dumps({"zip": str(target), "bytes": target.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
