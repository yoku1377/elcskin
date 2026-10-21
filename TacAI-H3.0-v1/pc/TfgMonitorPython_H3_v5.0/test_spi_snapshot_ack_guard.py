"""Black-box ACK-drain regressions; no hardware devices or production edits.

The model separates valid MISO bytes from receive-side acknowledgement. A
slave RX fault can leave response_ready set after the host accepted its CRC.
The cached MISO response remains readable even after a later clean dummy read
clears that flag. This is the state transition reproduced by the production C
firmware harness, rather than a queue that fabricates a request loss.
"""

import struct
from contextlib import nullcontext
import os
import unittest
from unittest.mock import patch
import zlib

from iap_protocol import CMD_BEGIN, CMD_BOOT, CMD_DATA, CMD_END, HEADER, IapError, encode_frame
from spi_iap_protocol import CMD_APP_ENTER, CMD_SNAPSHOT, SNAPSHOT_HEADER, SPI_FRAME_BYTES, SPIIapClient


DUMMY = b"\xff" * SPI_FRAME_BYTES


def response(sequence, *, counter=1, command=CMD_SNAPSHOT, payload=None, status=0):
    if payload is None:
        payload = (SNAPSHOT_HEADER.pack(1, 3, counter * 100, counter, 0, 1, 255)
                   + b"h3_spi_fixture".ljust(32, b"\0")
                   + struct.pack("<24f", *[float(counter)] * 24)
                   + struct.pack("<3f", 0.0, 0.0, float(counter)))
    wire = encode_frame(command | 0x80, sequence, payload, status)
    return wire + b"\xff" * (SPI_FRAME_BYTES - len(wire))


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class AckLatchedFirmware:
    def __init__(self, clock, *, first_ack_faults=1, fault_first_read_each_response=False):
        self.clock = clock
        self.first_ack_faults = first_ack_faults
        self.fault_first_read_each_response = fault_first_read_each_response
        self.persistent_rx_fault = False
        self.drop_request_sequences = set()
        self.ops = []
        self.requests = []
        self.accepted = []
        self.ignored = []
        self.cache = DUMMY
        self.available = False
        self.response_ready = False
        self.staged = None
        self.valid_reads = 0
        self.snapshot_counter = 0
        self.next_dummy_override = None
        self.next_response_override = None
        self.open_calls = self.close_calls = 0

    def open(self):
        self.open_calls += 1
        raise AssertionError("An IAP transaction must not reopen the bridge")

    def close(self):
        self.close_calls += 1
        raise AssertionError("An IAP transaction must not close the bridge")

    def exchange(self, transmitted):
        transmitted = bytes(transmitted)
        self.ops.append(transmitted)
        self.clock.now += 0.01
        if transmitted == DUMMY:
            miso = self.cache if self.available else DUMMY
            if self.staged is not None:
                # As in SPI_LinkQueue, the first dummy clocks idle data and
                # commits the staged response at its CS rising edge.
                self.cache = self.staged
                self.staged = None
                self.available = self.response_ready = True
                self.valid_reads = 0
            elif self.response_ready:
                fault = (self.persistent_rx_fault or self.first_ack_faults > 0 or
                         (self.fault_first_read_each_response and self.valid_reads == 0))
                self.valid_reads += 1
                if self.first_ack_faults > 0:
                    self.first_ack_faults -= 1
                if not fault:
                    self.response_ready = False
                # Do not discard cache: clean polls still return the old ACK.
            if self.next_dummy_override is not None:
                action = self.next_dummy_override
                self.next_dummy_override = None
                return action(miso)
            return miso

        magic, command, flags, sequence, size, reserved = HEADER.unpack_from(transmitted)
        if magic != 0x33494154 or flags != 0 or reserved != 0:
            raise AssertionError("Not a valid TAI3 request")
        if len(transmitted) != HEADER.size + size + 4:
            raise AssertionError("Do not pad or split requests")
        if struct.unpack_from("<I", transmitted, len(transmitted) - 4)[0] != zlib.crc32(transmitted[:-4]) & 0xffffffff:
            raise AssertionError("Request CRC mismatch")
        if command == CMD_SNAPSHOT and len(transmitted) != 16:
            raise AssertionError("The application accepts the original 16-byte snapshot request")
        self.requests.append((command, sequence))
        if self.response_ready or self.staged is not None:
            self.ignored.append((command, sequence, "previous_ack_not_consumed"))
            return (self.cache if self.available else DUMMY)[:len(transmitted)]
        if sequence in self.drop_request_sequences:
            self.ignored.append((command, sequence, "request_receive_fault"))
            return (self.cache if self.available else DUMMY)[:len(transmitted)]

        self.accepted.append((command, sequence))
        if command == CMD_SNAPSHOT:
            self.snapshot_counter += 1
            self.staged = response(sequence, counter=self.snapshot_counter)
        else:
            self.staged = response(sequence, command=command, payload=b"ok")
        if self.next_response_override is not None:
            self.staged = self.next_response_override(sequence)
            self.next_response_override = None
        self.available = False
        return DUMMY[:len(transmitted)]


class SnapshotAckGuardTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.time_patch = patch("spi_iap_protocol.time.monotonic", self.clock.monotonic)
        self.sleep_patch = patch("spi_iap_protocol.time.sleep", self.clock.sleep)
        self.time_patch.start()
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.addCleanup(self.time_patch.stop)

    def client(self, **model_options):
        model = AckLatchedFirmware(self.clock, **model_options)
        traces = []
        return model, SPIIapClient(model, wait_between=0, trace=traces.append), traces

    def prime(self, model, client):
        sample = client.snapshot(timeout=0.6)
        self.assertEqual(sample.frame_counter, 1)
        self.assertTrue(model.response_ready, "Valid MISO may coexist with unconsumed slave RX ACK")
        return sample

    def test_first_snapshot_has_original_short_request_and_no_preflight_guard(self):
        model, client, traces = self.client()
        sample = client.snapshot(timeout=0.6)
        self.assertEqual(sample.frame_counter, 1)
        self.assertEqual(model.ops[0], encode_frame(CMD_SNAPSHOT, 1))
        self.assertEqual(model.requests, [(CMD_SNAPSHOT, 1)])
        self.assertFalse(any(row["event"] == "snapshot_ack_guard" for row in traces))

    def test_without_guard_a_valid_miso_ack_can_still_make_next_short_request_disappear(self):
        model, client, _ = self.client()
        self.prime(model, client)
        # Public command intentionally retains the original single-request path.
        with self.assertRaises(IapError):
            client.command(CMD_SNAPSHOT, timeout=0.25)
        self.assertEqual(model.requests, [(CMD_SNAPSHOT, 1), (CMD_SNAPSHOT, 2)])
        self.assertEqual(model.accepted, [(CMD_SNAPSHOT, 1)])
        self.assertEqual(model.ignored, [(CMD_SNAPSHOT, 2, "previous_ack_not_consumed")])
        self.assertFalse(model.response_ready)
        self.assertEqual(model.cache, response(1, counter=1))

    def test_guard_releases_previous_rx_latch_before_exactly_one_new_request(self):
        model, client, traces = self.client()
        first = self.prime(model, client)
        previous_ops = len(model.ops)
        second = client.snapshot(timeout=0.6)
        self.assertEqual([row.frame_counter for row in (first, second)], [1, 2])
        self.assertEqual(model.ops[previous_ops:previous_ops + 2], [DUMMY, encode_frame(CMD_SNAPSHOT, 2)])
        self.assertEqual(model.requests, [(CMD_SNAPSHOT, 1), (CMD_SNAPSHOT, 2)])
        self.assertEqual(model.accepted, model.requests)
        self.assertFalse(model.ignored)
        self.assertFalse(any(row["event"] == "snapshot_retry" for row in traces))
        guard = [row for row in traces if row["event"] == "snapshot_ack_guard"]
        self.assertEqual([(row["sequence"], row["length"]) for row in guard], [(1, SPI_FRAME_BYTES)])

    def test_every_response_may_have_one_receive_fault_without_retries_or_old_samples(self):
        model, client, traces = self.client(first_ack_faults=0, fault_first_read_each_response=True)
        samples = [client.snapshot(timeout=0.6) for _ in range(20)]
        self.assertEqual([sample.frame_counter for sample in samples], list(range(1, 21)))
        self.assertEqual([sample.device_ms for sample in samples], list(range(100, 2001, 100)))
        self.assertEqual(len(model.requests), 20)
        self.assertEqual(model.requests, model.accepted)
        self.assertFalse(model.ignored)
        self.assertEqual(sum(row["event"] == "snapshot_ack_guard" for row in traces), 19)
        self.assertFalse(any(row["event"] == "snapshot_retry" for row in traces))

    def test_guard_bytes_are_never_a_new_sample_even_if_they_claim_the_next_sequence(self):
        ignored_contents = [DUMMY, b"\0" * SPI_FRAME_BYTES,
                            response(2, counter=999), response(1, counter=999)]
        for guard_bytes in ignored_contents:
            with self.subTest(prefix=guard_bytes[:12]):
                model, client, _ = self.client()
                self.prime(model, client)
                model.next_dummy_override = lambda _old, value=guard_bytes: value
                sample = client.snapshot(timeout=0.6)
                self.assertEqual((sample.device_ms, sample.frame_counter), (200, 2))
                self.assertEqual(model.requests, [(CMD_SNAPSHOT, 1), (CMD_SNAPSHOT, 2)])

    def test_guard_transport_failure_or_short_read_sends_no_new_request_and_never_reopens(self):
        def usb_failure(_old):
            raise OSError("USB failed during ACK drain")
        for action in (usb_failure, lambda old: old[:-1]):
            with self.subTest(action=action):
                model, client, traces = self.client()
                self.prime(model, client)
                model.next_dummy_override = action
                with self.assertRaises(IapError):
                    client.snapshot(timeout=0.6)
                self.assertEqual(model.requests, [(CMD_SNAPSHOT, 1)])
                self.assertEqual((model.open_calls, model.close_calls), (0, 0))
                failure = [row for row in traces if row["event"] == "transport_error"][-1]
                self.assertEqual(failure["phase"], "snapshot_ack_guard")

    def test_guard_uses_original_total_deadline_and_expiry_sends_no_new_request(self):
        model, client, _ = self.client()
        self.prime(model, client)
        def slow_guard(old):
            self.clock.now += 0.61
            return old
        model.next_dummy_override = slow_guard
        with self.assertRaisesRegex(IapError, "超时"):
            client.snapshot(timeout=0.6)
        self.assertEqual(model.requests, [(CMD_SNAPSHOT, 1)])

    def test_guard_does_not_extend_response_deadline_after_its_own_time_cost(self):
        model, client, _ = self.client()
        self.prime(model, client)
        def expensive_guard(old):
            self.clock.now += 0.39
            return old
        model.next_dummy_override = expensive_guard
        model.drop_request_sequences.add(2)
        started = self.clock.now
        with self.assertRaises(IapError):
            client.snapshot(timeout=0.45)
        self.assertLessEqual(self.clock.now - started, 0.461)
        self.assertEqual(model.requests, [(CMD_SNAPSHOT, 1), (CMD_SNAPSHOT, 2)])

    def test_new_request_receive_fault_still_triggers_the_real_bounded_stale_recovery(self):
        model, client, traces = self.client()
        self.prime(model, client)
        model.drop_request_sequences.add(2)
        sample = client.snapshot(timeout=0.6)
        self.assertEqual(sample.frame_counter, 2)
        self.assertEqual(model.requests, [(CMD_SNAPSHOT, 1), (CMD_SNAPSHOT, 2), (CMD_SNAPSHOT, 3)])
        self.assertIn((CMD_SNAPSHOT, 2, "request_receive_fault"), model.ignored)
        self.assertEqual(sum(row["event"] == "snapshot_retry" for row in traces), 1)
        self.assertEqual(sum(row["event"] == "snapshot_recovered" for row in traces), 1)

    def test_repeated_receive_faults_remain_bounded_and_never_return_cached_sample(self):
        model, client, traces = self.client()
        self.prime(model, client)
        model.persistent_rx_fault = True
        started = self.clock.now
        with self.assertRaises(IapError):
            client.snapshot(timeout=0.6)
        self.assertEqual(model.requests, [(CMD_SNAPSHOT, 1), (CMD_SNAPSHOT, 2), (CMD_SNAPSHOT, 3), (CMD_SNAPSHOT, 4)])
        self.assertEqual(model.accepted, [(CMD_SNAPSHOT, 1)])
        self.assertLessEqual(self.clock.now - started, 0.611)
        self.assertEqual(sum(row["event"] == "snapshot_retry" for row in traces), 2)

    def test_sequence_wrap_guard_acknowledges_ffff_then_only_accepts_new_zero_response(self):
        model, client, traces = self.client()
        client.sequence = 0xffff
        self.prime(model, client)
        sample = client.snapshot(timeout=0.6)
        self.assertEqual(sample.frame_counter, 2)
        self.assertEqual(model.requests, [(CMD_SNAPSHOT, 0xffff), (CMD_SNAPSHOT, 0)])
        self.assertEqual(client.sequence, 1)
        self.assertEqual([row["sequence"] for row in traces if row["event"] == "snapshot_ack_guard"], [0xffff])

    def test_post_guard_current_response_keeps_strict_crc_payload_and_rejection_checks(self):
        def corrupt_crc(sequence):
            wire = bytearray(response(sequence, counter=2)); wire[20] ^= 1
            return bytes(wire)
        for replacement in (corrupt_crc,
                            lambda seq: response(seq, payload=b"not a snapshot"),
                            lambda seq: response(seq, status=5)):
            with self.subTest(replacement=replacement):
                model, client, _ = self.client()
                self.prime(model, client)
                model.next_response_override = replacement
                with self.assertRaises(IapError):
                    client.snapshot(timeout=0.25)
                self.assertEqual(model.requests, [(CMD_SNAPSHOT, 1), (CMD_SNAPSHOT, 2)])

    def test_public_upgrade_commands_never_preflight_or_replay_after_prior_snapshot(self):
        for command in (CMD_BEGIN, CMD_DATA, CMD_END, CMD_BOOT, CMD_APP_ENTER):
            with self.subTest(command=command):
                model, client, traces = self.client()
                self.prime(model, client)
                count = len(model.ops)
                with self.assertRaises(IapError):
                    client.command(command, b"single request only", timeout=0.2)
                self.assertEqual(model.ops[count], encode_frame(command, 2, b"single request only"))
                self.assertEqual(model.requests, [(CMD_SNAPSHOT, 1), (command, 2)])
                self.assertFalse(any(row["event"] == "snapshot_ack_guard" for row in traces))

    def test_both_real_reader_paths_keep_guard_invisible_but_still_report_a_true_recovery_gap(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import main
        from PyQt5 import QtWidgets

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        for bridge_type in (main.SpiForceReader, main.CH341ForceReader):
            for lose_new_request in (False, True):
                with self.subTest(bridge=bridge_type.bridge_name, lose_new_request=lose_new_request):
                    model = AckLatchedFirmware(self.clock, first_ack_faults=0,
                                              fault_first_read_each_response=True)
                    if lose_new_request:
                        model.drop_request_sequences.add(2)
                    reader = bridge_type(0, 468750) if bridge_type is main.SpiForceReader else bridge_type(0)
                    frames, gaps, faults, ordered = [], [], [], []
                    def on_frame(frame):
                        frames.append(frame)
                        ordered.append("frame")
                        if len(frames) == 3:
                            reader.stop()
                    reader.paired_received.connect(on_frame)
                    reader.stream_gap.connect(lambda *args: (gaps.append(args), ordered.append("gap")))
                    reader.stream_fault.connect(lambda *args: faults.append(args))
                    with patch.object(reader, "_open_spi", return_value=nullcontext(model)) as open_spi, \
                         patch.object(reader, "_record_spi_event"), patch.object(reader, "msleep"):
                        reader.run()
                    open_spi.assert_called_once()
                    self.assertEqual([frame.device_ms for frame in frames], [100, 200, 300])
                    self.assertEqual([float(frame.raw[0]) for frame in frames], [1.0, 2.0, 3.0])
                    self.assertFalse(faults)
                    self.assertEqual(len(gaps), int(lose_new_request))
                    self.assertEqual(ordered, ["frame", "gap", "frame", "frame"] if lose_new_request
                                     else ["frame", "frame", "frame"])
                    self.assertEqual(len(model.requests), 4 if lose_new_request else 3)
                    self.assertEqual((model.open_calls, model.close_calls), (0, 0))
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
