"""Receive-only CH347 UART baud discovery for the complete H3 firmware.

Configures the USB adapter and reads bytes. No UART write/IAP/reset operation
is available in the adapter interface used by this tool.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys
import time

RATES = (115200, 9600, 57600, 230400, 460800, 38400, 19200, 28800,
         76800, 921600, 14400, 31250, 56000, 128000, 256000)
MARKERS = re.compile(
    rb"(?:^|[\r\n])(?:SPI_DIAG,\d+,|DIAG,\d+,h3_[A-Za-z0-9_]+,|"
    rb"RAW,\d+,|FORCE,\d+,|SPI_READY,h3_[A-Za-z0-9_]+,|"
    rb"BOOT\d[^\r\n]*|BOOT_ERROR Error_Handler)")


def analyze(data: bytes) -> dict:
    printable = sum(32 <= b <= 126 or b in (9, 10, 13) for b in data)
    ratio = printable / len(data) if data else 0.0
    # The last unterminated line may be a USB read fragment, not a full frame.
    complete = data.rsplit(b"\n", 1)[0] + b"\n" if b"\n" in data else b""
    matches = list(MARKERS.finditer(complete))
    # Reject an accidental marker inside a corrupt or binary byte stream.
    recognized = len(matches) >= 2 and ratio >= 0.97
    text = complete.decode("ascii", errors="replace")
    ids = sorted(set(re.findall(r"h3_[A-Za-z0-9_]+", text)))
    diagnostics, cycles = [], []
    pending_diag, pending_raw = None, False
    raw = force = 0
    for line in text.splitlines():
        fields = line.split(",")
        if len(fields) == 6 and fields[0] == "DIAG" and fields[2].startswith("h3_"):
            pending_diag, pending_raw = None, False
            try:
                diagnostic = {"device_ms": int(fields[1]), "firmware_id": fields[2],
                              "valid_mask": int(fields[3]), "errors": int(fields[4]),
                              "baseline_epoch": int(fields[5])}
                if not all(0 <= diagnostic[key] <= 0xFFFFFFFF
                           for key in ("device_ms", "errors", "baseline_epoch")):
                    continue
                if not 0 <= diagnostic["valid_mask"] <= 255:
                    continue
                diagnostics.append(diagnostic)
                if diagnostic["valid_mask"] == 255 and diagnostic["baseline_epoch"] > 0:
                    pending_diag = diagnostic
            except ValueError:
                pass
        elif fields[0] in ("RAW", "FORCE"):
            if len(fields) != (26 if fields[0] == "RAW" else 5):
                pending_diag, pending_raw = None, False
                continue
            try:
                timestamp = int(fields[1])
                if not 0 <= timestamp <= 0xFFFFFFFF or not all(math.isfinite(float(f)) for f in fields[2:]):
                    raise ValueError("Invalid sample")
            except ValueError:
                pending_diag, pending_raw = None, False
                continue
            if fields[0] == "RAW":
                raw += 1
                pending_raw = pending_diag is not None and timestamp == pending_diag["device_ms"]
            else:
                force += 1
                if pending_diag is not None and pending_raw and timestamp == pending_diag["device_ms"]:
                    cycles.append((pending_diag["firmware_id"], pending_diag["baseline_epoch"], timestamp))
                pending_diag, pending_raw = None, False
    last = diagnostics[-1] if diagnostics else None
    same_session = [c for c in cycles if last and c[:2] == (last["firmware_id"], last["baseline_epoch"])]
    times = [c[2] for c in same_session]
    advancing = len(times) >= 2 and all(0 < ((b - a) & 0xFFFFFFFF) < 0x80000000
                                      for a, b in zip(times, times[1:]))
    return {"bytes": len(data), "printable_ratio": round(ratio, 4), "markers": len(matches),
            "recognized": recognized, "firmware_ids": ids,
            "boot_error": recognized and b"BOOT_ERROR Error_Handler" in data,
            "last_diag": last, "complete_valid_cycles": len(same_session),
            "raw_frames": raw, "force_frames": force,
            "fresh_valid_acquisition": bool(recognized and advancing and last and last["valid_mask"] == 255)}


class CaptureReadError(RuntimeError):
    def __init__(self, message, partial):
        super().__init__(message)
        self.partial = partial


def capture(adapter, duration, clock=time.monotonic, sleep=time.sleep) -> bytes:
    deadline = clock() + duration
    chunks = []
    total = 0
    while clock() < deadline and total < 256 * 1024:
        try:
            block = adapter.read(4096)
        except (OSError, RuntimeError) as exc:
            raise CaptureReadError(str(exc), b"".join(chunks)) from exc
        if block:
            chunks.append(block)
            total += len(block)
        else:
            sleep(0.01)
    return b"".join(chunks)


def scan(adapter, directory, rates=RATES, duration=1.4, clock=time.monotonic, sleep=time.sleep):
    results = []
    for rate in rates:
        print(f"尝试 {rate} baud ...", flush=True)
        data, error, measuring = b"", None, False
        try:
            adapter.configure(rate)
            # Discard bytes queued under the previous adapter baud setting.
            capture(adapter, 0.2, clock, sleep)
            measuring = True
            data = capture(adapter, duration, clock, sleep)
        except (OSError, RuntimeError) as exc:
            data = exc.partial if measuring and isinstance(exc, CaptureReadError) else b""
            error = str(exc)
            print(f"  设置/接收失败：{exc}", flush=True)
        (directory / f"baud_{rate}.bin").write_bytes(data)
        result = {"baud": rate, **analyze(data)}
        if error:
            result["error"] = error
        results.append(result)
        print(f"  收到 {len(data)} 字节；可打印比例 {result['printable_ratio']:.1%}；"
              f"日志标记 {result['markers']}", flush=True)
        if result["recognized"] and error is None:
            print(f"识别到固件日志，继续以 {rate} 接收 5 秒。", flush=True)
            try:
                data += capture(adapter, 5.0, clock, sleep)
            except CaptureReadError as exc:
                data += exc.partial
                result["error"] = str(exc)
                print(f"  采集中断：{exc}；已保存此前数据。", flush=True)
            result.update(analyze(data))
            (directory / f"baud_{rate}.bin").write_bytes(data)
        if result["recognized"]:
            (directory / "decoded.log").write_text(data.decode("ascii", errors="replace"), encoding="utf-8")
            break
    return results


def self_test():
    import tempfile
    valid = (b"DIAG,100,h3_spi_20261016_audit_v4,255,2,1\r\n"
             b"RAW,100," + b",".join([b"1.000"] * 24) + b"\r\nFORCE,100,1.000,2.000,3.000\r\n")
    valid += valid.replace(b",100,", b",200,")
    a = analyze(valid)
    assert a["recognized"] and a["fresh_valid_acquisition"] and a["last_diag"]["errors"] == 2
    assert a["firmware_ids"] == ["h3_spi_20261016_audit_v4"]
    assert not analyze(b"DIAG,100,h3_spi_20261016_audit_v4,255,0,1\r\n")["recognized"]
    assert not analyze(b"\xff\x80" * 500 + valid)["recognized"]
    assert not analyze(bytes.fromhex("31 39 32 C7 32 31 3A 18 31 6E 39 C7"))["recognized"]
    assert not analyze(b"ordinary text\r\n" * 100)["recognized"]
    invalid = b"DIAG,100,h3_spi_20261016_audit_v4,0,2,0\r\nDIAG,200,h3_spi_20261016_audit_v4,0,3,0\r\n"
    assert analyze(invalid)["recognized"] and not analyze(invalid)["fresh_valid_acquisition"]
    assert analyze(b"BOOT_ERROR Error_Handler\r\n" * 5)["boot_error"]
    assert not analyze(b"")["recognized"]
    assert not analyze(valid.replace(b"RAW,200,", b"RAW,100,").replace(b"FORCE,200,", b"FORCE,100,"))["fresh_valid_acquisition"]
    assert not analyze(valid.replace(b"RAW,", b"RAW,999,").replace(b"FORCE,", b"FORCE,888,"))["fresh_valid_acquisition"]
    assert not analyze(valid.replace(b"1.000", b"nan").replace(b"2.000", b"inf"))["fresh_valid_acquisition"]
    assert not analyze(valid.rstrip(b"\r\n"))["fresh_valid_acquisition"]

    class FakeClock:
        now = 0.0
        def clock(self):
            return self.now
        def sleep(self, seconds):
            self.now += seconds

    class FakeAdapter:
        """Deliberately has no write/reset API."""
        def __init__(self, timing):
            self.timing = timing
            self.rate = 0
            self.rates = []
            self.counter = 0
        def configure(self, rate):
            self.rate = rate
            self.rates.append(rate)
        def read(self, capacity):
            assert capacity == 4096
            self.timing.now += 0.1
            self.counter += 1
            stamp = str(self.counter * 1000).encode()
            return (valid.replace(b",100,", b"," + stamp + b",").replace(b",200,", b"," + str(self.counter * 1000 + 100).encode() + b",")) if self.rate == 230400 else b"\xc7\xe6\x18\x00" * 20

    with tempfile.TemporaryDirectory(prefix="h3_uart_baud_") as temp:
        timing = FakeClock()
        adapter = FakeAdapter(timing)
        results = scan(adapter, Path(temp), (115200, 230400, 460800),
                       duration=0.4, clock=timing.clock, sleep=timing.sleep)
        assert adapter.rates == [115200, 230400]
        assert results[-1]["recognized"] and results[-1]["fresh_valid_acquisition"]
        assert (Path(temp) / "decoded.log").read_bytes()
        class FailingAdapter(FakeAdapter):
            def read(self, capacity):
                if self.counter >= 8:
                    raise OSError("simulated USB disconnect")
                return super().read(capacity)
        timing = FakeClock()
        adapter = FailingAdapter(timing)
        results = scan(adapter, Path(temp), (230400,), duration=0.4,
                       clock=timing.clock, sleep=timing.sleep)
        assert results[0]["recognized"] and "error" in results[0]
        assert (Path(temp) / "baud_230400.bin").stat().st_size > 0
    print("PASS: UART log recognition, corrupt/noisy rejection, fresh/invalid sampling, error banner and receive-only baud scan")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--index", type=int, help="Demo 的 UART 设备索引，例如 1")
    parser.add_argument("--dll", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    from ch347_uart_backend import CH347UartBackend
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    directory = base / "logs" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_baud")
    directory.mkdir(parents=True, exist_ok=True)
    print("H3 CH347 UART 自动波特率接收\n请先关闭 CH347Demo 的 UART 设备，保留接线和传感器供电。", flush=True)
    print("工具只配置 CH347 接收参数和读取字节，不向传感器发送命令。", flush=True)
    backend = CH347UartBackend(args.dll)
    devices = backend.enumerate_devices()
    if not devices:
        raise RuntimeError("未找到可打开的 CH347 UART。请关闭 Demo 的 UART 设备并检查 USB 枚举。")
    for number, entry in enumerate(devices, 1):
        print(f"[{number}] 设备索引 {entry['index']}：{entry['description']}")
    selected = next((d for d in devices if d["index"] == args.index), None) if args.index is not None else None
    if args.index is not None and selected is None:
        raise RuntimeError(f"设备索引 {args.index} 当前不可打开")
    if selected is None:
        if len(devices) == 1:
            selected = devices[0]
        else:
            choice = int(input("选择接着 PB6 的 UART1 对应序号：").strip())
            if not 1 <= choice <= len(devices):
                raise RuntimeError("选择超出列表范围")
            selected = devices[choice - 1]
    results = []
    try:
        backend.open(selected["index"])
        results = scan(backend, directory)
    except (OSError, RuntimeError) as exc:
        results.append({"phase": "open_or_scan", "error": str(exc), "recognized": False})
        print(f"设备操作失败：{exc}；仍保存诊断摘要。", flush=True)
    finally:
        try:
            backend.close()
        except (OSError, RuntimeError) as exc:
            results.append({"phase": "close", "error": str(exc), "recognized": False})
            print(f"关闭设备时出错：{exc}；仍保存此前数据。", flush=True)
    summary = {"device": selected, "results": results,
               "recognized": any(r.get("recognized") for r in results),
               "sensor_commands_sent": 0, "flash_operations": 0}
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    found = next((r for r in results if r.get("recognized")), None)
    if found:
        print(f"\n识别波特率：{found['baud']}；固件：{', '.join(found['firmware_ids']) or '当前片段未含版本号'}")
        if found["boot_error"]:
            print("结果：固件进入 BOOT_ERROR，尚未正常完成启动。")
        elif found["fresh_valid_acquisition"]:
            print("结果：已识别时间递增的有效 DIAG + RAW/FORCE 采集帧。")
        else:
            print("结果：日志可解码；采集状态请结合 last_diag 检查。")
        print("最后诊断：", found["last_diag"])
        if found.get("error"):
            print("采集期间错误：", found["error"])
    else:
        print("\n各档均未识别有效固件日志。收到字节不代表采集正常；已保存原始数据供继续定位。")
    print(f"日志目录：{directory}")
    print("请提供 summary.json；识别成功时还会生成 decoded.log。")


if __name__ == "__main__":
    status = 0
    try:
        main()
    except (OSError, RuntimeError, ValueError, EOFError) as exc:
        print(f"失败：{exc}", file=sys.stderr, flush=True)
        status = 1
    if getattr(sys, "frozen", False) and "--self-test" not in sys.argv:
        try:
            input("按回车关闭窗口。")
        except EOFError:
            pass
    raise SystemExit(status)
