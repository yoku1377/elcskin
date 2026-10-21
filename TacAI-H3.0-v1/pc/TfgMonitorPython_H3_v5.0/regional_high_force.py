"""Optional frozen per-Hall correction adapter; no embedded or approved profile.

Inference consumes current measured inputs and a past dual crossing state. It has
no fitting, reference-force, file-name, round-number or future-sample input.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import numpy as np

from local_high_force import LocalForceResult, ORIGINAL_MODEL_SHA256, ROI, allocate_regions

METHOD = 'FRONT_REGIONAL_INDEPENDENT_V1'
SUPPORT_METHOD = 'THREE_BLOCK_TRACE_NEAREST_NEIGHBOR_V1'
FAMILIES = ('SINGLE20_RBF', 'DUAL15_20_RBF', 'SINGLE20_LINEAR_RBF')


def _array(value, shape, name):
    a = np.asarray(value, dtype=float)
    if a.shape != shape or not np.all(np.isfinite(a)):
        raise ValueError(f'{name} must be finite with shape {shape}')
    return a.copy()


def _scalar(value, name, *, positive=False):
    x = float(value)
    if not np.isfinite(x) or (positive and x <= 0):
        raise ValueError(f'{name} must be finite' + (' and positive' if positive else ''))
    return x


class _FrozenFamily:
    """A validated family evaluates state drift, never a held-output template."""

    def __init__(self, component, training_count):
        self.family = component['family']
        if self.family not in FAMILIES:
            raise ValueError('Unknown regional family')
        m = component['model']; em = m['embedding']
        if m.get('eligible') is not True:
            raise ValueError('Regional component is unavailable')
        self.dual = self.family == 'DUAL15_20_RBF'
        self.linear = self.family == 'SINGLE20_LINEAR_RBF'
        if self.dual:
            if em['kernel_family'] != 'RBF':
                raise ValueError('The declared dual regional family requires RBF')
            rank = em['actual_rank']
            if type(rank) is not int or not 1 <= rank <= min(training_count-2, 6):
                raise ValueError('Invalid dual embedding rank')
            self.centers = _array(em['block_centers3x24'], (3, 24), 'block centers')
            self.scales = _array(em['block_scales3'], (3,), 'block scales')
            self.pca_center = _array(em['pca_center72'], (72,), 'PCA center')
            self.pc = _array(em['pc_rankx72'], (rank, 72), 'PCA axes')
            self.score_scale = _array(em['score_scale'], (rank,), 'PCA scales')
            self.training_z = _array(em['training_z'], (training_count, rank), 'kernel inputs')
        else:
            if training_count < 4:
                raise ValueError('Single20 rank3 requires at least four training inputs')
            rank = 3
            self.centers = _array([em['block_A_center24'], em['block_B_center24']], (2, 24), 'block centers')
            self.scales = _array([em['block_A_scale'], em['block_B_scale']], (2,), 'block scales')
            self.pca_center = _array(em['pca_center48'], (48,), 'PCA center')
            self.pc = _array(em['pc3x48'], (3, 48), 'PCA axes')
            self.score_scale = _array(em['score_scale3'], (3,), 'PCA scales')
            self.training_z = _array(em['training_z3'], (training_count, 3), 'kernel inputs')
        if np.any(self.scales <= 0) or np.any(self.score_scale <= 0):
            raise ValueError('Embedding scales must be positive')
        if len(m['training_ids']) != training_count:
            raise ValueError('Component training count differs from common support')
        self.length = _scalar(em['kernel_length'], 'kernel length', positive=True)
        self.column_mean = _array(em['kernel_column_mean'], (training_count,), 'kernel column mean')
        self.grand_mean = _scalar(em['kernel_grand_mean'], 'kernel grand mean')
        self.beta_mean = _scalar(m['beta_training_mean'], 'beta intercept')
        if self.linear:
            self.feature_center = _array(m['linear_feature_center3'], (3,), 'linear feature center')
            self.linear_mag = _array(m['linear_magnetic_coef3x12'], (3, 12), 'linear magnetic coefficients')
            self.alpha_mag = _array(m['magnetic_residual_alpha_nx12'], (training_count, 12), 'magnetic residual coefficients')
            self.linear_beta = _array(m['linear_beta_coef3'], (3,), 'linear beta coefficients')
            self.alpha_beta = _array(m['beta_residual_alpha_n'], (training_count,), 'beta residual coefficients')
            self.beta_residual_mean = _scalar(m['beta_residual_mean'], 'beta residual intercept')
        else:
            self.alpha_mag = _array(m['magnetic_alpha_nx12'], (training_count, 12), 'magnetic coefficients')
            self.alpha_beta = _array(m['beta_alpha_n'], (training_count,), 'beta coefficients')

    def predict(self, blocks):
        inputs = blocks if self.dual else (blocks[0]+blocks[1], blocks[2])
        point = np.concatenate([(b-c)/s for b,c,s in zip(inputs,self.centers,self.scales)])
        z = ((point-self.pca_center) @ self.pc.T) / self.score_scale
        distance = np.linalg.norm(self.training_z-z, axis=1)
        kernel = np.exp(-.5*(distance/self.length)**2)
        kc = kernel-kernel.mean()-self.column_mean+self.grand_mean
        drift = kc @ self.alpha_mag
        beta = self.beta_mean + kc @ self.alpha_beta
        if self.linear:
            x = z-self.feature_center
            drift = x @ self.linear_mag + drift
            beta = self.beta_mean+self.beta_residual_mean+x @ self.linear_beta+kc @ self.alpha_beta
        # Audit-only RAW target means are deliberately not inference inputs.
        return drift, float(beta), z


class RegionalHighForceModel:
    method = METHOD
    requires_dual_anchor = True

    def __init__(self, payload, *, allow_development=False):
        if not isinstance(payload, dict):
            raise ValueError('A frozen regional profile object is required')
        document = json.loads(json.dumps(payload, allow_nan=False))
        model = document.get('model', document)
        if not isinstance(model, dict):
            raise ValueError('The frozen regional model must be an object')
        for item in (document, model):
            for field in ('method', 'schema'):
                if field in item and item[field] != METHOD:
                    raise ValueError('Frozen method does not match the regional adapter')
        if model.get('method', model.get('schema')) != METHOD:
            raise ValueError('A named regional schema is required')
        approved = (document.get('runtime_approved') is True
                    and not document.get('not_for_production', False)
                    and not model.get('not_for_production', False))
        if not approved and not allow_development:
            raise ValueError('Unapproved development profile cannot be enabled')
        if document.get('profile', 'front') != 'front':
            raise ValueError('Regional correction requires an explicit front profile')
        original_sha = document.get('original_model_sha256')
        if not isinstance(original_sha, str) or original_sha.lower() != ORIGINAL_MODEL_SHA256:
            raise ValueError('The frozen profile must name the unchanged original model SHA256')
        if model.get('eligible') is not True:
            raise ValueError('Regional model is unavailable')
        self.development_only = not approved
        self.signature = METHOD + ':' + hashlib.sha256(json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False
        ).encode('utf8')).hexdigest()
        try:
            self._parameters(model)
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError(f'Incomplete or invalid regional profile: {exc}') from exc

    def _parameters(self, model):
        if model['required_anchor_old_output_indices'] != [15., 20.]:
            raise ValueError('Regional model requires the past15 and20 output anchors')
        if model.get('beta_limits', [.5, 1.]) != [.5, 1.]:
            raise ValueError('Invalid beta limits')
        self.pre_anchor = _array(model['pre_anchor12'], (12,), 'pre anchor')
        support = model['support_model']
        if support['method'] != SUPPORT_METHOD:
            raise ValueError('Unknown regional input support method')
        points = np.asarray(support['training_points_normalized72'], dtype=float)
        if points.ndim != 2 or points.shape[1] != 72 or len(points) < 3 or not np.all(np.isfinite(points)):
            raise ValueError('Common support requires at least three finite72-axis points')
        self.support_points = points.copy(); n = len(points)
        if support['training_count'] != n or len(model['training_ids']) != n:
            raise ValueError('Common support training count differs from regional model')
        self.support_ids = support.get('training_ids')
        if self.support_ids is not None and self.support_ids != model['training_ids']:
            raise ValueError('Common support and regional model training IDs differ')
        self.support_centers = _array(support['block_centers3x24'], (3, 24), 'support centers')
        self.support_scales = _array(support['block_scales3'], (3,), 'support scales')
        self.support_threshold = _scalar(support['threshold'], 'support threshold')
        if np.any(self.support_scales <= 0) or self.support_threshold < 0:
            raise ValueError('Invalid common support scale or threshold')
        components = model['magnetic_components']
        if len(components) != 4:
            raise ValueError('Exactly four regional magnetic components are required')
        self.components = []
        for index, component in enumerate(components):
            if component['sensor_index'] != index+2 or component['roi_columns'] != list(range(index*3,index*3+3)):
                raise ValueError('Regional component mapping must be orderedS3-S6 xyz')
            self.components.append(_FrozenFamily(component,n))
        self.beta_component = _FrozenFamily(model['beta_component'],n)
        for component in [*components,model['beta_component']]:
            if component['model']['training_ids'] != model['training_ids']:
                raise ValueError('Regional components must share the declared training inputs')

    @classmethod
    def load(cls, path, *, allow_development=False):
        return cls(json.loads(Path(path).read_text(encoding='utf8')), allow_development=allow_development)

    def _support(self, blocks):
        query = np.concatenate([(b-c)/s for b,c,s in zip(blocks,self.support_centers,self.support_scales)])
        distances = np.linalg.norm(self.support_points-query, axis=1)
        index = int(np.argmin(distances)); distance = float(distances[index])
        supported = bool(np.isfinite(distance) and distance <= self.support_threshold)
        return dict(supported=supported, reason='SUPPORTED' if supported else 'OUTSIDE_INPUT_NEIGHBORHOOD',
                    distance=distance if np.isfinite(distance) else None, threshold=self.support_threshold,
                    nearest_training_index=index,
                    nearest_training_id=self.support_ids[index] if self.support_ids is not None else None)

    def predict(self, delta24, old_force, tare24, noise_floor, state, *, timestamp=None):
        """Pure inference; low force returns before inspecting state or tare."""
        try:
            delta = _array(delta24,(24,),'current delta'); floor = _array(noise_floor,(8,),'noise floor')
            old = _scalar(old_force,'original force')
            original_zones = allocate_regions(delta,old,floor)
        except (ValueError, TypeError, OverflowError):
            return LocalForceResult(None,None,None,None,None,None,'UNAVAILABLE','INVALID_CURRENT_INPUT',
                                    profile='front',signature=self.signature)
        original = dict(original_total_n=old,total_n=old,original_zones=original_zones,
                        zones=original_zones.copy(),original_delta24=delta,corrected_delta24=delta.copy(),
                        status='BYPASS',profile='front',signature=self.signature)
        if old <= 20:
            return LocalForceResult(**original,reason='LOW_FORCE_IDENTITY')
        if state is None or getattr(state,'response24',None) is None or getattr(state,'response15',None) is None:
            return LocalForceResult(**original,reason='MISSING_DUAL_PAST_ANCHORS')
        try:
            now = _scalar(timestamp,'acquisition time')
            time15 = _scalar(state.available_time15,'anchor15 time')
            time20 = _scalar(state.available_time,'anchor20 time')
            if time15 > time20 or time20 > now:
                return LocalForceResult(**original,reason='ANCHOR_NOT_YET_AVAILABLE_OR_UNORDERED')
            tare = _array(tare24,(24,),'tare'); quiet = _array(state.quiet_raw24,(24,),'quiet')
            raw15 = _array(state.raw15,(24,),'RAW15'); raw20 = _array(state.rising_raw24,(24,),'RAW20')
            response15 = _array(state.response15,(24,),'response15')
            response20 = _array(state.response24,(24,),'response20')
            if not (np.allclose(response15,raw15-quiet,rtol=1e-12,atol=1e-9)
                    and np.allclose(response20,raw20-quiet,rtol=1e-12,atol=1e-9)):
                return LocalForceResult(**original,reason='INCONSISTENT_DUAL_ANCHOR_COORDINATES')
            blocks = (response15,raw20-raw15,quiet-tare)
            if not all(np.all(np.isfinite(b)) for b in blocks):
                raise ValueError('Invalid past input subtraction')
        except (ValueError, TypeError, AttributeError, OverflowError):
            return LocalForceResult(**original,reason='INVALID_OR_MISSING_PAST_INPUT')
        with np.errstate(over='ignore',invalid='ignore',divide='ignore',under='ignore'):
            support = self._support(blocks)
            if not support['supported']:
                return LocalForceResult(**original,reason=support['reason'],support=support)
            drift = np.empty(12); features = []
            for i,component in enumerate(self.components):
                value,_,z = component.predict(blocks); columns = slice(i*3,i*3+3)
                drift[columns] = value[columns]; features.extend(z.tolist())
            _,beta_unlimited,z = self.beta_component.predict(blocks); features.extend(z.tolist())
            correction = drift+blocks[2][ROI]-self.pre_anchor
            if not np.isfinite(beta_unlimited) or not np.all(np.isfinite(correction)) or not np.all(np.isfinite(features)):
                return LocalForceResult(**original,reason='NONFINITE_MODEL_OUTPUT',support=support)
            beta = float(np.clip(beta_unlimited,.5,1.))
            corrected = delta.copy(); corrected[ROI] -= np.clip((old-20.)/5.,0.,1.)*correction
            total = float(20.+beta*(old-20.))
        try:
            zones = allocate_regions(corrected,total,floor)
        except ValueError:
            return LocalForceResult(**original,reason='NONFINITE_MODEL_OUTPUT',support=support)
        warnings = []
        old_vectors,new_vectors = delta.reshape(8,3),corrected.reshape(8,3)
        if np.any(np.sum(old_vectors[2:6]*new_vectors[2:6],axis=1)<0):warnings.append('ROI_VECTOR_DIRECTION_REVERSED')
        if np.any(np.linalg.norm(new_vectors[2:6],axis=1)<=floor[2:6]):warnings.append('ROI_AT_OR_BELOW_NOISE_FLOOR')
        if beta != beta_unlimited:warnings.append('BETA_LIMITED')
        if self.development_only:warnings.append('DEVELOPMENT_PROFILE_NOT_ACCEPTED')
        original.update(total_n=total,zones=zones,corrected_delta24=corrected,status='APPLIED')
        return LocalForceResult(**original,reason='SUPPORTED_CAUSAL_REGIONAL_STATE',applied=True,
                                support=support,beta=beta,state_features=tuple(features),
                                magnetic_correction12=correction.copy(),warnings=tuple(warnings))
