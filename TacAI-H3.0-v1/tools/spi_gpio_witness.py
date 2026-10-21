"""Observe PA4 through J-Link while CH347 sends constant bytes with CS high."""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import queue
import sys
import threading
import time

# Windows Python 3.11 monotonic() can be quantized to ~15ms; QPC is both
# monotonic and high-resolution enough to distinguish reads from USB gaps.
_clock = time.perf_counter

_PC_PATH = Path(__file__).resolve().parents[1] / "pc" / "TfgMonitorPython_H3_v5.0"
if _PC_PATH.is_dir():
    sys.path.insert(0, str(_PC_PATH))
import ch347_spi as sdk

EXPECTED_DLL_SHA256 = "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375"
GPIOA_MODER, GPIOA_IDR = 0x50000000, 0x50000010
GPIOA_AFRL, GPIOA_AFRH = 0x50000020, 0x50000024
READ_ADDRESSES = frozenset((GPIOA_MODER, GPIOA_IDR, GPIOA_AFRL, GPIOA_AFRH))
PATTERNS = (0x00, 0xFF, 0x00, 0xFF)
MAX_TOTAL_SECONDS = 24.0
PHASE_SECONDS = 1.25
MAX_TRANSFERS_PER_PHASE = 32
TRANSFER_LENGTH = 4096
MAX_SAMPLES_PER_PHASE = 256
MIN_MATCHING_SAMPLES = 2


class Record:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.state = {
            "tool": "spi_gpio_witness", "tool_version": 3, "status": "started", "errors": [],
            "phases": [], "samples": 0, "transactions": 0, "expected_dll_sha256": EXPECTED_DLL_SHA256,
            "cs_policy": "CS0 always high; WriteRead iChipSelect=0; never use exchange()",
            "read_addresses": [f"0x{address:08X}" for address in sorted(READ_ADDRESSES)],
            "evidence_scope": "GPIO input during successful SPI API windows; includes USB waiting time",
            "target_commands_sent": 0, "uart_commands_sent": 0, "flash_operations": 0,
            "target_writes": 0, "reset_halt_go_operations": 0,
        }
        self.save()

    def save(self):
        with self.lock:
            path = self.directory / "summary.json.tmp"
            path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
            path.replace(self.directory / "summary.json")

    def update(self, **values):
        with self.lock:
            self.state.update(values)
            self.save()

    def append(self, filename, event):
        with self.lock:
            with (self.directory / filename).open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def error(self, phase, error):
        with self.lock:
            entry = {"phase": phase, "type": type(error).__name__, "message": str(error)}
            self.state["errors"].append(entry)
            self.state.setdefault("primary_error", entry)
            self.save()


