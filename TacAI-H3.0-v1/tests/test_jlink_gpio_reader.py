"""Read-only J-Link backend tests; no real SEGGER DLL or target is loaded."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "tools/jlink_gpio_reader.py"
spec = importlib.util.spec_from_file_location("jlink_gpio_reader_under_test", SOURCE)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class JLinkReaderTests(unittest.TestCase):
    def test_fake_dll_safety_and_failure_matrix(self):
        with patch.object(reader.ctypes, "CDLL", side_effect=AssertionError("real DLL load forbidden")):
            self.assertGreaterEqual(reader.self_test(), 25)

    def test_pe_check_requires_amd64_without_loading(self):
        with tempfile.TemporaryDirectory(prefix="h3_jlink_pe_test_") as temporary:
            path = Path(temporary) / "JLink_x64.dll"
            data = bytearray(128)
            data[:2] = b"MZ"
            struct.pack_into("<I", data, 0x3C, 64)
            data[64:70] = b"PE\0\0\x64\x86"
            path.write_bytes(data)
            self.assertEqual(reader.discover_dll(path), path.resolve())
            data[68:70] = b"\x4c\x01"
            path.write_bytes(data)
            with self.assertRaises(reader.JLinkReadError):
                reader.discover_dll(path)

    def test_whitelist_excludes_consuming_and_write_only_registers(self):
        for address in (0x4000380C, 0x50000018, 0x50000028, 0x40021020, 0xE000EDF0):
            self.assertNotIn(address, reader.ALLOWED_ADDRESSES)
        for address in (reader.GPIOA_IDR, reader.RCC_IOPENR, reader.CPUID, reader.DBGMCU_IDCODE):
            self.assertIn(address, reader.ALLOWED_ADDRESSES)

    def test_usb_enumeration_abi_matches_vendor_structure(self):
        self.assertEqual(reader.ctypes.sizeof(reader.JLinkConnectInfo), 264)
        self.assertEqual(reader.JLinkConnectInfo.SerialNumber.offset, 0)
        self.assertEqual(reader.JLinkConnectInfo.Connection.offset, 4)
        self.assertEqual(reader.JLinkConnectInfo.USBAddr.offset, 8)
        self.assertEqual(reader.JLinkConnectInfo.Time_us.offset, 32)
        self.assertEqual(reader.JLinkConnectInfo.acProduct.offset, 50)
        self.assertEqual(reader.JLinkConnectInfo.aPadding.offset, 230)

    def test_cli_selftest_is_fake_only(self):
        with patch.object(reader, "self_test") as checked:
            self.assertEqual(reader.main(["--self-test"]), 0)
            checked.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
