"""Fake-only CH341 ABI, complete-CS transactions, and failure regression.

No test loads a hardware DLL, installs a driver, or opens a physical device.
Run: python -m unittest test_ch341_spi -v
"""

import ctypes
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import ch341_spi as driver


class FakeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = "not-bound"

    def __call__(self, *args):
        return self.callback(*args)


class FakeDLL:
    """Models the documented SPI4 in/out buffer and automatic CS window."""
    def __init__(self, devices=None):
        self.devices = devices if devices is not None else {
            0: (0x20, r"\\?\USB#VID_1A86&PID_5512#CH341A")
        }
        self.calls = []
        self.transfers = []
        self.cs_windows = []
        self.fail = {}
        self.cs_high = True
        self.closed = []
        self.name_buffers = {}
        self.response = None
        self.stream_observer = None
        self.invalid_handle = ctypes.c_void_p(-1).value
        for name in (
            "CH341OpenDevice", "CH341CloseDevice", "CH341GetDeviceName",
            "CH341GetVerIC", "CH341GetVersion", "CH341SetTimeout",
            "CH341SetStream", "CH341Set_D5_D0", "CH341StreamSPI4",
        ):
            setattr(self, name, FakeFunction(lambda *args, _name=name: self._call(_name, *args)))

    def _call(self, name, *args):
        self.calls.append((name, args))
        failure = self.fail.get(name)
        if isinstance(failure, list):
            failure = failure.pop(0) if failure else None
        if failure is not None:
            if name == "CH341StreamSPI4":
                self.cs_high = False  # Simulate failure before native CS release.
                self.transfers.append((args[0], args[1], ctypes.string_at(args[3], args[2])))
            if isinstance(failure, BaseException):
                raise failure
            return failure
        if name == "CH341OpenDevice":
            return 0x123400001234 if args[0] in self.devices else self.invalid_handle
        if name == "CH341CloseDevice":
            self.closed.append(args[0])
            return None  # VOID must be accepted as a successful close.
        if name == "CH341GetVerIC":
            return self.devices[args[0]][0]
        if name == "CH341GetDeviceName":
            text = self.devices[args[0]][1]
            if text is None:
                return None
            self.name_buffers[args[0]] = ctypes.create_string_buffer(text.encode("ascii"))
            return ctypes.addressof(self.name_buffers[args[0]])
        if name == "CH341GetVersion":
            return 0x110
        if name == "CH341Set_D5_D0":
            self.cs_high = bool(args[2] & 1)
            return 1
        if name == "CH341StreamSPI4":
            index, cs, length, buffer = args
            payload = ctypes.string_at(buffer, length)
            self.transfers.append((index, cs, payload))
            if self.stream_observer:
                self.stream_observer()
            # Hardware framing represented independently of wrapper CS code:
            # auto CS0 asserts once, then releases once after ALL bytes.
            assert cs == 0x80
            self.cs_high = False
            self.cs_windows.append(("low", length, "high"))
            reply = self.response if self.response is not None else bytes((i * 37 + 0x12) & 255 for i in range(length))
            assert len(reply) == length
            ctypes.memmove(buffer, reply, length)
            self.cs_high = True
            return 1
        return 1


class FakeClock:
    def __init__(self):
        self.now = 10.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds + 1e-9


