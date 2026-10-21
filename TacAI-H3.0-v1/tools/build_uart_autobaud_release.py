"""Package the receive-only UART diagnosis tool without opening hardware."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release/uart_autobaud_20261016"
WORK = ROOT / "tmp/uart_autobaud_build"
DLL = ROOT / "release/TacAI_H3_v5_3_7_CH347F_DetectionFix_20261015/CH347DLLA64.dll"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    assert sha(DLL) == "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375"
    subprocess.run([sys.executable, str(ROOT / "tools/spi_uart_autobaud.py"), "--self-test"], check=True)
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(WORK / "cache")
    with (WORK / "build.log").open("w", encoding="utf-8") as log:
        process = subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
            "--name", "H3_UART_AutoBaud", "--distpath", str(OUTPUT),
            "--workpath", str(WORK / "work"), "--specpath", str(WORK),
            "--add-binary", str(DLL) + os.pathsep + ".", str(ROOT / "tools/spi_uart_autobaud.py"),
        ], env=environment, stdout=log, stderr=subprocess.STDOUT)
    if process.returncode:
        raise RuntimeError(f"PyInstaller failed; see {WORK / 'build.log'}")
    executable = OUTPUT / "H3_UART_AutoBaud.exe"
    subprocess.run([str(executable), "--self-test"], check=True)
    for name in ("spi_uart_autobaud.py", "ch347_uart_backend.py"):
        shutil.copy2(ROOT / "tools" / name, OUTPUT / name)
    shutil.copy2(DLL, OUTPUT / DLL.name)
    readme = """H3 CH347 UART 自动波特率接收工具 — 2026-10-16

用途：当前 UART 已收到字节但都是乱码，自动尝试波特率并识别固件日志。
本工具不需要 J-Link，不替换固件，不发送 UART 命令，不擦写传感器。
它使用与 CH347Demo 相同的 CH347Uart SDK 接口，不依赖 COM 口枚举。

操作
1. 保持传感器供电和当前 PB6 -> CH347 UART1 RXD、GND 共地接线。
2. 在 CH347Demo 关闭 UART 设备，或直接退出 Demo，释放设备占用。
3. 解压此 ZIP 后双击 H3_UART_AutoBaud.exe。
4. 若出现多个设备，选择与 Demo 中 UART1 相同的设备索引（此前为 1）。
5. 等待自动扫描；各档约 1.6 秒，识别到日志后额外接收 5 秒。
6. 将程序旁 logs 下本次目录中的 summary.json 发回；识别成功另有 decoded.log。

判读
- recognized=true：收到多条符合固件格式的 ASCII 日志，不仅是有 RX 字节。
- fresh_valid_acquisition=true：DIAG 时间递增、有效掩码255、基线已建立，且存在 RAW/FORCE 帧。
- boot_error=true：识别到 BOOT_ERROR，应用未正常完成启动。
- 各档均未识别：保留原始 .bin 数据继续分析，不会把最高可打印比例误报为正常。

扫描速率：115200、9600、57600、230400、460800、38400、19200、28800、76800、
921600、14400、31250、56000、128000、256000；统一8N1，无硬件流控。
SDK设置或USB读取失败会明确显示；扫描结束关闭接口。
后续重新使用 Demo 时，必须重新点“参数设置”应用所需速率。

已完成源代码和EXE自检、模拟DLL接口测试；尚未在这块传感器上运行。
Windows x64，包含本机已审核使用的 CH347DLLA64.dll；驱动仍需正确安装。
"""
    (OUTPUT / "README_双击运行.txt").write_text(readme, encoding="utf-8-sig")
    manifest = {
        "purpose": "receive-only UART baud discovery", "sensor_commands_sent": 0,
        "flash_operations": 0, "source_self_test": "passed", "exe_self_test": "passed",
        "hardware_validation": "pending",
        "files": {p.name: {"size": p.stat().st_size, "sha256": sha(p)}
                  for p in sorted(OUTPUT.iterdir()) if p.is_file() and p.name != "manifest.json"},
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = ROOT / "release/H3_UART_AutoBaud_20261016.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(OUTPUT.iterdir()):
            if p.is_file():
                z.write(p, p.name)
    print(json.dumps({"zip": str(archive), "exe": str(executable), "zip_bytes": archive.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
