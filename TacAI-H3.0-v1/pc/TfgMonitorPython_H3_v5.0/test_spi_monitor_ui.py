import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtWidgets
import numpy as np
import main
from firmware_updater import FirmwareUpgradeDialog, UpgradeWorker, CONFIRM_TEXT, UPGRADER_VERSION
from iap_protocol import IapError, load_image
from spi_iap_protocol import Snapshot
from ch347_spi import CH347Device, CH347Error
from ch341_spi import CH341Device, CH341Error

IMAGE = Path(__file__).resolve().parent / "TacAI_H3_App_SPI_rxirq_v5.tacai"
if not IMAGE.is_file():
    IMAGE = Path(__file__).resolve().parents[2] / "release" / "spi_rxirq_v5_20261016" / IMAGE.name


class SpiMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_spi_interface_uses_device_index_and_spi_clock(self):
        window = main.Monitor()
        try:
            window.transport_box.setCurrentIndex(1)
            self.assertEqual(window.port_label.text(), "CH347 序号")
            self.assertEqual(window.port_box.currentText(), "0")
            self.assertEqual(window.baud_box.currentText(), "468750")
            with mock.patch.object(main, "SpiForceReader") as reader_class:
                reader = reader_class.return_value
                window._toggle_connection()
                reader_class.assert_called_once_with(0, 468750)
                self.assertFalse(window.transport_box.isEnabled())
                reader.wait.return_value = True
                window._toggle_connection()
                self.assertTrue(window.transport_box.isEnabled())
                self.assertIsNone(window.reader)
        finally:
            window.close()

    def test_ch341_interface_fixed_clock_and_reader_selection(self):
        window = main.Monitor()
        try:
            window.transport_box.setCurrentIndex(2)
            self.assertEqual(window.port_label.text(), "CH341A 序号")
            self.assertEqual(window.baud_box.currentText(), "硬件固定")
            self.assertFalse(window.baud_box.isEnabled())
            with mock.patch.object(main, "CH341ForceReader") as backend:
                window._toggle_connection()
                backend.assert_called_once_with(0)
                backend.return_value.wait.return_value = True
                window._toggle_connection()
            self.assertFalse(window.baud_box.isEnabled())
            window.transport_box.setCurrentIndex(1)
            self.assertEqual(window.baud_box.currentText(), "468750")
            self.assertTrue(window.baud_box.isEnabled())
        finally:
            window.close()

    def test_ch341_reader_uses_same_valid_snapshot_pairing(self):
        reader = main.CH341ForceReader(4)
        sample = Snapshot(1, 3, 100, 1, 0, 1, 255, "h3_spi_20261016_rxirq_v5",
                          tuple(range(24)), (0., 0., 1.25))
        frames, statuses = [], []
        reader.paired_received.connect(frames.append)
        reader.status.connect(lambda *args: statuses.append(args))
        with mock.patch.object(main, "CH341Spi") as bridge, mock.patch.object(main, "SPIIapClient") as client:
            client.return_value.snapshot.side_effect = [sample, IapError("USB lost")]
            reader.run()
            bridge.assert_called_once_with(4)
        self.assertEqual(len(frames), 1)
        np.testing.assert_array_equal(frames[0].raw, np.arange(24))
        self.assertIn("CH341A #4", statuses[1][0])
        self.assertEqual([s[1] for s in statuses], ["info", "success", "error"])

    def test_spi_stale_recovery_keeps_usb_open_marks_gap_and_waits_for_new_sample(self):
        for reader in (main.SpiForceReader(0, 468750), main.CH341ForceReader(0)):
            with self.subTest(adapter=reader.bridge_name):
                first = Snapshot(1, 3, 100, 1, 0, 1, 255, "h3_spi_20261016_rxirq_v5",
                                 tuple(range(24)), (0., 0., 1.25))
                next_sample = Snapshot(1, 3, 300, 2, 0, 1, 255, "h3_spi_20261016_rxirq_v5",
                                      tuple(range(24)), (0., 0., 1.5))
                frames, gaps, faults, statuses, ordered = [], [], [], [], []
                reader.paired_received.connect(lambda frame: (frames.append(frame), ordered.append("frame")))
                reader.stream_gap.connect(lambda *args: (gaps.append(args), ordered.append("gap")))
                reader.stream_fault.connect(lambda *args: faults.append(args))
                reader.status.connect(lambda *args: statuses.append(args))
                calls = [0]
                def poll(**_kwargs):
                    calls[0] += 1
                    if calls[0] == 1:
                        return first
                    if calls[0] == 2:
                        reader._snapshot_trace(dict(event="snapshot_retry", old_sequence=1,
                                                    sequence=2, next_sequence=3))
                        return first  # A new sequence with identical sample is still deduplicated.
                    if calls[0] == 3:
                        self.assertTrue(reader._spi_recovering)
                        reader._snapshot_trace(dict(event="snapshot_recovered", sequence=4, attempts=2))
                        reader.stop()
                        return next_sample
                    self.fail("Recovery must not restart the reader")
                with tempfile.TemporaryDirectory() as temp, \
                     mock.patch.dict(os.environ, {"TACAI_STREAM_LOG_DIR": temp}), \
                     mock.patch.object(reader, "_open_spi") as backend, \
                     mock.patch.object(main, "SPIIapClient") as client:
                    client.return_value.snapshot.side_effect = poll
                    with mock.patch.object(reader, "msleep"):
                        reader.run()
                    backend.assert_called_once()
                    backend.return_value.__exit__.assert_called_once()
                    client.assert_called_once_with(backend.return_value.__enter__.return_value,
                                                  trace=reader._snapshot_trace)
                    entries = [json.loads(line) for line in reader._spi_log_path.read_text(encoding="utf-8").splitlines()]
                self.assertEqual([f.device_ms for f in frames], [100, 300])
                self.assertEqual(ordered, ["frame", "gap", "frame"])
                self.assertEqual(len(gaps), 1)
                self.assertEqual(faults, [])
                self.assertEqual([s[1] for s in statuses], ["info", "success", "info", "success"])
                self.assertIn("已恢复", statuses[-1][0])
                self.assertEqual([entry["detail"]["event"] for entry in entries],
                                 ["snapshot_retry", "snapshot_recovered"])

    def test_spi_recovery_marks_display_unavailable_and_stops_active_test(self):
        window = main.Monitor()
        reader = main.CH341ForceReader(0)
        try:
            window.reader = reader
            reader._spi_has_sample = True
            reader.stream_gap.connect(window._on_stream_gap)
            now = main.time.time()
            window.last_paired_at = now
            window.last_device_ms = 1000
            window.acquisition_diagnostic = main.AcquisitionDiagnostic(1000, now, "fixture", 255, 0, 1)
            window.tare_raw = np.zeros(24)
            window.protocol.active = True
            window.data_stale = False
            with mock.patch.object(reader, "_record_spi_event"), \
                 mock.patch.object(window.protocol, "stop", wraps=window.protocol.stop) as stopped, \
                 mock.patch.object(window, "_update_protocol_ui"):
                reader._snapshot_trace(dict(event="snapshot_retry", old_sequence=4152,
                                            sequence=4153, next_sequence=4154))
                stopped.assert_called_once()
            self.assertTrue(window.data_stale)
            self.assertEqual(window.total_readout.text(), "— N")
            self.assertIn("旧序号 4152", window.force_display_source)
        finally:
            window.reader = None
            window.close()

    def test_switching_adapters_does_not_reuse_other_device_indices(self):
        window = main.Monitor()
        try:
            window.transport_box.setCurrentIndex(1)
            window._on_devices_detected([CH347Device(7, "CH347F", 4, 2, 2)])
            self.assertEqual(window.port_box.currentText(), "7")
            with mock.patch.object(main, "list_ch341_devices") as detect:
                window.transport_box.setCurrentIndex(2)
                detect.assert_not_called()
                self.assertEqual(window.port_box.currentText(), "0")
                detect.return_value = [CH341Device(3, "CH341A", 0x30)]
                window._detect_devices()
                self.assertTrue(window.device_probe.wait(2000))
                self.app.processEvents()
                self.assertEqual(window.port_box.currentText(), "3")
                self.assertFalse(window.baud_box.isEnabled())
                self.assertIn("CH341A", window.status.text())
        finally:
            window.close()

    def test_ch341_upgrade_defaults_to_v5_and_correct_transport(self):
        window = main.Monitor()
        try:
            window.transport_box.setCurrentIndex(2)
            with mock.patch.object(main, "FirmwareUpgradeDialog") as dialog:
                window._open_upgrade()
                _args, kwargs = dialog.call_args
                self.assertEqual(kwargs["transport"], "ch341")
                self.assertEqual(kwargs["clock_hz"], 0)
                self.assertEqual(kwargs["default_image_path"].name, "TacAI_H3_App_SPI_rxirq_v5.tacai")
        finally:
            window.close()

    def test_uart_upgrade_defaults_to_latest_v5_application(self):
        window = main.Monitor()
        try:
            window.port_box.addItem("COM7")
            window.port_box.setCurrentText("COM7")
            with mock.patch.object(main, "FirmwareUpgradeDialog") as dialog:
                window._open_upgrade()
                _args, kwargs = dialog.call_args
                self.assertEqual(kwargs["transport"], "uart")
                self.assertEqual(kwargs["default_image_path"].name, "TacAI_H3_App_SPI_rxirq_v5.tacai")
        finally:
            window.close()

    def test_upgrade_error_stays_visible_after_controls_are_reenabled(self):
        dialog = FirmwareUpgradeDialog("COM7", default_image_path=IMAGE)
        try:
            dialog.confirm_edit.setText(CONFIRM_TEXT)
            with mock.patch.object(QtWidgets.QMessageBox, "critical"):
                dialog._failed("DATA 偏移 512 响应超时；本次已停止")
            self.assertIn("DATA 偏移 512", dialog.status.text())
            self.assertIn("DATA 偏移 512", dialog.event_log.toPlainText())
            dialog._update_enabled()
            self.assertIn("DATA 偏移 512", dialog.status.text())
            self.assertTrue(dialog.choose_button.isEnabled())
        finally:
            dialog.close()

    def test_upgrade_success_status_is_not_replaced_by_confirmation_prompt(self):
        dialog = FirmwareUpgradeDialog("COM7", default_image_path=IMAGE)
        try:
            dialog.confirm_edit.setText(CONFIRM_TEXT)
            with mock.patch.object(QtWidgets.QMessageBox, "information"):
                dialog._completed("CRC 校验通过，启动命令已确认")
            self.assertEqual(dialog.status.text(), "CRC 校验通过，启动命令已确认")
            self.assertEqual(dialog.progress_bar.value(), 1000)
        finally:
            dialog.close()

    def test_ch341_upgrade_dialog_identifies_fixed_clock(self):
        dialog = FirmwareUpgradeDialog("3", transport="ch341")
        try:
            self.assertIn("CH341A #3", dialog.details.toPlainText())
            self.assertIn("固定", dialog.details.toPlainText())
            self.assertIn("SPI", dialog.windowTitle())
        finally:
            dialog.close()

    def test_ch341_failed_flash_is_not_replayed(self):
        failures = []
        worker = UpgradeWorker("3", str(IMAGE), transport="ch341")
        worker.failed.connect(failures.append)
        with mock.patch("firmware_updater.CH341Spi") as bridge, \
             mock.patch("firmware_updater.CH347Spi") as other, \
             mock.patch("firmware_updater.SPIIapClient") as client:
            client.return_value.upgrade.side_effect = IapError("DATA ACK lost")
            worker.run()
            bridge.assert_called_once_with(device_index=3)
            other.assert_not_called()
            client.return_value.enter.assert_called_once()
            client.return_value.upgrade.assert_called_once()
            client.return_value.boot.assert_not_called()
            bridge.return_value.close.assert_called_once()
        self.assertEqual(failures, ["DATA ACK lost"])

    def test_spi_reader_pairs_whole_snapshot_and_rejects_invalid_sample(self):
        reader = main.SpiForceReader(0, 468750)
        good = Snapshot(1, 3, 100, 1, 0, 1, 255, "h3_spi_v1", tuple(range(24)), (0., 0., 1.25))
        bad = Snapshot(1, 18, 200, 1, 1, 1, 247, "h3_spi_v1", tuple(range(24)), (0., 0., 1.25))
        frames, diagnostics, statuses = [], [], []
        reader.paired_received.connect(frames.append)
        reader.diagnostic_received.connect(diagnostics.append)
        reader.status.connect(lambda *args: statuses.append(args))
        with mock.patch.object(main, "CH347Spi"), mock.patch.object(main, "SPIIapClient") as client:
            client.return_value.snapshot.side_effect = [good, good, bad, IapError("USB lost")]
            reader.run()
        self.assertEqual(len(frames), 1)
        np.testing.assert_array_equal(frames[0].raw, np.arange(24))
        self.assertEqual(frames[0].force[2], 1.25)
        self.assertEqual(frames[0].diagnostic.device_ms, 100)
        self.assertEqual(len(diagnostics), 2)
        self.assertFalse(diagnostics[-1].valid)
        self.assertEqual([level for _message, level in statuses], ["info", "success", "error"])
        self.assertIn("USB 已打开", statuses[0][0])

    def test_failed_usb_open_never_reports_sensor_connected(self):
        reader = main.SpiForceReader(0, 468750)
        statuses = []
        reader.status.connect(lambda *args: statuses.append(args))
        with mock.patch.object(main, "CH347Spi", side_effect=CH347Error("USB cannot open")), \
             mock.patch.object(main, "SPIIapClient") as client:
            reader.run()
            client.assert_not_called()
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0][1], "error")

    def test_failed_sensor_response_reports_usb_open_without_success(self):
        reader = main.SpiForceReader(0, 468750)
        statuses = []
        reader.status.connect(lambda *args: statuses.append(args))
        with mock.patch.object(main, "CH347Spi"), mock.patch.object(main, "SPIIapClient") as client:
            client.return_value.snapshot.side_effect = IapError("timeout")
            reader.run()
        self.assertEqual([level for _message, level in statuses], ["info", "error"])
        self.assertIn("logs/stream", statuses[-1][0])

    def test_reader_finished_preserves_visible_error_and_retry_clears_it(self):
        window = main.Monitor()
        reader = main.SpiForceReader(0, 468750)
        try:
            window.transport_box.setCurrentIndex(1)
            window.reader = reader
            reader.status.connect(window._on_status)
            reader.finished.connect(window._on_reader_finished)
            reader.status.emit("SPI 采集失败：USB cannot open", "error")
            reader.finished.emit()
            self.assertIsNone(window.reader)
            self.assertEqual(window.status.text(), "连接失败")
            self.assertIn("USB cannot open", window.connection_error_label.text())
            self.assertFalse(window.connection_error_label.isHidden())
            self.assertIn("USB cannot open", window.force_display_source)
            with mock.patch.object(main, "SpiForceReader") as backend:
                window._toggle_connection()
                self.assertEqual(window.last_connection_error, "")
                self.assertTrue(window.connection_error_label.isHidden())
                backend.return_value.wait.return_value = True
                window._toggle_connection()
        finally:
            window.close()

    def test_probe_lists_real_spi_indices_and_empty_result_has_actionable_error(self):
        window = main.Monitor()
        try:
            window.transport_box.setCurrentIndex(1)
            with mock.patch.object(main, "list_ch347_devices") as detect:
                self.assertEqual(detect.call_count, 0)  # Interface selection never opens USB.
                detect.return_value = [CH347Device(7, "CH347F", 4, 2, 2), CH347Device(3, "UART", 4, 0, 0)]
                window._detect_devices()
                self.assertFalse(window.connect_button.isEnabled())
                self.assertTrue(window.device_probe.wait(2000))
                self.app.processEvents()
                self.assertEqual(window.port_box.count(), 1)
                self.assertEqual(window.port_box.currentText(), "7")
                self.assertTrue(window.connect_button.isEnabled())
                self.assertIsNone(window.device_probe)
                detect.return_value = []
                window._detect_devices()
                self.assertTrue(window.device_probe.wait(2000))
                self.app.processEvents()
                self.assertEqual(window.port_box.count(), 0)
                self.assertIn("CH341PAR", window.last_connection_error)
        finally:
            window.close()

    def test_upgrade_window_identifies_spi_and_first_deployment_requirement(self):
        dialog = FirmwareUpgradeDialog("0", transport="spi", clock_hz=468750)
        try:
            self.assertIn("SPI", dialog.windowTitle())
            self.assertIn(f"v{UPGRADER_VERSION}", dialog.windowTitle())
            self.assertIn("CH347F #0", dialog.details.toPlainText())
            self.assertTrue(any("SWD" in label.text() for label in dialog.findChildren(QtWidgets.QLabel)))
        finally:
            dialog.close()

    def test_spi_protocol_diagnostic_keeps_command_sequence_and_raw_prefix(self):
        worker = UpgradeWorker("0", str(IMAGE), transport="spi")
        messages = []
        worker.diagnostic.connect(messages.append)
        record = dict(event="poll_corrupt", command=2, sequence=7,
                      error="SPI 返回非法 TAI3 帧头", raw_prefix_hex="00 00 00 FF")
        with mock.patch.object(worker, "_log") as logged:
            worker._spi_trace(record)
            logged.assert_called_once_with("spi_protocol", detail=record)
        self.assertEqual(len(messages), 1)
        self.assertIn("cmd=0x02", messages[0])
        self.assertIn("seq=7", messages[0])
        self.assertIn("00 00 00 FF", messages[0])

    def test_real_ch347f_combined_interface_is_selected_and_connectable(self):
        window = main.Monitor()
        device = CH347Device(5, "CH347F", 0, 3, 4, "USB\\VID_1A86&PID_55DE&MI_04",
                             "CH347F.M0:USB2.0 To SPI&I2C&JTAG&SWD", 0)
        try:
            window.transport_box.setCurrentIndex(1)
            window._on_devices_detected([device])
            self.assertEqual(window.port_box.currentText(), "5")
            self.assertIn("FuncType=3", window.log.toPlainText())
            self.assertEqual(window.last_connection_error, "")
            with mock.patch.object(main, "SpiForceReader") as reader_class:
                window._toggle_connection()
                reader_class.assert_called_once_with(5, 468750)
                reader_class.return_value.wait.return_value = True
                window._toggle_connection()
        finally:
            window.close()

    def test_uart_only_or_unknown_result_keeps_exact_exclusion_details_in_log(self):
        window = main.Monitor()
        try:
            window.transport_box.setCurrentIndex(1)
            window._on_devices_detected([CH347Device(1, "UART", 0, 0, 2), CH347Device(8, "Unknown", 0, 99, 4)])
            self.assertEqual(window.port_box.count(), 0)
            self.assertIn("UART 串口接口", window.log.toPlainText())
            self.assertIn("FuncType=99", window.log.toPlainText())
            self.assertIn("未识别的接口类型 99", window.log.toPlainText())
            self.assertIn("各接口的类型和排除原因已记录", window.last_connection_error)
        finally:
            window.close()

    def test_failed_flash_command_is_not_automatically_replayed(self):
        failures = []
        worker = UpgradeWorker("0", str(IMAGE), transport="spi")
        worker.failed.connect(failures.append)
        with mock.patch("firmware_updater.CH347Spi") as backend, \
             mock.patch("firmware_updater.SPIIapClient") as client:
            client.return_value.upgrade.side_effect = IapError("DATA ACK lost")
            worker.run()
            self.assertEqual(client.return_value.upgrade.call_count, 1)
            self.assertEqual(client.return_value.enter.call_count, 1)
            client.return_value.boot.assert_not_called()
            backend.return_value.close.assert_called_once()
        self.assertEqual(failures, ["DATA ACK lost"])

    def test_cleanup_failure_preserves_flash_error(self):
        failures = []
        worker = UpgradeWorker("0", str(IMAGE), transport="spi")
        worker.failed.connect(failures.append)
        with mock.patch("firmware_updater.CH347Spi") as backend, \
             mock.patch("firmware_updater.SPIIapClient") as client:
            client.return_value.upgrade.side_effect = IapError("DATA ACK lost")
            backend.return_value.close.side_effect = OSError("close failed")
            worker.run()
        self.assertEqual(failures, ["DATA ACK lost"])

    def test_close_failure_after_boot_does_not_emit_success_and_failure(self):
        failures, completed = [], []
        worker = UpgradeWorker("0", str(IMAGE), transport="spi")
        worker.failed.connect(failures.append)
        worker.completed.connect(completed.append)
        with mock.patch("firmware_updater.CH347Spi") as backend, \
             mock.patch("firmware_updater.SPIIapClient"):
            backend.return_value.close.side_effect = OSError("close failed")
            worker.run()
        self.assertEqual(completed, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("镜像校验和启动命令已完成", failures[0])

    def test_window_keeps_live_reader_when_close_wait_expires(self):
        window = main.Monitor()
        try:
            window.reader = mock.Mock()
            window.reader.wait.return_value = False
            event = mock.Mock()
            window.closeEvent(event)
            event.ignore.assert_called_once()
            event.accept.assert_not_called()
        finally:
            window.reader = None
            window.close()


if __name__ == "__main__":
    unittest.main()