class CH341Tests(unittest.TestCase):
    def setUp(self):
        self.dll = FakeDLL()
        self.clock = FakeClock()
        self.patches = [patch.object(driver.time, "monotonic", self.clock.monotonic),
                        patch.object(driver.time, "sleep", self.clock.sleep)]
        for replacement in self.patches:
            replacement.start()
            self.addCleanup(replacement.stop)

    def names(self):
        return [name for name, args in self.dll.calls]

    def test_constructor_binds_exact_x64_abi_and_void_close(self):
        with driver.CH341Spi(_dll=self.dll) as spi:
            self.assertIs(driver.Ch341Spi, driver.CH341Spi)
            self.assertIs(self.dll.CH341OpenDevice.restype, ctypes.c_void_p)
            self.assertIsNone(self.dll.CH341CloseDevice.restype)
            self.assertEqual(self.dll.CH341StreamSPI4.argtypes,
                             [driver.ULONG, driver.ULONG, driver.ULONG, ctypes.c_void_p])
            self.assertEqual(ctypes.sizeof(driver.ULONG), 4)
            self.assertEqual(self.dll.CH341Set_D5_D0.argtypes, [driver.ULONG] * 3)
            self.assertEqual(self.dll.CH341GetVersion.argtypes, [])
            self.assertIs(self.dll.CH341GetVersion.restype, driver.ULONG)
            self.assertIsNone(spi.clock_hz)
            self.assertIn("硬件固定", spi.configuration_summary)
            self.assertIn("无硬件回读", spi.configuration_summary)
        self.assertEqual(self.dll.closed, [0])

    def test_msb_single_lane_and_gpio_idle_initialization(self):
        with driver.CH341Spi(_dll=self.dll):
            self.assertIn(("CH341SetStream", (0, 0x81)), self.dll.calls)
            self.assertIn(("CH341SetTimeout", (0, 1000, 1000)), self.dll.calls)
            self.assertIn(("CH341Set_D5_D0", (0, 0x29, 1)), self.dll.calls)
            self.assertTrue(self.dll.cs_high)
            self.assertEqual(self.dll.transfers, [])
            self.assertEqual(driver.STREAM_MODE & 0x84, 0x80)
            self.assertEqual(driver.SPI_OUTPUT_DIRECTION & 0x80, 0)

    def test_asymmetric_bytes_are_not_reversed_twice(self):
        tx = b"\x12\x80\x01\x96\xE1"
        rx = b"\x48\x01\x80\x69\x87"
        self.dll.response = rx
        with driver.CH341Spi(_dll=self.dll) as spi:
            result = spi.exchange(tx)
        self.assertEqual(self.dll.transfers, [(0, 0x80, tx)])
        self.assertEqual(result, rx)
        self.assertEqual(tx, b"\x12\x80\x01\x96\xE1")

    def test_16_byte_request_and_536_byte_response_are_complete_cs_windows(self):
        request = b"TAI3" + bytes(range(12))
        with driver.CH341Spi(_dll=self.dll) as spi:
            self.assertEqual(len(spi.exchange(request)), 16)
            self.assertEqual(len(spi.exchange(b"\xFF" * 536)), 536)
        self.assertEqual(self.dll.transfers, [(0, 0x80, request), (0, 0x80, b"\xFF" * 536)])
        self.assertEqual(self.dll.cs_windows, [("low", 16, "high"), ("low", 536, "high")])
        self.assertTrue(self.dll.cs_high)

    def test_532_byte_data_and_4096_limit_are_not_split(self):
        with driver.CH341Spi(_dll=self.dll) as spi:
            for size in (532, 4096):
                self.assertEqual(len(spi.exchange(b"\x96" * size)), size)
        self.assertEqual([len(tx) for _, _, tx in self.dll.transfers], [532, 4096])

    def test_every_request_and_poll_waits_two_milliseconds_high(self):
        observed = []
        with driver.CH341Spi(_dll=self.dll) as spi:
            self.dll.stream_observer = lambda: observed.append(self.clock.now - spi._last_deassert)
            spi.exchange(bytes(16))
            spi.exchange(b"\xFF" * 536)
            spi.exchange(bytes(16))
        self.assertEqual(len(observed), 3)
        self.assertTrue(all(gap >= 0.002 for gap in observed), observed)

    def test_mutable_input_is_copied(self):
        tx = bytearray(b"\x12\x34")
        with driver.CH341Spi(_dll=self.dll) as spi:
            spi.exchange(tx)
            spi.exchange(memoryview(tx))
        self.assertEqual(tx, bytearray(b"\x12\x34"))

    def test_invalid_lengths_and_types_never_send_payload(self):
        with driver.CH341Spi(_dll=self.dll) as spi:
            for value in (b"", b"\xFF" * 4097):
                with self.assertRaises(ValueError):
                    spi.exchange(value)
            for value in ("TAI3", [1, 2], 16, None):
                with self.assertRaises(TypeError):
                    spi.exchange(value)
        self.assertEqual(self.dll.transfers, [])

    def test_invalid_device_indices_do_not_load_dll(self):
        with patch.object(driver, "_load_dll", side_effect=AssertionError("hardware loader forbidden")):
            for index in (-1, 16, True, 1.5, "0", None):
                with self.assertRaises(ValueError):
                    driver.CH341Spi(index)

    def test_missing_required_export_fails_before_open(self):
        del self.dll.CH341StreamSPI4
        with self.assertRaisesRegex(driver.CH341Error, "CH341StreamSPI4"):
            driver.CH341Spi(_dll=self.dll)
        self.assertEqual(self.dll.calls, [])

    def test_invalid_open_handles_fail_without_close_or_configuration(self):
        for handle in (None, 0, -1, ctypes.c_void_p(-1).value):
            dll = FakeDLL()
            dll.fail["CH341OpenDevice"] = [handle] if handle is not None else [0]
            with self.assertRaisesRegex(driver.CH341Error, "无法打开"):
                driver.CH341Spi(_dll=dll)
            self.assertEqual([name for name, _ in dll.calls], ["CH341OpenDevice"])

    def test_non_spi_interface_is_rejected_before_gpio_or_stream(self):
        for version, path in ((0x10, ""), (0x20, "USB#VID_1A86&PID_5523#UART"),
                              (0x20, "USB#VID_1A86&PID_5584#PRINT")):
            dll = FakeDLL({0: (version, path)})
            with self.assertRaises(driver.CH341Error):
                driver.CH341Spi(_dll=dll)
            names = [name for name, _ in dll.calls]
            self.assertNotIn("CH341Set_D5_D0", names)
            self.assertNotIn("CH341SetStream", names)
            self.assertEqual(dll.closed, [0])

    def test_field_revision_32_pid_5512_initializes_and_transfers(self):
        dll = FakeDLL({0: (0x32, "USB#VID_1A86&PID_5512#field-device")})
        request = b"TAI3" + bytes(range(12))
        with driver.CH341Spi(_dll=dll) as spi:
            self.assertTrue(spi.device.supports_spi)
            self.assertEqual(spi.device.product, "CH341 同步接口")
            self.assertIn("CH341 同步接口 SPI", spi.configuration_summary)
            self.assertIn(("CH341SetStream", (0, 0x81)), dll.calls)
            self.assertEqual(len(spi.exchange(request)), 16)
            self.assertEqual(len(spi.exchange(b"\xFF" * 536)), 536)
        self.assertEqual(dll.transfers, [(0, 0x80, request), (0, 0x80, b"\xFF" * 536)])
        self.assertEqual(dll.cs_windows, [("low", 16, "high"), ("low", 536, "high")])
        self.assertEqual(dll.closed, [0])

    def test_field_revision_32_cannot_bypass_synchronous_pid_check(self):
        for path in ("USB#VID_1A86&PID_5523#UART", "USB#VID_1A86&PID_5584#PRINT", "", None):
            with self.subTest(path=path):
                dll = FakeDLL({0: (0x32, path)})
                with self.assertRaisesRegex(driver.CH341Error, "PID_"):
                    driver.CH341Spi(_dll=dll)
                names = [name for name, _ in dll.calls]
                self.assertNotIn("CH341SetStream", names)
                self.assertNotIn("CH341Set_D5_D0", names)
                self.assertEqual(dll.transfers, [])
                self.assertEqual(dll.closed, [0])

    def test_unknown_revision_with_synchronous_pid_is_still_rejected(self):
        for version in (0x31, 0x33, 0x40, 0xFF):
            with self.subTest(version=version):
                dll = FakeDLL({0: (version, "USB#VID_1A86&PID_5512#unknown")})
                with self.assertRaisesRegex(driver.CH341Error, "未确认支持 SPI 的芯片版本"):
                    driver.CH341Spi(_dll=dll)
                self.assertNotIn("CH341SetStream", [name for name, _ in dll.calls])
                self.assertEqual(dll.transfers, [])
                self.assertEqual(dll.closed, [0])

    def test_initialization_failures_always_close(self):
        for operation in ("CH341GetVerIC", "CH341SetTimeout", "CH341SetStream", "CH341Set_D5_D0"):
            dll = FakeDLL()
            dll.fail[operation] = [0]
            with self.assertRaises(driver.CH341Error):
                driver.CH341Spi(_dll=dll)
            self.assertEqual(dll.closed, [0], operation)
            self.assertEqual(dll.transfers, [])

    def test_failed_flash_like_transfer_is_not_replayed_and_cs_released(self):
        with driver.CH341Spi(_dll=self.dll) as spi:
            tx = b"TAI3" + b"\x03" * 12
            self.dll.fail["CH341StreamSPI4"] = [0]
            with self.assertRaisesRegex(driver.CH341Error, "CH341StreamSPI4"):
                spi.exchange(tx)
            self.assertTrue(self.dll.cs_high)
            self.assertEqual(self.dll.transfers, [(0, 0x80, tx)])
            self.assertEqual(self.names()[-1], "CH341Set_D5_D0")

    def test_transfer_error_retained_when_release_also_fails(self):
        spi = driver.CH341Spi(_dll=self.dll)
        self.dll.fail["CH341StreamSPI4"] = [RuntimeError("original native failure")]
        self.dll.fail["CH341Set_D5_D0"] = [0]
        with self.assertRaisesRegex(RuntimeError, "original native failure") as caught:
            spi.exchange(bytes(16))
        self.assertIn("CH341Set_D5_D0", str(caught.exception.__notes__))
        self.assertEqual(len(self.dll.transfers), 1)
        spi.close()
        self.assertEqual(self.dll.closed, [0])

    def test_successful_transfer_release_failure_is_not_success(self):
        spi = driver.CH341Spi(_dll=self.dll)
        self.dll.fail["CH341Set_D5_D0"] = [0]
        with self.assertRaisesRegex(driver.CH341Error, "CS0 high"):
            spi.exchange(bytes(16))
        self.assertEqual(len(self.dll.transfers), 1)
        spi.close()

    def test_close_releases_cs_and_void_close_even_when_release_fails(self):
        spi = driver.CH341Spi(_dll=self.dll)
        self.dll.fail["CH341Set_D5_D0"] = [0]
        with self.assertRaises(driver.CH341Error):
            spi.close()
        self.assertEqual(self.dll.closed, [0])
        self.assertFalse(spi._opened)
        spi.close()
        self.assertEqual(self.dll.closed, [0])

    def test_closed_instance_cannot_send_or_reenter(self):
        spi = driver.CH341Spi(_dll=self.dll)
        spi.close()
        with self.assertRaises(driver.CH341Error):
            spi.exchange(bytes(16))
        with self.assertRaises(driver.CH341Error):
            spi.__enter__()
        self.assertEqual(self.dll.transfers, [])

    def test_context_preserves_body_error_and_closes(self):
        with self.assertRaisesRegex(ValueError, "body failure") as caught:
            with driver.CH341Spi(_dll=self.dll):
                self.dll.fail["CH341Set_D5_D0"] = [0]
                raise ValueError("body failure")
        self.assertEqual(self.dll.closed, [0])
        self.assertIn("清理错误", str(caught.exception.__notes__))

    def test_enumeration_only_opens_metadata_and_void_close(self):
        self.dll = FakeDLL({0: (0x20, "USB#VID_1A86&PID_5512#a"),
                            3: (0x30, "USB#VID_1A86&PID_5512#b"),
                            8: (0x20, "USB#VID_1A86&PID_5523#c")})
        devices = driver.list_ch341_devices(_dll=self.dll)
        self.assertEqual([device.index for device in devices], [0, 3, 8])
        self.assertEqual([device.supports_spi for device in devices], [True, True, False])
        self.assertEqual(self.dll.closed, [0, 3, 8])
        self.assertEqual(len([1 for name in self.names() if name == "CH341OpenDevice"]), 16)
        self.assertTrue(set(self.names()) <= set(driver.PROBE_SIGNATURES))
        self.assertIsNone(devices[0].interface_number)
        self.assertIn("IC=0x20", devices[0].diagnostic_summary)

    def test_enumeration_metadata_failure_closes_before_raising(self):
        self.dll.fail["CH341GetVerIC"] = [0]
        with self.assertRaisesRegex(driver.CH341Error, "GetVerIC"):
            driver.list_ch341_devices(_dll=self.dll)
        self.assertEqual(self.dll.closed, [0])

    def test_enumeration_includes_field_revision_32_without_configuring_spi(self):
        dll = FakeDLL({2: (0x32, "usb#vid_1a86&pid_5512#field-device")})
        devices = driver.list_ch341_devices(_dll=dll)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].index, 2)
        self.assertTrue(devices[0].supports_spi)
        self.assertEqual(devices[0].product, "CH341 同步接口")
        self.assertIn("IC=0x32", devices[0].diagnostic_summary)
        self.assertEqual(dll.closed, [2])
        self.assertTrue(set(name for name, _ in dll.calls) <= set(driver.PROBE_SIGNATURES))

    def test_enumeration_cancellation_does_not_open(self):
        self.assertEqual(driver.list_ch341_devices(_dll=self.dll, cancelled=lambda: True), [])
        self.assertEqual(self.dll.calls, [])

    def test_null_device_name_uses_verified_ic_version(self):
        self.dll = FakeDLL({0: (0x20, None)})
        with driver.CH341Spi(_dll=self.dll) as spi:
            self.assertTrue(spi.device.supports_spi)
            self.assertIn("SDK 未返回名称", spi.device.diagnostic_summary)

    def test_fake_injection_never_loads_real_dll(self):
        with patch.object(driver, "_load_dll", side_effect=AssertionError("hardware loader forbidden")):
            with driver.CH341Spi(_dll=self.dll):
                pass
            self.assertTrue(driver.list_ch341_devices(_dll=self.dll))

    def test_explicit_and_environment_dll_path_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            dll_path = Path(folder) / "CH341DLLA64.DLL"
            dll_path.write_bytes(b"test only; never loaded")
            with patch.dict(driver.os.environ, {"CH341_DLL_PATH": str(dll_path)}):
                self.assertEqual(driver._dll_location(), dll_path.resolve())
                self.assertEqual(driver._dll_location(dll_path), dll_path.resolve())
                with self.assertRaises(driver.CH341Error):
                    driver._dll_location(Path(folder) / "missing.dll")

    def test_x86_or_malformed_pe_rejected_without_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.dll"
            for payload in (b"", b"MZ" + bytes(62), b"bad"):
                path.write_bytes(payload)
                with self.assertRaises(driver.CH341Error):
                    driver._verify_x64_pe(path)
            pe = bytearray(256)
            pe[:2] = b"MZ"
            struct.pack_into("<I", pe, 0x3C, 64)
            pe[64:68] = b"PE\0\0"
            struct.pack_into("<H", pe, 68, 0x14C)
            struct.pack_into("<H", pe, 88, 0x10B)
            path.write_bytes(pe)
            with self.assertRaisesRegex(driver.CH341Error, "AMD64"):
                driver._verify_x64_pe(path)
            struct.pack_into("<H", pe, 68, 0x8664)
            struct.pack_into("<H", pe, 88, 0x20B)
            path.write_bytes(pe)
            driver._verify_x64_pe(path)


if __name__ == "__main__":
    unittest.main()
