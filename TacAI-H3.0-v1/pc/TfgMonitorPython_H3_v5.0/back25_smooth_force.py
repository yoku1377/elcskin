"""Smooth nonlinear historical magnetic-direction correction, frozen candidate.

Shares the v5.3.8 total-force mapping, three domain checks, causal history,
original-value audit and live current-response allocation. No online fitting.
"""
import numpy as np

from back25_consistency_force import Back25ConsistencyForceModel
from regional_high_force import _array, _scalar

METHOD = 'BACK_NOMINAL25_PAST15_QUIET_TOTAL_AND_SPATIAL_RBF_CANDIDATE_V2'
SPATIAL_METHOD = 'BACK25_PAST15_QUIET_DIRECTION_RBF_V2'


class Back25SmoothForceModel(Back25ConsistencyForceModel):
    method = METHOD
    spatial_method = SPATIAL_METHOD

    def _load_direction(self, model, rank, count):
        self.direction_points = _array(model['training_x'], (count, 48), 'rear25 direction inputs')
        self.direction_length = _scalar(model['kernel_length'], 'rear25 direction length', positive=True)
        self.spatial_coefficient = _array(model['coefficient'], (count, 24), 'rear25 nonlinear direction coefficients')
        if not np.allclose(self.direction_points @ self.spatial_pc.T, model['training_z'], rtol=0, atol=1e-8):
            raise ValueError('Rear25 direction and guard training points differ')

    def _predict_direction(self, normalized48, scores):
        distances = np.linalg.norm(self.direction_points - normalized48, axis=1)
        kernel = np.exp(-.5 * (distances / self.direction_length) ** 2)
        return self.spatial_center + kernel @ self.spatial_coefficient
