"""Verify the real constant-writer boundary with an injected DLL, never hardware."""
import ctypes
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import spi_gpio_witness as witness


class FakeSPI:
    device_index = 7
    _opened = True

    def __init__(self):
        self.calls = []
        self.succeed = True
        self._dll = self

    def CH347SPI_WriteRead(self, index, cs_control, length, buffer):
        self.calls.append((index, cs_control, length, ctypes.string_at(buffer, length)))
        # The SDK overwrites its in/out buffer. The next transfer must rebuild TX.
        ctypes.memset(buffer, 0xA5, length)
        return int(self.succeed)

    @staticmethod
    def _check(result, operation):
        if not result:
            raise RuntimeError(operation + " failed")

    def exchange(self, data):
        raise AssertionError("CS-asserting exchange() must not be used")


class ConstantWriterBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.spi = FakeSPI()
        self.writer = witness.CH347ConstantWriter()
        self.writer.spi = self.spi

    def test_only_no_cs_sdk_calls_and_fresh_exact_payloads(self):
        for length in (536, 4096):
            for value in (0, 255):
                self.writer.transmit(value, length)
                self.assertEqual(self.spi.calls[-1], (7, 0, length, bytes([value]) * length))
        self.assertEqual(len(self.spi.calls), 4)

    def test_forbidden_payloads_never_reach_sdk(self):
        for value, length in ((0x54, 4096), (0, 16), (255, 0), (255, 537), (255, 4097)):
            with self.assertRaises(RuntimeError):
                self.writer.transmit(value, length)
        self.assertEqual(self.spi.calls, [])

    def test_sdk_error_is_not_success(self):
        self.spi.succeed = False
        with self.assertRaises(RuntimeError):
            self.writer.transmit(0, 4096)

    def test_closed_device_never_reaches_sdk(self):
        self.spi._opened = False
        with self.assertRaises(RuntimeError):
            self.writer.transmit(255, 4096)
        self.assertEqual(self.spi.calls, [])


if __name__ == "__main__":
    unittest.main()
