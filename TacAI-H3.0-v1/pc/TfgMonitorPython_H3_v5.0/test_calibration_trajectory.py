"""Continuous calibration evidence must include transitions and honest references."""

import csv
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5 import QtTest, QtWidgets

from calibration_acquisition import CalibrationAcquisitionDialog, CaptureStep, DELTA_COLUMNS


class CalibrationTrajectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.ports = patch("calibration_acquisition.serial.tools.list_ports.comports", return_value=[])
        self.ports.start()
        self.clock = patch("calibration_acquisition.time.time", return_value=100.0)
        self.now = self.clock.start()
        self.dialog = CalibrationAcquisitionDialog()
        self.dialog.device_sn.setText("TRAJECTORY-TEST")
        self.dialog.output_dir.setText(self.folder.name)

    def tearDown(self):
        self.dialog.capturing = False
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()
        self.clock.stop()
        self.ports.stop()
        self.folder.cleanup()

    def start(self, *, gauge=False):
        self.dialog.reference_mode.setCurrentIndex(int(gauge))
        self.dialog._start_session()
        self.dialog.steps = [CaptureStep(1, "前", force, "加载", "力值/迟滞") for force in (0.0, 15.0, 0.0)]
        self.dialog._show_step()
        self.assertTrue(self.dialog.session_started)
        return self.dialog.trajectory_writer

    def feed(self, received=100.0, ms=1, *, tare=True, context=None):
        self.now.return_value = received
        frame = SimpleNamespace(raw=np.arange(24, dtype=float) + 100.0,
                                force=np.array([0.1, 0.2, 15.0]),
                                received_at=received, device_ms=ms)
        self.dialog.feed_frame(
            frame, np.arange(24, dtype=float) if tare else None, 14.8, 0.9,
            0.8, False, 14.9, 14.9, 15.0, 0.3, "LOADED", audit_context=context,
        )

    def capture(self, start=100.0, ms=1):
        self.now.return_value = start
        self.dialog._begin_capture()
        for index in range(31):
            self.feed(start + index / 10, ms + index)

    @staticmethod
    def rows(path):
        with Path(path).open(newline="", encoding="utf-8-sig") as source:
            return list(csv.DictReader(source))

    def flush(self):
        self.dialog._flush_trajectory()

    def test_between_step_frames_preserve_raw_and_effective_zero_without_fake_manual_truth(self):
        writer = self.start()
        context = {"manual_tare_raw24": np.arange(24), "effective_tare_raw24": np.arange(24),
                   "release_offset_raw24": np.zeros(24), "host_tare_epoch": 7,
                   "effective_baseline_epoch": 2, "selected_force_n": 14.7,
                   "firmware_id": "v5", "firmware_baseline_epoch": 1}
        self.dialog.manual_force.setValue(15.1234)
        self.feed(100.0, 1, context=context)
        self.feed(100.1, 2, context=context)
        self.dialog._finish_session(stopped=True)
        rows = self.rows(writer.sensor_path)
        self.assertEqual(len(rows), 2)
        self.assertFalse(self.rows(self.dialog.detail_path))
        self.assertFalse(self.rows(self.dialog.summary_path))
        self.assertEqual(rows[0]["trajectory_phase"], "between_steps")
        self.assertEqual(rows[0]["reference_force_n"], "")
        self.assertEqual(rows[0]["manual_plateau_reading_n"], "15.1234")
        self.assertIn("not_continuous", rows[0]["reference_provenance"])
        self.assertEqual(rows[0]["S1_dBx"], "100.0")
        self.assertEqual(rows[0]["S8_retared_dBz"], "100.0")
        self.assertEqual(json.loads(rows[0]["manual_tare_raw_json"]), list(range(24)))
        self.assertEqual(json.loads(rows[0]["audit_context_json"])["selected_force_n"], 14.7)
        self.assertEqual(rows[0]["effective_baseline_epoch"], "2")
        self.assertEqual(rows[0]["record_type"], "CALIBRATION_TRAJECTORY_SENSOR")
        self.assertEqual(rows[0]["schema_version"], "1")
        self.assertEqual(float(rows[0]["host_received_at_unix_s"]), 100.0)
        self.assertGreater(float(rows[0]["host_written_monotonic_s"]), 0)
        self.assertFalse(writer.sensor_path.name.startswith("H3_calibration_"))
        self.assertFalse(writer.sensor_path.name.endswith("_frames.csv"))
        self.assertTrue(writer.closed)

    def test_three_second_csv_format_and_manual_point_reading_are_preserved(self):
        writer = self.start()
        self.dialog.manual_force.setValue(0.0123)
        self.capture()
        self.flush()
        plateau = self.rows(self.dialog.detail_path)
        summary = self.rows(self.dialog.summary_path)
        trajectory = self.rows(writer.sensor_path)
        self.assertEqual(len(plateau), 31)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["reference_median_n"], "0.0123")
        self.assertTrue(all(row["reference_mode"] == "manual" for row in plateau))
        self.assertNotIn("trajectory_session_id", plateau[0])
        self.assertEqual(len(trajectory), 31)
        self.assertTrue(all(row["reference_force_n"] == "" for row in trajectory))

    def test_gauge_asof_uses_nearest_past_when_newer_event_is_already_queued(self):
        writer = self.start(gauge=True)
        self.dialog.gauge_port.addItem("COM17")
        self.dialog.gauge_port.setCurrentText("COM17")
        self.dialog._on_gauge(14.9, "ST,NT,+0014.9 N", 99.8)
        self.dialog._on_gauge(15.1, "ST,NT,+0015.1 N", 100.1)
        self.feed(100.0, 1)
        self.flush()
        row = self.rows(writer.sensor_path)[0]
        self.assertEqual(row["reference_force_n"], "14.9")
        self.assertAlmostEqual(float(row["gauge_age_s"]), 0.2)
        self.assertEqual(row["gauge_raw"], "ST,NT,+0014.9 N")
        self.assertEqual(float(row["gauge_host_received_at_unix_s"]), 99.8)
        self.assertEqual(json.loads(row["gauge_event_json"])["received_at"], 99.8)
        gauges = self.rows(writer.gauge_path)
        self.assertEqual([row["raw_text"] for row in gauges], ["ST,NT,+0014.9 N", "ST,NT,+0015.1 N"])
        self.assertEqual(gauges[0]["gauge_port"], "COM17")
        self.assertEqual(gauges[0]["scale_to_n"], "1.0")
        self.assertIn("not_hardware_synchronized", gauges[0]["clock_alignment"])

    def test_missing_future_stale_gauge_remains_blank_and_real_zero_is_kept(self):
        writer = self.start(gauge=True)
        self.feed(100.0, 1)
        self.dialog._on_gauge(15.0, "15.0 N", 100.2)
        self.feed(100.1, 2)
        self.feed(101.3, 3)
        self.dialog._on_gauge(0.0, "0.0 N", 101.4)
        self.feed(101.5, 4)
        self.flush()
        rows = self.rows(writer.sensor_path)
        self.assertEqual([row["reference_force_n"] for row in rows], ["", "", "", "0.0"])
        self.assertEqual([row["reference_provenance"] for row in rows[:3]], [
            "gauge_missing", "gauge_future_event_not_used", "gauge_stale_event_not_used",
        ])
        self.assertEqual(rows[3]["manual_plateau_reading_n"], "")

    def test_gauge_expiring_mid_window_cancels_only_current_capture_without_false_summary(self):
        writer = self.start(gauge=True)
        self.dialog._on_gauge(15.0, "15.0 N", 99.9)
        self.now.return_value = 100.0
        self.dialog._begin_capture()
        self.feed(100.0, 1)
        self.feed(100.5, 2)
        self.assertTrue(self.dialog.capturing)
        self.feed(101.0, 3)
        self.assertFalse(self.dialog.capturing)
        self.assertFalse(self.dialog.stream_paused)
        self.assertTrue(self.dialog.session_started)
        self.assertEqual(self.dialog.capture_rows, [])
        self.assertEqual(self.dialog.step_index, 0)
        self.assertIn("测力计参考", self.dialog.step_instruction.text())
        self.assertTrue(self.dialog.capture_button.isEnabled())
        self.assertFalse(self.rows(self.dialog.summary_path))
        self.assertFalse(self.rows(self.dialog.detail_path))
        self.flush()
        self.assertEqual(len(self.rows(writer.sensor_path)), 3)
        events = self.rows(writer.events_path)
        cancelled = next(row for row in events if row["event"] == "capture_cancel")
        self.assertEqual(json.loads(cancelled["details_json"])["reason"], "gauge_stale_event_not_used")

    def test_programmatic_reference_switch_cancels_window_instead_of_mixing_sources(self):
        self.start()
        self.dialog._begin_capture()
        self.feed(100.0, 1)
        self.dialog.reference_mode.setCurrentIndex(1)
        self.feed(100.1, 2)
        self.assertFalse(self.dialog.capturing)
        self.assertTrue(self.dialog.session_started)
        self.assertFalse(self.rows(self.dialog.summary_path))

    def test_no_tare_does_not_fabricate_retared_channels_or_zero_epochs(self):
        writer = self.start()
        self.feed(tare=False)
        self.flush()
        row = self.rows(writer.sensor_path)[0]
        self.assertEqual(row["host_tare_valid"], "0")
        self.assertTrue(all(row[name] == "" for name in DELTA_COLUMNS))
        self.assertEqual(row["effective_tare_raw_json"], "null")
        self.assertEqual(row["host_tare_epoch"], "")
        self.assertEqual(row["effective_baseline_epoch"], "")

    def test_gap_keeps_full_trajectory_but_never_combines_partial_point_windows(self):
        writer = self.start()
        self.feed(99.9, 1)
        self.now.return_value = 100.0
        self.dialog._begin_capture()
        self.feed(100.0, 2)
        self.feed(101.0, 3)
        self.dialog.pause_for_stream_gap("old sequence")
        self.dialog.resume_after_stream_gap()
        self.feed(102.0, 4)
        self.assertFalse(self.dialog.capturing)
        self.capture(110.0, 100)
        self.dialog._finish_session(stopped=True)
        self.assertEqual(len(self.rows(self.dialog.detail_path)), 31)
        self.assertEqual(len(self.rows(self.dialog.summary_path)), 1)
        trajectory = self.rows(writer.sensor_path)
        self.assertEqual(len(trajectory), 35)
        self.assertEqual([int(row["device_ms"]) for row in trajectory[:4]], [1, 2, 3, 4])
        events = [row["event"] for row in self.rows(writer.events_path)]
        for event in ("session_start", "capture_start", "stream_pause", "stream_resume", "capture_complete", "session_stop"):
            self.assertIn(event, events)

    def test_sensor_write_failure_stops_explicitly_without_success_message(self):
        writer = self.start()
        with patch.object(writer, "sensor", side_effect=OSError("disk full")):
            self.feed()
        self.assertFalse(self.dialog.session_started)
        self.assertTrue(writer.closed)
        self.assertIn("写盘失败", self.dialog.step_title.text())
        self.assertIn("保存可能不完整", self.dialog.step_instruction.text())
        self.assertNotIn("采集结束；已有数据均已写入磁盘", self.dialog.log.toPlainText())
        self.assertFalse(self.dialog.trajectory_flush_timer.isActive())

    def test_periodic_flush_failure_stops_and_does_not_report_all_data_saved(self):
        writer = self.start()
        with patch.object(writer, "flush", side_effect=OSError("flush denied")):
            self.dialog._flush_trajectory()
        self.assertFalse(self.dialog.session_started)
        self.assertTrue(writer.closed)
        self.assertIn("flush denied", self.dialog.log.toPlainText())
        self.assertNotIn("采集结束；已有数据均已写入磁盘", self.dialog.log.toPlainText())

    def test_plateau_summary_write_failure_is_not_silently_successful(self):
        self.start()
        original_open = Path.open
        def fail_summary(path, mode="r", *args, **kwargs):
            if path == self.dialog.summary_path and mode == "a":
                raise OSError("summary permission denied")
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, "open", new=fail_summary):
            self.capture()
        self.assertFalse(self.dialog.session_started)
        self.assertIn("写盘失败", self.dialog.step_title.text())
        self.assertIn("summary permission denied", self.dialog.log.toPlainText())
        self.assertNotIn("已保存 前", self.dialog.log.toPlainText())

    def test_timer_flushes_last_frame_without_needing_another_input(self):
        writer = self.start()
        self.assertLessEqual(self.dialog.trajectory_flush_timer.interval(), 1000)
        self.feed()
        QtTest.QTest.qWait(1150)
        self.assertEqual(len(self.rows(writer.sensor_path)), 1)

    def test_restart_uses_new_names_and_preserves_previous_files(self):
        first = self.start()
        self.feed()
        self.dialog._finish_session(stopped=True)
        old_files = [first.sensor_path, first.gauge_path, first.events_path, self.dialog.detail_path, self.dialog.summary_path]
        original = {path: path.read_bytes() for path in old_files}
        second = self.start()
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertNotEqual(first.sensor_path, second.sensor_path)
        self.assertNotIn(self.dialog.detail_path, old_files)
        self.assertTrue(all(path.read_bytes() == contents for path, contents in original.items()))

    def test_close_paused_session_closes_streams_and_retains_events(self):
        writer = self.start()
        self.feed()
        self.dialog.pause_for_stream_gap("recovering")
        self.dialog.close()
        self.assertTrue(writer.closed)
        self.assertFalse(self.dialog.session_started)
        self.assertEqual([row["event"] for row in self.rows(writer.events_path)][-1], "session_stop")
        self.assertEqual(len(self.rows(writer.sensor_path)), 1)

    def test_nonfinite_gauge_is_audited_without_becoming_a_force_reference(self):
        writer = self.start(gauge=True)
        self.dialog._on_gauge(float("inf"), "1e308 kg", 99.9)
        self.feed()
        self.flush()
        self.assertEqual(self.rows(writer.sensor_path)[0]["reference_force_n"], "")
        gauge = self.rows(writer.gauge_path)[0]
        self.assertEqual(gauge["is_finite"], "0")
        self.assertEqual(gauge["force_n"], "")
        self.assertEqual(gauge["raw_text"], "1e308 kg")


if __name__ == "__main__":
    unittest.main()
