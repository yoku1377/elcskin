"""Monitor wiring for the explicit second-capture back-25 N candidate."""
import os
import time
import unittest
from unittest.mock import Mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtWidgets
from local_high_force import LocalForceResult, allocate_regions
from main import MONITOR_VERSION, Monitor
from no8_raw_tare_core import PairedFrame


class Back25ConsistencyMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.w = Monitor()
        self.w.timeout_timer.stop()

    def tearDown(self):
        self.w.recording = False
        self.w.protocol.active = False
        self.w.reader = None
        self.w.close()

    def test_new_candidate_is_default_and_old_indices_preserved(self):
        self.assertEqual(MONITOR_VERSION, "5.3.14-recording-availability-fix")
        self.assertEqual(self.w.local_force_box.currentIndex(), 6)
        self.assertEqual(self.w.local_force_selection_index, 6)
        self.assertEqual(self.w.local_force_runtime.profile, "auto")
        self.assertIs(self.w.local_force_runtime.model, self.w.automatic_compensation_model)
        self.assertEqual(self.w.local_force_box.itemText(1), "前按键非线性校正（候选）")
        self.assertEqual(self.w.local_force_box.itemText(2), "后25 N总力校正（候选）")
        self.assertIn("二次测试候选", self.w.local_force_box.itemText(3))
        self.assertIsNotNone(self.w.back25_consistency_model, self.w.back25_consistency_load_error)
        self.assertTrue(self.w.local_force_box.model().item(3).isEnabled())
        self.assertEqual(self.w.back25_consistency_model.profile, "back")
        self.assertFalse(self.w.back25_consistency_model.requires_dual_anchor)
        self.assertIsNotNone(self.w.back25_smooth_model, self.w.back25_smooth_load_error)
        self.assertTrue(self.w.local_force_box.model().item(4).isEnabled())

    def test_selection_is_mutually_exclusive_and_clears_cached_correction(self):
        cases = ((4, "back", self.w.back25_smooth_model, "LOCAL_BACK25_SMOOTH:"),
                 (3, "back", self.w.back25_consistency_model, "LOCAL_BACK25_CONSISTENCY:"),
                 (1, "front", self.w.local_force_model, "LOCAL_FRONT:"),
                 (2, "back", self.w.back_force_model, "LOCAL_BACK:"),
                 (0, "off", None, None))
        for index, profile, model, signature_prefix in cases:
            with self.subTest(index=index):
                if self.w.local_force_box.currentIndex() == index:
                    self.w.local_force_box.setCurrentIndex(0)
                self.w.local_force_result = object()
                self.w.numeric_zone_forces = np.ones(8)
                self.w.local_force_box.setCurrentIndex(index)
                self.assertEqual(self.w.local_force_selection_index, index)
                self.assertEqual(self.w.local_force_runtime.profile, profile)
                self.assertIs(self.w.local_force_runtime.model, model)
                self.assertIsNone(self.w.local_force_result)
                self.assertIsNone(self.w.numeric_zone_forces)
                status = self.w._active_model_status()
                if signature_prefix:
                    self.assertIn(signature_prefix + model.signature, status)
                else:
                    self.assertNotIn("|LOCAL_", status)

    def test_recording_and_protocol_locks_restore_exact_back25_choice(self):
        self.w.local_force_box.setCurrentIndex(3)
        for lock in ("recording", "protocol"):
            with self.subTest(lock=lock):
                self.w.recording = lock == "recording"
                self.w.protocol.active = lock == "protocol"
                epoch = self.w.local_force_runtime.model_epoch
                self.w.local_force_box.setCurrentIndex(2)
                self.assertEqual(self.w.local_force_box.currentIndex(), 3)
                self.assertEqual(self.w.local_force_selection_index, 3)
                self.assertIs(self.w.local_force_runtime.model, self.w.back25_consistency_model)
                self.assertEqual(self.w.local_force_runtime.model_epoch, epoch)
        self.w.recording = False
        self.w.protocol.active = False

    def test_smooth_choice_is_locked_during_recording(self):
        self.w.local_force_box.setCurrentIndex(4)
        self.w.recording = True
        self.w.local_force_box.setCurrentIndex(3)
        self.assertEqual(self.w.local_force_box.currentIndex(), 4)
        self.assertIs(self.w.local_force_runtime.model, self.w.back25_smooth_model)

    def _prepare_frame(self, *, applied=True, reason="TEST_CURRENT_FRAME"):
        self.w.local_force_box.setCurrentIndex(3)
        self.w.tare_raw = np.zeros(24)
        self.w.tare_noise_floor = np.zeros(8)
        self.w.data_stale = False

        def prediction(raw, tare, floor, old, timestamp, **kwargs):
            delta = raw - tare
            # Distinct allocation proves numeric and animation paths use the
            # selected model's zones, rather than reallocating total over RAW.
            total = 25.0 if applied else old
            zones = np.array([.1, .2, .3, .4, 2., 4., 7., 11.]) if applied else allocate_regions(delta, old, floor)
            return LocalForceResult(
                old, total, allocate_regions(delta, old, floor), zones,
                delta.copy(), delta.copy(), "APPLIED" if applied else "BYPASS", reason,
                applied=applied, profile="back", signature=self.w.back25_consistency_model.signature,
                timestamp=timestamp, source_token=kwargs["source_token"], evidence={},
            )

        self.w.local_force_runtime.feed = Mock(side_effect=prediction)
        return PairedFrame(100, time.time(), np.arange(24, dtype=float) * 100 + 1000,
                           np.array([0., 0., 7.5]))

    def test_corrected_total_zones_and_csv_match_and_keep_raw(self):
        frame = self._prepare_frame()
        self.w._on_paired(frame)
        local = self.w.local_force_result
        row = self.w._record_base("PAIRED", frame.device_ms, frame.received_at)
        self.assertEqual(len(row), len(self.w._csv_header()))
        named = dict(zip(self.w._csv_header(), row))
        self.assertEqual(self.w.device_fz, 25.)
        self.assertAlmostEqual(float(self.w.last_zones.sum()), self.w.device_fz)
        np.testing.assert_array_equal(self.w.last_zones, local.zones)
        np.testing.assert_array_equal(self.w.reconstructed_force_map[..., 2].ravel(), local.zones)
        np.testing.assert_array_equal(row[38:46], local.zones)
        np.testing.assert_array_equal(row[46:70], frame.raw)
        np.testing.assert_array_equal(row[70:94], frame.raw - self.w.tare_raw)
        self.assertIn("LOCAL_BACK25_CONSISTENCY:", row[6])
        self.assertEqual(named["local_force_correction_signature"], self.w.back25_consistency_model.signature)
        self.assertEqual(named["local_force_profile"], "back")
        self.assertEqual(named["local_force_corrected_total_n"], 25.)
        self.assertEqual(named["original_selected_resultant_n"], self.w.model_raw_fz)
        self.assertIn("后25 N总力＋区域校正", self.w.last_zone_source)
        self.assertNotIn("原磁响应占比保留", self.w.last_zone_source)
        self.assertIn("后25 N总力＋区域校正已应用", self.w.force_display_source)
        self.w._update_force_display()
        self.w._render(frame.device_ms)
        self.assertEqual(self.w.local_force_runtime.feed.call_count, 1)

    def test_missing_history_and_out_of_domain_show_chinese_bypass(self):
        reasons = (("MISSING_PAST15_ANCHOR", "缺少15 N加载历史"),
                   ("OUTSIDE_BACK25_CALIBRATION_DOMAIN", "当前磁状态超出本批后25 N校准范围"))
        for reason, chinese in reasons:
            with self.subTest(reason=reason):
                frame = self._prepare_frame(applied=False, reason=reason)
                self.w._on_paired(frame)
                self.assertEqual(self.w.device_fz, self.w.model_raw_fz)
                self.assertIn(chinese, self.w.force_display_source)
                self.assertIn("校正旁路", self.w.last_zone_source)
                self.assertNotIn("校正已应用", self.w.force_display_source)

    def test_original_back_total_only_keeps_its_source_label(self):
        frame = self._prepare_frame()
        self.w.local_force_box.setCurrentIndex(2)
        self.w._on_paired(frame)
        self.assertIn("原磁响应占比保留", self.w.last_zone_source)
        self.assertIn("LOCAL_BACK:", self.w._active_model_status())


if __name__ == "__main__":
    unittest.main()
