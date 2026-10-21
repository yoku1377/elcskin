"""H3 SPI snapshot check: only command 0x20 and separate 536-byte FF polls.

No firmware/IAP/reset command is permitted by the transmitting guard. UART,
when available, is received at 115200 before and after the SPI check.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import time
import zlib

PC_SOURCE = Path(__file__).resolve().parents[1] / "pc" / "TfgMonitorPython_H3_v5.0"
if PC_SOURCE.is_dir():
    sys.path.insert(0, str(PC_SOURCE))
from spi_iap_protocol import SPIIapClient, SPI_FRAME_BYTES

EXPECTED_FIRMWARE = "h3_spi_20261016_audit_v4"


def _write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class Record:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state = {"tool_version": 2, "status": "started", "allowed_commands": [32], "flash_operations": 0,
                      "expected_firmware": EXPECTED_FIRMWARE,
                      "uart_commands_sent": 0, "snapshots": [], "errors": [], "warnings": [],
                      "transactions": 0, "snapshot_requests": 0}
        self.save()

    def save(self):
        _write_json(self.directory / "summary.json", self.state)

    def problem(self, phase, error, warning=False):
        self.state["warnings" if warning else "errors"].append({"phase": phase, "error": str(error)})
        self.save()


class SnapshotOnlyBus:
    """Record exact wire bytes and reject every request outside the whitelist."""
    def __init__(self, spi, record):
        self.spi, self.record = spi, record
        self.sequences = set()

    def exchange(self, data):
        data = bytes(data)
        if data == b"\xff" * SPI_FRAME_BYTES:
            if not self.sequences:
                raise RuntimeError("不允许在快照请求前发送 SPI 轮询")
            kind = "poll"
        elif len(data) == 16:
            magic, command, flags, sequence, length, reserved = struct.unpack_from("<4sBBHHH", data)
            crc = struct.unpack_from("<I", data, 12)[0]
            if (magic != b"TAI3" or command != 0x20 or flags or length or reserved
                    or crc != zlib.crc32(data[:12]) & 0xFFFFFFFF):
                raise RuntimeError("发送保护：仅允许 CRC 正确的 cmd0x20 空负载快照请求")
            if not sequence or sequence in self.sequences:
                raise RuntimeError("发送保护：快照请求序号必须非零且不重复")
            self.sequences.add(sequence)
            self.record.state["snapshot_requests"] = len(self.sequences)
            kind = "snapshot_request"
        else:
            raise RuntimeError("发送保护：拒绝非快照请求或非536字节FF轮询")

        directory = self.record.directory
        tx_path, rx_path = directory / "spi_tx.bin", directory / "spi_rx.bin"
        entry = {"transaction": self.record.state["transactions"] + 1, "kind": kind,
                 "host_monotonic": time.monotonic(), "tx_length": len(data),
                 "tx_offset": tx_path.stat().st_size if tx_path.exists() else 0,
                 "rx_offset": rx_path.stat().st_size if rx_path.exists() else 0}
        with tx_path.open("ab") as stream:
            stream.write(data)
        try:
            received = bytes(self.spi.exchange(data))
            with rx_path.open("ab") as stream:
                stream.write(received)
            entry.update(rx_length=len(received), rx_prefix_hex=received[:16].hex(" "),
                         all_ff=bool(received) and received == b"\xff" * len(received))
            return received
        except BaseException as error:
            entry.update(rx_length=0, error=str(error))
            raise
        finally:
            with (directory / "spi_transactions.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.record.state["transactions"] += 1
            self.record.save()


def parse_spi_diagnostics(data):
    result = []
    complete = data.rsplit(b"\n", 1)[0] if b"\n" in data else b""
    for line in complete.decode("ascii", "replace").splitlines():
        fields = line.strip().split(",")
        if len(fields) < 3 or fields[0] != "SPI_DIAG":
            continue
        try:
            row = {"device_ms": int(fields[1])}
            for field in fields[2:]:
                key, value = field.split("=", 1)
                row[key] = int(value, 16 if key in ("head", "cr1", "cr2", "sr", "flags") else 10)
            required = ("fall", "rise", "rx", "data", "head", "req", "cmd", "bad", "stage",
                        "commit", "ack", "fault", "resync", "nss", "cr1", "cr2", "sr",
                        "rxleft", "txleft", "flags")
            if all(key in row for key in required):
                result.append(row)
        except ValueError:
            continue
    return result


def run_uart_worker(directory, dll_path=None, uart_index=None, duration=60.0, *,
                    uart_factory=None, clock=time.monotonic, sleep=time.sleep):
    """Only the child process may load or call the UART SDK."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    raw_path = directory / "uart_continuous.bin"
    events_path = directory / "uart_events.jsonl"
    raw_path.write_bytes(b"")
    pending = b""
    uart = None
    result = {"status": "starting", "bytes": 0, "spi_diag_count": 0,
              "uart_commands_sent": 0, "started_host_monotonic": clock(), "errors": []}
    try:
        if not 0 < duration <= 60.0:
            raise ValueError("UART worker duration must be in (0, 60] seconds")
        if uart_factory is None:
            from ch347_uart_backend import CH347UartBackend
            uart_factory = CH347UartBackend
        uart = uart_factory(dll_path)
        devices = uart.enumerate_devices()
        result["devices"] = devices
        candidates = ([d for d in devices if d["index"] == uart_index] if uart_index is not None
                      else [d for d in devices if d["uart_channel"] == 1])
        if len(candidates) != 1:
            raise RuntimeError("未找到唯一可用UART1；独立UART旁路不可用")
        selected = candidates[0]
        uart.open(selected["index"])
        uart.configure(115200)
        result.update(status="receiving", selected_uart=selected, ready_host_monotonic=clock())
        _write_json(directory / "uart_worker_ready.json", result)
        deadline = clock() + duration
        with raw_path.open("ab") as raw, events_path.open("a", encoding="utf-8") as events:
            while clock() < deadline and not (directory / "uart_worker_stop").exists():
                data = uart.read(4096)
                if not data:
                    sleep(0.005)
                    continue
                data = bytes(data)
                now = clock()
                offset = result["bytes"]
                raw.write(data)
                raw.flush()
                result["bytes"] += len(data)
                events.write(json.dumps({"kind": "chunk", "host_monotonic": now,
                                         "offset": offset, "length": len(data)}) + "\n")
                pending += data
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    for row in parse_spi_diagnostics(line + b"\n"):
                        result["spi_diag_count"] += 1
                        result["last_spi_diag"] = row
                        events.write(json.dumps({"kind": "spi_diag", "host_monotonic": now,
                                                 "diagnostic": row}) + "\n")
                # Corrupt input must not grow an unterminated line indefinitely.
                if len(pending) > 16384:
                    pending = pending[-16384:]
                events.flush()
        result["status"] = "stopped" if (directory / "uart_worker_stop").exists() else "duration_complete"
    except Exception as error:
        result["status"] = "failed"
        result["errors"].append(str(error))
        _write_json(directory / "uart_worker_error.json", {"error": str(error)})
        print(f"UART旁路失败：{error}", flush=True)
    finally:
        if uart is not None:
            try:
                uart.close()
            except Exception as error:
                result["errors"].append(f"close: {error}")
        result["ended_host_monotonic"] = clock()
        (directory / "uart_continuous.log").write_text(
            raw_path.read_bytes().decode("ascii", "replace"), encoding="utf-8")
        _write_json(directory / "uart_worker_result.json", result)
    return 1 if result["errors"] else 0


