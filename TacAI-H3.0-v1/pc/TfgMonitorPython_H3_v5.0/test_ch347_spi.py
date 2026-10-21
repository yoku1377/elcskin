"""Injected Windows-DLL ABI tests; never access a real CH347 device."""

import ctypes
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ch347_spi import (BOOL, ULONG, CH347Device, CH347Error, CH347Spi, SPIConfig, DeviceInfo,
                       list_ch347_devices, RAW_FIRST_BIT_DLL_SHA256)


class Function:
    def __init__(self, implementation):
        self.implementation = implementation
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.implementation(*args)


class InjectedDll:
    def __init__(self, fail=None, bad_field=None, invalid_handle=None):
        self.fail = fail
        self.bad_field = bad_field
        self.invalid_handle = invalid_handle
        self.events = []
        self.config = SPIConfig()
        self.transferred = []
        self.cs_levels = [1, 1]
        self.transfer_cs_levels = []
        self.fail_cs_status = None
        names = ("CH347OpenDevice", "CH347CloseDevice", "CH347SetTimeout",
                 "CH347SPI_Init", "CH347SPI_GetCfg", "CH347SPI_SetDataBits",
                 "CH347SPI_ChangeCS", "CH347SPI_WriteRead")
        for name in names:
            setattr(self, name, Function(lambda *args, name=name: self.call(name, *args)))

    def call(self, name, *args):
        self.events.append((name, args))
        if name == self.fail:
            return 0
        if name == "CH347SPI_ChangeCS" and args[1] == self.fail_cs_status:
            return 0
        if name == "CH347OpenDevice":
            return self.invalid_handle if self.invalid_handle is not None else 0x1234
        if name == "CH347SPI_Init":
            self.config = SPIConfig.from_buffer_copy(ctypes.string_at(args[1], ctypes.sizeof(SPIConfig)))
        if name == "CH347SPI_ChangeCS":
            # Actual WCH DLL C1 output semantics, independently of the wrapper:
            # bit 6 clear drives low, bit 6 set drives high on the chosen line.
            selected = 1 if self.config.iChipSelect in (0x81, 0x8000) else 0
            self.cs_levels[selected] = args[1] & 1
        if name == "CH347SPI_GetCfg":
            if self.bad_field:
                setattr(self.config, *self.bad_field)
            ctypes.memmove(args[1], ctypes.byref(self.config), ctypes.sizeof(SPIConfig))
        if name == "CH347SPI_WriteRead":
            index, chip_select, count, buffer = args
            self.transfer_cs_levels.append(tuple(self.cs_levels))
            self.transferred.append((index, chip_select, ctypes.string_at(buffer, count)))
            for offset in range(count):
                ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint8))[offset] ^= 0xFF
        return 1


class AbiTests(unittest.TestCase):
    def test_windows_packed_struct_size_and_offsets(self):
        self.assertEqual(ctypes.sizeof(SPIConfig), 20)
        expected = dict(zip(
            (name for name, _type in SPIConfig._fields_),
            (0, 1, 2, 3, 5, 6, 10, 11, 12, 14, 16),
        ))
        for field, offset in expected.items():
            self.assertEqual(getattr(SPIConfig, field).offset, offset, field)
        self.assertEqual((ctypes.sizeof(ULONG), ctypes.sizeof(BOOL)), (4, 4))

    def test_windows_argument_and_return_types(self):
        dll = InjectedDll()
        with CH347Spi(_dll=dll):
            self.assertEqual(dll.CH347OpenDevice.restype, ctypes.c_void_p)
            self.assertEqual(dll.CH347SPI_WriteRead.restype, ctypes.c_int32)
            self.assertEqual(dll.CH347SPI_WriteRead.argtypes,
                             [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p])
            self.assertEqual(dll.CH347SPI_Init.argtypes, [ctypes.c_uint32, ctypes.POINTER(SPIConfig)])

    def test_mode_msb_clock_8bit_and_manual_cs_configuration(self):
        dll = InjectedDll()
        with CH347Spi(_dll=dll) as spi:
            cfg = spi.config
            self.assertEqual((cfg.iMode, cfg.iClock, cfg.iByteOrder), (0, 7, 1))
            self.assertEqual((cfg.iChipSelect, cfg.iIsAutoDeativeCS, cfg.CS1Polarity), (0x80, 0, 0))
            self.assertEqual(cfg.iSpiOutDefaultData, 0xFF)
            self.assertIn(("CH347SPI_SetDataBits", (0, 0)), dll.events)
            self.assertEqual(dll.cs_levels, [1, 1])
            self.assertEqual([args for name, args in dll.events if name == "CH347SPI_ChangeCS"], [(0, 1)])


