"""Luo's original 4x2 -> 4x3 spatial reconstruction, calibrated for NO8.

The geometric/feature calculation and the 4x3 fixed weights are a direct
Python port of ``TacAI-H2.1/Core/Src/main.c`` and ``sr.c``.  Only the final
raw-response-to-newton scale uses the approved NO8 calibration captures.
"""

from __future__ import annotations

import numpy as np

from no8_force_model import PROTOTYPE, REGION_NAMES


RANGE_N = (0.0, 35.0)

# Exact W_TOP and W_BOTTOM from TacAI-H2.1/Core/Src/sr.c.
W_TOP = np.asarray(
    ((0.08, 0.14, 0.08), (0.12, 0.22, 0.12), (0.06, 0.12, 0.06), (0.00, 0.00, 0.00)),
    dtype=float,
)
W_BOTTOM = np.asarray(
    ((0.00, 0.00, 0.00), (0.06, 0.12, 0.06), (0.12, 0.22, 0.12), (0.08, 0.14, 0.08)),
    dtype=float,
)

# NO8 scale only.  These zero-anchored quadratic fits use the approved stable
# holds: rear centre, button boundary, front centre and front tip.  They are
# deliberately not a new spatial model; they convert Luo's own summed Fz
# response to N after the original reconstruction has been performed.
NO8_SCALE_QUADRATIC = np.asarray(
    (
        (4.37146341, 0.07586428),   # rear centre
        (3.72403548, -0.10115513),  # boundary
        (1.39864605, 0.08338189),   # front centre
        (6.65103508, -0.28267717),  # front tip
    ),
    dtype=float,
)

# Increment-force fits generated from every valid virtual-tare pair in the
# 2026-09-08 NO8 stable holds.  Given r = Luo reconstructed response after
# manual tare and p = response already present at the instant of tare, each
# row evaluates F_increment = r*(a + b*r + c*p + d*p^2).  This lets a real
# post-start residual/preload influence the calibration curve instead of only
# shifting the display zero.
NO8_PRELOAD_INCREMENT = np.asarray(
    (
        (6.3999007902, -0.2675042939, 1.1729951747, -0.2014072083),
        (4.0832166459, -0.1480292433, -0.0936237056, 0.0076034090),
        (1.5028460719, 0.0822430783, 0.3181908361, -0.0025874188),
        (7.8591975150, -0.5450222765, -0.1265975171, -0.0064388703),
    ),
    dtype=float,
)

# Measured Luo responses at 5 N physical preload for the same four regions.
# They set the gradual hand-off from the validated no-preload curve to the
# validated preload-aware curve.  The front tip is below the original Luo
# dead-zone at 5 N, so it deliberately remains on the no-preload curve there.
NO8_PRELOAD_5_RESPONSE = np.asarray((0.455, 0.298, 1.717, 0.0), dtype=float)


def _deadzone_signed(value: float, width: float) -> float:
    if abs(value) <= width:
        return 0.0
    return value - width if value > 0.0 else value + width


def luo_half_force_no_calib(delta_2x2: np.ndarray) -> np.ndarray:
    """Port of Calc_3D_Force_NoCalib for one physical 2x2 half."""
    b1, b2, b3, b4 = np.asarray(delta_2x2, dtype=float).reshape(4, 3)
    n1, n2, n3, n4 = (float(np.linalg.norm(item)) for item in (b1, b2, b3, b4))

    fz_raw = 0.25 * (n1 + n2 + n3 + n4)
    fx_raw = 0.5 * ((n2 + n4) - (n1 + n3))
    fy_raw = 0.5 * ((n3 + n4) - (n1 + n2))

    dxx, dxy, dxz = 0.5 * ((b2 + b4) - (b1 + b3))
    dyx, dyy, dyz = 0.5 * ((b3 + b4) - (b1 + b2))
    mean = 0.25 * (b1 + b2 + b3 + b4)
    fx_raw += 0.20 * dxx + 0.10 * dxy + 0.10 * dxz
    fy_raw += 0.10 * dyx + 0.20 * dyy + 0.10 * dyz
    fz_raw += 0.15 * abs(mean[0]) + 0.15 * abs(mean[1]) + 0.30 * abs(mean[2])

    fx_raw = _deadzone_signed(float(fx_raw), 3000.0)
    fy_raw = _deadzone_signed(float(fy_raw), 3000.0)
    fz_raw = max(0.0, float(fz_raw) - 5000.0)
    return np.asarray(
        (
            np.clip(fx_raw / 300000.0, -1.0, 1.0) * 50.0,
            np.clip(fy_raw / 300000.0, -1.0, 1.0) * 50.0,
            np.clip(fz_raw / 150000.0, 0.0, 1.0) * 50.0,
        ),
        dtype=float,
    )


