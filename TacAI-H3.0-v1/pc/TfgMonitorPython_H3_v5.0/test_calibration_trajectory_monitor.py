"""Calibration traces receive actual, immutable RAW coordinate audit values."""
import csv
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
from PyQt5 import QtWidgets

import main
from calibration_acquisition import CalibrationAcquisitionDialog, CaptureStep
from calibration_trajectory import RAW_COLUMNS, DELTA_COLUMNS
from no8_raw_tare_core import AcquisitionDiagnostic, PairedFrame


class CalibrationTrajectoryMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make_window(self):
        window = main.Monitor()
        window._save_force_trace = mock.Mock()
        self.addCleanup(window.close)
        return window

    @staticmethod
    def frame(with_diag=True):
        diag = AcquisitionDiagnostic(2500, 100., "h3_actual", 255, 17, 8) if with_diag else None
        return PairedFrame(2500, 100., np.arange(24, dtype=float) + 125.,
                           np.array([.1, .2, 9.3]), diag)

    def test_accepted_frame_forwards_actual_audit_to_calibration_without_mutating_raw(self):
        window = self.make_window()
        frame = self.frame()
        original_raw = frame.raw.copy()
        window.tare_raw = np.arange(24, dtype=float)
        window.host_tare_epoch = 9
        tracker = window.raw_baseline_tracker
        reference_epoch = window._raw_baseline_reference_epoch(frame.diagnostic)
        tracker.feed(window.tare_raw + 100., 4., 98.,
                     manual_tare24=window.tare_raw, epoch=reference_epoch)
        for index in range(12):
            tracker.feed(window.tare_raw + .5, 1., 98.1 + index*.1,
                         manual_tare24=window.tare_raw, epoch=reference_epoch)
        expected_offset = tracker.offset_raw24.copy()
        self.assertTrue(np.all(expected_offset != 0.))
        dialog = mock.Mock()
        dialog.session_started = True
        dialog.capturing = False  # Waiting between stable-point captures still logs trajectory.
        dialog.isVisible.return_value = True
        window.calibration_dialog = dialog
        with mock.patch.object(main.time, "time", return_value=100.):
            window._on_paired(frame)
        dialog.feed_frame.assert_called_once()
        positional, keyword = dialog.feed_frame.call_args
        self.assertIs(positional[0], frame)
        audit = keyword["audit_context"]
        self.assertEqual(audit["monitor_version"], main.MONITOR_VERSION)
        self.assertEqual(audit["frame_received_at_host"], frame.received_at)
        self.assertEqual(audit["firmware_id"], "h3_actual")
        self.assertEqual(audit["valid_mask"], 255)
        self.assertEqual(audit["acquisition_errors"], 17)
        self.assertEqual(audit["firmware_baseline_epoch"], 8)
        self.assertEqual(audit["host_tare_epoch"], 9)
        self.assertEqual(audit["effective_baseline_epoch"], tracker.baseline_epoch)
        self.assertEqual(audit["manual_reference_model_n"], window.manual_reference_model_n)
        self.assertEqual(audit["selected_force_n"], window.device_fz)
        self.assertEqual(audit["force_display_source"], window.force_display_source)
        self.assertEqual(audit["quality_flags"], list(window.quality_flags))
        np.testing.assert_array_equal(audit["manual_tare_raw24"], window.tare_raw)
        np.testing.assert_array_equal(audit["release_offset_raw24"], expected_offset)
        np.testing.assert_array_equal(audit["effective_tare_raw24"], window.tare_raw + expected_offset)
        np.testing.assert_array_equal(positional[1], window.tare_raw + expected_offset)
        np.testing.assert_array_equal(frame.raw, original_raw)
        # Audit values remain the original snapshots after runtime arrays change.
        window.tare_raw[:] = -1.
        tracker.offset_raw24[:] = 99.
        np.testing.assert_array_equal(audit["manual_tare_raw24"], np.arange(24))
        np.testing.assert_array_equal(audit["release_offset_raw24"], expected_offset)

    def test_missing_diagnostic_and_tare_stay_missing_not_fabricated_zeros(self):
        window = self.make_window()
        audit = window._calibration_audit_context(self.frame(with_diag=False))
        for name in ("manual_tare_raw24", "effective_tare_raw24", "firmware_id", "valid_mask",
                     "acquisition_errors", "firmware_baseline_epoch"):
            with self.subTest(field=name):
                self.assertIsNone(audit[name])
        self.assertEqual(audit["release_offset_raw24"], window.raw_baseline_tracker.offset_raw24.tolist())

    def test_entire_session_including_step_wait_and_gap_freezes_zero(self):
        window = self.make_window()
        window.tare_raw = np.arange(24, dtype=float)
        dialog = mock.Mock()
        dialog.session_started = True
        window.calibration_dialog = dialog
        for capturing, paused in ((True, False), (False, False), (False, True)):
            with self.subTest(capturing=capturing, paused=paused):
                dialog.capturing, dialog.stream_paused = capturing, paused
                self.assertFalse(window._raw_baseline_updates_allowed())
                epoch = window.host_tare_epoch
                tare, offset = window.tare_raw.copy(), window.raw_baseline_tracker.offset_raw24.copy()
                window._tare()
                window._on_release_zero_changed(False)
                self.assertEqual(window.host_tare_epoch, epoch)
                self.assertTrue(window.release_zero_enabled)
                np.testing.assert_array_equal(window.tare_raw, tare)
                np.testing.assert_array_equal(window.raw_baseline_tracker.offset_raw24, offset)
        dialog.session_started = False
        self.assertTrue(window._raw_baseline_updates_allowed())

    def test_monitor_and_real_dialog_write_full_sensor_trajectory_csv(self):
        window = self.make_window()
        frame = self.frame()
        window.tare_raw = np.arange(24, dtype=float)
        manual_tare = window.tare_raw.copy()
        window.host_tare_epoch = 4
        tracker = window.raw_baseline_tracker
        reference_epoch = window._raw_baseline_reference_epoch(frame.diagnostic)
        tracker.feed(manual_tare + 100., 4., 98., manual_tare24=manual_tare, epoch=reference_epoch)
        for index in range(12):
            tracker.feed(manual_tare + .5, 1., 98.1 + index*.1,
                         manual_tare24=manual_tare, epoch=reference_epoch)
        offset = tracker.offset_raw24.copy()
        effective_tare = manual_tare + offset
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch("calibration_acquisition.serial.tools.list_ports.comports", return_value=[]), \
             mock.patch.object(main.time, "time", return_value=100.) as clock:
            dialog = CalibrationAcquisitionDialog(window)
            window.calibration_dialog = dialog
            try:
                dialog.output_dir.setText(directory)
                dialog.device_sn.setText("MONITOR-TRACE-E2E")
                dialog._start_session()
                dialog.steps = [CaptureStep(1, "前", value, "加载", "力值/迟滞") for value in (15., 20.)]
                dialog._show_step()
                dialog.show()
                self.app.processEvents()
                self.assertTrue(dialog.session_started)
                self.assertTrue(dialog.isVisible())
                self.assertFalse(dialog.capturing)
                sensor_path = dialog.trajectory_writer.sensor_path
                window._on_paired(frame)
                self.assertIsNone(dialog.trajectory_error)
                dialog._begin_capture()
                for index in range(31):
                    now = 100.1 + index*.1
                    clock.return_value = now
                    diag = AcquisitionDiagnostic(2600 + index*100, now, "h3_actual", 255, 17, 8)
                    window._on_paired(PairedFrame(diag.device_ms, now, frame.raw.copy(), frame.force.copy(), diag))
                self.assertFalse(dialog.capturing)
                self.assertEqual(dialog.step_index, 1)
                clock.return_value = 103.3
                diag = AcquisitionDiagnostic(5800, 103.3, "h3_actual", 255, 17, 8)
                window._on_paired(PairedFrame(diag.device_ms, diag.received_at, frame.raw.copy(), frame.force.copy(), diag))
                dialog._finish_session(stopped=True)
                self.assertTrue(dialog.trajectory_writer.closed)
                self.assertIsNone(dialog.trajectory_error)
                dialog.close()
                with sensor_path.open(newline="", encoding="utf-8-sig") as source:
                    rows = list(csv.DictReader(source))
                self.assertEqual(len(rows), 33)
                self.assertEqual(rows[0]["trajectory_phase"], "between_steps")
                self.assertEqual(rows[1]["trajectory_phase"], "stable_capture")
                self.assertEqual(rows[-1]["trajectory_phase"], "between_steps")
                self.assertEqual(rows[-1]["step_index"], "2")
                for row in rows:
                    self.assertEqual(row["reference_force_n"], "")
                    self.assertEqual(row["reference_provenance"], "manual_plateau_reading_not_continuous_reference")
                    np.testing.assert_allclose([float(row[column]) for column in RAW_COLUMNS], frame.raw)
                    np.testing.assert_allclose([float(row[column]) for column in DELTA_COLUMNS], frame.raw-effective_tare)
                    audit = json.loads(row["audit_context_json"])
                    self.assertEqual(audit["monitor_version"], main.MONITOR_VERSION)
                    self.assertEqual(audit["firmware_id"], "h3_actual")
                    self.assertEqual(audit["acquisition_errors"], 17)
                    self.assertEqual(audit["host_tare_epoch"], 4)
                    np.testing.assert_allclose(json.loads(row["manual_tare_raw_json"]), manual_tare)
                    np.testing.assert_allclose(json.loads(row["effective_tare_raw_json"]), effective_tare)
                    np.testing.assert_allclose(json.loads(row["residual_offset_raw_json"]), offset)
                    np.testing.assert_allclose(audit["effective_tare_raw24"], effective_tare)
            finally:
                if dialog.session_started:
                    dialog._finish_session(stopped=True)
                dialog.close()
                window.calibration_dialog = None

    def test_interrupt_preserves_specific_trajectory_writer_failure_message(self):
        window = self.make_window()
        dialog = mock.Mock()
        dialog.session_started = True
        dialog.trajectory_error = None
        dialog.step_instruction = QtWidgets.QLabel("before")
        failure_message = "连续轨迹写入失败：磁盘空间不足，采集已停止"
        def fail_finish(*, stopped):
            dialog.session_started = False
            dialog.trajectory_error = "disk full"
            dialog.step_instruction.setText(failure_message)
        dialog._finish_session.side_effect = fail_finish
        window.calibration_dialog = dialog
        window._interrupt_protocol()
        dialog._finish_session.assert_called_once_with(stopped=True)
        self.assertEqual(dialog.step_instruction.text(), failure_message)


if __name__ == "__main__":
    unittest.main()
