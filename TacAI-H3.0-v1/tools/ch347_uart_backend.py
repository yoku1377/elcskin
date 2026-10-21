"""Receive-only WCH UART backend; importing this module never opens hardware.

Uses the packed Windows x64 mDeviceInforS and CH347Uart_* ABI from the WCH
SDK used by CH347Demo. No UART Write, SPI, GPIO or firmware API is bound.
Pass an injected _dll only for tests; normal use requires CH347DLLA64.dll.
"""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
import sys

ULONG = ctypes.c_uint32
UCHAR = ctypes.c_uint8
USHORT = ctypes.c_uint16
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p


class CH347UartError(OSError):
    pass


class NotUartInterface(CH347UartError):
    """An SDK-openable entry belongs to another interface, not a UART."""
    pass


class DeviceInfo(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("iIndex", UCHAR), ("DevicePath", UCHAR * 260),
        ("UsbClass", UCHAR), ("FuncType", UCHAR),
        ("DeviceID", ctypes.c_char * 64), ("ChipMode", UCHAR),
        ("DevHandle", HANDLE), ("BulkOutEndpMaxSize", USHORT),
        ("BulkInEndpMaxSize", USHORT), ("UsbSpeedType", UCHAR),
        ("CH347IfNum", UCHAR), ("DataUpEndp", UCHAR), ("DataDnEndp", UCHAR),
        ("ProductString", ctypes.c_char * 64),
        ("ManufacturerString", ctypes.c_char * 64),
        ("WriteTimeout", ULONG), ("ReadTimeout", ULONG),
        ("FuncDescStr", ctypes.c_char * 64), ("FirewareVer", UCHAR),
    ]


@dataclass(frozen=True)
class CH347UartDevice:
    index: int
    description: str
    device_id: str
    interface_number: int
    chip_mode: int
    usb_class: int
    product: str = ""

    @property
    def uart_channel(self):
        match = re.search(r"UART\s*([01])\b", self.description, re.IGNORECASE)
        if match:
            return int(match.group(1))
        # CH347F VCP interfaces: UART0=MI_00, UART1=MI_02.
        if "PID_55DE" in self.device_id.upper() and self.interface_number in (0, 2):
            return self.interface_number // 2
        return None


def _bind(dll, signatures):
    for name, (arguments, result) in signatures.items():
        try:
            function = getattr(dll, name)
        except AttributeError as error:
            raise CH347UartError(f"CH347 DLL 缺少 {name}，需要匹配的 Windows x64 SDK") from error
        function.argtypes = arguments
        function.restype = result


def _valid_handle(handle):
    value = handle.value if isinstance(handle, HANDLE) else handle
    return value not in (None, 0, -1, HANDLE(-1).value)


