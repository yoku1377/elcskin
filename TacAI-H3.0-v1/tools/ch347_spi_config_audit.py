"""Query the CH347F-reported 26-byte SPI configuration; no SPI traffic or init."""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import struct
import sys

_PC_PATH = Path(__file__).resolve().parents[1] / "pc" / "TfgMonitorPython_H3_v5.0"
if _PC_PATH.is_dir():
    sys.path.insert(0, str(_PC_PATH))
import ch347_spi as sdk

EXPECTED_DLL_SHA256 = "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375"
QUERY = bytes.fromhex("CA 01 00 01")
REPLY_HEADER = bytes.fromhex("CA 1A 00")
HARDWARE_FORMAT = struct.Struct("<9HHBB4s")
FIELD_NAMES = ("direction", "mode", "data_size", "cpol", "cpha", "nss", "prescaler",
               "first_bit", "crc_polynomial", "rw_interval_us", "read_default_byte", "misc_flags")
EXPECTED_FIELDS = {"direction": 0x0000, "mode": 0x0104, "data_size": 0x0000,
                   "cpol": 0x0000, "cpha": 0x0000, "nss": 0x0200,
                   "prescaler": 0x0038, "first_bit": 0x0000}
PRIMARY_FIELDS = ("direction", "mode", "nss")
DIRECTIONS = {0: "2Lines_FullDuplex", 0x0400: "2Lines_RxOnly",
              0x8000: "1Line_Rx", 0xC000: "1Line_Tx"}


class Record:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state = {"tool": "ch347_spi_config_audit", "tool_version": 1, "status": "started",
                      "expected_dll_sha256": EXPECTED_DLL_SHA256, "errors": [],
                      "usb_requests": [], "evidence_scope": "bridge_reported_configuration_not_live_register_dump",
                      "operations": "enumerate/open/query_CA010001/close; no SPI_Init, C0, CS, target SPI or UART"}
        self.save()

    def save(self):
        temporary = self.directory / "summary.json.tmp"
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.directory / "summary.json")

    def error(self, phase, error):
        item = {"phase": phase, "type": type(error).__name__, "message": str(error)}
        self.state["errors"].append(item)
        self.state.setdefault("primary_error", item)
        self.state["status"] = "failed"
        self.save()


def is_target(device):
    return (device.function_type == 3 and device.chip_mode == 0
            and device.interface_number == 4 and "PID_55DE" in device.device_id.upper())


def decode_hardware(raw):
    if len(raw) != HARDWARE_FORMAT.size:
        raise RuntimeError(f"Hardware configuration is {len(raw)} bytes, expected 26")
    unpacked = HARDWARE_FORMAT.unpack(raw)
    fields = dict(zip(FIELD_NAMES, unpacked[:-1]))
    fields["reserved_hex"] = unpacked[-1].hex(" ")
    validation = {name: fields[name] == expected for name, expected in EXPECTED_FIELDS.items()}
    return {"fields": fields,
            "fields_hex": {name: f"0x{value:04X}" for name, value in fields.items() if isinstance(value, int)},
            "direction_name": DIRECTIONS.get(fields["direction"], "unknown"),
            "mode_name": {0x0104: "Master", 0x0000: "Slave"}.get(fields["mode"], "unknown"),
            "nss_name": {0x0200: "Software", 0x0000: "Hardware"}.get(fields["nss"], "unknown"),
            "expected_fields": EXPECTED_FIELDS, "validation": validation,
            "primary_concerns": [name for name in PRIMARY_FIELDS if not validation[name]],
            "configuration_matches_expected": all(validation.values())}


