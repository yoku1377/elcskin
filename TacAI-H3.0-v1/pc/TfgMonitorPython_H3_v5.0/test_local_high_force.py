"""Causal runtime integrity; synthetic coefficients are NOT a released profile."""
import copy
import json
from pathlib import Path
import unittest
import tempfile

import numpy as np

from local_high_force import (LocalHighForceModel, LocalHighForceRuntime, SUPPORT_METHOD,
                              LinearKernelHighForceModel, LINEAR_KERNEL_METHOD, load_local_model,
                              LOCAL_FORCE_CSV_COLUMNS, allocate_regions, export_dict, export_row)
from local_state import RisingState


def synthetic_profile():
    unit = [1.] + [0.] * 23
    block = dict(center24=[0.] * 24, pc1x24=unit, score_scale=1.)
    return dict(runtime_approved=True, profile="front", model=dict(
        block_A=block, block_B=copy.deepcopy(block), feature_center2=[0., 0.],
        magnetic_coef2x12=np.zeros((2, 12)).tolist(), pre_anchor12=[-100.] * 12,
        beta_coef2=[0., 0.], beta_center=.75, beta_limits=[.5, 1.],
        support_model=dict(method=SUPPORT_METHOD, training_count=4,
                           load_center24=[0.] * 24, quiet_center24=[0.] * 24,
                           load_scale=1e6, quiet_scale=1e6,
                           training_points_normalized48=np.zeros((4, 48)).tolist(), threshold=1.)))


def runtime(profile="front"):
    return LocalHighForceRuntime(LocalHighForceModel(synthetic_profile()), profile=profile)


def synthetic_linear_kernel_profile():
    support = synthetic_profile()['model']['support_model']
    return dict(runtime_approved=True, method=LINEAR_KERNEL_METHOD, profile='front', model=dict(
        embedding=dict(block_A_center24=[0.] * 24, block_B_center24=[0.] * 24,
                       block_A_scale=1e6, block_B_scale=1e6, pca_center48=[0.] * 48,
                       pc3x48=np.eye(48)[:3].tolist(), score_scale3=[1.] * 3,
                       training_z3=np.zeros((4, 3)).tolist(), kernel_length=1.,
                       kernel_column_mean=[1.] * 4, kernel_grand_mean=1.),
        linear_feature_center3=[0.] * 3,
        linear_magnetic_coef3x12=np.zeros((3, 12)).tolist(),
        magnetic_residual_alpha_nx12=np.full((4, 12), 1000.).tolist(),
        linear_beta_coef3=[0.] * 3, beta_residual_alpha_n=[10.] * 4,
        beta_training_mean=.75, beta_residual_mean=.05,
        pre_anchor12=[0.] * 12, support_model=support,
        magnetic_target_training_mean12_for_audit_only=[1e9] * 12))


def feed(r, t, force, raw=None, **kwargs):
    if raw is None:
        raw = np.full(24, force * 100)
    return r.feed(raw, np.zeros(24), np.full(8, 20.), force, t,
                  source_token=round(t * 1000), epoch=(1, 1), **kwargs)


def load(r, start=0.):
    for i in range(15):
        feed(r, start + i * .1, 0.)
    feed(r, start + 1.5, 1.)
    feed(r, start + 1.6, 10.)
    before = feed(r, start + 1.7, 16.)
    final = feed(r, start + 1.8, 25.)
    return before, final


