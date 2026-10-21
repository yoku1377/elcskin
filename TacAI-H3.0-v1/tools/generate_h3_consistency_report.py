"""Generate normalized H3 consistency metrics from calibration CSV sessions.

The source CSV files are read-only.  This script writes a compact JSON payload
that is consumed by the artifact-tool workbook builder.  It supports both the
legacy v5.2.1 shifted headers and the corrected v5.2.2 headers.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_h3_session import read_recoverable_csv


POSITION_ORDER = {"前": 0, "中": 1, "后": 2, "front": 0, "middle": 1, "rear": 2}
LOAD_WORDS = {"加载", "load", "loading"}
UNLOAD_WORDS = {"卸载", "unload", "unloading"}


def _number(value, default=math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _text(value, default="") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    return str(value).strip()


def _direction(value: object) -> str:
    text = _text(value).lower()
    if text in LOAD_WORDS:
        return "加载"
    if text in UNLOAD_WORDS:
        return "卸载"
    return _text(value)


def _session_id(path: Path) -> str:
    match = re.search(r"(\d{8}_\d{6})", path.stem)
    return match.group(1) if match else path.stem.replace("_summary", "")


def _safe_stats(values: pd.Series | np.ndarray) -> dict[str, float | None]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return {"mean": None, "median": None, "p95": None, "max": None}
    return {
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "p95": float(np.percentile(array, 95)),
        "max": float(np.max(array)),
    }


def _metadata_from_frames(path: Path) -> tuple[dict[str, str], int, bool]:
    if not path.exists():
        return {}, 0, False
    frames, recovered = read_recoverable_csv(path, "frames")
    metadata = {}
    for column in ("device_sn", "silicone_batch", "magnet_assembly", "operator"):
        if column not in frames:
            continue
        values = [_text(value) for value in frames[column].dropna().tolist()]
        values = [value for value in values if value]
        if values:
            metadata[column] = values[0]
    return metadata, int(len(frames)), bool(recovered)


def load_sessions(input_dir: Path) -> tuple[pd.DataFrame, list[dict[str, object]], dict[str, dict[str, str]]]:
    rows: list[pd.DataFrame] = []
    sources: list[dict[str, object]] = []
    metadata_by_device: dict[str, dict[str, str]] = {}

    for summary_path in sorted(input_dir.rglob("*_summary.csv")):
        summary, recovered = read_recoverable_csv(summary_path, "summary")
        frames_path = summary_path.with_name(summary_path.name.replace("_summary.csv", "_frames.csv"))
        metadata, frame_rows, frames_recovered = _metadata_from_frames(frames_path)
        source = {
            "session_id": _session_id(summary_path),
            "summary_file": summary_path.name,
            "frames_file": frames_path.name if frames_path.exists() else "",
            "summary_rows": int(len(summary)),
            "frame_rows": frame_rows,
            "header_recovered": bool(recovered or frames_recovered),
            "status": "可用" if len(summary) else "空文件，已跳过",
        }
        sources.append(source)
        if summary.empty:
            continue

        table = summary.copy()
        table["session_id"] = source["session_id"]
        table["source_file"] = summary_path.name
        table["device_id"] = table.get("device_sn", pd.Series(index=table.index, dtype=object)).map(_text)
        fallback = metadata.get("device_sn") or summary_path.parent.name or source["session_id"]
        table.loc[table["device_id"] == "", "device_id"] = fallback
        table["direction_norm"] = table["direction"].map(_direction)
        for column in ("repeat", "step_index", "target_n", "reference_median_n", "upper_model_median_n", "temperature_c"):
            table[column] = pd.to_numeric(table.get(column), errors="coerce")
        table["position_norm"] = table["position"].map(_text)
        table["quality_norm"] = table.get("quality", pd.Series(index=table.index, dtype=object)).map(_text)
        rows.append(table)
        for device_id in table["device_id"].unique():
            metadata_by_device.setdefault(device_id, {}).update(metadata)

    if not rows:
        raise ValueError(f"No non-empty *_summary.csv files found under {input_dir}")
    return pd.concat(rows, ignore_index=True), sources, metadata_by_device


def mark_post_unload_zero(table: pd.DataFrame) -> pd.Series:
    result = pd.Series(False, index=table.index)
    group_columns = ["device_id", "session_id", "repeat", "position_norm"]
    for _, group in table.sort_values([*group_columns, "step_index"]).groupby(group_columns, dropna=False):
        has_loaded = False
        for index, row in group.iterrows():
            reference = _number(row["reference_median_n"])
            if reference > 0:
                has_loaded = True
            elif has_loaded and reference == 0:
                result.loc[index] = True
    return result


def build_report(table: pd.DataFrame, sources: list[dict[str, object]], metadata_by_device: dict[str, dict[str, str]]) -> dict[str, object]:
    table = table.copy()
    table["post_unload_zero"] = mark_post_unload_zero(table)
    table["error_n"] = table["upper_model_median_n"] - table["reference_median_n"]
    table["abs_error_n"] = table["error_n"].abs()
    table["loaded"] = table["reference_median_n"] > 0
    table["low_force"] = table["loaded"] & (table["reference_median_n"] <= 2.0)

    repeatability_rows = []
    repeat_group = ["device_id", "position_norm", "direction_norm", "reference_median_n"]
    for keys, group in table[table["loaded"]].groupby(repeat_group, dropna=False):
        values = group["upper_model_median_n"].dropna().to_numpy(float)
        if len(values) >= 2:
            repeatability_rows.append({"device_id": keys[0], "std_n": float(np.std(values, ddof=1))})
    repeatability = pd.DataFrame(repeatability_rows)

    hysteresis_rows = []
    hysteresis_group = ["device_id", "session_id", "repeat", "position_norm", "reference_median_n"]
    for keys, group in table[table["loaded"]].groupby(hysteresis_group, dropna=False):
        loaded = group.loc[group["direction_norm"] == "加载", "upper_model_median_n"].dropna()
        unloaded = group.loc[group["direction_norm"] == "卸载", "upper_model_median_n"].dropna()
        if len(loaded) and len(unloaded):
            hysteresis_rows.append({"device_id": keys[0], "difference_n": abs(float(loaded.median() - unloaded.median()))})
    hysteresis = pd.DataFrame(hysteresis_rows)

    spread_rows = []
    spread_group = ["device_id", "session_id", "repeat", "direction_norm", "reference_median_n"]
    for keys, group in table[table["loaded"]].groupby(spread_group, dropna=False):
        by_position = group.groupby("position_norm")["upper_model_median_n"].median().dropna()
        if len(by_position) >= 2:
            spread_rows.append({"device_id": keys[0], "spread_n": float(by_position.max() - by_position.min())})
    spread = pd.DataFrame(spread_rows)

    device_metrics = []
    for device_id, group in table.groupby("device_id"):
        loaded = group[group["loaded"]]
        low = group[group["low_force"]]
        zero = group[group["post_unload_zero"]]
        rep = repeatability.loc[repeatability.get("device_id", pd.Series(dtype=object)) == device_id, "std_n"] if not repeatability.empty else pd.Series(dtype=float)
        hyst = hysteresis.loc[hysteresis.get("device_id", pd.Series(dtype=object)) == device_id, "difference_n"] if not hysteresis.empty else pd.Series(dtype=float)
        pos_spread = spread.loc[spread.get("device_id", pd.Series(dtype=object)) == device_id, "spread_n"] if not spread.empty else pd.Series(dtype=float)
        meta = metadata_by_device.get(device_id, {})
        device_metrics.append({
            "device_id": device_id,
            "silicone_batch": meta.get("silicone_batch", ""),
            "magnet_assembly": meta.get("magnet_assembly", ""),
            "operator": meta.get("operator", ""),
            "session_count": int(group["session_id"].nunique()),
            "repeat_count": int(group["repeat"].nunique()),
            "position_count": int(group["position_norm"].nunique()),
            "loaded_steps": int(len(loaded)),
            "overall_mae_n": float(loaded["abs_error_n"].mean()),
            "low_force_mae_n": float(low["abs_error_n"].mean()) if len(low) else None,
            "within_1n_rate": float((loaded["abs_error_n"] <= 1.0).mean()),
            "p95_abs_error_n": float(np.percentile(loaded["abs_error_n"].dropna(), 95)),
            "max_abs_error_n": float(loaded["abs_error_n"].max()),
            "repeatability_p95_n": _safe_stats(rep)["p95"],
            "hysteresis_p95_n": _safe_stats(hyst)["p95"],
            "residual_median_n": _safe_stats(zero["upper_model_median_n"].abs())["median"],
            "residual_p95_n": _safe_stats(zero["upper_model_median_n"].abs())["p95"],
            "residual_max_n": _safe_stats(zero["upper_model_median_n"].abs())["max"],
            "position_spread_p95_n": _safe_stats(pos_spread)["p95"],
            "bad_quality_steps": int((group["quality_norm"].str.upper() != "OK").sum()),
        })

    position_metrics = []
    for (device_id, position), group in table[table["loaded"]].groupby(["device_id", "position_norm"]):
        low = group[group["reference_median_n"] <= 2.0]
        position_metrics.append({
            "device_id": device_id,
            "position": position,
            "steps": int(len(group)),
            "mae_n": float(group["abs_error_n"].mean()),
            "low_force_mae_n": float(low["abs_error_n"].mean()) if len(low) else None,
            "bias_n": float(group["error_n"].mean()),
            "within_1n_rate": float((group["abs_error_n"] <= 1.0).mean()),
            "p95_abs_error_n": float(np.percentile(group["abs_error_n"].dropna(), 95)),
        })

    force_points = []
    point_columns = ["device_id", "position_norm", "direction_norm", "reference_median_n"]
    for keys, group in table[table["loaded"]].groupby(point_columns, dropna=False):
        outputs = group["upper_model_median_n"].dropna().to_numpy(float)
        errors = group["error_n"].dropna().to_numpy(float)
        force_points.append({
            "device_id": keys[0],
            "position": keys[1],
            "direction": keys[2],
            "reference_n": float(keys[3]),
            "samples": int(len(outputs)),
            "output_mean_n": float(np.mean(outputs)),
            "output_std_n": float(np.std(outputs, ddof=1)) if len(outputs) >= 2 else 0.0,
            "bias_n": float(np.mean(errors)),
            "mae_n": float(np.mean(np.abs(errors))),
        })

    consistency = []
    condition_columns = ["position_norm", "direction_norm", "reference_median_n"]
    for keys, group in table[table["loaded"]].groupby(condition_columns, dropna=False):
        by_device = group.groupby("device_id")["upper_model_median_n"].median().dropna()
        if len(by_device) < 2:
            continue
        reference = float(keys[2])
        consistency.append({
            "position": keys[0],
            "direction": keys[1],
            "reference_n": reference,
            "device_count": int(len(by_device)),
            "device_mean_n": float(by_device.mean()),
            "device_std_n": float(by_device.std(ddof=1)),
            "cv_percent_of_reference": float(by_device.std(ddof=1) / max(abs(reference), 1e-9) * 100.0),
            "device_spread_n": float(by_device.max() - by_device.min()),
        })

    raw_steps = []
    raw_columns = [
        "device_id", "session_id", "completed_time_iso", "repeat", "position_norm", "direction_norm",
        "target_n", "reference_median_n", "upper_model_median_n", "error_n", "abs_error_n",
        "temperature_c", "quality_norm", "post_unload_zero", "source_file",
    ]
    for _, row in table.sort_values(["device_id", "session_id", "step_index"]).iterrows():
        raw_steps.append({column: row.get(column) for column in raw_columns})

    return {
        "generated_from": str(Path.cwd()),
        "sources": sources,
        "device_metrics": device_metrics,
        "position_metrics": sorted(position_metrics, key=lambda row: (row["device_id"], POSITION_ORDER.get(row["position"], 99))),
        "force_points": sorted(force_points, key=lambda row: (row["device_id"], POSITION_ORDER.get(row["position"], 99), row["direction"], row["reference_n"])),
        "device_consistency": sorted(consistency, key=lambda row: (POSITION_ORDER.get(row["position"], 99), row["direction"], row["reference_n"])),
        "raw_steps": raw_steps,
    }


def clean_json(value):
    if isinstance(value, dict):
        return {key: clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate H3 consistency evaluation input and Excel report")
    parser.add_argument("input_dir", type=Path, help="Folder containing *_summary.csv and *_frames.csv files")
    parser.add_argument("--json", type=Path, required=True, help="Intermediate JSON output")
    parser.add_argument("--xlsx", type=Path, help="Final Excel output")
    parser.add_argument("--node", type=Path, help="Node.js executable")
    parser.add_argument("--builder", type=Path, help="Artifact-tool workbook builder")
    args = parser.parse_args()

    table, sources, metadata = load_sessions(args.input_dir)
    report = clean_json(build_report(table, sources, metadata))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"JSON: {args.json}")
    print(f"Devices: {len(report['device_metrics'])}; sessions: {sum(1 for source in sources if source['summary_rows'])}")

    if args.xlsx:
        if not args.node or not args.builder:
            parser.error("--xlsx requires --node and --builder")
        args.xlsx.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(args.node), str(args.builder), str(args.json), str(args.xlsx)], check=True)
        print(f"XLSX: {args.xlsx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
