"""Explicit rear25 total calibration, using past state and current force only.

The magnetic RAW coordinates and their eight allocation proportions are kept.
The position calibration prior can be used outside a local RBF neighborhood;
that extrapolation is exposed, and geometry limits still apply. No acceptance
or full-range physical accuracy is implied by loading a runtime candidate.
"""
import hashlib
import json
import numpy as np

from local_high_force import LocalForceResult,ORIGINAL_MODEL_SHA256,allocate_regions
from regional_high_force import _array,_scalar

METHOD='BACK_PAST_STATE_TOTAL_RBF_V1'
SUPPORT_METHOD='TWO_BLOCK_TRACE_STANDARDIZED_NEAREST_NEIGHBOR_V1'


class BackTotalForceModel:
    method=METHOD
    profile='back'
    requires_dual_anchor=True

    def __init__(self,payload,*,allow_development=False):
        document=json.loads(json.dumps(payload,allow_nan=False));m=document.get('model',document)
        for item in (document,m):
            for key in ('schema','method'):
                if key in item and item[key]!=METHOD:raise ValueError('Rear total schema mismatch')
        if m.get('method',m.get('schema'))!=METHOD:raise ValueError('Explicit rear total schema required')
        if document.get('profile')!='back':raise ValueError('Rear total calibration requires explicit back profile')
        if document.get('original_model_sha256')!=ORIGINAL_MODEL_SHA256:raise ValueError('Rear calibration original model SHA mismatch')
        approved=(document.get('runtime_approved') is True and not document.get('not_for_production',False) and not m.get('not_for_production',False))
        if not approved and not allow_development:raise ValueError('Rear calibration has not been approved for runtime')
        self.development_only=not approved;self.acceptance_passed=document.get('acceptance_passed') is True
        self.validation_status=document.get('validation_status','CALIBRATION_VALIDATION_PENDING')
        self.signature=METHOD+':'+hashlib.sha256(json.dumps(document,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()
        try:self._parameters(m)
        except (KeyError,TypeError,OverflowError) as error:raise ValueError(f'Invalid rear calibration: {error}') from error

    def _parameters(self,m):
        if m.get('eligible') is not True:raise ValueError('Rear calibration is unavailable')
        if (m['lower_identity_original_n'],m['local_upper_original_n'],m['original_max_n'])!=(20.,31.,35.):
            raise ValueError('Unsupported rear mapping boundaries')
        if m['beta_limits']!=[.5,1.]:raise ValueError('Invalid rear gain limits')
        if m['neighborhood_policy']!='RBF_STATE_RESIDUAL_WITH_POSITION_CALIBRATION_PRIOR':raise ValueError('Invalid rear support policy')
        if m['geometry_angle_degrees']!=5. or m['geometry_norm_ratio']!=[.9,1.1]:raise ValueError('Invalid rear geometry limits')
        self.geometry_unit=_array(m['geometry_unit24'],(24,),'rear geometry unit')
        if abs(np.linalg.norm(self.geometry_unit)-1)>1e-9:raise ValueError('Rear geometry direction must have unit length')
        self.geometry_norm=_scalar(m['geometry_reference_norm'],'rear response norm',positive=True)
        sm=m['support_model'];points=np.asarray(sm['training_points_normalized48'],float)
        if sm['method']!=SUPPORT_METHOD or points.ndim!=2 or points.shape[1]!=48 or len(points)<4 or not np.all(np.isfinite(points)):
            raise ValueError('Invalid rear RBF inputs')
        n=len(points)
        if sm['training_count']!=n or sm['training_ids']!=m['training_ids'] or len(m['training_ids'])!=n:
            raise ValueError('Rear training and support IDs differ')
        self.support_ids=list(m['training_ids']);self.points=points.copy()
        self.center_a=_array(sm['load_center24'],(24,),'rear load center');self.center_b=_array(sm['quiet_center24'],(24,),'rear quiet center')
        self.scale_a=_scalar(sm['load_scale'],'rear load scale',positive=True);self.scale_b=_scalar(sm['quiet_scale'],'rear quiet scale',positive=True)
        self.threshold=_scalar(sm['threshold'],'rear neighbor threshold')
        if self.threshold<0:raise ValueError('Negative rear support distance')
        self.length=_scalar(m['kernel_length'],'rear kernel length',positive=True)
        self.coefficient=_array(m['coefficient'],(n,),'rear RBF coefficients')
        self.beta_center=_scalar(m['beta_center'],'rear gain center')

    @staticmethod
    def correct_total(old,beta):
        if old<=20. or old>=35.:return float(old)
        if old<=31.:return float(20.+beta*(old-20.))
        junction=20.+beta*11.
        return float(junction+(35.-junction)*(old-31.)/4.)

    def predict(self,delta24,old_force,tare24,noise_floor,state,*,timestamp=None):
        try:
            delta=_array(delta24,(24,),'rear current delta');floor=_array(noise_floor,(8,),'rear noise floor');old=_scalar(old_force,'rear original force')
            original_zones=allocate_regions(delta,old,floor)
        except (ValueError,TypeError,OverflowError):
            return LocalForceResult(None,None,None,None,None,None,'UNAVAILABLE','INVALID_CURRENT_INPUT',profile='back',signature=self.signature)
        values=dict(original_total_n=old,total_n=old,original_zones=original_zones,zones=original_zones.copy(),original_delta24=delta,
                    corrected_delta24=delta.copy(),status='BYPASS',profile='back',signature=self.signature)
        if old<=20.:return LocalForceResult(**values,reason='LOW_FORCE_IDENTITY')
        if state is None or getattr(state,'response24',None) is None or getattr(state,'response15',None) is None:
            return LocalForceResult(**values,reason='MISSING_DUAL_PAST_ANCHORS')
        try:
            now=_scalar(timestamp,'rear acquisition time');t15=_scalar(state.available_time15,'rear past15 time');t20=_scalar(state.available_time,'rear past20 time')
            if t15>t20 or t20>now:return LocalForceResult(**values,reason='ANCHOR_NOT_YET_AVAILABLE_OR_UNORDERED')
            tare=_array(tare24,(24,),'rear tare');quiet=_array(state.quiet_raw24,(24,),'rear quiet');raw20=_array(state.rising_raw24,(24,),'rear RAW20')
            a=_array(state.response24,(24,),'rear response20');b=quiet-tare
            if not np.allclose(raw20-quiet,a,rtol=1e-12,atol=1e-9):return LocalForceResult(**values,reason='INCONSISTENT_DUAL_ANCHOR_COORDINATES')
        except (ValueError,TypeError,AttributeError,OverflowError):return LocalForceResult(**values,reason='INVALID_OR_MISSING_PAST_INPUT')
        with np.errstate(over='ignore',invalid='ignore',divide='ignore',under='ignore'):
            norm=float(np.linalg.norm(a));ratio=norm/self.geometry_norm
            cosine=float((a/norm)@self.geometry_unit) if norm>0 else -1.
            query=np.r_[(a-self.center_a)/self.scale_a,(b-self.center_b)/self.scale_b]
            distances=np.linalg.norm(self.points-query,axis=1);nearest=int(np.argmin(distances));distance=float(distances[nearest])
            local=bool(np.isfinite(distance) and distance<=self.threshold)
            geometry=bool(np.isfinite(ratio) and np.isfinite(cosine) and cosine>=np.cos(np.radians(5.)) and .9<=ratio<=1.1)
            support=dict(supported=geometry,reason='SUPPORTED_BACK_GEOMETRY' if geometry else 'OUTSIDE_BACK_CALIBRATION_GEOMETRY',
                distance=distance if np.isfinite(distance) else None,threshold=self.threshold,nearest_training_id=self.support_ids[nearest],
                local_neighborhood_supported=local,angle_degrees=float(np.degrees(np.arccos(np.clip(cosine,-1,1)))) if np.isfinite(cosine) else None,
                norm_ratio=ratio if np.isfinite(ratio) else None,calibration_prior_used_outside_neighborhood=geometry and not local)
            if not geometry:return LocalForceResult(**values,reason=support['reason'],support=support)
            beta_raw=float(self.beta_center+np.exp(-.5*(distances/self.length)**2)@self.coefficient)
            if not np.isfinite(beta_raw) or not np.all(np.isfinite(query)):
                return LocalForceResult(**values,reason='NONFINITE_MODEL_OUTPUT',support=support)
            beta=float(np.clip(beta_raw,.5,1.));total=self.correct_total(old,beta)
        try:zones=allocate_regions(delta,total,floor)
        except (ValueError,TypeError,OverflowError):return LocalForceResult(**values,reason='NONFINITE_MODEL_OUTPUT',support=support)
        warnings=[]
        if not self.acceptance_passed:warnings.append('CALIBRATED_CANDIDATE_ACCEPTANCE_PENDING')
        if not local:warnings.append('RBF_CALIBRATION_PRIOR_OUTSIDE_NEIGHBORHOOD')
        if old>31.:warnings.append('OUTSIDE_BACK25_VALIDATED_FORCE_RANGE')
        if beta_raw!=beta:warnings.append('BETA_LIMITED')
        if self.development_only:warnings.append('DEVELOPMENT_PROFILE_NOT_ACCEPTED')
        values.update(total_n=total,zones=zones,status='APPLIED')
        return LocalForceResult(**values,reason='SUPPORTED_CAUSAL_BACK_TOTAL_STATE',applied=True,support=support,beta=beta,
          state_features=tuple(query.tolist()),warnings=tuple(warnings))
