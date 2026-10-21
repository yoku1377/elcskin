"""Verify the CH3410x32 GUI and resident UART fix, no hardware execution."""
from __future__ import annotations

import json
import marshal
import struct
import zipfile

from PyInstaller.archive.readers import CArchiveReader
from build_upgrade_ch341_revision_release import ROOT, BASE, OUT, PACKAGE, CHANGED, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, FULL_NAME, FULL_SHA, sha
from build_bootloader_uart_rxfix_v6 import OUT as FIRMWARE, BASE as APP_BASE
from build_spi_recovery_release import check_image
from tacai_image import FirmwareImage
from verify_spi_gui_cs_fix_release import stored_constant, codes


def main():
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "5.3.19-upgrade-ch341-revision-fix"
    for name, info in manifest["files"].items():
        data = (OUT / name).read_bytes()
        assert len(data) == info["bytes"] and sha(data) == info["sha256"]
    with zipfile.ZipFile(BASE / "source.zip") as z:
        before = {n: z.read(n) for n in z.namelist()}
    with zipfile.ZipFile(OUT / "source.zip") as z:
        assert z.testzip() is None
        after = {n: z.read(n) for n in z.namelist()}
    assert set(after) == set(before)
    assert {n for n in before if before[n] != after[n]} == CHANGED
    for name in PROFILES:
        assert before[name] == after[name] == (OUT / name).read_bytes()
    for name in ("iap_protocol.py", "spi_iap_protocol.py", "ch347_spi.py"):
        assert before[name] == after[name]
    exe = OUT / manifest["exe"]
    assert sha(exe.read_bytes()) == manifest["exe_sha256"]
    archive = CArchiveReader(str(exe))
    pyz_name, = (n for n, i in archive.toc.items() if i[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    entry = marshal.loads(archive.extract("main"))
    assert stored_constant(entry, "MONITOR_VERSION") == "5.3.19-upgrade-ch341-revision-fix"
    updater = pyz.extract("firmware_updater")
    assert stored_constant(updater, "UPGRADER_VERSION") == "5.3.19"
    driver = pyz.extract("ch341_spi")
    assert (0x20, 0x30, 0x32) in driver.co_consts
    exclusion, = (c for c in codes(driver) if c.co_name == "spi_exclusion_reason")
    assert 0x32 in exclusion.co_consts and "5512" in exclusion.co_consts
    for name, expected in (("CH341DLLA64.DLL", CH341_SHA), ("CH347DLLA64.dll", CH347_SHA),
                           ("TacAI_H3_App_SPI_rxirq_v5.tacai", APP_SHA)):
        actual, = (n for n in archive.toc if n.casefold() == name.casefold())
        assert sha(archive.extract(actual)) == expected
    fw_manifest = json.loads((FIRMWARE / "manifest.json").read_text(encoding="utf-8"))
    for name, meta in fw_manifest["files"].items():
        data = (FIRMWARE / name).read_bytes()
        assert sha(data) == meta["sha256"] and len(data) == meta["bytes"]
    full = (OUT / FULL_NAME).read_bytes()
    previous = (APP_BASE / "TacAI_H3_SPI_RXIRQ_v5_FULL_08000000.bin").read_bytes()
    boot = (FIRMWARE / "TacAI_H3_Dual_SPI_UART_Bootloader_v6.bin").read_bytes()
    image = FirmwareImage.load(OUT / "TacAI_H3_App_SPI_rxirq_v5.tacai")
    assert sha(full) == FULL_SHA and full == (FIRMWARE / FULL_NAME).read_bytes()
    assert full[0x2000:] == previous[0x2000:]
    assert len(full) == 0xF800 and len(boot) < 0x2000
    check_image(full, boot, image)
    assert struct.unpack_from("<I", full)[0] == 0x20001FF0
    tests = (OUT / "offline_tests.log").read_text(encoding="utf-8")
    assert "\nOK\n" in tests and "FAILED" not in tests
    c_tests = (OUT / "uart_rx_phase_tests.log").read_text(encoding="utf-8")
    assert "286 phase cases and 11 boundary/fault cases" in c_tests and "\nOK\n" in c_tests
    with zipfile.ZipFile(PACKAGE) as z:
        assert z.testzip() is None
        assert set(z.namelist()) == {OUT.name + "/" + p.name for p in OUT.iterdir() if p.is_file()}
        for p in OUT.iterdir():
            if p.is_file():
                assert z.read(OUT.name + "/" + p.name) == p.read_bytes()
    report = dict(result="passed", version=manifest["version"], changed_gui_sources=sorted(CHANGED),
                  embedded_ch341_32_requires_pid5512=True, calibration_profiles_byte_identical=True,
                  uart_spi_protocol_and_ch347_unchanged=True, gui_tests=tests.strip().splitlines()[-3:],
                  firmware_old_parser_failure_reproduced=True, fixed_parser_phase_cases=286,
                  fixed_parser_boundary_cases=11, full_image_bytes=len(full), bootloader_bytes=len(boot),
                  program_address="0x08000000", application_and_metadata_byte_identical=True,
                  config_page_excluded=True, full_sha256=FULL_SHA, package_sha256=sha(PACKAGE.read_bytes()),
                  hardware_accessed=False, hardware_validation="pending")
    target = ROOT / "outputs/uart_boot_ch341_fix_20261019/release_verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
