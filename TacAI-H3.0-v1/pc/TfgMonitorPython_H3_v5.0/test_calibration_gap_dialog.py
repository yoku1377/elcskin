"""Recoverable transport gaps must not finish or contaminate calibration."""

import csv
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5 import QtWidgets

from calibration_acquisition import CalibrationAcquisitionDialog, CaptureStep


class CalibrationGapDialogTests(unittest.TestCase):
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
        self.dialog.device_sn.setText("GAP-TEST")
        self.dialog.output_dir.setText(self.folder.name)

    def tearDown(self):
        self.dialog.capturing = False
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()
        self.clock.stop()
        self.ports.stop()
        self.folder.cleanup()

    def start(self):
        self.dialog._start_session()
        self.dialog.steps = [CaptureStep(1, "前", force, "加载", "力值/迟滞") for force in (10.0, 15.0, 20.0)]
        self.dialog._show_step()

    def frame(self, received_at, device_ms, value=15.0):
        self.now.return_value = received_at
        frame = SimpleNamespace(
            raw=np.full(24, value), force=np.array([0.0, 0.0, value]),
            received_at=received_at, device_ms=device_ms,
        )
        self.dialog.feed_frame(frame, np.zeros(24), value, 1.0, 1.0, False, value, value, value, 1.0, "OK")

    def capture(self, started_at, device_ms=0, value=15.0):
        self.now.return_value = started_at
        self.dialog._begin_capture()
        for index in range(31):
            self.frame(started_at + index / 10, device_ms + index, value)

    @staticmethod
    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def rows(path):
        with Path(path).open(newline="", encoding="utf-8-sig") as source:
            return list(csv.DictReader(source))

    def test_15n_gap_discards_only_partial_window_and_manual_recapture_appends_once(self):
        self.start()
        self.capture(100.0, value=10.0)
        self.assertEqual(self.dialog.step_index, 1)
        detail, summary = self.dialog.detail_path, self.dialog.summary_path
        detail_before, summary_before = self.digest(detail), self.digest(summary)
        steps_before = list(self.dialog.steps)
        self.now.return_value = 110.0
        self.dialog._begin_capture()
        for index in range(11):
            self.frame(110.0 + index / 10, 1000 + index)
        self.assertEqual(len(self.dialog.capture_rows), 11)

        self.dialog.pause_for_stream_gap("old seq=6774")
        self.assertTrue(self.dialog.session_started)
        self.assertTrue(self.dialog.stream_paused)
        self.assertFalse(self.dialog.capturing)
        self.assertIsNone(self.dialog.capture_started_at)
        self.assertEqual(self.dialog.capture_rows, [])
        self.assertEqual(self.dialog.step_index, 1)
        self.assertEqual(self.dialog.steps, steps_before)
        self.assertEqual((self.dialog.detail_path, self.dialog.summary_path), (detail, summary))
        self.assertEqual(self.digest(detail), detail_before)
        self.assertEqual(self.digest(summary), summary_before)
        self.assertEqual(self.dialog.progress.value(), 0)
        self.assertTrue(self.dialog.stop_button.isEnabled())
        for button in (self.dialog.capture_button, self.dialog.previous_button, self.dialog.skip_button):
            self.assertFalse(button.isEnabled())

        self.dialog._begin_capture()
        self.dialog._previous()
        self.dialog._skip()
        self.dialog._complete_capture()
        self.frame(115.0, 1999)
        self.dialog.pause_for_stream_gap("retry still pending")
        self.assertEqual(self.dialog.capture_rows, [])
        self.assertEqual(self.dialog.step_index, 1)
        self.assertEqual(self.digest(detail), detail_before)
        self.assertEqual(self.digest(summary), summary_before)

        self.dialog.manual_force.setValue(15.1234)
        self.dialog.resume_after_stream_gap()
        self.assertFalse(self.dialog.stream_paused)
        self.assertFalse(self.dialog.capturing)
        self.assertEqual(self.dialog.step_index, 1)
        self.assertAlmostEqual(self.dialog.manual_force.value(), 15.1234)
        self.assertIn("重新采样完整3秒", self.dialog.step_instruction.text())
        self.frame(116.0, 2000)
        self.assertEqual(self.dialog.capture_rows, [])
        self.assertEqual(self.digest(detail), detail_before)
        self.assertEqual(self.digest(summary), summary_before)
        self.capture(120.0, device_ms=3000)
        self.assertEqual(self.dialog.step_index, 2)
        summaries = self.rows(summary)
        details = self.rows(detail)
        self.assertEqual([row["target_n"] for row in summaries], ["10.0", "15.0"])
        self.assertEqual(len(details), 62)
        self.assertEqual(len(summaries), 2)
        self.assertEqual(summaries[1]["sample_count"], "31")
        self.assertEqual([int(row["device_ms"]) for row in details[31:]], list(range(3000, 3031)))
        self.assertEqual(self.rows(summary)[0]["target_n"], "10.0")

    def test_idle_active_step_pauses_without_losing_session_or_writing_files(self):
        self.start()
        paths = (self.dialog.detail_path, self.dialog.summary_path)
        hashes = tuple(self.digest(path) for path in paths)
        self.dialog.pause_for_stream_gap("idle gap")
        self.dialog.pause_for_stream_gap("same gap")
        self.assertTrue(self.dialog.session_started)
        self.assertTrue(self.dialog.stream_paused)
        self.assertEqual(self.dialog.step_index, 0)
        self.assertEqual(tuple(self.digest(path) for path in paths), hashes)
        self.dialog.resume_after_stream_gap()
        self.dialog.resume_after_stream_gap()
        self.assertTrue(self.dialog.capture_button.isEnabled())
        self.assertFalse(self.dialog.capturing)
        self.assertEqual(tuple(self.digest(path) for path in paths), hashes)

    def test_gap_before_session_has_no_effect(self):
        title = self.dialog.step_title.text()
        self.dialog.pause_for_stream_gap("no session")
        self.dialog.resume_after_stream_gap()
        self.assertFalse(self.dialog.stream_paused)
        self.assertFalse(self.dialog.session_started)
        self.assertIsNone(self.dialog.detail_path)
        self.assertEqual(self.dialog.step_title.text(), title)

    def test_stop_clears_paused_state_and_does_not_report_100_percent(self):
        self.start()
        self.capture(100.0, value=10.0)
        self.now.return_value = 110.0
        self.dialog._begin_capture()
        self.frame(110.2, 2000)
        self.dialog.pause_for_stream_gap("pending")
        self.dialog._finish_session(stopped=True)
        self.assertFalse(self.dialog.session_started)
        self.assertFalse(self.dialog.stream_paused)
        self.assertFalse(self.dialog.capturing)
        self.assertIsNone(self.dialog.capture_started_at)
        self.assertEqual(self.dialog.capture_rows, [])
        self.assertEqual(self.dialog.progress.value(), 333)
        self.assertEqual(len(self.rows(self.dialog.summary_path)), 1)
        self.dialog.resume_after_stream_gap()
        self.assertFalse(self.dialog.capture_button.isEnabled())
        self.dialog._start_session()
        self.assertFalse(self.dialog.stream_paused)
        self.assertTrue(self.dialog.session_started)

    def test_only_completed_plan_reports_100_percent(self):
        self.start()
        self.dialog.steps = self.dialog.steps[:1]
        self.capture(100.0, value=10.0)
        self.assertFalse(self.dialog.session_started)
        self.assertEqual(self.dialog.progress.value(), 1000)
        self.assertIn("全部采集完成", self.dialog.step_title.text())

    def test_closing_paused_session_cannot_be_revived_by_queued_recovery(self):
        self.start()
        self.dialog.pause_for_stream_gap("pending recovery")
        paths=(self.dialog.detail_path,self.dialog.summary_path)
        hashes=tuple(self.digest(path) for path in paths)
        event=Mock()
        self.dialog.closeEvent(event)
        event.accept.assert_called_once()
        self.assertFalse(self.dialog.session_started)
        self.assertFalse(self.dialog.stream_paused)
        self.dialog.resume_after_stream_gap()
        self.assertFalse(self.dialog.capture_button.isEnabled())
        self.assertFalse(self.dialog.session_started)
        self.assertEqual(tuple(self.digest(path) for path in paths),hashes)


if __name__ == "__main__":
    unittest.main()
