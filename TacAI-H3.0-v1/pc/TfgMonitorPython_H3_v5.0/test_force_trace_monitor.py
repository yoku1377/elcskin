"""Monitor diagnostics retain pre-zero and held-load measurement evidence."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtWidgets

import main
from h3_v3_3_force_model import REGION_PROTOTYPES
from no8_raw_tare_core import PairedFrame, evaluate_raw_force


class ForceTraceMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "force_trace"
        self.environment = mock.patch.dict(os.environ, TACAI_FORCE_TRACE_DIR=str(self.root))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.now = 1700000000.
        self.device_ms = 0
        self.clock = mock.patch("main.time.time", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.window = main.Monitor()
        self.window.timeout_timer.stop()
        self.window.reader = None
        self.addCleanup(self.window.close)

    def feed(self, raw, count=1, *, interval=.1, firmware_force=None):
        for _ in range(count):
            self.now += interval
            self.device_ms += round(interval * 1000)
            frame = PairedFrame(self.device_ms, self.now, np.asarray(raw, dtype=float).copy(),
                                np.zeros(3) if firmware_force is None else np.asarray(firmware_force, float))
            self.window._on_paired(frame)
        return frame

    def prime_tare(self):
        self.feed(np.zeros(24), 21)
        self.window._tare()
        self.assertIsNotNone(self.window.tare_raw)
        return self.window.tare_raw.copy()

    def traces(self):
        return [[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                for path in sorted(self.root.glob("*.jsonl"))]

    def test_first_manual_tare_saves_received_samples_before_new_zero(self):
        raw = np.full(24, 20.)
        self.feed(raw, 21)
        self.assertEqual(len(self.window.force_trace), 21)
        self.window._tare()
        traces = self.traces()
        self.assertEqual(len(traces), 1)
        header, *samples = traces[0]
        self.assertEqual(header["reason"], "BEFORE_MANUAL_TARE")
        self.assertFalse(header["metadata"]["automatic_zero"])
        self.assertFalse(header["metadata"]["physical_unload_inferred"])
        self.assertEqual(header["count"], 21)
        self.assertTrue(all(sample["tare_raw24"] is None for sample in samples))
        self.assertTrue(all(sample["delta_raw24"] is None for sample in samples))
        self.assertTrue(all(sample["host_tare_epoch"] == 0 for sample in samples))
        self.assertEqual(samples[-1]["raw24"], raw.tolist())
        self.assertEqual(len(self.window.force_trace), 0)
        np.testing.assert_array_equal(self.window.tare_raw, raw)
        self.assertEqual(self.window.device_fz, 0.)

    def test_long_constant_response_keeps_real_force_raw_and_existing_zero(self):
        tare = self.prime_tare()
        epoch = self.window.host_tare_epoch
        raw = tare + REGION_PROTOTYPES[2] * 10000.
        expected = evaluate_raw_force(raw, tare).fz_n
        self.assertGreater(expected, .05)
        self.feed(raw, 180, interval=.2)  # Thirty-six seconds cannot trigger an automatic zero.
        self.assertAlmostEqual(self.window.device_fz, expected)
        np.testing.assert_array_equal(self.window.tare_raw, tare)
        self.assertEqual(self.window.host_tare_epoch, epoch)
        samples = list(self.window.force_trace.frames)
        self.assertEqual(len(samples), 180)
        for sample in samples:
            self.assertAlmostEqual(sample["selected_force_n"], expected)
            self.assertAlmostEqual(sample["original_model_force_n"], expected)
            self.assertEqual(sample["raw24"], raw.tolist())
            self.assertEqual(sample["tare_raw24"], tare.tolist())
        self.assertEqual(len(self.traces()), 1)  # No pressure-dependent trace flush or zero.

    def test_next_successful_manual_tare_saves_residual_in_old_coordinates(self):
        old_tare = self.prime_tare()
        old_epoch = self.window.host_tare_epoch
        raw = old_tare + REGION_PROTOTYPES[2] * 10000.
        frame = self.feed(raw, 31)
        old_force = self.window.device_fz
        self.assertGreater(old_force, .05)
        record = self.window._record_base("PAIRED", frame.device_ms, frame.received_at)
        self.window.records = [record]
        original_record = copy.deepcopy(record)
        self.window._tare()
        traces = self.traces()
        self.assertEqual(len(traces), 2)
        header, *samples = traces[-1]
        self.assertEqual(header["reason"], "BEFORE_MANUAL_TARE")
        self.assertGreater(len(samples), 20)
        for sample in samples:
            self.assertAlmostEqual(sample["selected_force_n"], old_force)
            self.assertEqual(sample["host_tare_epoch"], old_epoch)
            self.assertEqual(sample["tare_raw24"], old_tare.tolist())
            self.assertEqual(sample["raw24"], raw.tolist())
            np.testing.assert_allclose(sample["delta_raw24"], raw - old_tare)
        self.assertEqual(self.window.host_tare_epoch, old_epoch + 1)
        np.testing.assert_array_equal(self.window.tare_raw, raw)
        self.assertEqual(self.window.device_fz, 0.)
        self.assertEqual(self.window.records[0], original_record)
        event = dict(zip(self.window._csv_header(), self.window.records[-1]))
        self.assertEqual(event["record_type"], "TARE_CHANGED")
        # A coordinate-change event is not a new accepted measurement.  Its
        # empty numeric fields must not overwrite the prior physical sample.
        self.assertEqual(event["S1_raw_dBx"], "")
        self.assertEqual(event["S1_retared_dBx"], "")
        self.feed(raw)
        newest = self.window.force_trace.frames[-1]
        self.assertEqual(newest["host_tare_epoch"], old_epoch + 1)
        self.assertEqual(newest["selected_force_n"], 0.)
        self.assertEqual(newest["delta_raw24"], [0.] * 24)

    def test_failed_quality_tare_does_not_flush_or_replace_old_coordinates(self):
        old_tare = self.prime_tare()
        old_epoch = self.window.host_tare_epoch
        for i in range(23):
            self.feed(np.full(24, 500. * (i % 2)))
        count = len(self.window.force_trace)
        with mock.patch.object(self.window, "_save_force_trace", wraps=self.window._save_force_trace) as save:
            self.window._tare()
            save.assert_not_called()
        self.assertEqual(len(self.traces()), 1)
        self.assertEqual(len(self.window.force_trace), count)
        np.testing.assert_array_equal(self.window.tare_raw, old_tare)
        self.assertEqual(self.window.host_tare_epoch, old_epoch)

    def test_failed_build_tare_retains_trace_and_manual_zero(self):
        old_tare = self.prime_tare()
        old_epoch = self.window.host_tare_epoch
        raw = old_tare + REGION_PROTOTYPES[2] * 10000.
        for i in range(23):
            self.feed(raw, firmware_force=[0., 0., i % 2])
        count = len(self.window.force_trace)
        with mock.patch.object(self.window, "_save_force_trace", wraps=self.window._save_force_trace) as save:
            self.window._tare()
            save.assert_not_called()
        self.assertEqual(len(self.traces()), 1)
        self.assertEqual(len(self.window.force_trace), count)
        np.testing.assert_array_equal(self.window.tare_raw, old_tare)
        self.assertEqual(self.window.host_tare_epoch, old_epoch)
        self.assertGreater(self.window.device_fz, .05)

    def test_fault_and_window_close_save_last_accepted_raw_and_force(self):
        tare = self.prime_tare()
        raw = tare + REGION_PROTOTYPES[2] * 10000.
        frame = self.feed(raw, 21)
        before_fault = self.window.device_fz
        self.window._on_stream_fault("fixture recovery gap", "", self.now)
        traces = self.traces()
        header, *samples = traces[-1]
        self.assertEqual(header["reason"], "STREAM_FAULT")
        self.assertEqual(samples[-1]["device_ms"], frame.device_ms)
        self.assertAlmostEqual(samples[-1]["selected_force_n"], before_fault)
        self.assertEqual(samples[-1]["raw24"], raw.tolist())
        self.assertEqual(samples[-1]["tare_raw24"], tare.tolist())
        self.assertEqual(len(self.window.force_trace), 0)
        self.assertTrue(self.window.data_stale)
        frame = self.feed(raw, 3)
        event = mock.Mock()
        self.window.closeEvent(event)
        event.accept.assert_called_once()
        header, *samples = self.traces()[-1]
        self.assertEqual(header["reason"], "WINDOW_CLOSED")
        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[-1]["device_ms"], frame.device_ms)
        self.assertAlmostEqual(samples[-1]["selected_force_n"], before_fault)
        self.assertEqual(samples[-1]["raw24"], raw.tolist())


if __name__ == "__main__":
    unittest.main()