class ConfigReader:
    """Only public generic USB exports, with one hardcoded read-only query."""
    def __init__(self, dll, record):
        self.dll, self.record = dll, record
        self.index, self.opened = None, False
        sdk._bind_functions(dll, {
            "CH347OpenDevice": ([sdk.ULONG], sdk.HANDLE),
            "CH347GetDeviceInfor": ([sdk.ULONG, ctypes.c_void_p], sdk.BOOL),
            "CH347WriteData": ([sdk.ULONG, ctypes.c_void_p, ctypes.POINTER(sdk.ULONG)], sdk.BOOL),
            "CH347ReadData": ([sdk.ULONG, ctypes.c_void_p, ctypes.POINTER(sdk.ULONG)], sdk.BOOL),
            "CH347SetTimeout": ([sdk.ULONG, sdk.ULONG, sdk.ULONG], sdk.BOOL),
            "CH347CloseDevice": ([sdk.ULONG], sdk.BOOL),
        })

    def open(self, index):
        if self.opened:
            raise RuntimeError("Configuration reader is already open")
        self.index = index
        if not sdk._valid_handle(self.dll.CH347OpenDevice(index)):
            raise OSError(f"CH347OpenDevice({index}) failed")
        self.opened = True
        buffer = ctypes.create_string_buffer(4096)
        if not self.dll.CH347GetDeviceInfor(index, buffer):
            raise OSError("CH347GetDeviceInfor failed after open")
        info = sdk.DeviceInfo.from_buffer(buffer)
        device = sdk.CH347Device(index, bytes(info.ProductString).decode("utf-8", "replace"),
                                 info.ChipMode, info.FuncType, info.CH347IfNum,
                                 bytes(info.DeviceID).decode("ascii", "replace"),
                                 bytes(info.FuncDescStr).decode("utf-8", "replace"), info.UsbClass)
        self.record.state["rechecked_device"] = asdict(device)
        self.record.save()
        if not is_target(device):
            raise RuntimeError("Opened interface is not CH347F mode 0 SPI IF4 (PID_55DE)")
        if not self.dll.CH347SetTimeout(index, 1000, 1000):
            raise OSError("CH347SetTimeout failed")

    def query(self):
        if not self.opened:
            raise RuntimeError("Configuration reader is closed")
        request = ctypes.create_string_buffer(QUERY, len(QUERY))
        (self.record.directory / "usb_query.bin").write_bytes(QUERY)
        self.record.state["usb_requests"].append(QUERY.hex(" "))
        self.record.save()
        written = sdk.ULONG(len(QUERY))
        succeeded = bool(self.dll.CH347WriteData(self.index, request, ctypes.byref(written)))
        self.record.state["write_result"] = {"success": succeeded, "reported_length": written.value}
        self.record.save()
        if not succeeded:
            raise OSError("CH347WriteData(CA010001) failed")
        if written.value != len(QUERY):
            raise RuntimeError(f"USB query write length {written.value}, expected 4")
        response = (ctypes.c_ubyte * 29)()
        received = sdk.ULONG(29)
        succeeded = bool(self.dll.CH347ReadData(self.index, response, ctypes.byref(received)))
        self.record.state["read_result"] = {
            "success": succeeded, "reported_length": received.value,
            "reported_length_trusted": succeeded and received.value <= 29,
        }
        self.record.save()
        if not succeeded:
            # On failure the SDK may leave the requested length unchanged. The
            # zero-filled buffer is not received data and must not be logged as such.
            raise OSError("CH347ReadData(CA configuration) failed")
        if received.value > 29:
            raise RuntimeError(f"Invalid USB read length {received.value}, buffer capacity 29")
        raw_reply = bytes(response[:received.value])
        (self.record.directory / "usb_response.bin").write_bytes(raw_reply)
        self.record.state["usb_response_hex"] = raw_reply.hex(" ")
        self.record.save()
        if len(raw_reply) != 29:
            raise RuntimeError(f"Short USB configuration reply: {len(raw_reply)} bytes, expected 29; not concatenated")
        if raw_reply[:3] != REPLY_HEADER:
            raise RuntimeError(f"Unexpected USB configuration header {raw_reply[:3].hex(' ')}, expected CA 1A 00")
        raw = raw_reply[3:]
        (self.record.directory / "raw26.bin").write_bytes(raw)
        self.record.state.update(raw26_hex=raw.hex(" "), **decode_hardware(raw))
        self.record.save()

    def close(self):
        if self.opened:
            self.opened = False
            if not self.dll.CH347CloseDevice(self.index):
                raise OSError(f"CH347CloseDevice({self.index}) failed")


