"""Exact field-byte replay;constant16.1N evidence, not metrology validation."""
import unittest

from zp500n_protocol import ZP500NProtocol

# Original raw-v3 pasted log SHA256: a13677835af04af51ddb66353653cbd19ee853dd9ff3038bcd3602d0ac2284e7
# Preserved at outputs/zp500n_stream_20261020/input/ZP500N_Raw_v3_20261020_175812.txt.
# Tests inline its exact bytes so frozen source has no external file dependency.
DESCRIPTOR = b"130271400760500.00000.00500.030980.0"
PASSIVE_CHUNKS = [
    (168.3, b'0016.10016.1'),
    (309.0, b'0016.1'),
    (401.2, b'0016.1'),
    (507.3, b'0016.1'),
    (613.4, b'0016.1'),
    (704.6, b'0016.1'),
    (795.4, b'0016.1'),
    (887.2, b'0016.1'),
    (1008.6, b'0016.1'),
    (1100.7, b'0016.1'),
]

AFTER_FA_CHUNKS = [
    (1208.6, b'0016.1'),
    (1314.8, b'130271400760500.0000'),
    (1391.1, b'0.00500.030980.0130'),
    (1466.3, b'271400760500.00000.'),
    (1558.0, b'00500.030980.013027'),
    (1635.0, b'1400760500.00000.005'),
    (1726.4, b'00.030980.0130271400760'),
    (1817.5, b'500.00000.00500.030'),
    (1892.5, b'980.013027140076050'),
    (1983.0, b'0.00000.00500.030980.01'),
    (2058.9, b'30271400760500.00000'),
    (2150.4, b'.00500.030980.01302'),
    (2226.7, b'71400760500.00000.0'),
    (2317.4, b'0500.030980.01302714007'),
]

AFTER_25_CHUNKS = [
    (2423.2, b'60500.00000.00500.03098'),
    (2501.2, b'0.00'),
    (2577.6, b'016.1'),
    (2700.3, b'0016.1'),
    (2792.5, b'0016.1'),
    (2899.0, b'0016.1'),
    (3017.9, b'0016.1'),
    (3109.9, b'0016.1'),
    (3200.8, b'0016.1'),
    (3308.3, b'0016.1'),
    (3413.2, b'0016.1'),
    (3504.2, b'0016.1'),
    (3596.3, b'0016.1'),
    (3689.2, b'0016.1'),
    (3809.5, b'0016.1'),
    (3902.6, b'0016.1'),
    (3994.2, b'0016.1'),
    (4115.6, b'0016.1'),
    (4207.5, b'0016.1'),
    (4298.1, b'0016.1'),
    (4405.4, b'0016.1'),
    (4497.2, b'0016.1'),
    (4603.0, b'0016.1'),
    (4725.3, b'0016.1'),
    (4815.8, b'0016.1'),
    (4891.1, b'0016.1'),
    (5014.3, b'0016.1'),
    (5105.4, b'0016.1'),
    (5197.0, b'0016.1'),
    (5290.3, b'0016.1'),
    (5425.3, b'0016.1'),
    (5516.0, b'0016.1'),
    (5593.8, b'0016.1'),
    (5701.5, b'0016.1'),
    (5808.4, b'0016.1'),
    (5913.6, b'0016.1'),
    (6005.5, b'0016.1'),
    (6096.7, b'0016.1'),
    (6203.2, b'0016.1'),
    (6310.1, b'0016.1'),
    (6404.5, b'0016.1'),
    (6494.3, b'0016.1'),
    (6602.4, b'0016.1'),
    (6724.7, b'0016.1'),
    (6815.4, b'0016.1'),
    (6905.0, b'0016.1'),
    (7010.4, b'0016.1'),
    (7116.9, b'0016.1'),
    (7222.7, b'0016.1'),
    (7298.9, b'0016.1'),
    (7406.0, b'0016.1'),
    (7496.7, b'0016.1'),
    (7618.6, b'0016.1'),
    (7725.6, b'0016.1'),
    (7817.8, b'0016.1'),
    (7909.4, b'0016.1'),
    (8000.2, b'0016.1'),
    (8090.6, b'0016.1'),
    (8212.4, b'0016.1'),
    (8319.2, b'0016.1'),
    (8410.0, b'0016.1'),
    (8517.3, b'0016.1'),
    (8608.8, b'0016.1'),
    (8700.1, b'0016.1'),
    (8791.3, b'0016.1'),
    (8899.4, b'0016.1'),
    (9005.4, b'0016.1'),
    (9096.5, b'0016.1'),
    (9202.6, b'0016.1'),
    (9294.4, b'0016.1'),
    (14127.9, b'0016.10016.10016.10016.1'),
    (14202.2, b'0016.10016.10016.10016.1'),
    (14279.5, b'0016.10016.10016.10016.1'),
    (14354.5, b'0016.10016.10016.10016.1'),
    (14431.3, b'0016.10016.10016.10016.1'),
    (14507.0, b'0016.10016.10016.10016.1'),
    (14583.9, b'0016.10016.10016.10016.1'),
    (14659.4, b'0016.10016.10016.10016.1'),
    (14735.5, b'0016.10016.10016.10016.1'),
    (14812.1, b'0016.10016.10016.10016.1'),
    (14888.3, b'0016.10016.10016.10016.1'),
    (14963.6, b'0016.10016.10016.10016.1'),
    (15039.6, b'0016.10016.10016.10016.1'),
    (15115.4, b'0016.10016.10016.10016.1'),
    (15192.5, b'0016.10016.10016.1'),
    (15313.7, b'0016.1'),
    (15405.4, b'0016.1'),
    (15496.4, b'0016.1'),
    (15603.3, b'0016.1'),
    (15695.5, b'0016.1'),
    (15801.7, b'0016.1'),
    (15910.0, b'0016.1'),
    (16000.2, b'0016.1'),
    (16091.8, b'0016.1'),
    (16213.2, b'0016.1'),
    (16318.2, b'0016.1'),
    (16409.6, b'0016.1'),
    (16501.0, b'0016.1'),
    (16607.9, b'0016.1'),
    (16700.0, b'0016.1'),
    (16792.4, b'0016.1'),
    (16900.0, b'0016.1'),
    (17021.4, b'0016.1'),
    (17113.5, b'0016.1'),
    (17204.7, b'0016.1'),
    (17295.4, b'0016.1'),
    (17403.4, b'0016.1'),
]


