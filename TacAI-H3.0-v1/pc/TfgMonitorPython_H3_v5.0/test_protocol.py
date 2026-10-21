"""Acceptance state machines for H3 calibration, recovery and EMI tests."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from types import MappingProxyType


ACCURACY_SEQUENCE = tuple(range(31)) + tuple(range(29, -1, -1))
ACCURACY_POSITIONS = ("前", "中", "后")
ACCURACY_TOTAL_POINTS = len(ACCURACY_SEQUENCE) * len(ACCURACY_POSITIONS)

# Development captures, not an acceptance test. Each pair is (target N, hold s).
# The operator confirms each plateau; targets never replace gauge readings.
HIGH_FORCE_TRAJECTORIES = MappingProxyType({
    "A": (
        (0.0, 180.0), (15.0, 20.0), (0.0, 180.0), (25.0, 20.0),
        (0.0, 180.0), (25.0, 60.0), (30.0, 20.0), (25.0, 20.0),
        (20.0, 20.0), (25.0, 60.0), (0.0, 600.0), (15.0, 20.0),
        (0.0, 240.0),
    ),
    "B": (
        (0.0, 180.0), (15.0, 20.0), (30.0, 20.0), (25.0, 60.0),
        (20.0, 20.0), (25.0, 20.0), (0.0, 600.0), (25.0, 60.0),
        (0.0, 180.0), (25.0, 20.0), (0.0, 180.0), (15.0, 20.0),
        (0.0, 240.0),
    ),
})
HIGH_FORCE_MAX_SAMPLE_GAP_S = 1.0

RECOVERY_HOLD_S = 500.0
RECOVERY_OBSERVE_S = 120.0
FAST_RECOVERY_WINDOW_S = 5.0
RECOVERY_STEADY_WINDOW_S = 10.0
FAST_RECOVERY_LIMIT_N = 1.5
RECOVERY_STEADY_LIMIT_N = 0.01

EMI_BASELINE_S = 30.0
EMI_EXPOSURE_S = 60.0
EMI_STEADY_SHIFT_LIMIT_N = 0.01
EMI_TRANSIENT_LIMIT_N = 1.5
EMI_MAX_SAMPLE_GAP_S = 1.0


@dataclass
class ProtocolContext:
    protocol: str = "none"
    phase: str = "idle"
    point_index: int = -1
    nominal_force_n: float | None = None
    reference_force_n: float | None = None
    stable_sample: bool = False
    test_position: str = ""
    acceptance: str = "PENDING"


@dataclass
class TestProtocol:
    kind: str = "none"
    active: bool = False
    phase: str = "idle"
    point_index: int = -1
    reference_force_n: float | None = None
    sample_started_at: float | None = None
    phase_started_at: float | None = None
    test_position: str = ""
    scenario: str = ""
    acceptance: str = "PENDING"
    samples: list[tuple[float, float, float, float]] = field(default_factory=list)
    baseline_samples: list[float] = field(default_factory=list)
    exposure_samples: list[float] = field(default_factory=list)
    summaries: list[dict] = field(default_factory=list)
    last_sample_at: float | None = None
    max_sample_gap_s: float = 0.0
    quality_fault_count: int = 0
    high_force_trajectory: str = ""
    high_force_last_time: float | None = None
    high_force_step_last_sample_at: float | None = None
    high_force_step_max_gap_s: float = 0.0
    high_force_step_quality_fault_count: int = 0
    high_force_clock_error_reason: str = ""

    def start_high_force(self, now: float, position: str, trajectory: str = "A") -> None:
        """Start one fixed-position, continuously recorded development sequence."""
        if self.active:
            raise ValueError("已有测试进行中，请先结束当前测试")
        if position not in {"前", "后"}:
            raise ValueError("高力专项位置只能选择前或后，整条序列位置固定")
        if trajectory not in HIGH_FORCE_TRAJECTORIES:
            raise ValueError("高力专项轨迹只能选择 A 或 B")
        now = self._high_force_time(now, None)
        self._reset("high_force_continuous", now)
        self.high_force_trajectory = trajectory
        self.high_force_last_time = now
        self.last_sample_at = now
        self.test_position = position
        self.point_index = 0
        self.phase = "waiting_for_load"
        self.acceptance = "DATA_ONLY"

    def high_force_target(self) -> tuple[str, int, float, float]:
        steps = HIGH_FORCE_TRAJECTORIES.get(self.high_force_trajectory, ())
        if self.kind != "high_force_continuous" or not 0 <= self.point_index < len(steps):
            return "", -1, 0.0, 0.0
        force_n, duration_s = steps[self.point_index]
        return self.test_position, self.point_index, force_n, duration_s

    def begin_high_force_step(self, reference_force_n: float, now: float) -> None:
        """Mark operator-confirmed stability; the reference is a manual reading."""
        if not self.active or self.kind != "high_force_continuous" or self.phase != "waiting_for_load":
            raise ValueError("当前不能开始高力专项持载")
        try:
            reference_force_n = float(reference_force_n)
        except (TypeError, ValueError) as exc:
            raise ValueError("测力计实测参考必须在 0-40 N 且为有限数") from exc
        if not math.isfinite(reference_force_n) or not 0.0 <= reference_force_n <= 40.0:
            raise ValueError("测力计实测参考必须在 0-40 N 且为有限数")
        now = self._high_force_time(now, self.high_force_last_time)
        self.high_force_last_time = now
        self.reference_force_n = reference_force_n
        self.phase = "hold_reference"
        self.phase_started_at = now
        self.sample_started_at = now
        self.high_force_step_last_sample_at = now
        self.high_force_step_max_gap_s = 0.0
        self.high_force_step_quality_fault_count = 0
        self.samples.clear()

    @staticmethod
    def _high_force_time(now: float, previous: float | None) -> float:
        try:
            value = float(now)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"高力专项时钟无效：{now!r}") from exc
        if not math.isfinite(value) or (previous is not None and value <= previous):
            raise ValueError(f"高力专项时钟必须严格递增且有限：previous={previous!r}, now={value!r}")
        return value

    def _feed_high_force(
        self, now: float, resultant_n: float, confidence: float,
        firmware_fz: float, quality_ok: bool,
    ) -> str | None:
        try:
            now = self._high_force_time(now, self.high_force_last_time)
        except ValueError as exc:
            self.active = False
            self.phase = "clock_error"
            self.acceptance = "INVALID_CLOCK"
            self.high_force_clock_error_reason = str(exc)
            return "high_force_clock_error"
        self.high_force_last_time = now
        self._track_sample_gap(now)
        if self.phase != "hold_reference":
            return None
        self.high_force_step_max_gap_s = max(
            self.high_force_step_max_gap_s, now - float(self.high_force_step_last_sample_at),
        )
        self.high_force_step_last_sample_at = now
        # Preserve every force value, including flagged samples. A quality flag
        # is audit metadata, never permission to discard an inconvenient value.
        self.samples.append((now, float(resultant_n), float(confidence), float(firmware_fz)))
        if not quality_ok or not math.isfinite(resultant_n):
            self.quality_fault_count += 1
            self.high_force_step_quality_fault_count += 1
        position, point_index, nominal, duration = self.high_force_target()
        elapsed = now - float(self.sample_started_at)
        if now < float(self.sample_started_at) + duration:
            return None
        values = [sample[1] for sample in self.samples]
        finite_count = sum(math.isfinite(value) for value in values)
        all_finite = finite_count == len(values)
        # A nonfinite value invalidates statistics instead of silently removing
        # that sample or synthesizing a measurement. Raw session logging is UI-owned.
        self.summaries.append({
            "summary_type": "high_force_continuous",
            "calibration_model": "ORIGINAL_MODEL_UNCHANGED",
            "trajectory": self.high_force_trajectory,
            "test_position": position,
            "point_index": point_index,
            "nominal_force_n": nominal,
            "reference_resultant_force_n": self.reference_force_n,
            "reference_source": "manual_plateau_reading",
            "reference_is_continuous_measurement": False,
            "hold_age_source": "operator_stable_mark",
            "hold_started_at": self.sample_started_at,
            "hold_finished_at": now,
            "requested_duration_s": duration,
            "actual_duration_s": elapsed,
            "sample_count": len(values),
            "finite_sample_count": finite_count,
            "software_resultant_mean_n": statistics.mean(values) if all_finite else None,
            "software_resultant_median_n": statistics.median(values) if all_finite else None,
            "software_resultant_std_n": statistics.pstdev(values) if all_finite else None,
            "standard_deviation_definition": "population",
            "software_resultant_min_n": min(values) if all_finite else None,
            "software_resultant_max_n": max(values) if all_finite else None,
            "quality_fault_count": self.high_force_step_quality_fault_count,
            "max_sample_gap_s": self.high_force_step_max_gap_s,
            "sample_gap_limit_s": HIGH_FORCE_MAX_SAMPLE_GAP_S,
            "sample_gap_exceeded": self.high_force_step_max_gap_s > HIGH_FORCE_MAX_SAMPLE_GAP_S,
            "session_max_sample_gap_s": self.max_sample_gap_s,
            "acceptance": "DATA_ONLY",
        })
        self.point_index += 1
        self.reference_force_n = None
        self.sample_started_at = None
        self.high_force_step_last_sample_at = None
        self.samples.clear()
        if self.point_index >= len(HIGH_FORCE_TRAJECTORIES[self.high_force_trajectory]):
            self.active = False
            self.phase = "complete"
            return "high_force_complete"
        self.phase = "waiting_for_load"
        self.phase_started_at = now
        return "high_force_step_complete"

    def start_accuracy(self, now: float) -> None:
        """Run one shared model through front, middle and rear positions."""
        self._reset("accuracy_hysteresis_front_middle_rear", now)
        self.phase = "waiting_for_load"
        self.point_index = 0

    def accuracy_target(self) -> tuple[str, int, float]:
        if not 0 <= self.point_index < ACCURACY_TOTAL_POINTS:
            return "", -1, 0.0
        position_index, position_point = divmod(self.point_index, len(ACCURACY_SEQUENCE))
        return ACCURACY_POSITIONS[position_index], position_point, float(ACCURACY_SEQUENCE[position_point])

    def begin_accuracy_sample(self, reference_force_n: float, now: float) -> None:
        if not self.active or self.kind != "accuracy_hysteresis_front_middle_rear" or self.phase != "waiting_for_load":
            raise ValueError("当前不能开始稳定采样")
        if not math.isfinite(reference_force_n) or reference_force_n < 0.0 or reference_force_n > 40.0:
            raise ValueError("测力计真实合力必须在 0-40 N 且为有限数")
        self.reference_force_n = float(reference_force_n)
        self.sample_started_at = now
        self.phase = "stable_3s"
        self.samples.clear()

    def start_long_recovery(self, now: float, position: str) -> None:
        self._reset("long_hold_recovery", now)
        self.phase = "hold_30n_500s"
        self.reference_force_n = 30.0
        self.test_position = position

    def confirm_unloaded(self, now: float) -> None:
        if not self.active or self.kind != "long_hold_recovery" or self.phase != "await_fast_unload":
            raise ValueError("尚未到快速卸载阶段")
        self.phase = "recovery_0n_120s"
        self.reference_force_n = 0.0
        self.phase_started_at = now
        self.samples.clear()
        self.last_sample_at = None
        self.max_sample_gap_s = 0.0

    def start_emi(self, now: float, position: str) -> None:
        self._reset("emi_immunity", now)
        self.phase = "baseline_30s"
        self.reference_force_n = 0.0
        self.test_position = position

    def confirm_emi_on(self, now: float, scenario: str) -> None:
        if not self.active or self.kind != "emi_immunity" or self.phase != "await_emi_on":
            raise ValueError("尚未完成 30 秒无干扰基线")
        scenario = scenario.strip()
        if not scenario:
            raise ValueError("请在备注中填写电磁干扰源/工况")
        self.scenario = scenario
        self.phase = "emi_exposure_60s"
        self.phase_started_at = now
        self.exposure_samples.clear()
        self.last_sample_at = None
        self.max_sample_gap_s = 0.0
        self.quality_fault_count = 0

    def feed(
        self,
        now: float,
        resultant_n: float,
        confidence: float,
        firmware_fz: float,
        quality_ok: bool = True,
    ) -> str | None:
        if not self.active:
            return None
        if self.kind == "high_force_continuous":
            return self._feed_high_force(now, resultant_n, confidence, firmware_fz, quality_ok)
        self._track_sample_gap(now)

        if self.kind == "accuracy_hysteresis_front_middle_rear" and self.phase == "stable_3s":
            self.samples.append((now, float(resultant_n), float(confidence), float(firmware_fz)))
            if now - float(self.sample_started_at) >= 3.0:
                values = [sample[1] for sample in self.samples]
                confidences = [sample[2] for sample in self.samples]
                firmware = [sample[3] for sample in self.samples]
                position, position_point, nominal = self.accuracy_target()
                self.summaries.append({
                    "summary_type": "accuracy_hysteresis",
                    "calibration_model": "ONE_SHARED_MODEL_FRONT_MIDDLE_REAR",
                    "test_position": position,
                    "overall_point_index": self.point_index,
                    "position_point_index": position_point,
                    "nominal_force_n": nominal,
                    "reference_resultant_force_n": self.reference_force_n,
                    "software_resultant_median_n": statistics.median(values),
                    "software_resultant_error_n": statistics.median(values) - float(self.reference_force_n),
                    "model_confidence_median": statistics.median(confidences),
                    "firmware_fz_raw_median_n": statistics.median(firmware),
                    "sample_count": len(values),
                })
                self.point_index += 1
                self.samples.clear()
                self.sample_started_at = None
                self.reference_force_n = None
                if self.point_index >= ACCURACY_TOTAL_POINTS:
                    self.active = False
                    self.phase = "complete"
                    self.acceptance = "DATA_READY_FOR_POSITION_CONSISTENCY_REVIEW"
                    return "accuracy_complete"
                self.phase = "waiting_for_load"
                return "accuracy_point_complete"

        if self.kind == "long_hold_recovery":
            if self.phase == "hold_30n_500s" and now - float(self.phase_started_at) >= RECOVERY_HOLD_S:
                self.phase = "await_fast_unload"
                return "hold_complete"
            if self.phase == "recovery_0n_120s":
                elapsed = now - float(self.phase_started_at)
                self.samples.append((elapsed, float(resultant_n), float(confidence), float(firmware_fz)))
                if not quality_ok:
                    self.quality_fault_count += 1
                if elapsed >= RECOVERY_OBSERVE_S:
                    fast = [abs(sample[1]) for sample in self.samples if sample[0] <= FAST_RECOVERY_WINDOW_S]
                    steady_start = RECOVERY_OBSERVE_S - RECOVERY_STEADY_WINDOW_S
                    steady = [abs(sample[1]) for sample in self.samples if sample[0] >= steady_start]
                    fast_peak = max(fast, default=float("inf"))
                    steady_max = max(steady, default=float("inf"))
                    steady_median = statistics.median(steady) if steady else float("inf")
                    passed = (
                        fast_peak <= FAST_RECOVERY_LIMIT_N
                        and steady_max <= RECOVERY_STEADY_LIMIT_N
                        and self.quality_fault_count == 0
                        and self.max_sample_gap_s <= EMI_MAX_SAMPLE_GAP_S
                    )
                    self.acceptance = "PASS" if passed else "FAIL"
                    self.summaries.append({
                        "summary_type": "long_hold_recovery_acceptance",
                        "test_position": self.test_position,
                        "hold_force_n": 30.0,
                        "hold_duration_s": RECOVERY_HOLD_S,
                        "recovery_duration_s": RECOVERY_OBSERVE_S,
                        "fast_window_s": FAST_RECOVERY_WINDOW_S,
                        "fast_peak_abs_n": fast_peak,
                        "fast_limit_abs_n": FAST_RECOVERY_LIMIT_N,
                        "final_window_s": RECOVERY_STEADY_WINDOW_S,
                        "final_median_abs_n": steady_median,
                        "final_max_abs_n": steady_max,
                        "final_limit_abs_n": RECOVERY_STEADY_LIMIT_N,
                        "max_sample_gap_s": self.max_sample_gap_s,
                        "quality_fault_count": self.quality_fault_count,
                        "acceptance": self.acceptance,
                    })
                    self.active = False
                    self.phase = "complete"
                    return "recovery_complete_pass" if passed else "recovery_complete_fail"

        if self.kind == "emi_immunity":
            if self.phase == "baseline_30s":
                self.baseline_samples.append(float(resultant_n))
                if now - float(self.phase_started_at) >= EMI_BASELINE_S:
                    self.phase = "await_emi_on"
                    self.last_sample_at = None
                    return "emi_baseline_complete"
            elif self.phase == "emi_exposure_60s":
                self.exposure_samples.append(float(resultant_n))
                if not quality_ok:
                    self.quality_fault_count += 1
                if now - float(self.phase_started_at) >= EMI_EXPOSURE_S:
                    baseline = statistics.median(self.baseline_samples) if self.baseline_samples else float("inf")
                    exposure_median = statistics.median(self.exposure_samples) if self.exposure_samples else float("inf")
                    steady_shift = abs(exposure_median - baseline)
                    transient_peak = max((abs(value - baseline) for value in self.exposure_samples), default=float("inf"))
                    passed = (
                        steady_shift <= EMI_STEADY_SHIFT_LIMIT_N
                        and transient_peak <= EMI_TRANSIENT_LIMIT_N
                        and self.max_sample_gap_s <= EMI_MAX_SAMPLE_GAP_S
                        and self.quality_fault_count == 0
                    )
                    self.acceptance = "PASS" if passed else "FAIL"
                    self.summaries.append({
                        "summary_type": "emi_immunity_acceptance",
                        "test_position": self.test_position,
                        "emi_scenario": self.scenario,
                        "baseline_duration_s": EMI_BASELINE_S,
                        "exposure_duration_s": EMI_EXPOSURE_S,
                        "baseline_median_n": baseline,
                        "exposure_median_n": exposure_median,
                        "steady_shift_abs_n": steady_shift,
                        "steady_shift_limit_n": EMI_STEADY_SHIFT_LIMIT_N,
                        "transient_peak_abs_n": transient_peak,
                        "transient_limit_n": EMI_TRANSIENT_LIMIT_N,
                        "max_sample_gap_s": self.max_sample_gap_s,
                        "max_sample_gap_limit_s": EMI_MAX_SAMPLE_GAP_S,
                        "quality_fault_count": self.quality_fault_count,
                        "acceptance": self.acceptance,
                    })
                    self.active = False
                    self.phase = "complete"
                    return "emi_complete_pass" if passed else "emi_complete_fail"
        return None

    def context(self) -> ProtocolContext:
        nominal = None
        stable = False
        position = self.test_position
        reference = self.reference_force_n
        if self.kind == "accuracy_hysteresis_front_middle_rear" and 0 <= self.point_index < ACCURACY_TOTAL_POINTS:
            position, _, nominal = self.accuracy_target()
            stable = self.phase == "stable_3s"
        elif self.kind == "long_hold_recovery":
            nominal = 30.0 if self.phase in {"hold_30n_500s", "await_fast_unload"} else 0.0
        elif self.kind == "emi_immunity":
            nominal = 0.0
        elif self.kind == "high_force_continuous":
            _, point_index, target, _ = self.high_force_target()
            nominal = target if point_index >= 0 else None
            if self.phase != "hold_reference":
                reference = None
        return ProtocolContext(
            self.kind,
            self.phase,
            self.point_index,
            nominal,
            reference,
            stable,
            position,
            self.acceptance,
        )

    def stop(self) -> None:
        self.active = False
        self.phase = "stopped"

    def _track_sample_gap(self, now: float) -> None:
        if self.last_sample_at is not None:
            self.max_sample_gap_s = max(self.max_sample_gap_s, now - self.last_sample_at)
        self.last_sample_at = now

    def _reset(self, kind: str, now: float) -> None:
        self.kind = kind
        self.active = True
        self.phase = "idle"
        self.point_index = -1
        self.reference_force_n = None
        self.sample_started_at = None
        self.phase_started_at = now
        self.test_position = ""
        self.scenario = ""
        self.acceptance = "PENDING"
        self.samples.clear()
        self.baseline_samples.clear()
        self.exposure_samples.clear()
        self.summaries.clear()
        self.last_sample_at = None
        self.max_sample_gap_s = 0.0
        self.quality_fault_count = 0
        self.high_force_trajectory = ""
        self.high_force_last_time = None
        self.high_force_step_last_sample_at = None
        self.high_force_step_max_gap_s = 0.0
        self.high_force_step_quality_fault_count = 0
        self.high_force_clock_error_reason = ""
