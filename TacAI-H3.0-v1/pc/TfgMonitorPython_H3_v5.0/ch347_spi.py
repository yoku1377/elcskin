"""Windows x64 CH347F SPI master via WCH CH347DLLA64.dll.

ABI: WCH-copyright CH347-DLL V1.4 Windows header source mirror, WINAPI/
stdcall, pragma pack(1), mSpiCfgS (20 bytes). Evidence and provenance are in
SPI_预联调_20261014/references/CH347DLL_EN_mirror.H and
CH347_Windows_ABI_provenance.json; this is not an official DLL runtime test.
The header's iByteOrder=1 means MSB first.
SPI CS is controlled explicitly around one WriteRead call, even above the
USB packet length. No driver is installed and no device is opened on import.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sys
import threading
import time


ULONG = ctypes.c_uint32  # Windows ULONG stays 32-bit in the 64-bit DLL ABI.
USHORT = ctypes.c_uint16
UCHAR = ctypes.c_uint8
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p

CLOCKS_HZ = (60000000, 30000000, 15000000, 7500000, 3750000, 1875000, 937500, 468750)
CS_HIGH_SECONDS = 0.002
# ChangeCS forwards the physical CS level to C1 bit 6: 0 asserts the
# configured low-active line, 1 releases it. Verified in the shipped x64
# DLL (SetChipSelect RVA 0xE970, ChangeCS 0xEB30) and WCH's Linux driver
# (SET_CS=0, CLR_CS=1); some Windows-header comments reverse these values.
CS_ASSERT_LEVEL = 0
CS_DEASSERT_LEVEL = 1
# WCH DLL 1.20: Init uses the API enum (1=MSB), but GetCfg copies
# SPI_FirstBit & 0x80 directly (0=MSB, 0x80=LSB). Scope this decoding to
# the binary that was inspected; a different DLL must use the documented enum.
RAW_FIRST_BIT_DLL_SHA256 = "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375"


def _dll_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path is not None else ""


def _readback_is_msb(value, dll_sha256):
    return value == (0 if dll_sha256 == RAW_FIRST_BIT_DLL_SHA256 else 1)


def _readback_cs_matches(value, cs):
    # Init can rewrite CS2's API selector 0x81 in place to the cached
    # bitmask 0x8000, which GetCfg returns. Both refer to CS2 alone.
    return value in ((0x80,) if cs == 0 else (0x81, 0x8000))


class SPIConfig(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("iMode", UCHAR),
        ("iClock", UCHAR),
        ("iByteOrder", UCHAR),
        ("iSpiWriteReadInterval", USHORT),
        ("iSpiOutDefaultData", UCHAR),
        ("iChipSelect", ULONG),
        ("CS1Polarity", UCHAR),
        ("CS2Polarity", UCHAR),
        ("iIsAutoDeativeCS", USHORT),
        ("iActiveDelay", USHORT),
        ("iDelayDeactive", ULONG),
    ]


class CH347Error(OSError):
    pass


class DeviceInfo(ctypes.Structure):
    """Packed Windows x64 mDeviceInforS, CH347-DLL V1.4 header."""
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
class CH347Device:
    index: int
    product: str
    chip_mode: int
    function_type: int
    interface_number: int
    device_id: str = ""
    function_description: str = ""
    usb_class: int = -1

    @property
    def supports_spi(self):
        return not self.spi_exclusion_reason

    @property
    def spi_exclusion_reason(self):
        # The distributed DLL 1.20's own descriptor table uses FuncType=3
        # for F's combined SPI/I2C/JTAG interface, Mode=0/1 and IF=4/1.
        # Older headers instead describe Mode=4 and logical IF=2.
        if self.function_type == 0:
            return "UART 串口接口"
        if self.function_type == 3:
            return ""
        if self.function_type == 1:
            # DLL 1.20 also labels T.M3 JTAG/I2C as FuncType=1.
            if self.chip_mode == 3 or "PID_55DD" in self.device_id.upper():
                return "CH347T JTAG/I2C 模式，不支持 SPI"
            return ""
        if self.function_type == 2 and self.chip_mode == 4:
            return ""  # Legacy header's CH347F combined-interface encoding.
        if self.function_type == 2:
            return "JTAG/I2C 接口，未确认支持 SPI"
        return f"未识别的接口类型 {self.function_type}"

    @property
    def diagnostic_summary(self):
        result = "SPI 候选接口" if self.supports_spi else self.spi_exclusion_reason
        return (f"CH347 #{self.index}: FuncType={self.function_type}, ChipMode={self.chip_mode}, "
                f"IF={self.interface_number}, UsbClass={self.usb_class}, "
                f"ID={self.device_id or '未返回'}, 功能={self.function_description or self.product or '未返回'}; "
                f"判定={result}")


USB_OPEN_HELP = (
    "电脑尚未打开 CH347 USB 同步接口；请检查 USB 连接和 CH341PAR/CH347PAR 驱动"
    "（只有 COM 串口不够），并关闭占用模块的调试软件。此阶段尚未与传感器通信"
)


def _valid_handle(handle):
    value = handle.value if isinstance(handle, HANDLE) else handle
    return value not in (None, 0, -1, HANDLE(-1).value)


def _load_dll(dll_path=None):
    if os.name != "nt" or ctypes.sizeof(HANDLE) != 8:
        raise CH347Error("CH347DLLA64.dll 需要 Windows 64 位运行环境")
    path = _dll_location(dll_path)
    cookie = os.add_dll_directory(str(path.parent)) if hasattr(os, "add_dll_directory") else None
    try:
        return ctypes.WinDLL(str(path), use_last_error=True), path, cookie
    except OSError as error:
        if cookie is not None:
            cookie.close()
        raise CH347Error(f"无法加载 CH347DLLA64.dll：{error}") from error


def _bind_functions(dll, signatures):
    for name, (arguments, result) in signatures.items():
        try:
            function = getattr(dll, name)
        except AttributeError as error:
            raise CH347Error(f"官方 DLL 缺少所需函数 {name}，请使用匹配的 CH347 Windows SDK") from error
        function.argtypes = arguments
        function.restype = result


def list_ch347_devices(dll_path=None, *, _dll=None, cancelled=lambda: False):
    """Probe SDK indices without configuring SPI, toggling CS, or SPI payloads.

    Call only on an explicit user request, while acquisition/upgrading is idle.
    Every successfully opened handle is closed, including metadata failures.
    """
    cookie = None
    if _dll is None:
        _dll, _path, cookie = _load_dll(dll_path)
    try:
        _bind_functions(_dll, {
            "CH347OpenDevice": ([ULONG], HANDLE),
            "CH347CloseDevice": ([ULONG], BOOL),
            "CH347GetDeviceInfor": ([ULONG, ctypes.c_void_p], BOOL),
        })
        devices = []
        for index in range(32):  # CH347 DLL's native device table capacity.
            if cancelled():
                break
            if not _valid_handle(_dll.CH347OpenDevice(index)):
                continue
            try:
                # Reserve headroom for SDK revisions with appended fields.
                buffer = ctypes.create_string_buffer(4096)
                if not _dll.CH347GetDeviceInfor(index, buffer):
                    raise CH347Error(f"CH347 #{index} 已打开，但无法读取 USB 接口信息")
                info = DeviceInfo.from_buffer(buffer)
                devices.append(CH347Device(index, bytes(info.ProductString).decode("utf-8", "replace"),
                                           info.ChipMode, info.FuncType, info.CH347IfNum,
                                           bytes(info.DeviceID).decode("ascii", "replace"),
                                           bytes(info.FuncDescStr).decode("utf-8", "replace"), info.UsbClass))
            except BaseException as primary:
                if not _dll.CH347CloseDevice(index):
                    CH347Spi._note(primary, CH347Error(f"CH347CloseDevice({index}) 失败"))
                raise
            else:
                if not _dll.CH347CloseDevice(index):
                    raise CH347Error(f"CH347CloseDevice({index}) 失败；请重新插拔模块后再连接")
        return devices
    finally:
        if cookie is not None:
            cookie.close()


def _dll_location(dll_path) -> Path:
    if dll_path is not None:
        candidates = [Path(dll_path).expanduser()]
    elif os.environ.get("CH347_DLL_PATH"):
        candidates = [Path(os.environ["CH347_DLL_PATH"]).expanduser()]
    else:
        directories = [Path(__file__).resolve().parent]
        if getattr(sys, "frozen", False):
            directories.insert(0, Path(sys.executable).resolve().parent)
            directories.append(Path(getattr(sys, "_MEIPASS", directories[0])))
        candidates = [directory / "CH347DLLA64.dll" for directory in directories]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise CH347Error(
        "未找到官方 Windows x64 CH347DLLA64.dll；请指定 dll_path、设置 CH347_DLL_PATH，"
        "或将官方 DLL 放在上位机 EXE 旁。该工具不会自动安装驱动。"
    )


class CH347Spi:
    """Open/configure a selected CH347 index, mode 0, MSB first, 8-bit words.

    _dll is solely an injection point for tests. With a real DLL this class
    requires a 64-bit Windows Python/executable. cs=0 selects CS1; cs=1 CS2.
    A logical exchange may span USB packets, but CS remains asserted throughout.
    """

    def __init__(self, device_index=0, clock_hz=468750, cs=0, dll_path=None, *, _dll=None):
        if isinstance(device_index, bool) or not isinstance(device_index, int) or not 0 <= device_index <= 0xFFFFFFFF:
            raise ValueError("device_index must be an unsigned 32-bit integer")
        if isinstance(clock_hz, bool) or not isinstance(clock_hz, int) or clock_hz not in CLOCKS_HZ:
            raise ValueError(f"clock_hz must be one of {CLOCKS_HZ}")
        if isinstance(cs, bool) or not isinstance(cs, int) or cs not in (0, 1):
            raise ValueError("cs must be 0 (CS1) or 1 (CS2)")
        self.device_index = device_index
        self.clock_hz = clock_hz
        self.cs = cs
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
            self.dll_sha256 = _dll_sha256(self.dll_path)
            self._bind()
            handle = self._dll.CH347OpenDevice(self.device_index)
            if not _valid_handle(handle):
                raise CH347Error(f"CH347 索引 {self.device_index} 无法打开。{USB_OPEN_HELP}")
            self._opened = True
            self._check(self._dll.CH347SetTimeout(self.device_index, 1000, 1000), "CH347SetTimeout")
            config = SPIConfig(
                iMode=0, iClock=CLOCKS_HZ.index(clock_hz), iByteOrder=1,
                iSpiWriteReadInterval=0, iSpiOutDefaultData=0xFF,
                iChipSelect=0x80 | cs, CS1Polarity=0, CS2Polarity=0,
                iIsAutoDeativeCS=0, iActiveDelay=0, iDelayDeactive=0,
            )
            requested = SPIConfig.from_buffer_copy(config)
            # DLL 1.20 caches this value; Init uses it to program hardware.
            self._check(self._dll.CH347SPI_SetDataBits(self.device_index, 0), "CH347SPI_SetDataBits(8-bit)")
            self._check(self._dll.CH347SPI_Init(self.device_index, ctypes.byref(config)), "CH347SPI_Init")
            self._configured = True
            self._deassert()
            readback = SPIConfig()
            self._check(self._dll.CH347SPI_GetCfg(self.device_index, ctypes.byref(readback)), "CH347SPI_GetCfg")
            self.raw_readback = SPIConfig.from_buffer_copy(readback)
            raw_first_bit = self.dll_sha256 == RAW_FIRST_BIT_DLL_SHA256
            convention = "FirstBit 寄存器（0=MSB，128=LSB）" if raw_first_bit else "API 枚举（1=MSB，0=LSB）"
            details = (
                f"请求 mode={requested.iMode}, clock档位={requested.iClock} ({clock_hz} Hz), MSB, CS={cs}; "
                f"回读 mode={readback.iMode}, clock档位={readback.iClock}, "
                f"byteOrder={readback.iByteOrder} (0x{readback.iByteOrder:02X}), "
                f"CS=0x{readback.iChipSelect:X}; 位序解析={convention}; "
                f"DLL SHA256={self.dll_sha256 or '测试注入'}"
            )
            if ((readback.iMode, readback.iClock) != (requested.iMode, requested.iClock)
                    or not _readback_is_msb(readback.iByteOrder, self.dll_sha256)):
                raise CH347Error("CH347 SPI 回读的 mode/clock/MSB 位序与请求配置不一致。" + details)
            if not _readback_cs_matches(readback.iChipSelect, cs) or readback.iIsAutoDeativeCS:
                raise CH347Error("CH347 SPI 回读的 CS 配置不允许完整独立事务。" + details)
            if readback.CS1Polarity or readback.CS2Polarity:
                raise CH347Error("CH347 SPI 回读的片选极性不是低有效。" + details)
            self.configuration_summary = "CH347 SPI 参数校验通过：" + details
            # Present the public API convention to callers while preserving
            # the exact DLL response above for diagnostics.
            readback.iByteOrder = 1
            readback.iChipSelect = requested.iChipSelect
            self.config = readback
        except Exception:
            try:
                self.close()
            except OSError:
                pass
            raise

    def _bind(self):
        signatures = {
            "CH347OpenDevice": ([ULONG], HANDLE),
            "CH347CloseDevice": ([ULONG], BOOL),
            "CH347SetTimeout": ([ULONG, ULONG, ULONG], BOOL),
            "CH347SPI_Init": ([ULONG, ctypes.POINTER(SPIConfig)], BOOL),
            "CH347SPI_GetCfg": ([ULONG, ctypes.POINTER(SPIConfig)], BOOL),
            "CH347SPI_SetDataBits": ([ULONG, UCHAR], BOOL),
            "CH347SPI_ChangeCS": ([ULONG, UCHAR], BOOL),
            "CH347SPI_WriteRead": ([ULONG, ULONG, ULONG, ctypes.c_void_p], BOOL),
        }
        _bind_functions(self._dll, signatures)

    @staticmethod
    def _check(result, operation):
        if not result:
            raise CH347Error(f"{operation} 失败")

    def _close_directory(self):
        if self._directory_cookie is not None:
            self._directory_cookie.close()
            self._directory_cookie = None

    @staticmethod
    def _note(primary, cleanup):
        if hasattr(primary, "add_note"):
            primary.add_note(f"同时发生清理错误（保留原始异常）：{cleanup}")

    def _deassert(self):
        self._check(self._dll.CH347SPI_ChangeCS(self.device_index, CS_DEASSERT_LEVEL), "CH347SPI_ChangeCS(deassert)")
        self._last_deassert = time.monotonic()

    def _wait_cs_high(self):
        if self._last_deassert is not None:
            # Both response polls and the next command request need the same
            # high interval for the 16 MHz bootloader's copy/re-arm work.
            remaining = CS_HIGH_SECONDS - (time.monotonic() - self._last_deassert)
            while remaining > 0:
                time.sleep(remaining)
                remaining = CS_HIGH_SECONDS - (time.monotonic() - self._last_deassert)

    def exchange(self, data: bytes) -> bytes:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("SPI exchange requires bytes")
        data = bytes(data)
        if not data or len(data) > 0xFFFFFFFF:
            raise ValueError("SPI exchange length must be in 1..0xFFFFFFFF")
        with self._lock:
            if not self._opened:
                raise CH347Error("CH347 SPI 设备已关闭")
            buffer = (UCHAR * len(data)).from_buffer_copy(data)
            self._wait_cs_high()
            try:
                self._check(self._dll.CH347SPI_ChangeCS(self.device_index, CS_ASSERT_LEVEL), "CH347SPI_ChangeCS(assert)")
                # CS bit7=0 tells the SDK not to control CS during its internal
                # USB packets. One full-length SDK call provides all SPI clocks.
                self._check(self._dll.CH347SPI_WriteRead(
                    self.device_index, 0, len(data), ctypes.cast(buffer, ctypes.c_void_p)
                ), "CH347SPI_WriteRead")
            except BaseException as primary:
                try:
                    self._deassert()
                except BaseException as cleanup:
                    self._note(primary, cleanup)
                raise
            else:
                self._deassert()
                return bytes(buffer)

    def close(self):
        with self._lock:
            error = None
            if self._opened:
                try:
                    if self._configured:
                        self._deassert()
                except OSError as caught:
                    if error is None:
                        error = caught
                    else:
                        self._note(error, caught)
                try:
                    self._check(self._dll.CH347CloseDevice(self.device_index), "CH347CloseDevice")
                except OSError as caught:
                    if error is None:
                        error = caught
                    else:
                        self._note(error, caught)
                finally:
                    self._opened = False
                    self._configured = False
            self._close_directory()
            if error is not None:
                raise error

    def __enter__(self):
        if not self._opened:
            raise CH347Error("CH347 SPI 设备已关闭")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.close()
        except OSError as cleanup:
            if exc_value is None:
                raise
            self._note(exc_value, cleanup)
        return False
