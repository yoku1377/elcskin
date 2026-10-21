"""State and audit invariants for continuous high-force development captures."""

import math
import statistics
import unittest

from test_protocol import (
    ACCURACY_TOTAL_POINTS,
    HIGH_FORCE_TRAJECTORIES,
    TestProtocol,
)


class HighForceProtocolTests(unittest.TestCase):
    def test_fixed_trajectories_cover_histories_without_changing_old_protocol(self):
        self.assertEqual(ACCURACY_TOTAL_POINTS, 183)
        self.assertEqual(set(HIGH_FORCE_TRAJECTORIES), {"A", "B"})
        for steps in HIGH_FORCE_TRAJECTORIES.values():
            self.assertEqual(len(steps), 13)
            self.assertEqual(sum(force == 25 for force, _ in steps), 4)
            self.assertEqual({force for force, _ in steps}, {0, 15, 20, 25, 30})
            self.assertEqual(steps[1], (15, 20))
            self.assertEqual(steps[-2:], ((15, 20), (0, 240)))
            self.assertEqual({seconds for force, seconds in steps if force}, {20, 60})
            self.assertIn((0, 180), steps)
            self.assertIn((0, 600), steps)
            previous_25 = {steps[i - 1][0] for i, step in enumerate(steps) if step[0] == 25}
            self.assertEqual(previous_25, {0, 20, 30})

    def test_start_validates_position_trajectory_and_clock_without_partial_reset(self):
        for position, trajectory, now in (("中", "A", 0), ("前", "C", 0), ("前", "A", math.nan)):
            with self.subTest(position=position, trajectory=trajectory, now=now):
                protocol = TestProtocol()
                with self.assertRaises(ValueError):
                    protocol.start_high_force(now, position, trajectory)
                self.assertFalse(protocol.active)
                self.assertEqual(protocol.kind, "none")
        protocol = TestProtocol()
        protocol.start_accuracy(0)
        with self.assertRaises(ValueError):
            protocol.start_high_force(1, "前")
        self.assertEqual(protocol.kind, "accuracy_hysteresis_front_middle_rear")

    def test_waiting_never_infers_stability_from_model_force(self):
        protocol = TestProtocol()
        protocol.start_high_force(10, "后", "B")
        self.assertEqual(protocol.high_force_target(), ("后", 0, 0, 180))
        for now, value in ((11, 0), (100, 25), (1000, 30)):
            self.assertIsNone(protocol.feed(now, value, 0.9, 123))
        self.assertEqual(protocol.phase, "waiting_for_load")
        self.assertEqual(protocol.point_index, 0)
        self.assertEqual(protocol.samples, [])
        self.assertEqual(protocol.summaries, [])
        self.assertIsNone(protocol.context().reference_force_n)
        self.assertFalse(protocol.context().stable_sample)

    def test_manual_reference_is_not_nominal_or_software_output(self):
        protocol = TestProtocol()
        protocol.start_high_force(0, "前")
        protocol.begin_high_force_step(0.18, 1)
        self.assertEqual(protocol.context().nominal_force_n, 0)
        self.assertEqual(protocol.context().reference_force_n, 0.18)
        self.assertFalse(protocol.context().stable_sample)
        protocol.feed(2, 4.5, 0.9, 2.3)
        self.assertEqual(protocol.samples[0][1], 4.5)
        self.assertEqual(protocol.reference_force_n, 0.18)
        self.assertEqual(protocol.phase, "hold_reference")
        self.assertIsNone(protocol.feed(180.999, 1.5, 0.8, 2.2))
        self.assertEqual(protocol.feed(181.1, 3, 0.8, 2.1), "high_force_step_complete")
        row = protocol.summaries[0]
        self.assertEqual(row["reference_resultant_force_n"], 0.18)
        self.assertEqual(row["reference_source"], "manual_plateau_reading")
        self.assertFalse(row["reference_is_continuous_measurement"])
        self.assertEqual(row["hold_age_source"], "operator_stable_mark")
        self.assertEqual(row["requested_duration_s"], 180)
        self.assertAlmostEqual(row["actual_duration_s"], 180.1)
        self.assertEqual(row["software_resultant_mean_n"], 3)
        self.assertEqual(protocol.high_force_target(), ("前", 1, 15, 20))
        self.assertTrue(protocol.active)
        self.assertIsNone(protocol.context().reference_force_n)
        self.assertFalse(protocol.context().stable_sample)

    def test_begin_rejects_bad_reference_phase_and_nonmonotonic_time(self):
        protocol = TestProtocol()
        with self.assertRaises(ValueError):
            protocol.begin_high_force_step(25, 1)
        protocol.start_high_force(0, "前")
        for reference in (-0.01, 40.01, math.nan, math.inf, "not a force"):
            with self.subTest(reference=reference):
                with self.assertRaises(ValueError):
                    protocol.begin_high_force_step(reference, 1)
                self.assertEqual(protocol.phase, "waiting_for_load")
                self.assertIsNone(protocol.reference_force_n)
                self.assertEqual(protocol.high_force_last_time, 0)
        for now in (0, -1, math.inf):
            with self.subTest(now=now), self.assertRaises(ValueError):
                protocol.begin_high_force_step(24.72, now)
        protocol.begin_high_force_step(24.72, 1)
        self.assertEqual(protocol.reference_force_n, 24.72)
        with self.assertRaises(ValueError):
            protocol.begin_high_force_step(25, 2)
        self.assertEqual(protocol.reference_force_n, 24.72)

    def test_flagged_values_are_included_in_statistics_and_gaps_are_audited(self):
        protocol = TestProtocol()
        protocol.start_high_force(0, "后")
        protocol.begin_high_force_step(0.2, 1)
        values = [1.5, 4.5, -0.75, 30.25]
        for now, value, ok in zip((1.1, 2.1, 170, 181), values, (True, False, True, False)):
            event = protocol.feed(now, value, 0.9, 24.8, quality_ok=ok)
        self.assertEqual(event, "high_force_step_complete")
        row = protocol.summaries[0]
        self.assertEqual(row["sample_count"], 4)
        self.assertEqual(row["finite_sample_count"], 4)
        self.assertEqual(row["quality_fault_count"], 2)
        self.assertAlmostEqual(row["max_sample_gap_s"], 167.9)
        self.assertTrue(row["sample_gap_exceeded"])
        self.assertEqual(row["software_resultant_mean_n"], statistics.mean(values))
        self.assertEqual(row["software_resultant_median_n"], statistics.median(values))
        self.assertEqual(row["software_resultant_std_n"], statistics.pstdev(values))
        self.assertEqual(row["software_resultant_min_n"], min(values))
        self.assertEqual(row["software_resultant_max_n"], max(values))
        self.assertEqual(row["acceptance"], "DATA_ONLY")
        self.assertEqual(protocol.acceptance, "DATA_ONLY")

    def test_nonfinite_force_is_not_silently_removed_or_replaced_with_zero(self):
        protocol = TestProtocol()
        protocol.start_high_force(0, "前")
        protocol.begin_high_force_step(0.1, 1)
        protocol.feed(2, math.nan, 0.9, 0)
        self.assertTrue(math.isnan(protocol.samples[0][1]))
        protocol.feed(181, 3, 0.9, 0)
        row = protocol.summaries[0]
        self.assertEqual(row["sample_count"], 2)
        self.assertEqual(row["finite_sample_count"], 1)
        self.assertEqual(row["quality_fault_count"], 1)
        self.assertIsNone(row["software_resultant_mean_n"])
        self.assertEqual(row["acceptance"], "DATA_ONLY")

    def test_feed_clock_error_stops_with_event_and_preserves_partial_samples(self):
        for invalid in (2, 1, math.nan, math.inf, "clock"):
            with self.subTest(invalid=invalid):
                protocol = TestProtocol()
                protocol.start_high_force(0, "前")
                protocol.begin_high_force_step(24.8, 1)
                protocol.feed(2, 26.3, 0.9, 25)
                before = list(protocol.samples)
                self.assertEqual(protocol.feed(invalid, 999, 0.9, 25), "high_force_clock_error")
                self.assertFalse(protocol.active)
                self.assertEqual(protocol.phase, "clock_error")
                self.assertEqual(protocol.acceptance, "INVALID_CLOCK")
                self.assertTrue(protocol.high_force_clock_error_reason)
                self.assertEqual(protocol.samples, before)
                self.assertEqual(protocol.summaries, [])
                self.assertEqual(protocol.high_force_last_time, 2)
                self.assertIsNone(protocol.context().reference_force_n)
                self.assertIsNone(protocol.feed(3, 999, 0.9, 25))

    def test_both_whole_sequences_complete_data_only_with_final_unload_recorded(self):
        for trajectory, steps in HIGH_FORCE_TRAJECTORIES.items():
            with self.subTest(trajectory=trajectory):
                protocol = TestProtocol()
                now = 0.0
                protocol.start_high_force(now, "后", trajectory)
                for index, (nominal, duration) in enumerate(steps):
                    self.assertEqual(protocol.high_force_target(), ("后", index, nominal, duration))
                    now += 0.1
                    protocol.feed(now, 999.0, 0.9, 0)  # waiting remains logged by main, not plateau stats
                    now += 0.1
                    protocol.begin_high_force_step(nominal + 0.17, now)
                    hold_start = now
                    for second in range(1, int(duration) + 1):
                        now = hold_start + second
                        event = protocol.feed(now, nominal + 2.0, 0.9, nominal)
                    self.assertEqual(event, "high_force_complete" if index == len(steps)-1 else "high_force_step_complete")
                    self.assertEqual(protocol.summaries[-1]["software_resultant_mean_n"], nominal + 2.0)
                    self.assertEqual(protocol.summaries[-1]["sample_count"], int(duration))
                    self.assertFalse(protocol.context().stable_sample)
                self.assertFalse(protocol.active)
                self.assertEqual(protocol.phase, "complete")
                self.assertEqual(protocol.acceptance, "DATA_ONLY")
                self.assertEqual(len(protocol.summaries), 13)
                self.assertEqual(protocol.summaries[-1]["actual_duration_s"], 240)
                self.assertIsNone(protocol.context().reference_force_n)
                self.assertIsNone(protocol.context().nominal_force_n)

    def test_stop_keeps_partial_data_and_next_start_clears_session_state(self):
        protocol = TestProtocol()
        protocol.start_high_force(0, "前")
        protocol.begin_high_force_step(0.1, 1)
        protocol.feed(2, 0.4, 0.9, 0.3)
        protocol.stop()
        self.assertEqual(protocol.phase, "stopped")
        self.assertEqual(len(protocol.samples), 1)
        self.assertEqual(protocol.summaries, [])
        self.assertIsNone(protocol.context().reference_force_n)
        protocol.start_high_force(10, "后", "B")
        self.assertEqual(protocol.samples, [])
        self.assertEqual(protocol.high_force_step_quality_fault_count, 0)
        self.assertEqual(protocol.high_force_step_max_gap_s, 0)
        self.assertEqual(protocol.high_force_last_time, 10)
        self.assertEqual(protocol.high_force_target(), ("后", 0, 0, 180))


if __name__ == "__main__":
    unittest.main()
