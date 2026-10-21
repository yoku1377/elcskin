"""Restricted, running-target GPIO reads through a locally installed J-Link DLL.

No DLL is loaded on import or by --self-test.  No SEGGER binary is redistributed.
Only the fixed STM32G031 register whitelist below is accessible.  In particular,
SPI_DR is excluded: reading it would consume sensor RX data.

ABI references (checked 2026-10-16):
https://raw.githubusercontent.com/square/pylink/master/pylink/jlink.py
https://raw.githubusercontent.com/square/pylink/master/pylink/library.py
https://raw.githubusercontent.com/square/pylink/master/pylink/enums.py
https://kb.segger.com/J-Link_Command_Strings
https://kb.segger.com/Memory_accesses
SEGGER SDK V6.98a declarations (unaltered vendor header in an upstream project):
https://raw.githubusercontent.com/asmwarrior/FactoryTestApp/master/3rdParty/JLink_SDK_V698a/Inc/JLinkARMDLL.h
https://raw.githubusercontent.com/asmwarrior/FactoryTestApp/master/3rdParty/JLink_SDK_V698a/Inc/JLINKARM_Const.h

JLINKARM_* exports use cdecl; the unsecure hook setter is stdcall in PyLink.
This backend requires Windows x64, where those calling conventions share the
same ABI. ReadMemEx(address, byte_count, data, access_width) returns bytes read.
Cortex-M supports background memory access; a halted CPU is rejected, never
resumed by this module. Attaching still establishes a hardware debug session.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
from pathlib import Path
import struct
import sys
import threading

GPIOA_IDR = 0x50000010
GPIOB_IDR = 0x50000410
RCC_IOPENR = 0x40021034
CPUID = 0xE000ED00
DBGMCU_IDCODE = 0x40015800
DEVICE = "STM32G031G8"

REGISTER_NAMES = {
    **{base + offset: f"GPIO{port}_{name}"
       for port, base in (("A", 0x50000000), ("B", 0x50000400))
       for offset, name in ((0x00, "MODER"), (0x04, "OTYPER"), (0x08, "OSPEEDR"),
                            (0x0C, "PUPDR"), (0x10, "IDR"), (0x14, "ODR"),
                            (0x1C, "LCKR"), (0x20, "AFR0"), (0x24, "AFR1"))},
    **{0x40021000 + offset: f"RCC_{name}" for offset, name in (
        (0x00, "CR"), (0x04, "ICSCR"), (0x08, "CFGR"), (0x0C, "PLLCFGR"),
        (0x34, "IOPENR"), (0x38, "AHBENR"), (0x3C, "APBENR1"),
        (0x40, "APBENR2"), (0x54, "CCIPR"), (0x5C, "BDCR"))},
    CPUID: "CPUID", DBGMCU_IDCODE: "DBGMCU_IDCODE",
}
ALLOWED_ADDRESSES = frozenset(REGISTER_NAMES)
LOG_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
UNSECURE_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p,
                                    ctypes.c_char_p, ctypes.c_uint32)
DLG_BUTTON_NO = 2
MAX_USB_PROBES = 16


class JLinkConnectInfo(ctypes.Structure):
    """SEGGER JLINKARM_EMU_CONNECT_INFO, SDK V6.98a, natural alignment.

    JLINKARM_Const.h specifies 264 bytes, with 34 reserved trailing bytes.
    Connection is unsigned (32-bit), not a packed one-byte field.
    """
    _fields_ = [
        ("SerialNumber", ctypes.c_uint32), ("Connection", ctypes.c_uint32),
        ("USBAddr", ctypes.c_uint32), ("aIPAddr", ctypes.c_uint8 * 16),
        ("Time", ctypes.c_int32), ("Time_us", ctypes.c_uint64),
        ("HWVersion", ctypes.c_uint32), ("abMACAddr", ctypes.c_uint8 * 6),
        ("acProduct", ctypes.c_char * 32), ("acNickName", ctypes.c_char * 32),
        ("acFWString", ctypes.c_char * 112), ("IsDHCPAssignedIP", ctypes.c_char),
        ("IsDHCPAssignedIPIsValid", ctypes.c_char), ("NumIPConnections", ctypes.c_char),
        ("NumIPConnectionsIsValid", ctypes.c_char), ("aPadding", ctypes.c_uint8 * 34),
    ]


assert ctypes.sizeof(JLinkConnectInfo) == 264
assert JLinkConnectInfo.USBAddr.offset == 8 and JLinkConnectInfo.Time_us.offset == 32

# These commands configure the host/debug session, not target application data.
# An unsupported protection is an error; never silently continue to connect.
PRE_OPEN_COMMANDS = ("DisableAutoUpdateFW", "SuppressGUI = 1")
ATTACH_COMMANDS = (
    "InhibitConnectRetries = 1", "ForceAttachTarget = 1",
    "DisableFlashDL", "DisableFlashBPs", "DisableCortexMXPSRAutoCorrectTBit",
    "SetSkipDebugDeInit = 1",
)


class JLinkReadError(RuntimeError):
    pass


def _verify_x64_dll(path):
    """Inspect the PE header without loading the DLL or executing its code."""
    path = Path(path).expanduser().resolve(strict=True)
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise JLinkReadError(f"Not a PE DLL: {path}")
        stream.seek(struct.unpack_from("<I", header, 0x3C)[0])
        signature = stream.read(6)
    if signature != b"PE\0\0\x64\x86":
        raise JLinkReadError(f"A Windows AMD64 JLink_x64.dll is required: {path}")
    return path


def dll_candidates():
    roots = {Path(os.environ[key]) for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")
             if os.environ.get(key)}
    roots.update((Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")))
    candidates = {candidate for root in roots
                  for candidate in (root / "SEGGER").glob("JLink*/JLink_x64.dll")}
    return sorted(candidates, key=lambda p: (p.stat().st_mtime, str(p)), reverse=True)


def discover_dll(path=None):
    if path is not None:
        return _verify_x64_dll(path)
    errors = []
    for candidate in dll_candidates():
        try:
            return _verify_x64_dll(candidate)
        except (OSError, JLinkReadError) as error:
            errors.append(str(error))
    detail = "; ".join(errors)
    raise JLinkReadError("未找到本机已安装的64位 JLink_x64.dll；可显式指定路径。" + detail)


class JLinkGPIOReader:
    """open/read32/close, with fail-closed connection and memory access.

    Use one instance per session. After any error it cannot reconnect or retry;
    create a new instance for a later, explicitly initiated attempt. ``log`` may
    be a callable receiving strings. ``events`` and ``first_error`` survive close.
    ``_dll`` is a fake-injection seam and never causes a real library load.
    """
    def __init__(self, path=None, _dll=None, log=None, serial_no=None):
        self.dll_path = str(path) if path is not None else None
        self.serial_no = serial_no
        self.selected_serial_no = None
        self._dll = _dll
        self._log = log
        self._lock = threading.RLock()
        self._opened = False
        self._close_needed = False
        self.first_error = None
        self.events = []
        self._callbacks = []
        self.diagnostics = {"dll_candidates": [], "dll_path": self.dll_path,
                            "dll_sha256": None, "dll_version": None,
                            "requested_serial_no": serial_no, "usb_probes": [],
                            "selected_serial_no": None, "stage": "not_opened"}

    @property
    def is_open(self):
        return self._opened

    def _note(self, message):
        message = str(message)
        self.events.append(message)
        if self._log is not None:
            try:
                self._log(message)
            except Exception as error:
                self.events.append(f"log callback failed: {error}")

    def _remember(self, error):
        if self.first_error is None:
            self.first_error = str(error)
        self.diagnostics["first_error"] = self.first_error
        self._note(str(error))

    def _bind(self):
        signatures = {
            "JLINKARM_ExecCommand": (ctypes.c_int, [ctypes.c_char_p, ctypes.POINTER(ctypes.c_char), ctypes.c_int]),
            "JLINKARM_GetDLLVersion": (ctypes.c_uint32, []),
            "JLINKARM_EMU_GetList": (ctypes.c_int, [ctypes.c_int, ctypes.POINTER(JLinkConnectInfo), ctypes.c_int]),
            "JLINKARM_EMU_SelectByUSBSN": (ctypes.c_int, [ctypes.c_uint32]),
            "JLINKARM_OpenEx": (ctypes.c_char_p, [LOG_CALLBACK, LOG_CALLBACK]),
            "JLINK_SetHookUnsecureDialog": (ctypes.c_int, [UNSECURE_CALLBACK]),
            "JLINKARM_TIF_Select": (ctypes.c_int, [ctypes.c_int]),
            "JLINKARM_SetSpeed": (None, [ctypes.c_uint32]),
            "JLINKARM_Connect": (ctypes.c_int, []),
            "JLINKARM_IsConnected": (ctypes.c_char, []),
            "JLINKARM_IsHalted": (ctypes.c_byte, []),
            "JLINKARM_ReadMemEx": (ctypes.c_int, [ctypes.c_uint32, ctypes.c_uint32,
                                               ctypes.c_void_p, ctypes.c_uint32]),
            "JLINKARM_Close": (None, []),
        }
        for name, (result, arguments) in signatures.items():
            try:
                function = getattr(self._dll, name)
                function.restype, function.argtypes = result, arguments
            except (AttributeError, TypeError) as error:
                raise JLinkReadError(f"Missing or incompatible J-Link API {name}: {error}") from error

    def _select_usb_probe(self):
        self.diagnostics["stage"] = "usb_enumeration"
        if self.serial_no is not None and (type(self.serial_no) is not int or not 0 < self.serial_no <= 0xFFFFFFFF):
            raise JLinkReadError("J-Link USB serial number must be a nonzero uint32")
        count = self._dll.JLINKARM_EMU_GetList(1, None, 0)
        self.diagnostics["usb_probe_count"] = count
        if not 0 < count <= MAX_USB_PROBES:
            raise JLinkReadError(f"USB J-Link enumeration returned {count}; expected 1..{MAX_USB_PROBES}. No probe was opened")
        array = (JLinkConnectInfo * count)()
        found = self._dll.JLINKARM_EMU_GetList(1, array, count)
        self.diagnostics["usb_probe_fill_count"] = found
        if not 0 < found <= count:
            raise JLinkReadError(f"USB J-Link enumeration changed/failed: {found} entries for capacity {count}. No probe was opened")
        probes = [{"serial_no": int(item.SerialNumber), "connection": int(item.Connection),
                   "usb_addr": int(item.USBAddr),
                   "product": bytes(item.acProduct).decode("utf-8", errors="replace")}
                  for item in array[:found]]
        self.diagnostics["usb_probes"] = probes
        self._note(f"USB J-Link enumeration: {probes}")
        serials = [probe["serial_no"] for probe in probes]
        if any(probe["connection"] != 1 for probe in probes) or 0 in serials or len(set(serials)) != len(serials):
            raise JLinkReadError("USB J-Link enumeration has invalid/duplicate serial numbers or non-USB entries; no probe was opened")
        if self.serial_no is None:
            if len(probes) != 1:
                raise JLinkReadError("Multiple USB J-Links found; an explicit serial number is required. No probe was opened")
            chosen = serials[0]
        elif self.serial_no not in serials:
            raise JLinkReadError(f"Requested USB J-Link serial {self.serial_no} was not enumerated; no probe was opened")
        else:
            chosen = self.serial_no
        self.selected_serial_no = chosen
        self.diagnostics["selected_serial_no"] = chosen
        result = self._dll.JLINKARM_EMU_SelectByUSBSN(chosen)
        self.diagnostics["usb_selection_result"] = result
        if result < 0:
            raise JLinkReadError(f"USB J-Link serial selection failed: {result}, SN={chosen}; no probe was opened")
        self._note(f"USB J-Link selected by serial: {chosen} (index {result})")

    @staticmethod
    def _char_result(value):
        return int.from_bytes(value, "little", signed=True) if isinstance(value, bytes) else int(value)

    def _exec(self, command):
        if command not in (*PRE_OPEN_COMMANDS, *ATTACH_COMMANDS, f"Device = {DEVICE}"):
            raise JLinkReadError("Command is not on the fixed setup whitelist")
        error_buffer = ctypes.create_string_buffer(1024)
        result = self._dll.JLINKARM_ExecCommand(command.encode("ascii"), error_buffer, len(error_buffer))
        text = error_buffer.value.decode("utf-8", errors="replace").strip()
        if result < 0 or text:
            raise JLinkReadError(f"J-Link setup failed ({command}): {text or result}")
        self._note(f"setup: {command}")

    def _running(self):
        # SDK declares char, not int; avoid depending on the upper return bits.
        status = self._char_result(self._dll.JLINKARM_IsHalted())
        if status != 0:
            raise JLinkReadError("目标CPU已停止，未执行Go或复位。" if status > 0 else
                                 f"J-Link IsHalted failed: {status}")

    def _close(self, suppress=False):
        self._opened = False
        if self._close_needed:
            self._close_needed = False
            try:
                self._dll.JLINKARM_Close()
                self._note("closed")
            except Exception as error:
                self._remember(error)
                if not suppress:
                    raise JLinkReadError(str(error)) from error

    def open(self):
        with self._lock:
            if self._opened:
                return self
            if self.first_error is not None:
                raise JLinkReadError(f"This session already failed: {self.first_error}")
            try:
                if self._dll is None:
                    if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
                        raise JLinkReadError("Windows x64 Python/EXE is required")
                    candidates = dll_candidates()
                    self.diagnostics["dll_candidates"] = [str(candidate) for candidate in candidates]
                    self.diagnostics["dll_selection"] = "explicit_path" if self.dll_path else "first_valid_candidate_by_file_mtime"
                    self._note(f"Installed DLL candidates (not version-ranked): {self.diagnostics['dll_candidates']}")
                    self.dll_path = str(discover_dll(self.dll_path))
                    self.diagnostics["dll_path"] = self.dll_path
                    self.diagnostics["dll_sha256"] = hashlib.sha256(Path(self.dll_path).read_bytes()).hexdigest()
                    self._dll = ctypes.CDLL(self.dll_path, winmode=0x1100)
                self._bind()
                version = int(self._dll.JLINKARM_GetDLLVersion())
                self.diagnostics["dll_version"] = version
                self._note(f"DLL: {self.dll_path or 'injected fake'}, version={version}")
                # SuppressGUI is explicitly documented as a pre-Open command.
                # DisableAutoUpdateFW must succeed before opening the probe.
                for command in PRE_OPEN_COMMANDS:
                    self._exec(command)
                self._select_usb_probe()
                log_callback = LOG_CALLBACK(lambda text: self._note((text or b"").decode("utf-8", errors="replace")))
                error_callback = LOG_CALLBACK(lambda text: self._note("DLL: " + (text or b"").decode("utf-8", errors="replace")))

                def refuse_unsecure(title, message, flags):
                    self._note("unsecure refused: " + (message or b"").decode("utf-8", errors="replace"))
                    return DLG_BUTTON_NO

                unsecure_callback = UNSECURE_CALLBACK(refuse_unsecure)
                self._callbacks = [log_callback, error_callback, unsecure_callback]
                self._close_needed = True
                self.diagnostics["stage"] = "usb_probe_open"
                error = self._dll.JLINKARM_OpenEx(log_callback, error_callback)
                if error is not None:
                    raise JLinkReadError(f"J-Link USB probe Open failed: {error.decode('utf-8', errors='replace')}; "
                                         f"SN={self.selected_serial_no}, DLL={self.dll_path}, version={version}. "
                                         "Failure is at host-to-probe USB open; target Device/Connect/read was not attempted")
                self.diagnostics["stage"] = "target_attach_setup"
                # SDK declaration is int STDCALL (not a previous-hook pointer).
                if self._dll.JLINK_SetHookUnsecureDialog(unsecure_callback) < 0:
                    raise JLinkReadError("Cannot install the refuse-unsecure hook")
                for command in ATTACH_COMMANDS:
                    self._exec(command)
                # PyLink set_tif documents 0=success, 1=failure, explicitly
                # correcting the older "previous interface" interpretation.
                if self._dll.JLINKARM_TIF_Select(1) != 0:
                    raise JLinkReadError("Cannot select SWD")
                self._dll.JLINKARM_SetSpeed(1000)
                # PyLink notes that Device may itself perform auto-connect.
                # All protections, interface and speed therefore precede it.
                self._exec(f"Device = {DEVICE}")
                connected = self._char_result(self._dll.JLINKARM_IsConnected())
                if connected < 0:
                    raise JLinkReadError(f"J-Link IsConnected failed: {connected}")
                if not connected:
                    result = self._dll.JLINKARM_Connect()
                    if result < 0:
                        raise JLinkReadError(f"J-Link Connect failed: {result}")
                if self._char_result(self._dll.JLINKARM_IsConnected()) != 1:
                    raise JLinkReadError("J-Link did not establish a target connection")
                self._running()
                self._opened = True
                self.diagnostics["stage"] = "running_target_attached"
                self._note(f"attached: {DEVICE}, SWD, 1000 kHz, running")
                return self
            except Exception as error:
                self._remember(error)
                self._close(suppress=True)
                if isinstance(error, JLinkReadError):
                    raise
                raise JLinkReadError(str(error)) from error

    def read32(self, address):
        with self._lock:
            try:
                if type(address) is not int or address not in ALLOWED_ADDRESSES:
                    raise JLinkReadError(f"Register address is not permitted: {address!r}")
                if not self._opened:
                    raise JLinkReadError("J-Link reader is not open")
                self._running()
                value = ctypes.c_uint32(0xA5A5A5A5)
                count = self._dll.JLINKARM_ReadMemEx(address, 4, ctypes.byref(value), 4)
                if count != 4:
                    raise JLinkReadError(f"J-Link read 0x{address:08X} failed/short: {count} of 4 bytes")
                self._running()
                return value.value
            except Exception as error:
                self._remember(error)
                self._close(suppress=True)
                if isinstance(error, JLinkReadError):
                    raise
                raise JLinkReadError(str(error)) from error

    def close(self):
        with self._lock:
            self._close(suppress=self.first_error is not None)

    def __enter__(self):
        return self.open()

    def __exit__(self, exception_type, exception, traceback):
        if exception is not None:
            self._remember(exception)
        with self._lock:
            self._close(suppress=exception is not None or self.first_error is not None)
        return False


def self_test():
    """Fake-DLL-only checks; also works inside a frozen executable."""
    class Function:
        def __init__(self, function):
            self.function = function
        def __call__(self, *arguments):
            return self.function(*arguments)

    class FakeDLL:
        def __init__(self, *, fail_command=None, connect=0, halted=0, read_count=4,
                     open_error=None, hook_result=0, tif_result=0, auto_connect=False,
                     close_error=False, value=0x12345678, probes=None,
                     enum_count=None, fill_count=None, select_result=0):
            self.calls, self.connected = [], False
            self.fail_command, self.connect_result = fail_command, connect
            self.halted, self.read_count, self.open_error = halted, read_count, open_error
            self.hook_result, self.auto_connect, self.close_error = hook_result, auto_connect, close_error
            self.tif_result, self.value = tif_result, value
            self.probes = [(123456789, 1, 0)] if probes is None else probes
            self.enum_count, self.fill_count, self.select_result = enum_count, fill_count, select_result
            self.hook = None
            for name in ("ExecCommand", "GetDLLVersion", "EMU_GetList", "EMU_SelectByUSBSN", "OpenEx", "TIF_Select", "SetSpeed", "Connect",
                         "IsConnected", "IsHalted", "ReadMemEx", "Close"):
                setattr(self, "JLINKARM_" + name, Function(getattr(self, name)))
            self.JLINK_SetHookUnsecureDialog = Function(self.set_hook)
        def ExecCommand(self, command, error, capacity):
            command = command.decode("ascii")
            self.calls.append(("exec", command))
            if command == self.fail_command:
                error.value = b"unsupported protection"
                return -1
            if command.startswith("Device") and self.auto_connect:
                self.connected = True
            return 0
        def GetDLLVersion(self):
            self.calls.append(("version",)); return 81800
        def EMU_GetList(self, host, entries, capacity):
            self.calls.append(("enum", host, capacity))
            assert host == 1 and 0 <= capacity <= MAX_USB_PROBES
            if entries is None:
                assert capacity == 0
                return len(self.probes) if self.enum_count is None else self.enum_count
            for entry, (serial, connection, usb_addr) in zip(entries, self.probes):
                entry.SerialNumber, entry.Connection, entry.USBAddr = serial, connection, usb_addr
                entry.acProduct = b"Fake J-Link"
            return len(self.probes) if self.fill_count is None else self.fill_count
        def EMU_SelectByUSBSN(self, serial):
            self.calls.append(("serial", serial)); return self.select_result
        def OpenEx(self, log, error):
            self.calls.append(("open",)); log(b"fake open"); return self.open_error
        def set_hook(self, hook):
            self.calls.append(("hook",)); self.hook = hook; return self.hook_result
        def TIF_Select(self, interface):
            self.calls.append(("tif", interface)); return self.tif_result
        def SetSpeed(self, speed):
            self.calls.append(("speed", speed))
        def Connect(self):
            self.calls.append(("connect",))
            self.connected = self.connect_result >= 0
            return self.connect_result
        def IsConnected(self):
            return b"\1" if self.connected else b"\0"
        def IsHalted(self):
            self.calls.append(("halted",)); return self.halted
        def ReadMemEx(self, address, count, buffer, width):
            self.calls.append(("read", address, count, width))
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint32))[0] = self.value
            return self.read_count
        def Close(self):
            self.calls.append(("close",))
            if self.close_error:
                raise OSError("secondary close failure")

    checks = 0
    fake = FakeDLL()
    with JLinkGPIOReader(_dll=fake) as reader:
        assert reader.read32(GPIOA_IDR) == 0x12345678
        assert fake.hook(b"secured", b"erase?", 0xF) == DLG_BUTTON_NO
        assert fake.calls.index(("exec", "DisableAutoUpdateFW")) < fake.calls.index(("open",))
        assert fake.calls.index(("exec", "ForceAttachTarget = 1")) < fake.calls.index(("exec", f"Device = {DEVICE}"))
        assert ("read", GPIOA_IDR, 4, 4) in fake.calls
        assert fake.JLINKARM_ReadMemEx.argtypes == [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    assert fake.calls.count(("close",)) == 1
    assert ("serial", 123456789) in fake.calls
    assert reader.diagnostics["dll_version"] == 81800
    assert reader.diagnostics["usb_probes"][0]["serial_no"] == 123456789
    assert ctypes.sizeof(JLinkConnectInfo) == 264
    assert JLinkConnectInfo.Connection.offset == 4 and JLinkConnectInfo.acProduct.offset == 50
    assert fake.JLINKARM_EMU_SelectByUSBSN.argtypes == [ctypes.c_uint32]
    assert fake.JLINKARM_GetDLLVersion.argtypes == []
    assert fake.JLINKARM_GetDLLVersion.restype is ctypes.c_uint32
    checks += 1
    for command in (*PRE_OPEN_COMMANDS, *ATTACH_COMMANDS):
        fake = FakeDLL(fail_command=command)
        reader = JLinkGPIOReader(_dll=fake)
        try:
            reader.open()
            raise AssertionError("failed protection accepted")
        except JLinkReadError:
            assert ("exec", f"Device = {DEVICE}") not in fake.calls
            assert ("connect",) not in fake.calls
            assert reader.first_error
        checks += 1
    for settings in ({"connect": -1}, {"halted": 1}, {"halted": -1},
                     {"open_error": b"probe absent"}, {"hook_result": -1},
                     {"tif_result": -1}, {"tif_result": 1}):
        fake = FakeDLL(**settings)
        reader = JLinkGPIOReader(_dll=fake)
        try:
            reader.open()
            raise AssertionError("connection failure accepted")
        except JLinkReadError:
            assert fake.calls.count(("connect",)) <= 1
            assert fake.calls.count(("close",)) == 1
            if settings.get("open_error"):
                assert reader.diagnostics["stage"] == "usb_probe_open"
                assert reader.diagnostics["selected_serial_no"] == 123456789
                assert reader.diagnostics["dll_version"] == 81800
                assert reader.diagnostics["usb_probes"]
                assert ("exec", f"Device = {DEVICE}") not in fake.calls
        checks += 1
    for settings, serial in (({"probes": []}, None), ({"enum_count": -1}, None),
                             ({"enum_count": 17}, None), ({"fill_count": 2}, None),
                             ({"fill_count": -1}, None), ({"fill_count": 0}, None),
                             ({"probes": [(0, 1, 0)]}, None),
                             ({"probes": [(5, 2, 0)]}, None),
                             ({"probes": [(5, 1, 0), (5, 1, 1)]}, None),
                             ({"probes": [(5, 1, 0), (6, 1, 1)]}, None),
                             ({"probes": [(5, 1, 0)]}, 6),
                             ({"select_result": -1}, None), ({}, 0)):
        fake = FakeDLL(**settings)
        reader = JLinkGPIOReader(_dll=fake, serial_no=serial)
        try:
            reader.open()
            raise AssertionError("ambiguous/failed USB enumeration accepted")
        except JLinkReadError:
            assert ("open",) not in fake.calls and ("connect",) not in fake.calls
        checks += 1
    fake = FakeDLL(probes=[(5, 1, 0), (6, 1, 1)], select_result=1)
    with JLinkGPIOReader(_dll=fake, serial_no=6) as reader:
        assert ("serial", 6) in fake.calls
        assert reader.diagnostics["usb_selection_result"] == 1
    checks += 1
    fake = FakeDLL(probes=[(123456, 1, 3)])
    with JLinkGPIOReader(_dll=fake) as reader:
        assert ("serial", 123456) in fake.calls
        assert reader.diagnostics["usb_probes"][0]["usb_addr"] == 3
    checks += 1
    for count in (-1, 0, 1, 3, 5):
        fake = FakeDLL(read_count=count, close_error=True)
        reader = JLinkGPIOReader(_dll=fake).open()
        try:
            reader.read32(GPIOA_IDR)
            raise AssertionError("short read accepted")
        except JLinkReadError as error:
            assert "failed/short" in str(error)
            assert "failed/short" in reader.first_error
            assert "secondary close failure" not in reader.first_error
        checks += 1
    for address in (0x4000380C, 0x08000000, 0x20000000, GPIOA_IDR + 1, -1, True):
        fake = FakeDLL()
        reader = JLinkGPIOReader(_dll=fake).open()
        try:
            reader.read32(address)
            raise AssertionError("forbidden address accepted")
        except JLinkReadError:
            assert not any(call[0] == "read" for call in fake.calls)
        checks += 1
    fake = FakeDLL(auto_connect=True)
    reader = JLinkGPIOReader(_dll=fake).open()
    assert ("connect",) not in fake.calls
    fake.halted = 1
    try:
        reader.read32(GPIOA_IDR)
        raise AssertionError("halted sample accepted")
    except JLinkReadError:
        assert not any(call[0] == "read" for call in fake.calls)
    checks += 1
    fake = FakeDLL(value=0, hook_result=3)
    with JLinkGPIOReader(_dll=fake) as reader:
        assert reader.read32(GPIOA_IDR) == 0  # Real zero is distinct from a failed read.
    checks += 1
    fake = FakeDLL(close_error=True)
    reader = JLinkGPIOReader(_dll=fake)
    try:
        with reader:
            raise RuntimeError("primary operation failure")
    except RuntimeError as error:
        assert str(error) == "primary operation failure"
        assert reader.first_error == "primary operation failure"
    checks += 1
    print(f"PASS: {checks} fake-DLL-only J-Link ABI/protection/connection/read/whitelist/cleanup checks; no hardware")
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if not args.self_test:
        parser.error("This backend only exposes --self-test; use the sampling application's UI for hardware reads")
    self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