def luo_force_map(delta_raw: np.ndarray) -> np.ndarray:
    """Return Luo's unscaled 4x3 Fx/Fy/Fz map from 24 Delta-B values."""
    matrix = np.asarray(delta_raw, dtype=float).reshape(4, 2, 3)
    front = luo_half_force_no_calib(matrix[:2])
    rear = luo_half_force_no_calib(matrix[2:])
    return W_TOP[..., None] * front + W_BOTTOM[..., None] * rear


def _no8_scale_weights(delta_raw: np.ndarray) -> np.ndarray:
    feature = np.asarray(delta_raw, dtype=float).reshape(-1)
    norm = float(np.linalg.norm(feature))
    if norm < 1e-9:
        return np.full(4, 0.25, dtype=float)
    scores = PROTOTYPE @ (feature / norm)
    logits = 12.0 * (scores - float(np.max(scores)))
    weights = np.exp(logits)
    return weights / float(weights.sum())


def reconstruct_no8_force_map(
    delta_raw: np.ndarray, tare_raw: np.ndarray | None = None
) -> tuple[np.ndarray, float, np.ndarray, int, float]:
    """Return (4x3 map, total Fz N, region weights, best region, preload blend).

    Unlike the previous four-region force estimator, a low similarity score
    does not blank the force map.  The NO8 captures only select a smooth force
    *scale*; the spatial response is always the original Luo calculation.
    """
    raw_map = luo_force_map(delta_raw)
    raw_total = float(raw_map[..., 2].sum())
    weights = _no8_scale_weights(delta_raw)
    coefficients = weights @ NO8_SCALE_QUADRATIC
    no_preload_n = coefficients[0] * raw_total + coefficients[1] * raw_total * raw_total

    # Firmware RAW is baseline-subtracted at start-up.  Therefore a nonzero
    # manual-tare RAW frame is evidence of a force state that developed after
    # that baseline.  If the force was already present at firmware start-up,
    # it is information-theoretically invisible to any upper-computer model.
    preload_response = 0.0
    preload_blend = 0.0
    preload_n = no_preload_n
    if tare_raw is not None:
        preload_response = float(luo_force_map(tare_raw)[..., 2].sum())
        preload_coefficients = weights @ NO8_PRELOAD_INCREMENT
        preload_n = raw_total * (
            preload_coefficients[0]
            + preload_coefficients[1] * raw_total
            + preload_coefficients[2] * preload_response
            + preload_coefficients[3] * preload_response * preload_response
        )
        response_at_5n = float(weights @ NO8_PRELOAD_5_RESPONSE)
        if response_at_5n > 1e-6:
            preload_blend = float(np.clip(preload_response / response_at_5n, 0.0, 1.0))
    total_n = float(np.clip((1.0 - preload_blend) * no_preload_n + preload_blend * preload_n, *RANGE_N))
    if raw_total <= 1e-9 or total_n <= 0.0:
        return np.zeros((4, 3, 3), dtype=float), 0.0, weights, int(np.argmax(weights)), preload_blend
    return raw_map * (total_n / raw_total), total_n, weights, int(np.argmax(weights)), preload_blend
