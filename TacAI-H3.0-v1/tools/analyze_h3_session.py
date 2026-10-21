"""Analyze one H3 calibration session, including v5.2.1 header recovery."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd


FRAME_DIAGNOSTICS = ["upper_model_load_score", "upper_model_soft_gate_rescue"]
SUMMARY_DIAGNOSTICS = ["upper_model_load_score_median", "upper_model_soft_gate_rescue_frames"]


def read_recoverable_csv(path: Path, kind: str) -> tuple[pd.DataFrame, bool]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        first = next(reader, None)
    if first is None:
        return pd.DataFrame(columns=header), False
    if len(first) == len(header):
        return pd.read_csv(path, encoding="utf-8-sig"), False
    if len(first) != len(header) + 2:
        raise ValueError(f"{path.name}: row has {len(first)} fields; header has {len(header)}")
    names = list(header)
    if kind == "frames":
        insert_at = names.index("upper_model_confidence") + 1
        names[insert_at:insert_at] = FRAME_DIAGNOSTICS
    else:
        insert_at = names.index("upper_model_median_n") + 1
        names[insert_at:insert_at] = SUMMARY_DIAGNOSTICS
    return pd.read_csv(path, encoding="utf-8-sig", header=None, skiprows=1, names=names), True


def finite(series: pd.Series) -> np.ndarray:
    return pd.to_numeric(series, errors="coerce").to_numpy(float)


def metric_block(reference: np.ndarray, prediction: np.ndarray, mask: np.ndarray) -> dict[str, float | int]:
    error = prediction[mask] - reference[mask]
    return {
        "count": int(mask.sum()),
        "mae_n": float(np.mean(np.abs(error))),
        "median_error_n": float(np.median(error)),
        "p95_abs_error_n": float(np.percentile(np.abs(error), 95)),
        "within_1n_rate": float(np.mean(np.abs(error) <= 1.0)),
    }


def monotone_lookup(train: pd.DataFrame, prediction: np.ndarray) -> np.ndarray:
    anchors = train.groupby("reference_median_n", sort=True)["upper_model_median_n"].median()
    target = anchors.index.to_numpy(float)
    measured = anchors.to_numpy(float)
    measured = np.maximum.accumulate(measured)
    keep = np.r_[True, np.diff(measured) > 1e-9]
    measured = measured[keep]
    target = target[keep]
    if measured[0] > 0.0:
        measured = np.r_[0.0, measured]
        target = np.r_[0.0, target]
    return np.interp(prediction, measured, target, left=0.0, right=float(target[-1]))


def analyze(summary: pd.DataFrame, frames: pd.DataFrame) -> dict[str, object]:
    reference = finite(summary["reference_median_n"])
    prediction = finite(summary["upper_model_median_n"])
    loaded = reference > 0.0
    low = loaded & (reference < 2.0)
    normal = reference >= 2.0
    result: dict[str, object] = {
        "summary_steps": int(len(summary)),
        "frame_rows": int(len(frames)),
        "loaded_all": metric_block(reference, prediction, loaded),
        "loaded_below_2n": metric_block(reference, prediction, low),
        "loaded_2n_and_above": metric_block(reference, prediction, normal),
    }

    load_score = finite(summary["upper_model_load_score_median"])
    rescue_frames = finite(summary["upper_model_soft_gate_rescue_frames"])
    result["loaded_gate_diagnostics"] = {
        "load_score_min": float(np.min(load_score[loaded])),
        "load_score_p05": float(np.percentile(load_score[loaded], 5)),
        "load_score_median": float(np.median(load_score[loaded])),
        "steps_with_any_rescue": int(np.sum(rescue_frames[loaded] > 0)),
        "steps_total": int(np.sum(loaded)),
    }

    low_rows = summary.loc[low, ["reference_median_n", "position", "direction", "upper_model_median_n"]].copy()
    low_rows["reference_median_n"] = pd.to_numeric(low_rows["reference_median_n"])
    low_rows["upper_model_median_n"] = pd.to_numeric(low_rows["upper_model_median_n"])
    low_rows["error_n"] = low_rows["upper_model_median_n"] - low_rows["reference_median_n"]
    result["low_force_by_target"] = [
        {
            "reference_n": float(target),
            "prediction_median_n": float(group["upper_model_median_n"].median()),
            "mae_n": float(group["error_n"].abs().mean()),
            "min_prediction_n": float(group["upper_model_median_n"].min()),
            "max_prediction_n": float(group["upper_model_median_n"].max()),
        }
        for target, group in low_rows.groupby("reference_median_n", sort=True)
    ]
    result["low_force_by_direction"] = []
    for direction, group in low_rows.groupby("direction", sort=False):
        error = group["error_n"].to_numpy(float)
        result["low_force_by_direction"].append({
            "direction": direction,
            "count": int(len(group)),
            "mae_n": float(np.mean(np.abs(error))),
            "median_error_n": float(np.median(error)),
        })

    loaded_by_target = []
    for target in sorted(np.unique(reference[loaded])):
        target_mask = loaded & (reference == target)
        loaded_by_target.append({
            "reference_n": float(target),
            "load_score_min": float(np.min(load_score[target_mask])),
            "load_score_median": float(np.median(load_score[target_mask])),
            "rescue_step_rate": float(np.mean(rescue_frames[target_mask] > 0)),
        })
    result["gate_by_target"] = loaded_by_target

    post_zero_values = prediction[(reference == 0.0) & (summary["direction"].to_numpy(str) == "卸载")]
    global_residual = float(np.median(post_zero_values))
    global_corrected = np.maximum(prediction - global_residual, 0.0)
    result["hypothetical_global_residual_subtraction"] = {
        "subtracted_n": global_residual,
        "loaded_all": metric_block(reference, global_corrected, loaded),
        "loaded_below_2n": metric_block(reference, global_corrected, low),
        "loaded_2n_and_above": metric_block(reference, global_corrected, normal),
    }


    calibration = summary.copy()
    for name in ("reference_median_n", "upper_model_median_n", "repeat"):
        calibration[name] = pd.to_numeric(calibration[name], errors="coerce")
    lookup_folds = {"global": [], "per_position": []}
    for repeat in sorted(calibration["repeat"].dropna().unique()):
        train = calibration[(calibration["repeat"] != repeat) & (calibration["reference_median_n"] > 0.0)]
        test = calibration[(calibration["repeat"] == repeat) & (calibration["reference_median_n"] > 0.0)]
        reference_fold = test["reference_median_n"].to_numpy(float)
        prediction_fold = test["upper_model_median_n"].to_numpy(float)
        global_prediction = monotone_lookup(train, prediction_fold)
        global_metrics = metric_block(
            reference_fold, global_prediction, np.ones(len(test), dtype=bool)
        )
        global_metrics["below_2n_mae_n"] = float(np.mean(np.abs(
            global_prediction[reference_fold < 2.0] - reference_fold[reference_fold < 2.0]
        )))
        lookup_folds["global"].append(global_metrics)
        position_prediction = np.zeros(len(test), dtype=float)
        for position in test["position"].unique():
            mask = test["position"].to_numpy(str) == position
            position_prediction[mask] = monotone_lookup(
                train[train["position"] == position], prediction_fold[mask]
            )
        position_metrics = metric_block(
            reference_fold, position_prediction, np.ones(len(test), dtype=bool)
        )
        position_metrics["below_2n_mae_n"] = float(np.mean(np.abs(
            position_prediction[reference_fold < 2.0] - reference_fold[reference_fold < 2.0]
        )))
        lookup_folds["per_position"].append(position_metrics)
    result["hypothetical_monotone_lookup_leave_repeat_out"] = {
        kind: {
            key: float(np.mean([fold[key] for fold in folds]))
            for key in ("mae_n", "below_2n_mae_n", "p95_abs_error_n", "within_1n_rate")
        }
        for kind, folds in lookup_folds.items()
    }
    position_anchors = []
    loaded_calibration = calibration[calibration["reference_median_n"] > 0.0]
    for position, group in loaded_calibration.groupby("position", sort=False):
        anchors = group.groupby("reference_median_n", sort=True)["upper_model_median_n"].median()
        target = anchors.index.to_numpy(float)
        measured = np.maximum.accumulate(anchors.to_numpy(float))
        position_anchors.append({
            "position": position,
            "model_n": [0.0, *[float(value) for value in measured]],
            "reference_n": [0.0, *[float(value) for value in target]],
        })
    result["position_lookup_anchors"] = position_anchors

    zero = summary.loc[reference == 0.0].copy()
    zero["upper_model_median_n"] = pd.to_numeric(zero["upper_model_median_n"])
    zero["upper_model_load_score_median"] = pd.to_numeric(
        zero.get("upper_model_load_score_median"), errors="coerce"
    )
    zero["upper_model_soft_gate_rescue_frames"] = pd.to_numeric(
        zero.get("upper_model_soft_gate_rescue_frames"), errors="coerce"
    )
    zero_records = []
    for row in zero.itertuples(index=False):
        zero_records.append({
            "repeat": int(row.repeat),
            "position": row.position,
            "direction": row.direction,
            "residual_n": float(row.upper_model_median_n),
            "load_score": float(row.upper_model_load_score_median),
            "rescue_frames": int(row.upper_model_soft_gate_rescue_frames),
        })
    result["zero_steps"] = zero_records
    for direction, key in (("加载", "initial_zero"), ("卸载", "post_cycle_zero")):
        values = zero.loc[zero["direction"] == direction, "upper_model_median_n"].to_numpy(float)
        result[key] = {
            "count": int(len(values)),
            "median_n": float(np.median(values)),
            "min_n": float(np.min(values)),
            "max_n": float(np.max(values)),
            "over_0p01_rate": float(np.mean(values > 0.01)),
            "over_0p5_rate": float(np.mean(values > 0.5)),
        }

    residual_by_position = []
    post = zero[zero["direction"] == "卸载"]
    for position, group in post.groupby("position", sort=False):
        residual_by_position.append({
            "position": position,
            "values_n": [float(value) for value in group["upper_model_median_n"]],
            "median_n": float(group["upper_model_median_n"].median()),
        })
    result["post_cycle_zero_by_position"] = residual_by_position

    frame_reference = finite(frames["reference_force_n"])
    post_zero_frames = frames[(frame_reference == 0.0) & (frames["direction"] == "卸载")].copy()
    decay = []
    for keys, group in post_zero_frames.groupby(["repeat", "position"], sort=False):
        values = pd.to_numeric(group["upper_model_n"], errors="coerce").to_numpy(float)
        times = pd.to_datetime(group["local_time_iso"], errors="coerce")
        elapsed = (times - times.iloc[0]).dt.total_seconds().to_numpy(float)
        slope = float(np.polyfit(elapsed, values, 1)[0]) if len(values) >= 2 and np.ptp(elapsed) > 0 else 0.0
        decay.append({
            "repeat": int(keys[0]),
            "position": keys[1],
            "first_median_n": float(np.median(values[: min(5, len(values))])),
            "last_median_n": float(np.median(values[-min(5, len(values)) :])),
            "slope_n_per_s": slope,
            "duration_s": float(np.ptp(elapsed)),
        })
    result["post_cycle_zero_3s_decay"] = decay
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    frames, frames_recovered = read_recoverable_csv(args.frames, "frames")
    summary, summary_recovered = read_recoverable_csv(args.summary, "summary")
    report = analyze(summary, frames)
    report["recovered_stale_headers"] = {
        "frames": frames_recovered,
        "summary": summary_recovered,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
