"""Windows x64 CH341A SPI master, using WCH's CH341DLLA64.DLL.

ABI/semantics: WCH CH341PAR LIB/CH341/CH341DLL.H (copyright W.ch),
CH341SetStream and CH341StreamSPI4. SetStream bit 7 selects MSB first;
the DLL performs the needed bit conversion, so Python must not reverse bits.
SPI4 is fixed mode 0, single DIN/DOUT, with the SDK's fixed hardware speed.
Each exchange is one SPI4 call with automatic, low-active D0/CS0, including
536-byte responses. Internal USB packets do not create separate CS windows.
No device/DLL is opened on import. Tests inject a fake DLL explicitly.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import struct
import sys
import threading
import time


ULONG = ctypes.c_uint32
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
UCHAR = ctypes.c_uint8
MAX_DEVICES = 16  # WCH mCH341_MAX_NUMBER.
MAX_EXCHANGE_BYTES = 4096  # WCH mMAX_BUFFER_LENGTH / SPI4 DLL bound.
STREAM_MODE = 0x81  # MSB, single DIN/DOUT; retain the default I2C speed bits.
AUTO_CS0 = 0x80
CS_HIGH_SECONDS = 0.002
# The official CH341DLL.H names 0x20 (CH341A) and 0x30 (CH341A3).
# A field device also reports 0x32 on the PID_5512 synchronous interface.
# Accept that exact combination without inferring an undocumented chip model
# or accepting arbitrary future firmware revisions / UART interfaces.
SUPPORTED_SPI_VERSIONS = frozenset((0x20, 0x30, 0x32))
# D0=CS0, D3=SCK, D5=MOSI are outputs. D7/MISO is never made an output;
# this cleanup leaves unused D1/D2/D4 as inputs. SPI4's own native GPIO
# sequence may also drive those unused pins during a transaction.
# The cleanup idle values are CS0 high and SCK low.
SPI_OUTPUT_DIRECTION = 0x29
SPI_IDLE_OUTPUT = 0x01
USB_OPEN_HELP = (
    "请确认 CH341A 模块处于 I2C/SPI 模式，且已安装对应 WCH 同步接口驱动；"
    "只有 UART/COM 接口不够。此阶段尚未与传感器通信"
)


class CH341Error(OSError):
    pass


@dataclass(frozen=True)
class CH341Device:
    index: int
    product: str
    chip_version: int
    device_id: str = ""
    interface_number: int | None = None

    @property
    def spi_exclusion_reason(self):
        if self.chip_version not in SUPPORTED_SPI_VERSIONS:
            return f"未确认支持 SPI 的芯片版本 0x{self.chip_version:X}"
        pid = re.search(r"PID_([0-9A-F]{4})", self.device_id.upper())
        if pid and pid.group(1) != "5512":
            return f"USB PID_{pid.group(1)} 不是 CH341 同步 SPI/I2C 接口 PID_5512"
        if self.chip_version == 0x32 and pid is None:
            return "IC=0x32 需要 USB PID_5512 才能确认是 CH341 同步 SPI/I2C 接口"
        return ""

    @property
    def supports_spi(self):
        return not self.spi_exclusion_reason

    @property
    def diagnostic_summary(self):
        result = self.spi_exclusion_reason or "CH341 SPI/I2C 同步接口候选"
        return (f"CH341 #{self.index}: IC=0x{self.chip_version:X}, "
                f"ID={self.device_id or 'SDK 未返回名称'}, 判定={result}")


def _valid_handle(handle):
    value = handle.value if isinstance(handle, HANDLE) else handle
    return value not in (None, 0, -1, HANDLE(-1).value)


def _bind_functions(dll, signatures):
    for name, (arguments, result) in signatures.items():
        try:
            function = getattr(dll, name)
        except AttributeError as error:
            raise CH341Error(f"WCH DLL 缺少 {name}，请使用匹配的官方 CH341 Windows x64 SDK") from error
        function.argtypes = arguments
        function.restype = result


PROBE_SIGNATURES = {
    "CH341OpenDevice": ([ULONG], HANDLE),
    "CH341CloseDevice": ([ULONG], None),  # Official ABI is VOID, not BOOL.
    "CH341GetDeviceName": ([ULONG], ctypes.c_void_p),
    "CH341GetVerIC": ([ULONG], ULONG),
}


def _dll_location(dll_path=None):
    if dll_path is not None:
        candidates = [Path(dll_path).expanduser()]
    elif os.environ.get("CH341_DLL_PATH"):
        candidates = [Path(os.environ["CH341_DLL_PATH"]).expanduser()]
    else:
        directories = [Path(__file__).resolve().parent]
        if getattr(sys, "frozen", False):
            directories.insert(0, Path(sys.executable).resolve().parent)
            directories.append(Path(getattr(sys, "_MEIPASS", directories[0])))
        candidates = [directory / "CH341DLLA64.DLL" for directory in directories]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise CH341Error(
        "未找到官方 Windows x64 CH341DLLA64.DLL；请使用随上位机分发的文件，"
        "或指定 dll_path / CH341_DLL_PATH。附件中的旧版 32 位 DLL 不能用于此上位机。"
    )


def _verify_x64_pe(path):
    """Reject an old x86 attachment before loading any executable code."""
    try:
        data = path.read_bytes()
        if len(data) < 64 or data[:2] != b"MZ":
            raise ValueError("missing DOS header")
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[offset:offset + 4] != b"PE\0\0":
            raise ValueError("missing PE header")
        machine = struct.unpack_from("<H", data, offset + 4)[0]
        magic = struct.unpack_from("<H", data, offset + 24)[0]
        if machine != 0x8664 or magic != 0x20B:
            raise ValueError(f"machine=0x{machine:04X}, magic=0x{magic:04X}")
    except (OSError, ValueError, struct.error) as error:
        raise CH341Error(f"CH341 DLL 必须是 Windows AMD64 PE32+：{path}（{error}）") from error


def _load_dll(dll_path=None):
    if os.name != "nt" or ctypes.sizeof(HANDLE) != 8:
        raise CH341Error("CH341DLLA64.DLL 需要 Windows 64 位运行环境")
    path = _dll_location(dll_path)
    _verify_x64_pe(path)
    cookie = os.add_dll_directory(str(path.parent)) if hasattr(os, "add_dll_directory") else None
    try:
        return ctypes.WinDLL(str(path), use_last_error=True), path, cookie
    except OSError as error:
        if cookie is not None:
            cookie.close()
        raise CH341Error(f"无法加载 CH341DLLA64.DLL：{error}") from error


def _device_info(dll, index):
    version = int(dll.CH341GetVerIC(index))
    if not version:
        raise CH341Error(f"CH341 #{index} 已打开，但 CH341GetVerIC 未返回有效芯片版本")
    pointer = dll.CH341GetDeviceName(index)
    name = ctypes.string_at(pointer).decode("ascii", "replace") if pointer else ""
    product = ("CH341A" if version in (0x20, 0x30) else
               "CH341 同步接口" if version == 0x32 else "CH341")
    return CH341Device(index, product, version, name)


def _note(primary, cleanup):
    if hasattr(primary, "add_note"):
        primary.add_note(f"同时发生清理错误（保留原始异常）：{cleanup}")


def list_ch341_devices(dll_path=None, *, _dll=None, cancelled=lambda: False):
    """Enumerate without SetStream, GPIO output, CS changes, or SPI payloads.

    Use while acquisition/upgrading is idle. Every valid opened SDK index is
    closed; the SDK's CloseDevice deliberately has no success return value.
    """
    cookie = None
    if _dll is None:
        _dll, _path, cookie = _load_dll(dll_path)
    try:
        _bind_functions(_dll, PROBE_SIGNATURES)
        devices = []
        for index in range(MAX_DEVICES):
            if cancelled():
                break
            if not _valid_handle(_dll.CH341OpenDevice(index)):
                continue
            try:
                info = _device_info(_dll, index)
            except BaseException as primary:
                try:
                    _dll.CH341CloseDevice(index)
                except BaseException as cleanup:
                    _note(primary, cleanup)
                raise
            else:
                _dll.CH341CloseDevice(index)
                devices.append(info)
        return devices
    finally:
        if cookie is not None:
            cookie.close()


class CH341Spi:
    """Fixed mode 0 / MSB / 8-bit / CS0 CH341A transport for SPIIapClient.

    No adjustable CH347 clock value is accepted: CH341 SPI4 uses its fixed
    hardware clock. Failed transfers are never retried by this adapter.
    """

    def __init__(self, device_index=0, dll_path=None, *, _dll=None):
        if (isinstance(device_index, bool) or not isinstance(device_index, int)
                or not 0 <= device_index < MAX_DEVICES):
            raise ValueError(f"device_index must be an integer in 0..{MAX_DEVICES - 1}")
        self.device_index = device_index
        self.clock_hz = None
        self.cs = 0
        self._lock = threading.RLock()
        self._opened = False
        self._configured = False
        self._directory_cookie = None
        self._last_deassert = None
        self.dll_path = None
        if _dll is None:
            self._dll, self.dll_path, self._directory_cookie = _load_dll(dll_path)
        else:
            self._dll = _dll
        try:
            self.dll_sha256 = hashlib.sha256(self.dll_path.read_bytes()).hexdigest() if self.dll_path else ""
            self._bind()
            if not _valid_handle(self._dll.CH341OpenDevice(device_index)):
                raise CH341Error(
                    f"CH341 索引 {device_index} 无法打开 USB 同步接口。{USB_OPEN_HELP}。"
                )
            self._opened = True
            self.device = _device_info(self._dll, device_index)
            if not self.device.supports_spi:
                raise CH341Error(self.device.diagnostic_summary)
            self.dll_version = int(self._dll.CH341GetVersion())
            self._check(self._dll.CH341SetTimeout(device_index, 1000, 1000), "CH341SetTimeout")
            self._check(self._dll.CH341SetStream(device_index, STREAM_MODE), "CH341SetStream(MSB/single SPI)")
            self._configured = True
            self._deassert()
            self.configuration_summary = (
                f"{self.device.product} SPI：Mode 0，MSB，8 bit，CS0/D0 低有效，硬件固定时钟（不可设置 CH347 时钟档位）；"
                "SetStream=0x81，单次 SPI4 保持完整片选窗口；配置无硬件回读 API，实机通信尚需验证。"
                f" DLL version=0x{self.dll_version:X}，SHA256={self.dll_sha256 or '测试注入'}"
            )
        except BaseException as primary:
            try:
                self.close()
            except BaseException as cleanup:
                _note(primary, cleanup)
            raise

    def _bind(self):
        _bind_functions(self._dll, {
            **PROBE_SIGNATURES,
            "CH341GetVersion": ([], ULONG),
            "CH341SetTimeout": ([ULONG, ULONG, ULONG], BOOL),
            "CH341SetStream": ([ULONG, ULONG], BOOL),
            "CH341Set_D5_D0": ([ULONG, ULONG, ULONG], BOOL),
            "CH341StreamSPI4": ([ULONG, ULONG, ULONG, ctypes.c_void_p], BOOL),
        })

    @staticmethod
    def _check(result, operation):
        if not result:
            raise CH341Error(f"{operation} 失败")

    def _deassert(self):
        self._check(self._dll.CH341Set_D5_D0(
            self.device_index, SPI_OUTPUT_DIRECTION, SPI_IDLE_OUTPUT
        ), "CH341Set_D5_D0(CS0 high)")
        self._last_deassert = time.monotonic()

    def _wait_cs_high(self):
        if self._last_deassert is not None:
            remaining = CS_HIGH_SECONDS - (time.monotonic() - self._last_deassert)
            while remaining > 0:
                time.sleep(remaining)
                remaining = CS_HIGH_SECONDS - (time.monotonic() - self._last_deassert)

    def exchange(self, data: bytes) -> bytes:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("SPI exchange requires bytes")
        data = bytes(data)
        if not 1 <= len(data) <= MAX_EXCHANGE_BYTES:
            raise ValueError(f"CH341 SPI exchange length must be in 1..{MAX_EXCHANGE_BYTES}")
        with self._lock:
            if not self._opened:
                raise CH341Error("CH341 SPI 设备已关闭")
            buffer = (UCHAR * len(data)).from_buffer_copy(data)
            self._wait_cs_high()
            try:
                self._check(self._dll.CH341StreamSPI4(
                    self.device_index, AUTO_CS0, len(data), ctypes.cast(buffer, ctypes.c_void_p)
                ), "CH341StreamSPI4")
            except BaseException as primary:
                try:
                    self._deassert()
                except BaseException as cleanup:
                    _note(primary, cleanup)
                raise
            else:
                # Also covers SDK failures leaving an asserted CS in future
                # versions; this GPIO operation sends no SPI clocks/payload.
                self._deassert()
                return bytes(buffer)

    def close(self):
        with self._lock:
            error = None
            if self._opened:
                try:
                    if self._configured:
                        self._deassert()
                except BaseException as caught:
                    error = caught
                try:
                    self._dll.CH341CloseDevice(self.device_index)
                except BaseException as caught:
                    if error is None:
                        error = caught
                    else:
                        _note(error, caught)
                finally:
                    self._opened = False
                    self._configured = False
            if self._directory_cookie is not None:
                try:
                    self._directory_cookie.close()
                except BaseException as caught:
                    if error is None:
                        error = caught
                    else:
                        _note(error, caught)
                finally:
                    self._directory_cookie = None
            if error is not None:
                raise error

    def __enter__(self):
        if not self._opened:
            raise CH341Error("CH341 SPI 设备已关闭")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.close()
        except BaseException as cleanup:
            if exc_value is None:
                raise
            _note(exc_value, cleanup)
        return False


# Keep the familiar mixed-case spelling available to GUI callers.
Ch341Spi = CH341Spi
