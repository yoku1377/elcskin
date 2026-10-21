import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc" / "TfgMonitorPython_H3_v5.0"))

from test_protocol import (
    ACCURACY_SEQUENCE,
    ACCURACY_TOTAL_POINTS,
    TestProtocol,
)


class ProtocolTests(unittest.TestCase):
    def test_accuracy_uses_one_model_for_front_middle_rear(self):
        protocol = TestProtocol()
        protocol.start_accuracy(0.0)
        self.assertEqual(ACCURACY_TOTAL_POINTS, len(ACCURACY_SEQUENCE) * 3)
        self.assertEqual(protocol.accuracy_target(), ("前", 0, 0.0))
        protocol.begin_accuracy_sample(0.04, 10.0)
        self.assertIsNone(protocol.feed(11.0, 0.1, 0.8, 0.0))
        self.assertEqual(protocol.feed(13.1, 0.2, 0.9, 0.0), "accuracy_point_complete")
        self.assertEqual(protocol.summaries[0]["calibration_model"], "ONE_SHARED_MODEL_FRONT_MIDDLE_REAR")
        self.assertAlmostEqual(protocol.summaries[0]["software_resultant_median_n"], 0.15)
        protocol.point_index = len(ACCURACY_SEQUENCE)
        self.assertEqual(protocol.accuracy_target(), ("中", 0, 0.0))
        protocol.point_index = len(ACCURACY_SEQUENCE) * 2
        self.assertEqual(protocol.accuracy_target(), ("后", 0, 0.0))

    def test_long_recovery_passes_001_and_15_limits(self):
        protocol = TestProtocol()
        protocol.start_long_recovery(100.0, "中")
        self.assertIsNone(protocol.feed(599.9, 30.0, 0.8, 30.0))
        self.assertEqual(protocol.feed(600.0, 30.0, 0.8, 30.0), "hold_complete")
        protocol.confirm_unloaded(601.0)
        event = None
        for step in range(241):
            elapsed = step * 0.5
            value = 1.0 if elapsed <= 5.0 else (0.005 if elapsed >= 110.0 else 0.2)
            event = protocol.feed(601.0 + elapsed, value, 0.9, value)
        self.assertEqual(event, "recovery_complete_pass")
        summary = protocol.summaries[0]
        self.assertEqual(summary["acceptance"], "PASS")
        self.assertLessEqual(summary["fast_peak_abs_n"], 1.5)
        self.assertLessEqual(summary["final_max_abs_n"], 0.01)

    def test_long_recovery_fails_final_residual_over_001(self):
        protocol = TestProtocol()
        protocol.start_long_recovery(0.0, "后")
        protocol.feed(500.0, 30.0, 0.9, 30.0)
        protocol.confirm_unloaded(501.0)
        event = None
        for step in range(241):
            elapsed = step * 0.5
            event = protocol.feed(501.0 + elapsed, 0.02, 0.9, 0.02)
        self.assertEqual(event, "recovery_complete_fail")
        self.assertEqual(protocol.summaries[0]["acceptance"], "FAIL")

    def test_emi_acceptance_checks_shift_peak_gap_and_quality(self):
        protocol = TestProtocol()
        protocol.start_emi(0.0, "前")
        event = None
        for step in range(61):
            event = protocol.feed(step * 0.5, 0.0, 0.95, 0.0)
        self.assertEqual(event, "emi_baseline_complete")
        protocol.confirm_emi_on(31.0, "开关电源+手机发射")
        for step in range(121):
            elapsed = step * 0.5
            value = 0.5 if step == 10 else 0.005
            event = protocol.feed(31.0 + elapsed, value, 0.95, value, quality_ok=True)
        self.assertEqual(event, "emi_complete_pass")
        summary = protocol.summaries[0]
        self.assertEqual(summary["acceptance"], "PASS")
        self.assertLessEqual(summary["steady_shift_abs_n"], 0.01)
        self.assertLessEqual(summary["transient_peak_abs_n"], 1.5)


if __name__ == "__main__":
    unittest.main()