class ExchangeTests(unittest.TestCase):
    def test_536_bytes_one_sdk_call_with_explicit_cs(self):
        dll = InjectedDll()
        with CH347Spi(device_index=2, _dll=dll) as spi:
            before = len(dll.events)
            data = bytes(range(256)) * 2 + b"a" * 24
            self.assertEqual(len(data), 536)
            self.assertEqual(spi.exchange(data), bytes(value ^ 0xFF for value in data))
            events = dll.events[before:]
            self.assertEqual([name for name, args in events], [
                "CH347SPI_ChangeCS", "CH347SPI_WriteRead", "CH347SPI_ChangeCS"
            ])
            self.assertEqual(events[0][1], (2, 0))
            self.assertEqual(events[1][1][:3], (2, 0, 536))
            self.assertEqual(events[2][1], (2, 1))
            self.assertEqual(dll.transferred, [(2, 0, data)])
            self.assertEqual(dll.transfer_cs_levels, [(0, 1)])
            self.assertEqual(dll.cs_levels, [1, 1])

    def test_cs2_configuration(self):
        dll = InjectedDll()
        with CH347Spi(cs=1, clock_hz=937500, _dll=dll) as spi:
            self.assertEqual((spi.config.iChipSelect, spi.config.iClock), (0x81, 6))
            self.assertEqual(dll.cs_levels, [1, 1])
            spi.exchange(b"request")
            self.assertEqual(dll.transfer_cs_levels, [(1, 0)])
            self.assertEqual(dll.cs_levels, [1, 1])

    def test_two_exchanges_have_separate_cs_edges(self):
        dll = InjectedDll()
        with CH347Spi(_dll=dll) as spi:
            before = len(dll.events)
            spi.exchange(b"request")
            spi.exchange(b"\xFF" * 536)
            cs = [args[1] for name, args in dll.events[before:] if name == "CH347SPI_ChangeCS"]
            self.assertEqual(cs, [0, 1, 0, 1])
            self.assertEqual(len(dll.transferred), 2)

    def test_transfer_failure_always_releases_cs(self):
        dll = InjectedDll(fail="CH347SPI_WriteRead")
        with CH347Spi(_dll=dll) as spi:
            with self.assertRaisesRegex(CH347Error, "WriteRead"):
                spi.exchange(b"test")
            self.assertEqual(dll.events[-1], ("CH347SPI_ChangeCS", (0, 1)))
            self.assertEqual(dll.cs_levels, [1, 1])

    def test_cs_high_interval_covers_every_next_request(self):
        clock = [100.0]
        delays = []

        def sleep(delay):
            delays.append(delay)
            clock[0] += delay + 0.0000001

        dll = InjectedDll()
        with patch("ch347_spi.time.monotonic", side_effect=lambda: clock[0]), \
                patch("ch347_spi.time.sleep", side_effect=sleep):
            with CH347Spi(_dll=dll) as spi:
                spi.exchange(b"first request")
                spi.exchange(b"next request")
        self.assertEqual(len(delays), 2)
        for delay in delays:
            self.assertAlmostEqual(delay, 0.002)

    def test_assert_failure_releases_cs_without_transfer(self):
        dll = InjectedDll()
        with CH347Spi(_dll=dll) as spi:
            dll.fail_cs_status = 0
            with self.assertRaisesRegex(CH347Error, "assert"):
                spi.exchange(b"test")
            self.assertEqual(dll.transferred, [])
            self.assertEqual(dll.events[-1], ("CH347SPI_ChangeCS", (0, 1)))

    def test_deassert_failure_does_not_report_success(self):
        dll = InjectedDll()
        spi = CH347Spi(_dll=dll)
        dll.fail_cs_status = 1
        with self.assertRaisesRegex(CH347Error, "deassert"):
            spi.exchange(b"test")
        dll.fail_cs_status = None
        spi.close()

    def test_transfer_failure_not_masked_by_release_and_close_failures(self):
        dll = InjectedDll()
        with self.assertRaisesRegex(CH347Error, "WriteRead") as caught:
            with CH347Spi(_dll=dll) as spi:
                dll.fail = "CH347SPI_WriteRead"
                dll.fail_cs_status = 1
                spi.exchange(b"test")
        self.assertTrue(any("deassert" in note for note in caught.exception.__notes__))
        self.assertEqual(dll.events[-1], ("CH347CloseDevice", (0,)))

    def test_empty_data_has_no_cs_edges(self):
        dll = InjectedDll()
        with CH347Spi(_dll=dll) as spi:
            count = len(dll.events)
            with self.assertRaises(ValueError):
                spi.exchange(b"")
            self.assertEqual(len(dll.events), count)

    def test_closed_device_is_not_called(self):
        dll = InjectedDll()
        spi = CH347Spi(_dll=dll)
        spi.close()
        count = len(dll.events)
        with self.assertRaisesRegex(CH347Error, "关闭"):
            spi.exchange(b"x")
        self.assertEqual(len(dll.events), count)


