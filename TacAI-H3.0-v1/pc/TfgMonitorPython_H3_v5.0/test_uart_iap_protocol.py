"""UART IAP failure regressions with a memory serial stream, never hardware."""

import struct
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from iap_protocol import (
    APP_START, CMD_BEGIN, CMD_BOOT, CMD_DATA, CMD_END, CMD_HELLO,
    FRAME_MAGIC, HEADER, IapClient, IapError, encode_frame,
)


class MemorySerial:
    def __init__(self, incoming=b"", write_count=None, on_write=None, fragment=536):
        self.incoming = bytearray(incoming)
        self.writes = []
        self.flushes = 0
        self.reads = 0
        self.write_count = write_count
        self.on_write = on_write
        self.fragment = fragment

    @property
    def in_waiting(self):
        return min(len(self.incoming), self.fragment)

    def read(self, length):
        self.reads += 1
        length = min(length, self.fragment)
        result = bytes(self.incoming[:length])
        del self.incoming[:length]
        if not result:
            time.sleep(0.0001)
        return result

    def write(self, wire):
        self.writes.append(bytes(wire))
        if self.on_write:
            self.incoming.extend(self.on_write(wire))
        return len(wire) if self.write_count is None else self.write_count

    def flush(self):
        self.flushes += 1

    def reset_input_buffer(self):
        self.incoming.clear()


def reply(command=CMD_HELLO, sequence=1, payload=b"ok", status=0):
    return encode_frame(command | 0x80, sequence, payload, flags=status)


class UartFrameTests(unittest.TestCase):
    def test_stale_sequence_does_not_discard_matching_ack_or_resend(self):
        uart = MemorySerial(reply(sequence=0) + reply())
        self.assertEqual(IapClient(uart).command(CMD_HELLO), b"ok")
        self.assertEqual(len(uart.writes), 1)

    def test_other_command_and_stale_rejection_are_skipped(self):
        uart = MemorySerial(reply(CMD_DATA, status=5) + reply(sequence=99, status=4) + reply())
        self.assertEqual(IapClient(uart).command(CMD_HELLO), b"ok")
        self.assertEqual(len(uart.writes), 1)

    def test_fragmented_frame_and_ascii_noise_recover(self):
        uart = MemorySerial(b"RAW,123,4.5\r\nTTA" + reply(), fragment=1)
        self.assertEqual(IapClient(uart).command(CMD_HELLO), b"ok")

    def test_bad_crc_before_valid_ack_resynchronizes(self):
        corrupt = bytearray(reply(payload=b"broken"))
        corrupt[-1] ^= 0x01
        uart = MemorySerial(bytes(corrupt) + reply())
        self.assertEqual(IapClient(uart).command(CMD_HELLO), b"ok")
        self.assertEqual(len(uart.writes), 1)

    def test_bad_reserved_header_before_valid_ack_resynchronizes(self):
        corrupt = HEADER.pack(FRAME_MAGIC, 0x81, 0, 1, 0, 1)
        uart = MemorySerial(corrupt + reply())
        self.assertEqual(IapClient(uart).command(CMD_HELLO), b"ok")

    def test_bad_oversize_length_header_before_valid_ack_resynchronizes(self):
        corrupt = HEADER.pack(FRAME_MAGIC, 0x81, 0, 1, 521, 0)
        uart = MemorySerial(corrupt + reply())
        self.assertEqual(IapClient(uart).command(CMD_HELLO), b"ok")

    def test_plausible_but_corrupted_length_does_not_hide_following_ack(self):
        corrupt = HEADER.pack(FRAME_MAGIC, 0x81, 0, 1, 520, 0)
        uart = MemorySerial(corrupt + b"garbage" + reply())
        self.assertEqual(IapClient(uart).command(CMD_HELLO), b"ok")

    def test_partial_candidate_is_retained_between_reads(self):
        uart = MemorySerial(reply(payload=b"abcdefghijk"), fragment=5)
        self.assertEqual(IapClient(uart).command(CMD_HELLO), b"abcdefghijk")

    def test_matching_rejection_stops_without_resend(self):
        uart = MemorySerial(reply(CMD_BEGIN, status=5))
        with self.assertRaisesRegex(IapError, "FLASH_ERROR"):
            IapClient(uart).command(CMD_BEGIN)
        self.assertEqual(len(uart.writes), 1)

    def test_timeout_preserves_command_sequence_and_stale_details(self):
        uart = MemorySerial(reply(CMD_DATA, sequence=77))
        with self.assertRaises(IapError) as caught:
            IapClient(uart).command(CMD_BEGIN, timeout=0.003)
        error = str(caught.exception)
        for text in ("cmd=0x02", "seq=1", "旧帧/其他命令=1", "cmd=0x83, seq=77", "没有自动重发"):
            self.assertIn(text, error)
        self.assertEqual(len(uart.writes), 1)

    def test_corrupt_frame_timeout_preserves_crc_error(self):
        corrupt = bytearray(reply())
        corrupt[-1] ^= 0xFF
        with self.assertRaisesRegex(IapError, "CRC32"):
            IapClient(MemorySerial(corrupt)).command(CMD_HELLO, timeout=0.003)

    def test_short_write_is_not_completed_by_second_write(self):
        uart = MemorySerial(write_count=3)
        with self.assertRaisesRegex(IapError, "串口短写"):
            IapClient(uart).command(CMD_DATA, b"test")
        self.assertEqual(len(uart.writes), 1)
        self.assertEqual(uart.flushes, 0)
        self.assertEqual(uart.reads, 0)

    def test_uncertain_flush_failure_never_replays_write(self):
        uart = MemorySerial()
        uart.flush = mock.Mock(side_effect=OSError("USB removed"))
        with self.assertRaisesRegex(IapError, "cmd=0x02.*USB removed"):
            IapClient(uart).command(CMD_BEGIN)
        self.assertEqual(len(uart.writes), 1)

    def test_sequence_wrap_preserves_zero(self):
        uart = MemorySerial(reply(sequence=65535) + reply(sequence=0))
        client = IapClient(uart)
        client.sequence = 65535
        self.assertEqual(client.command(CMD_HELLO), b"ok")
        self.assertEqual(client.command(CMD_HELLO), b"ok")
        self.assertEqual(client.sequence, 1)

    def test_invalid_arguments_do_not_write(self):
        uart = MemorySerial()
        client = IapClient(uart)
        for args in ((128,), (CMD_DATA, b"x" * 521), (CMD_HELLO, b"", float("nan")),
                     (CMD_HELLO, b"", 0), (CMD_HELLO, "not bytes")):
            with self.subTest(args=args):
                with self.assertRaises(IapError):
                    client.command(*args)
        self.assertEqual(uart.writes, [])