def uart_worker_command(directory, dll_path=None, uart_index=None, *, fixture=False):
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(Path(__file__).resolve()))
    command += ["--uart-worker", "--uart-worker-dir", str(Path(directory).resolve()),
                "--uart-worker-duration", "60"]
    if dll_path is not None:
        command += ["--dll", str(Path(dll_path).resolve())]
    if uart_index is not None:
        command += ["--uart-index", str(uart_index)]
    if fixture:
        command.append("--uart-worker-fixture")
    return command


class UARTWorkerProcess:
    """File IPC only: the SPI process never imports/opens the UART backend."""
    def __init__(self, record, dll_path=None, uart_index=None, *, popen=None,
                 clock=time.monotonic, sleep=time.sleep, fixture=False):
        self.record, self.dll_path, self.uart_index = record, dll_path, uart_index
        self.popen = popen or subprocess.Popen
        self.clock, self.sleep = clock, sleep
        self.fixture = fixture
        self.process = self.stdout = None
        self.ready = False

    def start(self, timeout=15.0):
        directory = self.record.directory
        try:
            self.stdout = (directory / "uart_worker_stdout.log").open("wb")
            self.process = self.popen(
                uart_worker_command(directory, self.dll_path, self.uart_index, fixture=self.fixture),
                stdin=subprocess.DEVNULL, stdout=self.stdout, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
            deadline = self.clock() + timeout
            while self.clock() < deadline:
                ready = directory / "uart_worker_ready.json"
                failure = directory / "uart_worker_error.json"
                if ready.exists():
                    info = json.loads(ready.read_text(encoding="utf-8"))
                    self.record.state["uart_worker"] = {"status": "ready", "pid": self.process.pid,
                                                         "ready": info}
                    self.record.state["selected_uart"] = info["selected_uart"]
                    self.record.save()
                    self.ready = True
                    print("独立UART进程已开始115200接收；保存SPI前诊断。", flush=True)
                    self.sleep(1.5)
                    return True
                if failure.exists():
                    raise RuntimeError(json.loads(failure.read_text(encoding="utf-8"))["error"])
                if self.process.poll() is not None:
                    raise RuntimeError(f"UART子进程提前退出：{self.process.returncode}")
                self.sleep(0.05)
            raise RuntimeError("UART子进程15秒内未就绪；继续SPI，详情见uart_worker_stdout.log")
        except Exception as error:
            self.record.problem("uart_worker_start", error, warning=True)
            return False

    def finish(self):
        directory = self.record.directory
        try:
            if self.ready and self.record.state.get("spi_check_ended_host_monotonic") is not None:
                self.sleep(1.5)
            (directory / "uart_worker_stop").touch()
            if self.process is not None:
                try:
                    self.process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self.record.problem("uart_worker_stop", "子进程未及时退出，保留原始记录后终止", warning=True)
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=2.0)
        except Exception as error:
            self.record.problem("uart_worker_cleanup", error, warning=True)
        finally:
            if self.stdout is not None:
                self.stdout.close()
            self.collect()

    def collect(self):
        directory = self.record.directory
        result_path = directory / "uart_worker_result.json"
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                self.record.state.setdefault("uart_worker", {})["result"] = result
                for error in result.get("errors", []):
                    self.record.problem("uart_worker", error, warning=True)
            except (OSError, ValueError) as error:
                self.record.problem("uart_worker_result", error, warning=True)
        raw_path = directory / "uart_continuous.bin"
        events_path = directory / "uart_events.jsonl"
        if not raw_path.exists() or not events_path.exists():
            self.record.save()
            return
        raw = raw_path.read_bytes()
        events = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        start = self.record.state.get("spi_check_started_host_monotonic")
        end = self.record.state.get("spi_check_ended_host_monotonic")
        for phase, boundary in (("before", start), ("after", end)):
            if boundary is None:
                continue
            selected = [event for event in events if
                        (event["host_monotonic"] <= boundary if phase == "before"
                         else event["host_monotonic"] >= boundary)]
            data = b"".join(raw[event["offset"]:event["offset"] + event["length"]]
                            for event in selected if event.get("kind") == "chunk")
            rows = [event for event in selected if event.get("kind") == "spi_diag"]
            (directory / f"uart_{phase}.bin").write_bytes(data)
            (directory / f"uart_{phase}.log").write_text(data.decode("ascii", "replace"), encoding="utf-8")
            self.record.state[f"uart_{phase}"] = {
                "bytes": len(data), "spi_diag_count": len(rows),
                "last_spi_diag": rows[-1]["diagnostic"] if rows else None,
                "last_host_monotonic": rows[-1]["host_monotonic"] if rows else None}
        before = self.record.state.get("uart_before", {}).get("last_spi_diag")
        after = self.record.state.get("uart_after", {}).get("last_spi_diag")
        if before and after and _advances(before["device_ms"], after["device_ms"]):
            counters = ("fall", "rise", "req", "cmd", "stage", "commit", "ack", "fault", "resync")
            self.record.state["spi_diag_delta"] = {
                key: (after[key] - before[key]) & 0xFFFFFFFF for key in counters}
        elif self.ready:
            self.record.problem("uart_worker_diagnostics", "未获得SPI前后完整且时间递增的SPI_DIAG，保留连续原始UART", warning=True)
        self.record.save()


