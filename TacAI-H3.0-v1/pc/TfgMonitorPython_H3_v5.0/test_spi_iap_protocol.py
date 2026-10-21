"""Injected protocol tests only; none of these tests opens a hardware device."""

from collections import deque
import struct
import unittest
from unittest.mock import patch
import zlib

from iap_protocol import (
    CMD_BEGIN, CMD_BOOT, CMD_DATA, CMD_END, CMD_HELLO, CONFIRM_XOR,
    HEADER, IapError, encode_frame,
)
from spi_iap_protocol import (
    APP_ENTER_TOKEN, CMD_APP_ENTER, CMD_SNAPSHOT, SNAPSHOT_HEADER,
    SNAPSHOT_STALE_MIN_SECONDS, SPI_FRAME_BYTES, SPIIapClient,
    SnapshotStaleResponse, _decode_response, decode_snapshot,
)
from tacai_image import APP_MAX_SIZE, APP_START, MODEL_ID, create_container


def padded(command, sequence, payload=b"", status=0):
    frame = encode_frame(command, sequence, payload, status)
    return frame + b"\xFF" * (SPI_FRAME_BYTES - len(frame))


def snapshot_payload():
    return (SNAPSHOT_HEADER.pack(1, 0, 1234, 56, 7, 8, 0xFF)
            + b"H3_SPI_V1".ljust(32, b"\0")
            + struct.pack("<24f", *range(24)) + struct.pack("<3f", 1.25, -2.5, 3.75))


def corrupt_frames(command=CMD_DATA, sequence=1):
    valid = padded(command | 0x80, sequence, b"accepted")
    crc = bytearray(valid)
    crc[HEADER.size] ^= 1
    return [b"\0" * SPI_FRAME_BYTES, bytes(crc), valid[:-1] + b"\0"]


class QueuedExchange:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.requests = []
        self.polls = 0

    def exchange(self, transmitted):
        if transmitted == b"\xFF" * SPI_FRAME_BYTES:
            self.polls += 1
            return self.responses.popleft() if self.responses else transmitted
        self.requests.append(transmitted)
        return b"\xFF" * len(transmitted)


