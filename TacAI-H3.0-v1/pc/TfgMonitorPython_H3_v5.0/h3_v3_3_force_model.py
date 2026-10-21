"""V3-3 H3 RAW-to-Fz calibration model.

The model was fitted from the 2026-09-30 three-repeat calibration run for
device V3-3.  It covers 0.2 N through 30 N at the front, middle, and rear
test positions, including loading and unloading.  The regression uses all
24 re-tared Hall channels and their element-wise second- and third-order
terms.  It is intentionally kept independent from the legacy NO8 model.
A provisional high-force post-calibration uses the 2026-10-08 three-repeat
nominal-target run and was replayed against the 2026-10-09 quick run.  It can
be disabled per estimate and is not an independent force-gauge validation.

The calibration does not establish accuracy below 0.2 N and must not be used
as evidence that the 0.01 N recovery requirement has passed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from h3_release_prototypes import LOW_LOAD_0P2_PROTOTYPES, RELEASE_ZERO_PROTOTYPES


RANGE_N = (0.0, 35.0)
CALIBRATED_RANGE_N = (0.2, 30.0)
MODEL_ID = "H3_V3_3_FZ_V5_PROVISIONAL_HIGH_FORCE_20261009"
REGION_NAMES = ("后按键区域", "两按键交界区域", "前按键区域", "未标定前端")
LOW_SIGNAL_NORM = 1500.0
ZERO_SNAP_N = 0.01
LOAD_SCORE_THRESHOLD = 0.48
# Four same-assembly calibration rounds provide a clean interval between the
# largest unloaded continuous-regression result (0.305 N) and the fifth
# percentile of loaded results (0.426 N).  A rejected classifier result is
# therefore allowed through only above this interval.  This prevents a
# session-shifted binary classifier from turning an obvious press into 0 N.
SOFT_GATE_RESCUE_N = 0.35
LOW_FORCE_CALIBRATION_FULL_N = 5.0
LOW_FORCE_CALIBRATION_FADE_END_N = 10.0
RELEASE_PROTOTYPE_RATIO = 0.85
RELEASE_SUPPRESS_MAX_N = 2.0
DIRECTION_DIVERGENCE_MIN_N = 2.0
DIRECTION_DIVERGENCE_RATIO = 0.20
DIRECTION_CONFIDENCE_FLOOR = 0.20

# A direction-invariant fallback is used only when the signed polynomial model
# returns no usable force.  Its two monotone inputs were pooled from the four
# 2026-09-30 force-gauge rounds and the 2026-10-08 verification run.  It is a
# continuity estimate, not an independently validated oblique-load model.
INVARIANT_REFERENCE_N = np.asarray([0.0, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0])
INVARIANT_NORM_INPUT = np.asarray([
    8500.0, 9972.3, 11088.3, 13284.7, 17158.5, 29301.9, 48276.1, 65516.6, 75679.4,
])
INVARIANT_ACTIVITY_INPUT = np.asarray([
    22000.0, 26408.9, 28286.8, 31381.8, 38094.3, 63114.4, 100486.3, 143690.6, 177399.3,
])


@dataclass(frozen=True)
class ForceEstimateDetails:
    fz_n: float
    region_weights: np.ndarray
    confidence: float
    best_region: int
    regression_unclamped_n: float
    regression_clamped_n: float
    classifier_score: float
    invariant_fallback_n: float
    release_similarity_ratio: float
    gate_reason: str
    rescued: bool

# Median preliminary-model outputs from the 2026-10-08 three-repeat run.
# Row order matches REGION_PROTOTYPES: rear, middle, front.  Only the low-force
# part is applied fully; correction fades to zero from 5 N to 10 N so the
# already-usable middle/high range is not replaced by a single-session table.
POSITION_CALIBRATION_INPUT_N = np.asarray([
    [0.0, 1.3103137008355912, 1.5894256615535625, 1.8884463320467506,
     2.6497315779214237, 5.677063870249668, 11.207541449387634],
    [0.0, 1.4515895057276276, 1.6091711688517774, 1.85800272579622,
     2.523501302403294, 5.095679457296024, 10.33106541978128],
    [0.0, 0.7941207968514858, 0.9118488663366513, 1.2216542525387601,
     2.09503481549189, 4.880643903002543, 10.543228535289472],
], dtype=float)
POSITION_CALIBRATION_REFERENCE_N = np.asarray(
    [0.0, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0], dtype=float
)

# Provisional high-force correction for the same V3-3 assembly.  These are
# medians of the *current* host estimator on the 2026-10-08 three-repeat
# summary (loading and unloading combined).  Its manually entered reference
# readings equal the nominal targets, so apply only 60% of the discrepancy.
# The separate 2026-10-09 quick run confirms the position/force error shape;
# neither CSV contains an independently captured force-gauge reading.  The
# first/last anchors keep low forces unchanged and avoid extrapolating a
# single-session correction beyond the model's 35 N display range.
HIGH_FORCE_INPUT_N = np.asarray([
    [5.0, 11.206361078, 16.602198984, 21.713199380, 25.899573455, 29.693765764, 35.0],
    [5.0, 10.333114204, 16.248821007, 22.071192212, 27.849282887, 33.439897405, 35.0],
    [5.0, 10.543903940, 16.710063715, 22.507697601, 27.940027718, 33.320590769, 35.0],
], dtype=float)
HIGH_FORCE_REFERENCE_N = np.asarray([5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0])
HIGH_FORCE_CORRECTION_FRACTION = 0.60

# Per-channel bounds of the 189 unique calibration steps.  Prediction is
# allowed a 25% margin beyond them, but confidence falls outside the measured
# envelope so an extrapolation is visible to the operator.
RAW_MIN = np.asarray([
    -12220.272, -7582.095, -15062.079, -2505.336, -2487.312, -4578.6375,
    -3028.032, -853.136, -4026.875, -8867.808, -15116.13, -64643.047,
    -24801.023, -10922.544, -39300.796, -17663.52, -3544.72, -18411.359,
    -16461.92, -2475.296, -3668.7225, -30772.973, -6248.32, -50916.798,
], dtype=float)
RAW_MAX = np.asarray([
    3160.208, 420.561, 3581.601, 11697.576, 4518.016, 14200.5595,
    12977.279, 21256.305, 63713.767, 4848.457, 432.576, 3910.719,
    18588.751, 4397.857, 18179.04, 22530.0, 5395.184, 47838.557,
    34654.144, 14226.944, 49571.2855, 15644.833, 5959.936, 3368.641,
], dtype=float)

# Normalized response directions for rear, middle, and front loads.  The
# fourth weight is kept at zero for compatibility with the existing 4-region
# logging format; no front-tip calibration was collected in this run.
REGION_PROTOTYPES = np.asarray([
    [0.0868711299982, -0.0126522344388, 0.101006661272, -0.0512110705973,
     0.0155878270274, -0.146430850935, -0.0506212644551, 0.0104096389015,
     -0.022853196514, 0.0533488656725, -0.021556458731, -0.00998570205055,
     -0.182275079379, 0.0809018376554, 0.0716773895943, 0.154983432654,
     -0.0354366592339, -0.0630825132838, 0.204464596708, 0.137959049976,
     0.546682951307, -0.127642346608, 0.0702150877796, -0.704339664999],
    [0.0238641263109, -0.0349546965621, 0.0664415755418, 0.0147169421088,
     -0.0209444800474, -0.122524491443, -0.0237309132651, 0.0546682288846,
     0.156821626017, 0.0332058267316, -0.0510609557213, -0.27165020713,
     0.101628828777, -0.061350063341, -0.532100443056, -0.151015985527,
     -0.0701317285032, 0.645417826606, -0.115342020806, -0.0102033933246,
     0.16188502111, 0.17715911628, -0.0519375696404, -0.239309629159],
    [-0.0737196387334, -0.0699992404657, -0.0636485638435, 0.0919052271864,
     -0.0166668132892, 0.00953288584236, 0.0236580862717, 0.164658087108,
     0.58285737016, 0.027949564444, -0.084193697066, -0.688670535829,
     0.111099645268, 0.0341539123539, -0.139318978962, -0.172665373159,
     -0.0402712931705, 0.171830487929, -0.10061655714, -0.0039976988274,
     -0.00421277558625, 0.142127678087, -0.0204309438235, -0.0511100703968],
], dtype=float)

FEATURE_MEAN = np.asarray([
    -450.53642328042338, -1111.0980846560853, 66.328412698412833,
    1006.0698148148142, 74.798010582010548, -1068.6929629629626,
    460.80408465608525, 2688.8503571428569, 9288.3982248677221,
    410.99258994708998, -1847.5714074074076, -11459.024251322744,
    582.52169312169281, 17.070407407407291, -6696.7704814814833,
    -1562.2706957671951, -839.72139947089966, 7882.7978994708965,
    -63.004761904761509, 1448.2775529100538, 8034.1442645502621,
    1499.5524285714293, -298.33375661375669, -10140.081148148147,
    11694741.812146457, 3278432.7861988028, 15359174.510390652,
    10672482.353957854, 1092384.7340552807, 17709102.886845887,
    9508802.4921516608, 26385229.960061174, 345458326.82441193,
    5833876.4881734541, 11262442.039006388, 430214360.56256348,
    51703714.631298609, 7418207.7100109793, 194413299.45502552,
    49000333.312485166, 2726362.7376815681, 242017660.16701248,
    71671517.377944738, 11402321.714324739, 224830212.88299394,
    61202096.148652114, 5205947.19102138, 280130341.67528337,
    -68922977462.463455, -14894649247.419165, -123301997514.59013,
    71302676004.085709, 1840907331.3870342, 79041488604.690079,
    59664721018.094803, 368461239892.70868, 15909088778540.955,
    -11082484677.760336, -112124050812.87703, -20832447697168.32,
    -218240713014.18741, -32215247560.749325, -5071932387664.4092,
    58506429328.510696, -1422804959.1197639, 7317465116914.3408,
    1042103633981.8959, 99072789895.606735, 7868423185591.6855,
    -531314074841.84998, 1132588591.6431155, -10153461901880.199,
], dtype=float)

FEATURE_SCALE = np.asarray([
    3389.9496668009883, 1429.6481498859714, 3918.5169454858255,
    3108.1032611025553, 1042.4922022098074, 4070.2577606042751,
    3049.0100176476826, 4376.6783885675104, 16099.192067948201,
    2380.117975895741, 2801.5927494082721, 17288.87283110615,
    7166.8949419076316, 2723.5851944086353, 12229.740985539465,
    6823.4627269174871, 1421.6999363270018, 13411.903572689069,
    8465.6687732229439, 3050.3792951142336, 12660.281940754247,
    7678.11426475392, 2262.0663475429046, 13315.746167010468,
    23497556.930956759, 8489131.9877827354, 36147956.703106605,
    22857252.001454439, 2906905.3319690926, 30162091.607012983,
    23038817.82209504, 72057160.556186557, 824008579.47080529,
    10819571.024053345, 34456002.702441573, 959066339.39684343,
    100889830.60275081, 16713035.866968293, 365247975.98295921,
    84562725.69451943, 4019637.192701017, 465218064.00372291,
    183792347.13011426, 30076587.686875511, 507127819.95985293,
    138865519.88616776, 9411756.5811946243, 576750327.97584212,
    252462247161.67749, 55104462135.67189, 468403883599.922,
    233218297045.36838, 11775558639.769888, 390445572989.35077,
    264642178382.8992, 1293116731015.2061, 44599990398911.195,
    91136749515.223419, 453586911222.37952, 54933410025739.305,
    2264503561625.3623, 160519653690.94073, 13023077172407.252,
    1730335485333.3474, 19219281247.282898, 19033157075564.555,
    5862264310664.1904, 356835809842.77649, 22067344085668.41,
    3891781033756.4014, 57948249273.658806, 25900338859960.359,
], dtype=float)

# First element is the intercept.  Remaining 72 values correspond to the
# standardized [x, x**2, x**3] features above.  Low-force points receive a
# moderate inverse-force weight; ridge alpha=13.2571.
MODEL_WEIGHT = np.asarray([
    8.8645044752770641, -0.081454104367102459, -0.44531367796547688,
    -0.6105451460217417, 0.029441514656993607, 0.15737177335102343,
    0.47685812485258094, -0.042652746438293331, 0.25339484811431101,
    0.59064134744529551, 0.23223309142848017, -0.18273217582799722,
    -0.51648291494509579, 0.21640070608275058, -0.24125210912019387,
    -1.1494840657470422, -0.1730349410335135, -0.060764459222131126,
    0.8180468390961404, -0.091117374521068495, 0.36122359390741499,
    1.3681993260784377, 0.044630718845863629, -0.24633319881365964,
    -1.0952379916605719, -0.0014559392275914377, 0.18147420055551222,
    0.33229284218461491, 0.063578625633609442, -0.015957934290528136,
    0.12519287206427421, -0.10198094064494316, 0.092115330027961878,
    0.34472987913540543, 0.13220854231523518, 0.013456247728792324,
    0.30734040340654589, 0.18611021686239596, 0.0031309284137062479,
    1.1887251292217185, 0.31907184300653169, 0.083353212941462113,
    0.49180794425815105, 0.35905815881660219, 0.29852281885313064,
    0.9367629035917554, 0.36909909451583844, 0.37495736056332279,
    0.81883454818643242, 0.022328917689961796, -0.080791182906283385,
    -0.2627871306925445, 0.027668697501692041, -0.153954530502181,
    0.4000969607031486, -0.031627394039672754, 0.049668464567103873,
    0.23225967706821896, 0.067879340944654837, -0.0084999744859850963,
    -0.21612160384728238, 0.036912153991218832, 0.087473382232604008,
    -0.9931610907571955, 0.050799276374883726, -0.22644347694459102,
    0.081709140768391156, 0.22076962312256632, 0.57597635022801918,
    0.84908182103313157, -0.17762958081569741, -0.18624684357186549,
    -0.81212351749764899,
], dtype=float)

# Separate loaded/unloaded discriminator.  It prevents structural recovery
# drift from appearing as force while retaining every recorded 0.2 N frame.
# Leave-one-repeat-out validation separated all 18 zero steps from all loaded
# steps; the full-run frame check separated 499/499 zero frames and retained
# 516/516 0.2 N frames.
LOAD_WEIGHT = np.asarray([
    0.9047619047619061, -0.11708046997633374, -0.85834436906638145,
    -0.81920722462174322, -0.16631379431491949, -0.45966279789048153,
    -0.730531686440138, 0.10973506071940255, 1.6477634362686089,
    -3.654154839935793, 0.044595032401117486, 1.4026668414856192,
    -3.0639125362097492, 0.95317944638831098, -1.512373426871843,
    -1.0689792863883876, 0.5265178785761061, -0.7913586007952137,
    -1.5193313252086058, 1.7857875360550175, 1.0810593023062911,
    -1.5167325763220072, 1.217659331879569, 0.93389378946122681,
    -1.0215713136250884,
], dtype=float)


def clamp_force(value: float) -> float:
    return float(np.clip(value, *RANGE_N))


def load_score(delta_raw) -> float:
    """Return the legacy loaded/unloaded discriminator score."""
    feature = np.asarray(delta_raw, dtype=float).reshape(-1)
    if feature.size != 24 or not np.all(np.isfinite(feature)):
        raise ValueError("H3 V3-3 load score requires 24 finite RAW Delta-B values")
    raw_standardized = (feature - FEATURE_MEAN[:24]) / FEATURE_SCALE[:24]
    return float(LOAD_WEIGHT[0] + np.dot(LOAD_WEIGHT[1:], raw_standardized))


def soft_gate_force(force: float, classifier_score: float) -> tuple[float, bool]:
    """Suppress unloaded drift without allowing a hard false-zero at real load."""
    force = clamp_force(force)
    if classifier_score >= LOAD_SCORE_THRESHOLD:
        return force, False
    if force > SOFT_GATE_RESCUE_N:
        return force, True
    return 0.0, False


def direction_invariant_force(delta_raw) -> float:
    """Return a conservative non-directional continuity estimate.

    The lower of the vector norm and per-sensor activity mappings is used so a
    single abnormal channel cannot create a large force.  This path is marked
    low-confidence and is never presented as validated oblique-load accuracy.
    """
    feature = np.asarray(delta_raw, dtype=float).reshape(-1)
    vectors = feature.reshape(8, 3)
    norm_value = float(np.linalg.norm(feature))
    activity_value = float(np.linalg.norm(vectors, axis=1).sum())
    from_norm = float(np.interp(norm_value, INVARIANT_NORM_INPUT, INVARIANT_REFERENCE_N))
    from_activity = float(np.interp(activity_value, INVARIANT_ACTIVITY_INPUT, INVARIANT_REFERENCE_N))
    return clamp_force(min(from_norm, from_activity))


def release_similarity_ratio(delta_raw) -> float:
    """Return nearest known-release distance divided by nearest 0.2 N distance.

    Only a clearly release-like vector (ratio <= 0.85) may suppress a small
    candidate force.  Unseen directions are therefore retained instead of
    being rejected by a broad signed linear classifier.
    """
    feature = np.asarray(delta_raw, dtype=float).reshape(-1)
    standardized = feature / FEATURE_SCALE[:24]
    zero_distance = float(np.min(np.linalg.norm(
        RELEASE_ZERO_PROTOTYPES / FEATURE_SCALE[:24] - standardized, axis=1
    )))
    load_distance = float(np.min(np.linalg.norm(
        LOW_LOAD_0P2_PROTOTYPES / FEATURE_SCALE[:24] - standardized, axis=1
    )))
    return zero_distance / max(load_distance, 1e-12)


def calibrate_low_force(force: float, region_weights) -> float:
    """Apply the 2026-10-08 position-aware low-force correction."""
    force = clamp_force(force)
    if force <= 0.0 or force >= LOW_FORCE_CALIBRATION_FADE_END_N:
        return force
    weights = np.asarray(region_weights, dtype=float).reshape(-1)[:3]
    total = float(weights.sum())
    if total <= 1e-12:
        weights = np.full(3, 1.0 / 3.0)
    else:
        weights = weights / total
    corrected_by_region = np.asarray([
        np.interp(force, POSITION_CALIBRATION_INPUT_N[index], POSITION_CALIBRATION_REFERENCE_N)
        for index in range(3)
    ])
    corrected = float(np.dot(weights, corrected_by_region))
    if force <= LOW_FORCE_CALIBRATION_FULL_N:
        blend = 1.0
    else:
        blend = (
            LOW_FORCE_CALIBRATION_FADE_END_N - force
        ) / (
            LOW_FORCE_CALIBRATION_FADE_END_N - LOW_FORCE_CALIBRATION_FULL_N
        )
    return clamp_force(blend * corrected + (1.0 - blend) * force)


def calibrate_nominal_high_force(
    force: float, region_weights, *, enabled: bool = True,
) -> float:
    """Apply a reversible, partially shrunk V3-3 high-force correction.

    Region weights blend the three monotone lookups.  The identity result when
    disabled makes the pre-correction model available for replay and rollback.
    This table does not claim force-gauge accuracy or model oblique contacts.
    """
    force = clamp_force(force)
    if not enabled or force <= HIGH_FORCE_INPUT_N[0, 0]:
        return force
    weights = np.asarray(region_weights, dtype=float).reshape(-1)[:3]
    total = float(weights.sum())
    if total <= 1e-12:
        weights = np.full(3, 1.0 / 3.0)
    else:
        weights = weights / total
    corrected_by_region = np.asarray([
        np.interp(force, HIGH_FORCE_INPUT_N[index], HIGH_FORCE_REFERENCE_N)
        for index in range(3)
    ])
    correction = float(np.dot(weights, corrected_by_region - force))
    return clamp_force(force + HIGH_FORCE_CORRECTION_FRACTION * correction)


def _region_state(feature: np.ndarray) -> tuple[np.ndarray, float, int]:
    norm = float(np.linalg.norm(feature))
    if norm <= 1e-12:
        return np.asarray([1 / 3, 1 / 3, 1 / 3, 0.0]), 0.0, 0
    scores = REGION_PROTOTYPES @ (feature / norm)
    logits = 12.0 * (scores - float(np.max(scores)))
    weights3 = np.exp(logits)
    weights3 /= float(weights3.sum())
    weights = np.asarray([weights3[0], weights3[1], weights3[2], 0.0])
    direction_confidence = float(np.clip((float(np.max(scores)) - 0.40) / 0.55, 0.0, 1.0))
    return weights, direction_confidence, int(np.argmax(weights3))


def estimate_force_details(
    delta_raw, *, use_provisional_high_force_correction: bool = True,
) -> ForceEstimateDetails:
    """Estimate force and expose every decision that can produce zero."""
    feature = np.asarray(delta_raw, dtype=float).reshape(-1)
    if feature.size != 24 or not np.all(np.isfinite(feature)):
        raise ValueError("H3 V3-3 estimator requires 24 finite RAW Delta-B values")

    norm = float(np.linalg.norm(feature))
    region_weights, direction_confidence, best_region = _region_state(feature)
    if norm < LOW_SIGNAL_NORM:
        return ForceEstimateDetails(
            0.0, region_weights, 0.0, best_region,
            0.0, 0.0, load_score(feature), 0.0, float("inf"),
            "LOW_SIGNAL", False,
        )

    span = np.maximum(RAW_MAX - RAW_MIN, 1.0)
    lower = RAW_MIN - 0.25 * span
    upper = RAW_MAX + 0.25 * span
    bounded = np.clip(feature, lower, upper)
    outside = np.maximum(np.maximum(RAW_MIN - feature, feature - RAW_MAX), 0.0) / span
    envelope_confidence = float(np.exp(-5.0 * float(np.mean(outside))))

    polynomial = np.concatenate((bounded, bounded * bounded, bounded * bounded * bounded))
    standardized = (polynomial - FEATURE_MEAN) / FEATURE_SCALE
    regression_unclamped = MODEL_WEIGHT[0] + float(np.dot(MODEL_WEIGHT[1:], standardized))
    regression_clamped = clamp_force(regression_unclamped)
    classifier_score = load_score(feature)
    force, rescued = soft_gate_force(regression_clamped, classifier_score)
    invariant_force = direction_invariant_force(feature)
    release_ratio = release_similarity_ratio(feature)

    direction_diverged = (
        invariant_force >= DIRECTION_DIVERGENCE_MIN_N
        and direction_confidence <= DIRECTION_CONFIDENCE_FLOOR
        and force < DIRECTION_DIVERGENCE_RATIO * invariant_force
    )
    if (force <= 0.0 and invariant_force >= ZERO_SNAP_N) or direction_diverged:
        force = invariant_force
        rescued = True
        gate_reason = "DIRECTION_INVARIANT_FALLBACK"
    elif force > 0.0:
        gate_reason = "CLASSIFIER_LOAD" if not rescued else "CLASSIFIER_IGNORED_CONTINUITY"
    else:
        gate_reason = "NO_FORCE_CANDIDATE"

    if release_ratio <= RELEASE_PROTOTYPE_RATIO and force <= RELEASE_SUPPRESS_MAX_N:
        force = 0.0
        rescued = False
        gate_reason = "KNOWN_RELEASE_PROTOTYPE"

    if force > 0.0:
        # The invariant fallback already maps activity to force anchors.  The
        # signed regression alone receives the position-aware low-force table.
        if gate_reason != "DIRECTION_INVARIANT_FALLBACK":
            force = calibrate_low_force(force, region_weights)
            force = calibrate_nominal_high_force(
                force, region_weights,
                enabled=use_provisional_high_force_correction,
            )
    if force < ZERO_SNAP_N:
        force = 0.0
        if gate_reason not in {"LOW_SIGNAL", "KNOWN_RELEASE_PROTOTYPE"}:
            gate_reason = "ZERO_SNAP"
    if force <= 0.0:
        if gate_reason == "KNOWN_RELEASE_PROTOTYPE":
            zero_confidence = float(np.clip((RELEASE_PROTOTYPE_RATIO - release_ratio) / 0.35, 0.0, 1.0))
        else:
            zero_confidence = float(np.clip((LOAD_SCORE_THRESHOLD - classifier_score) / 0.15, 0.0, 1.0))
        return ForceEstimateDetails(
            0.0, region_weights, zero_confidence, best_region,
            float(regression_unclamped), float(regression_clamped), float(classifier_score),
            float(invariant_force), float(release_ratio), gate_reason, rescued,
        )
    if rescued:
        # A visible value is safer than a false zero, but keep confidence low
        # until controlled fixed-angle captures validate this path.
        load_confidence = float(np.clip(0.20 + (force - SOFT_GATE_RESCUE_N) / 12.0, 0.20, 0.55))
    else:
        load_confidence = float(np.clip((classifier_score - LOAD_SCORE_THRESHOLD) / 0.35, 0.0, 1.0))
    confidence = float(np.clip(direction_confidence * envelope_confidence * load_confidence, 0.0, 1.0))
    return ForceEstimateDetails(
        float(force), region_weights, confidence, best_region,
        float(regression_unclamped), float(regression_clamped), float(classifier_score),
        float(invariant_force), float(release_ratio), gate_reason, rescued,
    )


def estimate_force(delta_raw, *, use_provisional_high_force_correction: bool = True):
    """Return ``(fz_n, region_weights, confidence, best_region)``.

    Input is the same 24-channel ``current_RAW - tare_RAW`` vector recorded by
    the calibration wizard.  The output is a scalar normal-force estimate; it
    does not claim independently calibrated Fx/Fy components.
    Set ``use_provisional_high_force_correction=False`` to replay the prior
    high-force behavior on the same RAW data.
    """
    details = estimate_force_details(
        delta_raw,
        use_provisional_high_force_correction=use_provisional_high_force_correction,
    )
    return details.fz_n, details.region_weights, details.confidence, details.best_region