def perform_audit(record, dll_path=None, index=None, *, _dll=None, _dll_sha256=EXPECTED_DLL_SHA256):
    reader = cookie = None
    phase = "load"
    try:
        if _dll is None:
            path = sdk._dll_location(dll_path)
            actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            record.state.update(dll_path=str(path), dll_sha256=actual_sha)
        else:
            path, actual_sha = None, _dll_sha256
            record.state.update(dll_path="fake_DLL_for_self_test", dll_sha256=actual_sha)
        record.save()
        if actual_sha.lower() != EXPECTED_DLL_SHA256:
            raise RuntimeError("DLL SHA256 differs from the audited 1.20 binary; no device was opened")
        if _dll is None:
            _dll, _path, cookie = sdk._load_dll(path)
        phase = "enumerate"
        devices = sdk.list_ch347_devices(_dll=_dll)
        record.state["devices"] = [asdict(device) for device in devices]
        candidates = [device for device in devices if is_target(device)
                      and (index is None or device.index == index)]
        record.save()
        if len(candidates) != 1:
            raise RuntimeError(f"Expected exactly one CH347F mode 0 SPI IF4; found {len(candidates)} (use --index if needed)")
        selected = candidates[0]
        record.state["selected_device"] = asdict(selected)
        record.save()
        reader = ConfigReader(_dll, record)
        phase = "open"
        reader.open(selected.index)
        phase = "query"
        reader.query()
        record.state["status"] = ("ok" if record.state["configuration_matches_expected"]
                                  else "configuration_mismatch")
    except (OSError, RuntimeError, ValueError, KeyboardInterrupt) as error:
        record.error(phase, error or "Interrupted")
    finally:
        if reader is not None:
            try:
                reader.close()
            except Exception as error:
                record.error("close", error)
        if cookie is not None:
            try:
                cookie.close()
            except Exception as error:
                record.error("dll_directory_close", error)
        record.save()
    return record.state


