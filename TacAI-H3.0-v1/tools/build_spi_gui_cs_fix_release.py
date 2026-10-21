"""Build the isolated v5.3.15 GUI release from delivered v5.3.14 sources.

No device is opened and no GUI executable is run by this builder. The only
production changes are the reviewed CH347 CS levels and three version labels.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "release/TacAI_H3_v5_3_14_RecordingFix_20261016"
CURRENT = ROOT / "pc/TfgMonitorPython_H3_v5.0"
OUT = ROOT / "release/TacAI_H3_v5_3_15_SPI_CSFix_20261019"
WORK = ROOT / "tmp/spi_gui_cs_fix_20261019_build"
PACKAGE = ROOT / "release/TacAI_H3_v5_3_15_SPI_CSFix_20261019.zip"
NAME = "TacAI H3 v5.3.15 SPI CS Fix"
PROFILES = ("local_force_profile.json", "back_force_profile.json", "back25_consistency_profile.json",
            "back25_smooth_profile.json", "back25_domain_profile.json")
ALLOWED = frozenset(("ch347_spi.py", "main.py", "test_ch347_spi.py"))
TEST_FIXTURE = "testdata/ch347_dll120_interfaces.json"
EXPECTED_DLL = "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375"
CS_ADDITION = (
    "# ChangeCS forwards the physical CS level to C1 bit 6: 0 asserts the\n"
    "# configured low-active line, 1 releases it. Verified in the shipped x64\n"
    "# DLL (SetChipSelect RVA 0xE970, ChangeCS 0xEB30) and WCH's Linux driver\n"
    "# (SET_CS=0, CLR_CS=1); some Windows-header comments reverse these values.\n"
    "CS_ASSERT_LEVEL = 0\nCS_DEASSERT_LEVEL = 1\n"
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    return digest(path.read_bytes())


def normalized(data):
    return data.decode("utf-8-sig").replace("\r\n", "\n")


def checked_backend(baseline):
    """Fail before building if the live driver contains any other changes."""
    old = normalized(baseline)
    marker = "CS_HIGH_SECONDS = 0.002\n"
    assert old.count(marker) == 1
    expected = old.replace(marker, marker + CS_ADDITION, 1)
    changes = (
        ('CH347SPI_ChangeCS(self.device_index, 0), "CH347SPI_ChangeCS(deassert)"',
         'CH347SPI_ChangeCS(self.device_index, CS_DEASSERT_LEVEL), "CH347SPI_ChangeCS(deassert)"'),
        ('CH347SPI_ChangeCS(self.device_index, 1), "CH347SPI_ChangeCS(assert)"',
         'CH347SPI_ChangeCS(self.device_index, CS_ASSERT_LEVEL), "CH347SPI_ChangeCS(assert)"'),
    )
    for before, after in changes:
        assert expected.count(before) == 1, before
        expected = expected.replace(before, after, 1)
    current = (CURRENT / "ch347_spi.py").read_bytes()
    assert normalized(current) == expected, "Current driver has changes beyond the reviewed CS fix"
    return current


def version_labels(baseline):
    text = baseline.decode("utf-8-sig")
    replacements = (
        ('MONITOR_VERSION = "5.3.14-recording-availability-fix"',
         'MONITOR_VERSION = "5.3.15-spi-cs-fix"'),
        ('self.setWindowTitle("TacAI H3 v5.3.14 Recording Fix")',
         'self.setWindowTitle("TacAI H3 v5.3.15 SPI CS Fix")'),
        ('"v5.3.14 自动补偿默认开启 · 补偿旁路可记录 · 原值追溯"',
         '"v5.3.15 自动补偿默认开启 · 补偿旁路可记录 · 原值追溯"'),
    )
    for before, after in replacements:
        assert text.count(before) == 1, before
        text = text.replace(before, after, 1)
    return text.encode("utf-8")


def isolated_optional_dependencies():
    """Copy only missing packages from the same Python 3.12 base installation.

    Do not change the build venv or prepend an unrelated full site-packages.
    Pillow is retained for image exports; openpyxl retains Excel reports.
    """
    destination = WORK / "isolateddeps"
    destination.mkdir(parents=True, exist_ok=True)
    source = Path(sys.base_prefix) / "Lib/site-packages"
    records = {}
    for module, metadata in (("openpyxl", "openpyxl"), ("et_xmlfile", "et_xmlfile"), ("PIL", "pillow")):
        if importlib.util.find_spec(module) is not None:
            records[module] = "available in selected build venv"
            continue
        package = source / module
        assert (package / "__init__.py").is_file(), f"Missing offline dependency: {package}"
        shutil.copytree(package, destination / module, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for extra in source.glob(metadata + "*.dist-info"):
            shutil.copytree(extra, destination / extra.name, dirs_exist_ok=True)
        for extra in source.glob(metadata + "*.libs"):
            shutil.copytree(extra, destination / extra.name, dirs_exist_ok=True)
        records[module] = str(package)
    return destination, records


def source_files(directory):
    return {path.relative_to(directory).as_posix(): path for path in directory.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}


def main():
    resume = sys.argv[1:] == ["--resume"]
    assert not OUT.exists() or resume, f"Fresh release directory required: {OUT}"
    assert not WORK.exists() or resume, f"Fresh build directory required: {WORK}"
    if resume:
        assert not (OUT / (NAME + ".exe")).exists() and not (OUT / "manifest.json").exists()
    assert not PACKAGE.exists(), f"Never overwrite a release ZIP: {PACKAGE}"
    baseline_manifest = json.loads((BASE / "build_manifest.json").read_text(encoding="utf-8-sig"))
    assert sha(BASE / baseline_manifest["exe"]) == baseline_manifest["exe_sha256"]
    with zipfile.ZipFile(BASE / "source.zip") as archive:
        baseline = {item.filename: archive.read(item) for item in archive.infolist() if not item.is_dir()}
    backend = checked_backend(baseline["ch347_spi.py"])
    main_source = version_labels(baseline["main.py"])
    tests = (CURRENT / "test_ch347_spi.py").read_bytes()
    # The reviewed regression tests model physical CS levels independently.
    assert b"transfer_cs_levels" in tests and b"self.cs_levels = [1, 1]" in tests
    assert digest(tests) != digest(baseline["test_ch347_spi.py"])
    assert digest(baseline["CH347DLLA64.dll"]) == EXPECTED_DLL
    OUT.mkdir(parents=True, exist_ok=resume)
    WORK.mkdir(parents=True, exist_ok=resume)
    frozen = OUT / "source"
    frozen.mkdir(exist_ok=resume)
    for name, data in baseline.items():
        target = (frozen / name).resolve()
        assert target.is_relative_to(frozen.resolve()), name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (frozen / "ch347_spi.py").write_bytes(backend)
    (frozen / "main.py").write_bytes(main_source)
    (frozen / "test_ch347_spi.py").write_bytes(tests)
    # v5.3.14's archive omitted the static descriptor fixture already referenced
    # by its tests. Restore only this existing offline test datum.
    assert TEST_FIXTURE not in baseline
    (frozen / TEST_FIXTURE).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CURRENT / TEST_FIXTURE, frozen / TEST_FIXTURE)
    for name in PROFILES:
        assert sha(BASE / name) == digest(baseline[name]), name
        shutil.copy2(BASE / name, OUT / name)
    comparison = {}
    for name, data in baseline.items():
        actual = sha(frozen / name)
        comparison[name] = {"baseline_sha256": digest(data), "release_sha256": actual,
                            "changed": actual != digest(data)}
        assert name in ALLOWED or actual == digest(data), f"Unexpected source change: {name}"
    assert {name for name, item in comparison.items() if item["changed"]} == ALLOWED
    (OUT / "baseline_comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    dependencies, copied = isolated_optional_dependencies()
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=os.pathsep.join((str(frozen), str(dependencies))),
               PYINSTALLER_CONFIG_DIR=str(WORK / "pyinstaller_cache"))
    print(f"FROZEN: {frozen}\nBaseline differences: {', '.join(sorted(ALLOWED))}", flush=True)
    dependency_check = subprocess.run(
        [sys.executable, "-B", "-c", "import json,sys,PyInstaller,openpyxl,PIL,et_xmlfile; "
         "print(json.dumps(dict(python=sys.version,pyinstaller=PyInstaller.__version__,"
         "openpyxl=openpyxl.__version__,pillow=PIL.__version__)))"],
        env=env, cwd=frozen, check=True, capture_output=True, text=True)
    print(dependency_check.stdout.strip(), flush=True)
    with (OUT / "driver_protocol_tests.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", "test_ch347_spi", "test_spi_iap_protocol", "-v"],
                       env=env, cwd=frozen, check=True, stdout=log, stderr=subprocess.STDOUT)
    print("Frozen CH347 and SPI protocol fake tests passed; building EXE", flush=True)
    command = [sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--name", NAME, "--distpath", str(OUT), "--workpath", str(WORK / "work"),
               "--specpath", str(WORK), "--paths", str(frozen), "--paths", str(dependencies),
               "--exclude-module", "pandas", "--exclude-module", "scipy", "--collect-data", "openpyxl",
               "--collect-submodules", "openpyxl", "--runtime-hook", str(frozen / "qt_platform_runtime_hook.py"),
               "--add-binary", f"{frozen / 'CH347DLLA64.dll'};.",
               "--add-data", f"{frozen / 'assets'};assets"]
    for name in (*PROFILES, "TacAI_H3_App_SPI_v2.tacai", "TacAI_H3_App_v1_3_VTORFIX.tacai"):
        command += ["--add-data", f"{frozen / name};."]
    command.append(str(frozen / "main.py"))
    with (OUT / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, env=env, cwd=frozen, check=True, stdout=log, stderr=subprocess.STDOUT)
    for name, data in baseline.items():
        assert name in ALLOWED or sha(frozen / name) == digest(data), name
    assert sha(frozen / "ch347_spi.py") == digest(backend)
    for name in PROFILES:
        assert sha(OUT / name) == digest(baseline[name])
    with zipfile.ZipFile(OUT / "source.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(source_files(frozen).items()):
            archive.write(path, name)
    (OUT / "source_sha256.json").write_text(json.dumps(
        {name: sha(path) for name, path in sorted(source_files(frozen).items())},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "先看这里.txt").write_text(
        "TacAI H3 v5.3.15 SPI 片选修复版 — 2026-10-19\n\n"
        "完整解压本文件夹，运行 TacAI H3 v5.3.15 SPI CS Fix.exe。\n"
        "保持当前 v5 固件和已确认的接线，不用刷机；关闭 Demo 和其他 CH347 测试工具。\n"
        "连接方式选择 CH347F SPI → 检测设备 → 选择检测到的设备0（SPI IF4）→\n"
        "SPI时钟选468750 Hz → 连接。其余参数固定为Mode0 / MSB / 8bit / CS0。\n"
        "连接后确认数据持续更新，再按原流程置零、加载与记录。\n\n"
        "本版将发送期间CS拉低、事务后CS释放为高；保留v5.3.14的全部采集、\n"
        "补偿、标定及记录逻辑和五份参数。兼容当前v5的TAI3快照。\n"
        "已通过冻结源码模拟CH347及SPI协议回归；连续GUI采集仍待现场测试。\n",
        encoding="utf-8-sig")
    shutil.copy2(Path(__file__), OUT / Path(__file__).name)
    executable = OUT / (NAME + ".exe")
    files = {path.name: {"size": path.stat().st_size, "sha256": sha(path)}
             for path in sorted(OUT.iterdir()) if path.is_file()}
    manifest = {
        "version": "5.3.15-spi-cs-fix", "date": "2026-10-19", "exe": executable.name,
        "exe_sha256": sha(executable), "exe_bytes": executable.stat().st_size,
        "source_baseline": BASE.name, "baseline_source_zip_sha256": sha(BASE / "source.zip"),
        "allowed_changed_sources": sorted(ALLOWED),
        "production_changes": {"ch347_spi.py": "assert physical CS=0; deassert physical CS=1",
                               "main.py": "three version strings only: title, display and CSV metadata"},
        "test_change": "independent physical CS fake state and corrected level assertions",
        "added_test_fixture": {"path": TEST_FIXTURE, "sha256": sha(frozen / TEST_FIXTURE),
                               "reason": "restore existing static descriptor fixture omitted from v5.3.14 source.zip"},
        "all_other_baseline_source_files_byte_identical": True,
        "calibration_profiles_unchanged": True, "force_calculations_unchanged": True,
        "default_correction_index": 6, "default_runtime_profile": "auto",
        "spi_defaults": {"clock_hz": 468750, "mode": 0, "msb_first": True, "data_bits": 8, "cs": 0},
        "snapshot": {"protocol": "TAI3", "response_transaction_bytes": 536,
                     "payload_bytes": 164, "version": 1, "firmware_id_whitelist": False},
        "source_fake_tests": "test_ch347_spi + test_spi_iap_protocol passed",
        "gui_executable_not_run_by_builder": True, "hardware_validation": "pending",
        "hardware_connected": False, "offline_dependency_sources": copied,
        "build_environment": json.loads(dependency_check.stdout), "files": files,
    }
    for name in ("manifest.json", "build_manifest.json"):
        (OUT / name).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(PACKAGE, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUT.iterdir()):
            if path.is_file():
                archive.write(path, OUT.name + "/" + path.name)
    with zipfile.ZipFile(PACKAGE) as archive:
        assert archive.testzip() is None
        for path in OUT.iterdir():
            if path.is_file():
                assert digest(archive.read(OUT.name + "/" + path.name)) == sha(path), path.name
    print(json.dumps({"release": str(OUT), "zip": str(PACKAGE), "zip_sha256": sha(PACKAGE),
                      "exe_sha256": sha(executable)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
