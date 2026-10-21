"""Compare lightweight H3 force-model candidates on calibration CSV files.

The script treats each acquisition/repeat as an independent validation group.
It never edits the source CSV files.  It intentionally uses only NumPy/Pandas
so the same analysis can run from the bundled Codex scientific runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_h3_session import read_recoverable_csv


DELTA_COLUMNS = [f"S{sensor}_retared_dB{axis}" for sensor in range(1, 9) for axis in "xyz"]


@dataclass
class DataSet:
    x: np.ndarray
    y: np.ndarray
    groups: np.ndarray
    sessions: np.ndarray
    positions: np.ndarray
    directions: np.ndarray


def load_summary(paths: list[Path]) -> DataSet:
    tables = []
    for path in paths:
        table, _recovered = read_recoverable_csv(path, "summary")
        missing = [f"median_{name}" for name in DELTA_COLUMNS if f"median_{name}" not in table]
        if missing:
            raise ValueError(f"{path.name} lacks {missing[0]}")
        table = table.copy()
        table["source"] = path.stem
        table["group"] = table["source"] + "_r" + table["repeat"].astype(str)
        tables.append(table)
    merged = pd.concat(tables, ignore_index=True)
    x = merged[[f"median_{name}" for name in DELTA_COLUMNS]].to_numpy(float)
    y = merged["reference_median_n"].to_numpy(float)
    return DataSet(
        x=x,
        y=y,
        groups=merged["group"].to_numpy(str),
        sessions=merged["source"].to_numpy(str),
        positions=merged["position"].to_numpy(str),
        directions=merged["direction"].to_numpy(str),
    )


def ridge_fit(x: np.ndarray, y: np.ndarray, alpha: float, sample_weight: np.ndarray | None = None):
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-9] = 1.0
    z = (x - mean) / scale
    design = np.column_stack([np.ones(len(z)), z])
    if sample_weight is None:
        weighted_design, weighted_y = design, y
    else:
        root = np.sqrt(np.asarray(sample_weight, dtype=float))
        weighted_design, weighted_y = design * root[:, None], y * root
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    weight = np.linalg.solve(weighted_design.T @ weighted_design + penalty, weighted_design.T @ weighted_y)
    return mean, scale, weight


def ridge_predict(model, x: np.ndarray) -> np.ndarray:
    mean, scale, weight = model
    z = (x - mean) / scale
    return np.column_stack([np.ones(len(z)), z]) @ weight


def features(raw: np.ndarray, kind: str) -> np.ndarray:
    raw = np.asarray(raw, dtype=float)
    sensor = raw.reshape(-1, 8, 3)
    magnitudes = np.linalg.norm(sensor, axis=2)
    activity = magnitudes.sum(axis=1, keepdims=True)
    norm = np.linalg.norm(raw, axis=1, keepdims=True)
    peak = magnitudes.max(axis=1, keepdims=True)
    if kind == "raw_linear":
        return raw
    if kind == "raw_quadratic":
        return np.column_stack([raw, np.sign(raw) * np.sqrt(np.abs(raw)), raw * raw])
    if kind == "raw_cubic":
        return np.column_stack([raw, raw * raw, raw * raw * raw])
    if kind == "physics_compact":
        # Firmware-friendly summary: global magnitude plus eight sensor magnitudes.
        return np.column_stack([activity, norm, peak, magnitudes, np.sqrt(np.maximum(activity, 0.0))])
    raise ValueError(kind)


def metrics(y: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    pred = np.clip(pred, 0.0, 35.0)
    loaded = y > 0.0
    low = loaded & (y < 2.0)
    zero = ~loaded
    return {
        "loaded_mae_n": float(np.mean(np.abs(pred[loaded] - y[loaded]))),
        "loaded_below_2n_mae_n": float(np.mean(np.abs(pred[low] - y[low]))),
        "loaded_p95_abs_n": float(np.percentile(np.abs(pred[loaded] - y[loaded]), 95)),
        "loaded_within_1n_rate": float(np.mean(np.abs(pred[loaded] - y[loaded]) <= 1.0)),
        "loaded_zero_rate": float(np.mean(pred[loaded] <= 0.05)),
        "zero_median_n": float(np.median(pred[zero])),
        "zero_p95_n": float(np.percentile(pred[zero], 95)),
        "zero_over_0p05_rate": float(np.mean(pred[zero] > 0.05)),
    }


def cross_validate(data: DataSet, group_values: np.ndarray | None = None) -> list[dict[str, object]]:
    group_values = data.groups if group_values is None else np.asarray(group_values)
    if len(np.unique(group_values)) < 2:
        return []
    results: list[dict[str, object]] = []
    for kind in ("raw_linear", "raw_quadratic", "raw_cubic", "physics_compact"):
        x = features(data.x, kind)
        for alpha in (0.1, 1.0, 10.0, 100.0):
            fold_metrics = []
            for group in np.unique(group_values):
                train = group_values != group
                test = ~train
                # Retain low-force points without allowing them to dominate the fit.
                sample_weight = np.where(data.y[train] <= 1.0, 2.0, 1.0)
                model = ridge_fit(x[train], data.y[train], alpha, sample_weight)
                fold_metrics.append(metrics(data.y[test], ridge_predict(model, x[test])))
            aggregate = {
                key: float(np.mean([fold[key] for fold in fold_metrics]))
                for key in fold_metrics[0]
            }
            results.append({"features": kind, "alpha": alpha, **aggregate})
    return sorted(results, key=lambda row: (row["loaded_mae_n"], row["zero_p95_n"]))


def cross_validate_deployed_postcalibration(data: DataSet, source_dir: Path) -> list[dict[str, object]]:
    sys.path.insert(0, str(source_dir))
    import h3_v3_3_force_model as model  # pylint: disable=import-error,import-outside-toplevel

    deployed = np.asarray([model.estimate_force(row)[0] for row in data.x], dtype=float)
    results = []
    for degree in (1, 2, 3):
        feature = np.column_stack([deployed ** power for power in range(1, degree + 1)])
        folds = []
        for group in np.unique(data.groups):
            train = (data.groups != group) & (data.y > 0.0)
            test = (data.groups == group) & (data.y > 0.0)
            calibration = ridge_fit(feature[train], data.y[train], alpha=1.0)
            prediction = np.clip(ridge_predict(calibration, feature[test]), 0.0, 35.0)
            reference = data.y[test]
            low = reference < 2.0
            folds.append({
                "mae_n": float(np.mean(np.abs(prediction - reference))),
                "low_mae_n": float(np.mean(np.abs(prediction[low] - reference[low]))),
                "p95_abs_n": float(np.percentile(np.abs(prediction - reference), 95)),
                "within_1n_rate": float(np.mean(np.abs(prediction - reference) <= 1.0)),
            })
        results.append({
            "degree": degree,
            **{key: float(np.mean([fold[key] for fold in folds])) for key in folds[0]},
        })
    return results


def _lookup_predict(base: np.ndarray, y: np.ndarray, prediction: np.ndarray) -> np.ndarray:
    levels = np.unique(y)
    measured = np.asarray([np.median(base[y == level]) for level in levels], dtype=float)
    measured = np.maximum.accumulate(measured)
    keep = np.r_[True, np.diff(measured) > 1e-9]
    return np.interp(prediction, measured[keep], levels[keep], left=0.0, right=float(levels[keep][-1]))


def cross_validate_deployed_position_lookup(
    data: DataSet, source_dir: Path, group_values: np.ndarray
) -> dict[str, float]:
    if len(np.unique(group_values)) < 2:
        return {}
    sys.path.insert(0, str(source_dir))
    import h3_v3_3_force_model as model  # pylint: disable=import-error,import-outside-toplevel

    deployed = np.asarray([model.estimate_force(row)[0] for row in data.x], dtype=float)
    fold_metrics = []
    for group in np.unique(group_values):
        train = (group_values != group) & (data.y > 0.0)
        test = (group_values == group) & (data.y > 0.0)
        prediction = np.zeros(int(test.sum()), dtype=float)
        test_positions = data.positions[test]
        for position in np.unique(test_positions):
            train_position = train & (data.positions == position)
            test_position = test_positions == position
            prediction[test_position] = _lookup_predict(
                deployed[train_position], data.y[train_position], deployed[test][test_position]
            )
        reference = data.y[test]
        low = reference < 2.0
        error = np.abs(prediction - reference)
        fold_metrics.append({
            "loaded_mae_n": float(np.mean(error)),
            "loaded_below_2n_mae_n": float(np.mean(error[low])),
            "loaded_p95_abs_n": float(np.percentile(error, 95)),
            "loaded_within_1n_rate": float(np.mean(error <= 1.0)),
        })
    return {
        key: float(np.mean([fold[key] for fold in fold_metrics]))
        for key in fold_metrics[0]
    }


def evaluate_existing(data: DataSet, source_dir: Path) -> dict[str, object]:
    sys.path.insert(0, str(source_dir))
    import h3_v3_3_force_model as model  # pylint: disable=import-error,import-outside-toplevel

    prediction = np.asarray([model.estimate_force(row)[0] for row in data.x], dtype=float)
    regression = []
    load_scores = []
    region_scores = []
    norms = []
    for row in data.x:
        bounded = np.clip(
            row,
            model.RAW_MIN - 0.25 * np.maximum(model.RAW_MAX - model.RAW_MIN, 1.0),
            model.RAW_MAX + 0.25 * np.maximum(model.RAW_MAX - model.RAW_MIN, 1.0),
        )
        polynomial = np.concatenate((bounded, bounded * bounded, bounded * bounded * bounded))
        standardized = (polynomial - model.FEATURE_MEAN) / model.FEATURE_SCALE
        regression.append(model.clamp_force(model.MODEL_WEIGHT[0] + np.dot(model.MODEL_WEIGHT[1:], standardized)))
        raw_standardized = (row - model.FEATURE_MEAN[:24]) / model.FEATURE_SCALE[:24]
        load_scores.append(model.LOAD_WEIGHT[0] + np.dot(model.LOAD_WEIGHT[1:], raw_standardized))
        norm = float(np.linalg.norm(row))
        norms.append(norm)
        region_scores.append(float(np.max(model.REGION_PROTOTYPES @ (row / max(norm, 1e-12)))))
    regression = np.asarray(regression)
    load_scores = np.asarray(load_scores)
    region_scores = np.asarray(region_scores)
    norms = np.asarray(norms)
    loaded = data.y > 0.0
    zero = ~loaded
    return {
        "deployed_model": metrics(data.y, prediction),
        "regression_without_gate": metrics(data.y, regression),
        "regression_zero_max_n": float(np.max(regression[zero])),
        "regression_loaded_p05_n": float(np.percentile(regression[loaded], 5)),
        "load_score_loaded_min": float(np.min(load_scores[loaded])),
        "load_score_loaded_p05": float(np.percentile(load_scores[loaded], 5)),
        "load_score_zero_max": float(np.max(load_scores[zero])),
        "load_score_zero_p95": float(np.percentile(load_scores[zero], 95)),
        "region_score_loaded_p05": float(np.percentile(region_scores[loaded], 5)),
        "region_score_zero_max": float(np.max(region_scores[zero])),
        "region_score_zero_p95": float(np.percentile(region_scores[zero], 95)),
        "norm_loaded_p05": float(np.percentile(norms[loaded], 5)),
        "norm_zero_max": float(np.max(norms[zero])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", nargs="+", type=Path)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    data = load_summary(args.summary)
    report = {
        "rows": int(len(data.y)),
        "groups": sorted(np.unique(data.groups).tolist()),
        "existing_model_all_rows": evaluate_existing(data, args.source_dir),
        "candidate_leave_group_out": cross_validate(data),
        "candidate_leave_session_out": cross_validate(data, data.sessions),
        "deployed_postcalibration_leave_group_out": cross_validate_deployed_postcalibration(data, args.source_dir),
        "deployed_position_lookup_leave_group_out": cross_validate_deployed_position_lookup(
            data, args.source_dir, data.groups
        ),
        "deployed_position_lookup_leave_session_out": cross_validate_deployed_position_lookup(
            data, args.source_dir, data.sessions
        ),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
