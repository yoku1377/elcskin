"""Inspect snapshot recovery delivery without launching EXE or loading USB DLLs."""
from __future__ import annotations

import ast
import json
import marshal
import zipfile

from PyInstaller.archive.readers import CArchiveReader
from build_spi_snapshot_recovery_release import ROOT, BASE, OUT, PACKAGE, CHANGED, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, sha
from verify_spi_gui_cs_fix_release import codes, stored_constant


def function(data, name):
    module = ast.parse(data.decode("utf-8-sig"))
    found, = (n for n in ast.walk(module) if isinstance(n, ast.FunctionDef) and n.name == name)
    return found


def main():
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "5.3.20-spi-snapshot-recovery"
    for name, metadata in manifest["files"].items():
        data = (OUT / name).read_bytes()
        assert len(data) == metadata["bytes"] and sha(data) == metadata["sha256"], name
    with zipfile.ZipFile(BASE / "source.zip") as z:
        before = {n: z.read(n) for n in z.namelist()}
    with zipfile.ZipFile(OUT / "source.zip") as z:
        assert z.testzip() is None
        after = {n: z.read(n) for n in z.namelist()}
    assert set(after) == set(before)
    assert {n for n in before if before[n] != after[n]} == CHANGED
    for name in PROFILES:
        assert before[name] == after[name] == (OUT / name).read_bytes()
    for name in ("ch341_spi.py", "ch347_spi.py", "iap_protocol.py"):
        assert before[name] == after[name]
    assert ast.dump(function(before["baseline_quality.py"], "evaluate_window")) == ast.dump(
        function(after["baseline_quality.py"], "evaluate_window"))
    assert ast.dump(function(before["spi_iap_protocol.py"], "_decode_response")) == ast.dump(
        function(after["spi_iap_protocol.py"], "_decode_response"))
    def monitor_methods(data):
        tree = ast.parse(data.decode("utf-8-sig"))
        monitor, = (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Monitor")
        return {n.name: ast.dump(n) for n in monitor.body if isinstance(n, ast.FunctionDef)}
    a, b = monitor_methods(before["main.py"]), monitor_methods(after["main.py"])
    assert {n for n in a if a[n] != b[n]} == {"__init__", "_build_ui_v524"}
    request_method = function(after["spi_iap_protocol.py"], "_command")
    sends = [n for n in ast.walk(request_method) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "_exchange"
             and len(n.args) == 1 and isinstance(n.args[0], ast.Name) and n.args[0].id == "request"]
    assert len(sends) == 1
    assert not any(sends[0] is n for loop in ast.walk(request_method) if isinstance(loop, ast.While)
                   for n in ast.walk(loop))
    public = function(after["spi_iap_protocol.py"], "command")
    assert not any(isinstance(n, ast.keyword) and n.arg == "snapshot_stale_recovery" for n in ast.walk(public))
    archive = CArchiveReader(str(OUT / manifest["exe"]))
    pyz_name, = (n for n, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    entry = marshal.loads(archive.extract("main"))
    assert stored_constant(entry, "MONITOR_VERSION") == "5.3.20-spi-snapshot-recovery"
    assert any(c.co_name == "_snapshot_trace" for c in codes(entry))
    assert any(c.co_name == "_record_spi_event" for c in codes(entry))
    spi = pyz.extract("spi_iap_protocol")
    assert stored_constant(spi, "SNAPSHOT_MAX_ATTEMPTS") == 3
    assert stored_constant(spi, "SNAPSHOT_STALE_MIN_POLLS") == 3
    assert stored_constant(spi, "SNAPSHOT_STALE_MIN_SECONDS") == 0.18
    updater = pyz.extract("firmware_updater")
    assert stored_constant(updater, "UPGRADER_VERSION") == "5.3.20"
    for file, expected in (("CH341DLLA64.DLL", CH341_SHA), ("CH347DLLA64.dll", CH347_SHA),
                           ("TacAI_H3_App_SPI_rxirq_v5.tacai", APP_SHA)):
        name, = (n for n in archive.toc if n.casefold() == file.casefold())
        assert sha(archive.extract(name)) == expected
    log = (OUT / "offline_tests.log").read_text(encoding="utf-8")
    assert "\nOK\n" in log and "FAILED" not in log
    c_log = (OUT / "firmware_state_repro.log").read_text(encoding="utf-8")
    assert "PASS: 3 production RXIRQ stale snapshot/reissue recovery cases" in c_log and "\nOK\n" in c_log
    with zipfile.ZipFile(PACKAGE) as z:
        assert z.testzip() is None
        assert set(z.namelist()) == {OUT.name + "/" + p.name for p in OUT.iterdir() if p.is_file()}
        for path in OUT.iterdir():
            if path.is_file():
                assert z.read(OUT.name + "/" + path.name) == path.read_bytes()
    report = dict(result="passed", version=manifest["version"], changed_sources=sorted(CHANGED),
                  all_other_sources_byte_identical=True, force_and_quality_math_unchanged=True,
                  calibration_profiles_byte_identical=True, drivers_and_dlls_unchanged=True,
                  firmware_unchanged=True, strict_response_decoder_unchanged=True,
                  upgrade_commands_still_single_send=True, snapshot_recovery_bounded=True,
                  runtime_gap_reporting_present=True, firmware_state_injection_cases=3,
                  tests=log.strip().splitlines()[-3:], package_sha256=sha(PACKAGE.read_bytes()),
                  hardware_accessed=False, hardware_soak_validation="pending")
    target = ROOT / "outputs/spi_snapshot_recovery_20261019/release_verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
