"""Execute production SPI link state machine for the random stale-ACK stall."""
import unittest

import test_spi_firmware as firmware_tests


class SnapshotStaleFirmwareTests(unittest.TestCase):
    def test_valid_old_response_stalls_and_read_only_reissue_recovers(self):
        firmware_tests.SpiFirmwareTests.compile_and_run(
            self,
            "spi_snapshot_stale_ack_harness",
            "PASS: 3 production RXIRQ stale snapshot/reissue recovery cases",
        )

    def test_ack_drain_guard_covers_receive_faults_and_preserves_mailbox(self):
        firmware_tests.SpiFirmwareTests.compile_and_run(
            self,
            "spi_snapshot_stale_ack_harness",
            "PASS: 7 production RXIRQ/mailbox/snapshot ACK-drain guard cases",
        )

if __name__ == "__main__":
    unittest.main()
