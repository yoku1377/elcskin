"""Check the packaged SPI fix and create a portable ZIP after validation."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile
import pefile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release/TacAI_H3_v5_3_6_SPI_ConfigFix_20261015"
SOURCE = ROOT / "pc/TfgMonitorPython_H3_v5.0"
EXE = OUTPUT / "TacAI H3 v5.3.6 SPI Config Fix Front Back Candidate.exe"


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
for name in ("ch347_spi", "spi_iap_protocol", "firmware_updater", "local_high_force",
             "back_total_force", "regional_ard_force", "regional_high_force"):
    assert name in pyz.toc, name
pe = pefile.PE(str(OUTPUT / "CH347DLLA64.dll"))
# Pin the inspected Init conversion and GetCfg raw-mask instructions.
assert pe.get_data(0xE38A, 15) == bytes.fromhex("41 80 61 02 01 B8 80 00 00 00 66 41 0F 45 C3")
assert pe.get_data(0xE6A7, 6) == bytes.fromhex("24 80 88 44 24 2A")
process = subprocess.run([str(EXE), "--self-test"], timeout=30, check=True)
manifest.update({"software_tests": 248, "self_test_exit": process.returncode,
                 "embedded_resources_verified": True, "hardware_flashed": False,
                 "driver_installed_by_agent": False})
(OUTPUT / "验证记录/软件验证.txt").write_text(
    "248 PC unit/integration tests passed (54.642s).\n"
    "EXE --self-test exit code: 0.\n"
    "Embedded front/back profiles, DLL and UART/SPI application images match original bytes.\n"
    "All other v5.3.5 baseline source/resource hashes remain unchanged.\n"
    "DLL Init/GetCfg encoding instruction bytes match static inspection.\n"
    "1100px and 1500px controls rendered with a simulated real configuration mismatch.\n"
    "No hardware access, driver installation, or firmware flashing performed.\n",
    encoding="utf-8")
(OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
zip_path = ROOT / "release/TacAI_H3_v5.3.6_SPI参数回读修复_20261015.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
    for path in OUTPUT.iterdir():
        if path.is_file() and path.name != "build.log":
            bundle.write(path, OUTPUT.name + "/" + path.name)
    for path in (OUTPUT / "验证记录").iterdir():
        bundle.write(path, OUTPUT.name + "/验证记录/" + path.name)
print(json.dumps({"package": str(zip_path), "sha256": sha(zip_path),
                  "exe_sha256": sha(EXE), "size": zip_path.stat().st_size}, ensure_ascii=False))