class EnterTests(unittest.TestCase):
    @staticmethod
    def bootloader_serial(payload):
        def on_write(wire):
            if wire.startswith(b"TAI3"):
                _magic, command, _flags, sequence, _length, _reserved = HEADER.unpack_from(wire)
                return reply(command, sequence, payload)
            return b"IAP,ACK,H3G031G8\r\n"
        return MemorySerial(on_write=on_write)

    def assert_invalid_hello(self, payload, pattern):
        # Avoid a five-millisecond wall-clock race at the final response read.
        # Permit one real framed HELLO exchange, then expire on its retry wait.
        now = [0.0]
        sleeps = [0]
        def sleep(_seconds):
            sleeps[0] += 1
            if sleeps[0] > 1:
                now[0] = 1.0
        uart = self.bootloader_serial(payload)
        with mock.patch("iap_protocol.time.monotonic", side_effect=lambda: now[0]), \
             mock.patch("iap_protocol.time.sleep", side_effect=sleep):
            with self.assertRaisesRegex(IapError, pattern):
                IapClient(uart).enter(sync_timeout=0.005)
        self.assertEqual(len(uart.writes), 2)

    def test_hello_length_error_is_a_protocol_error_not_struct_exception(self):
        self.assert_invalid_hello(b"short", "HELLO 长度错误")

    def test_hello_protocol_mismatch_is_reported_with_details(self):
        self.assert_invalid_hello(struct.pack("<IIII", 2, APP_START, 0xD000, 0), "protocol=2")

    @mock.patch("iap_protocol.time.sleep")
    def test_valid_hello_returns_bootloader_metadata(self, _sleep):
        uart = self.bootloader_serial(struct.pack("<IIII", 1, APP_START, 0xD000, 0x56414C44))
        result = IapClient(uart).enter(sync_timeout=0.1)
        self.assertEqual(result, {"protocol": 1, "app_start": APP_START, "app_max": 0xD000, "state": 0x56414C44})
        self.assertEqual(len(uart.writes), 2)