def _advances(before, after):
    return 0 < ((after - before) & 0xFFFFFFFF) < 0x80000000


def check_snapshots(spi, record, *, timeout=5.0, sequence=None,
                    clock=time.monotonic, sleep=time.sleep):
    bus = SnapshotOnlyBus(spi, record)
    client = SPIIapClient(bus)
    # Reserve three nonzero IDs even when the random seed approaches uint16 wrap.
    client.sequence = sequence if sequence is not None else secrets.randbelow(65533) + 1
    if not 1 <= client.sequence <= 65533:
        raise ValueError("初始序号必须在1..65533")
    record.state["initial_sequence"] = client.sequence
    record.state["status"] = "checking"
    record.save()
    record.state["spi_check_started_host_monotonic"] = clock()
    record.save()
    for index in range(3):
        sequence = client.sequence
        try:
            snapshot = asdict(client.snapshot(timeout=timeout))
            snapshot["sequence"] = sequence
            record.state["snapshots"].append(snapshot)
            print(f"快照 {index + 1}/3：seq={sequence}, ms={snapshot['device_ms']}, "
                  f"frame={snapshot['frame_counter']}, mask=0x{snapshot['valid_mask']:02X}, "
                  f"errors={snapshot['error_count']}, baseline={snapshot['baseline_epoch']}, "
                  f"firmware={snapshot['firmware_id']}", flush=True)
        except Exception as error:
            record.problem(f"snapshot_{index + 1}_seq_{sequence}", error)
            print(f"快照 {index + 1}/3 失败：{error}", flush=True)
        record.save()
        if index < 2:
            sleep(0.25)
    record.state["spi_check_ended_host_monotonic"] = clock()
    rows = record.state["snapshots"]
    same_session = len({(s["firmware_id"], s["baseline_epoch"]) for s in rows}) == 1
    advancing = len(rows) == 3 and same_session and all(
        _advances(a["device_ms"], b["device_ms"]) and _advances(a["frame_counter"], b["frame_counter"])
        for a, b in zip(rows, rows[1:]))
    valid = len(rows) == 3 and all(s["valid_mask"] == 255 and s["baseline_epoch"] > 0
              and s["status"] & 0x13 == 3 for s in rows)
    changes = [sum(a != b for a, b in zip(left["raw"], right["raw"]))
               for left, right in zip(rows, rows[1:])]
    firmware_match = len(rows) == 3 and all(s["firmware_id"] == EXPECTED_FIRMWARE for s in rows)
    record.state.update(protocol_ok=len(rows) == 3, samples_advance=advancing,
                        all_samples_valid=valid, raw_changed_channels=changes,
                        firmware_match=firmware_match,
                        spi_fresh_acquisition_ok=advancing and valid and firmware_match)
    record.state["status"] = "finished"
    record.save()


