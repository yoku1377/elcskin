"""Verify v5.3.7 resources, real DLL metadata fixture and portable package."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile
import pefile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release/TacAI_H3_v5_3_7_CH347F_DetectionFix_20261015"
SOURCE = ROOT / "pc/TfgMonitorPython_H3_v5.0"
EXE = OUTPUT / "TacAI H3 v5.3.7 CH347F Detection Fix Front Back Candidate.exe"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
for name, expected in manifest["source_hashes"].items():
    assert sha(SOURCE / name) == expected, name
archive = CArchiveReader(str(EXE))
for name in ("local_force_profile.json", "back_force_profile.json", "CH347DLLA64.dll",
             "TacAI_H3_App_SPI_v2.tacai", "TacAI_H3_App_v1_3_VTORFIX.tacai"):
    assert archive.extract(name) == (OUTPUT / name).read_bytes(), name
pyz = archive.open_embedded_archive("PYZ.pyz")
for name in ("ch347_spi", "spi_iap_protocol", "firmware_updater", "back_total_force", "local_high_force"):
    assert name in pyz.toc, name
fixture = json.loads((SOURCE / "testdata/ch347_dll120_interfaces.json").read_text())
assert fixture["source_sha256"] == sha(OUTPUT / "CH347DLLA64.dll")
pe = pefile.PE(str(OUTPUT / "CH347DLLA64.dll"))
for index, entry in enumerate(fixture["records"]):
    raw = pe.get_data(int(fixture["table_rva"], 16) + index * fixture["record_size"], fixture["record_size"])
    assert raw[:32].split(b"\0")[0].decode() == entry["device_id_match"]
    for offset, name in enumerate(("usb_class", "function_type", "chip_type", "interface_number", "chip_mode"), 32):
        assert raw[offset] == entry[name]
    assert raw[37:].split(b"\0")[0].decode() == entry["function_description"]
result = subprocess.run([str(EXE), "--self-test"], timeout=30, check=True)
manifest.update({"software_tests": 253, "self_test_exit": result.returncode,
                 "embedded_resources_verified": True, "dll_all14_descriptor_fixture_verified": True,
                 "hardware_flashed": False, "driver_installed_by_agent": False})
(OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
zip_path = ROOT / "release/TacAI_H3_v5.3.7_CH347F接口检测修复_20261015.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
    for path in OUTPUT.iterdir():
        if path.is_file() and path.name != "build.log":
            bundle.write(path, OUTPUT.name + "/" + path.name)
    for path in (OUTPUT / "验证记录").iterdir():
        if path.is_file():
            bundle.write(path, OUTPUT.name + "/验证记录/" + path.name)
print(json.dumps({"package": str(zip_path), "size": zip_path.stat().st_size,
                  "sha256": sha(zip_path), "exe_sha256": sha(EXE)}, ensure_ascii=False))