class CH347ConstantWriter:
    """One-thread owner of the CH347 DLL; no target CS assertion is permitted."""
    def __init__(self, dll_path=None, index=None):
        self.dll_path, self.index, self.spi = dll_path, index, None

    def open(self):
        path = sdk._dll_location(self.dll_path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != EXPECTED_DLL_SHA256:
            raise RuntimeError("CH347 DLL SHA256与已审计的1.20版本不同，未打开设备")
        devices = sdk.list_ch347_devices(path)
        candidates = [device for device in devices if device.function_type == 3
                      and device.chip_mode == 0 and device.interface_number == 4
                      and "PID_55DE" in device.device_id.upper()
                      and (self.index is None or device.index == self.index)]
        if len(candidates) != 1:
            raise RuntimeError(f"需要唯一CH347F Mode0 IF4 SPI接口，当前{len(candidates)}个；可用--index指定")
        selected = candidates[0]
        self.spi = sdk.CH347Spi(selected.index, clock_hz=468750, cs=0, dll_path=path)
        self.spi._check(self.spi._dll.CH347SetTimeout(selected.index, 750, 750), "CH347SetTimeout")
        self.spi._deassert()
        return {"dll_path": str(path), "dll_sha256": digest,
                "devices": [asdict(device) for device in devices], "selected_spi": asdict(selected),
                "configuration": self.spi.configuration_summary}

    def transmit(self, value, length):
        if value not in (0x00, 0xFF) or length not in (536, 4096):
            raise RuntimeError("发送白名单仅允许536或4096个00/FF")
        spi = self.spi
        if spi is None or not spi._opened:
            raise RuntimeError("CH347设备未打开")
        buffer = (ctypes.c_ubyte * length).from_buffer_copy(bytes([value]) * length)
        # CS bit7=0: this SDK call cannot assert/deassert hardware CS. The
        # surrounding owner never invokes spi.exchange(), which asserts CS.
        call_start = _clock()
        try:
            result = spi._dll.CH347SPI_WriteRead(spi.device_index, 0, length, buffer)
        except BaseException as error:
            error.spi_api_window = (call_start, _clock())
            raise
        call_end = _clock()
        try:
            spi._check(result, "CH347SPI_WriteRead(CS保持高)")
        except BaseException as error:
            error.spi_api_window = (call_start, call_end)
            raise
        return call_start, call_end

    def close(self):
        if self.spi is not None:
            spi, self.spi = self.spi, None
            spi.close()  # Existing close attempts physical CS level 1 first.


class Phase:
    def __init__(self, number, value, duration):
        self.number, self.value, self.duration = number, value, duration
        self.done = threading.Event()
        self.transactions = []


class Writer:
    def __init__(self, factory, record, deadline, *, max_transfers=MAX_TRANSFERS_PER_PHASE):
        self.factory, self.record, self.deadline, self.max_transfers = factory, record, deadline, max_transfers
        self.ready, self.stop, self.closed = threading.Event(), threading.Event(), threading.Event()
        self.commands = queue.Queue()
        self.failure = None
        self.metadata = None
        self.thread = threading.Thread(target=self.run, name="CH347-only-writer", daemon=True)

    def run(self):
        backend = None
        phase = None
        try:
            backend = self.factory()
            self.metadata = backend.open()
            self.ready.set()
            while not self.stop.is_set() and _clock() < self.deadline:
                try:
                    phase = self.commands.get(timeout=0.02)
                except queue.Empty:
                    continue
                phase_end = min(self.deadline, _clock() + phase.duration)
                try:
                    for number in range(self.max_transfers):
                        if self.stop.is_set() or _clock() >= phase_end:
                            break
                        start = _clock()
                        transaction = {"phase": phase.number, "number": number,
                                       "value": phase.value, "length": TRANSFER_LENGTH,
                                       "iChipSelect": 0, "attempt_start": start,
                                       "host_start": None, "host_end": None, "success": False}
                        self.record.append("transactions.jsonl", {**transaction, "event": "begin"})
                        try:
                            call_start, call_end = backend.transmit(phase.value, TRANSFER_LENGTH)
                            if not start <= call_start < call_end <= _clock():
                                raise RuntimeError("传输后端没有返回有效的实际API调用窗口")
                            transaction.update(host_start=call_start, host_end=call_end)
                            transaction["success"] = True
                        except BaseException as error:
                            transaction["error"] = str(error)
                            window = getattr(error, "spi_api_window", None)
                            if window is not None:
                                transaction.update(host_start=window[0], host_end=window[1])
                            raise
                        finally:
                            transaction["attempt_end"] = _clock()
                            phase.transactions.append(transaction)
                            self.record.append("transactions.jsonl", {**transaction, "event": "end"})
                            with self.record.lock:
                                self.record.state["transactions"] += 1
                except BaseException as error:
                    self.failure = str(error)
                    self.stop.set()
                    raise
                finally:
                    phase.done.set()
                    phase = None
        except BaseException as error:
            self.failure = str(error)
            self.stop.set()
            self.record.error("ch347_writer", error)
        finally:
            self.ready.set()
            if phase is not None:
                phase.done.set()
            if backend is not None:
                try:
                    backend.close()
                except BaseException as error:
                    self.failure = self.failure or str(error)
                    self.record.error("ch347_close", error)
            self.record.update(writer_cleanup_completed=True)
            self.closed.set()


def read_checked(reader, address):
    if address not in READ_ADDRESSES:
        raise RuntimeError("J-Link读取地址不在白名单")
    return int(reader.read32(address)) & 0xFFFFFFFF


def initial_configuration(reader):
    values = {address: read_checked(reader, address) for address in
              (GPIOA_MODER, GPIOA_AFRL, GPIOA_AFRH, GPIOA_IDR)}
    moder, afrl, afrh = values[GPIOA_MODER], values[GPIOA_AFRL], values[GPIOA_AFRH]
    checks = {"pa0_alternate_mode": moder & 3 == 2,
              "pa0_af0": afrl & 15 == 0,
              "pa3_alternate_mode": (moder >> 6) & 3 == 2,
              "pa3_af0": (afrl >> 12) & 15 == 0,
              "pa4_alternate_mode": (moder >> 8) & 3 == 2,
              "pa4_af1": (afrl >> 16) & 15 == 1,
              "pa8_alternate_mode": (moder >> 16) & 3 == 2,
              "pa8_af1": afrh & 15 == 1,
              "nss_high": bool(values[GPIOA_IDR] & 0x100)}
    return {"registers": {f"0x{address:08X}": f"0x{value:08X}" for address, value in values.items()},
            "checks": checks, "valid": all(checks.values())}


def analyze_phase(phase, samples):
    windows = [transaction for transaction in phase.transactions if transaction["success"]]
    # Include only a read whose entire access interval is inside a completed,
    # successful API call. Samples during gaps, failed calls or incomplete calls
    # remain in the raw log but cannot support a signal-state conclusion.
    eligible = [sample for sample in samples if any(window["host_start"] <= sample["host_start"]
                and sample["host_end"] <= window["host_end"] for window in windows)]
    expected = 0 if phase.value == 0 else 1
    matched = sum(sample["mosi"] == expected for sample in eligible)
    sck_low = sum(not sample["sck"] for sample in eligible)
    sck_high = sum(bool(sample["sck"]) for sample in eligible)
    clock_seen = bool(sck_low and sck_high)
    witnessed = matched >= MIN_MATCHING_SAMPLES and clock_seen
    return {"phase": phase.number, "value": phase.value, "expected_mosi": expected,
            "completed": phase.done.is_set(), "successful_transfers": len(windows),
            "samples_total": len(samples), "samples_in_successful_api_windows": len(eligible),
            "mosi_low": sum(not sample["mosi"] for sample in eligible),
            "mosi_high": sum(bool(sample["mosi"]) for sample in eligible),
            "sck_low": sck_low, "sck_high": sck_high, "sck_both_levels_observed": clock_seen,
            "matching_samples": matched, "matching_level_observed": matched >= MIN_MATCHING_SAMPLES,
            "matching_observed": witnessed,
            "nss_low_observed": any(not sample["nss"] for sample in samples),
            "classification": ("matched_level_observed" if witnessed else
                               "insufficient_no_overlapping_samples" if not eligible else
                               "insufficient_sck_levels" if not clock_seen else
                               "requested_level_not_observed"),
            "transactions": phase.transactions}


def run_witness(record, reader, backend_factory, *, phase_seconds=PHASE_SECONDS,
                sample_interval=0.01, max_transfers=MAX_TRANSFERS_PER_PHASE,
                ready_timeout=5.0, phase_grace=2.0, join_timeout=2.0,
                total_seconds=MAX_TOTAL_SECONDS):
    started = _clock()
    deadline = started + total_seconds
    writer = None
    phase, samples = None, []
    record.update(started_host_monotonic=started)
    try:
        reader.open()  # The J-Link reader checks CPU running; no reset/halt/go.
        initial = initial_configuration(reader)
        record.update(initial_gpio_configuration=initial)
        if not initial["valid"]:
            raise RuntimeError("初始GPIO不符合PA4/PA8 AF1或NSS没有保持高，未启动CH347发送")
        writer = Writer(backend_factory, record, deadline, max_transfers=max_transfers)
        writer.thread.start()
        if not writer.ready.wait(min(ready_timeout, max(0.0, deadline - _clock()))):
            raise RuntimeError("CH347初始化线程未按时返回，停止后续阶段")
        if writer.failure:
            raise RuntimeError("CH347初始化失败：" + writer.failure)
        record.update(ch347=writer.metadata)
        for number, value in enumerate(PATTERNS):
            if writer.stop.is_set() or not writer.thread.is_alive() or _clock() >= deadline:
                raise RuntimeError("发送线程已停止或超过整体时限，未启动后续阶段")
            # Explicitly check CS before admitting a new constant-data phase.
            if not read_checked(reader, GPIOA_IDR) & 0x100:
                writer.stop.set()
                raise RuntimeError("观察到NSS低，已停止后续发送")
            phase, samples = Phase(number, value, phase_seconds), []
            phase_deadline = min(deadline, _clock() + phase_seconds + phase_grace)
            writer.commands.put(phase)
            while not phase.done.is_set():
                if _clock() >= phase_deadline:
                    raise RuntimeError("CH347阶段未按时完成，停止后续发送")
                if len(samples) >= MAX_SAMPLES_PER_PHASE:
                    raise RuntimeError("达到本阶段采样次数上限，停止后续发送")
                sample_start = _clock()
                idr = read_checked(reader, GPIOA_IDR)
                sample = {"phase": number, "value": value, "host_start": sample_start,
                          "host_end": _clock(), "address": GPIOA_IDR, "idr": idr,
                          "mosi": (idr >> 4) & 1, "sck": idr & 1, "nss": (idr >> 8) & 1}
                samples.append(sample)
                record.append("samples.jsonl", sample)
                with record.lock:
                    record.state["samples"] += 1
                if not sample["nss"]:
                    writer.stop.set()
                    raise RuntimeError("观察到NSS低，已停止后续发送")
                if sample_interval:
                    time.sleep(min(sample_interval, max(0.0, phase_deadline - _clock())))
            with record.lock:
                record.state["phases"].append(analyze_phase(phase, samples))
                record.save()
            phase, samples = None, []
            if writer.failure or writer.stop.is_set():
                raise RuntimeError("CH347发送失败，未启动后续阶段")
    except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as error:
        record.error("observation", error or "用户中断")
    finally:
        if writer is not None:
            writer.stop.set()
            writer.thread.join(min(join_timeout, max(0.0, deadline - _clock())))
            if writer.thread.is_alive():
                record.error("writer_join_timeout", RuntimeError("SDK调用尚未返回；不再发送，不能确认CH347释放/关闭完成"))
                record.update(writer_cleanup_completed=False)
            if phase is not None:
                with record.lock:
                    record.state["phases"].append(analyze_phase(phase, samples))
        try:
            reader.close()
        except Exception as error:
            record.error("jlink_close", error)
        events = [str(event) for event in getattr(reader, "events", [])]
        record.update(jlink={"exe_path": getattr(reader, "exe_path", None),
                             "dll_path": getattr(reader, "dll_path", None), "events": events,
                             "diagnostics": getattr(reader, "diagnostics", {})})
        (record.directory / "jlink_session.log").write_text("\n".join(events) + "\n", encoding="utf-8")
        with record.lock:
            phases = record.state["phases"]
            ok = len(phases) == len(PATTERNS) and all(phase["matching_observed"] for phase in phases)
            ok = ok and not record.state["errors"] and record.state.get("writer_cleanup_completed", False)
            record.state.update(gpio_witness_ok=ok,
                                status="matched_levels_observed" if ok else "failed" if record.state["errors"] else "insufficient_evidence",
                                ended_host_monotonic=_clock(),
                                elapsed_seconds=_clock() - started)
            record.save()
    return writer


def print_result(record):
    if not record.state["phases"]:
        print("尚未开始对照发送和GPIO采样；本次停在连接/初始化阶段。", flush=True)
        for error in record.state["errors"]:
            print(f"{error['phase']}: {error['message']}", flush=True)
        print(f"日志：{record.directory}", flush=True)
        return
    print("PA4定值段软件见证：", flush=True)
    for phase in record.state["phases"]:
        verdict = "已观察到对应电平" if phase["matching_observed"] else "未观察到对应电平／证据不足"
        print(f"阶段{phase['phase'] + 1} {phase['value']:02X}：{verdict}；"
              f"有效采样{phase['samples_in_successful_api_windows']}，"
              f"PA4低{phase['mosi_low']}／高{phase['mosi_high']}，"
              f"SCK低{phase['sck_low']}／高{phase['sck_high']}", flush=True)
    if record.state.get("gpio_witness_ok"):
        print("已观察到PA4随00/FF阶段出现低/高电平，随后可集中核对SPI输入采样路径。", flush=True)
    else:
        print("本次未获得完整低/高对应证据；这不能单独判定硬件故障。", flush=True)
    for error in record.state["errors"]:
        print(f"{error['phase']}: {error['message']}", flush=True)
    print(f"日志：{record.directory}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--jlink-exe", type=Path, help="本机原厂 JLink.exe 路径")
    parser.add_argument("--no-pause", action="store_true", help="命令行运行结束后直接退出")
    parser.add_argument("--index", type=int)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        from jlink_commander_gpio_reader import self_test as commander_self_test
        commander_self_test()
        return 0
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    record = Record(base / "logs" / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_gpio_witness"))
    print("输入状态工具v3：使用本机原厂 JLink.exe，不使用DLL枚举或序列号选择。", flush=True)
    print("CH347输出00/FF，片选始终保持高；J-Link只读PA4。保持当前接线和v5固件。", flush=True)
    print("不发送烧录、复位或暂停命令；请先关闭占用CH347/J-Link的其他程序。", flush=True)
    try:
        from jlink_commander_gpio_reader import CommanderGPIOReader
        reader = CommanderGPIOReader(path=args.jlink_exe)
        run_witness(record, reader, lambda: CH347ConstantWriter(args.dll, args.index))
    except (OSError, RuntimeError, ValueError, ImportError) as error:
        record.error("startup", error)
        record.update(status="failed", gpio_witness_ok=False)
    print_result(record)
    return 0 if record.state.get("gpio_witness_ok") else 1


def self_test():
    import tempfile

    class FakeBackend:
        def __init__(self, *, fail=False, hang=False):
            self.busy = False
            self.value = 255
            self.fail, self.hang = fail, hang
            self.release = threading.Event()
            self.calls, self.closed = [], False
        def open(self):
            self.owner = threading.get_ident()
            self.calls.append(("open", self.owner))
            return {"fake": True}
        def transmit(self, value, length):
            assert threading.get_ident() == self.owner
            assert value in (0, 255) and length == 4096
            self.calls.append(("write_no_cs", value, length, _clock()))
            start = _clock()
            self.busy, self.value = True, value
            try:
                if self.hang:
                    self.release.wait(5.0)
                else:
                    time.sleep(0.008)
                if self.fail:
                    error = OSError("fake_CH347_failure")
                    error.spi_api_window = (start, _clock())
                    raise error
            finally:
                self.busy = False
            return start, _clock()
        def close(self):
            assert threading.get_ident() == self.owner
            self.calls.append(("release_high_and_close",))
            self.closed = True

    class FakeReader:
        def __init__(self, backend, mode="follow"):
            self.backend, self.mode = backend, mode
            self.calls, self.closed = [], False
            self.events, self.dll_path = ["fake readonly J-Link session"], "fake_JLink_x64.dll"
            self.sample_number = 0
        def open(self):
            self.owner = threading.get_ident()
            self.calls.append(("open",))
            if self.mode == "halted":
                raise RuntimeError("CPU is halted; no halt/go performed")
        def read32(self, address):
            assert threading.get_ident() == self.owner and address in READ_ADDRESSES
            self.calls.append(("read32", address))
            if address == GPIOA_MODER:
                return (2 << 0) | (2 << 6) | (2 << 8) | (2 << 16)
            if address == GPIOA_AFRL:
                return 1 << 16
            if address == GPIOA_AFRH:
                return 1
            if self.backend.busy and self.mode == "read_failure":
                raise OSError("fake_JLink_read_failure")
            low_cs = self.backend.busy and self.mode == "nss_low"
            level = 1 if self.mode == "all_high" else int(self.backend.value != 0)
            self.sample_number += 1
            sck = self.sample_number & 1 if self.backend.busy and self.mode != "clock_stuck" else 0
            time.sleep(0.012 if self.mode == "no_overlap" else 0.0003)
            return (0 if low_cs else 0x100) | (level << 4) | sck
        def close(self):
            self.closed = True
            self.calls.append(("close",))

    with tempfile.TemporaryDirectory(prefix="spi_gpio_witness_") as temporary:
        def run(name, mode="follow", **backend_options):
            backend = FakeBackend(**backend_options)
            reader = FakeReader(backend, mode)
            record = Record(Path(temporary) / name)
            writer = run_witness(record, reader, lambda: backend, phase_seconds=0.045,
                                 sample_interval=0.001, max_transfers=16,
                                 ready_timeout=0.2, phase_grace=0.04,
                                 join_timeout=0.02 if backend_options.get("hang") else 0.15,
                                 total_seconds=2.0)
            assert reader.closed
            assert all(call[0] in ("open", "read32", "close") for call in reader.calls)
            assert all(call[0] in ("open", "write_no_cs", "release_high_and_close") for call in backend.calls)
            assert json.loads((record.directory / "summary.json").read_text(encoding="utf-8")) == record.state
            return backend, reader, record, writer

        backend, _reader, record, _writer = run("following")
        assert record.state["gpio_witness_ok"] and backend.closed, json.dumps(record.state, ensure_ascii=True)
        assert all(phase["matching_samples"] >= 2 for phase in record.state["phases"])
        assert [phase["value"] for phase in record.state["phases"]] == list(PATTERNS)
        _backend, _reader, record, _writer = run("all_high", "all_high")
        assert not record.state["gpio_witness_ok"] and record.state["status"] == "insufficient_evidence"
        assert all(phase["mosi_low"] == 0 for phase in record.state["phases"])
        _backend, _reader, record, _writer = run("clock_stuck", "clock_stuck")
        assert not record.state["gpio_witness_ok"] and not record.state["errors"]
        assert all(phase["classification"] == "insufficient_sck_levels" for phase in record.state["phases"])
        _backend, _reader, record, _writer = run("low_nss", "nss_low")
        assert len(record.state["phases"]) == 1 and record.state["errors"]
        assert record.state["phases"][0]["nss_low_observed"]
        backend, _reader, record, _writer = run("write_failure", fail=True)
        assert backend.closed and any("fake_CH347_failure" in error["message"] for error in record.state["errors"])
        assert len(record.state["phases"]) == 1
        backend, _reader, record, _writer = run("read_failure", "read_failure")
        assert backend.closed and any("fake_JLink_read_failure" in error["message"] for error in record.state["errors"])
        backend, _reader, record, writer = run("hung_writer", hang=True)
        assert not record.state["writer_cleanup_completed"]
        assert any(error["phase"] == "writer_join_timeout" for error in record.state["errors"])
        assert len([call for call in backend.calls if call[0] == "write_no_cs"]) == 1
        backend.release.set()
        writer.thread.join(0.5)
        assert not writer.thread.is_alive() and backend.closed
        _backend, _reader, record, _writer = run("no_overlap", "no_overlap")
        assert not record.state["gpio_witness_ok"]
        assert all(phase["samples_in_successful_api_windows"] == 0 for phase in record.state["phases"])
        backend, _reader, record, _writer = run("halted", "halted")
        assert not backend.calls and "halted" in record.state["primary_error"]["message"]

        # Reject observations from gaps, failed calls and reads spanning an API
        # boundary even if their sampled bit happens to match the requested one.
        phase = Phase(0, 0, 1)
        phase.done.set()
        phase.transactions = [{"host_start": 10, "host_end": 11, "success": True},
                              {"host_start": 12, "host_end": 13, "success": False}]
        samples = [{"host_start": a, "host_end": b, "mosi": 0, "nss": 1, "sck": i & 1}
                   for i, (a, b) in enumerate(((10.1, 10.2), (10.3, 10.4), (9.9, 10.1), (11.1, 11.2), (12.1, 12.2)))]
        result = analyze_phase(phase, samples)
        assert result["samples_in_successful_api_windows"] == 2 and result["matching_observed"]
    print("PASS: GPIO witness fake-only self-test (10 scenarios; no hardware DLL loaded)")


if __name__ == "__main__":
    exit_code = main()
    if getattr(sys, "frozen", False) and "--self-test" not in sys.argv and "--no-pause" not in sys.argv:
        try:
            input("按回车退出...")
        except (EOFError, KeyboardInterrupt):
            pass
    raise SystemExit(exit_code)
