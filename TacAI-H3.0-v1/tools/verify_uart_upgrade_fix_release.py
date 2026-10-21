"""Inspect the frozen UART upgrade fix; never launch the EXE or load a DLL."""
from __future__ import annotations

import ast
import json
import marshal
import zipfile

from PyInstaller.archive.readers import CArchiveReader
from build_uart_upgrade_fix_release import ROOT, BASE, OUT, PACKAGE, CHANGED, ADDED, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, sha
from verify_spi_gui_cs_fix_release import codes, stored_constant


def main():
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "5.3.17-uart-upgrade-fix"
    for name, metadata in manifest["files"].items():
        data = (OUT / name).read_bytes()
        assert len(data) == metadata["bytes"] and sha(data) == metadata["sha256"], name
    with zipfile.ZipFile(BASE / "source.zip") as archive:
        before = {n: archive.read(n) for n in archive.namelist()}
    with zipfile.ZipFile(OUT / "source.zip") as archive:
        assert archive.testzip() is None
        after = {n: archive.read(n) for n in archive.namelist()}
    assert {n for n in before if before[n] != after[n]} == CHANGED
    assert set(after) - set(before) == ADDED
    for name in PROFILES:
        assert before[name] == after[name] == (OUT / name).read_bytes()
    def monitor_methods(data):
        module = ast.parse(data.decode("utf-8-sig"))
        monitor, = (n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "Monitor")
        return {n.name: ast.dump(n) for n in monitor.body if isinstance(n, ast.FunctionDef)}
    a, b = monitor_methods(before["main.py"]), monitor_methods(after["main.py"])
    changed_methods = {n for n in a if a[n] != b[n]}
    assert changed_methods == {"__init__", "_build_ui_v524", "_open_upgrade"}, changed_methods
    updater_tree = ast.parse(after["firmware_updater.py"].decode("utf-8-sig"))
    uart, = (n for n in ast.walk(updater_tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_uart")
    # No exception handler around upgrade()/boot() may return to a retry loop.
    for block in (n for n in ast.walk(uart) if isinstance(n, ast.Try)):
        body_calls = {n.func.attr for stmt in block.body for n in ast.walk(stmt)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        if body_calls & {"upgrade", "boot"}:
            assert not any(isinstance(n, ast.Continue) for handler in block.handlers for n in ast.walk(handler))
            assert all(any(isinstance(n, ast.Raise) for n in ast.walk(handler)) for handler in block.handlers)
    exe = OUT / manifest["exe"]
    assert sha(exe.read_bytes()) == manifest["exe_sha256"]
    archive = CArchiveReader(str(exe))
    pyz_name, = (n for n, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    entry = marshal.loads(archive.extract("main"))
    assert stored_constant(entry, "MONITOR_VERSION") == "5.3.17-uart-upgrade-fix"
    updater = pyz.extract("firmware_updater")
    assert any(c.co_name == "_run_uart" for c in codes(updater))
    assert any(c.co_name == "_start_log" for c in codes(updater))
    protocol = pyz.extract("iap_protocol")
    command, = (c for c in codes(protocol) if c.co_name == "command")
    assert "_write_once" in command.co_names
    assert any(c.co_name == "_extract_frame" for c in codes(protocol))
    for name, expected in (("CH341DLLA64.DLL", CH341_SHA), ("CH347DLLA64.dll", CH347_SHA),
                           ("TacAI_H3_App_SPI_rxirq_v5.tacai", APP_SHA)):
        actual, = (n for n in archive.toc if n.casefold() == name.casefold())
        assert sha(archive.extract(actual)) == expected, name
    assert not any("v1_3" in name for name in archive.toc), "Old default application must not be bundled"
    with zipfile.ZipFile(PACKAGE) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == {OUT.name + "/" + p.name for p in OUT.iterdir() if p.is_file()}
        for p in OUT.iterdir():
            if p.is_file():
                assert package.read(OUT.name + "/" + p.name) == p.read_bytes()
    test_log = (OUT / "offline_tests.log").read_text(encoding="utf-8")
    assert "\nOK\n" in test_log and "FAILED" not in test_log
    report = dict(result="passed", version=manifest["version"], changed_sources=sorted(CHANGED),
                  added_sources=sorted(ADDED), monitor_changed_methods=sorted(changed_methods),
                  calibration_profiles_byte_identical=True, all_other_sources_byte_identical=True,
                  uart_flash_errors_cannot_continue_retry_loop=True, executable_modules_verified=True,
                  old_default_image_removed_from_exe=True, default_image_sha256=APP_SHA,
                  tests=test_log.strip().splitlines()[-3:], package_sha256=sha(PACKAGE.read_bytes()),
                  sensor_firmware_changed=False, hardware_accessed=False, hardware_validation="pending")
    target = ROOT / "outputs/uart_upgrade_fix_20261019/release_verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
