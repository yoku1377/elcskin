"""Bounded H3 cmd20 request-path probe with isolated receive-only UART evidence."""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import secrets
import struct
import sys
import time
import zlib

import spi_readcheck as shared
from iap_protocol import encode_frame
from spi_iap_protocol import _decode_response, decode_snapshot

MODES = ("manual_C2", "auto_C2", "write_C4_read_C3")
FF_POLL = b"\xff" * 536
ULONG = ctypes.c_uint32
BOOL = ctypes.c_int32
COUNTERS = ("fall", "rise", "req", "cmd", "bad", "stage", "commit", "ack", "fault", "resync")


def validate_wire_payload(data):
    data = bytes(data)
    if data == FF_POLL:
        return data
    if len(data) == 16:
        magic, command, flags, seq, length, reserved = struct.unpack_from("<4sBBHHH", data)
        if (magic == b"TAI3" and command == 0x20 and not flags and seq and not length and not reserved
                and struct.unpack_from("<I", data, 12)[0] == zlib.crc32(data[:12]) & 0xFFFFFFFF):
            return data
    raise RuntimeError("Transmit guard: only valid 16-byte cmd20 or 536 FF bytes are allowed")


class ProbeSDK:
    """Only the existing manual exchange or the two explicitly audited SDK paths."""
    def __init__(self, spi, mode):
        if mode not in MODES:
            raise ValueError("Unknown request probe mode")
        self.spi, self.mode = spi, mode
        if mode == "write_C4_read_C3":
            dll = spi._dll
            dll.CH347SPI_Write.argtypes = [ULONG, ULONG, ULONG, ULONG, ctypes.c_void_p]
            dll.CH347SPI_Write.restype = BOOL
            dll.CH347SPI_Read.argtypes = [ULONG, ULONG, ULONG, ctypes.POINTER(ULONG), ctypes.c_void_p]
            dll.CH347SPI_Read.restype = BOOL

    def exchange(self, data):
        data = validate_wire_payload(data)
        if self.mode == "manual_C2":
            return self.spi.exchange(data)
        spi = self.spi
        with spi._lock:
            if not spi._opened:
                raise RuntimeError("SPI device is closed")
            spi._wait_cs_high()
            buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            primary = None
            try:
                if self.mode == "auto_C2":
                    spi._check(spi._dll.CH347SPI_WriteRead(
                        spi.device_index, 0x80, len(data), ctypes.cast(buffer, ctypes.c_void_p)),
                        "CH347SPI_WriteRead(auto CS0)")
                    return bytes(buffer)
                if len(data) == 16:
                    spi._check(spi._dll.CH347SPI_Write(
                        spi.device_index, 0x80, 16, 512, ctypes.cast(buffer, ctypes.c_void_p)),
                        "CH347SPI_Write(C4)")
                    return None  # C4 does not capture MISO. Do not invent RX FF bytes.
                length = ULONG(536)
                spi._check(spi._dll.CH347SPI_Read(
                    spi.device_index, 0x80, 0, ctypes.byref(length), ctypes.cast(buffer, ctypes.c_void_p)),
                    "CH347SPI_Read(C3)")
                if length.value > 536:
                    raise RuntimeError(f"CH347SPI_Read returned invalid length {length.value}")
                return bytes(buffer[:length.value])
            except BaseException as error:
                primary = error
                raise
            finally:
                try:
                    # Also release on SDK errors and establish the next >=2ms gap.
                    spi._deassert()
                except BaseException as cleanup:
                    if primary is None:
                        raise
                    if hasattr(primary, "add_note"):
                        primary.add_note(f"CS release also failed: {cleanup}")