def _is_target_spi(device):
    # Match the actual CH347F mode-0 combined interface; never select UART0/1.
    is_f = "PID_55DE" in device.device_id.upper() or "CH347F" in device.function_description.upper()
    return is_f and device.supports_spi and device.function_type == 3 and device.chip_mode == 0 and device.interface_number == 4


def _fixture_diagnostic(device_ms, commands=0):
    return (f"SPI_DIAG,{device_ms},fall={12 + commands * 2},rise={12 + commands * 2},"
            f"rx={536 if commands else 0},data={16 if commands else 0},"
            f"head={'54414933' if commands else '00000000'},req={commands},cmd={commands},bad=0,"
            f"stage={commands},commit={commands},ack={commands},fault=0,resync=0,nss=1,"
            "cr1=00000040,cr2=00001703,sr=00001800,rxleft=537,txleft=533,flags=01\r\n").encode("ascii")


class _WorkerFixtureUART:
    """Self-test only; deliberately has no SDK, DLL loading or transmit API."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.pending = b""
        self.started = time.monotonic()

    def enumerate_devices(self):
        return [{"index": 1, "uart_channel": 1, "description": "SELFTEST UART1 fixture"}]

    def open(self, index):
        assert index == 1

    def configure(self, rate):
        assert rate == 115200

    def read(self, capacity):
        assert capacity == 4096
        time.sleep(0.03)
        if self.pending:
            result, self.pending = self.pending, b""
            return result
        count = 3 if (self.directory / "uart_worker_fixture_after").exists() else 0
        line = _fixture_diagnostic(int((time.monotonic() - self.started) * 1000), count)
        result, self.pending = line[:31], line[31:]
        return result

    def close(self):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--index", type=int, help="SPI SDK的真实设备索引")
    parser.add_argument("--cs", type=int, choices=(0, 1), default=0,
                        help="CH347片选：0=SCS0/CS1，1=SCS1/CS2；默认0")
    parser.add_argument("--uart-index", type=int, help="UART SDK真实索引，默认唯一UART1")
    parser.add_argument("--no-uart", action="store_true")
    parser.add_argument("--uart-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--uart-worker-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--uart-worker-duration", type=float, default=60.0, help=argparse.SUPPRESS)
    parser.add_argument("--uart-worker-fixture", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.uart_worker:
        if args.uart_worker_dir is None:
            raise ValueError("UART worker needs --uart-worker-dir")
        factory = (lambda unused: _WorkerFixtureUART(args.uart_worker_dir)) if args.uart_worker_fixture else None
        return run_uart_worker(args.uart_worker_dir, args.dll, args.uart_index,
                               args.uart_worker_duration, uart_factory=factory)
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    record = Record(base / "logs" / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_spi_readcheck"))
    print(f"H3 SPI 三次快照检查：Mode0 / 468750Hz / MSB / 8bit / SCS{args.cs}。")
    print("请先关闭 Demo 的 SPI/UART 设备，保留接线和传感器供电。只发送快照读取命令。", flush=True)
    print(f"记录目录：{record.directory}", flush=True)
    spi = None
    worker = None
    try:
        if not args.no_uart:
            worker = UARTWorkerProcess(record, args.dll, args.uart_index)
            worker.start()
        from ch347_spi import CH347Spi, list_ch347_devices
        devices = list_ch347_devices(args.dll)
        record.state["spi_devices"] = [asdict(d) for d in devices]
        candidates = [d for d in devices if _is_target_spi(d)]
        for candidate in candidates:
            print(candidate.diagnostic_summary)
        if args.index is not None:
            candidates = [d for d in candidates if d.index == args.index]
        if len(candidates) != 1:
            raise RuntimeError("无法唯一确定 CH347F Mode0 SPI接口(IF4)；请用 --index 指定上述SPI真实索引")
        selected = candidates[0]
        record.state["selected_spi"] = asdict(selected)
        record.state["spi_cs"] = args.cs
        record.save()
        spi = CH347Spi(selected.index, clock_hz=468750, cs=args.cs, dll_path=args.dll)
        record.state["spi_configuration"] = spi.configuration_summary
        record.state["spi_raw_readback"] = {name: getattr(spi.raw_readback, name)
                                              for name, _ in spi.raw_readback._fields_}
        print(spi.configuration_summary, flush=True)
        record.save()
        check_snapshots(spi, record)
    except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as error:
        record.problem("main", error or "用户中断")
        record.state["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
    finally:
        if worker is not None:
            worker.finish()
        for name, adapter in (("spi", spi),):
            if adapter is not None:
                try:
                    adapter.close()
                except Exception as error:
                    record.problem(f"{name}_close", error, warning=name == "uart")
                    print(f"{name} 关闭异常：{error}；记录已保留。", flush=True)
        record.save()
    if record.state.get("spi_fresh_acquisition_ok"):
        print("结果：三次SPI快照均通过CRC/序号校验，设备时间与采集帧号递增，8个传感器采集有效。")
        print(f"相邻快照 RAW 变化通道数：{record.state['raw_changed_channels']}（空载可不变化）。")
    elif record.state.get("protocol_ok"):
        print("结果：三次SPI快照协议校验通过，但固件ID、样本更新或采集有效性检查未通过；请查看summary.json。")
    else:
        print("结果：未完成三次有效SPI快照。原始SPI/UART及错误已保存，请提供summary.json。")
    if record.state.get("spi_diag_delta"):
        print("UART观测到SPI计数变化：", record.state["spi_diag_delta"])
    print(f"记录目录：{record.directory}", flush=True)
    return 0 if record.state.get("spi_fresh_acquisition_ok") and not record.state["errors"] else 1


def self_test():
    import tempfile
    from unittest.mock import patch
    from iap_protocol import encode_frame

    class Clock:
        now = 100.0
        def clock(self):
            return self.now
        def sleep(self, seconds):
            self.now += seconds

    class FakeSPI:
        def __init__(self, timing, corrupt=False, frozen=False, firmware_id=EXPECTED_FIRMWARE):
            self.timing, self.corrupt, self.frozen = timing, corrupt, frozen
            self.firmware_id = firmware_id
            self.calls, self.requests, self.polls = [], 0, 0
        def exchange(self, data):
            self.timing.now += 0.01
            self.calls.append(data)
            if len(data) == 16:
                self.sequence = struct.unpack_from("<H", data, 6)[0]
                self.requests += 1
                self.polls = 0
                return b"\xff" * len(data)
            self.polls += 1
            if self.polls == 1:
                return b"\xff" * 536
            count = 1 if self.frozen else self.requests
            payload = struct.pack("<HHIIIIB3x", 1, 3, count * 100, count, 0, 1, 255)
            payload += self.firmware_id.encode("ascii").ljust(32, b"\0")
            payload += struct.pack("<27f", *([1.0] * 27))
            frame = bytearray(encode_frame(0x20, self.sequence, payload))
            frame[4] = 0xA0
            frame[-4:] = struct.pack("<I", zlib.crc32(frame[:-4]) & 0xFFFFFFFF)
            if self.corrupt:
                frame[20] ^= 1
            return bytes(frame).ljust(536, b"\xff")

    with tempfile.TemporaryDirectory(prefix="h3_spi_readcheck_") as temporary:
        timing = Clock()
        for label, corrupt, frozen, firmware_id in (
                ("good", False, False, EXPECTED_FIRMWARE),
                ("bad_crc", True, False, EXPECTED_FIRMWARE),
                ("stale_sample", False, True, EXPECTED_FIRMWARE),
                ("other_firmware", False, False, "h3_other_firmware")):
            record = Record(Path(temporary) / label)
            spi = FakeSPI(timing, corrupt, frozen, firmware_id)
            with patch("spi_iap_protocol.time.monotonic", side_effect=timing.clock), \
                    patch("spi_iap_protocol.time.sleep", side_effect=timing.sleep):
                check_snapshots(spi, record, sequence=30000, clock=timing.clock, sleep=timing.sleep)
            assert spi.requests == 3
            assert all(len(x) == 536 and set(x) == {255} or len(x) == 16 and x[4] == 0x20 for x in spi.calls)
            saved = json.loads((record.directory / "summary.json").read_text(encoding="utf-8"))
            assert saved["spi_fresh_acquisition_ok"] == (not corrupt and not frozen and firmware_id == EXPECTED_FIRMWARE)
            if label == "good":
                assert saved["raw_changed_channels"] == [0, 0]
                assert [s["sequence"] for s in saved["snapshots"]] == [30000, 30001, 30002]
            if corrupt:
                assert len(saved["errors"]) == 3 and "CRC32" in saved["errors"][0]["error"]
            if label == "other_firmware":
                assert saved["protocol_ok"] and not saved["firmware_match"]
                assert saved["expected_firmware"] == EXPECTED_FIRMWARE
            assert (record.directory / "spi_tx.bin").read_bytes()
            assert (record.directory / "spi_rx.bin").read_bytes()
            bus = SnapshotOnlyBus(spi, record)
            before = len(spi.calls)
            bad_crc = bytearray(encode_frame(0x20, 5))
            bad_crc[-1] ^= 1
            reserved = bytearray(encode_frame(0x20, 6))
            reserved[10] = 1
            reserved[-4:] = struct.pack("<I", zlib.crc32(reserved[:-4]) & 0xFFFFFFFF)
            for forbidden in (encode_frame(0x21, 1), encode_frame(2, 2), encode_frame(0x20, 3, b"bad"),
                              encode_frame(0x20, 4, flags=1), bytes(bad_crc), bytes(reserved),
                              encode_frame(0x20, 0), b"wrong"):
                try:
                    bus.exchange(forbidden)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Forbidden request reached SPI")
            assert len(spi.calls) == before
        class FailingUART:
            def __init__(self, no_device=False):
                self.calls, self.closed, self.no_device = 0, False, no_device
            def enumerate_devices(self):
                return [] if self.no_device else [{"index": 1, "uart_channel": 1, "description": "FakeUART1"}]
            def open(self, index):
                assert index == 1
            def configure(self, rate):
                assert rate == 115200
            def read(self, capacity):
                assert capacity == 4096
                self.calls += 1
                timing.now += 0.1
                if self.calls > 1:
                    raise OSError("test disconnected UART")
                return _fixture_diagnostic(100)
            def close(self):
                self.closed = True

        for label, no_device in (("uart_partial", False), ("uart_no_device", True)):
            directory = Path(temporary) / label
            uart = FailingUART(no_device)
            status = run_uart_worker(directory, uart_factory=lambda unused: uart,
                                     clock=timing.clock, sleep=timing.sleep)
            result = json.loads((directory / "uart_worker_result.json").read_text(encoding="utf-8"))
            assert status == 1 and result["status"] == "failed" and uart.closed
            assert (directory / "uart_worker_error.json").exists()
            assert result["bytes"] == (0 if no_device else len(_fixture_diagnostic(100)))
            assert result["spi_diag_count"] == (0 if no_device else 1)

        for frozen in (False, True):
            with patch.object(sys, "frozen", frozen, create=True):
                command = uart_worker_command(Path(temporary), fixture=True)
            assert command[0] == sys.executable and "--uart-worker" in command
            assert (str(Path(__file__).resolve()) in command) == (not frozen)

        # A child failure must not prevent the independent SPI-only check.
        record = Record(Path(temporary) / "uart_failed_child")
        class FailedChild:
            pid, returncode = 1234, 1
            def poll(self):
                return self.returncode
            def wait(self, timeout):
                assert (record.directory / "uart_worker_stop").exists()
                return self.returncode
        def failed_popen(command, **options):
            assert "--uart-worker" in command and options["stdin"] == subprocess.DEVNULL
            assert options["stderr"] == subprocess.STDOUT
            if os.name == "nt":
                assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
            _write_json(record.directory / "uart_worker_error.json", {"error": "mock child has no UART1"})
            _write_json(record.directory / "uart_worker_result.json",
                        {"status": "failed", "errors": ["mock child has no UART1"], "bytes": 0})
            return FailedChild()
        worker = UARTWorkerProcess(record, popen=failed_popen, clock=timing.clock, sleep=timing.sleep)
        assert not worker.start()
        spi = FakeSPI(timing)
        with patch("spi_iap_protocol.time.monotonic", side_effect=timing.clock), \
                patch("spi_iap_protocol.time.sleep", side_effect=timing.sleep):
            check_snapshots(spi, record, sequence=31000, clock=timing.clock, sleep=timing.sleep)
        worker.finish()
        assert spi.requests == 3 and record.state["spi_fresh_acquisition_ok"]
        assert record.state["warnings"] and not record.state["errors"]

        # Real process round trip, including the frozen one-file EXE entry point
        # when this self-test is run by the packaged application. No DLL is loaded.
        record = Record(Path(temporary) / "uart_real_child_fixture")
        worker = UARTWorkerProcess(record, fixture=True)
        try:
            assert worker.start(), record.state["warnings"]
            record.state["spi_check_started_host_monotonic"] = time.monotonic()
            (record.directory / "uart_worker_fixture_after").touch()
            record.state["spi_check_ended_host_monotonic"] = time.monotonic()
            record.save()
        finally:
            worker.finish()
        assert worker.process.returncode == 0
        assert (record.directory / "uart_worker_stop").exists()
        assert record.state["uart_worker"]["result"]["status"] == "stopped"
        assert record.state["uart_before"]["spi_diag_count"] > 0
        assert record.state["uart_after"]["spi_diag_count"] > 0
        assert record.state["spi_diag_delta"]["req"] == record.state["spi_diag_delta"]["ack"] == 3
        assert not record.state["warnings"], record.state["warnings"]
    print("PASS: snapshot-only transmit guard, CRC/sequence validation, three advancing samples, "
          "unchanged RAW accepted, stale-sample rejection, independent UART child ready/stop/result IPC, "
          "real same-program fixture subprocess and partial UART/error persistence")


if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = main()
    except (OSError, RuntimeError, ValueError, EOFError) as error:
        print(f"失败：{error}", file=sys.stderr, flush=True)
        exit_code = 1
    if (getattr(sys, "frozen", False) and "--self-test" not in sys.argv
            and "--uart-worker" not in sys.argv):
        try:
            input("按回车关闭窗口。")
        except EOFError:
            pass
    raise SystemExit(exit_code)
