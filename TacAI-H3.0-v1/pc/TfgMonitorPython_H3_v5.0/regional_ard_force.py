"""Frozen per-Hall ARD inference, with explicit runtime approval.

Runtime approval can designate a calibrated experimental candidate. It does not
imply independent acceptance; no coefficients or approval are embedded here.
"""
from dataclasses import replace
import hashlib
import json
import numpy as np

from local_high_force import ORIGINAL_MODEL_SHA256, allocate_regions
from regional_high_force import RegionalHighForceModel, _array, _scalar, SUPPORT_METHOD

METHOD = 'FRONT_REGIONAL_ARD_RBF_V1'
SIX_HALL_METHOD = 'FRONT_REGIONAL_ARD_RBF_SIX_HALL_V2'


class _FrozenARDGP:
    def __init__(self, component, embedding, n, *, hall=None):
        if component['family'] != 'ARD_RBF':
            raise ValueError('Only the declared ARD RBF family is supported')
        self.embedding = embedding; self.hall = hall
        gp = component['gp']; rank = embedding['pc'].shape[0]
        self.lengths = _array(gp['lengths'],(rank,),'ARD lengths')
        if np.any(self.lengths <= 0):raise ValueError('ARD lengths must be positive')
        width = 1 if hall is None else 3
        self.coefficient = _array(gp['coefficient'],(n,width),'ARD coefficients')
        center = _array(gp['center_for_training_prediction'],(width,),'GP target center')
        self.beta_center = float(center[0]) if hall is None else None

    def predict(self, blocks):
        em = self.embedding
        point = np.concatenate([(b-c)/s for b,c,s in zip(blocks,em['centers'],em['scales'])])
        z = ((point-em['pca_center']) @ em['pc'].T)/em['score_scale']
        # ARD query is deliberately uncentered. No shared/global kernel length.
        kernel = np.exp(-.5*np.sum(((em['training_z']-z)/self.lengths)**2,axis=1))
        value = kernel @ self.coefficient
        magnetic = np.zeros(12)
        if self.hall is None:
            beta = self.beta_center+float(value[0])
        else:
            magnetic[self.hall*3:self.hall*3+3] = value
            beta = 0.  # This component contributes only its own magnetic triple.
        return magnetic,beta,z