class ProbeWire:
    def __init__(self, record, stage, backend, seen, clock=time.monotonic):
        self.record, self.stage, self.backend, self.seen, self.clock = record, stage, backend, seen, clock
        self.directory = record.directory / stage["mode"]
        self.directory.mkdir(parents=True, exist_ok=True)
        self.request_sent = False
        self.polls_sent = 0

    def exchange(self, data, kind, number=0):
        data = bytes(data)
        if kind == "request":
            if self.request_sent or len(data) != 16:
                raise RuntimeError("Exactly one 16-byte request is allowed per stage")
            magic, command, flags, seq, length, reserved = struct.unpack_from("<4sBBHHH", data)
            if (magic != b"TAI3" or command != 0x20 or flags or length or reserved or not seq
                    or seq in self.seen or seq != self.stage["sequence"]
                    or struct.unpack_from("<I", data, 12)[0] != zlib.crc32(data[:12]) & 0xFFFFFFFF):
                raise RuntimeError("Transmit guard: only a unique valid cmd20 empty request is allowed")
            self.request_sent = True
            self.seen.add(seq)
            self.record.state["snapshot_requests"] = len(self.seen)
            label = "request"
        elif (kind == "poll" and self.request_sent and data == FF_POLL
              and number == self.polls_sent + 1 and self.polls_sent < 3):
            self.polls_sent += 1
            label = f"poll_{number}"
        else:
            raise RuntimeError("Transmit guard: only a request followed by <=3 full FF polls is allowed")
        entry = {"kind": kind, "number": number, "host_start": self.clock(), "tx_length": len(data),
                 "tx_file": f"{self.stage['mode']}/{label}_tx.bin", "rx": None, "rx_length": None,
                 "tx_only": kind == "request" and self.stage["mode"] == MODES[2],
                 "rx_status": "not_captured" if kind == "request" and self.stage["mode"] == MODES[2] else "pending"}
        (self.directory / f"{label}_tx.bin").write_bytes(data)
        try:
            received = self.backend.exchange(data)
            if received is None:
                if kind != "request" or self.stage["mode"] != MODES[2]:
                    raise RuntimeError("Unexpected missing MISO capture")
            else:
                received = bytes(received)
                (self.directory / f"{label}_rx.bin").write_bytes(received)
                entry.update(rx_length=len(received), rx=f"{self.stage['mode']}/{label}_rx.bin",
                             rx_prefix_hex=received[:20].hex(" "), all_ff=received == b"\xff" * len(received),
                             rx_status="captured")
                if len(received) != len(data):
                    raise RuntimeError(f"SPI captured length {len(received)}, expected {len(data)}")
            return received
        except BaseException as error:
            entry["error"] = str(error)
            raise
        finally:
            entry["host_end"] = self.clock()
            self.stage["transactions"].append(entry)
            self.record.state["transactions"] += 1
            self.record.save()


def run_stages(spi, record, *, sequence=None, clock=time.monotonic, sleep=time.sleep):
    seed = sequence if sequence is not None else secrets.randbelow(65533) + 1
    if not 1 <= seed <= 65533:
        raise ValueError("Initial sequence must be in 1..65533")
    seen = set()
    record.state.update(tool_version=3, purpose="request_path_probe", status="probing", stages=[],
                        spi_check_started_host_monotonic=clock())
    for offset, mode in enumerate(MODES):
        stage = {"mode": mode, "sequence": seed + offset, "transactions": [], "polls": [],
                 "protocol_ok": False, "request_count": 0, "errors": []}
        record.state["stages"].append(stage)
        stage["baseline_window"] = {"start": clock()}
        sleep(1.3)
        stage["baseline_window"]["end"] = clock()
        print(f"阶段 {offset + 1}/3：{mode}，仅发一次16字节快照请求，seq={stage['sequence']}", flush=True)
        try:
            backend = ProbeSDK(spi, mode)
            wire = ProbeWire(record, stage, backend, seen, clock)
            request = encode_frame(0x20, stage["sequence"])
            stage["request_hex"] = request.hex(" ")
            stage["request_started"] = clock()
            stage["request_count"] = 1
            wire.exchange(request, "request")
            stage["request_completed"] = clock()
            record.state["snapshot_requests"] = len(seen)
            # Critical: no response poll may overwrite last_rx during this window.
            stage["after_request_window"] = {"start": clock()}
            sleep(1.5)
            stage["after_request_window"]["end"] = clock()
            for number in range(1, 4):
                if number > 1:
                    sleep(0.02)
                received = wire.exchange(FF_POLL, "poll", number)
                answer = {"poll": number}
                stage["polls"].append(answer)
                try:
                    decoded = _decode_response(received)
                    if decoded is None:
                        answer["status"] = "all_ff"
                        continue
                    command, status, seq, payload = decoded
                    answer.update(command=command, device_status=status, sequence=seq)
                    if command != 0xA0 or seq != stage["sequence"]:
                        answer["status"] = "stale_or_other_response"
                        continue
                    if status:
                        answer["status"] = "device_status_error"
                        continue
                    snapshot = asdict(decode_snapshot(payload))
                    answer["status"] = "crc_sequence_snapshot_ok"
                    stage.update(protocol_ok=True, snapshot=snapshot,
                                 firmware_match=snapshot["firmware_id"] == shared.EXPECTED_FIRMWARE,
                                 sample_valid=snapshot["valid_mask"] == 255 and snapshot["baseline_epoch"] > 0
                                 and snapshot["status"] & 0x13 == 3)
                    break
                except (RuntimeError, ValueError, struct.error) as error:
                    answer.update(status="invalid_response", error=str(error))
            stage["polls_completed"] = clock()
        except Exception as error:
            stage["errors"].append(str(error))
            record.problem(mode, error)
            stage.setdefault("request_completed", clock())
            stage.setdefault("polls_completed", clock())
        finally:
            stage["after_poll_window"] = {"start": clock()}
            sleep(1.3)
            stage["after_poll_window"]["end"] = clock()
            record.save()
    record.state.update(spi_check_ended_host_monotonic=clock(), status="finished",
                        any_protocol_ok=any(s["protocol_ok"] for s in record.state["stages"]))
    record.save()


