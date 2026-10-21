"""Monitor integration integrity; fixtures are never deployable calibration."""
import os
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtWidgets
from local_high_force import LOCAL_FORCE_CSV_COLUMNS, LocalForceResult, allocate_regions
from main import FIRMWARE_COMPAT_MODE, Monitor
from no8_raw_tare_core import PairedFrame


class LocalForceMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.w = Monitor()
        self.w.timeout_timer.stop()
        self.w.tare_raw = np.zeros(24)
        self.w.tare_noise_floor = np.full(8, 20.)
        self.w.data_stale = False
        self.w.local_force_model = SimpleNamespace(signature="INTEGRATION_FIXTURE_ONLY")
        self.w.local_force_runtime.profile = "front"
        self.w.local_force_runtime.feed = Mock(side_effect=self.result)

    def tearDown(self):
        self.w.reader = None
        self.w.close()

    def result(self, raw, tare, floor, old, timestamp, **kwargs):
        delta = raw - tare
        total = .02  # Exercise the numeric/CSV path below the animation gate.
        return LocalForceResult(
            old, total, allocate_regions(delta, old, floor),
            allocate_regions(delta, total, floor), delta.copy(), delta.copy(),
            "APPLIED", "INTEGRATION_FIXTURE", applied=True, profile="front",
            signature="INTEGRATION_FIXTURE_ONLY", timestamp=timestamp,
            source_token=kwargs["source_token"], evidence={},
        )

    def frame(self, ms=100):
        return PairedFrame(ms, time.time(), np.arange(24, dtype=float)*100+1000,
                           np.array([0., 0., 7.5]))

    def named(self, event="PAIRED"):
        row = self.w._record_base(event, self.w.last_device_ms, self.w.last_paired_at)
        self.assertEqual(len(row), len(self.w._csv_header()))
        return dict(zip(self.w._csv_header(), row))

    def test_one_feed_per_pair_redraw_does_not_advance_state(self):
        frame = self.frame()
        self.w._on_paired(frame)
        self.assertEqual(self.w.local_force_runtime.feed.call_count, 1)
        self.w._update_force_display()
        self.w._render(frame.device_ms)
        self.assertEqual(self.w.local_force_runtime.feed.call_count, 1)
        call = self.w.local_force_runtime.feed.call_args
        self.assertEqual(call.args[4], frame.received_at)
        self.assertEqual(call.kwargs["source_token"], frame.device_ms)

    def test_numeric_export_and_halves_match_current_result_keep_raw(self):
        frame = self.frame()
        self.w._on_paired(frame)
        row = self.named()
        self.assertEqual(self.w.device_fz, .02)
        self.assertAlmostEqual(sum(self.w.last_zones), .02)
        self.assertEqual(self.w.front_readout.text(), f"{self.w.last_zones[:4].sum():.2f} N")
        self.assertEqual(self.w.back_readout.text(), f"{self.w.last_zones[4:].sum():.2f} N")
        self.assertEqual(row["local_force_corrected_total_n"], .02)
        self.assertEqual(row["original_selected_resultant_n"], self.w.model_raw_fz)
        np.testing.assert_array_equal(self.w.raw, frame.raw)
        raw_row = self.w._record_base("PAIRED", frame.device_ms, frame.received_at)
        np.testing.assert_array_equal(raw_row[46:70], frame.raw)
        np.testing.assert_array_equal(raw_row[70:94], frame.raw-self.w.tare_raw)
        np.testing.assert_array_equal(raw_row[38:46], self.w.local_force_result.zones)

    def test_wrong_frame_cached_result_cannot_override_display(self):
        self.w._on_paired(self.frame())
        self.w.local_force_result = replace(self.w.local_force_result, source_token=99)
        self.w._update_force_display()
        self.assertEqual(self.w.device_fz, self.w.model_raw_fz)
        self.assertIsNone(self.w.numeric_zone_forces)
        self.assertTrue(all(self.named()[name] == "" for name in LOCAL_FORCE_CSV_COLUMNS))

    def test_fault_resets_state_and_keeps_measurements_blank(self):
        self.w._on_paired(self.frame())
        self.w.recording = True
        self.w._on_stream_fault("test fault", "bad line", time.time())
        self.assertIsNone(self.w.local_force_result)
        row = dict(zip(self.w._csv_header(), self.w.records[-1]))
        self.assertEqual(row["record_type"], "STREAM_ERROR")
        self.assertTrue(all(row[name] == "" for name in LOCAL_FORCE_CSV_COLUMNS))

    def test_firmware_value_mode_has_no_mislabelled_host_correction_audit(self):
        self.w.force_mode_box.setCurrentText(FIRMWARE_COMPAT_MODE)
        self.w._on_paired(self.frame())
        self.assertEqual(self.w.device_fz, 7.5)
        self.assertTrue(all(self.named()[name] == "" for name in LOCAL_FORCE_CSV_COLUMNS))

    def test_fresh_pair_retires_previous_legacy_map(self):
        self.w._on_luo_force_map(np.ones((4, 3, 3)), time.time())
        self.assertIsNotNone(self.w.luo_force_map)
        self.w._on_paired(self.frame())
        self.assertIsNone(self.w.luo_force_map)
        self.assertEqual(self.w.local_force_runtime.feed.call_count, 1)
        self.assertEqual(self.named()["local_force_corrected_total_n"], .02)

    def test_profile_changes_blocked_during_recording(self):
        self.w._on_paired(self.frame())
        current = self.w.local_force_result
        self.w.recording = True
        self.w._on_local_force_changed(0)
        self.assertEqual(self.w.local_force_runtime.profile, "front")
        self.assertIs(self.w.local_force_result, current)

    def test_off_profile_does_not_keep_a_correction_signature(self):
        self.w._on_local_force_changed(0)
        self.assertEqual(self.w.local_force_runtime.profile, "off")
        self.assertIsNone(self.w.local_force_runtime.model)


if __name__ == "__main__":
    unittest.main()
