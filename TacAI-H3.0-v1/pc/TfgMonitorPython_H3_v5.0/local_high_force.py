"""Causal front high-force correction with original-value audit fields.

Runtime permission and metrology acceptance are separate profile metadata.
This module never fits, reads labels, or infers a force target from a filename.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

import numpy as np

from local_state import CausalDualRisingStateLatch, CausalRisingStateLatch, RisingState
from baseline_quality import DEFAULT_POLICY

METHOD = "FRONT_TWO_BLOCK_CAUSAL_LINEAR_V1"
LINEAR_KERNEL_METHOD = "FRONT_LINEAR_KERNEL_CAUSAL_V1"
SUPPORT_METHOD = "TWO_BLOCK_TRACE_STANDARDIZED_NEAREST_NEIGHBOR_V1"
ORIGINAL_MODEL_SHA256 = "249e8b41c4a29f2bf3756ba21c4ee03509d25a99656873ab1ed11674e100a259"
ROI = np.arange(6, 18)  # S3-S6 xyz; other four magnetic vectors stay untouched.
LOCAL_FORCE_CSV_COLUMNS = [
    "local_force_correction_signature", "local_force_profile", "local_force_applied",
    "local_force_status", "local_force_reason", "local_force_clock_source",
    "local_force_model_epoch", "local_force_state_epoch", "local_force_timestamp",
    "local_force_source_token", "local_force_state_json", "local_force_support_json",
    "local_force_beta", "local_force_warnings_json", "original_selected_resultant_n",
    "local_force_corrected_total_n",
    *[f"S{i}_original_resultant_allocation_estimated_n" for i in range(1, 9)],
    *[f"S{i}_corrected_retared_dB{axis}" for i in range(1, 9) for axis in "xyz"],
]


def _array(value, shape, name):
    result = np.asarray(value, dtype=float)
    if result.shape != shape or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must be a finite array of shape {shape}")
    return result.copy()


def allocate_regions(delta24, total_n, noise_floor):
    """The existing norm / noise-floor / exponent-1.35 allocation, no display gate."""
    delta = _array(delta24, (24,), "delta24")
    floor = _array(noise_floor, (8,), "noise_floor")
    total = float(total_n)
    if not np.isfinite(total) or total < 0 or np.any(floor < 0):
        raise ValueError("force and noise floor must be finite and nonnegative")
    if total <= 0:
        return np.zeros(8)
    with np.errstate(over="ignore", invalid="ignore"):
        magnitudes = np.maximum(np.linalg.norm(delta.reshape(8, 3), axis=1) - floor, 0.)
        strengths = magnitudes ** 1.35
    if not np.all(np.isfinite(strengths)):
        raise ValueError("Magnetic allocation overflow")
    summed = strengths.sum()
    strengths = np.full(8, 1. / 8) if summed < 1e-9 else strengths / summed
    return strengths * total


def _state_evidence(latch, state):
    quiet = latch.quiet.state
    evidence = {
        "quiet_raw24": None if state.quiet_raw24 is None else state.quiet_raw24.tolist(),
        "quiet_first_time": quiet.first_time, "quiet_last_time": quiet.last_time,
        "quiet_first_source": quiet.source_first, "quiet_last_source": quiet.source_last,
        "quiet_sample_count": quiet.sample_count, "quiet_variance_sum24": quiet.variance_sum24,
        "baseline_quality": quiet.quality if latch.quiet.loaded else latch.quiet.preview_quality(),
        "rising_raw24": None if state.rising_raw24 is None else state.rising_raw24.tolist(),
        "rising_response24": None if state.response24 is None else state.response24.tolist(),
        "rising_first_source": state.first_source, "rising_second_source": state.second_source,
        "rising_available_time": state.available_time,
        "rising_interpolation_fraction": state.interpolation_fraction,
        "state_reason": state.reason,
    }
    if hasattr(state, 'response15'):
        evidence.update(
            anchor_old_output_indices=[15., 20.],
            rising15_raw24=None if state.raw15 is None else state.raw15.tolist(),
            rising15_response24=None if state.response15 is None else state.response15.tolist(),
            rising15_first_source=state.first_source15,
            rising15_second_source=state.second_source15,
            rising15_available_time=state.available_time15,
            rising15_interpolation_fraction=state.interpolation_fraction15,
        )
    else:
        evidence['anchor_old_output_indices'] = [15.]
    return evidence


@dataclass(frozen=True)
class LocalForceResult:
    original_total_n: float | None
    total_n: float | None
    original_zones: np.ndarray | None
    zones: np.ndarray | None
    original_delta24: np.ndarray | None
    corrected_delta24: np.ndarray | None
    status: str
    reason: str
    applied: bool = False
    profile: str = "off"
    signature: str = ""
    model_epoch: int = 0
    state_epoch: int = 0
    clock_source: str = "CLOCK_RECEIVED_AT"
    source_token: int | None = None
    timestamp: float | None = None
    evidence: dict = field(default_factory=dict)
    support: dict | None = None
    beta: float | None = None
    features2: tuple | None = None
    state_features: tuple | None = None
    magnetic_correction12: np.ndarray | None = None
    warnings: tuple[str, ...] = ()

    def audit_record(self):
        result = dict(self.__dict__)
        for key, value in result.items():
            if isinstance(value, np.ndarray):
                result[key] = value.tolist()
        return result

    def export_dict(self):
        return export_dict(self)

    def export_row(self):
        return export_row(self)


def export_dict(result: LocalForceResult | None):
    """Named, append-only audit schema; reset/invalid events have blank measurements.

    quiet RAW24, rising RAW24/response24, source tokens, interpolation fraction,
    and timestamps are retained inside local_force_state_json, even when the
    operator starts recording after those source frames.
    """
    values = dict.fromkeys(LOCAL_FORCE_CSV_COLUMNS, "")
    if result is None:
        return values
    values.update(local_force_correction_signature=result.signature,
                  local_force_profile=result.profile, local_force_applied=int(result.applied),
                  local_force_status=result.status, local_force_reason=result.reason,
                  local_force_clock_source=result.clock_source,
                  local_force_model_epoch=result.model_epoch, local_force_state_epoch=result.state_epoch,
                  local_force_timestamp=result.timestamp if result.timestamp is not None else "",
                  local_force_source_token=result.source_token if result.source_token is not None else "")
    if result.status == "UNAVAILABLE":
        return values
    encode = lambda obj: json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    values.update(local_force_state_json=encode(result.evidence),
                  local_force_support_json=encode(result.support) if result.support is not None else "",
                  local_force_beta=result.beta if result.beta is not None else "",
                  local_force_warnings_json=encode(result.warnings),
                  original_selected_resultant_n=result.original_total_n,
                  local_force_corrected_total_n=result.total_n)
    if result.original_zones is not None:
        values.update(zip(LOCAL_FORCE_CSV_COLUMNS[16:24], result.original_zones.tolist()))
    if result.corrected_delta24 is not None:
        values.update(zip(LOCAL_FORCE_CSV_COLUMNS[24:], result.corrected_delta24.tolist()))
    return values


def export_row(result: LocalForceResult | None):
    values = export_dict(result)
    return [values[column] for column in LOCAL_FORCE_CSV_COLUMNS]


class LocalHighForceModel:
    """Validated shape/finite coefficients; inference only, no training methods."""
    method = METHOD

    def __init__(self, payload: dict, *, allow_development=False):
        # Copy before validation so the caller cannot later mutate coefficients.
        document = json.loads(json.dumps(payload, allow_nan=False))
        model = document.get("model", document)
        declared_method = document.get("method", model.get("method", model.get("schema")))
        if declared_method is not None and declared_method != self.method:
            raise ValueError("Frozen method does not match this prediction adapter")
        approved = (document.get("runtime_approved") is True
                    and not document.get("not_for_production", False)
                    and not model.get("not_for_production", False))
        if not approved and not allow_development:
            raise ValueError("Profile is not approved for runtime; development candidates stay disabled")
        if document.get("profile", "front") != "front":
            raise ValueError("Only an explicit front profile is supported")
        original_sha = document.get("original_model_sha256", ORIGINAL_MODEL_SHA256)
        if original_sha.lower() != ORIGINAL_MODEL_SHA256:
            raise ValueError("Profile was prepared for a different original force model")
        self.development_only = not approved
        self.signature = self.method + ":" + hashlib.sha256(
            json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False).encode("utf-8")).hexdigest()
        self._parameters(model)
        self.pre_anchor = _array(model["pre_anchor12"], (12,), "pre anchor")
        if model.get("beta_limits", [.5, 1]) != [.5, 1]:
            raise ValueError("Invalid beta bounds")
        support = model["support_model"]
        if support.get("method") != SUPPORT_METHOD:
            raise ValueError("Unknown input support method")
        self.support_centers = (_array(support["load_center24"], (24,), "support load center"),
                                _array(support["quiet_center24"], (24,), "support quiet center"))
        self.support_scales = np.asarray([support["load_scale"], support["quiet_scale"]], float)
        if not np.all(np.isfinite(self.support_scales)) or np.any(self.support_scales <= 0):
            raise ValueError("Support block scales must be positive")
        points = np.asarray(support["training_points_normalized48"], float)
        if points.ndim != 2 or points.shape[1] != 48 or len(points) < 4 or not np.all(np.isfinite(points)):
            raise ValueError("Support requires at least four finite 48-axis input points")
        self.support_points = points.copy()
        if support.get("training_count", len(points)) != len(points):
            raise ValueError("Support training count differs from stored points")
        self.support_threshold = float(support["threshold"])
        if not np.isfinite(self.support_threshold) or self.support_threshold < 0:
            raise ValueError("Support threshold must be finite and nonnegative")
        if hasattr(self, 'training_z') and len(self.training_z) != len(self.support_points):
            raise ValueError("Kernel and support training counts differ")

    def _parameters(self, model):
        self.blocks = []
        for name in ("block_A", "block_B"):
            block = model[name]
            scale = float(block["score_scale"])
            if not np.isfinite(scale) or scale <= 0:
                raise ValueError("PCA score scale must be positive")
            self.blocks.append((_array(block["center24"], (24,), name + ".center"),
                                _array(block["pc1x24"], (24,), name + ".pc"), scale))
        self.center = _array(model["feature_center2"], (2,), "feature center")
        self.magnetic = _array(model["magnetic_coef2x12"], (2, 12), "magnetic coefficients")
        self.beta_coef = _array(model["beta_coef2"], (2,), "beta coefficients")
        self.beta_center = float(model["beta_center"])
        if not np.isfinite(self.beta_center):
            raise ValueError("Invalid beta center")

    def _state_prediction(self, state_a, state_b):
        z = np.asarray([(value - center) @ pc / scale
                        for value, (center, pc, scale) in zip((state_a, state_b), self.blocks)]) - self.center
        return z, z @ self.magnetic, self.beta_center + z @ self.beta_coef

    @classmethod
    def load(cls, path, *, allow_development=False):
        if cls is LocalHighForceModel:
            return load_local_model(path, allow_development=allow_development)
        return cls(json.loads(Path(path).read_text(encoding="utf-8")),
                   allow_development=allow_development)

    def _support(self, state_a, state_b):
        query = np.r_[(state_a - self.support_centers[0]) / self.support_scales[0],
                      (state_b - self.support_centers[1]) / self.support_scales[1]]
        distances = np.linalg.norm(self.support_points - query, axis=1)
        index = int(np.argmin(distances))
        distance = float(distances[index])
        supported = bool(np.isfinite(distance) and distance <= self.support_threshold)
        return dict(supported=supported, reason="SUPPORTED" if supported else "OUTSIDE_INPUT_NEIGHBORHOOD",
                    distance=distance, threshold=self.support_threshold, nearest_training_index=index)

    def predict(self, delta24, old_force, tare24, noise_floor, state: RisingState, *, timestamp=None):
        """Pure current-frame prediction using only previously latched input state."""
        delta = _array(delta24, (24,), "delta24")
        tare = _array(tare24, (24,), "tare24")
        floor = _array(noise_floor, (8,), "noise_floor")
        old = float(old_force)
        original_zones = allocate_regions(delta, old, floor)
        base = dict(original_total_n=old, total_n=old, original_zones=original_zones,
                    zones=original_zones.copy(), original_delta24=delta,
                    corrected_delta24=delta.copy(), status="BYPASS", signature=self.signature,
                    profile="front")
        if old <= 20:
            return LocalForceResult(**base, reason="LOW_FORCE_IDENTITY")
        if state.response24 is None or state.quiet_raw24 is None:
            return LocalForceResult(**base, reason="MISSING_PAST_LOAD_ANCHOR")
        if timestamp is not None and (state.available_time is None or state.available_time > timestamp):
            return LocalForceResult(**base, reason="ANCHOR_NOT_YET_AVAILABLE")
        state_a = _array(state.response24, (24,), "rising response")
        state_b = _array(state.quiet_raw24, (24,), "quiet RAW") - tare
        support = self._support(state_a, state_b)
        if not support["supported"]:
            return LocalForceResult(**base, reason=support["reason"], support=support)
        with np.errstate(over="ignore", invalid="ignore"):
            z, predicted_difference, beta_unlimited = self._state_prediction(state_a, state_b)
            correction = predicted_difference + state_b[ROI] - self.pre_anchor
            if not np.all(np.isfinite(z)) or not np.all(np.isfinite(correction)) or not np.isfinite(beta_unlimited):
                return LocalForceResult(**base, reason="NONFINITE_MODEL_OUTPUT", support=support)
            beta = float(np.clip(beta_unlimited, .5, 1.))
            corrected = delta.copy()
            corrected[ROI] -= np.clip((old - 20.) / 5., 0., 1.) * correction
            total = float(20. + beta * (old - 20.))
        if not np.all(np.isfinite(corrected)) or not np.isfinite(total):
            return LocalForceResult(**base, reason="NONFINITE_MODEL_OUTPUT", support=support)
        try:
            zones = allocate_regions(corrected, total, floor)
        except ValueError:
            return LocalForceResult(**base, reason="NONFINITE_MODEL_OUTPUT", support=support)
        warnings = []
        original_vectors, new_vectors = delta.reshape(8, 3), corrected.reshape(8, 3)
        if np.any(np.sum(original_vectors[2:6] * new_vectors[2:6], axis=1) < 0):
            warnings.append("ROI_VECTOR_DIRECTION_REVERSED")
        if np.any(np.linalg.norm(new_vectors[2:6], axis=1) <= floor[2:6]):
            warnings.append("ROI_AT_OR_BELOW_NOISE_FLOOR")
        if beta != beta_unlimited:
            warnings.append("BETA_LIMITED")
        if self.development_only:
            warnings.append("DEVELOPMENT_PROFILE_NOT_ACCEPTED")
        base.update(total_n=total, zones=zones, corrected_delta24=corrected, status="APPLIED")
        return LocalForceResult(**base, reason="SUPPORTED_CAUSAL_STATE", applied=True,
                                support=support, beta=beta,
                                features2=tuple(z) if len(z) == 2 else None, state_features=tuple(z),
                                magnetic_correction12=correction.copy(), warnings=tuple(warnings))


class LinearKernelHighForceModel(LocalHighForceModel):
    """Frozen linear trend plus centered-RBF residual, without any time feature."""
    method = LINEAR_KERNEL_METHOD

    def _parameters(self, model):
        em = model['embedding']
        self.block_centers = (_array(em['block_A_center24'], (24,), 'load center'),
                              _array(em['block_B_center24'], (24,), 'quiet center'))
        self.block_scales = _array([em['block_A_scale'], em['block_B_scale']], (2,), 'block scales')
        self.pca_center = _array(em['pca_center48'], (48,), 'PCA center')
        self.pc = _array(em['pc3x48'], (3, 48), 'PCA axes')
        self.score_scales = _array(em['score_scale3'], (3,), 'PCA scales')
        if np.any(self.block_scales <= 0) or np.any(self.score_scales <= 0):
            raise ValueError('Embedding scales must be positive')
        z = np.asarray(em['training_z3'], float)
        if z.ndim != 2 or z.shape[1] != 3 or len(z) < 4 or not np.all(np.isfinite(z)):
            raise ValueError('Kernel needs at least four finite training points of width 3')
        self.training_z = z.copy()
        n = len(z)
        self.kernel_length = float(em['kernel_length'])
        self.kernel_grand_mean = float(em['kernel_grand_mean'])
        if not np.isfinite(self.kernel_length) or self.kernel_length <= 0 or not np.isfinite(self.kernel_grand_mean):
            raise ValueError('Invalid frozen kernel parameters')
        self.kernel_column_mean = _array(em['kernel_column_mean'], (n,), 'kernel column mean')
        self.linear_feature_center = _array(model['linear_feature_center3'], (3,), 'linear center')
        self.linear_magnetic = _array(model['linear_magnetic_coef3x12'], (3, 12), 'linear magnetic coefficients')
        self.residual_magnetic = _array(model['magnetic_residual_alpha_nx12'], (n, 12), 'kernel magnetic coefficients')
        self.linear_beta = _array(model['linear_beta_coef3'], (3,), 'linear beta coefficients')
        self.residual_beta = _array(model['beta_residual_alpha_n'], (n,), 'kernel beta coefficients')
        self.beta_mean = float(model['beta_training_mean'])
        self.beta_residual_mean = float(model['beta_residual_mean'])
        if not np.isfinite(self.beta_mean) or not np.isfinite(self.beta_residual_mean):
            raise ValueError('Invalid beta intercepts')

    def _state_prediction(self, state_a, state_b):
        point = np.r_[(state_a - self.block_centers[0]) / self.block_scales[0],
                      (state_b - self.block_centers[1]) / self.block_scales[1]]
        z = ((point - self.pca_center) @ self.pc.T) / self.score_scales
        distance2 = np.sum((self.training_z - z) ** 2, axis=1)
        kernel = np.exp(-distance2 / (2 * self.kernel_length ** 2))
        centered_kernel = kernel - kernel.mean() - self.kernel_column_mean + self.kernel_grand_mean
        x = z - self.linear_feature_center
        # No magnetic target mean/template is added at inference.
        difference = x @ self.linear_magnetic + centered_kernel @ self.residual_magnetic
        beta = (self.beta_mean + self.beta_residual_mean
                + x @ self.linear_beta + centered_kernel @ self.residual_beta)
        return z, difference, beta


class LocalHighForceRuntime:
    """One feed per accepted frame. UI redisplay must only read last_result."""

    def __init__(self, model: LocalHighForceModel | None = None, *, profile="off",
                 clock_source="CLOCK_RECEIVED_AT", allow_development=False, baseline_policy=DEFAULT_POLICY):
        self.baseline_policy = baseline_policy
        self.latch = CausalRisingStateLatch(baseline_policy)
        self.clock_source = str(clock_source)
        self.allow_development = bool(allow_development)
        self.model_epoch = 0
        self.state_epoch = 0
        self._state_key = None
        self._coordinate_key = None
        self._last_tare = None
        self._last_floor = None
        self.last_result = None
        self.configure(model=model, profile=profile)

    def configure(self, *, model=None, profile="off"):
        if profile not in {"off", "front", "back", "auto"}:
            raise ValueError("profile must be explicitly 'front', 'back', 'auto' or 'off'")
        if profile in {'back','auto'} and model is None:
            raise ValueError('Selected correction requires an explicit model')
        if model is not None and profile != 'off' and getattr(model,'profile','front') != profile:
            raise ValueError('Selected position does not match the calibration profile')
        if model is not None and model.development_only and not self.allow_development:
            raise ValueError("Unaccepted development model cannot be enabled in this runtime")
        self.model, self.profile = model, profile
        required = CausalDualRisingStateLatch if getattr(model, 'requires_dual_anchor', False) else CausalRisingStateLatch
        if not isinstance(self.latch, required):
            self.latch = required(self.baseline_policy)
        self.model_epoch += 1
        self.reset("CONFIGURATION_CHANGED")

    def reset(self, reason="RESET"):
        self.latch.reset()
        self._state_key = None
        self._coordinate_key = None
        self._last_tare = self._last_floor = None
        self.state_epoch += 1
        self.reset_reason = str(reason)
        self._load_started_at = None
        self.last_result = None

    def feed(self, raw24, tare24, noise_floor, original_force, timestamp, *,
             source_token=0, epoch=None, valid=True, provisional_enabled=True):
        metadata = dict(profile=self.profile, signature=self.model.signature if self.model else "",
                        model_epoch=self.model_epoch, clock_source=self.clock_source)
        try:
            raw = _array(raw24, (24,), "raw24")
            floor = _array(noise_floor, (8,), "noise_floor")
            old, now = float(original_force), float(timestamp)
            token = int(source_token)
            if not valid or not np.isfinite(old) or old < 0 or not np.isfinite(now) or np.any(floor < 0):
                raise ValueError("Invalid frame")
        except (TypeError, ValueError, OverflowError):
            self.reset("INVALID_FRAME")
            self.last_result = LocalForceResult(None, None, None, None, None, None,
                                                "UNAVAILABLE", "INVALID_FRAME", **metadata,
                                                state_epoch=self.state_epoch)
            return self.last_result
        if tare24 is None:
            self.reset("NO_HOST_TARE")
            self.last_result = LocalForceResult(None, None, None, None, None, None,
                                                "UNAVAILABLE", "NO_HOST_TARE", **metadata,
                                                state_epoch=self.state_epoch, source_token=token, timestamp=now)
            return self.last_result
        try:
            tare = _array(tare24, (24,), "tare24")
        except (TypeError, ValueError, OverflowError):
            return self.feed(raw, None, floor, old, now, source_token=token, epoch=epoch)
        if (self._last_tare is not None and
                (epoch != self._coordinate_key or not np.array_equal(tare, self._last_tare)
                 or not np.array_equal(floor, self._last_floor))):
            self.reset("BASELINE_OR_CONFIGURATION_CHANGED")
        self._coordinate_key = epoch
        self._last_tare, self._last_floor = tare.copy(), floor.copy()
        if not provisional_enabled:
            self.reset("ORIGINAL_HIGH_FORCE_CORRECTION_DISABLED")
            state = RisingState(reason="ORIGINAL_HIGH_FORCE_CORRECTION_DISABLED")
        else:
            was_loaded = self.latch.quiet.loaded
            state = self.latch.feed(now, raw, old, token)
            if self.latch.quiet.loaded and not was_loaded:
                self._load_started_at = now
            elif not self.latch.quiet.loaded:
                self._load_started_at = None
        key = (state.available_time, state.first_source, state.second_source) if state.response24 is not None else None
        if key != self._state_key:
            self.state_epoch += 1
            self._state_key = key
        delta = raw - tare
        try:
            original_zones = allocate_regions(delta, old, floor)
        except ValueError:
            self.reset("INVALID_ALLOCATION_INPUT")
            self.last_result = LocalForceResult(None, None, None, None, None, None,
                                                "UNAVAILABLE", "INVALID_ALLOCATION_INPUT", **metadata,
                                                state_epoch=self.state_epoch, source_token=token, timestamp=now)
            return self.last_result
        reason = ("PROFILE_OFF" if self.profile == "off" else
                  "NO_MODEL" if self.model is None else
                  "ORIGINAL_HIGH_FORCE_CORRECTION_DISABLED" if not provisional_enabled else None)
        quality=self.latch.quiet.state.quality
        if reason is None and quality is not None and quality['status']=='REJECTED':
            reason=quality['reason']
        if reason is not None:
            zones = original_zones
            result = LocalForceResult(old, old, zones, zones.copy(), delta, delta.copy(), "BYPASS", reason)
        else:
            result = self.model.predict(delta, old, tare, floor, state, timestamp=now)
        evidence = _state_evidence(self.latch, state)
        if evidence['baseline_quality'] is None and self.baseline_policy is not None:
            evidence['baseline_quality']=dict(policy_id=self.baseline_policy.policy_id,status='WAITING',
                reason='BASELINE_HISTORY_RESET',physical_zero_confirmed=False)
        evidence.update(load_started_time=self._load_started_at,
                        load_age_s=None if self._load_started_at is None else now-self._load_started_at,
                        anchor_age_s=None if state.available_time is None else now-state.available_time,
                        last_reset_reason=self.reset_reason, acquisition_epoch=epoch,
                        model_method=self.model.method if self.model is not None else '',
                        calibration_validation_status=getattr(self.model, 'validation_status', ''),
                        independent_acceptance_passed=getattr(self.model, 'acceptance_passed', False),
                        model_state_features=result.state_features)
        values = {**result.__dict__, **metadata, "state_epoch": self.state_epoch,
                  "timestamp": now, "source_token": token,
                  "evidence": evidence}
        self.last_result = LocalForceResult(**values)
        return self.last_result


def load_local_model(path, *, allow_development=False):
    """Explicit frozen-method dispatch; unsupported profiles never fall back to a different formula.

    New accepted model methods can add adapters here without changing the causal
    runtime. Adapters alone do not approve or enable any research parameters.
    """
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    model = document.get("model", document)
    method = document.get("method", model.get("method", model.get("schema")))
    if method is None and "block_A" in model and "block_B" in model:
        method = METHOD  # named two-block development schema, still not approved
    if method == METHOD:
        return LocalHighForceModel(document, allow_development=allow_development)
    if method == LINEAR_KERNEL_METHOD:
        return LinearKernelHighForceModel(document, allow_development=allow_development)
    if method == 'FRONT_REGIONAL_INDEPENDENT_V1':
        from regional_high_force import RegionalHighForceModel
        return RegionalHighForceModel(document, allow_development=allow_development)
    if method == 'FRONT_REGIONAL_ARD_RBF_V1':
        from regional_ard_force import RegionalARDHighForceModel
        return RegionalARDHighForceModel(document, allow_development=allow_development)
    if method == 'FRONT_REGIONAL_ARD_RBF_SIX_HALL_V2':
        from regional_ard_force import SixHallARDHighForceModel
        return SixHallARDHighForceModel(document, allow_development=allow_development)
    if method == 'BACK_PAST_STATE_TOTAL_RBF_V1':
        from back_total_force import BackTotalForceModel
        return BackTotalForceModel(document, allow_development=allow_development)
    if method == 'BACK_NOMINAL25_PAST15_QUIET_TOTAL_AND_SPATIAL_RIDGE_CANDIDATE_V1':
        from back25_consistency_force import Back25ConsistencyForceModel
        return Back25ConsistencyForceModel(document, allow_development=allow_development)
    if method == 'BACK_NOMINAL25_PAST15_QUIET_TOTAL_AND_SPATIAL_RBF_CANDIDATE_V2':
        from back25_smooth_force import Back25SmoothForceModel
        return Back25SmoothForceModel(document, allow_development=allow_development)
    if method == 'BACK25_TWO_DOMAIN_CAUSAL_CANDIDATE_V3':
        from back25_domain_force import Back25DomainForceModel
        return Back25DomainForceModel(document, allow_development=allow_development)
    raise ValueError(f"Unsupported frozen correction method: {method!r}")