def _delta(before, after):
    if before is None or after is None or not shared._advances(before["device_ms"], after["device_ms"]):
        return None
    return {key: (after[key] - before[key]) & 0xFFFFFFFF for key in COUNTERS}


def classify_stage(stage):
    if stage["protocol_ok"]:
        return "snapshot_crc_sequence_ok"
    baseline, request = stage.get("uart_baseline"), stage.get("uart_after_request")
    delta = stage.get("delta_baseline_to_request")
    final = stage.get("delta_baseline_to_after_poll")
    if not baseline or not request or delta is None:
        return "insufficient_uart_window_evidence"
    if delta["fault"] or delta["resync"]:
        return "mcu_reported_link_fault_or_resync"
    if baseline["flags"] & 0x18 and not delta["req"]:
        return "previous_response_pending_new_request_not_independently_tested"
    if delta["cmd"] and delta["stage"]:
        if final and final["ack"]:
            return "mcu_accepted_request_and_read_but_host_response_unverified"
        return "mcu_accepted_request_response_not_verified"
    if not delta["fall"] and not delta["rise"]:
        return "no_new_cs_irq_edge_in_request_window"
    if request["rx"] == 0:
        return "cs_irq_edges_but_no_complete_rx_byte_recorded"
    if request["rx"] == 16 and request["data"] == 0 and request["head"] == 0:
        return "rx16_but_no_nonff_mosi_ever_recorded"
    if request["data"] == 16 and request["head"] == 0x54414933:
        if (request["data"], request["head"]) == (baseline["data"], baseline["head"]):
            return "retained_TAI3_prefix_unchanged_current_request_content_unattributed"
        return "TAI3_prefix_recorded_but_no_accepted_command"
    return "rx_bytes_recorded_without_verified_snapshot_request"


def annotate_uart_stages(record):
    path = record.directory / "uart_events.jsonl"
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                if event.get("kind") == "spi_diag":
                    rows.append(event)
            except ValueError:
                continue
    for stage in record.state.get("stages", []):
        for name, key in (("uart_baseline", "baseline_window"),
                          ("uart_after_request", "after_request_window"),
                          ("uart_after_poll", "after_poll_window")):
            window = stage.get(key)
            candidates = [event for event in rows if window and
                          window["start"] <= event["host_monotonic"] <= window["end"]]
            stage[name] = candidates[-1]["diagnostic"] if candidates else None
        stage["delta_baseline_to_request"] = _delta(stage["uart_baseline"], stage["uart_after_request"])
        stage["delta_baseline_to_after_poll"] = _delta(stage["uart_baseline"], stage["uart_after_poll"])
        stage["request_uart_window_evidence"] = (
            "new_complete_SPI_DIAG_before_first_poll" if stage["delta_baseline_to_request"] is not None
            else "insufficient_no_new_complete_pre_poll_SPI_DIAG")
        stage["classification"] = classify_stage(stage)
    record.save()


