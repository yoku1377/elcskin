"""Frozen two-domain rear25 candidate. No online fit or nominal-load input.

Legacy states retain the v5.3.9 formula. The updated domain uses recorded
past15/quiet state, input-only bounds, bidirectional bounded gain, and a fixed
legacy reference direction. Development acceptance remains explicitly pending.
"""
import hashlib
import json
from dataclasses import replace
import numpy as np
from local_high_force import LocalForceResult, ORIGINAL_MODEL_SHA256, _array, allocate_regions
from back25_smooth_force import Back25SmoothForceModel

METHOD='BACK25_TWO_DOMAIN_CAUSAL_CANDIDATE_V3'


class Back25DomainForceModel:
    method=METHOD
    profile='back'
    requires_dual_anchor=False
    acceptance_passed=False
    validation_status='DEVELOPMENT_REPLAY_PASS_HELD_OUT_NOT_ALL_PASS_FRESH_ACCEPTANCE_PENDING'

    def __init__(self,payload,*,allow_development=False):
        d=json.loads(json.dumps(payload,allow_nan=False))
        if (d.get('method')!=METHOD or d.get('profile')!='back'
                or d.get('original_model_sha256')!=ORIGINAL_MODEL_SHA256
                or d.get('acceptance_passed') is not False):
            raise ValueError('Invalid or mislabelled rear25 domain candidate')
        self.development_only=not (d.get('runtime_approved') is True and not d.get('not_for_production',False))
        if self.development_only and not allow_development:raise ValueError('Rear25 domain candidate not approved for runtime')
        self.legacy=Back25SmoothForceModel(d['legacy_profile'],allow_development=allow_development)
        self.signature=METHOD+':'+hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        m=d['updated_domain'];self.model=m
        if m.get('spatial_transition_n')!=2. or m.get('beta_limits')!=[.5,1.35]:raise ValueError('Unsupported rear25 mapping boundaries')
        self.reference=_array(m['reference_direction'],(24,),'reference direction')
        self.centers=_array(m['guard_centers'],(2,24),'guard centers')
        self.scales=_array(m['guard_scales'],(2,),'guard scales')
        if np.any(self.scales<=0):raise ValueError('Invalid guard scale')
        n=len(m['training_x'])
        if not 4<=n<=1000:raise ValueError('Invalid training count')
        self.points=_array(m['training_x'],(n,48),'training state')
        self.pc=_array(m['pc'],(3,48),'domain PCA')
        if not np.allclose(self.pc@self.pc.T,np.eye(3),atol=1e-8,rtol=0):raise ValueError('Invalid domain PCA')
        self.thresholds=np.asarray([m['neighbor'],m['spatial_neighbor'],m['residual']],float)
        if not np.all(np.isfinite(self.thresholds)) or np.any(self.thresholds<0):raise ValueError('Invalid domain bounds')
        self.total=self._component(m['total'],n,1)
        self.spatial=self._component(m['spatial'],n,24)

    @staticmethod
    def _component(m,n,outputs):
        mode=m.get('mode');kind=m.get('kind')
        if mode not in {'raw48','unit24','unit_quiet48'} or kind not in {'rbf','linear'}:raise ValueError('Invalid regression method')
        width=24 if mode=='unit24' else 48
        result=dict(mode=mode,kind=kind,centers=_array(m['centers'],(width,),'feature centers'),
                    scales=_array(m['scales'],(width,),'feature scales'),x=_array(m['x'],(n,width),'regression state'),
                    center=_array(m['center'],(outputs,),'regression center'),
                    coefficient=_array(m['coefficient'],(n,outputs),'regression coefficient'),length=float(m['length']))
        if np.any(result['scales']<=0) or not np.isfinite(result['length']) or result['length']<=0:raise ValueError('Invalid regression scale')
        return result

    @staticmethod
    def _evaluate(m,x):
        if m['mode']=='raw48':raw=x
        else:
            unit=x[:24]/max(np.linalg.norm(x[:24]),1e-12)
            raw=unit if m['mode']=='unit24' else np.r_[unit,x[24:]]
        q=(raw-m['centers'])/m['scales']
        k=np.exp(-.5*np.sum((m['x']-q)**2,axis=1)/m['length']**2) if m['kind']=='rbf' else m['x']@q
        return m['center']+k@m['coefficient']

    @staticmethod
    def correct_total(old,beta):
        if old<=20. or old>=35.:return float(old)
        if old<=31.:return float(20.+beta*(old-20.))
        junction=20.+beta*11.
        return float(junction+(35.-junction)*(old-31.)/4.)

    def predict(self,delta24,old_force,tare24,noise_floor,state,*,timestamp=None):
        prior=self.legacy.predict(delta24,old_force,tare24,noise_floor,state,timestamp=timestamp)
        if prior.applied:
            return replace(prior,signature=self.signature,support={**(prior.support or {}),'calibration_domain':'legacy_v539'})
        if prior.reason!='OUTSIDE_BACK25_CALIBRATION_DOMAIN':return replace(prior,signature=self.signature)
        try:
            delta=_array(delta24,(24,),'current delta');tare=_array(tare24,(24,),'tare')
            quiet=_array(state.quiet_raw24,(24,),'quiet');a=_array(state.response24,(24,),'past15')
            floor=_array(noise_floor,(8,),'floor');old=float(old_force)
            if not np.isfinite(old) or old<0:raise ValueError('Invalid force')
            x=np.r_[a,quiet-tare]
            q=((x.reshape(2,24)-self.centers)/self.scales[:,None]).reshape(48)
            z=q@self.pc.T
            distances=np.array([np.linalg.norm(self.points-q,axis=1).min(),
                                np.linalg.norm((self.points-q)@self.pc.T,axis=1).min(),
                                np.linalg.norm(q-z@self.pc)])
            checks={name:bool(np.isfinite(v) and v<=limit) for name,v,limit in zip(
                ['total_neighbor','spatial_neighbor','spatial_residual'],distances,self.thresholds)}
            support=dict(calibration_domain='updated_20261016',supported=all(checks.values()),checks=checks,
                         distances=[float(v) if np.isfinite(v) else None for v in distances],
                         thresholds=self.thresholds.tolist(),legacy_support=prior.support)
            if not all(checks.values()):return replace(prior,signature=self.signature,support=support)
            beta_raw=float(self._evaluate(self.total,x).item());beta=float(np.clip(beta_raw,.5,1.35))
            shift=self._evaluate(self.spatial,x)-self.reference;shift_norm=float(np.linalg.norm(shift))
            if not np.isfinite(beta_raw) or not np.isfinite(shift_norm):raise ValueError('Nonfinite regression output')
            shift*=min(1.,.5/max(shift_norm,1e-12))
            blend=float(np.clip((old-20.)/2.,0.,1.))
            corrected=delta-blend*((quiet-tare)+np.linalg.norm(delta-(quiet-tare))*shift)
            total=self.correct_total(old,beta)
            if not np.all(np.isfinite(corrected)) or not np.isfinite(total) or not 0<=total<=35:raise ValueError('Invalid model output')
            zones=allocate_regions(corrected,total,floor)
        except (TypeError,ValueError,AttributeError,OverflowError):
            return replace(prior,signature=self.signature,reason='NONFINITE_MODEL_OUTPUT')
        support.update(beta_unlimited=beta_raw,spatial_blend=blend,relative_direction_shift=shift_norm)
        return LocalForceResult(old,total,prior.original_zones,zones,delta.copy(),corrected,'APPLIED',
            'SUPPORTED_UPDATED_BACK25_DOMAIN',applied=True,profile='back',signature=self.signature,
            support=support,beta=beta,state_features=tuple(q),
            warnings=('NOMINAL_REFERENCE_ONLY','DEVELOPMENT_HELD_OUT_NOT_ALL_PASS','FRESH_ACCEPTANCE_PENDING'))