class ZP500NLiveReplayTests(unittest.TestCase):
    def replay(self, one_byte=False):
        protocol = ZP500NProtocol()
        protocol.start(0.)
        writes = protocol.drain_writes()
        samples = []
        for phase, chunks in (("AFTER_FA", AFTER_FA_CHUNKS), ("AFTER_25", AFTER_25_CHUNKS)):
            for timestamp, data in chunks:
                pieces = [bytes([byte]) for byte in data] if one_byte else [data]
                for index, piece in enumerate(pieces):
                    now = (timestamp-1114.5)/1000.+index*1e-6
                    received_at = 1000.+(timestamp-1114.5)/1000.
                    result = protocol.feed(piece, now, received_at=received_at)
                    if phase == "AFTER_FA":
                        self.assertEqual(result, [])
                    samples.extend(result)
                    writes.extend(protocol.drain_writes())
            if phase == "AFTER_FA":
                self.assertEqual(protocol.state, "SWITCHING")
                self.assertEqual(protocol.metadata_phase, 10)
                self.assertEqual(protocol.metadata_raw, DESCRIPTOR)
                self.assertEqual(protocol.metadata_prefix_raw, b"0016.1")
        self.assertEqual(writes, [b"\xfa", b"\x25"])
        self.assertEqual(protocol.state, "STREAMING")
        self.assertEqual(protocol.pending_numeric_bytes, 0)
        self.assertEqual(len(samples), 149)
        self.assertEqual([sample.raw for sample in samples], [b"0016.1"]*149)
        self.assertEqual([sample.force_n for sample in samples], [16.1]*149)
        self.assertTrue(all(sample.unit == "N" and sample.scale_to_n == 1. for sample in samples))
        return samples

    def test_original_field_read_chunks_yield149_values_after_exact_info_tail(self):
        samples = self.replay()
        backlog_time = 1000.+(14127.9-1114.5)/1000.
        backlog = [sample for sample in samples if sample.host_received_at == backlog_time]
        self.assertEqual(len(backlog), 4)
        self.assertEqual(len({sample.host_received_at for sample in backlog}), 1)

    def test_every_byte_fragmentation_yields_the_same149_values(self):
        self.replay(one_byte=True)

    def test_arbitrary_chunk_boundaries_preserve_metadata_phase_and_numeric_fields(self):
        protocol = ZP500NProtocol()
        protocol.start(0.)
        writes = protocol.drain_writes()
        samples, total = [], 0
        widths = [1, 13, 7, 31, 2, 4, 19]
        for phase, chunks in (("AFTER_FA", AFTER_FA_CHUNKS), ("AFTER_25", AFTER_25_CHUNKS)):
            raw = b"".join(data for _timestamp, data in chunks)
            cursor, index = 0, 0
            while cursor < len(raw):
                size = widths[index % len(widths)]
                piece = raw[cursor:cursor+size]
                total += len(piece)
                result = protocol.feed(piece, total/240., received_at=1000.+total/240.)
                if phase == "AFTER_FA":
                    self.assertEqual(result, [])
                samples.extend(result)
                writes.extend(protocol.drain_writes())
                cursor += len(piece)
                index += 1
        self.assertEqual(writes, [b"\xfa", b"\x25"])
        self.assertEqual([sample.force_n for sample in samples], [16.1]*149)
        self.assertEqual(protocol.pending_numeric_bytes, 0)

    def test_field_log_period_and_transition_have_independent_exact_byte_proofs(self):
        passive = b"".join(raw for _time, raw in PASSIVE_CHUNKS)
        after_fa = b"".join(raw for _time, raw in AFTER_FA_CHUNKS)
        after_25 = b"".join(raw for _time, raw in AFTER_25_CHUNKS)
        self.assertEqual(passive, b"0016.1"*11)
        self.assertEqual(after_fa, b"0016.1"+DESCRIPTOR*7+DESCRIPTOR[:10])
        self.assertEqual(after_25, DESCRIPTOR[10:]+b"0016.1"*149)
        self.assertEqual(DESCRIPTOR[10:11], b"6")
        for size in range(1, 36):
            stream = after_fa[6:]
            self.assertTrue(any(byte != stream[index % size] for index, byte in enumerate(stream)))


if __name__ == "__main__":
    unittest.main()
