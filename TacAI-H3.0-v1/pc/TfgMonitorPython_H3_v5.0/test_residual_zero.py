"""Software residual suppression is causal, bounded and does not edit RAW."""
import unittest

import numpy as np

from residual_zero import (ARM_PEAK_N, EXIT_RAW_NORM, MAX_RESIDUAL_N,
                           ResidualZeroFilter)


class ResidualZeroTests(unittest.TestCase):
    def setUp(self):
        self.filter = ResidualZeroFilter()
        self.raw = np.arange(24, dtype=float)
        self.time = 0.

    def feed(self, force, raw=None, **kwargs):
        self.time += .1
        return self.filter.feed(self.raw if raw is None else raw, force, self.time, **kwargs)

    def arm(self, peak=3.):
        result = self.feed(peak, self.raw + 2000.)
        self.assertEqual(result.force_n, peak)
        self.assertFalse(result.applied)

    def suppress(self, residual=1.74):
        self.arm()
        for _ in range(12):
            result = self.feed(residual)
        self.assertTrue(result.applied)
        self.assertEqual(result.force_n, 0.)
        return result

    def test_defaults_and_first_low_loads_are_visible_without_prior_peak(self):
        self.assertEqual(MAX_RESIDUAL_N, 2.)
        self.assertEqual(ARM_PEAK_N, 2.5)
        self.assertEqual(EXIT_RAW_NORM, 350.)
        for force in (.2, 1., 1.74, 2.):
            for _ in range(30):
                result = self.feed(force)
                self.assertEqual(result.force_n, force)
                self.assertFalse(result.applied)

    def test_constant_high_load_stays_visible_for_long_hold(self):
        for _ in range(1200):
            result = self.feed(10., self.raw + 2000.)
            self.assertEqual(result.force_n, 10.)
            self.assertFalse(result.applied)

    def test_1p74_residual_after_peak_is_zero_only_after_real_stable_second(self):
        self.arm()
        for _ in range(10):
            result = self.feed(1.74)
            self.assertEqual(result.force_n, 1.74)
            self.assertFalse(result.applied)
        result = self.feed(1.74)
        self.assertEqual(result.force_n, 0.)
        self.assertTrue(result.applied)
        self.assertEqual(result.peak_force_n, 3.)

    def test_point_two_residual_can_be_zero_after_high_peak(self):
        self.suppress(.2)

    def test_partial_drop_steady_low_load_is_an_explicit_software_limitation(self):
        # There is no physical-unload reference.  The requested software rule
        # deliberately also suppresses an indistinguishable steady partial load.
        self.suppress(1.74)

    def test_candidate_must_be_below_both_force_and_peak_ratio_limits(self):
        self.arm(2.5)
        for _ in range(20):
            result = self.feed(1.74)  # Under 2 N but above 60% of this peak.
            self.assertEqual(result.force_n, 1.74)
            self.assertFalse(result.applied)
        for _ in range(12):
            result = self.feed(1.5)
        self.assertTrue(result.applied)

    def test_stable_raw_with_varying_model_noise_does_not_flash_zero(self):
        self.arm()
        for i in range(20):
            result = self.feed(.2 if i % 2 else 1.74, self.raw + (i % 2) * .2)
        self.assertTrue(result.applied)
        # Once suppressed, the force may move throughout the 2 N tail band;
        # the software must not flicker because model output alone moved .25 N.
        for i, force in enumerate([.2, 1.9, .7, 1.74, 2.] * 10):
            result = self.feed(force, self.raw + (i % 2) * .2)
            self.assertTrue(result.applied)
            self.assertEqual(result.force_n, 0.)

    def test_unstable_raw_cannot_be_suppressed_until_new_stable_window(self):
        self.arm()
        for i in range(25):
            result = self.feed(1.74, self.raw + (i % 2) * 100.)
            self.assertFalse(result.applied)
        for _ in range(12):
            result = self.feed(1.74)
        self.assertTrue(result.applied)

    def test_large_new_raw_response_exits_and_forgets_previous_peak(self):
        self.suppress()
        moved = self.raw.copy()
        moved[0] += EXIT_RAW_NORM + 1.
        result = self.feed(.8, moved)
        self.assertFalse(result.applied)
        self.assertEqual(result.force_n, .8)
        self.assertEqual(result.reason, "NEW_RAW_RESPONSE")
        self.assertEqual(result.peak_force_n, 0.)
        for _ in range(30):
            result = self.feed(.8, moved)
            self.assertFalse(result.applied)
            self.assertEqual(result.force_n, .8)

    def test_new_high_press_exits_then_arms_its_own_later_tail(self):
        self.suppress()
        result = self.feed(8., self.raw + 4000.)
        self.assertFalse(result.applied)
        self.assertEqual(result.force_n, 8.)
        self.assertEqual(result.peak_force_n, 8.)
        for _ in range(12):
            result = self.feed(1.74, self.raw + 100.)
        self.assertTrue(result.applied)

    def test_force_above_two_exits_immediately_even_without_raw_motion(self):
        self.suppress()
        result = self.feed(2.01)
        self.assertFalse(result.applied)
        self.assertEqual(result.force_n, 2.01)
        self.assertEqual(result.peak_force_n, 0.)
        for _ in range(20):
            self.assertFalse(self.feed(1.74).applied)

    def test_duplicate_samples_cannot_manufacture_a_stable_second(self):
        self.arm()
        first = self.feed(1.74)
        for _ in range(100):
            result = self.filter.feed(self.raw.copy(), 1.74, self.time)
            self.assertIs(result, first)
        self.assertEqual(len(self.filter._candidate), 1)
        self.assertFalse(result.applied)
        self.time += 1.
        result = self.filter.feed(self.raw, 1.74, self.time)
        self.assertFalse(result.applied)  # A real one-second gap resets the old peak.
        self.assertEqual(result.reason, "GAP_RESET")

    def test_epoch_gap_clock_reset_and_disable_clear_suppression(self):
        for kind in ("epoch", "gap", "clock", "disabled", "reset"):
            with self.subTest(kind=kind):
                self.filter.reset()
                self.suppress()
                kwargs = {}
                if kind == "epoch":
                    kwargs["epoch"] = (2, "new firmware")
                elif kind == "gap":
                    self.time += .3
                elif kind == "clock":
                    self.time -= 1.
                elif kind == "disabled":
                    kwargs["enabled"] = False
                else:
                    self.filter.reset()
                result = self.feed(1.74, **kwargs)
                self.assertFalse(result.applied)
                self.assertEqual(result.force_n, 1.74)
                self.assertEqual(result.peak_force_n, 0.)

    def test_invalid_frames_and_nonfinite_inputs_are_unavailable_and_reset(self):
        cases = [(self.raw, 1.74, 1., {"valid": False}),
                 (np.full(24, np.nan), 1.74, 1., {}),
                 (np.zeros(23), 1.74, 1., {}),
                 (self.raw, np.inf, 1., {}),
                 (self.raw, 1.74, np.nan, {})]
        for raw, force, now, kwargs in cases:
            with self.subTest(force=force, now=now, kwargs=kwargs):
                self.filter.reset()
                self.suppress()
                result = self.filter.feed(raw, force, now, **kwargs)
                self.assertIsNone(result.force_n)
                self.assertFalse(result.applied)
                self.assertEqual(result.peak_force_n, 0.)
                self.assertFalse(self.feed(1.74).applied)

    def test_inputs_and_timestamps_are_not_modified_or_invented(self):
        raw = self.raw.copy()
        original = raw.copy()
        self.arm()
        for _ in range(12):
            result = self.feed(1.74, raw)
        np.testing.assert_array_equal(raw, original)
        self.assertTrue(result.applied)
        self.assertEqual(self.filter._last_time, self.time)
        raw[:] = 9999.
        np.testing.assert_array_equal(self.filter._zero_raw, original)


if __name__ == "__main__":
    unittest.main()
