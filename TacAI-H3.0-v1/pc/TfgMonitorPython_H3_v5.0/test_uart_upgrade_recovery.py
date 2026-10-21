"""UART recovery may retry HELLO, but must never replay a Flash attempt.

All USB/serial access is replaced with fakes.  These tests reproduce a lost
acknowledgement without requiring a connected sensor or writing any Flash.
"""

from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest import mock

import serial

import firmware_updater
from firmware_updater import UpgradeWorker
from iap_protocol import IapError


IMAGE = Path(__file__).resolve().parent / "TacAI_H3_App_SPI_rxirq_v5.tacai"
if not IMAGE.is_file():
    IMAGE = Path(__file__).resolve().parents[2] / "release" / "spi_rxirq_v5_20261016" / IMAGE.name


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.calls = 0

    def monotonic(self):
        self.calls += 1
        if self.calls > 200:
            raise AssertionError("recovery loop did not terminate")
        self.now += 0.1
        return self.now

    def sleep(self, seconds):
        # Advance the deadline without introducing wall-clock delays.
        self.now += max(float(seconds), 1.0)


class FakeUart:
    def __init__(self, close_error=None):
        self.close = mock.Mock(side_effect=close_error)

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()
        return False


class UartUpgradeRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tacai-upgrade-test-")
        self.addCleanup(temporary.cleanup)
        self.log_directory = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {"TACAI_UPGRADE_LOG_DIR": temporary.name})
        environment.start()
        self.addCleanup(environment.stop)

    def run_worker(self, clients, uarts=None, present=True, open_errors=None):
        clients = list(clients)
        uarts = list(uarts or [FakeUart() for _ in clients])
        clock = FakeClock()
        worker = UpgradeWorker("COM_TEST", str(IMAGE), transport="uart")
        completed, failed, progress = [], [], []
        worker.completed.connect(completed.append)
        worker.failed.connect(failed.append)
        worker.progress.connect(lambda *args: progress.append(args))
        opened = []
        created = []
        errors = list(open_errors or [])

        def open_serial(*args, **kwargs):
            opened.append((args, kwargs))
            if errors:
                error = errors.pop(0)
                if error is not None:
                    raise error
            # Returning the last fake again makes any unintended retry visible
            # in call counts, while still allowing the deadline to expire.
            return uarts[min(len(opened) - 1, len(uarts) - 1)]

        def create_client(uart, progress_callback):
            client = clients[min(len(created), len(clients) - 1)]
            created.append((uart, progress_callback))
            return client

        present_effect = {"return_value": present} if isinstance(present, bool) else {"side_effect": present}
        with mock.patch.object(firmware_updater, "RECOVERY_WAIT_SECONDS", 6.0), \
             mock.patch.object(firmware_updater.time, "monotonic", side_effect=clock.monotonic), \
             mock.patch.object(firmware_updater.time, "sleep", side_effect=clock.sleep), \
             mock.patch.object(firmware_updater, "_port_present", **present_effect), \
             mock.patch.object(firmware_updater.serial, "Serial", side_effect=open_serial), \
             mock.patch.object(firmware_updater, "IapClient", side_effect=create_client):
            worker.run()
        return completed, failed, progress, opened, created

    def assert_terminal_failure(self, result, original):
        completed, failed, progress, opened, created = result
        self.assertEqual(completed, [])
        self.assertEqual(len(failed), 1)
        self.assertIn(original, failed[0])
        self.assertEqual(len(opened), 1, "an accepted Flash attempt must not reopen the port")
        self.assertEqual(len(created), 1, "an accepted Flash attempt must not start a new session")
        self.assertFalse(any("尚未捕获 Bootloader" in item[2] for item in progress),
                         "Flash/BOOT errors must not be mislabeled as synchronization failures")

    def test_success_opens_upgrades_boots_and_completes_once(self):
        client, uart = mock.Mock(), FakeUart()
        completed, failed, _progress, opened, created = self.run_worker([client], [uart])
        self.assertEqual(len(completed), 1)
        self.assertEqual(failed, [])
        self.assertEqual(len(opened), 1)
        self.assertEqual(len(created), 1)
        client.enter.assert_called_once()
        client.upgrade.assert_called_once()
        client.boot.assert_called_once()
        uart.close.assert_called_once()

    def test_sync_failure_can_reconnect_before_flash_begins(self):
        first, second = mock.Mock(), mock.Mock()
        first.enter.side_effect = IapError("HELLO not yet available")
        uarts = [FakeUart(), FakeUart()]
        completed, failed, _progress, opened, created = self.run_worker([first, second], uarts)
        self.assertEqual(len(completed), 1)
        self.assertEqual(failed, [])
        self.assertEqual(len(opened), 2)
        self.assertEqual(len(created), 2)
        first.upgrade.assert_not_called()
        first.boot.assert_not_called()
        second.enter.assert_called_once()
        second.upgrade.assert_called_once()
        second.boot.assert_called_once()
        for uart in uarts:
            uart.close.assert_called_once()

    def test_absent_port_is_waited_for_without_starting_flash(self):
        client = mock.Mock()
        completed, failed, _progress, opened, _created = self.run_worker(
            [client], present=[False, False, True])
        self.assertEqual(len(completed), 1)
        self.assertEqual(failed, [])
        self.assertEqual(len(opened), 1)
        client.upgrade.assert_called_once()

    def test_open_failure_can_retry_before_flash_begins(self):
        client = mock.Mock()
        completed, failed, _progress, opened, created = self.run_worker(
            [client], open_errors=[serial.SerialException("port still enumerating"), None])
        self.assertEqual(len(completed), 1)
        self.assertEqual(failed, [])
        self.assertEqual(len(opened), 2)
        self.assertEqual(len(created), 1)
        client.upgrade.assert_called_once()

    def test_begin_data_and_end_failures_each_stop_without_flash_replay(self):
        for phase in ("BEGIN", "DATA", "END"):
            with self.subTest(phase=phase):
                client, uart = mock.Mock(), FakeUart()
                message = f"{phase} ACK lost after transmission"
                client.upgrade.side_effect = IapError(message)
                result = self.run_worker([client], [uart])
                self.assert_terminal_failure(result, message)
                client.enter.assert_called_once()
                client.upgrade.assert_called_once()
                client.boot.assert_not_called()
                uart.close.assert_called_once()

    def test_usb_disconnect_during_flash_stops_without_flash_replay(self):
        client = mock.Mock()
        client.upgrade.side_effect = serial.SerialException("USB disconnected during DATA")
        result = self.run_worker([client])
        self.assert_terminal_failure(result, "USB disconnected during DATA")
        client.upgrade.assert_called_once()
        client.boot.assert_not_called()

    def test_lost_boot_ack_does_not_rewrite_successfully_checked_image(self):
        client, uart = mock.Mock(), FakeUart()
        client.boot.side_effect = IapError("BOOT ACK timeout")
        result = self.run_worker([client], [uart])
        self.assert_terminal_failure(result, "BOOT ACK timeout")
        client.upgrade.assert_called_once()
        client.boot.assert_called_once()
        uart.close.assert_called_once()

    def test_close_failure_does_not_replay_or_report_success(self):
        client = mock.Mock()
        uart = FakeUart(serial.SerialException("USB release failed"))
        result = self.run_worker([client], [uart])
        self.assert_terminal_failure(result, "USB release failed")
        client.upgrade.assert_called_once()
        client.boot.assert_called_once()
        uart.close.assert_called_once()

    def test_flash_error_is_preserved_when_cleanup_also_fails(self):
        client = mock.Mock()
        client.upgrade.side_effect = IapError("DATA original ACK failure")
        uart = FakeUart(serial.SerialException("secondary USB release failure"))
        result = self.run_worker([client], [uart])
        self.assert_terminal_failure(result, "DATA original ACK failure")
        client.upgrade.assert_called_once()
        client.boot.assert_not_called()
        uart.close.assert_called_once()

    def test_failed_log_keeps_phase_and_original_error_without_payload_or_secrets(self):
        client = mock.Mock()
        client.upgrade.side_effect = IapError("DATA ACK missing at offset 512")
        uart = FakeUart(serial.SerialException("secondary release error"))
        with mock.patch.dict(os.environ, {"TACAI_TEST_SECRET": "not-for-upgrade-log-7f8c"}):
            result = self.run_worker([client], [uart])
        self.assert_terminal_failure(result, "DATA ACK missing at offset 512")
        paths = list(self.log_directory.glob("*.log"))
        self.assertEqual(len(paths), 1)
        text = paths[0].read_text(encoding="utf-8")
        records = [json.loads(line) for line in text.splitlines()]
        events = [record["event"] for record in records]
        self.assertEqual(events.count("started"), 1)
        self.assertEqual(events.count("image_validated"), 1)
        self.assertEqual(events.count("failed"), 1)
        self.assertNotIn("completed", events)
        failed = next(record for record in records if record["event"] == "failed")
        self.assertIn("DATA ACK missing at offset 512", failed["error"])
        self.assertIn("写入", failed["phase"])
        self.assertIn("secondary release error", failed["traceback"])
        self.assertNotIn("not-for-upgrade-log-7f8c", text)
        self.assertNotIn(firmware_updater.CONFIRM_TEXT, text)
        self.assertNotIn("TACAI:IAP:ENTER:H3G031G8", text)
        self.assertTrue(all("payload" not in record for record in records))
        self.assertNotIn(IMAGE.read_bytes()[64:].hex(), text)

    def test_boot_failure_log_identifies_verified_image_without_reflashing(self):
        client = mock.Mock()
        client.boot.side_effect = IapError("BOOT ACK missing after verified image")
        result = self.run_worker([client])
        self.assert_terminal_failure(result, "BOOT ACK missing after verified image")
        paths = list(self.log_directory.glob("*.log"))
        self.assertEqual(len(paths), 1)
        records = [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]
        failed = next(record for record in records if record["event"] == "failed")
        self.assertIn("镜像校验完成", failed["phase"])
        self.assertIn("等待启动确认", failed["phase"])
        self.assertIn("BOOT ACK missing after verified image", failed["error"])
        self.assertEqual(sum(record["event"] == "image_validated" for record in records), 1)
        client.upgrade.assert_called_once()
        client.boot.assert_called_once()


if __name__ == "__main__":
    unittest.main()
