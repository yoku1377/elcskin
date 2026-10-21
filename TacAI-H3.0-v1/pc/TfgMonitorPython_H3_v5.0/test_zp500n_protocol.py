"""No hardware. Synthetic changing/zero values test math, not field accuracy."""
import unittest

from zp500n_protocol import (
    BAUD_RATE, DATA_BITS, PARITY, STOP_BITS, ZP500NProtocol, ZPProtocolError,
)

# Exact36-byte descriptor from raw probe v3, N-mode code6.
DESCRIPTOR = b"130271400760500.00000.00500.030980.0"


class FakePort:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)


class ZP500NProtocolTests(unittest.TestCase):
    def start_protocol(self):
        protocol, port = ZP500NProtocol(), FakePort()
        protocol.start(0.)
        for write in protocol.drain_writes():
            port.write(write)
        return protocol, port

    def confirm_metadata(self, *, tail=b"", old=b"0016.1"):
        protocol, port = self.start_protocol()
        self.assertEqual(protocol.feed(old+DESCRIPTOR*3+tail, .1, received_at=100.1), [])
        for write in protocol.drain_writes():
            port.write(write)
        self.assertEqual(port.writes, [b"\xfa", b"\x25"])
        self.assertEqual(protocol.state, "SWITCHING")
        return protocol, port

    def numeric_protocol(self):
        protocol, port = self.confirm_metadata()
        sample = protocol.feed(b"0016.1", .2, received_at=100.2)
        self.assertEqual(sample[0].force_n, 16.1)
        self.assertEqual(protocol.state, "STREAMING")
        return protocol, port

    def test_serial_parameters_match_confirmed_vendor_profile(self):
        self.assertEqual((BAUD_RATE, DATA_BITS, PARITY, STOP_BITS), (2400, 8, "N", 1))

    def test_three_repetitions_are_required_without_idle_or_fake_metadata(self):
        protocol, port = self.start_protocol()
        self.assertEqual(protocol.feed(b"0016.1"+DESCRIPTOR*2, .1), [])
        protocol.advance(.5)
        self.assertEqual(protocol.drain_writes(), [])
        self.assertIsNone(protocol.unit)
        self.assertEqual(protocol.feed(DESCRIPTOR, .51), [])
        self.assertEqual(protocol.drain_writes(), [b"\x25"])
        self.assertEqual(protocol.metadata_raw, DESCRIPTOR)
        self.assertEqual(protocol.metadata_repeat_count, 3)
        self.assertEqual(protocol.metadata_prefix_raw, b"0016.1")
        self.assertEqual(protocol.unit, "N")
        self.assertEqual(port.writes, [b"\xfa"])

    def test_old_numeric_prefix_is_never_emitted_or_used_as_descriptor(self):
        protocol, _ = self.start_protocol()
        self.assertEqual(protocol.feed(b"0016.1"*20, .1), [])
        self.assertIsNone(protocol.unit)
        self.assertEqual(protocol.feed(DESCRIPTOR*3, .2), [])
        self.assertEqual(protocol.metadata_prefix_raw, b"0016.1"*20)
        self.assertEqual(protocol.drain_writes(), [b"\x25"])

    def test_descriptor_alignment_is_independent_of_old_numeric_tail_length(self):
        for length in range(36):
            with self.subTest(old_tail_bytes=length):
                protocol, _ = self.start_protocol()
                prefix = (b"0016.1"*6)[:length]
                self.assertEqual(protocol.feed(prefix+DESCRIPTOR*3+DESCRIPTOR[:10], .1), [])
                self.assertEqual(protocol.metadata_prefix_raw, prefix)
                self.assertEqual(protocol.drain_writes(), [b"\x25"])
                sample = protocol.feed(DESCRIPTOR[10:]+b"0016.1", .2)[0]
                self.assertEqual(sample.force_n, 16.1)

    def test_transition_tail_crosses_chunks_and_numeric_starts_at_known_boundary(self):
        protocol, _ = self.confirm_metadata(tail=DESCRIPTOR[:10])
        self.assertEqual(protocol.metadata_phase, 10)
        self.assertEqual(protocol.feed(DESCRIPTOR[10:33], .2), [])
        self.assertEqual(protocol.feed(DESCRIPTOR[33:]+b"0", .3), [])
        self.assertEqual(protocol.state, "SWITCHING")
        sample = protocol.feed(b"016.1", .4, received_at=123.4)[0]
        self.assertEqual(sample.raw, b"0016.1")
        self.assertEqual(sample.force_n, 16.1)
        self.assertEqual(sample.host_received_at, 123.4)
        self.assertEqual(protocol.transition_raw, DESCRIPTOR[10:])

    def test_optional_complete_descriptors_after25_are_not_force_values(self):
        protocol, _ = self.confirm_metadata(tail=DESCRIPTOR[:10])
        raw = DESCRIPTOR[10:]+DESCRIPTOR*4
        for index, byte in enumerate(raw):
            self.assertEqual(protocol.feed(bytes([byte]), .2+index*.001), [])
        self.assertEqual(protocol.transition_raw, raw)
        self.assertEqual(protocol.state, "SWITCHING")
        self.assertEqual(protocol.feed(b"0016.1", .5)[0].force_n, 16.1)

    def test_tail_mismatch_fails_instead_of_searching_for_a_number(self):
        protocol, _ = self.confirm_metadata(tail=DESCRIPTOR[:10])
        with self.assertRaisesRegex(ZPProtocolError, "tail changed"):
            protocol.feed(b"0500.0", .2)
        self.assertEqual(protocol.state, "FAILED")

    def test_descriptor_unit_must_be_verified_n_mode(self):
        for code in b"01245789":
            with self.subTest(code=chr(code)):
                protocol, _ = self.start_protocol()
                raw = bytearray(DESCRIPTOR)
                raw[10] = code
                with self.assertRaisesRegex(ZPProtocolError, "unit is unsupported"):
                    protocol.feed(bytes(raw)*3, .1)
                self.assertIsNone(protocol.unit)
                self.assertEqual(protocol.metadata_raw, bytes(raw))
                self.assertEqual(protocol.drain_writes(), [])

    def test_n_mode_code3_uses_same_confirmed_source_unit_mapping(self):
        protocol, _ = self.start_protocol()
        raw = bytearray(DESCRIPTOR)
        raw[10] = ord("3")
        protocol.feed(bytes(raw)*3, .1)
        self.assertEqual(protocol.drain_writes(), [b"\x25"])
        sample = protocol.feed(b"0000.0", .2)[0]  # Synthetic zero, not field accuracy evidence.
        self.assertEqual((sample.unit, sample.scale_to_n, sample.force_n), ("N", 1., 0.))

    def test_invalid_descriptor_layout_never_accepts_an_arbitrary_unit_position(self):
        for raw in (b"M"*40, b"0016.1"*6, b"12345678903"+b"0"*25):
            protocol, _ = self.start_protocol()
            self.assertEqual(protocol.feed(raw*3, .1), [])
            self.assertIsNone(protocol.unit)
            self.assertEqual(protocol.drain_writes(), [])

    def test_changed_or_ambiguous_descriptors_fail_closed(self):
        second = b"2"+DESCRIPTOR[1:]
        protocol, _ = self.start_protocol()
        with self.assertRaisesRegex(ZPProtocolError, "ambiguous"):
            protocol.feed(DESCRIPTOR*3+second*3, .1)
        self.assertEqual(protocol.drain_writes(), [])
        protocol, _ = self.start_protocol()
        with self.assertRaisesRegex(ZPProtocolError, "descriptor changed"):
            protocol.feed(DESCRIPTOR*3+b"x", .1)

    def test_numeric_cannot_escape_in_descriptor_feed_before25_is_released(self):
        protocol, _ = self.start_protocol()
        with self.assertRaisesRegex(ZPProtocolError, "descriptor changed"):
            protocol.feed(DESCRIPTOR*3+b"0016.1", .1)
        self.assertEqual(protocol.drain_writes(), [])

    def test_numeric_before_draining25_fails_without_emitting_a_measurement(self):
        protocol, _ = self.start_protocol()
        self.assertEqual(protocol.feed(DESCRIPTOR*3, .1), [])
        with self.assertRaisesRegex(ZPProtocolError, "preceded release"):
            protocol.feed(b"0016.1", .2)
        self.assertEqual(protocol.drain_writes(), [])

    def test_four_coalesced_numeric_fields_remain_four_samples(self):
        protocol, _ = self.confirm_metadata()
        samples = protocol.feed(b"0016.1"*4, .2, received_at=1000.)
        self.assertEqual([sample.force_n for sample in samples], [16.1]*4)
        self.assertEqual([sample.raw for sample in samples], [b"0016.1"]*4)
        self.assertEqual([sample.host_received_at for sample in samples], [1000.]*4)
        self.assertEqual(protocol.pending_numeric_bytes, 0)

    def test_partial_numeric_bytes_keep_phase_across_host_pause(self):
        protocol, _ = self.numeric_protocol()
        self.assertEqual(protocol.feed(b"0012", .3), [])
        self.assertEqual(protocol.pending_numeric_bytes, 4)
        self.assertEqual(protocol.advance(2.), [])
        self.assertEqual(protocol.pending_numeric_bytes, 4)
        self.assertEqual(protocol.feed(b".3", 2.1)[0].force_n, 12.3)
        self.assertEqual(protocol.pending_numeric_bytes, 0)

    def test_dynamic_and_zero_synthetic_fields_preserve_arithmetic(self):
        protocol, _ = self.confirm_metadata()
        raw = b"0000.00000.10001.00123.40500.0"
        samples = protocol.feed(raw, .2)
        self.assertEqual([sample.force_n for sample in samples], [0., .1, 1., 123.4, 500.])

    def test_unsupported_numeric_formats_fail_without_phase_guessing(self):
        for raw in (b"-016.1", b"+016.1", b"16.100", b"0016,1", b"0500.1", b"1.2-3.4", b"123456", b"\x000016."):
            with self.subTest(raw=raw):
                protocol, _ = self.confirm_metadata()
                with self.assertRaises(ZPProtocolError):
                    protocol.feed(raw, .2)
                self.assertEqual(protocol.state, "FAILED")
                self.assertEqual(protocol.rejected_frames, 1)
                self.assertIsNotNone(protocol.last_rejected_raw)
                self.assertEqual(protocol.drain_writes(), [])

    def test_initialization_including_switch_is_bounded_and_not_retried(self):
        protocol, _ = self.start_protocol()
        with self.assertRaisesRegex(ZPProtocolError, "timed out"):
            protocol.advance(3.)
        self.assertEqual(protocol.drain_writes(), [])
        protocol, _ = self.confirm_metadata()
        with self.assertRaisesRegex(ZPProtocolError, "timed out"):
            protocol.advance(3.)
        self.assertEqual(protocol.drain_writes(), [])

    def test_receive_and_transition_buffers_are_bounded(self):
        protocol, _ = self.start_protocol()
        with self.assertRaisesRegex(ZPProtocolError, "4096"):
            protocol.feed(b"0"*4097, .1)
        self.assertEqual(len(protocol.last_rejected_raw), 4096)
        self.assertTrue(protocol.last_rejected_truncated)
        protocol, _ = self.confirm_metadata()
        with self.assertRaisesRegex(ZPProtocolError, "4096"):
            protocol.feed(b"0"*4097, .2)
        protocol, _ = self.confirm_metadata()
        protocol.feed(DESCRIPTOR*110, .2)
        with self.assertRaisesRegex(ZPProtocolError, "drain exceeded4096"):
            protocol.feed(DESCRIPTOR*4, .3)

    def test_only_fa_then25_can_be_written_and_session_cannot_restart(self):
        protocol, port = self.numeric_protocol()
        for cycle in range(20):
            protocol.feed(b"0016.1", .3+cycle*.1)
            protocol.advance(.31+cycle*.1)
            for write in protocol.drain_writes():
                port.write(write)
        self.assertEqual(port.writes, [b"\xfa", b"\x25"])
        with self.assertRaises(ZPProtocolError):
            protocol.start(3.)

    def test_reverse_or_nonfinite_clocks_fail(self):
        for now in (-1., float("inf"), float("nan")):
            protocol, _ = self.start_protocol()
            with self.assertRaises(ZPProtocolError):
                protocol.advance(now)


if __name__ == "__main__":
    unittest.main()