class RegionalARDHighForceModel(RegionalHighForceModel):
    """Same causal/result contract as the regional adapter, different frozen kernel."""
    method = METHOD
    requires_dual_anchor = True

    def __init__(self,payload,*,allow_development=False):
        if not isinstance(payload,dict):raise ValueError('A frozen ARD profile object is required')
        document = json.loads(json.dumps(payload,allow_nan=False)); model = document.get('model',document)
        if not isinstance(model,dict):raise ValueError('The ARD model must be an object')
        for item in (document,model):
            for field in ('method','schema'):
                if field in item and item[field] != self.method:raise ValueError('ARD adapter method mismatch')
        if model.get('method',model.get('schema')) != self.method:raise ValueError('An explicit ARD schema is required')
        approved = (document.get('runtime_approved') is True and not document.get('not_for_production',False)
                    and not model.get('not_for_production',False))
        if not approved and not allow_development:raise ValueError('ARD profile requires explicit runtime approval')
        if document.get('profile','front') != 'front':raise ValueError('ARD correction requires explicit front profile')
        original_sha = document.get('original_model_sha256')
        if not isinstance(original_sha,str) or original_sha.lower() != ORIGINAL_MODEL_SHA256:
            raise ValueError('ARD profile must name the unchanged original model SHA256')
        if model.get('eligible') is not True:raise ValueError('ARD profile is unavailable')
        self.development_only = not approved
        self.acceptance_passed = document.get('acceptance_passed') is True
        self.validation_status = document.get('validation_status','CALIBRATION_VALIDATION_PENDING')
        self.release_stage = document.get('release_stage',document.get('approval_status',''))
        self.signature = self.method+':'+hashlib.sha256(json.dumps(document,ensure_ascii=False,sort_keys=True,
          separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()
        try:self._parameters(model)
        except (KeyError,TypeError,OverflowError) as exc:raise ValueError(f'Invalid ARD profile: {exc}') from exc

    def _parameters(self,model):
        if self.method == METHOD and ('additional_S1_S2_components' in model or 'additional_S1_S2_pre_anchor6' in model):
            raise ValueError('Additional Hall calibration requires the explicit six-Hall schema')
        if model.get('required_anchor_old_output_indices',[15.,20.]) != [15.,20.]:
            raise ValueError('ARD requires both past15 and20 output anchors')
        if model.get('beta_limits',[.5,1.]) != [.5,1.]:raise ValueError('Invalid beta limits')
        self.pre_anchor = _array(model['pre_anchor12'],(12,),'pre anchor')
        support = model['support_model']
        if support['method'] != SUPPORT_METHOD:raise ValueError('Unknown common support method')
        points = np.asarray(support['training_points_normalized72'],float)
        if points.ndim != 2 or points.shape[1] != 72 or len(points)<3 or not np.all(np.isfinite(points)):
            raise ValueError('ARD support requires finite paired72-axis inputs')
        self.support_points = points.copy(); n = len(points)
        if support['training_count'] != n or len(model['training_ids']) != n:raise ValueError('ARD training count mismatch')
        self.support_ids = support.get('training_ids')
        if self.support_ids is not None and self.support_ids != model['training_ids']:raise ValueError('ARD support IDs mismatch')
        self.support_centers = _array(support['block_centers3x24'],(3,24),'support centers')
        self.support_scales = _array(support['block_scales3'],(3,),'support scales')
        self.support_threshold = _scalar(support['threshold'],'support threshold')
        if np.any(self.support_scales<=0) or self.support_threshold<0:raise ValueError('Invalid ARD input support')
        em = model['embedding']; rank = em['actual_rank']
        if type(rank) is not int or not 1<=rank<=min(n-2,6):raise ValueError('Invalid ARD embedding rank')
        embedding = dict(centers=_array(em['block_centers3x24'],(3,24),'embedding centers'),
          scales=_array(em['block_scales3'],(3,),'embedding scales'),
          pca_center=_array(em['pca_center72'],(72,),'PCA center'),pc=_array(em['pc_rankx72'],(rank,72),'PCA axes'),
          score_scale=_array(em['score_scale'],(rank,),'PCA scales'),
          training_z=_array(em['training_z'],(n,rank),'training PC inputs'))
        if np.any(embedding['scales']<=0) or np.any(embedding['score_scale']<=0):raise ValueError('ARD embedding scales must be positive')
        self.embedding = embedding
        components = model['magnetic_components']
        if len(components)!=4:raise ValueError('Exactly four ARD Hall models are required')
        self.components=[]
        for index,component in enumerate(components):
            if component['sensor_name'] != f'S{index+3}' or component['roi_columns'] != list(range(index*3,index*3+3)):
                raise ValueError('ARD Hall mapping must be S3-S6 xyz')
            self.components.append(_FrozenARDGP(component,embedding,n,hall=index))
        self.beta_component = _FrozenARDGP(model['beta_component'],embedding,n)

    def predict(self,*args,**kwargs):
        result = super().predict(*args,**kwargs)
        if result.applied and not self.acceptance_passed:
            return replace(result,warnings=(*result.warnings,'CALIBRATED_CANDIDATE_ACCEPTANCE_PENDING'))
        return result


class SixHallARDHighForceModel(RegionalARDHighForceModel):
    """Add S1/S2 drift calibration, retaining the frozen S3-S6 and force models.

    The base result's magnetic_correction12 continues to describe S3-S6. The
    complete result and CSV carry all 24 original and corrected coordinates.
    """
    method = SIX_HALL_METHOD

    def _parameters(self,model):
        super()._parameters(model)
        self.additional_pre_anchor = _array(model['additional_S1_S2_pre_anchor6'],(6,),'S1/S2 pre anchor')
        components = model['additional_S1_S2_components']
        if len(components) != 2:raise ValueError('Exactly two additional S1/S2 Hall models are required')
        self.additional_components = []
        for index,component in enumerate(components):
            if component['sensor_name'] != f'S{index+1}' or component['roi_columns'] != list(range(index*3,index*3+3)):
                raise ValueError('Additional ARD Hall mapping must be S1-S2 xyz')
            self.additional_components.append(_FrozenARDGP(component,self.embedding,len(self.support_points),hall=index))

    def predict(self,delta24,old_force,tare24,noise_floor,state,*,timestamp=None):
        result = super().predict(delta24,old_force,tare24,noise_floor,state,timestamp=timestamp)
        if not result.applied:return result
        # The parent has validated the complete causal state, timestamps, support
        # and current inputs. Both component groups apply atomically.
        blocks = (np.asarray(state.response15),np.asarray(state.rising_raw24)-np.asarray(state.raw15),
                  np.asarray(state.quiet_raw24)-np.asarray(tare24))
        with np.errstate(over='ignore',invalid='ignore',divide='ignore',under='ignore'):
            drift = np.empty(6);features = list(result.state_features)
            for index,component in enumerate(self.additional_components):
                value,_,z = component.predict(blocks);columns = slice(index*3,index*3+3)
                drift[columns] = value[columns];features.extend(z.tolist())
            correction = drift+blocks[2][:6]-self.additional_pre_anchor
            corrected = result.corrected_delta24.copy()
            corrected[:6] -= np.clip((float(old_force)-20.)/5.,0.,1.)*correction
        try:
            if not np.all(np.isfinite(correction)) or not np.all(np.isfinite(features)):
                raise ValueError('Nonfinite additional Hall prediction')
            zones = allocate_regions(corrected,result.total_n,noise_floor)
        except (ValueError,TypeError,OverflowError):
            return replace(result,total_n=result.original_total_n,zones=result.original_zones.copy(),
                corrected_delta24=result.original_delta24.copy(),applied=False,status='BYPASS',
                reason='NONFINITE_MODEL_OUTPUT',beta=None,state_features=(),magnetic_correction12=None,warnings=())
        warnings = list(result.warnings)
        before=result.original_delta24.reshape(8,3)[:2];after=corrected.reshape(8,3)[:2]
        if np.any(np.sum(before*after,axis=1)<0) and 'ROI_VECTOR_DIRECTION_REVERSED' not in warnings:
            warnings.append('ROI_VECTOR_DIRECTION_REVERSED')
        if np.any(np.linalg.norm(after,axis=1)<=np.asarray(noise_floor)[:2]) and 'ROI_AT_OR_BELOW_NOISE_FLOOR' not in warnings:
            warnings.append('ROI_AT_OR_BELOW_NOISE_FLOOR')
        return replace(result,corrected_delta24=corrected,zones=zones,state_features=tuple(features),warnings=tuple(warnings))
