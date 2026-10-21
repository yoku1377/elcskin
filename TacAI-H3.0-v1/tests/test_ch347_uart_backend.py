"""Fake-DLL tests: no USB device, COM port, UART writes or SPI calls."""
import ctypes
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ch347_uart_backend import (CH347Uart, CH347UartBackend, CH347UartError, DeviceInfo,
                                 ULONG, HANDLE, list_uart_devices)


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


class FakeDll:
    def __init__(self, indices=(1, 7), fail=None, payload=b"BOOT\r\n\x00\xff", interface_types=None):
        self.indices = indices
        self.fail = fail
        self.payload = payload
        self.interface_types = interface_types or {}
        self.events = []
        for name in ("Open", "Close", "GetDeviceInfor", "Init", "Read", "SetTimeout"):
            setattr(self, "CH347Uart_" + name, Function(lambda *a, name=name: self.call(name, *a)))

    def call(self, name, *args):
        self.events.append((name, args))
        if name == self.fail:
            return 0
        if name == "Open":
            return 0x1234 if args[0] in self.indices else HANDLE(-1).value
        if name == "GetDeviceInfor":
            info = DeviceInfo()
            info.iIndex = args[0]
            info.FuncType = self.interface_types.get(args[0], 0)
            info.UsbClass = 3
            info.CH347IfNum = 2
            info.DeviceID = b"USB\\VID_1A86&PID_55DE&MI_02"
            info.FuncDescStr = b"CH347F.M0:USB2.0 To VCP UART1"
            ctypes.memmove(args[1], ctypes.byref(info), ctypes.sizeof(info))
        if name == "Read":
            length = ctypes.cast(args[2], ctypes.POINTER(ULONG))
            data = self.payload[:length.contents.value]
            ctypes.memmove(args[1], data, len(data))
            length.contents.value = len(data)
        return 1


class UartBackendTests(unittest.TestCase):
    def test_windows_x64_packed_abi(self):
        self.assertEqual(ctypes.sizeof(ULONG), 4)
        self.assertEqual(ctypes.sizeof(DeviceInfo), 545)
        self.assertEqual(DeviceInfo.DevHandle.offset, 328)
        self.assertEqual(DeviceInfo.CH347IfNum.offset, 341)
        self.assertEqual(DeviceInfo.FuncDescStr.offset, 480)

    def test_sparse_uart_indices_preserved_and_every_opened_device_closed(self):
        dll = FakeDll()
        devices = list_uart_devices(_dll=dll)
        self.assertEqual([d.index for d in devices], [1, 7])
        self.assertEqual([d.uart_channel for d in devices], [1, 1])
        self.assertEqual([a[0] for n, a in dll.events if n == "Close"], [1, 7])
        self.assertEqual([a[0] for n, a in dll.events if n == "Open"], list(range(32)))
        self.assertFalse(any(n in ("Init", "Read", "SetTimeout") for n, _ in dll.events))

    def test_metadata_failure_still_closes_handle(self):
        dll = FakeDll(fail="GetDeviceInfor")
        with self.assertRaisesRegex(CH347UartError, "GetDeviceInfor"):
            list_uart_devices(_dll=dll)
        self.assertEqual(dll.events[-1], ("Close", (1,)))

    def test_non_uart_slot_is_closed_and_does_not_hide_later_uart1(self):
        dll = FakeDll(indices=(0, 1), interface_types={0: 3})
        devices = list_uart_devices(_dll=dll)
        self.assertEqual([d.index for d in devices], [1])
        self.assertEqual([a[0] for n, a in dll.events if n == "Close"], [0, 1])
        self.assertFalse(any(n in ("Init", "Read", "SetTimeout") for n, _ in dll.events))

    def test_open_rejects_non_uart_before_configuration(self):
        dll = FakeDll(indices=(0,), interface_types={0: 3})
        with self.assertRaisesRegex(CH347UartError, "不是 UART"):
            CH347Uart(0, _dll=dll)
        self.assertEqual(dll.events[-1], ("Close", (0,)))
        self.assertFalse(any(n in ("Init", "Read", "SetTimeout") for n, _ in dll.events))

    def test_cancel_before_scan_has_no_device_calls(self):
        dll = FakeDll()
        self.assertEqual(list_uart_devices(_dll=dll, cancelled=lambda: True), [])
        self.assertEqual(dll.events, [])

    def test_8n1_reconfigure_and_unmodified_binary_receive(self):
        dll = FakeDll()
        with CH347Uart(7, _dll=dll, read_timeout_ms=50) as uart:
            self.assertEqual(uart.device.uart_channel, 1)
            self.assertEqual(uart.read(), b"BOOT\r\n\x00\xff")
            uart.configure(9600)
            self.assertEqual(uart.baudrate, 9600)
            self.assertIn(("SetTimeout", (7, 50, 50)), dll.events)
            self.assertIn(("Init", (7, 115200, 8, 0, 0, 0)), dll.events)
            self.assertIn(("Init", (7, 9600, 8, 0, 0, 0)), dll.events)
            self.assertEqual(dll.CH347Uart_Read.argtypes,
                             [ULONG, ctypes.c_void_p, ctypes.POINTER(ULONG)])
            self.assertFalse(hasattr(dll, "CH347Uart_Write"))
        self.assertEqual(dll.events[-1], ("Close", (7,)))

    def test_empty_read_is_normal_timeout(self):
        with CH347Uart(1, _dll=FakeDll(payload=b"")) as uart:
            self.assertEqual(uart.read(), b"")

    def test_configuration_failure_closes_device(self):
        dll = FakeDll(fail="Init")
        with self.assertRaisesRegex(CH347UartError, "Init"):
            CH347Uart(1, _dll=dll)
        self.assertEqual(dll.events[-1], ("Close", (1,)))

    def test_read_failure_is_reported_and_context_closes_device(self):
        dll = FakeDll(fail="Read")
        with self.assertRaisesRegex(CH347UartError, "Read"):
            with CH347Uart(1, _dll=dll) as uart:
                uart.read()
        self.assertEqual(dll.events[-1], ("Close", (1,)))

    def test_closed_device_rejects_read_and_reconfigure_without_dll_call(self):
        dll = FakeDll()
        uart = CH347Uart(1, _dll=dll)
        uart.close()
        count = len(dll.events)
        for call in (uart.read, lambda: uart.configure(9600)):
            with self.assertRaises(CH347UartError):
                call()
        uart.close()
        self.assertEqual(len(dll.events), count)

    def test_invalid_arguments_never_open_device(self):
        for args in ((-1, 115200), (True, 115200), (1, 0), (1, 1.5)):
            dll = FakeDll()
            with self.subTest(args=args), self.assertRaises(ValueError):
                CH347Uart(*args, _dll=dll)
            self.assertEqual(dll.events, [])

    def test_scanner_adapter_only_opens_selected_raw_index(self):
        dll = FakeDll()
        backend = CH347UartBackend(_dll=dll)
        self.assertEqual(dll.events, [])
        devices = backend.enumerate_devices()
        self.assertEqual([d["index"] for d in devices], [1, 7])
        self.assertIn("UART1", devices[-1]["description"])
        before = len(dll.events)
        backend.open(devices[-1]["index"])
        backend.configure(230400)
        self.assertEqual(backend.read(4), b"BOOT")
        backend.close()
        backend.close()
        self.assertEqual([a[0] for n, a in dll.events[before:] if n == "Open"], [7])
        self.assertEqual(dll.events[-1], ("Close", (7,)))


if __name__ == "__main__":
    unittest.main()
