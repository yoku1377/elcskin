from collections import deque
import tempfile
import unittest
from pathlib import Path

from stm32_rom_uart import (
    ACK,
    CMD_EXTENDED_ERASE,
    CMD_GET,
    CMD_GET_ID,
    CMD_READ_MEMORY,
    CMD_WRITE_MEMORY,
    RomBootError,
    STM32G03_G04_PID,
    Stm32RomBootloader,
    parse_intel_hex,
)


ROOT = Path(__file__).resolve().parents[3]
NO8_HEX = ROOT / "TacAI-H2.2" / "交付" / "TacAI_NO8_四区域平滑融合_v3" / "TacAI_NO8_4region_softfusion_v3.hex"


class FakeRomSerial:
    def __init__(self):
        self.timeout = 1.0
        self._rx = deque()
        self.memory = bytearray(b"\xFF" * 0x10000)
        self.stage = "sync"
        self.current_command = None
        self.address = 0

    def reset_input_buffer(self):
        self._rx.clear()

    def read(self, count):
        data = bytearray()
        while self._rx and len(data) < count:
            data.append(self._rx.popleft())
        return bytes(data)

    def _queue(self, data):
        self._rx.extend(data)

    def write(self, data):
        data = bytes(data)
        if self.stage == "sync":
            if data != b"\x7f":
                raise AssertionError(data)
            self._queue(bytes((ACK,)))
            self.stage = "command"
            return len(data)

        if self.stage == "command":
            command = data[0]
            if data != bytes((command, command ^ 0xFF)):
                raise AssertionError(data)
            self.current_command = command
            self._queue(bytes((ACK,)))
            if command == CMD_GET:
                commands = bytes((0x54, CMD_GET, CMD_GET_ID, CMD_READ_MEMORY, CMD_WRITE_MEMORY, CMD_EXTENDED_ERASE))
                self._queue(bytes((len(commands) - 1,)) + commands + bytes((ACK,)))
            elif command == CMD_GET_ID:
                raw = STM32G03_G04_PID.to_bytes(2, "big")
                self._queue(bytes((len(raw) - 1,)) + raw + bytes((ACK,)))
            elif command == CMD_EXTENDED_ERASE:
                self.stage = "erase"
            elif command in (CMD_WRITE_MEMORY, CMD_READ_MEMORY):
                self.stage = "address"
            else:
                raise AssertionError(f"unsupported fake command 0x{command:02X}")
            return len(data)

        if self.stage == "erase":
            if data != b"\xff\xff\x00":
                raise AssertionError(data)
            self.memory[:] = b"\xFF" * len(self.memory)
            self._queue(bytes((ACK,)))
            self.stage = "command"
            return len(data)

        if self.stage == "address":
            if len(data) != 5:
                raise AssertionError(data)
            self.address = int.from_bytes(data[:4], "big")
            self._queue(bytes((ACK,)))
            self.stage = "write_data" if self.current_command == CMD_WRITE_MEMORY else "read_length"
            return len(data)

        if self.stage == "write_data":
            count = data[0] + 1
            payload = data[1 : 1 + count]
            offset = self.address - 0x08000000
            self.memory[offset : offset + count] = payload
            self._queue(bytes((ACK,)))
            self.stage = "command"
            return len(data)

        if self.stage == "read_length":
            count = data[0] + 1
            offset = self.address - 0x08000000
            self._queue(bytes((ACK,)) + self.memory[offset : offset + count])
            self.stage = "command"
            return len(data)

        raise AssertionError(self.stage)


class IntelHexTests(unittest.TestCase):
    def test_known_no8_image(self):
        image = parse_intel_hex(NO8_HEX)
        self.assertEqual(image.minimum_address, 0x08000000)
        self.assertEqual(image.maximum_address, 0x0800AC7F)
        self.assertEqual(image.byte_count, 44160)
        self.assertEqual(image.initial_sp, 0x20001428)
        self.assertEqual(image.reset_handler, 0x080000D5)

    def test_rejects_bad_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.hex"
            path.write_text(":020000040800F3\n:00000001FF\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "校验和错误"):
                parse_intel_hex(path)


class ProtocolTests(unittest.TestCase):
    def test_programs_and_verifies_known_no8(self):
        image = parse_intel_hex(NO8_HEX)
        uart = FakeRomSerial()
        client = Stm32RomBootloader(uart)
        client.synchronize()
        version, pid, commands = client.identify()
        self.assertEqual(version, 0x54)
        self.assertEqual(pid, STM32G03_G04_PID)
        self.assertIn(CMD_WRITE_MEMORY, commands)
        client.program_and_verify(image)
        expected = image.segments[0].data
        self.assertEqual(uart.memory[: len(expected)], expected)

    def test_rejects_wrong_target_before_erase(self):
        image = parse_intel_hex(NO8_HEX)
        uart = FakeRomSerial()
        client = Stm32RomBootloader(uart)
        client.commands = {CMD_WRITE_MEMORY, CMD_READ_MEMORY, CMD_EXTENDED_ERASE}
        client.pid = 0x1234
        with self.assertRaisesRegex(RomBootError, "芯片型号不匹配"):
            client.program_and_verify(image)


if __name__ == "__main__":
    unittest.main()

