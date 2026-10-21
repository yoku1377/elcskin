"""Release adaptation changes RAW coordinates, never a scalar force offset."""
import unittest

import numpy as np

from release_baseline import ReleaseBaselineTracker
from residual_zero import ResidualZeroFilter


class ReleaseBaselineTests(unittest.TestCase):
    def setUp(self):
        self.tracker = ReleaseBaselineTracker()
        self.manual = np.zeros(24)
        self.time = 0.
        self.epoch = (1, "fixture", 1)

    @staticmethod
    def nonlinear_force(raw, tare):
        # An independently evaluated nonlinear mapping makes scalar N
        # subtraction observably different from a proper RAW-coordinate shift.
        return float(((raw[0] - tare[0]) / 100.) ** 2)

    def raw(self, first):
        value = np.zeros(24)
        value[0] = first
        return value

    def feed(self, first, *, interval=.1, **kwargs):
        self.time += interval
        raw = self.raw(first)
        force = self.nonlinear_force(raw, self.tracker.effective_tare(self.manual))
        return self.tracker.feed(raw, force, self.time, manual_tare24=self.manual,
                                 epoch=kwargs.pop("epoch", self.epoch), **kwargs)

    def commit(self, peak_raw=200., residual_raw=100.):
        self.feed(peak_raw)
        changed = []
        for _ in range(12):
            result = self.feed(residual_raw)
            if result.changed:
                changed.append(result)
        self.assertEqual(len(changed), 1)
        return changed[0]

    def test_first_low_load_never_creates_work_offset(self):
        value = np.sqrt(.2) * 100.
        for _ in range(120):
            self.assertFalse(self.feed(value).changed)
        np.testing.assert_array_equal(self.tracker.offset_raw24, np.zeros(24))
        self.assertAlmostEqual(self.nonlinear_force(self.raw(value),
                               self.tracker.effective_tare(self.manual)), .2)
        self.assertEqual(self.tracker.baseline_epoch, 0)

    def test_two_press_cycles_use_each_new_raw_baseline_without_scalar_subtraction(self):
        self.commit()
        self.assertEqual(self.tracker.offset_raw24[0], 100.)
        self.assertEqual(self.tracker.baseline_epoch, 1)
        self.assertEqual(self.nonlinear_force(self.raw(100.),
                         self.tracker.effective_tare(self.manual)), 0.)
        # Next response is 200 RAW units beyond the new work zero: 4 N.  A
        # scalar subtraction would incorrectly calculate 9 N - 1 N = 8 N.
        self.assertEqual(self.nonlinear_force(self.raw(300.),
                         self.tracker.effective_tare(self.manual)), 4.)
        self.assertNotEqual(4., self.nonlinear_force(self.raw(300.), self.manual) - 1.)
        self.commit(peak_raw=300., residual_raw=200.)
        self.assertEqual(self.tracker.offset_raw24[0], 200.)  # Not 100 + 200.
        self.assertEqual(self.tracker.baseline_epoch, 2)
        self.assertAlmostEqual(self.nonlinear_force(self.raw(280.),
                               self.tracker.effective_tare(self.manual)), .64)
        for _ in range(30):
            self.assertFalse(self.feed(280.).changed)
        self.assertAlmostEqual(self.nonlinear_force(self.raw(280.),
                               self.tracker.effective_tare(self.manual)), .64)

    def test_high_constant_load_cannot_move_zero(self):
        for _ in range(1200):
            self.assertFalse(self.feed(200.).changed)
        np.testing.assert_array_equal(self.tracker.offset_raw24, np.zeros(24))

    def test_freeze_preserves_work_zero_and_does_not_accumulate_peak(self):
        self.commit()
        baseline = self.tracker.offset_raw24
        old_epoch = self.tracker.baseline_epoch
        for _ in range(30):
            self.assertFalse(self.feed(300., allow_update=False).changed)
        for _ in range(20):
            self.assertFalse(self.feed(200.).changed)
        np.testing.assert_array_equal(self.tracker.offset_raw24, baseline)
        self.assertEqual(self.tracker.baseline_epoch, old_epoch)

    def test_disabled_restores_manual_zero_and_monotonic_epoch(self):
        self.commit()
        result = self.feed(100., enabled=False)
        self.assertTrue(result.changed)
        self.assertEqual(result.baseline_epoch, 2)
        np.testing.assert_array_equal(self.tracker.effective_tare(self.manual), self.manual)
        self.assertEqual(self.nonlinear_force(self.raw(100.),
                         self.tracker.effective_tare(self.manual)), 1.)
        self.assertFalse(self.feed(100., enabled=False).changed)
        self.assertEqual(self.tracker.baseline_epoch, 2)
        for _ in range(20):
            self.assertFalse(self.feed(100.).changed)

    def test_external_epoch_change_clears_reference_even_on_invalid_frame(self):
        self.commit()
        result = self.feed(100., epoch=(2, "new firmware", 4), valid=False)
        self.assertTrue(result.changed)
        self.assertEqual(result.reason, "INVALID_FRAME")
        np.testing.assert_array_equal(self.tracker.offset_raw24, np.zeros(24))
        self.assertEqual(self.tracker.baseline_epoch, 2)
        self.assertEqual(self.tracker.last_commit_frames, ())

    def test_manual_tare_change_cannot_reuse_offset_in_old_coordinates(self):
        self.commit()
        self.manual = self.raw(20.)
        result = self.feed(100.)
        self.assertTrue(result.changed)
        self.assertEqual(result.reason, "EPOCH_REFERENCE_RESET")
        np.testing.assert_array_equal(self.tracker.effective_tare(self.manual), self.manual)

    def test_gap_keeps_offset_but_clears_a_pending_peak(self):
        self.commit()
        offset = self.tracker.offset_raw24
        self.feed(300.)
        result = self.feed(200., interval=.4)
        self.assertFalse(result.changed)
        self.assertEqual(result.reason, "GAP_RESET")
        for _ in range(20):
            self.assertFalse(self.feed(200.).changed)
        np.testing.assert_array_equal(self.tracker.offset_raw24, offset)
        self.assertEqual(self.tracker.baseline_epoch, 1)

    def test_invalid_and_cycle_reset_preserve_offset_but_discard_old_peak(self):
        for kind in ("invalid", "nonfinite", "cycle", "clock"):
            with self.subTest(kind=kind):
                self.setUp()
                self.commit()
                offset = self.tracker.offset_raw24
                self.feed(300.)
                if kind == "invalid":
                    self.feed(200., valid=False)
                elif kind == "nonfinite":
                    result = self.tracker.feed(np.full(24, np.nan), 1., self.time + .1,
                        manual_tare24=self.manual, epoch=self.epoch)
                    self.assertFalse(result.changed)
                elif kind == "cycle":
                    self.tracker.reset_cycle()
                else:
                    self.time -= 1.
                    self.feed(200.)
                for _ in range(20):
                    self.assertFalse(self.feed(200.).changed)
                np.testing.assert_array_equal(self.tracker.offset_raw24, offset)

    def test_duplicate_frames_cannot_commit_or_add_an_offset_twice(self):
        self.feed(200.)
        first = self.feed(100.)
        for _ in range(100):
            result = self.tracker.feed(self.raw(100.), 1., self.time,
                manual_tare24=self.manual, epoch=self.epoch)
            self.assertFalse(result.changed)
        self.assertFalse(first.changed)
        for _ in range(11):
            result = self.feed(100.)
        self.assertEqual(self.tracker.baseline_epoch, 1)
        self.assertEqual(self.tracker.offset_raw24[0], 100.)
        for _ in range(100):
            self.assertFalse(self.tracker.feed(self.raw(100.), 1., self.time,
                manual_tare24=self.manual, epoch=self.epoch).changed)
        self.assertEqual(self.tracker.baseline_epoch, 1)
        self.assertEqual(self.tracker.offset_raw24[0], 100.)

    def test_raw_manual_and_returned_snapshot_arrays_do_not_mutate_state(self):
        original_manual = self.manual.copy()
        raw = self.raw(200.)
        original_raw = raw.copy()
        self.tracker.feed(raw, 4., 0., manual_tare24=self.manual, epoch=self.epoch)
        for i in range(12):
            residual = self.raw(100.)
            result = self.tracker.feed(residual, 1., (i + 1) * .1,
                manual_tare24=self.manual, epoch=self.epoch)
        np.testing.assert_array_equal(raw, original_raw)
        np.testing.assert_array_equal(self.manual, original_manual)
        result.offset_raw24[:] = 999.
        exposed = self.tracker.offset_raw24
        exposed[:] = 777.
        frames = self.tracker.last_commit_frames
        self.assertGreaterEqual(len(frames), 10)
        self.assertGreaterEqual(frames[-1][0] - frames[0][0], 1. - 1e-6)
        frames[0][1][:] = 888.
        self.assertEqual(self.tracker.offset_raw24[0], 100.)
        self.assertEqual(self.tracker.last_commit_frames[0][1][0], 100.)

    def test_detector_reference_properties_are_detached_and_reset_with_cycle(self):
        detector = ResidualZeroFilter()
        self.assertIsNone(detector.reference_raw24)
        self.assertEqual(detector.release_samples, ())
        detector.feed(self.raw(200.), 4., 0.)
        for i in range(12):
            detector.feed(self.raw(100.), 1., (i + 1) * .1)
        reference = detector.reference_raw24
        reference[:] = 999.
        samples = detector.release_samples
        samples[0][1][:] = 888.
        self.assertEqual(detector.reference_raw24[0], 100.)
        self.assertEqual(detector.release_samples[0][1][0], 100.)
        detector.reset()
        self.assertIsNone(detector.reference_raw24)
        self.assertEqual(detector.release_samples, ())


if __name__ == "__main__":
    unittest.main()