class CommandTests(unittest.TestCase):
    def test_full_request_and_separate_536_byte_poll(self):
        spi = QueuedExchange([padded(CMD_SNAPSHOT | 0x80, 1, b"answer")])
        client = SPIIapClient(spi)
        with patch("spi_iap_protocol.time.sleep") as sleep:
            self.assertEqual(client.command(CMD_SNAPSHOT), b"answer")
        self.assertEqual(spi.requests, [encode_frame(CMD_SNAPSHOT, 1)])
        self.assertEqual(spi.polls, 1)
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.002)

    def test_all_ff_and_stale_reply_only_repoll(self):
        spi = QueuedExchange([
            b"\xFF" * SPI_FRAME_BYTES,
            padded(CMD_BEGIN | 0x80, 0, status=5),
            padded(CMD_DATA | 0x80, 1),
            padded(CMD_BEGIN | 0x80, 1, b"ok"),
        ])
        self.assertEqual(SPIIapClient(spi, wait_between=0).command(CMD_BEGIN), b"ok")
        self.assertEqual(len(spi.requests), 1)
        self.assertEqual(spi.polls, 4)

    def test_bad_crc_even_for_stale_reply_only_repolls(self):
        bad = bytearray(padded(CMD_DATA | 0x80, 0, b"bad"))
        bad[HEADER.size] ^= 1
        spi = QueuedExchange([bytes(bad), padded(CMD_DATA | 0x80, 1)])
        self.assertEqual(SPIIapClient(spi, wait_between=0).command(CMD_DATA), b"")
        self.assertEqual((len(spi.requests), spi.polls), (1, 2))

    def test_bad_header_crc_and_padding_recover_without_repeating_request(self):
        for invalid in corrupt_frames():
            with self.subTest(error_prefix=invalid[:16]):
                spi = QueuedExchange([invalid, padded(CMD_DATA | 0x80, 1, b"ok")])
                answer = SPIIapClient(spi, wait_between=0).command(CMD_DATA, b"write only once")
                self.assertEqual(answer, b"ok")
                self.assertEqual(spi.requests, [encode_frame(CMD_DATA, 1, b"write only once")])
                self.assertEqual(spi.polls, 2)

    def test_decoder_remains_strict_for_bad_header_crc_and_padding(self):
        for invalid in corrupt_frames():
            with self.subTest(error_prefix=invalid[:16]), self.assertRaises(IapError):
                _decode_response(invalid)

    def test_matching_device_error_is_immediate(self):
        spi = QueuedExchange([padded(CMD_BEGIN | 0x80, 1, status=5)])
        with self.assertRaisesRegex(IapError, "FLASH_ERROR"):
            SPIIapClient(spi, wait_between=0).command(CMD_BEGIN)
        self.assertEqual((len(spi.requests), spi.polls), (1, 1))

    def test_matching_device_error_after_corruption_stops_without_further_poll(self):
        spi = QueuedExchange([b"\0" * SPI_FRAME_BYTES,
                              padded(CMD_BEGIN | 0x80, 1, status=5),
                              padded(CMD_BEGIN | 0x80, 1)])
        with self.assertRaisesRegex(IapError, "FLASH_ERROR"):
            SPIIapClient(spi, wait_between=0).command(CMD_BEGIN)
        self.assertEqual((len(spi.requests), spi.polls), (1, 2))

    def test_timeout_does_not_resend_flash_request(self):
        spi = QueuedExchange([])
        with self.assertRaisesRegex(IapError, "没有自动重发"):
            SPIIapClient(spi, wait_between=0).command(CMD_DATA, b"one write", timeout=0.003)
        self.assertEqual(len(spi.requests), 1)
        self.assertGreater(spi.polls, 0)

    def test_timeout_reports_idle_and_stale_responses_without_resending(self):
        spi = QueuedExchange([padded(CMD_BEGIN | 0x80, 9)])
        with self.assertRaises(IapError) as caught:
            SPIIapClient(spi, wait_between=0).command(CMD_SNAPSHOT, timeout=0.003)
        message = str(caught.exception)
        self.assertIn("cmd=0x20", message)
        self.assertIn("旧帧/其他命令=1", message)
        self.assertIn("最后旧帧 cmd=0x82, seq=9", message)
        self.assertIn(f"全 FF={spi.polls - 1}", message)
        self.assertEqual(len(spi.requests), 1)

    def test_persistent_corruption_times_out_with_counts_prefix_and_last_error(self):
        for invalid, expected in zip(corrupt_frames(), ("TAI3", "CRC32", "填充")):
            with self.subTest(expected=expected):
                spi = QueuedExchange([invalid] * 20)
                traces = []
                # Advance a virtual monotonic clock to keep the deadline exact.
                with patch("spi_iap_protocol.time.monotonic",
                           side_effect=(n / 1000 for n in range(1000))):
                    with self.assertRaises(IapError) as caught:
                        SPIIapClient(spi, wait_between=0, trace=traces.append).command(
                            CMD_DATA, b"write only once", timeout=0.01)
                message = str(caught.exception)
                self.assertIn("cmd=0x03, seq=1", message)
                self.assertIn(f"坏帧={spi.polls}", message)
                self.assertIn("全 FF=0", message)
                self.assertIn("旧帧/其他命令=0", message)
                self.assertIn(expected, message)
                self.assertIn(invalid[:32].hex(" ").upper(), message)
                self.assertIn("没有自动重发", message)
                self.assertEqual(len(spi.requests), 1)
                self.assertEqual([event["event"] for event in traces],
                                 ["request", "poll_corrupt", "timeout"])
                self.assertEqual(traces[-1]["corrupt_polls"], spi.polls)

    def test_begin_data_end_and_boot_never_resend_request_after_bad_polls(self):
        for command in (CMD_BEGIN, CMD_DATA, CMD_END, CMD_BOOT):
            with self.subTest(command=command):
                spi = QueuedExchange([b"\0" * SPI_FRAME_BYTES] * 20)
                with patch("spi_iap_protocol.time.monotonic",
                           side_effect=(n / 1000 for n in range(1000))):
                    with self.assertRaisesRegex(IapError, "没有自动重发"):
                        SPIIapClient(spi, wait_between=0).command(command, timeout=0.01)
                self.assertEqual(spi.requests, [encode_frame(command, 1)])

    def test_trace_summarizes_poll_corruption_and_matching_ack(self):
        traces = []
        spi = QueuedExchange([b"\xFF" * SPI_FRAME_BYTES,
                              padded(CMD_BEGIN | 0x80, 99),
                              *corrupt_frames(),
                              padded(CMD_DATA | 0x80, 1, b"ack")])
        SPIIapClient(spi, wait_between=0, trace=traces.append).command(CMD_DATA, b"private image data")
        self.assertEqual([event["event"] for event in traces],
                         ["request", "poll_corrupt", "response"])
        self.assertEqual(traces[0]["command"], CMD_DATA)
        self.assertEqual(traces[0]["sequence"], 1)
        self.assertEqual(traces[0]["payload_length"], len(b"private image data"))
        self.assertEqual(traces[-1]["status"], 0)
        self.assertEqual((traces[-1]["idle_polls"], traces[-1]["stale_polls"],
                          traces[-1]["corrupt_polls"]), (1, 1, 3))
        self.assertNotIn("private image data", str(traces))
        self.assertNotIn("payload", traces[-1])

    def test_broken_trace_callback_cannot_change_flash_transaction(self):
        def broken(_event):
            raise OSError("log file unavailable")
        spi = QueuedExchange([padded(CMD_DATA | 0x80, 1)])
        self.assertEqual(SPIIapClient(spi, wait_between=0, trace=broken).command(CMD_DATA), b"")
        self.assertEqual(len(spi.requests), 1)

    def test_maximum_payload_one_complete_request(self):
        spi = QueuedExchange([padded(CMD_DATA | 0x80, 1)])
        SPIIapClient(spi, wait_between=0).command(CMD_DATA, b"a" * 520)
        self.assertEqual(len(spi.requests[0]), 536)

    def test_oversize_payload_stops_before_exchange(self):
        spi = QueuedExchange([])
        with self.assertRaisesRegex(IapError, "520"):
            SPIIapClient(spi).command(CMD_DATA, b"a" * 521)
        self.assertEqual(spi.requests, [])

    def test_short_response_is_immediate_transport_failure(self):
        spi = QueuedExchange([b"a", padded(CMD_HELLO | 0x80, 1)])
        with self.assertRaisesRegex(IapError, "长度"):
            SPIIapClient(spi, wait_between=0).command(CMD_HELLO)
        self.assertEqual((len(spi.requests), spi.polls), (1, 1))

    def test_usb_error_during_poll_stops_immediately(self):
        spi = QueuedExchange([])
        original = spi.exchange
        traces = []
        def disconnected_during_poll(transmitted):
            if transmitted == b"\xFF" * SPI_FRAME_BYTES:
                raise OSError("device unplugged")
            return original(transmitted)
        with self.assertRaisesRegex(IapError, "device unplugged"):
            SPIIapClient(disconnected_during_poll, trace=traces.append).command(CMD_DATA)
        self.assertEqual(len(spi.requests), 1)
        self.assertEqual(traces[-1]["event"], "transport_error")
        self.assertEqual(traces[-1]["phase"], "poll")

    def test_exchange_exception_is_iap_error(self):
        def disconnected(transmitted):
            raise OSError("device unplugged")
        with self.assertRaisesRegex(IapError, "device unplugged"):
            SPIIapClient(disconnected).command(CMD_HELLO)

    def test_sequence_wraps_at_uint16(self):
        spi = QueuedExchange([padded(0x81, 0xFFFF), padded(0x81, 0)])
        client = SPIIapClient(spi, wait_between=0)
        client.sequence = 0xFFFF
        client.command(CMD_HELLO)
        client.command(CMD_HELLO)
        self.assertEqual(client.sequence, 1)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_exact_layout_and_values(self):
        data = snapshot_payload()
        self.assertEqual(len(data), 164)
        frame = decode_snapshot(data)
        self.assertEqual((frame.version, frame.status, frame.device_ms, frame.frame_counter), (1, 0, 1234, 56))
        self.assertEqual((frame.error_count, frame.baseline_epoch, frame.valid_mask), (7, 8, 255))
        self.assertEqual(frame.firmware_id, "H3_SPI_V1")
        self.assertEqual(frame.raw, tuple(float(v) for v in range(24)))
        self.assertEqual(frame.force, (1.25, -2.5, 3.75))

    def test_snapshot_helper_uses_command20(self):
        spi = QueuedExchange([padded(CMD_SNAPSHOT | 0x80, 1, snapshot_payload())])
        self.assertEqual(SPIIapClient(spi, wait_between=0).snapshot().device_ms, 1234)
        self.assertEqual(HEADER.unpack_from(spi.requests[0])[1], CMD_SNAPSHOT)

    def test_reject_length_version_reserved(self):
        data = snapshot_payload()
        cases = [data[:-1], data + b"\0", b"\x02" + data[1:], data[:21] + b"\x01" + data[22:]]
        for invalid in cases:
            with self.subTest(length=len(invalid)), self.assertRaises(IapError):
                decode_snapshot(invalid)

    def test_reject_malformed_name(self):
        for name in (b"a" * 32, b"\0" * 32, b"\xff\0" + b"\0" * 30,
                     b"ok\0junk" + b"\0" * 25, b"bad space\0" + b"\0" * 22):
            data = snapshot_payload()
            with self.subTest(name=name), self.assertRaises(IapError):
                decode_snapshot(data[:24] + name + data[56:])

    def test_reject_nonfinite_channels(self):
        for offset, value in ((56, float("nan")), (152, float("inf"))):
            data = bytearray(snapshot_payload())
            struct.pack_into("<f", data, offset, value)
            with self.subTest(offset=offset), self.assertRaises(IapError):
                decode_snapshot(data)


