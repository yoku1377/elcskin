"""Verify delivered CH341A EXE contents without loading DLLs or opening USB."""
from __future__ import annotations

import ast
import json
import marshal
from pathlib import Path
import zipfile

from PyInstaller.archive.readers import CArchiveReader
from build_ch341_gui_release import BASE, OUT, PACKAGE, CHANGED, ADDED, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, ROOT, sha
from verify_spi_gui_cs_fix_release import stored_constant, codes


def main():
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "5.3.16-ch341a-support"
    for name, meta in manifest["files"].items():
        data = (OUT / name).read_bytes()
        assert len(data) == meta["bytes"] and sha(data) == meta["sha256"], name
    with zipfile.ZipFile(BASE / "source.zip") as archive:
        baseline = {name: archive.read(name) for name in archive.namelist()}
    with zipfile.ZipFile(OUT / "source.zip") as archive:
        assert archive.testzip() is None
        after = {name: archive.read(name) for name in archive.namelist()}
    assert {n for n in baseline if baseline[n] != after[n]} == CHANGED
    assert set(after) - set(baseline) == ADDED
    for name in PROFILES:
        assert (OUT / name).read_bytes() == baseline[name] == after[name]
    # Model/recording methods outside transport and version labels stay identical.
    def methods(data):
        module = ast.parse(data.decode("utf-8-sig"))
        monitor, = (n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "Monitor")
        return {n.name: ast.dump(n) for n in monitor.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    before_methods, after_methods = methods(baseline["main.py"]), methods(after["main.py"])
    allowed_methods = {"__init__", "_build", "_transport_changed", "_refresh_ports", "_detect_devices",
                       "_on_devices_detected", "_on_device_probe_finished", "_open_upgrade",
                       "_toggle_connection", "_on_reader_finished"}
    changed_methods = {name for name in before_methods if before_methods[name] != after_methods[name]}
    # The UI construction method has varied names in older delivered baselines.
    assert changed_methods <= allowed_methods | {"_build_ui", "_build_ui_v524", "_ui"}, changed_methods
    exe = OUT / manifest["exe"]
    assert sha(exe.read_bytes()) == manifest["exe_sha256"]
    archive = CArchiveReader(str(exe))
    pyz_name, = (name for name, meta in archive.toc.items() if meta[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    backend = pyz.extract("ch341_spi")
    assert stored_constant(backend, "STREAM_MODE") == 0x81
    assert stored_constant(backend, "AUTO_CS0") == 0x80
    assert stored_constant(backend, "MAX_EXCHANGE_BYTES") == 4096
    driver347 = pyz.extract("ch347_spi")
    assert stored_constant(driver347, "CS_ASSERT_LEVEL") == 0
    assert stored_constant(driver347, "CS_DEASSERT_LEVEL") == 1
    entry = marshal.loads(archive.extract("main"))
    assert stored_constant(entry, "MONITOR_VERSION") == "5.3.16-ch341a-support"
    assert any(c.co_name == "CH341ForceReader" for c in codes(entry))
    upgrade = pyz.extract("firmware_updater")
    run_spi, = (c for c in codes(upgrade) if c.co_name == "_run_spi")
    assert "CH341Spi" in run_spi.co_names and "CH347Spi" in run_spi.co_names
    for file, expected in (("CH341DLLA64.DLL", CH341_SHA), ("CH347DLLA64.dll", CH347_SHA),
                           ("TacAI_H3_App_SPI_rxirq_v5.tacai", APP_SHA)):
        name, = (n for n in archive.toc if n.casefold() == file.casefold())
        assert sha(archive.extract(name)) == expected, file
    with zipfile.ZipFile(PACKAGE) as package:
        assert package.testzip() is None
        expected_names = {OUT.name + "/" + p.name for p in OUT.iterdir() if p.is_file()}
        assert set(package.namelist()) == expected_names
        for p in OUT.iterdir():
            if p.is_file():
                assert package.read(OUT.name + "/" + p.name) == p.read_bytes()
    tests = (OUT / "offline_tests.log").read_text(encoding="utf-8")
    assert "\nOK\n" in tests and "FAILED" not in tests
    report = dict(result="passed", changed_source_files=sorted(CHANGED), added_source_files=sorted(ADDED),
                  changed_monitor_methods=sorted(changed_methods), calibration_profiles_byte_identical=True,
                  all_other_sources_byte_identical=True, executable_backends=["CH347", "CH341A"],
                  embedded_ch341_msb_mode=0x81, embedded_ch341_cs0=0x80,
                  embedded_ch347_cs_assert=0, embedded_ch347_cs_deassert=1,
                  spi_default_image="h3_spi_20261016_rxirq_v5", sensor_firmware_changed=False,
                  offline_tests=tests.strip().splitlines()[-3:], hardware_accessed=False,
                  hardware_validation="pending", package_sha256=sha(PACKAGE.read_bytes()))
    target = ROOT / "outputs/ch341_support_20261019/release_verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
