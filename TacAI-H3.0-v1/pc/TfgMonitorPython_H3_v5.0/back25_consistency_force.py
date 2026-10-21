"""Frozen rear25 consistency candidate using past quiet and 15-output state.

This adapter only predicts. Nominal calibration labels, file names and round
numbers are not runtime inputs. Runtime permission does not imply independent
force accuracy or regional force acceptance. All three state-domain guards
must pass before either the total or spatial correction can be applied.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np

from local_high_force import LocalForceResult, ORIGINAL_MODEL_SHA256, allocate_regions
from regional_high_force import _array, _scalar

METHOD = 'BACK_NOMINAL25_PAST15_QUIET_TOTAL_AND_SPATIAL_RIDGE_CANDIDATE_V1'
TOTAL_METHOD = 'BACK_NOMINAL25_SINGLE15_TOTAL_RBF_OFFLINE_V1'
SPATIAL_METHOD = 'BACK25_PAST15_QUIET_DIRECTION_RIDGE_V1'
SUPPORT_METHOD = 'TWO_BLOCK_TRACE_STANDARDIZED_NEAREST_NEIGHBOR_V1'


def _training_ids(value, minimum=4):
    if (not isinstance(value, list) or len(value) < minimum
            or any(not isinstance(x, str) or not x for x in value)
            or len(set(value)) != len(value)):
        raise ValueError('Rear25 requires distinct nonempty training IDs')
    return list(value)


class Back25ConsistencyForceModel:
    method = METHOD
    spatial_method = SPATIAL_METHOD
    profile = 'back'
    requires_dual_anchor = False

    def __init__(self, payload, *, allow_development=False):
        try:
            document = json.loads(json.dumps(payload, allow_nan=False))
            if not isinstance(document, dict) or document.get('method') != self.method:
                raise ValueError('Explicit rear25 consistency method required')
            if document.get('profile') != 'back':
                raise ValueError('Rear25 consistency requires explicit back profile')
            if document.get('original_model_sha256') != ORIGINAL_MODEL_SHA256:
                raise ValueError('Rear25 original force model SHA mismatch')
            approved = (document.get('runtime_approved') is True
                        and not document.get('not_for_production', False))
            if not approved and not allow_development:
                raise ValueError('Rear25 consistency candidate is not approved for runtime')
            # This particular frozen candidate remains pending fresh acceptance.
            if document.get('acceptance_passed') is not False:
                raise ValueError('Rear25 candidate acceptance must remain explicitly pending')
            self.development_only = not approved
            self.acceptance_passed = False
            self.validation_status = document.get('validation_status', 'CALIBRATED_CANDIDATE_ACCEPTANCE_PENDING')
            self.signature = self.method + ':' + hashlib.sha256(json.dumps(
                document, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                allow_nan=False).encode('utf8')).hexdigest()
            self._parameters(document)
        except (KeyError, TypeError, OverflowError, AttributeError) as error:
            raise ValueError(f'Invalid rear25 consistency profile: {error}') from error

    def _parameters(self, document):
        total = document['total']
        spatial = document['spatial']
        if total.get('method') != TOTAL_METHOD or spatial.get('method') != self.spatial_method:
            raise ValueError('Unsupported rear25 component method')
        if total.get('original_model_sha256') != ORIGINAL_MODEL_SHA256:
            raise ValueError('Rear25 total component original model SHA mismatch')
        m = total['model']
        if m.get('method') != TOTAL_METHOD or m.get('required_anchor_old_output_n') != 15.:
            raise ValueError('Rear25 total component requires the original-output15 anchor')
        if (m['lower_identity_original_n'], m['local_upper_original_n'], m['original_max_n']) != (20., 31., 35.):
            raise ValueError('Unsupported rear25 force mapping boundaries')
        if m.get('beta_limits') != [.5, 1.]:
            raise ValueError('Unsupported rear25 gain limits')
        self.total_ids = _training_ids(m['training_ids'])
        n = len(self.total_ids)
        sm = m['support_model']
        if (sm.get('method') != SUPPORT_METHOD or sm.get('training_count') != n
                or sm.get('training_ids') != self.total_ids):
            raise ValueError('Rear25 total training/support inputs differ')
        self.total_points = _array(sm['training_points_normalized48'], (n, 48), 'rear25 total training points')
        self.total_centers = _array([sm['load_center24'], sm['quiet_center24']], (2, 24), 'rear25 total centers')
        self.total_scales = _array([sm['load_scale'], sm['quiet_scale']], (2,), 'rear25 total scales')
        if np.any(self.total_scales <= 0):
            raise ValueError('Rear25 total scales must be positive')
        self.kernel_length = _scalar(m['kernel_length'], 'rear25 kernel length', positive=True)
        self.beta_center = _scalar(m['beta_center'], 'rear25 beta center')
        self.total_coefficient = _array(m['coefficient'], (n,), 'rear25 RBF coefficients')
        m = spatial['model']
        self.spatial_ids = _training_ids(m['training_ids'])
        n = len(self.spatial_ids)
        self.spatial_centers = _array(m['centers'], (2, 24), 'rear25 spatial centers')
        self.spatial_scales = _array(m['scales'], (2,), 'rear25 spatial scales')
        if np.any(self.spatial_scales <= 0):
            raise ValueError('Rear25 spatial scales must be positive')
        pc = np.asarray(m['pc'], float)
        if pc.ndim != 2 or not 1 <= len(pc) <= min(3, n - 2):
            raise ValueError('Rear25 spatial PCA rank must be between one and three')
        rank = len(pc)
        self.spatial_pc = _array(pc, (rank, 48), 'rear25 spatial PCA axes')
        if not np.allclose(self.spatial_pc @ self.spatial_pc.T, np.eye(rank), rtol=0, atol=1e-8):
            raise ValueError('Rear25 spatial PCA axes must be orthonormal')
        self.spatial_center = _array(m['center'], (24,), 'rear25 direction center')
        self._load_direction(m, rank, n)
        self.spatial_points = _array(m['training_z'], (n, rank), 'rear25 spatial training scores')
        guard = document['domain_guard']
        self.total_neighbor_threshold = _scalar(guard['total_neighbor_threshold'], 'rear25 total neighbor threshold')
        self.spatial_neighbor_threshold = _scalar(guard['spatial_neighbor_threshold'], 'rear25 spatial neighbor threshold')
        self.spatial_residual_norm_threshold = _scalar(guard['spatial_residual_norm_threshold'], 'rear25 spatial PCA residual threshold')
        if min(self.total_neighbor_threshold, self.spatial_neighbor_threshold, self.spatial_residual_norm_threshold) < 0:
            raise ValueError('Rear25 domain thresholds must be nonnegative')

    def _load_direction(self, model, rank, count):
        self.spatial_coefficient = _array(model['coefficient'], (rank, 24), 'rear25 direction coefficients')

    def _predict_direction(self, normalized48, scores):
        return self.spatial_center + scores @ self.spatial_coefficient

    @staticmethod
    def correct_total(old, beta):
        if old <= 20. or old >= 35.:
            return float(old)
        if old <= 31.:
            return float(20. + beta * (old - 20.))
        junction = 20. + beta * 11.
        return float(junction + (35. - junction) * (old - 31.) / 4.)

    def predict(self, delta24, old_force, tare24, noise_floor, state, *, timestamp=None):
        try:
            delta = _array(delta24, (24,), 'rear25 current delta')
            floor = _array(noise_floor, (8,), 'rear25 noise floor')
            old = _scalar(old_force, 'rear25 original force')
            original_zones = allocate_regions(delta, old, floor)
        except (ValueError, TypeError, OverflowError):
            return LocalForceResult(None, None, None, None, None, None, 'UNAVAILABLE',
                                    'INVALID_CURRENT_INPUT', profile='back', signature=self.signature)
        values = dict(original_total_n=old, total_n=old, original_zones=original_zones,
                      zones=original_zones.copy(), original_delta24=delta,
                      corrected_delta24=delta.copy(), status='BYPASS', profile='back', signature=self.signature)
        if old <= 20.:
            return LocalForceResult(**values, reason='LOW_FORCE_IDENTITY')
        if state is None or getattr(state, 'response24', None) is None:
            return LocalForceResult(**values, reason='MISSING_PAST15_ANCHOR')
        try:
            now = _scalar(timestamp, 'rear25 acquisition time')
            available = _scalar(state.available_time, 'rear25 past15 available time')
            if available > now:
                return LocalForceResult(**values, reason='ANCHOR_NOT_YET_AVAILABLE')
            tare = _array(tare24, (24,), 'rear25 tare')
            quiet = _array(state.quiet_raw24, (24,), 'rear25 quiet')
            rising = _array(state.rising_raw24, (24,), 'rear25 rising15')
            a = _array(state.response24, (24,), 'rear25 past15 response')
            if not np.allclose(rising - quiet, a, rtol=1e-12, atol=1e-9):
                return LocalForceResult(**values, reason='INCONSISTENT_PAST15_COORDINATES')
            b = quiet - tare
        except (ValueError, TypeError, AttributeError, OverflowError):
            return LocalForceResult(**values, reason='INVALID_OR_MISSING_PAST_INPUT')
        with np.errstate(over='ignore', invalid='ignore', divide='ignore', under='ignore'):
            blocks = np.array([a, b])
            total_x = ((blocks - self.total_centers) / self.total_scales[:, None]).reshape(48)
            spatial_x = ((blocks - self.spatial_centers) / self.spatial_scales[:, None]).reshape(48)
            z = spatial_x @ self.spatial_pc.T
            total_distances = np.linalg.norm(self.total_points - total_x, axis=1)
            spatial_distances = np.linalg.norm(self.spatial_points - z, axis=1)
            total_nearest = int(np.argmin(total_distances))
            spatial_nearest = int(np.argmin(spatial_distances))
            total_distance = float(total_distances[total_nearest])
            spatial_distance = float(spatial_distances[spatial_nearest])
            residual = float(np.linalg.norm(spatial_x - z @ self.spatial_pc))
            finite = bool(np.all(np.isfinite(total_x)) and np.all(np.isfinite(spatial_x))
                          and np.all(np.isfinite(z)) and np.isfinite(total_distance)
                          and np.isfinite(spatial_distance) and np.isfinite(residual))
            checks = dict(total_neighbor=finite and total_distance <= self.total_neighbor_threshold,
                          spatial_neighbor=finite and spatial_distance <= self.spatial_neighbor_threshold,
                          spatial_residual=finite and residual <= self.spatial_residual_norm_threshold)
            supported = all(checks.values())
            reason = 'SUPPORTED_BACK25_PAST15_STATE' if supported else 'OUTSIDE_BACK25_CALIBRATION_DOMAIN'
            safe = lambda value: value if np.isfinite(value) else None
            support = dict(supported=supported, reason=reason, checks=checks,
                           total_neighbor_distance=safe(total_distance), total_neighbor_threshold=self.total_neighbor_threshold,
                           total_nearest_training_id=self.total_ids[total_nearest],
                           spatial_neighbor_distance=safe(spatial_distance), spatial_neighbor_threshold=self.spatial_neighbor_threshold,
                           spatial_nearest_training_id=self.spatial_ids[spatial_nearest],
                           spatial_residual_norm=safe(residual), spatial_residual_norm_threshold=self.spatial_residual_norm_threshold)
            if not supported:
                return LocalForceResult(**values, reason=reason, support=support)
            beta_raw = float(self.beta_center + np.exp(-.5 * (total_distances / self.kernel_length) ** 2) @ self.total_coefficient)
            beta = float(np.clip(beta_raw, .5, 1.))
            total = self.correct_total(old, beta)
            predicted = self._predict_direction(spatial_x, z)
            shift = predicted - self.spatial_center
            shift_norm = float(np.linalg.norm(shift))
            if shift_norm > .5:
                shift = shift * (.5 / shift_norm)
            blend = float(np.clip((old - 20.) / 5., 0., 1.))
            response_norm = float(np.linalg.norm(delta - b))
            corrected = delta - blend * (b + response_norm * shift)
            if (not np.isfinite(beta_raw) or not np.isfinite(total) or not np.isfinite(shift_norm)
                    or not np.isfinite(response_norm) or not np.all(np.isfinite(corrected))):
                return LocalForceResult(**values, reason='NONFINITE_MODEL_OUTPUT', support=support)
        try:
            zones = allocate_regions(corrected, total, floor)
        except (ValueError, TypeError, OverflowError):
            return LocalForceResult(**values, reason='NONFINITE_MODEL_OUTPUT', support=support)
        warnings = ['NOMINAL_REFERENCE_ONLY', 'CALIBRATED_CANDIDATE_ACCEPTANCE_PENDING']
        if old > 31.:
            warnings.append('OUTSIDE_BACK25_VALIDATED_FORCE_RANGE')
        if beta != beta_raw:
            warnings.append('BETA_LIMITED')
        if shift_norm > .5:
            warnings.append('SPATIAL_DIRECTION_SHIFT_LIMITED')
        if self.development_only:
            warnings.append('DEVELOPMENT_PROFILE_NOT_ACCEPTED')
        support.update(relative_direction_shift=shift_norm, spatial_blend=blend,
                       response_norm=response_norm, beta_unlimited=beta_raw)
        values.update(total_n=total, zones=zones, corrected_delta24=corrected, status='APPLIED')
        return LocalForceResult(**values, reason='SUPPORTED_CAUSAL_BACK25_TOTAL_AND_SPATIAL_STATE',
                                applied=True, support=support, beta=beta,
                                state_features=tuple(np.r_[total_x, z].tolist()), warnings=tuple(warnings))
