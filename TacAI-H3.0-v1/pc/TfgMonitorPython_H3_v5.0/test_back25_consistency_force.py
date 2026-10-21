"""Rear25 candidate prediction parity, causal guards and audit preservation."""
import copy
import csv
import json
from pathlib import Path
import unittest

import numpy as np

from back25_consistency_force import Back25ConsistencyForceModel, METHOD, TOTAL_METHOD, SPATIAL_METHOD
from local_high_force import ORIGINAL_MODEL_SHA256, LocalHighForceRuntime, allocate_regions, export_dict, load_local_model
from local_state import RisingState


def fixture():
    ids = ['a', 'b', 'c', 'd']
    points = np.zeros((4, 48)); points[:, 0] = [0., .1, -.1, .2]
    center = np.full(24, 1000.)
    sm = dict(method='TWO_BLOCK_TRACE_STANDARDIZED_NEAREST_NEIGHBOR_V1', training_count=4,
              training_ids=ids, training_points_normalized48=points.tolist(),
              load_center24=center.tolist(), quiet_center24=[1.] * 24, load_scale=100., quiet_scale=100.)
    total = dict(method=TOTAL_METHOD, training_ids=ids, support_model=sm,
                 kernel_length=1., beta_center=.6, coefficient=[.01, -.01, .02, -.02],
                 beta_limits=[.5, 1.], lower_identity_original_n=20., local_upper_original_n=31.,
                 original_max_n=35., required_anchor_old_output_n=15.)
    spatial_center = center.copy(); spatial_center[0] -= 5.
    pc = np.eye(48)[:2]
    coef = np.zeros((2, 24)); coef[0, 12] = .05; coef[1, 18] = -.03
    spatial = dict(centers=[spatial_center.tolist(), [1.] * 24], scales=[100., 100.],
                   pc=pc.tolist(), center=(np.ones(24) / np.sqrt(24)).tolist(),
                   coefficient=coef.tolist(), training_z=points[:, :2].tolist(), training_ids=ids)
    return dict(method=METHOD, profile='back', original_model_sha256=ORIGINAL_MODEL_SHA256,
                runtime_approved=True, acceptance_passed=False,
                total=dict(method=TOTAL_METHOD, original_model_sha256=ORIGINAL_MODEL_SHA256, model=total),
                spatial=dict(method=SPATIAL_METHOD, model=spatial),
                domain_guard=dict(total_neighbor_threshold=.5, spatial_neighbor_threshold=.5,
                                  spatial_residual_norm_threshold=.1))


def state():
    quiet = np.ones(24); response = np.full(24, 1000.)
    return RisingState(quiet_raw24=quiet, rising_raw24=quiet + response,
                       response24=response, available_time=1.5)