def main():
    # The packaged probe EXE is also the isolated UART worker executable.
    if "--uart-worker" in sys.argv:
        return shared.main()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--index", type=int)
    parser.add_argument("--uart-index", type=int)
    parser.add_argument("--no-uart", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    record = shared.Record(base / "logs" / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_request_probe_v3"))
    record.state.update(tool_version=3, purpose="request_path_probe")
    record.save()
    print("H3请求路径诊断v3：3条16B快照请求，各最多3次536B轮询；UART独立连续接收。", flush=True)
    print("请关闭Demo设备，保持供电和接线。本工具不升级、复位或发送UART命令。", flush=True)
    spi = worker = None
    try:
        if not args.no_uart:
            worker = shared.UARTWorkerProcess(record, args.dll, args.uart_index)
            worker.start()
        from ch347_spi import CH347Spi, list_ch347_devices
        devices = list_ch347_devices(args.dll)
        record.state["spi_devices"] = [asdict(device) for device in devices]
        candidates = [device for device in devices if shared._is_target_spi(device)
                      and (args.index is None or device.index == args.index)]
        if len(candidates) != 1:
            raise RuntimeError("无法唯一确定CH347F SPI(IF4)；请用--index指定")
        selected = candidates[0]
        record.state["selected_spi"] = asdict(selected)
        spi = CH347Spi(selected.index, clock_hz=468750, cs=0, dll_path=args.dll)
        record.state["spi_configuration"] = spi.configuration_summary
        record.state["spi_raw_readback"] = {name: getattr(spi.raw_readback, name)
                                              for name, _ in spi.raw_readback._fields_}
        record.save()
        run_stages(spi, record)
    except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as error:
        record.problem("main", error or "用户中断")
        record.state["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
    finally:
        if worker is not None:
            worker.finish()
        if spi is not None:
            try:
                spi.close()
            except Exception as error:
                record.problem("spi_close", error)
        annotate_uart_stages(record)
        record.save()
    for stage in record.state.get("stages", []):
        print(f"{stage['mode']}：{stage['classification']}；请求后UART={stage.get('uart_after_request')}", flush=True)
    print(f"诊断已保存：{record.directory}；请提供summary.json和整个日志目录。", flush=True)
    return 0 if record.state.get("any_protocol_ok") and not record.state["errors"] else 1


def self_test():
    import tempfile
    import threading

    class Clock:
        now = 100.0
        def clock(self):
            return self.now
        def sleep(self, seconds):
            self.now += seconds

    class Function:
        def __init__(self, function):
            self.function = function
        def __call__(self, *args):
            return self.function(*args)

    class FakeSPI:
        def __init__(self, timing, response="valid", short_read=False):
            self.timing, self.response, self.short_read = timing, response, short_read
            self._opened, self.device_index, self._last_deassert = True, 0, None
            self._lock = threading.Lock()
            self.calls, self.sequence, self.requests = [], 0, 0
            self._dll = type("DLL", (), {})()
            self._dll.CH347SPI_WriteRead = Function(self.write_read)
            self._dll.CH347SPI_Write = Function(self.write)
            self._dll.CH347SPI_Read = Function(self.read)
        def _wait_cs_high(self):
            if self._last_deassert is not None:
                self.timing.sleep(max(0.0, 0.002 - (self.timing.clock() - self._last_deassert)))
        def _deassert(self):
            self._last_deassert = self.timing.clock()
            self.calls.append(("release", self.timing.clock()))
        def _check(self, result, name):
            if not result:
                raise OSError(name)
        def request(self, data):
            assert len(data) == 16 and data[4] == 0x20
            assert struct.unpack_from("<I", data, 12)[0] == zlib.crc32(data[:12]) & 0xFFFFFFFF
            self.sequence = struct.unpack_from("<H", data, 6)[0]
            self.requests += 1
        def response_bytes(self):
            if self.response == "ff":
                return FF_POLL
            payload = struct.pack("<HHIIIIB3x", 1, 3, self.requests * 100, self.requests, 0, 1, 255)
            payload += shared.EXPECTED_FIRMWARE.encode().ljust(32, b"\0") + struct.pack("<27f", *([1.0] * 27))
            seq = self.sequence + (1 if self.response == "stale" else 0)
            frame = bytearray(encode_frame(0xA0, seq, payload))
            if self.response == "crc":
                frame[20] ^= 1
            return bytes(frame).ljust(536, b"\xff")
        def write_read(self, index, cs, length, pointer):
            assert index == 0 and cs in (0, 0x80)
            data = ctypes.string_at(pointer, length)
            self.calls.append(("C2", cs, length, data, self.timing.clock()))
            if length == 16:
                self.request(data)
                answer = b"\xff" * 16
            else:
                assert data == FF_POLL
                answer = self.response_bytes()
            ctypes.memmove(pointer, answer, length)
            return 1
        def write(self, index, cs, length, step, pointer):
            data = ctypes.string_at(pointer, length)
            assert (index, cs, length, step) == (0, 0x80, 16, 512)
            self.calls.append(("C4", cs, length, step, data, self.timing.clock()))
            self.request(data)
            return 1
        def read(self, index, cs, output_length, length_pointer, pointer):
            length = ctypes.cast(length_pointer, ctypes.POINTER(ULONG))
            assert (index, cs, output_length, length[0]) == (0, 0x80, 0, 536)
            assert ctypes.string_at(pointer, 536) == FF_POLL
            self.calls.append(("C3", cs, output_length, int(length[0]), self.timing.clock()))
            answer = self.response_bytes()
            ctypes.memmove(pointer, answer, 536)
            length[0] = 535 if self.short_read else 536
            return 1
        def exchange(self, data):
            self.calls.append(("manual_exchange", bytes(data)))
            self._wait_cs_high()
            buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            self.calls.append(("assert", self.timing.clock()))
            try:
                self.write_read(0, 0, len(data), ctypes.cast(buffer, ctypes.c_void_p))
                return bytes(buffer)
            finally:
                self._deassert()

    with tempfile.TemporaryDirectory(prefix="h3_request_probe_") as temporary:
        for response in ("valid", "ff", "crc", "stale"):
            timing = Clock()
            record = shared.Record(Path(temporary) / response)
            spi = FakeSPI(timing, response)
            run_stages(spi, record, sequence=10000, clock=timing.clock, sleep=timing.sleep)
            assert spi.requests == 3 and record.state["snapshot_requests"] == 3
            assert all(stage["protocol_ok"] == (response == "valid") for stage in record.state["stages"])
            assert len([call for call in spi.calls if call[0] == "C4"]) == 1
            assert spi._dll.CH347SPI_Write.argtypes == [ULONG, ULONG, ULONG, ULONG, ctypes.c_void_p]
            assert spi._dll.CH347SPI_Read.argtypes == [ULONG, ULONG, ULONG, ctypes.POINTER(ULONG), ctypes.c_void_p]
            for stage in record.state["stages"]:
                transactions = stage["transactions"]
                assert transactions[0]["tx_length"] == 16 and stage["request_count"] == 1
                assert 1 <= len(stage["polls"]) <= 3
                assert transactions[1]["host_start"] - stage["request_completed"] >= 1.5 - 1e-8
                assert stage["baseline_window"]["end"] - stage["baseline_window"]["start"] >= 1.3 - 1e-8
                assert (record.directory / stage["mode"] / "request_tx.bin").read_bytes() == encode_frame(0x20, stage["sequence"])
                if stage["mode"] == MODES[2]:
                    assert transactions[0]["rx"] is None and transactions[0]["rx_status"] == "not_captured"
                    assert not (record.directory / stage["mode"] / "request_rx.bin").exists()
                else:
                    assert transactions[0]["rx_length"] == 16
            if response != "valid":
                assert all(len(stage["polls"]) == 3 for stage in record.state["stages"])

        timing = Clock()
        record = shared.Record(Path(temporary) / "short_read")
        run_stages(FakeSPI(timing, short_read=True), record, sequence=11000,
                   clock=timing.clock, sleep=timing.sleep)
        assert record.state["stages"][2]["errors"] and not record.state["stages"][2]["protocol_ok"]
        assert (record.directory / MODES[2] / "poll_1_rx.bin").stat().st_size == 535

        # No native API is reachable for a rejected command or malformed request.
        for mode in MODES:
            timing = Clock()
            spi = FakeSPI(timing)
            backend = ProbeSDK(spi, mode)
            corrupt_request = bytearray(encode_frame(0x20, 500))
            corrupt_request[-1] ^= 1
            for bad in (encode_frame(0x21, 500), encode_frame(2, 501), encode_frame(0x20, 0),
                        encode_frame(0x20, 502, b"payload"), encode_frame(0x20, 503, flags=1),
                        bytes(corrupt_request), b"\xff" * 535):
                try:
                    backend.exchange(bad)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Forbidden data reached native SPI API")
            assert not spi.calls

        # Assert the UART time-window association and bounded diagnostic labels.
        record = shared.Record(Path(temporary) / "uart_windows")
        record.state["stages"] = []
        events = []
        for number, expected in enumerate(("cs_irq_edges_but_no_complete_rx_byte_recorded",
                                          "rx16_but_no_nonff_mosi_ever_recorded",
                                          "mcu_accepted_request_and_read_but_host_response_unverified")):
            baseline = shared.parse_spi_diagnostics(shared._fixture_diagnostic(100))[0]
            request = {**baseline, "device_ms": 200, "fall": 13, "rise": 13}
            final = {**request, "device_ms": 300}
            if number == 1:
                request["rx"] = 16
            if number == 2:
                request.update(rx=16, data=16, head=0x54414933, req=1, cmd=1, stage=1)
                final.update(req=1, cmd=1, stage=1, commit=1, ack=1)
            start = number * 10
            stage = {"mode": MODES[number], "protocol_ok": False,
                     "baseline_window": {"start": start, "end": start + 1.3},
                     "after_request_window": {"start": start + 2, "end": start + 3.5},
                     "after_poll_window": {"start": start + 4, "end": start + 5.3}}
            record.state["stages"].append(stage)
            for host, row in ((start + 1, baseline), (start + 3, request), (start + 5, final)):
                events.append({"kind": "spi_diag", "host_monotonic": host, "diagnostic": row})
            stage["expected_classification"] = expected
        (record.directory / "uart_events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        annotate_uart_stages(record)
        assert all(stage["classification"] == stage["expected_classification"] for stage in record.state["stages"])
        first = record.state["stages"][0]
        baseline = {**first["uart_baseline"], "data": 16, "head": 0x54414933}
        first.update(uart_baseline=baseline, uart_after_request={**baseline, "device_ms": 200,
                                                               "fall": 13, "rise": 13, "rx": 16})
        first["delta_baseline_to_request"] = _delta(first["uart_baseline"], first["uart_after_request"])
        assert classify_stage(first) == "retained_TAI3_prefix_unchanged_current_request_content_unattributed"
        # No after-request row may be substituted with the later 536-byte poll evidence.
        (record.directory / "uart_events.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events if event["host_monotonic"] != 3), encoding="utf-8")
        annotate_uart_stages(record)
        assert first["classification"] == "insufficient_uart_window_evidence"
        assert first["uart_after_request"] is None and first["uart_after_poll"] is not None
        assert first["request_uart_window_evidence"].startswith("insufficient")

        # The same frozen probe EXE must route private worker flags without pause.
        record = shared.Record(Path(temporary) / "real_worker_fixture")
        worker = shared.UARTWorkerProcess(record, fixture=True)
        try:
            assert worker.start(), record.state["warnings"]
            record.state["spi_check_started_host_monotonic"] = time.monotonic()
            (record.directory / "uart_worker_fixture_after").touch()
            record.state["spi_check_ended_host_monotonic"] = time.monotonic()
        finally:
            worker.finish()
        assert worker.process.returncode == 0 and record.state["spi_diag_delta"]["req"] == 3
    print("PASS: three request paths and five-argument SDK ABI, strict cmd20/FF guard, quiet request window, "
          "CRC/sequence/all-FF checks, tx-only C4 records, short-read rejection, UART window labels and real isolated worker")


if __name__ == "__main__":
    status = 0
    try:
        status = main()
    except (OSError, RuntimeError, ValueError, EOFError) as error:
        print(f"失败：{error}", file=sys.stderr, flush=True)
        status = 1
    if (getattr(sys, "frozen", False) and "--self-test" not in sys.argv
            and "--uart-worker" not in sys.argv):
        try:
            input("按回车关闭窗口。")
        except EOFError:
            pass
    raise SystemExit(status)