class FailureTests(unittest.TestCase):
    def test_config_readback_mismatch_closes_device(self):
        for field, value in (("iMode", 1), ("iClock", 6), ("iByteOrder", 0),
                             ("iChipSelect", 0), ("iIsAutoDeativeCS", 1), ("CS1Polarity", 1)):
            dll = InjectedDll(bad_field=(field, value))
            with self.subTest(field=field), self.assertRaises(CH347Error):
                CH347Spi(_dll=dll)
            self.assertEqual(dll.events[-1], ("CH347CloseDevice", (0,)))

    def test_init_failure_closes_device(self):
        dll = InjectedDll(fail="CH347SPI_Init")
        with self.assertRaisesRegex(CH347Error, "Init"):
            CH347Spi(_dll=dll)
        self.assertEqual(dll.events[-1], ("CH347CloseDevice", (0,)))

    def test_set_data_bits_failure_closes_device(self):
        dll = InjectedDll(fail="CH347SPI_SetDataBits")
        with self.assertRaisesRegex(CH347Error, "SetDataBits"):
            CH347Spi(_dll=dll)
        self.assertEqual(dll.events[-1], ("CH347CloseDevice", (0,)))

    def test_invalid_handle_not_closed_as_opened_device(self):
        for handle in (0, -1, ctypes.c_void_p(-1).value):
            dll = InjectedDll(invalid_handle=handle)
            with self.subTest(handle=handle), self.assertRaises(CH347Error):
                CH347Spi(_dll=dll)
            self.assertEqual([name for name, args in dll.events], ["CH347OpenDevice"])

    def test_missing_dll_export_is_clear_error(self):
        dll = InjectedDll()
        del dll.CH347SPI_WriteRead
        with self.assertRaisesRegex(CH347Error, "缺少.*WriteRead"):
            CH347Spi(_dll=dll)
        self.assertEqual(dll.events, [])

    def test_invalid_arguments_prevent_device_open(self):
        for keywords in ({"device_index": -1}, {"device_index": True}, {"clock_hz": 1234},
                         {"clock_hz": 468750.0}, {"cs": 2}, {"cs": 0.0}, {"cs": True}):
            dll = InjectedDll()
            with self.subTest(keywords=keywords), self.assertRaises(ValueError):
                CH347Spi(_dll=dll, **keywords)
            self.assertEqual(dll.events, [])

    def test_close_is_idempotent(self):
        dll = InjectedDll()
        spi = CH347Spi(_dll=dll)
        spi.close()
        count = len(dll.events)
        spi.close()
        self.assertEqual(len(dll.events), count)

    def test_close_release_failure_preserves_body_exception(self):
        dll = InjectedDll()
        with self.assertRaisesRegex(RuntimeError, "original protocol failure") as caught:
            with CH347Spi(_dll=dll):
                dll.fail_cs_status = 1
                raise RuntimeError("original protocol failure")
        self.assertTrue(any("deassert" in note for note in caught.exception.__notes__))
        self.assertEqual(dll.events[-1], ("CH347CloseDevice", (0,)))

    def test_close_attempted_even_after_cs_failure(self):
        dll = InjectedDll()
        spi = CH347Spi(_dll=dll)
        dll.fail_cs_status = 1
        dll.fail = "CH347CloseDevice"
        with self.assertRaisesRegex(CH347Error, "deassert") as caught:
            spi.close()
        self.assertTrue(any("CloseDevice" in note for note in caught.exception.__notes__))
        self.assertFalse(spi._opened)