class VirtualClock:
    """Advances only with injected USB latency or protocol waits."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class StickySnapshotExchange:
    """A missed request leaves the last valid ACK available indefinitely."""

    def __init__(self, clock, dropped_requests=(1,), ready_delay=0.0,
                 poll_seconds=0.015, old_sequence=4152):
        self.clock = clock
        self.dropped_requests = dropped_requests
        self.ready_delay = ready_delay
        self.poll_seconds = poll_seconds
        self.requests = []
        self.request_times = []
        self.polls_per_request = []
        old_payload = bytearray(snapshot_payload())
        struct.pack_into("<II", old_payload, 4, 100, 5)
        self.pending = padded(CMD_SNAPSHOT | 0x80, old_sequence, old_payload)
        self.next_response = None

    def exchange(self, transmitted):
        if transmitted == b"\xFF" * SPI_FRAME_BYTES:
            self.clock.now += self.poll_seconds
            self.polls_per_request[-1] += 1
            if self.next_response is not None:
                ready_at, response = self.next_response
                if self.clock.now >= ready_at:
                    self.pending = response
                    self.next_response = None
            return self.pending
        _magic, command, _status, sequence, _length, _reserved = HEADER.unpack_from(transmitted)
        self.requests.append((command, sequence))
        self.request_times.append(self.clock.now)
        self.polls_per_request.append(0)
        if len(self.requests) not in self.dropped_requests:
            payload = bytearray(snapshot_payload())
            struct.pack_into("<II", payload, 4, 2000 + len(self.requests), 100 + len(self.requests))
            self.next_response = (self.clock.now + self.ready_delay,
                                  padded(command | 0x80, sequence, payload))
        return b"\xFF" * len(transmitted)


class SnapshotRecoveryTests(unittest.TestCase):
    def run_with_clock(self, client, clock, timeout=0.6):
        with patch("spi_iap_protocol.time.monotonic", clock.monotonic), \
             patch("spi_iap_protocol.time.sleep", clock.sleep):
            return client.snapshot(timeout=timeout)

    def test_missed_request_recovers_fresh_sample_without_accepting_old_ack(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock)
        traces = []
        client = SPIIapClient(spi, wait_between=0, trace=traces.append)
        client.sequence = 4153
        frame = self.run_with_clock(client, clock)
        self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 4153), (CMD_SNAPSHOT, 4154)])
        self.assertEqual((frame.device_ms, frame.frame_counter), (2002, 102))
        self.assertGreaterEqual(spi.request_times[1], SNAPSHOT_STALE_MIN_SECONDS)
        self.assertLess(clock.now, 0.6)
        self.assertEqual([event["event"] for event in traces],
                         ["request", "snapshot_retry", "request", "response", "snapshot_recovered"])
        self.assertEqual(traces[1]["old_sequence"], 4152)
        self.assertEqual(traces[1]["next_sequence"], 4154)
        self.assertEqual(traces[-1]["attempts"], 2)

    def test_old_ack_during_normal_103ms_acquisition_does_not_start_another_request(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock, dropped_requests=(), ready_delay=0.12,
                                     poll_seconds=0.01)
        traces = []
        frame = self.run_with_clock(SPIIapClient(spi, wait_between=0, trace=traces.append), clock)
        self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 1)])
        self.assertEqual(frame.frame_counter, 101)
        self.assertGreater(spi.polls_per_request[0], 3)
        self.assertEqual([event["event"] for event in traces], ["request", "response"])

    def test_stale_detection_requires_three_polls_even_if_usb_transactions_are_slow(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock, poll_seconds=0.10)
        self.run_with_clock(SPIIapClient(spi, wait_between=0), clock)
        self.assertEqual(spi.polls_per_request[0], 3)
        self.assertAlmostEqual(spi.request_times[1], 0.3)

    def test_permanent_stale_ack_stops_after_three_requests_inside_original_deadline(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock, dropped_requests=range(1, 100), poll_seconds=0.01)
        traces = []
        with self.assertRaisesRegex(IapError, "超时"):
            self.run_with_clock(SPIIapClient(spi, wait_between=0, trace=traces.append), clock)
        self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 1), (CMD_SNAPSHOT, 2), (CMD_SNAPSHOT, 3)])
        self.assertLessEqual(clock.now, 0.61)
        self.assertLess(spi.request_times[-1], 0.6)
        self.assertEqual(len([event for event in traces if event["event"] == "snapshot_retry"]), 2)
        self.assertFalse(any(event["event"] == "snapshot_recovered" for event in traces))

    def test_snapshot_retry_sequence_wraps_without_accepting_previous_ffff_ack(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock, old_sequence=0xFFFE)
        client = SPIIapClient(spi, wait_between=0)
        client.sequence = 0xFFFF
        frame = self.run_with_clock(client, clock)
        self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 0xFFFF), (CMD_SNAPSHOT, 0)])
        self.assertEqual(client.sequence, 1)
        self.assertEqual(frame.frame_counter, 102)

    def test_short_snapshot_deadline_polls_old_ack_without_exceeding_it_to_retry(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock, poll_seconds=0.01)
        with self.assertRaisesRegex(IapError, "没有自动重发"):
            self.run_with_clock(SPIIapClient(spi, wait_between=0), clock, timeout=0.15)
        self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 1)])
        self.assertLessEqual(clock.now, 0.16)

    def test_intermittent_old_acks_are_not_treated_as_repeated_same_response(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock, dropped_requests=range(1, 100), poll_seconds=0.01)
        original_exchange = spi.exchange

        def occasional_idle(transmitted):
            response = original_exchange(transmitted)
            if transmitted == b"\xFF" * SPI_FRAME_BYTES and spi.polls_per_request[-1] % 3 == 0:
                return b"\xFF" * SPI_FRAME_BYTES
            return response

        with self.assertRaisesRegex(IapError, "没有自动重发"):
            self.run_with_clock(SPIIapClient(occasional_idle, wait_between=0), clock)
        self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 1)])

    def test_three_requests_may_recover_but_old_pending_sample_is_never_delivered(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock, dropped_requests=(1, 2), poll_seconds=0.01)
        traces = []
        frame = self.run_with_clock(SPIIapClient(spi, wait_between=0, trace=traces.append), clock)
        self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 1), (CMD_SNAPSHOT, 2), (CMD_SNAPSHOT, 3)])
        self.assertEqual((frame.device_ms, frame.frame_counter), (2003, 103))
        self.assertEqual(traces[-1]["attempts"], 3)
        self.assertLess(clock.now, 0.6)

    def test_late_ack_for_abandoned_sequence_is_ignored_during_recovery(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock)
        original_exchange = spi.exchange

        def late_first_request_ack(transmitted):
            response = original_exchange(transmitted)
            if (transmitted == b"\xFF" * SPI_FRAME_BYTES and len(spi.requests) == 2
                    and spi.polls_per_request[-1] == 1):
                return padded(CMD_SNAPSHOT | 0x80, 1, snapshot_payload())
            return response

        frame = self.run_with_clock(SPIIapClient(late_first_request_ack, wait_between=0), clock)
        self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 1), (CMD_SNAPSHOT, 2)])
        self.assertEqual(spi.polls_per_request[1], 2)
        self.assertEqual((frame.device_ms, frame.frame_counter), (2002, 102))

    def test_public_command_even_command20_stays_single_send(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock, dropped_requests=range(1, 100))
        client = SPIIapClient(spi, wait_between=0)
        with patch("spi_iap_protocol.time.monotonic", clock.monotonic), \
             patch("spi_iap_protocol.time.sleep", clock.sleep):
            with self.assertRaisesRegex(IapError, "没有自动重发"):
                client.command(CMD_SNAPSHOT, timeout=0.6)
        self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 1)])

    def test_no_upgrade_command_replays_when_device_repeats_old_snapshot_ack(self):
        for command in (CMD_BEGIN, CMD_DATA, CMD_END, CMD_BOOT, CMD_APP_ENTER):
            with self.subTest(command=command):
                clock = VirtualClock()
                spi = StickySnapshotExchange(clock, dropped_requests=range(1, 100))
                with patch("spi_iap_protocol.time.monotonic", clock.monotonic), \
                     patch("spi_iap_protocol.time.sleep", clock.sleep):
                    with self.assertRaisesRegex(IapError, "没有自动重发"):
                        SPIIapClient(spi, wait_between=0).command(command, timeout=0.6)
                self.assertEqual(spi.requests, [(command, 1)])

    def test_snapshot_all_ff_or_corruption_does_not_trigger_a_fresh_request(self):
        for response in (b"\xFF" * SPI_FRAME_BYTES, b"\0" * SPI_FRAME_BYTES):
            with self.subTest(prefix=response[:1]):
                clock = VirtualClock()
                spi = StickySnapshotExchange(clock, dropped_requests=range(1, 100))
                spi.pending = response
                with self.assertRaisesRegex(IapError, "没有自动重发"):
                    self.run_with_clock(SPIIapClient(spi, wait_between=0), clock)
                self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 1)])

    def test_matching_rejection_or_bad_snapshot_payload_stops_immediately(self):
        for response in (padded(CMD_SNAPSHOT | 0x80, 1, status=5),
                         padded(CMD_SNAPSHOT | 0x80, 1, b"malformed snapshot")):
            with self.subTest(response=response[:20]):
                clock = VirtualClock()
                spi = StickySnapshotExchange(clock, dropped_requests=range(1, 100))
                spi.pending = response
                with self.assertRaises(IapError):
                    self.run_with_clock(SPIIapClient(spi, wait_between=0), clock)
                self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 1)])
                self.assertEqual(spi.polls_per_request, [1])

    def test_snapshot_usb_failure_after_old_ack_does_not_retry_or_reopen(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock)
        original_exchange = spi.exchange

        def failed_poll(transmitted):
            if transmitted == b"\xFF" * SPI_FRAME_BYTES and clock.now >= 0.03:
                raise OSError("USB lost")
            return original_exchange(transmitted)

        with self.assertRaisesRegex(IapError, "USB lost"):
            self.run_with_clock(SPIIapClient(failed_poll, wait_between=0), clock)
        self.assertEqual(spi.requests, [(CMD_SNAPSHOT, 1)])

    def test_snapshot_stale_error_has_recoverable_read_only_diagnostics(self):
        clock = VirtualClock()
        spi = StickySnapshotExchange(clock)
        with patch("spi_iap_protocol.time.monotonic", clock.monotonic), \
             patch("spi_iap_protocol.time.sleep", clock.sleep):
            with self.assertRaises(SnapshotStaleResponse) as caught:
                SPIIapClient(spi, wait_between=0)._command(
                    CMD_SNAPSHOT, timeout=0.6, snapshot_stale_recovery=True)
        error = caught.exception
        self.assertEqual((error.sequence, error.old_sequence), (1, 4152))
        self.assertGreaterEqual(error.stale_polls, 3)
        self.assertGreaterEqual(error.elapsed, SNAPSHOT_STALE_MIN_SECONDS)


class AutoBootExchange:
    def __init__(self, fail_enter=False, wrong_offset=False):
        self.requests = []
        self.pending = None
        self.fail_enter = fail_enter
        self.wrong_offset = wrong_offset

    def exchange(self, transmitted):
        if transmitted == b"\xFF" * SPI_FRAME_BYTES:
            return self.pending
        magic, cmd, flags, seq, size, reserved = HEADER.unpack_from(transmitted)
        payload = transmitted[HEADER.size:HEADER.size + size]
        self.requests.append((cmd, seq, payload))
        status, answer = 0, b""
        if cmd == CMD_APP_ENTER:
            status = 3 if self.fail_enter else 0
            answer = b"" if self.fail_enter else APP_ENTER_TOKEN
        elif cmd == CMD_HELLO:
            answer = struct.pack("<IIII", 1, APP_START, APP_MAX_SIZE, 1)
        elif cmd == CMD_DATA:
            offset, = struct.unpack_from("<I", payload)
            answer = struct.pack("<I", offset + len(payload) - 4 + int(self.wrong_offset))
        self.pending = padded(cmd | 0x80, seq, answer, status)
        return b"\xFF" * len(transmitted)


class UpgradeTests(unittest.TestCase):
    def test_enter_application_ack_then_bootloader_hello(self):
        spi = AutoBootExchange()
        with patch("spi_iap_protocol.time.sleep"):
            hello = SPIIapClient(spi, wait_between=0).enter()
        self.assertEqual(hello["app_start"], APP_START)
        self.assertEqual([cmd for cmd, seq, data in spi.requests], [CMD_APP_ENTER, CMD_HELLO])
        self.assertEqual(spi.requests[0][2], b"H3G031G8")

    def test_enter_already_in_bootloader_uses_hello_recovery(self):
        spi = AutoBootExchange(fail_enter=True)
        with patch("spi_iap_protocol.time.sleep"):
            self.assertEqual(SPIIapClient(spi, wait_between=0).enter()["protocol"], 1)

    def test_reuses_image_manifest_blocks_and_progress(self):
        spi = AutoBootExchange()
        progress = []
        payload = struct.pack("<II", 0x20001000, APP_START + 9) + b"b" * 515
        image = create_container(payload, 0x10002)
        client = SPIIapClient(spi, lambda *items: progress.append(items), wait_between=0)
        client.upgrade(image)
        client.boot()
        commands = [cmd for cmd, seq, data in spi.requests]
        self.assertEqual(commands, [CMD_BEGIN, CMD_DATA, CMD_DATA, CMD_END, CMD_BOOT])
        manifest = struct.unpack("<12sIIIII", spi.requests[0][2])
        self.assertEqual(manifest[:5], (MODEL_ID.encode().ljust(12, b"\0"), APP_START,
                                       len(payload), zlib.crc32(payload), 0x10002))
        self.assertEqual(manifest[5], zlib.crc32(payload) ^ len(payload) ^ APP_START ^ CONFIRM_XOR)
        self.assertEqual(spi.requests[1][2], struct.pack("<I", 0) + payload[:512])
        self.assertEqual(spi.requests[2][2], struct.pack("<I", 512) + payload[512:] + b"\xFF" * 5)
        self.assertEqual(progress[-1][0:2], (len(payload), len(payload)))
        self.assertIn("BOOT ACK", progress[-1][2])

    def test_full_upgrade_repolls_corrupt_ack_for_each_write_without_replay(self):
        class CorruptFirstPoll(AutoBootExchange):
            def __init__(self):
                super().__init__()
                self.first_poll = False
                self.corruptions = 0

            def exchange(self, transmitted):
                if transmitted != b"\xFF" * SPI_FRAME_BYTES:
                    self.first_poll = True
                    return super().exchange(transmitted)
                command = self.requests[-1][0]
                if self.first_poll and command in (CMD_BEGIN, CMD_DATA, CMD_END, CMD_BOOT):
                    self.first_poll = False
                    invalid = corrupt_frames(command, self.requests[-1][1])[self.corruptions % 3]
                    self.corruptions += 1
                    return invalid
                self.first_poll = False
                return super().exchange(transmitted)

        spi = CorruptFirstPoll()
        payload = struct.pack("<II", 0x20001000, APP_START + 9) + b"b" * 515
        image = create_container(payload, 0x20005)
        traces = []
        client = SPIIapClient(spi, wait_between=0, trace=traces.append)
        with patch("spi_iap_protocol.time.sleep"):
            client.enter()
        client.upgrade(image)
        client.boot()
        commands = [cmd for cmd, seq, data in spi.requests]
        self.assertEqual(commands, [CMD_APP_ENTER, CMD_HELLO, CMD_BEGIN,
                                    CMD_DATA, CMD_DATA, CMD_END, CMD_BOOT])
        self.assertEqual([seq for cmd, seq, data in spi.requests], list(range(1, 8)))
        self.assertEqual(spi.corruptions, 5)
        self.assertEqual(len([event for event in traces if event["event"] == "poll_corrupt"]), 5)
        self.assertEqual(len([event for event in traces if event["event"] == "response"]), 7)

    def test_corrupt_boot_ack_timeout_preserves_completed_image_without_reflash(self):
        class BadBootAck(AutoBootExchange):
            def __init__(self):
                super().__init__()
                self.bad_poll_sent = False

            def exchange(self, transmitted):
                if transmitted == b"\xFF" * SPI_FRAME_BYTES and self.requests[-1][0] == CMD_BOOT:
                    if not self.bad_poll_sent:
                        self.bad_poll_sent = True
                        return b"\0" * SPI_FRAME_BYTES
                    return transmitted
                return super().exchange(transmitted)

        spi = BadBootAck()
        image = create_container(struct.pack("<II", 0x20001000, APP_START + 9) + b"abcd", 1)
        progress = []
        client = SPIIapClient(spi, lambda *items: progress.append(items), wait_between=0)
        client.upgrade(image)
        with patch("spi_iap_protocol.time.monotonic", side_effect=(n / 10 for n in range(1000))):
            with self.assertRaises(IapError) as caught:
                client.boot()
        message = str(caught.exception)
        self.assertIn("BOOT 启动确认未收到", message)
        self.assertIn("镜像校验已完成", message)
        self.assertIn("未自动重新升级", message)
        self.assertIn("坏帧=1", message)
        self.assertEqual([cmd for cmd, seq, data in spi.requests],
                         [CMD_BEGIN, CMD_DATA, CMD_END, CMD_BOOT])
        self.assertIn("END 已确认", progress[-2][2])
        self.assertIn("正在请求启动", progress[-1][2])

    def test_offset_error_stops_before_end_or_boot(self):
        spi = AutoBootExchange(wrong_offset=True)
        image = create_container(struct.pack("<II", 0x20001000, APP_START + 9) + b"abcd", 1)
        with self.assertRaisesRegex(IapError, "偏移"):
            SPIIapClient(spi, wait_between=0).upgrade(image)
        self.assertEqual([cmd for cmd, seq, data in spi.requests], [CMD_BEGIN, CMD_DATA])


if __name__ == "__main__":
    unittest.main()
