"""Host-only model of the H3 bootloader state transitions for failure testing."""

from __future__ import annotations

import struct
import zlib

from tacai_image import APP_MAX_SIZE, APP_START, MODEL_ID, validate_vector

CONFIRM_XOR = 0xA5A55A5A


class SimulatedBootloader:
    def __init__(self):
        self.flash = bytearray(b"\xFF" * APP_MAX_SIZE)
        self.state = "EMPTY"
        self.manifest = None
        self.offset = 0

    @property
    def recovery_required(self) -> bool:
        return self.state == "DOWNLOADING"

    def begin(self, model: str, start: int, length: int, crc32: int, version: int, token: int):
        expected = crc32 ^ length ^ start ^ CONFIRM_XOR
        if model != MODEL_ID or start != APP_START or not 8 <= length <= APP_MAX_SIZE or token != expected:
            raise ValueError("BAD_MANIFEST")
        self.state = "DOWNLOADING"
        self.manifest = (length, crc32, version)
        self.offset = 0
        self.flash[:] = b"\xFF" * len(self.flash)

    def data(self, offset: int, block: bytes):
        if self.state != "DOWNLOADING" or self.manifest is None:
            raise ValueError("NOT_READY")
        length = self.manifest[0]
        padded = (length + 7) & ~7
        if offset != self.offset or len(block) % 8 or offset + len(block) > padded:
            raise ValueError("BAD_SEQUENCE")
        self.flash[offset : offset + len(block)] = block
        self.offset += len(block)

    def end(self):
        if self.state != "DOWNLOADING" or self.manifest is None:
            raise ValueError("NOT_READY")
        length, expected_crc, _version = self.manifest
        if self.offset < length:
            raise ValueError("NOT_READY")
        payload = bytes(self.flash[:length])
        if zlib.crc32(payload) & 0xFFFFFFFF != expected_crc:
            raise ValueError("CRC_MISMATCH")
        validate_vector(payload)
        self.state = "VALID"

    def power_cycle(self):
        self.offset = 0
        self.manifest = None

