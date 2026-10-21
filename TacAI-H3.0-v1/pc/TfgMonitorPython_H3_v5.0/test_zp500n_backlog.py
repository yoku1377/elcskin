"""Field-derived ZP stream framing must not relabel queued values as fresh."""

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
from PyQt5 import QtWidgets

from calibration_acquisition import CalibrationAcquisitionDialog, GaugeReader
from test_zp500n_gauge import observed_metadata, run_fake_reader


class ZP500NBacklogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def exercise(self, tail):
        reader = GaugeReader("COM13", 2400, 999.0, protocol="zp500n")
        return run_fake_reader(reader, [(0.02, observed_metadata() * 3), (0.09, b"")] + tail)

    def test_long_reader_pause_preserves_values_as_audit_and_waits_for_new_frame(self):
        port, values, contexts, events, statuses, _ = self.exercise([
            (0.2, b"0016.1"), (0.3, b"0017."),
            (1.0, b"1" + b"0020.0" * 3), (1.01, b""), (1.1, b"0021.0"),
        ])
        self.assertEqual([value for value, _, _ in values], [16.1, 21.0], statuses)
        self.assertEqual(port.writes, [b"\xfa", b"\x25"])
        self.assertEqual(len(port.invalidations), 1)
        self.assertEqual(port.invalidations[0]["reason"], "read_loop_pause")
        suppressed = [event for event in events if event["event"] == "zp500n_sample_suppressed"]
        self.assertEqual([event["force_n"] for event in suppressed], [17.1, 20.0, 20.0, 20.0])
        self.assertTrue(all(event["host_received_at"] == 1001.05 for event in suppressed))
        self.assertTrue(all(event["reference_usable"] is False for event in suppressed))
        self.assertEqual([context["raw_text"] for context in contexts], ["0016.1", "0021.0"])
        self.assertTrue(all(context["reference_usable"] for context in contexts))
        invalidated_index = next(i for i, item in enumerate(port.signal_order) if item[0] == "invalidated")
        paused_raw_index = next(i for i, item in enumerate(port.signal_order)
                                if item[0] == "protocol" and item[1].get("event") == "zp500n_raw_receive"
                                and item[1].get("read_loop_gap_s", 0) > 0.3)
        self.assertLess(invalidated_index, paused_raw_index)
        self.assertTrue(all(1 <= size <= 256 for size in port.read_sizes))
        self.assertIn(1, port.read_sizes)

    def test_multiple_frames_without_large_pause_are_not_current_reference(self):
        port, values, _contexts, events, _statuses, _ = self.exercise([
            (0.2, b"0016.1"), (0.3, b"0017.10018.1"), (0.31, b""), (0.4, b"0019.1"),
        ])
        self.assertEqual([value for value, _, _ in values], [16.1, 19.1])
        self.assertEqual(len(port.invalidations), 1)
        self.assertLess(port.invalidations[0]["read_loop_gap_s"], 0.3)
        self.assertEqual([event["force_n"] for event in events if event["event"] == "zp500n_sample_suppressed"],
                         [17.1, 18.1])

    def test_partial_queued_frame_survives_empty_read_without_becoming_fresh(self):
        port, values, _contexts, events, statuses, _ = self.exercise([
            (0.2, b"0016.1"), (0.7, b"0018.1002"), (0.71, b""),
            (0.8, b"0.0"), (0.81, b""), (0.9, b"0021.0"),
        ])
        self.assertEqual([value for value, _, _ in values], [16.1, 21.0], statuses)
        self.assertEqual([event["force_n"] for event in events if event["event"] == "zp500n_sample_suppressed"],
                         [18.1, 20.0])
        drained = [event for event in events if event["event"] == "zp500n_backlog_drained"]
        self.assertEqual(len(drained), 1)
        self.assertAlmostEqual(drained[0]["host_received_at"], 1000.86)
        self.assertTrue(port.closed)

    def test_pause_with_empty_queue_still_invalidates_old_reference(self):
        port, values, _contexts, events, _statuses, _ = self.exercise([
            (0.2, b"0016.1"), (0.7, b""), (0.8, b"0017.1"),
        ])
        self.assertEqual([value for value, _, _ in values], [16.1, 17.1])
        self.assertEqual(len(port.invalidations), 1)
        self.assertTrue(any(event["event"] == "zp500n_backlog_drained" for event in events))

    def test_prefa_stream_is_preserved_but_not_emitted_without_confirmed_units(self):
        reader = GaugeReader("COM13", 2400, 1.0, protocol="zp500n")
        port, values, _contexts, events, _statuses, _ = run_fake_reader(reader, [
            (0.0, b"0016.10016.1"), (0.02, observed_metadata() * 3),
            (0.09, b""), (0.2, b"0016.1"),
        ])
        prefa = [event for event in events if event.get("phase") == "PRE_FA"]
        self.assertEqual([event["raw_text"] for event in prefa], ["0016.10016.1"])
        self.assertEqual([value for value, _, _ in values], [16.1])
        self.assertEqual(port.writes, [b"\xfa", b"\x25"])

    def test_dialog_backlog_cancels_only_current_window_and_records_rejected_reference(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch("calibration_acquisition.serial.tools.list_ports.comports", return_value=[]):
            dialog = CalibrationAcquisitionDialog()
            dialog.device_sn.setText("ZP-BACKLOG")
            dialog.output_dir.setText(folder)
            dialog.reference_mode.setCurrentIndex(1)
            dialog.gauge_protocol.setCurrentIndex(1)
            dialog._start_session()
            writer = dialog.trajectory_writer
            reader = GaugeReader("COM13", 2400, 1.0, protocol="zp500n")
            dialog.gauge_reader = reader
            reader.sample_context.connect(dialog._on_gauge_sample_context)
            reader.measurement.connect(dialog._on_gauge)
            reader.protocol_event.connect(dialog._on_gauge_protocol_event)
            reader.reference_invalidated.connect(dialog._on_gauge_reference_invalidated)
            capture_started = []

            def begin_first_capture(_value, _raw, received):
                if capture_started:
                    return
                capture_started.append(received)
                dialog._begin_capture()
                dialog.feed_frame(SimpleNamespace(raw=np.zeros(24), force=np.zeros(3),
                                                  received_at=received, device_ms=1),
                                  np.zeros(24), 0.0, 1.0)
                self.assertEqual(len(dialog.capture_rows), 1)

            reader.measurement.connect(begin_first_capture)
            run_fake_reader(reader, [
                (0.02, observed_metadata() * 3), (0.09, b""), (0.2, b"0016.1"),
                (0.7, b"0017.10018.1"), (0.71, b""), (0.8, b"0019.1"),
            ])
            self.assertTrue(dialog.session_started)
            self.assertFalse(dialog.capturing)
            self.assertEqual(dialog.step_index, 0)
            self.assertFalse(dialog.capture_rows)
            self.assertEqual([sample["value_n"] for sample in dialog.gauge_history], [19.1])
            self.assertIn("重新采集", dialog.step_instruction.text())
            dialog._finish_session(stopped=True)
            dialog.gauge_reader = None
            dialog.close()
            dialog.deleteLater()
            with Path(dialog.summary_path).open(newline="", encoding="utf-8-sig") as source:
                self.assertFalse(list(csv.DictReader(source)))
            with writer.events_path.open(newline="", encoding="utf-8-sig") as source:
                records = list(csv.DictReader(source))
            self.assertTrue(any(row["event"] == "capture_cancel" for row in records))
            details = [json.loads(row["details_json"]) for row in records if row["event"] == "gauge_protocol"]
            self.assertTrue(any(event["event"] == "zp500n_raw_receive" for event in details))
            self.assertTrue(any(event["event"] == "zp500n_sample_suppressed" for event in details))


if __name__ == "__main__":
    unittest.main()