class Back25ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.payload = fixture()
        self.model = Back25ConsistencyForceModel(self.payload)
        self.state = state()
        self.delta = np.arange(1., 25.) * 100.
        self.floor = np.full(8, 10.)

    def predict(self, **kwargs):
        args = dict(delta24=self.delta, old_force=29., tare24=np.zeros(24),
                    noise_floor=self.floor, state=self.state, timestamp=2.)
        args.update(kwargs)
        return self.model.predict(**args)

    def test_explicit_runtime_permission_pending_acceptance_and_source_model(self):
        mutations = [lambda p: p.update(profile='front'), lambda p: p.update(runtime_approved=False),
                     lambda p: p.update(original_model_sha256='0' * 64),
                     lambda p: p['total'].update(original_model_sha256='0' * 64),
                     lambda p: p.update(acceptance_passed=True),
                     lambda p: p['total']['model'].update(coefficient=[1.]),
                     lambda p: p['total']['model']['support_model'].update(load_scale=0.),
                     lambda p: p['spatial']['model'].update(coefficient=[[1.]]),
                     lambda p: p['spatial']['model'].update(pc=[[1.] * 48]),
                     lambda p: p['domain_guard'].update(spatial_residual_norm_threshold=-1.)]
        for change in mutations:
            payload = copy.deepcopy(self.payload); change(payload)
            with self.subTest(change=str(change)), self.assertRaises(ValueError):
                Back25ConsistencyForceModel(payload)
        p = copy.deepcopy(self.payload); p['runtime_approved'] = False
        self.assertTrue(Back25ConsistencyForceModel(p, allow_development=True).development_only)
        self.assertFalse(self.model.acceptance_passed)
        self.assertFalse(self.model.requires_dual_anchor)
        with self.assertRaises(ValueError):
            LocalHighForceRuntime(self.model, profile='front')

    def test_exact_formula_audit_and_live_current_response(self):
        r = self.predict()
        self.assertTrue(r.applied)
        self.assertEqual(r.status, 'APPLIED')
        tm = self.payload['total']['model']; sm = self.payload['spatial']['model']
        x = np.zeros(48)
        distances = np.linalg.norm(np.array(tm['support_model']['training_points_normalized48']) - x, axis=1)
        beta = np.clip(tm['beta_center'] + np.exp(-.5 * (distances / tm['kernel_length']) ** 2) @ tm['coefficient'], .5, 1.)
        total = 20. + beta * 9.
        z = np.array([.05, 0.]); predicted = np.array(sm['center']) + z @ np.array(sm['coefficient'])
        corrected = self.delta - (np.ones(24) + np.linalg.norm(self.delta - 1.) * (predicted - sm['center']))
        np.testing.assert_allclose(r.corrected_delta24, corrected, rtol=0, atol=1e-11)
        np.testing.assert_allclose(r.zones, allocate_regions(corrected, total, self.floor), rtol=0, atol=1e-12)
        np.testing.assert_array_equal(r.original_delta24, self.delta)
        np.testing.assert_array_equal(self.state.response24, np.full(24, 1000.))
        self.assertAlmostEqual(r.total_n, total, places=12)
        self.assertAlmostEqual(r.zones.sum(), r.total_n, places=12)
        self.assertIn('NOMINAL_REFERENCE_ONLY', r.warnings)
        self.assertIn('CALIBRATED_CANDIDATE_ACCEPTANCE_PENDING', r.warnings)
        audit = export_dict(r)
        self.assertEqual(audit['S5_corrected_retared_dBx'], r.corrected_delta24[12])
        self.assertEqual(audit['S5_original_resultant_allocation_estimated_n'], r.original_zones[4])
        shifted = self.delta.copy(); shifted[18] += 250.
        other = self.predict(delta24=shifted)
        self.assertGreater(float(np.max(np.abs(r.zones - other.zones))), .01)
        self.assertGreater(self.predict(old_force=29.1).total_n, r.total_n)

    def test_low_force_identity_and_invalid_history(self):
        future = copy.deepcopy(self.state); object.__setattr__(future, 'available_time', 3.)
        wrong = copy.deepcopy(self.state); object.__setattr__(wrong, 'rising_raw24', np.zeros(24))
        cases = [dict(old_force=20., state=None, tare24=None, timestamp=None), dict(state=None),
                 dict(state=future), dict(state=wrong), dict(tare24=np.full(24, np.nan))]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                r = self.predict(**kwargs)
                self.assertFalse(r.applied); self.assertEqual(r.total_n, r.original_total_n)
                np.testing.assert_array_equal(r.corrected_delta24, self.delta)
                np.testing.assert_array_equal(r.zones, r.original_zones)
        self.assertEqual(self.predict(old_force=-1.).status, 'UNAVAILABLE')

    def test_each_domain_guard_bypasses_both_corrections(self):
        cases = []
        p = copy.deepcopy(self.payload)
        p['total']['model']['support_model']['training_points_normalized48'] = (np.ones((4, 48)) * 2).tolist()
        cases.append((p, self.state, 'total_neighbor'))
        p = copy.deepcopy(self.payload); p['domain_guard']['spatial_neighbor_threshold'] = .01
        cases.append((p, self.state, 'spatial_neighbor'))
        p = copy.deepcopy(self.payload); p['domain_guard']['total_neighbor_threshold'] = 2.
        st = copy.deepcopy(self.state); a = self.state.response24.copy(); a[2] += 100.
        object.__setattr__(st, 'response24', a); object.__setattr__(st, 'rising_raw24', self.state.quiet_raw24 + a)
        cases.append((p, st, 'spatial_residual'))
        for payload, st, failed in cases:
            with self.subTest(failed=failed):
                self.model = Back25ConsistencyForceModel(payload)
                r = self.predict(state=st)
                self.assertFalse(r.applied); self.assertFalse(r.support['checks'][failed])
                self.assertEqual(sum(not x for x in r.support['checks'].values()), 1)
                self.assertEqual(r.reason, 'OUTSIDE_BACK25_CALIBRATION_DOMAIN')
                np.testing.assert_array_equal(r.corrected_delta24, self.delta)
                np.testing.assert_array_equal(r.zones, r.original_zones)
                self.assertEqual(r.total_n, r.original_total_n)

    def test_mapping_continuity_gain_and_limits(self):
        for beta in (.5, .6, 1.):
            x = np.linspace(0., 40., 4001)
            y = np.array([self.model.correct_total(v, beta) for v in x])
            self.assertTrue(np.all(np.diff(y) > 0))
            np.testing.assert_array_equal(x[x <= 20.], y[x <= 20.])
            np.testing.assert_array_equal(x[x >= 35.], y[x >= 35.])
            for knot in (20., 31., 35.):
                self.assertLess(abs(self.model.correct_total(knot - 1e-7, beta)
                                    - self.model.correct_total(knot + 1e-7, beta)), 1e-6)
        self.assertIn('OUTSIDE_BACK25_VALIDATED_FORCE_RANGE', self.predict(old_force=32.).warnings)
        self.model.beta_center = 2.
        self.model.spatial_coefficient *= 10000.
        r = self.predict()
        self.assertIn('BETA_LIMITED', r.warnings)
        self.assertIn('SPATIAL_DIRECTION_SHIFT_LIMITED', r.warnings)

    def test_runtime_single15_latch_and_history_reset(self):
        # Broad fixture domain isolates the reusable causal latch behavior here.
        p = copy.deepcopy(self.payload)
        p['domain_guard'] = {key: 1e6 for key in p['domain_guard']}
        runtime = LocalHighForceRuntime(Back25ConsistencyForceModel(p), profile='back')
        for i in range(15):
            runtime.feed(np.zeros(24), np.zeros(24), self.floor, 0., i * .1, source_token=i, epoch=(1, 1))
        for time, force in [(1.5, 10.), (1.6, 16.), (1.7, 29.)]:
            r = runtime.feed(np.full(24, force * 100.), np.zeros(24), self.floor, force, time,
                             source_token=int(time * 100), epoch=(1, 1))
        self.assertTrue(r.applied)
        self.assertEqual(r.evidence['anchor_old_output_indices'], [15.])
        r = runtime.feed(np.full(24, 2900.), np.zeros(24), self.floor, 29., 2.2,
                         source_token=220, epoch=(1, 1))
        self.assertFalse(r.applied)
        self.assertEqual(r.total_n, 29.)

    def test_private_candidate_replay_matches_frozen_offline_formula(self):
        root = Path(__file__).resolve().parents[2]
        source = root / 'outputs/20261015_后25N专项补偿候选/后25N_专项补偿候选参数.json'
        if not source.exists():
            self.skipTest('Private calibration source not distributed')
        payload = json.loads(source.read_text(encoding='utf8'))
        if not all(Path(s['path']).exists() for s in payload['source_files']):
            self.skipTest('Private recorded CSVs not distributed')
        payload.update(profile='back', original_model_sha256=ORIGINAL_MODEL_SHA256, runtime_approved=True)
        payload['domain_guard'] = dict(total_neighbor_threshold=1e6, spatial_neighbor_threshold=1e6,
                                       spatial_residual_norm_threshold=1e6)
        model = Back25ConsistencyForceModel(payload)
        runtime_profile = Path(__file__).with_name('back25_consistency_profile.json')
        guarded = load_local_model(runtime_profile) if runtime_profile.exists() else None
        tm = payload['total']['model']; sm = payload['spatial']['model']; support = tm['support_model']
        count = 0
        for item in payload['source_files']:
            with Path(item['path']).open(encoding='utf-8-sig', newline='') as stream:
                rows = [r for r in csv.DictReader(stream) if r['frame_state'] == 'PAIRED' and r['scalar_resultant_raw_model_n']]
            for row in rows:
                saved = json.loads(row['local_force_state_json'])
                st = RisingState(quiet_raw24=np.array(saved['quiet_raw24']), rising_raw24=np.array(saved['rising_raw24']),
                                 response24=np.array(saved['rising_response24']), available_time=saved['rising_available_time'])
                delta = np.array([float(row[f'S{i}_retared_dB{axis}']) for i in range(1, 9) for axis in 'xyz'])
                raw = np.array([float(row[f'S{i}_raw_dB{axis}']) for i in range(1, 9) for axis in 'xyz'])
                floor = np.array([float(row[f'S{i}_spatial_noise_floor']) for i in range(1, 9)])
                old = float(row['scalar_resultant_raw_model_n']); b = st.quiet_raw24 - (raw - delta)
                x = np.r_[(st.response24 - support['load_center24']) / support['load_scale'],
                          (b - support['quiet_center24']) / support['quiet_scale']]
                distances = np.linalg.norm(np.array(support['training_points_normalized48']) - x, axis=1)
                beta = float(np.clip(tm['beta_center'] + np.exp(-.5 * (distances / tm['kernel_length']) ** 2) @ tm['coefficient'], .5, 1.))
                expected_total = model.correct_total(old, beta)
                x = np.concatenate([(v - c) / scale for v, c, scale in zip((st.response24, b), sm['centers'], sm['scales'])])
                z = x @ np.array(sm['pc']).T
                predicted = np.array(sm['center']) + z @ np.array(sm['coefficient'])
                shift = predicted - np.array(sm['center']); length = np.linalg.norm(shift)
                if length > .5: shift *= .5 / length
                expected_delta = delta - np.clip((old - 20.) / 5., 0., 1.) * (b + np.linalg.norm(raw - st.quiet_raw24) * shift)
                r = model.predict(delta, old, raw - delta, floor, st, timestamp=float(row['local_force_timestamp']))
                self.assertTrue(r.applied)
                self.assertAlmostEqual(r.total_n, expected_total, places=10)
                np.testing.assert_allclose(r.corrected_delta24, expected_delta, rtol=0, atol=1e-8)
                np.testing.assert_allclose(r.zones, allocate_regions(expected_delta, expected_total, floor), rtol=0, atol=1e-10)
                np.testing.assert_array_equal(r.original_delta24, delta)
                if guarded is not None:
                    actual = guarded.predict(delta, old, raw - delta, floor, st,
                                             timestamp=float(row['local_force_timestamp']))
                    self.assertTrue(actual.applied)
                    self.assertAlmostEqual(actual.total_n, expected_total, places=10)
                    np.testing.assert_allclose(actual.zones, r.zones, rtol=0, atol=1e-10)
                count += 1
        self.assertGreater(count, 2000)


if __name__ == '__main__':
    unittest.main()
