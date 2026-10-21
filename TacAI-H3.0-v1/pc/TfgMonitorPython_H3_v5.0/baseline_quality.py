"""Frozen H3 input stability checks; these cannot prove physical zero load.

Limits are derived from de-duplicated H3 data, independently of FS metrics.
This module never fits force parameters, changes RAW, or excludes saved frames.
"""
from dataclasses import dataclass
from collections import Counter
import json
import numpy as np

POLICY_ID = 'H3_BASELINE_QUALITY_20261016_V1'
BASELINE_CSV_COLUMNS = ['baseline_quality_policy', 'baseline_quality_status',
                        'baseline_quality_reason', 'baseline_quality_json', 'host_tare_quality_json']


@dataclass(frozen=True)
class BaselinePolicy:
    variance_limit: float = 10100.
    max_step_limit: float = 190.
    min_samples: int = 10
    min_span_s: float = 1.
    max_gap_s: float = .25
    max_age_s: float = 10.
    policy_id: str = POLICY_ID

    def __post_init__(self):
        values=[self.variance_limit,self.max_step_limit,self.min_span_s,self.max_gap_s,self.max_age_s]
        if not np.all(np.isfinite(values)) or min(values)<=0 or self.min_samples<2:
            raise ValueError('Baseline quality limits must be finite and positive')


DEFAULT_POLICY = BaselinePolicy()


def evaluate_window(raw, times, *, now=None, policy=DEFAULT_POLICY):
    """Population variance sum in native RAW units squared; no force truth input."""
    result = dict(policy_id=policy.policy_id, status='WAITING', reason='BASELINE_INSUFFICIENT_HISTORY',
                  variance_limit=policy.variance_limit, max_step_limit=policy.max_step_limit,
                  physical_zero_confirmed=False)
    try:
        a=np.asarray(raw,float);t=np.asarray(times,float)
        if a.ndim!=2 or a.shape[1]!=24 or t.shape!=(len(a),) or not np.all(np.isfinite(a)) or not np.all(np.isfinite(t)):
            raise ValueError('Invalid baseline array')
        result['sample_count']=len(a)
        if len(a)==0:return result
        age=0. if now is None else float(now)-float(t[-1])
        if not np.isfinite(age):raise ValueError('Invalid baseline clock')
        result.update(first_time=float(t[0]),last_time=float(t[-1]),span_s=float(t[-1]-t[0]),age_s=age)
        if len(a)<policy.min_samples or t[-1]-t[0]<policy.min_span_s-1e-6:return result
        gaps=np.diff(t)
        if np.any(gaps<=0) or np.any(gaps>policy.max_gap_s+1e-6):
            result.update(status='REJECTED',reason='BASELINE_NONCONTINUOUS');return result
        if age < -1e-6 or age>policy.max_age_s+1e-6:
            result.update(status='REJECTED',reason='BASELINE_STALE');return result
        with np.errstate(over='ignore',invalid='ignore'):
            variance=float(np.var(a,axis=0).sum())
            step=float(np.linalg.norm(np.diff(a,axis=0),axis=1).max())
            third=max(1,len(a)//3)
            drift=float(np.linalg.norm(a[-third:].mean(axis=0)-a[:third].mean(axis=0)))
        if not np.all(np.isfinite([variance,step,drift])):raise ValueError('Baseline overflow')
        result.update(variance_sum24=variance,max_step_norm24=step,drift_norm24=drift)
        reason=('BASELINE_VARIANCE_HIGH' if variance>policy.variance_limit else
                'BASELINE_STEP_HIGH' if step>policy.max_step_limit else 'BASELINE_STABLE')
        result.update(status='READY' if reason=='BASELINE_STABLE' else 'REJECTED',reason=reason)
    except (TypeError,ValueError,OverflowError):
        result.update(status='REJECTED',reason='BASELINE_INVALID_INPUT')
    return result


def quality_text(quality):
    if not quality:return '基线待检查：完全卸载后保持静止至少2秒'
    reason=quality.get('reason','')
    detail={
        'BASELINE_INSUFFICIENT_HISTORY':'静置基线不足：完全卸载后保持静止至少2秒',
        'BASELINE_VARIANCE_HIGH':'基线波动过大：完全卸载、保持静止至少2秒后重新加载',
        'BASELINE_STEP_HIGH':'基线出现突变：检查触碰或干扰，完全卸载静置后重新加载',
        'BASELINE_NONCONTINUOUS':'基线采样不连续：等待连续数据后重新加载',
        'BASELINE_STALE':'静置基线已过期：完全卸载静置后重新加载',
        'BASELINE_INVALID_INPUT':'基线数据异常：检查采集并重新空载置零',
        'BASELINE_HISTORY_RESET':'基线历史已重置：完全卸载静置后重新加载',
    }.get(reason,'基线待检查：完全卸载后保持静止至少2秒')
    if quality.get('status')=='READY':detail='基线稳定性检查通过；采集基线时须确认完全卸载'
    if quality.get('variance_sum24') is not None:
        detail+=f"（RAW 方差 {quality['variance_sum24']:.0f} / 上限 {quality['variance_limit']:.0f}；非力值）"
    return detail


def audit_saved_baselines(samples):
    """Warn on recorded evidence without removing rows or recalculating force."""
    counts=Counter()
    for row in samples:
        status=row.get('baseline_quality_status','').strip().upper()
        if status in {'READY','REJECTED','WAITING'}:
            counts[status]+=1;continue
        encoded=row.get('local_force_state_json','')
        if not encoded:continue
        try:
            state=json.loads(encoded)
            variance=state.get('quiet_variance_sum24')
            if variance is None:continue
            variance=float(variance)
            counts['REJECTED' if not np.isfinite(variance) or variance>DEFAULT_POLICY.variance_limit else 'VARIANCE_ONLY_PASS']+=1
        except (ValueError,TypeError,AttributeError):counts['INVALID_EVIDENCE']+=1
    parts=[]
    if counts['REJECTED']:
        parts.append(f"基线检查未通过 {counts['REJECTED']} 行；需重新卸载静置复采，全部原始数值仍参与本表统计")
    if counts['WAITING']:parts.append(f"静置基线未就绪 {counts['WAITING']} 行")
    if counts['VARIANCE_ONLY_PASS']:
        parts.append(f"旧记录 {counts['VARIANCE_ONLY_PASS']} 行仅能核对基线方差，缺少静置原始样本，不能认定完整质量通过")
    if counts['INVALID_EVIDENCE']:parts.append(f"基线证据无法解析 {counts['INVALID_EVIDENCE']} 行")
    return dict(counts=dict(counts),text='；'.join(parts),rejected=bool(counts['REJECTED']))