def print_result(record):
    state = record.state
    if "fields" in state:
        fields = state["fields"]
        for key, label, name in (("direction", "方向", "direction_name"),
                                  ("mode", "主从", "mode_name"),
                                  ("nss", "NSS", "nss_name")):
            verdict = "符合预期" if state["validation"][key] else "与预期不同"
            print(f"{label}: 0x{fields[key]:04X} {state[name]} — {verdict}", flush=True)
        print("其余Mode0/8bit/MSB/分频配置: " +
              ("符合预期" if state["configuration_matches_expected"] else "请查看summary.json逐项结果"), flush=True)
    for error in state["errors"]:
        print(f"{error['phase']}: {error['message']}", flush=True)
    print(f"已保存: {record.directory}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", type=Path)
    parser.add_argument("--index", type=int)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    record = Record(base / "logs" / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_config_audit"))
    print("CH347F 配置只读审计：只查询桥接器CA配置，不初始化SPI、不发送传感器命令。", flush=True)
    state = perform_audit(record, args.dll, args.index)
    print_result(record)
    if state["errors"]:
        return 1
    return 0 if state.get("configuration_matches_expected") else 2


def self_test():
    import tempfile

    class Function:
        def __init__(self, function):
            self.function = function
        def __call__(self, *args):
            return self.function(*args)

    class FakeDLL:
        def __init__(self, raw=None, *, header=REPLY_HEADER, short=False, read_failure=False,
                     write_failure=False, close_failure=False, absent=False):
            self.calls, self.opens = [], 0
            self.reply = header + (raw if raw is not None else fixture())
            self.short, self.read_failure = short, read_failure
            self.write_failure, self.close_failure, self.absent = write_failure, close_failure, absent
            for name, function in (("CH347OpenDevice", self.open), ("CH347CloseDevice", self.close),
                                   ("CH347GetDeviceInfor", self.info), ("CH347SetTimeout", self.timeout),
                                   ("CH347WriteData", self.write), ("CH347ReadData", self.read)):
                setattr(self, name, Function(function))
        def __getattr__(self, name):
            raise AssertionError(f"Forbidden or unexpected DLL export: {name}")
        def open(self, index):
            self.calls.append(("open", index))
            if index or self.absent:
                return -1
            self.opens += 1
            return 100
        def close(self, index):
            self.calls.append(("close", index))
            return 0 if self.close_failure and self.opens > 1 else 1
        def info(self, index, pointer):
            self.calls.append(("info", index))
            value = sdk.DeviceInfo()
            value.FuncType, value.ChipMode, value.CH347IfNum = 3, 0, 4
            value.DeviceID = b"USB\\VID_1A86&PID_55DE&MI_04"
            value.ProductString = b"CH347F"
            value.FuncDescStr = b"CH347F.M0:USB2.0 To SPI&I2C&JTAG&SWD"
            ctypes.memmove(pointer, ctypes.byref(value), ctypes.sizeof(value))
            return 1
        def timeout(self, index, write_ms, read_ms):
            self.calls.append(("timeout", index, write_ms, read_ms))
            assert (write_ms, read_ms) == (1000, 1000)
            return 1
        def write(self, index, pointer, length_pointer):
            length = ctypes.cast(length_pointer, ctypes.POINTER(sdk.ULONG))
            raw = ctypes.string_at(pointer, length[0])
            self.calls.append(("write", raw))
            assert raw == QUERY
            return 0 if self.write_failure else 1
        def read(self, index, pointer, length_pointer):
            length = ctypes.cast(length_pointer, ctypes.POINTER(sdk.ULONG))
            self.calls.append(("read", int(length[0])))
            assert length[0] == 29
            if self.read_failure:
                return 0  # Deliberately leave requested length unchanged.
            answer = self.reply[:-1] if self.short else self.reply
            ctypes.memmove(pointer, answer, len(answer))
            length[0] = len(answer)
            return 1

    def fixture(**changes):
        fields = dict(EXPECTED_FIELDS, crc_polynomial=7, rw_interval_us=0,
                      read_default_byte=255, misc_flags=0)
        fields.update(changes)
        return HARDWARE_FORMAT.pack(*(fields[name] for name in FIELD_NAMES), b"\0" * 4)

    with tempfile.TemporaryDirectory(prefix="ch347_config_audit_") as temporary:
        def run(name, fake, **kwargs):
            record = Record(Path(temporary) / name)
            state = perform_audit(record, _dll=fake, **kwargs)
            assert json.loads((record.directory / "summary.json").read_text(encoding="utf-8")) == state
            assert all(call[0] in {"open", "close", "info", "timeout", "write", "read"} for call in fake.calls)
            assert [call[1] for call in fake.calls if call[0] == "write"] in ([], [QUERY])
            return record, state

        normal = FakeDLL()
        record, state = run("normal", normal)
        assert state["status"] == "ok" and state["configuration_matches_expected"]
        assert state["primary_concerns"] == []
        assert (record.directory / "raw26.bin").read_bytes() == fixture()
        assert [call for call in normal.calls if call[0] == "write"] == [("write", QUERY)]
        assert normal.calls[-1] == ("close", 0)
        assert normal.CH347ReadData.argtypes == [sdk.ULONG, ctypes.c_void_p, ctypes.POINTER(sdk.ULONG)]
        cases = (("rx_only", "direction", 0x0400), ("one_line_rx", "direction", 0x8000),
                 ("one_line_tx", "direction", 0xC000), ("slave", "mode", 0),
                 ("hardware_nss", "nss", 0), ("sixteen_bits", "data_size", 0x0800))
        for name, field, value in cases:
            _record, result = run(name, FakeDLL(fixture(**{field: value})))
            assert result["status"] == "configuration_mismatch" and not result["validation"][field]
            assert (field in result["primary_concerns"]) == (field in PRIMARY_FIELDS)
        for name, fake, phrase in (("bad_command", FakeDLL(header=b"\xc0\x1a\x00"), "header"),
                                   ("bad_length_header", FakeDLL(header=b"\xca\x19\x00"), "header"),
                                   ("short_reply", FakeDLL(short=True), "Short"),
                                   ("read_failed", FakeDLL(read_failure=True), "CH347ReadData"),
                                   ("write_failed", FakeDLL(write_failure=True), "CH347WriteData"),
                                   ("no_device", FakeDLL(absent=True), "exactly one")):
            record, result = run(name, fake)
            assert result["status"] == "failed" and phrase in result["primary_error"]["message"]
            assert not (record.directory / "raw26.bin").exists()
            if fake.short:
                assert (record.directory / "usb_response.bin").stat().st_size == 28
            if fake.read_failure:
                assert result["read_result"]["reported_length"] == 29
                assert not result["read_result"]["reported_length_trusted"]
                assert not (record.directory / "usb_response.bin").exists()
        _record, result = run("read_and_close_failed", FakeDLL(read_failure=True, close_failure=True))
        assert "CH347ReadData" in result["primary_error"]["message"]
        assert [error["phase"] for error in result["errors"]] == ["query", "close"]
        _record, result = run("close_failed", FakeDLL(close_failure=True))
        assert result["status"] == "failed" and result["primary_error"]["phase"] == "close"
        fake = FakeDLL()
        _record, result = run("wrong_dll", fake, _dll_sha256="0" * 64)
        assert result["status"] == "failed" and not fake.calls
        fake = FakeDLL()
        _record, result = run("wrong_index", fake, index=1)
        assert result["status"] == "failed" and not any(call[0] == "write" for call in fake.calls)
    print("PASS: read-only config audit self-test (17 scenarios; no hardware DLL loaded)")


if __name__ == "__main__":
    exit_code = main()
    if getattr(sys, "frozen", False) and "--self-test" not in sys.argv:
        try:
            input("按回车退出...")
        except (EOFError, KeyboardInterrupt):
            pass
    raise SystemExit(exit_code)