def _integer(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in {minimum}..{maximum}")
    return value


def _load_dll(dll_path=None):
    if os.name != "nt" or ctypes.sizeof(HANDLE) != 8:
        raise CH347UartError("CH347DLLA64.dll 需要 Windows 64 位 Python/EXE")
    explicit = dll_path or os.environ.get("CH347_DLL_PATH")
    if explicit:
        candidates = [Path(explicit).expanduser()]
    else:
        directories = [Path(__file__).resolve().parent]
        if getattr(sys, "frozen", False):
            directories.insert(0, Path(sys.executable).resolve().parent)
            directories.append(Path(getattr(sys, "_MEIPASS", directories[0])))
        candidates = [directory / "CH347DLLA64.dll" for directory in directories]
    path = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise CH347UartError("未找到 CH347DLLA64.dll；请放在 EXE 旁或指定 dll_path/CH347_DLL_PATH")
    cookie = os.add_dll_directory(str(path.parent)) if hasattr(os, "add_dll_directory") else None
    try:
        return ctypes.WinDLL(str(path), use_last_error=True), path, cookie
    except OSError as error:
        if cookie is not None:
            cookie.close()
        raise CH347UartError(f"无法加载 CH347DLLA64.dll：{error}") from error


def _check(result, operation):
    if not result:
        raise CH347UartError(f"{operation} 失败；请关闭占用 CH347 UART 的调试软件")


def _close_after_error(dll, index, error):
    try:
        _check(dll.CH347Uart_Close(index), f"CH347Uart_Close({index})")
    except BaseException as cleanup:
        if hasattr(error, "add_note"):
            error.add_note(f"释放 UART 时还发生：{cleanup}")


def _metadata(dll, index):
    buffer = ctypes.create_string_buffer(4096)
    _check(dll.CH347Uart_GetDeviceInfor(index, buffer), f"CH347Uart_GetDeviceInfor({index})")
    info = DeviceInfo.from_buffer(buffer)
    if info.FuncType != 0:
        raise NotUartInterface(f"CH347 #{index} 返回的接口类型 {info.FuncType} 不是 UART")
    decode = lambda raw: bytes(raw).split(b"\0", 1)[0].decode("utf-8", "replace")
    product = decode(info.ProductString)
    description = (decode(info.FuncDescStr) or product or "CH347 UART")[:160]
    return CH347UartDevice(index, description, decode(info.DeviceID),
                           info.CH347IfNum, info.ChipMode, info.UsbClass, product)


_ENUM_SIGNATURES = {
    "CH347Uart_Open": ([ULONG], HANDLE),
    "CH347Uart_Close": ([ULONG], BOOL),
    "CH347Uart_GetDeviceInfor": ([ULONG, ctypes.c_void_p], BOOL),
}


def list_uart_devices(dll_path=None, *, _dll=None, max_devices=32, cancelled=lambda: False):
    """Enumerate UART SDK indices, closing every opened handle; no Init or Read.

    Returned indices are actual DLL indices, never list positions or COM numbers.
    """
    _integer(max_devices, "max_devices", 1, 32)
    cookie = None
    if _dll is None:
        _dll, _path, cookie = _load_dll(dll_path)
    try:
        _bind(_dll, _ENUM_SIGNATURES)
        devices = []
        for index in range(max_devices):
            if cancelled():
                break
            if not _valid_handle(_dll.CH347Uart_Open(index)):
                continue
            try:
                devices.append(_metadata(_dll, index))
            except NotUartInterface:
                # Enumeration can expose a mixed interface list. Do not let a
                # non-UART slot hide later UART1 entries. SPI and UART still
                # require separate processes: this DLL can reuse cached SPI
                # paths in Uart_Open, even though its handle tables differ.
                _check(_dll.CH347Uart_Close(index), f"CH347Uart_Close({index})")
                continue
            except BaseException as error:
                _close_after_error(_dll, index, error)
                raise
            else:
                _check(_dll.CH347Uart_Close(index), f"CH347Uart_Close({index})")
        return devices
    finally:
        if cookie is not None:
            cookie.close()


class CH347Uart:
    """Open one UART for configurable 8N1 reception, with bounded read timeout."""

    def __init__(self, device_index, baudrate=115200, dll_path=None, *,
                 read_timeout_ms=100, _dll=None):
        self.device_index = _integer(device_index, "device_index", 0, 31)
        _integer(baudrate, "baudrate", 1, 0xFFFFFFFF)
        _integer(read_timeout_ms, "read_timeout_ms", 1, 60000)
        self._opened = False
        self._cookie = None
        self.dll_path = None
        if _dll is None:
            _dll, self.dll_path, self._cookie = _load_dll(dll_path)
        self._dll = _dll
        try:
            _bind(_dll, {
                **_ENUM_SIGNATURES,
                "CH347Uart_Init": ([ULONG, ULONG, UCHAR, UCHAR, UCHAR, UCHAR], BOOL),
                "CH347Uart_SetTimeout": ([ULONG, ULONG, ULONG], BOOL),
                "CH347Uart_Read": ([ULONG, ctypes.c_void_p, ctypes.POINTER(ULONG)], BOOL),
            })
            if not _valid_handle(_dll.CH347Uart_Open(self.device_index)):
                raise CH347UartError(f"无法打开 CH347 UART #{self.device_index}；请关闭 CH347Demo 的 UART 设备")
            self._opened = True
            self.device = _metadata(_dll, self.device_index)
            _check(_dll.CH347Uart_SetTimeout(self.device_index, read_timeout_ms, read_timeout_ms),
                   "CH347Uart_SetTimeout")
            self.configure(baudrate)
        except BaseException as error:
            if self._opened:
                _close_after_error(_dll, self.device_index, error)
                self._opened = False
            if self._cookie is not None:
                self._cookie.close()
                self._cookie = None
            raise

    def configure(self, baudrate):
        _integer(baudrate, "baudrate", 1, 0xFFFFFFFF)
        if not self._opened:
            raise CH347UartError("CH347 UART 已关闭")
        # SDK StopBits=0 means one stop bit; ByteTimeout=0, no parity, 8 bits.
        _check(self._dll.CH347Uart_Init(self.device_index, baudrate, 8, 0, 0, 0),
               f"CH347Uart_Init({baudrate},8N1)")
        self.baudrate = baudrate

    def read(self, max_bytes=4096):
        _integer(max_bytes, "max_bytes", 1, 1024 * 1024)
        if not self._opened:
            raise CH347UartError("CH347 UART 已关闭")
        buffer = ctypes.create_string_buffer(max_bytes)
        length = ULONG(max_bytes)
        _check(self._dll.CH347Uart_Read(self.device_index, buffer, ctypes.byref(length)), "CH347Uart_Read")
        if length.value > max_bytes:
            raise CH347UartError("CH347Uart_Read 返回长度超过接收缓冲区")
        return buffer.raw[:length.value]

    def close(self):
        try:
            if self._opened:
                self._opened = False
                _check(self._dll.CH347Uart_Close(self.device_index), "CH347Uart_Close")
        finally:
            if self._cookie is not None:
                self._cookie.close()
                self._cookie = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.close()
        except BaseException as error:
            if exc_value is None:
                raise
            if hasattr(exc_value, "add_note"):
                exc_value.add_note(f"释放 UART 时还发生：{error}")
        return False


class CH347UartBackend:
    """Small scanner adapter; construction alone does not load or call the DLL."""

    def __init__(self, dll_path=None, *, _dll=None, read_timeout_ms=100):
        self.dll_path = dll_path
        self._dll = _dll
        self.read_timeout_ms = read_timeout_ms
        self._uart = None

    def enumerate_devices(self):
        if self._uart is not None:
            raise CH347UartError("请先关闭当前 UART 再枚举设备")
        return [{**asdict(device), "uart_channel": device.uart_channel}
                for device in list_uart_devices(self.dll_path, _dll=self._dll)]

    def open(self, index):
        if self._uart is not None:
            raise CH347UartError("已有 UART 打开，请先关闭")
        self._uart = CH347Uart(index, dll_path=self.dll_path, _dll=self._dll,
                               read_timeout_ms=self.read_timeout_ms)

    def _require_open(self):
        if self._uart is None:
            raise CH347UartError("CH347 UART 尚未打开")
        return self._uart

    def configure(self, baudrate):
        self._require_open().configure(baudrate)

    def read(self, max_bytes=4096):
        return self._require_open().read(max_bytes)

    def close(self):
        uart, self._uart = self._uart, None
        if uart is not None:
            uart.close()