class LocalHighForceTests(unittest.TestCase):
    def test_linear_kernel_dispatch_does_not_add_target_mean_or_double_beta_intercept(self):
        profile = synthetic_linear_kernel_profile()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic_profile.json'
            path.write_text(json.dumps(profile), encoding='utf8')
            model = load_local_model(path)
        self.assertIsInstance(model, LinearKernelHighForceModel)
        r = LocalHighForceRuntime(model, profile='front')
        load(r)
        value = feed(r, 1.9, 30.)
        self.assertAlmostEqual(value.beta, .8)
        self.assertAlmostEqual(value.total_n, 28.)
        np.testing.assert_allclose(value.corrected_delta24, value.original_delta24, rtol=0, atol=1e-9)
        self.assertEqual(len(value.state_features), 3)
        # No time basis: the same load's state and beta remain fixed on later frames.
        later = feed(r, 2., 30.)
        self.assertEqual(later.beta, value.beta)
        self.assertEqual(later.support, value.support)

    def test_current_tare_coordinate_is_subtracted_exactly_once(self):
        model = LinearKernelHighForceModel(synthetic_linear_kernel_profile())
        state = RisingState(quiet_raw24=np.zeros(24), response24=np.zeros(24), available_time=0.)
        old_delta = np.full(24, 500.) - 1.
        result = model.predict(old_delta, 25., np.ones(24), np.full(8, 20.), state, timestamp=1.)
        np.testing.assert_allclose(result.corrected_delta24[6:18], np.full(12, 500.), atol=1e-12)
        np.testing.assert_array_equal(result.corrected_delta24[:6], np.full(6, 499.))
        np.testing.assert_array_equal(result.corrected_delta24[18:], np.full(6, 499.))

    def test_failed_candidate_requires_explicit_development_permission(self):
        profile = synthetic_profile()
        profile.update(not_for_production=True)
        with self.assertRaisesRegex(ValueError, "not approved"):
            LocalHighForceModel(profile)
        model = LocalHighForceModel(profile, allow_development=True)
        with self.assertRaisesRegex(ValueError, "Unaccepted"):
            LocalHighForceRuntime(model, profile="front")
        self.assertEqual(LocalHighForceRuntime().profile, "off")

    def test_bad_frozen_parameters_fail_load_before_measurements(self):
        for key, value in (("beta_center", float("nan")), ("beta_limits", [0., 1.]),
                           ("magnetic_coef2x12", [[0.] * 12])):
            p = synthetic_profile(); p["model"][key] = value
            with self.subTest(key=key), self.assertRaises((ValueError, TypeError)):
                LocalHighForceModel(p)

    def test_anchor_is_unavailable_until_right_frame_and_frozen_afterwards(self):
        r = runtime()
        for i in range(15): feed(r, i * .1, 0.)
        feed(r, 1.5, 10.)
        self.assertIsNone(r.last_result.evidence["rising_raw24"])
        captured = feed(r, 1.6, 16.)
        np.testing.assert_array_equal(captured.evidence["rising_raw24"], np.full(24, 1500.))
        self.assertEqual(captured.evidence["rising_available_time"], 1.6)
        frozen = copy.deepcopy(captured.audit_record())
        feed(r, 1.7, 25., np.full(24, -999.))
        self.assertEqual(captured.audit_record(), frozen)
        self.assertEqual(r.last_result.evidence["rising_raw24"], frozen["evidence"]["rising_raw24"])

    def test_one_zero_flicker_preserves_state_real_unload_releases_it(self):
        r = runtime(); _, loaded = load(r)
        before = loaded.evidence.copy(); state_epoch = loaded.state_epoch
        self.assertEqual(feed(r, 1.9, 0.).state_epoch, state_epoch)
        after_flicker = feed(r, 2.0, 25.)
        self.assertEqual(after_flicker.evidence["rising_raw24"], before["rising_raw24"])
        self.assertEqual(after_flicker.evidence["rising_available_time"], before["rising_available_time"])
        for i in range(12): last = feed(r, 2.1 + i * .1, 0.)
        self.assertIsNone(last.evidence["rising_raw24"])
        _, recaptured = load(r, 3.3)
        self.assertGreater(recaptured.evidence["rising_available_time"], before["rising_available_time"])

    def test_source_faults_clear_state_until_a_new_quiet_and_rise(self):
        for fault in ("invalid", "gap", "nonmonotonic", "nan", "epoch", "tare", "setting"):
            with self.subTest(fault=fault):
                r = runtime(); load(r)
                if fault == "invalid": feed(r, 1.9, 25., valid=False)
                elif fault == "gap": feed(r, 2.2, 25.)
                elif fault == "nonmonotonic": feed(r, 1.7, 25.)
                elif fault == "nan": feed(r, 1.9, float("nan"))
                elif fault == "epoch": r.feed(np.full(24, 2500), np.zeros(24), np.full(8, 20), 25, 1.9, epoch=(1, 2))
                elif fault == "tare": r.feed(np.full(24, 2500), np.ones(24), np.full(8, 20), 25, 1.9, epoch=(1, 1))
                else: feed(r, 1.9, 25., provisional_enabled=False)
                last = feed(r, 2.3, 25.)
                self.assertFalse(last.applied)
                self.assertEqual(last.total_n, 25.)
                self.assertIsNone(last.evidence["rising_raw24"])
                _, recaptured = load(r, 2.4)
                self.assertTrue(recaptured.applied)

    def test_missing_or_unsupported_state_preserves_all_original_values(self):
        r = runtime()
        missing = feed(r, 0., 25.)
        self.assertFalse(missing.applied)
        np.testing.assert_array_equal(missing.corrected_delta24, missing.original_delta24)
        np.testing.assert_array_equal(missing.zones, missing.original_zones)
        profile = synthetic_profile(); profile["model"]["support_model"]["threshold"] = 0.
        r = LocalHighForceRuntime(LocalHighForceModel(profile), profile="front")
        _, unsupported = load(r)
        self.assertEqual(unsupported.reason, "OUTSIDE_INPUT_NEIGHBORHOOD")
        self.assertEqual(unsupported.total_n, unsupported.original_total_n)
        np.testing.assert_array_equal(unsupported.zones, unsupported.original_zones)

    def test_allocation_conserves_total_and_only_roi_magnetic_vectors_change(self):
        r = runtime(); load(r)
        value = feed(r, 1.9, 30., np.full(24, 500.))
        self.assertEqual(value.total_n, 27.5)
        self.assertAlmostEqual(float(value.zones.sum()), 27.5, places=12)
        np.testing.assert_array_equal(value.corrected_delta24[:6], value.original_delta24[:6])
        np.testing.assert_array_equal(value.corrected_delta24[18:], value.original_delta24[18:])
        np.testing.assert_array_equal(value.corrected_delta24[6:18], np.full(12, 400.))
        future = feed(r, 2.0, 31., np.full(24, 501.))
        self.assertAlmostEqual(future.total_n - value.total_n, .75)
        np.testing.assert_array_equal(future.corrected_delta24 - value.corrected_delta24, np.ones(24))

    def test_low_force_returns_original_before_state_or_support_use(self):
        model = LocalHighForceModel(synthetic_profile())
        invalid_state = RisingState(response24=np.full(24, np.nan), quiet_raw24=np.full(24, np.nan))
        for force in (0., .2, 15., 20.):
            delta = np.arange(24, dtype=float) * 100
            result = model.predict(delta, force, np.zeros(24), np.full(8, 20), invalid_state)
            self.assertEqual(result.total_n, force)
            self.assertEqual(result.reason, "LOW_FORCE_IDENTITY")
            np.testing.assert_array_equal(result.zones, result.original_zones)
            np.testing.assert_array_equal(result.corrected_delta24, delta)

    def test_even_supplied_anchor_cannot_be_used_before_its_available_time(self):
        model = LocalHighForceModel(synthetic_profile())
        state = RisingState(quiet_raw24=np.zeros(24), response24=np.ones(24), available_time=5.)
        result = model.predict(np.full(24, 500), 25., np.zeros(24), np.full(8, 20), state, timestamp=4.9)
        self.assertEqual(result.reason, "ANCHOR_NOT_YET_AVAILABLE")
        self.assertEqual(result.total_n, 25.)

    def test_invalid_current_frame_is_unavailable_not_cached_force(self):
        r = runtime(); load(r)
        invalid = feed(r, 1.9, 25., valid=False)
        self.assertEqual(invalid.status, "UNAVAILABLE")
        self.assertIsNone(invalid.total_n)
        self.assertIsNone(invalid.zones)
        self.assertEqual(invalid.evidence, {})
        extreme = feed(r, 2., 25., np.full(24, 1e308))
        self.assertEqual(extreme.status, "UNAVAILABLE")

    def test_csv_append_preserves_frozen_evidence_and_blanks_invalid_events(self):
        r = runtime(); _, loaded = load(r)
        row = export_row(loaded)
        self.assertEqual(len(row), len(LOCAL_FORCE_CSV_COLUMNS))
        self.assertEqual(len(set(LOCAL_FORCE_CSV_COLUMNS)), len(LOCAL_FORCE_CSV_COLUMNS))
        named = dict(zip(LOCAL_FORCE_CSV_COLUMNS, row))
        self.assertEqual(named, loaded.export_dict())
        evidence = json.loads(named['local_force_state_json'])
        self.assertEqual(evidence['rising_available_time'], 1.7)
        self.assertEqual(len(evidence['quiet_raw24']), 24)
        self.assertEqual(len(evidence['rising_raw24']), 24)
        self.assertEqual(named['original_selected_resultant_n'], 25.)
        self.assertEqual(named['local_force_corrected_total_n'], loaded.total_n)
        self.assertEqual(export_row(None), [''] * len(LOCAL_FORCE_CSV_COLUMNS))
        invalid = export_dict(feed(r, 1.9, 25., valid=False))
        self.assertEqual(invalid['local_force_status'], 'UNAVAILABLE')
        for key in LOCAL_FORCE_CSV_COLUMNS[10:]: self.assertEqual(invalid[key], '')

    def test_recorded_front15_frames_are_exact_identity(self):
        root = Path(__file__).resolve().parents[2]
        path = root / "tmp/diagnose_20261014/sensor_arrays.json"
        if not path.exists(): self.skipTest("Local recorded regression data is unavailable")
        model = LocalHighForceModel(synthetic_profile())
        count = 0
        for trial in json.loads(path.read_text(encoding="utf8")):
            raw = np.asarray(trial["raw"]).reshape(-1, 24)
            delta = np.asarray(trial["retared"]).reshape(-1, 24)
            for current, baseline, old in zip(delta, raw - delta, trial["total"]):
                self.assertLessEqual(old, 20.)
                result = model.predict(current, old, baseline, np.full(8, 20.), RisingState())
                self.assertEqual(result.total_n, old)
                np.testing.assert_array_equal(result.corrected_delta24, current)
                np.testing.assert_array_equal(result.zones, result.original_zones)
                count += 1
        arr = np.load(root / "tmp/diagnose_20261014_full/sensor_full.npz")
        records = json.loads((root / "tmp/diagnose_20261014_full/aligned_full.json").read_text(encoding="utf8"))["records"]
        for record in records:
            if record["nominal_n"] != 15: continue
            lo, hi = record["sensor_episode"]["sensor_time_s"]
            mask = (arr["t"] >= lo-15) & (arr["t"] <= hi+15)
            raw = arr["raw"][mask].reshape(-1, 24)
            delta = arr["retared"][mask].reshape(-1, 24)
            for current, baseline, old in zip(delta, raw-delta, arr["total"][mask]):
                self.assertLessEqual(old, 20.)
                result = model.predict(current, old, baseline, np.full(8, 20.), RisingState())
                self.assertEqual(result.total_n, old)
                np.testing.assert_array_equal(result.corrected_delta24, current)
                np.testing.assert_array_equal(result.zones, result.original_zones)
                count += 1
        self.assertEqual(count, 8010)


if __name__ == "__main__":
    unittest.main()