class AlgorithmClient(IapClient):
    def __init__(self, failure=None, bad_data_response=None):
        self.calls = []
        self.events = []
        self.progress = lambda *event: self.events.append(event)
        self.failure = failure
        self.bad_data_response = bad_data_response

    def command(self, command, payload=b"", timeout=3):
        self.calls.append((command, bytes(payload)))
        offset = struct.unpack_from("<I", payload)[0] if command == CMD_DATA else None
        if self.failure == command or self.failure == (command, offset):
            raise IapError("test ACK missing")
        if command == CMD_DATA:
            return (self.bad_data_response if self.bad_data_response is not None else
                    struct.pack("<I", offset + len(payload) - 4))
        return b""


class UpgradeStagesTests(unittest.TestCase):
    @staticmethod
    def image():
        return SimpleNamespace(model="H3G031G8", payload=b"x" * 520, image_crc32=123,
                               app_start=APP_START, app_version=1)

    def test_begin_failure_never_sends_data_or_second_begin(self):
        client = AlgorithmClient(failure=CMD_BEGIN)
        with self.assertRaisesRegex(IapError, "BEGIN.*未确认"):
            client.upgrade(self.image())
        self.assertEqual([command for command, _ in client.calls], [CMD_BEGIN])

    def test_data_failure_keeps_exact_offset_and_stops_before_end(self):
        client = AlgorithmClient(failure=(CMD_DATA, 512))
        with self.assertRaisesRegex(IapError, "DATA.*offset=0x00000200, length=8.*test ACK missing"):
            client.upgrade(self.image())
        self.assertEqual([command for command, _ in client.calls], [CMD_BEGIN, CMD_DATA, CMD_DATA])

    def test_data_bad_ack_length_is_reported_as_data_error(self):
        client = AlgorithmClient(bad_data_response=b"bad")
        with self.assertRaisesRegex(IapError, "DATA.*offset=0x00000000.*确认长度错误"):
            client.upgrade(self.image())
        self.assertEqual([command for command, _ in client.calls], [CMD_BEGIN, CMD_DATA])

    def test_data_wrong_offset_is_reported_with_expected_and_actual(self):
        client = AlgorithmClient(bad_data_response=struct.pack("<I", 13))
        with self.assertRaisesRegex(IapError, "期望 512，实际 13"):
            client.upgrade(self.image())

    def test_end_failure_is_not_replayed_or_booted(self):
        client = AlgorithmClient(failure=CMD_END)
        with self.assertRaisesRegex(IapError, "END.*test ACK missing"):
            client.upgrade(self.image())
        self.assertEqual([command for command, _ in client.calls], [CMD_BEGIN, CMD_DATA, CMD_DATA, CMD_END])

    def test_boot_timeout_preserves_completed_end_without_reupgrade(self):
        client = AlgorithmClient(failure=CMD_BOOT)
        client.upgrade(self.image())
        with self.assertRaisesRegex(IapError, "BOOT.*END 已确认.*未自动重新升级"):
            client.boot()
        self.assertEqual([command for command, _ in client.calls], [CMD_BEGIN, CMD_DATA, CMD_DATA, CMD_END, CMD_BOOT])
        self.assertIn("END 已确认", client.events[-2][2])

    def test_progress_distinguishes_data_end_and_boot_ack(self):
        client = AlgorithmClient()
        client.upgrade(self.image())
        self.assertIn("等待 BOOT", client.events[-1][2])
        self.assertNotIn("BOOT ACK 已收到", client.events[-1][2])
        client.boot()
        self.assertIn("BOOT ACK 已收到", client.events[-1][2])


if __name__ == "__main__":
    unittest.main()