class ReadbackCompatibilityTests(unittest.TestCase):
    def test_dll_120_msb_register_zero_is_decoded_without_changing_init_enum(self):
        dll = InjectedDll(bad_field=("iByteOrder", 0))
        with patch("ch347_spi._dll_sha256", return_value=RAW_FIRST_BIT_DLL_SHA256):
            with CH347Spi(_dll=dll) as spi:
                self.assertEqual(spi.raw_readback.iByteOrder, 0)
                self.assertEqual(spi.config.iByteOrder, 1)
                self.assertIn("byteOrder=0 (0x00)", spi.configuration_summary)
                self.assertEqual(dll.transferred, [])
                names = [name for name, _ in dll.events]
                self.assertLess(names.index("CH347SPI_SetDataBits"), names.index("CH347SPI_Init"))
                init_ptr = next(args[1] for name, args in dll.events if name == "CH347SPI_Init")
                self.assertEqual(ctypes.cast(init_ptr, ctypes.POINTER(SPIConfig)).contents.iByteOrder, 1)

    def test_dll_120_real_lsb_and_invalid_values_still_fail_before_transfer(self):
        for value in (0x80, 1, 2, 255):
            dll = InjectedDll(bad_field=("iByteOrder", value))
            with self.subTest(value=value), patch("ch347_spi._dll_sha256", return_value=RAW_FIRST_BIT_DLL_SHA256):
                with self.assertRaisesRegex(CH347Error, f"byteOrder={value}") as caught:
                    CH347Spi(_dll=dll)
            self.assertIn("请求 mode=0", str(caught.exception))
            self.assertIn("clock档位=7", str(caught.exception))
            self.assertEqual(dll.transferred, [])
            self.assertEqual(dll.events[-1], ("CH347CloseDevice", (0,)))

    def test_unknown_dll_keeps_documented_enum_and_does_not_accept_zero_as_msb(self):
        dll = InjectedDll(bad_field=("iByteOrder", 0))
        with patch("ch347_spi._dll_sha256", return_value="different_binary"):
            with self.assertRaisesRegex(CH347Error, "API 枚举"):
                CH347Spi(_dll=dll)
        self.assertEqual(dll.transferred, [])

    def test_known_dll_still_rejects_real_mode_and_clock_mismatches(self):
        for field, value in (("iMode", 1), ("iClock", 6)):
            dll = InjectedDll(bad_field=(field, value))
            original = dll.CH347SPI_GetCfg.implementation

            def read_config(*args):
                dll.config.iByteOrder = 0
                return original(*args)

            dll.CH347SPI_GetCfg.implementation = read_config
            with self.subTest(field=field), patch("ch347_spi._dll_sha256", return_value=RAW_FIRST_BIT_DLL_SHA256):
                with self.assertRaisesRegex(CH347Error, "请求.*回读"):
                    CH347Spi(_dll=dll)
            self.assertEqual(dll.transferred, [])

    def test_sdk_cs2_in_place_normalization_and_readback_mask(self):
        dll = InjectedDll(bad_field=("iChipSelect", 0x8000))
        original = dll.CH347SPI_Init.implementation

        def init(index, config):
            ctypes.cast(config, ctypes.POINTER(SPIConfig)).contents.iChipSelect = 0x8000
            return original(index, config)

        dll.CH347SPI_Init.implementation = init
        with CH347Spi(cs=1, _dll=dll) as spi:
            self.assertEqual(spi.raw_readback.iChipSelect, 0x8000)
            self.assertEqual(spi.config.iChipSelect, 0x81)

    def test_cs_readback_with_extra_or_both_selects_remains_invalid(self):
        for value in (0, 0x180, 0x8080):
            dll = InjectedDll(bad_field=("iChipSelect", value))
            with self.subTest(value=value), self.assertRaisesRegex(CH347Error, "CS 配置"):
                CH347Spi(_dll=dll)
            self.assertEqual(dll.transferred, [])


