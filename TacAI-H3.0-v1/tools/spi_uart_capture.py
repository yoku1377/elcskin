"""Receive-only UART evidence capture for the H3 SPI diagnostic application.

Import, --help and --self-test never enumerate or open hardware. Opening a
selected port performs no Serial.write and sends no sensor/IAP commands.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
import time


def console_line(line: str, firmware_ids: set[str]) -> bool:
    if line.startswith(("SPI_", "BOOT", "TacAI-H3")):
        return True
    fields = line.split(",")
    if len(fields) >= 3 and fields[0] == "DIAG" and fields[2] not in firmware_ids:
        firmware_ids.add(fields[2])
        return True
    return False


def capture(port: str, seconds: float, output: Path, *, serial_factory=None,
            monotonic=None, show=print) -> int:
    if serial_factory is None:
        import serial
        serial_factory = serial.Serial
    if monotonic is None:
        monotonic = time.monotonic
    # Set modem control states before opening; do not intentionally reset the
    # sensor. The original PB6/PB7 UART and common GND are sufficient.
    connection = serial_factory(port=None, baudrate=115200, bytesize=8,
                                parity="N", stopbits=1, timeout=0.2)
    lines = 0
    try:
        connection.dtr = False
        connection.rts = False
        connection.port = port
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as log:
            connection.open()
            show(f"已打开 {port}，115200 / 8N1。请在 CH347Demo 中执行 SPI 请求和读取测试。")
            show(f"采集 {seconds:g} 秒；日志：{output}")
            deadline = monotonic() + seconds
            firmware_ids: set[str] = set()
            while monotonic() < deadline:
                raw = connection.read_until(b"\n", 1024)
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                stamped = f"[{datetime.now():%H:%M:%S.%f}] {line}"
                log.write(stamped + "\n")
                log.flush()
                lines += 1
                if console_line(line, firmware_ids):
                    show(stamped)
    finally:
        connection.close()
    show(f"采集完成，保存 {lines} 行：{output}")
    if lines == 0:
        show("没有收到 UART 字节；请确认选择了传感器原 UART 调试口及其供电。")
    return lines


def self_test() -> None:
    """Exercise the actual capture loop with injected UART; no device access."""
    import tempfile

    class FakeSerial:
        def __init__(self, **settings):
            assert settings["port"] is None and settings["baudrate"] == 115200
            self.closed = self.opened = False
            self.frames = iter((b"RAW,1,0\r\n", b"DIAG,1,h3_spi_20261015_diag_v3,255,0,1\r\n",
                                b"SPI_DIAG,1000,fall=2,rise=2\r\n", b""))

        def open(self):
            assert not self.dtr and not self.rts and self.port == "FAKE_PORT"
            self.opened = True

        def read_until(self, ending, maximum):
            assert self.opened and ending == b"\n" and maximum == 1024
            return next(self.frames, b"")

        def close(self):
            self.closed = True

        def write(self, _data):
            raise AssertionError("UART capture must never transmit")

    fake = FakeSerial(port=None, baudrate=115200)
    ticks = iter((0, 0.1, 0.2, 0.3, 0.4, 1.1))
    displayed = []
    with tempfile.TemporaryDirectory(prefix="h3_spi_capture_test_") as folder:
        target = Path(folder) / "capture.log"
        count = capture("FAKE_PORT", 1, target, serial_factory=lambda **_kwargs: fake,
                        monotonic=lambda: next(ticks), show=displayed.append)
        data = target.read_text(encoding="utf-8")
        assert count == 3 and "RAW,1,0" in data and "SPI_DIAG," in data
        assert not any("RAW,1,0" in item for item in displayed)
        assert any("SPI_DIAG," in item for item in displayed)
    assert fake.closed
    seen: set[str] = set()
    assert console_line("DIAG,1,h3_spi_20261015_diag_v3,255,0,1", seen)
    assert not console_line("DIAG,2,h3_spi_20261015_diag_v3,255,0,1", seen)
    print("PASS: UART capture saves raw evidence, filters console output, never writes, and closes the port")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="H3 SPI 诊断 UART 日志采集（仅接收，不发送命令）")
    parser.add_argument("--port", help="原 UART 调试口，例如 COM5；未指定时手动选择")
    parser.add_argument("--seconds", type=float, default=180, help="采集秒数，默认 180")
    parser.add_argument("--output", type=Path, help="输出日志路径；文件必须尚不存在")
    parser.add_argument("--self-test", action="store_true", help="运行无硬件测试")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not 1 <= args.seconds <= 3600:
        parser.error("--seconds 必须在 1 到 3600 之间")
    import serial.tools.list_ports
    port = args.port
    if not port:
        ports = list(serial.tools.list_ports.comports())
        print("请选择传感器原来的 UART 调试口。上位机关闭 UART 连接后，可单独使用 SPI 连接。")
        for number, item in enumerate(ports, 1):
            print(f"  {number}. {item.device}  {item.description}")
        if not ports:
            print("没有发现串口。")
            return 1
        selection = input("输入序号：").strip()
        if not selection.isdigit() or not 1 <= int(selection) <= len(ports):
            print("序号无效，未打开任何串口。")
            return 1
        port = ports[int(selection) - 1].device
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    output = args.output or base / "logs" / f"H3_SPI_UART_{datetime.now():%Y%m%d_%H%M%S_%f}.log"
    capture(port, args.seconds, output.resolve())
    return 0


if __name__ == "__main__":
    interactive = len(sys.argv) == 1
    try:
        result = main()
    except KeyboardInterrupt:
        print("已停止采集；已收到的内容保存在日志中。")
        result = 130
    except Exception as error:
        print(f"采集失败：{error}")
        result = 1
    if interactive:
        input("按回车关闭窗口…")
    raise SystemExit(result)
