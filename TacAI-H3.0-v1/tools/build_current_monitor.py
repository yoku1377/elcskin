"""Build current v5.3.28 PC sources without a historical release baseline.

Only offline tests and PyInstaller run. No application, COM port, USB adapter,
probe, or flashing tool is started. Importing this module has no side effects.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "pc/TfgMonitorPython_H3_v5.0"
VERSION = "5.3.28-zp500n-stream"
NAME = "TacAI H3 v5.3.28 ZP500N Stream"
PROFILES = (
    "local_force_profile.json", "back_force_profile.json", "back25_consistency_profile.json",
    "back25_smooth_profile.json", "back25_domain_profile.json",
)
RESOURCE_HASHES = {
    "CH347DLLA64.dll": "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375",
    "CH341DLLA64.DLL": "0eeed57d53f0ff9b6daaaab87207ff90c2e03762441aba5ed0904b3f7d5f4737",
    "TacAI_H3_App_SPI_rxirq_v5.tacai": "c5e09fd9a30a4da4916a774da45c7b94ee9ba88f511b7c7a2b04b90b4f944d42",
}
# Explicit v28 suite: do not silently replace it with discovery of obsolete tests.
CHECKS = (
    "test_ch341_spi", "test_ch347_spi", "test_spi_iap_protocol", "test_spi_monitor_ui",
    "test_uart_upgrade_recovery", "test_uart_iap_protocol", "test_baseline_quality",
    "test_recording_availability", "test_force_trace", "test_force_trace_monitor",
    "test_residual_zero", "test_h3_v3_3_force_model", "test_acquisition_diagnostics",
    "test_local_state", "test_local_high_force", "test_local_force_monitor",
    "test_automatic_compensation", "test_high_force_protocol", "test_high_force_monitor",
    "test_back_total_force", "test_back25_consistency_force", "test_back25_smooth_force",
    "test_back25_domain_force", "test_regional_high_force", "test_regional_ard_force",
    "test_six_hall_force", "test_release_baseline", "test_release_baseline_monitor",
    "test_release_baseline_replay", "test_recording_report", "test_folder_report",
    "test_markdown_report", "test_consistency_report", "test_calibration_acquisition",
    "test_calibration_gap_dialog", "test_calibration_gap_monitor", "test_calibration_trajectory",
    "test_calibration_trajectory_monitor", "test_zp500n_protocol", "test_zp500n_gauge",
    "test_spi_snapshot_ack_guard", "test_zp500n_live_replay", "test_zp500n_backlog",
)
EXCLUDED_DIRS = frozenset({
    "__pycache__", ".pytest_cache", ".mypy_cache", ".git", ".venv", "venv",
    "logs", "build", "dist",
})
DATA_TREES = frozenset({"assets", "testdata", "models"})
SOURCE_FILES_POLICY = {
    "recursive_code_and_models": ["*.py", "*.json"],
    "complete_data_trees": sorted(DATA_TREES),
    "root_files": ["requirements.txt", *RESOURCE_HASHES],
    "excluded_directories": sorted(EXCLUDED_DIRS),
    "excluded_extensions": [".pyc", ".pyo", ".spec"],
    "other_dlls_and_tacai_images": "excluded",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(directory: Path) -> dict[str, Path]:
    """Select reproducible PC inputs; also used by an independent verifier."""
    directory = directory.resolve()
    selected = {}
    for path in directory.rglob("*"):
        relative = path.relative_to(directory)
        if not path.is_file() or any(part.casefold() in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(directory):
            raise RuntimeError(f"Source link is not allowed: {path}")
        suffix = path.suffix.casefold()
        if suffix in {".pyc", ".pyo", ".spec"}:
            continue
        name = relative.as_posix()
        if suffix in {".dll", ".tacai"}:
            if name not in RESOURCE_HASHES:
                continue
        elif (suffix not in {".py", ".json"} and name != "requirements.txt"
              and relative.parts[0] not in DATA_TREES):
            continue
        selected[name] = path
    return dict(sorted(selected.items()))


def constant(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(item, ast.Name) and item.id == name
                                               for item in node.targets):
            return ast.literal_eval(node.value)
    raise RuntimeError(f"Missing source version constant {name}: {path}")


def isolated_dependencies(work: Path, dependency_dirs: list[Path]):
    """Copy only missing optional packages, never an entire site-packages tree."""
    destination = work / "isolateddeps"
    destination.mkdir()
    records = {}
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    for module, distribution in (("openpyxl", "openpyxl"), ("et_xmlfile", "et_xmlfile"), ("PIL", "pillow")):
        spec = importlib.util.find_spec(module)
        if spec is not None:
            records[module] = {"mode": "selected_python_environment", "origin": spec.origin}
            continue
        base = next((path for path in dependency_dirs if (path / module / "__init__.py").is_file()), None)
        if base is None:
            raise RuntimeError(f"Missing {module}; install it in this venv or provide --dependency-dir")
        shutil.copytree(base / module, destination / module, ignore=ignore)
        for extra in base.iterdir():
            if (extra.is_dir() and extra.name.casefold().startswith(distribution.casefold())
                    and extra.name.casefold().endswith((".dist-info", ".libs"))):
                shutil.copytree(extra, destination / extra.name, ignore=ignore)
        records[module] = {"mode": "isolated_copy", "origin": str(base / module)}
    return destination, records


def run_logged(command, *, cwd: Path, env: dict, log: Path):
    print(f"RUN {log.name}", flush=True)
    with log.open("x", encoding="utf-8") as output:
        subprocess.run(command, cwd=cwd, env=env, check=True, stdout=output, stderr=subprocess.STDOUT)


def test_result(log: Path):
    text = log.read_text(encoding="utf-8")
    match = re.search(r"^Ran (\d+) tests? in ", text, re.MULTILINE)
    result = re.search(r"^OK(?: \(skipped=(\d+)\))?\s*$", text, re.MULTILINE)
    if match is None or result is None or "FAILED" in text:
        raise RuntimeError(f"No successful unittest result: {log}")
    return {"run": int(match.group(1)), "skipped": int(result.group(1) or 0)}


def firmware_inputs():
    paths = [ROOT / "tests/test_spi_snapshot_stale_firmware.py", ROOT / "tests/test_spi_firmware.py",
             ROOT / "firmware/application/Core/Src/app_spi.c"]
    for folder in ("tests/firmware_spi", "firmware/shared", "firmware/application/Core/Inc"):
        paths.extend(path for path in (ROOT / folder).rglob("*") if path.suffix in {".c", ".h"})
    return {path.relative_to(ROOT).as_posix(): sha(path) for path in sorted(set(paths))}


def build(args):
    if os.name != "nt":
        raise RuntimeError("This release target requires Windows and the selected Windows build venv")
    date = datetime.strptime(args.date, "%Y%m%d")
    if date.strftime("%Y%m%d") != args.date:
        raise RuntimeError("--date must be YYYYMMDD")
    if constant(CURRENT / "main.py", "MONITOR_VERSION") != VERSION:
        raise RuntimeError("Current PC source is not the reviewed v5.3.28 version")
    if constant(CURRENT / "firmware_updater.py", "UPGRADER_VERSION") != "5.3.28":
        raise RuntimeError("Updater version does not match v5.3.28")
    resources = args.resources.resolve(strict=True)
    for name, expected in RESOURCE_HASHES.items():
        path = resources / name
        if not path.is_file() or sha(path) != expected:
            raise RuntimeError(f"Missing resource or SHA256 mismatch: {path}")
    inputs = source_files(CURRENT)
    required = {"main.py", "qt_platform_runtime_hook.py", "requirements.txt", *PROFILES,
                *(name + ".py" for name in CHECKS)}
    if required - inputs.keys():
        raise RuntimeError(f"Missing current source inputs: {sorted(required - inputs.keys())}")
    work = ROOT / f"tmp/current_monitor_{args.date}_build"
    release = ROOT / f"release/TacAI_H3_v5_3_28_ZP500NStream_{args.date}"
    report = ROOT / f"outputs/current_monitor_{args.date}"
    for path in (work, release):
        if path.exists():
            raise RuntimeError(f"Refusing to overwrite an existing build: {path}")
    for name in ("manifest.json", "offline_tests.log", "firmware_state_repro.log", "build.log", "dependencies.json"):
        if (report / name).exists():
            raise RuntimeError(f"Refusing to replace build evidence: {report / name}")
    report.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True)
    release.mkdir(parents=True)
    frozen = work / "source"
    frozen.mkdir()
    origins = {}
    current_hashes = {name: sha(path) for name, path in inputs.items()}
    for name, path in inputs.items():
        target = frozen / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        origins[name] = str(path)
    for name in RESOURCE_HASHES:
        shutil.copy2(resources / name, frozen / name)
        origins[name] = str(resources / name)
    frozen_hashes = {name: sha(path) for name, path in source_files(frozen).items()}
    for name, expected in current_hashes.items():
        if name not in RESOURCE_HASHES and frozen_hashes[name] != expected:
            raise RuntimeError(f"Source changed while freezing: {name}")
    for name, expected in RESOURCE_HASHES.items():
        if frozen_hashes[name] != expected:
            raise RuntimeError(f"Frozen resource changed: {name}")
    dependencies, copied = isolated_dependencies(work, [path.resolve(strict=True) for path in args.dependency_dir])
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
               TACAI_UPGRADE_LOG_DIR=str(work / "fake_upgrade_logs"),
               TACAI_STREAM_LOG_DIR=str(work / "fake_stream_logs"),
               TACAI_FORCE_TRACE_DIR=str(work / "fake_force_traces"),
               PYTHONPATH=os.pathsep.join((str(frozen), str(dependencies))),
               PYINSTALLER_CONFIG_DIR=str(work / "pyinstaller_cache"))
    checked = subprocess.run(
        [sys.executable, "-B", "-c", "import json,sys,PyInstaller,openpyxl,PIL,et_xmlfile; "
         "print(json.dumps(dict(python=sys.version,pyinstaller=PyInstaller.__version__,"
         "openpyxl=openpyxl.__version__,pillow=PIL.__version__)))"],
        cwd=frozen, env=env, check=True, capture_output=True, text=True, encoding="utf-8")
    environment = json.loads(checked.stdout)
    (report / "dependencies.json").write_text(json.dumps(
        {"environment": environment, "optional_dependency_sources": copied}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Frozen current source: {frozen}", flush=True)
    run_logged([sys.executable, "-B", "-m", "unittest", *CHECKS, "-v"],
               cwd=frozen, env=env, log=report / "offline_tests.log")
    offline = test_result(report / "offline_tests.log")
    c_hashes = firmware_inputs()
    run_logged([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p",
                "test_spi_snapshot_stale_firmware.py", "-v"],
               cwd=ROOT, env=env, log=report / "firmware_state_repro.log")
    firmware = test_result(report / "firmware_state_repro.log")
    c_log = (report / "firmware_state_repro.log").read_text(encoding="utf-8")
    for marker in ("PASS: 3 production RXIRQ stale snapshot/reissue recovery cases",
                   "PASS: 7 production RXIRQ/mailbox/snapshot ACK-drain guard cases"):
        if marker not in c_log:
            raise RuntimeError(f"Required production C injection did not execute: {marker}")
    if c_hashes != firmware_inputs():
        raise RuntimeError("Firmware injection inputs changed during tests")
    command = [sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--name", NAME, "--distpath", str(release), "--workpath", str(work / "work"),
               "--specpath", str(work), "--paths", str(frozen), "--paths", str(dependencies),
               "--exclude-module", "pandas", "--exclude-module", "scipy", "--collect-data", "openpyxl",
               "--collect-submodules", "openpyxl", "--runtime-hook", str(frozen / "qt_platform_runtime_hook.py"),
               "--add-binary", f"{frozen / 'CH347DLLA64.dll'};.",
               "--add-binary", f"{frozen / 'CH341DLLA64.DLL'};.",
               "--add-data", f"{frozen / 'assets'};assets"]
    for name in (*PROFILES, "TacAI_H3_App_SPI_rxirq_v5.tacai"):
        command += ["--add-data", f"{frozen / name};."]
    command.append(str(frozen / "main.py"))
    run_logged(command, cwd=frozen, env=env, log=report / "build.log")
    if frozen_hashes != {name: sha(path) for name, path in source_files(frozen).items()}:
        raise RuntimeError("Frozen sources changed during tests or build")
    if current_hashes != {name: sha(path) for name, path in source_files(CURRENT).items()}:
        raise RuntimeError("Current PC sources changed while building")
    executable = release / (NAME + ".exe")
    if set(release.iterdir()) != {executable} or not executable.is_file():
        raise RuntimeError("Standalone release must contain exactly one EXE")
    manifest = {
        "version": VERSION, "date": date.strftime("%Y-%m-%d"), "build_kind": "current_source_rebuild",
        "source_root": str(CURRENT), "frozen_source": str(frozen), "source_files_policy": SOURCE_FILES_POLICY,
        "source_sha256": frozen_hashes, "source_origins": origins, "current_source_sha256": current_hashes,
        "profile_sha256": {name: frozen_hashes[name] for name in PROFILES}, "resource_sha256": RESOURCE_HASHES,
        "exe": str(executable), "exe_sha256": sha(executable), "exe_bytes": executable.stat().st_size,
        "standalone_exe": True, "zip_created": False, "tests": list(CHECKS),
        "offline_tests": offline, "firmware_injection_tests": firmware,
        "firmware_injection_source_sha256": c_hashes,
        "build_environment": environment, "dependency_sources": copied,
        "build_command": command, "builder_sha256": sha(Path(__file__)),
        "force_zero_scale_and_models_changed_by_builder": False,
        "hardware_accessed": False, "gui_executable_run_by_builder": False,
        "gauge_clock_alignment": "host receipt only; no instrument timestamp",
        "gauge_supported_profile": "observed ZP-500N N-mode, 6-byte positive fields, 0..500 N",
        "unverified_wire_formats": ["negative values", "non-N units", "other precisions"],
        "hardware_validation": "Offline tests and field-byte replay; no new hardware validation performed",
    }
    with (report / "manifest.json").open("x", encoding="utf-8") as output:
        json.dump(manifest, output, ensure_ascii=False, indent=2)
    print(json.dumps({"exe": str(executable), "sha256": manifest["exe_sha256"],
                      "tests": offline, "report": str(report)}, ensure_ascii=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, required=True,
                        help="Explicit directory containing the three SHA256-pinned packaging resources")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="Build date YYYYMMDD")
    parser.add_argument("--dependency-dir", type=Path, action="append", default=[],
                        help="Optional offline site-packages source; only missing openpyxl/et_xmlfile/PIL are copied")
    build(parser.parse_args(argv))


if __name__ == "__main__":
    main()
