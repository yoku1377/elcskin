"""Verify frozen resources and record the bounded, hardware-free validation."""
from pathlib import Path
import hashlib
import json
import zipfile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release/TacAI_H3_v5_3_4_SPI_Diagnostics_20261015"
SOURCE = ROOT / "pc/TfgMonitorPython_H3_v5.0"


def digest(data):
    return hashlib.sha256(data).hexdigest()


manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
baseline = json.loads((ROOT / "release/TacAI_H3_v5_3_3_FrontLocalCandidate_20261015/source_sha256.json").read_text(encoding="utf-8"))
changed = []
for name, expected in baseline.items():
    if (OUTPUT / name).is_file():
        actual = digest((OUTPUT / name).read_bytes())
    elif (SOURCE / name).is_file():
        actual = digest((SOURCE / name).read_bytes())
    else:
        raise AssertionError(f"Baseline file missing: {name}")
    if actual != expected:
        changed.append(name)
assert set(changed) == {"main.py", "ch347_spi.py", "test_ch347_spi.py", "test_spi_monitor_ui.py"}, changed
for name, expected in manifest["source_hashes"].items():
    assert digest((SOURCE / name).read_bytes()) == expected, name
exe = OUTPUT / "TacAI H3 v5.3.4 SPI Diagnostics Front Local Candidate.exe"
archive = CArchiveReader(str(exe))
for name in ("CH347DLLA64.dll", "local_force_profile.json", "TacAI_H3_App_SPI_v2.tacai", "TacAI_H3_App_v1_3_VTORFIX.tacai"):
    expected = (SOURCE if name == "local_force_profile.json" else OUTPUT) / name
    assert archive.extract(name) == expected.read_bytes(), name
pyz = archive.open_embedded_archive("PYZ.pyz")
for name in ("ch347_spi", "spi_iap_protocol", "firmware_updater", "local_high_force", "regional_ard_force", "regional_high_force"):
    assert name in pyz.toc, name
manifest.update({
    "software_tests": 233, "self_test_exit": 0,
    "embedded_profile_dll_and_firmware_verified": True,
    "baseline_files_changed": changed,
    "calibration_and_other_baseline_files_unchanged": True,
    "usb_driver_installed_by_agent": False, "hardware_flashed": False,
    "gui_check": "1500px and 1100px controls: retained USB failure and empty enumeration; simulated data, no USB calls",
})
(OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
zip_path = ROOT / "release/TacAI_H3_v5.3.4_SPI诊断_含驱动_20261015.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
    for path in OUTPUT.iterdir():
        if path.is_file() and path.name != "build.log":
            bundle.write(path, OUTPUT.name + "/" + path.name)
    for path in (OUTPUT / "验证记录").glob("*.png"):
        bundle.write(path, OUTPUT.name + "/验证记录/" + path.name)
print(json.dumps({"package": str(zip_path), "size": zip_path.stat().st_size,
                  "sha256": digest(zip_path.read_bytes()), "baseline_changes": changed}, ensure_ascii=False))