class EnumerationTests(unittest.TestCase):
    def test_all_14_descriptor_records_from_actual_dll_classify_spi_and_uart(self):
        fixture = json.loads((Path(__file__).parent / "testdata/ch347_dll120_interfaces.json").read_text())
        self.assertEqual(fixture["source_sha256"], RAW_FIRST_BIT_DLL_SHA256)
        spi_ids = {"PID_55DB&MI_02", "PID_55DC&MI_01", "PID_55DE&MI_04", "PID_55E5&MI_01"}
        for index, record in enumerate(fixture["records"]):
            device = CH347Device(index, "CH347", record["chip_mode"], record["function_type"],
                                 record["interface_number"], "USB\\VID_1A86&" + record["device_id_match"],
                                 record["function_description"], record["usb_class"])
            with self.subTest(device=record["device_id_match"]):
                self.assertEqual(device.supports_spi, record["device_id_match"] in spi_ids)

    def test_real_dll_f_composite_metadata_bytes_survive_enumeration(self):
        dll = InjectedDll()
        dll.CH347OpenDevice = Function(lambda index: 0x1234 if index == 5 else -1)
        raw = bytearray(545)
        raw[0] = 5
        raw[261], raw[262], raw[327], raw[341] = 0, 3, 0, 4
        device_id = b"USB\\VID_1A86&PID_55DE&MI_04"
        description = b"CH347F.M0:USB2.0 To SPI&I2C&JTAG&SWD"
        raw[263:263+len(device_id)] = device_id
        raw[480:480+len(description)] = description

        def get_info(index, buffer):
            ctypes.memmove(buffer, bytes(raw), len(raw))
            return 1

        dll.CH347GetDeviceInfor = Function(get_info)
        devices = list_ch347_devices(_dll=dll)
        self.assertEqual([d.index for d in devices if d.supports_spi], [5])
        self.assertIn("FuncType=3", devices[0].diagnostic_summary)
        self.assertIn("PID_55DE&MI_04", devices[0].diagnostic_summary)
        self.assertEqual(devices[0].function_description, description.decode())
        self.assertEqual(dll.events, [("CH347CloseDevice", (5,))])

    def make_dll(self, fail_info=False):
        dll = InjectedDll()

        def open_device(index):
            dll.events.append(("CH347OpenDevice", (index,)))
            return 0x1234 if index in (2, 17, 19) else -1

        def get_info(index, buffer):
            dll.events.append(("CH347GetDeviceInfor", (index,)))
            if fail_info:
                return 0
            info = DeviceInfo.from_buffer(buffer)
            info.iIndex = index
            info.ProductString = b"CH347"
            info.ChipMode = 4 if index in (2, 19) else 3
            info.FuncType = 0 if index == 19 else 2
            info.CH347IfNum = 0 if index == 19 else 2
            return 1

        dll.CH347OpenDevice = Function(open_device)
        dll.CH347GetDeviceInfor = Function(get_info)
        return dll

    def test_scan_sparse_indices_releases_handles_without_spi_operations(self):
        dll = self.make_dll()
        devices = list_ch347_devices(_dll=dll)
        self.assertEqual([d.index for d in devices], [2, 17, 19])
        self.assertEqual([d.index for d in devices if d.supports_spi], [2])
        self.assertEqual([args[0] for name, args in dll.events if name == "CH347CloseDevice"], [2, 17, 19])
        self.assertTrue(all(name in {"CH347OpenDevice", "CH347CloseDevice", "CH347GetDeviceInfor"}
                            for name, _ in dll.events))

    def test_metadata_failure_still_closes_opened_handle(self):
        dll = self.make_dll(fail_info=True)
        with self.assertRaisesRegex(CH347Error, "无法读取 USB"):
            list_ch347_devices(_dll=dll)
        self.assertEqual(dll.events[-1], ("CH347CloseDevice", (2,)))

    def test_cancel_before_scan_makes_no_device_calls(self):
        dll = self.make_dll()
        self.assertEqual(list_ch347_devices(_dll=dll, cancelled=lambda: True), [])
        self.assertEqual(dll.events, [])

    def test_device_info_header_is_packed_with_windows_x64_handle(self):
        self.assertEqual(DeviceInfo.DevHandle.offset, 328)
        self.assertEqual(DeviceInfo.ProductString.offset, 344)
        self.assertEqual(ctypes.sizeof(DeviceInfo), 545)


if __name__ == "__main__":
    unittest.main()
