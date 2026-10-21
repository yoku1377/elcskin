import unittest

import numpy as np

from h3_v3_3_force_model import (
    calibrate_nominal_high_force,
    calibrate_low_force,
    estimate_force,
    estimate_force_details,
    soft_gate_force,
)
from no8_raw_tare_core import evaluate_raw_force


ZERO_FRONT = np.asarray([
    432.576, -769.023, 1471.359, -438.584, -24.032, -3300.8795,
    -588.784, 696.928, 1006.719, 961.281, -1249.664, -774.407,
    1165.552, 60.08, -96.804, -1189.584, -612.816, 212.961,
    -1117.488, -312.416, -261.3635, 1369.824, -300.4, -929.281,
])
FRONT_0P2 = np.asarray([
    -132.176, -1141.519, 174.242, 318.424, -660.88, -2449.0435,
    -973.296, 1441.92, 2284.485, 1225.633, -817.088, -2865.282,
    1369.824, 492.656, -77.445, -1754.336, -408.544, -38.718,
    -1177.568, -264.353, -222.6365, 1273.696, -216.288, -929.281,
])
FRONT_30 = np.asarray([
    -6548.72, -3160.208, -15062.079, 6146.184, -12.016, 9825.1995,
    4421.888, 12412.527, 63713.767, -132.175, -5431.232, -60732.327,
    18588.751, -1219.624, -21818.726, -17663.52, -901.2, 21083.039,
    -16461.92, -1946.592, -1597.1995, 15644.833, -348.464, 3368.641,
])


class H3V33ForceModelTests(unittest.TestCase):
    def test_zero_recovery_anchor_is_zero(self):
        force, weights, confidence, region = estimate_force(ZERO_FRONT)
        self.assertEqual(force, 0.0)
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=12)
        self.assertGreaterEqual(confidence, 0.9)
        self.assertIn(region, (0, 1, 2))

    def test_low_force_anchor_remains_visible(self):
        force, _weights, confidence, region = estimate_force(FRONT_0P2)
        self.assertGreater(force, 0.05)
        self.assertLess(force, 0.8)
        self.assertGreater(confidence, 0.35)
        self.assertEqual(region, 2)

    def test_high_force_anchor_is_not_compressed(self):
        force, _weights, confidence, region = estimate_force(FRONT_30)
        self.assertGreater(force, 28.0)
        self.assertLessEqual(force, 32.0)
        self.assertGreater(confidence, 0.8)
        self.assertEqual(region, 2)

    def test_invalid_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            estimate_force(np.zeros(23))

    def test_soft_gate_preserves_obvious_force_when_classifier_shifts(self):
        force, rescued = soft_gate_force(5.0, 0.20)
        self.assertEqual(force, 5.0)
        self.assertTrue(rescued)

    def test_soft_gate_still_suppresses_unloaded_residual(self):
        force, rescued = soft_gate_force(0.30, 0.20)
        self.assertEqual(force, 0.0)
        self.assertFalse(rescued)

    def test_negative_load_score_cannot_clear_obvious_force(self):
        force, rescued = soft_gate_force(1.0, -0.10)
        self.assertEqual(force, 1.0)
        self.assertTrue(rescued)

    def test_known_release_shape_is_zeroed_by_narrow_prototype_gate(self):
        details = estimate_force_details(ZERO_FRONT)
        self.assertEqual(details.fz_n, 0.0)
        self.assertEqual(details.gate_reason, "KNOWN_RELEASE_PROTOTYPE")
        self.assertLessEqual(details.release_similarity_ratio, 0.85)

    def test_unknown_direction_uses_continuity_fallback_instead_of_zero(self):
        # Axis reversal is an intentionally severe proxy for an unseen oblique
        # contact.  The signed regression becomes negative, while the magnetic
        # activity still proves a large structural response.
        oblique_proxy = -FRONT_30
        details = estimate_force_details(oblique_proxy)
        self.assertLess(details.regression_unclamped_n, 0.0)
        self.assertGreater(details.fz_n, 20.0)
        self.assertEqual(details.gate_reason, "DIRECTION_INVARIANT_FALLBACK")
        self.assertTrue(details.rescued)

    def test_position_calibration_maps_measured_low_force_anchor(self):
        # Middle-region 1.4516 N preliminary output corresponds to 0.2 N.
        corrected = calibrate_low_force(1.4515895057276276, [0.0, 1.0, 0.0, 0.0])
        self.assertAlmostEqual(corrected, 0.2, places=6)

    def test_provisional_high_force_correction_can_be_disabled(self):
        original = estimate_force_details(
            FRONT_30, use_provisional_high_force_correction=False,
        )
        corrected = estimate_force_details(FRONT_30)
        self.assertGreater(original.fz_n, corrected.fz_n)
        self.assertGreater(corrected.fz_n, 28.0)
        self.assertEqual(original.gate_reason, corrected.gate_reason)
        self.assertEqual(
            estimate_force(FRONT_0P2)[0],
            estimate_force(FRONT_0P2, use_provisional_high_force_correction=False)[0],
        )
        self.assertEqual(
            estimate_force(ZERO_FRONT)[0],
            estimate_force(ZERO_FRONT, use_provisional_high_force_correction=False)[0],
        )

    def test_host_force_core_exposes_provisional_correction_switch(self):
        tare = np.zeros(24)
        corrected = evaluate_raw_force(FRONT_30, tare)
        original = evaluate_raw_force(
            FRONT_30, tare, use_provisional_high_force_correction=False,
        )
        self.assertLess(corrected.fz_n, original.fz_n)
        self.assertAlmostEqual(corrected.fz_n, estimate_force(FRONT_30)[0])

    def test_provisional_high_force_curve_is_monotone_and_bounded(self):
        grid = np.linspace(0.0, 35.0, 351)
        for region in range(3):
            weights = np.eye(4)[region]
            output = np.asarray([
                calibrate_nominal_high_force(value, weights) for value in grid
            ])
            self.assertTrue(np.all(np.diff(output) >= -1e-10))
            self.assertEqual(output[0], 0.0)
            self.assertEqual(output[50], 5.0)
            self.assertEqual(output[-1], 35.0)
            self.assertEqual(
                calibrate_nominal_high_force(20.0, weights, enabled=False), 20.0,
            )

    def test_unknown_direction_fallback_skips_nominal_position_calibration(self):
        oblique_proxy = -FRONT_30
        corrected = estimate_force_details(oblique_proxy)
        original = estimate_force_details(
            oblique_proxy, use_provisional_high_force_correction=False,
        )
        self.assertEqual(corrected.gate_reason, "DIRECTION_INVARIANT_FALLBACK")
        self.assertEqual(corrected.fz_n, original.fz_n)


if __name__ == "__main__":
    unittest.main()
