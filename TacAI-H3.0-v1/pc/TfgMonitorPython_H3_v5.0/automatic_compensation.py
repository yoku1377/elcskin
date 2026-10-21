"""Automatically choose exactly one supported frozen calibration model.

No fitting, target force, filename, force averaging or extrapolation is used.
The15 and20 anchors remain distinct; ambiguous or failed models never apply.
"""
from dataclasses import replace
import hashlib
import json
import numpy as np

from local_high_force import LocalForceResult, _array, allocate_regions
from local_state import RisingState

METHOD='H3_AUTO_SUPPORTED_FRONT_BACK_V1'


def past15_state(state):
    """Convert an observed dual-anchor state to the original15-only contract."""
    if state is None:return None
    if not hasattr(state,'response15'):return state
    return RisingState(
        quiet_raw24=state.quiet_raw24,rising_raw24=state.raw15,response24=state.response15,
        available_time=state.available_time15,first_source=state.first_source15,
        second_source=state.second_source15,interpolation_fraction=state.interpolation_fraction15,
        reason='AUTO_OBSERVED_PAST15_STATE')


class AutomaticCompensationModel:
    method=METHOD
    profile='auto'
    requires_dual_anchor=True
    acceptance_passed=False
    validation_status='FROZEN_CANDIDATE_ROUTING_FRESH_FORCE_ACCEPTANCE_PENDING'

    def __init__(self,front=None,back=None):
        self.models={key:model for key,model in [('front',front),('back',back)] if model is not None}
        if not self.models:raise ValueError('Automatic compensation requires calibrated models')
        for key,model in self.models.items():
            if getattr(model,'profile','front')!=key or not getattr(model,'signature',None):
                raise ValueError('Automatic calibration profile identity mismatch')
            if getattr(model,'development_only',True):raise ValueError('Automatic calibration requires runtime-approved models')
        self.development_only=False
        self.signature=METHOD+':'+hashlib.sha256(json.dumps(
            {key:model.signature for key,model in self.models.items()},sort_keys=True).encode()).hexdigest()

    def predict(self,delta24,old_force,tare24,noise_floor,state,*,timestamp=None):
        try:
            delta=_array(delta24,(24,),'auto current delta');floor=_array(noise_floor,(8,),'auto floor')
            old=float(old_force);original=allocate_regions(delta,old,floor)
        except (ValueError,TypeError,OverflowError):
            return LocalForceResult(None,None,None,None,None,None,'UNAVAILABLE','INVALID_CURRENT_INPUT',
                                    profile=self.profile,signature=self.signature)
        base=LocalForceResult(old,old,original,original.copy(),delta,delta.copy(),'BYPASS','LOW_FORCE_IDENTITY',
                              profile=self.profile,signature=self.signature)
        if old<=20.:return base
        if old>=35.:return replace(base,reason='AUTO_FORCE_SATURATED')
        results={};candidates={};failed=False
        for key,model in self.models.items():
            try:
                input_state=state if getattr(model,'requires_dual_anchor',False) else past15_state(state)
                r=model.predict(delta,old,tare24,floor,input_state,timestamp=timestamp)
                candidates[key]=dict(applied=r.applied,reason=r.reason,signature=model.signature,support=r.support)
                if r.applied:
                    _array(r.zones,(8,),'auto corrected regions');_array(r.corrected_delta24,(24,),'auto corrected delta')
                    if (r.total_n is None or not np.isfinite(r.total_n) or not 0<=r.total_n<35.
                            or np.any(r.zones<0) or not np.isclose(r.zones.sum(),r.total_n,rtol=0,atol=1e-8)):
                        raise ValueError('Invalid calibrated output')
                    results[key]=r
                elif r.status=='UNAVAILABLE' or r.reason in {'NONFINITE_MODEL_OUTPUT','INVALID_CURRENT_INPUT'}:
                    failed=True
            except (ValueError,TypeError,AttributeError,OverflowError):
                failed=True;candidates[key]=dict(applied=False,reason='AUTO_MODEL_FAILURE',signature=model.signature)
        support=dict(automatic_compensation=True,available_profiles=list(self.models),candidates=candidates,
                     selected_profile=None,online_fitting=False)
        if failed:return replace(base,reason='AUTO_MODEL_FAILURE',support=support)
        if len(results)>1:return replace(base,reason='AUTO_AMBIGUOUS_PROFILES',support=support)
        if not results:
            missing=any('MISSING' in r['reason'] or 'ANCHOR_NOT' in r['reason'] for r in candidates.values())
            return replace(base,reason='AUTO_NO_SUPPORTED_PROFILE',support=support,
                           warnings=('AUTO_PARTIAL_HISTORY' if missing else 'AUTO_INPUT_OUTSIDE_CALIBRATION',))
        key,r=next(iter(results.items()))
        support.update(selected_profile=key,selected_method=self.models[key].method,
                       selected_signature=self.models[key].signature,selected_support=r.support,
                       total_correction_n=float(r.total_n-old),
                       max_region_correction_n=float(np.max(np.abs(r.zones-original))))
        return replace(r,profile=self.profile,signature=self.signature,support=support,
                       reason='AUTO_SUPPORTED_'+key.upper(),warnings=(*r.warnings,'AUTO_CALIBRATED_CANDIDATE'))
