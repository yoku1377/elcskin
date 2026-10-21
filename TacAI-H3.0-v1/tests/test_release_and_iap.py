import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "pc" / "TfgMonitorPython_H3_v5.0"))

from iap_protocol import CMD_HELLO, HEADER, encode_frame
from iap_simulator import CONFIRM_XOR, SimulatedBootloader
from tacai_image import APP_START, FirmwareImage, MODEL_ID, create_container


class ReleaseAndIapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / "firmware" / "application" / "build" / "TacAI_H3_App_v1.bin").read_bytes()

    def test_container_round_trip_and_vector(self):
        image = create_container(self.app, 0x00010000)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "image.tacai"
            path.write_bytes(image.encode())
            loaded = FirmwareImage.load(path)
        self.assertEqual(loaded.model, MODEL_ID)
        self.assertEqual(loaded.app_start, APP_START)
        self.assertEqual(loaded.payload, self.app)

    def test_container_rejects_corruption(self):
        encoded = bytearray(create_container(self.app, 1).encode())
        encoded[-1] ^= 0x01
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.tacai"
            path.write_bytes(encoded)
            with self.assertRaisesRegex(ValueError, "CRC32"):
                FirmwareImage.load(path)

    def test_frame_crc_covers_header_and_payload(self):
        frame = encode_frame(CMD_HELLO, 7, b"abc")
        header = frame[: HEADER.size]
        payload = frame[HEADER.size : -4]
        wire_crc = struct.unpack("<I", frame[-4:])[0]
        self.assertEqual(wire_crc, zlib.crc32(payload, zlib.crc32(header)) & 0xFFFFFFFF)

    def test_wrong_model_cannot_start_erase(self):
        sim = SimulatedBootloader()
        crc = zlib.crc32(self.app) & 0xFFFFFFFF
        token = crc ^ len(self.app) ^ APP_START ^ CONFIRM_XOR
        with self.assertRaisesRegex(ValueError, "BAD_MANIFEST"):
            sim.begin("WRONG", APP_START, len(self.app), crc, 1, token)
        self.assertEqual(sim.state, "EMPTY")

    def test_interrupted_download_stays_recoverable_then_retries(self):
        sim = SimulatedBootloader()
        crc = zlib.crc32(self.app) & 0xFFFFFFFF
        token = crc ^ len(self.app) ^ APP_START ^ CONFIRM_XOR
        sim.begin(MODEL_ID, APP_START, len(self.app), crc, 1, token)
        padded = self.app + b"\xFF" * ((-len(self.app)) % 8)
        sim.data(0, padded[:512])
        sim.power_cycle()
        self.assertTrue(sim.recovery_required)
        with self.assertRaisesRegex(ValueError, "NOT_READY"):
            sim.end()

        sim.begin(MODEL_ID, APP_START, len(self.app), crc, 1, token)
        for offset in range(0, len(padded), 512):
            sim.data(offset, padded[offset : offset + 512])
        sim.end()
        self.assertEqual(sim.state, "VALID")


if __name__ == "__main__":
    unittest.main()
