"""Verify v5.3.18 package and embedded protocol without opening hardware."""
from __future__ import annotations

import ast
import json
import marshal
import zipfile

from PyInstaller.archive.readers import CArchiveReader
from build_spi_upgrade_fix_release import ROOT, BASE, OUT, PACKAGE, CHANGED, PROFILES, CH341_SHA, CH347_SHA, APP_SHA, sha
from verify_spi_gui_cs_fix_release import codes, stored_constant


def main():
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "5.3.18-spi-upgrade-response-fix"
    for name, info in manifest["files"].items():
        data = (OUT / name).read_bytes()
        assert len(data) == info["bytes"] and sha(data) == info["sha256"], name
    with zipfile.ZipFile(BASE / "source.zip") as archive:
        before = {n: archive.read(n) for n in archive.namelist()}
    with zipfile.ZipFile(OUT / "source.zip") as archive:
        assert archive.testzip() is None
        after = {n: archive.read(n) for n in archive.namelist()}
    assert set(after) == set(before)
    assert {n for n in before if before[n] != after[n]} == CHANGED
    for name in PROFILES:
        assert before[name] == after[name] == (OUT / name).read_bytes()
    def function(data, name):
        tree = ast.parse(data.decode("utf-8-sig"))
        found, = (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        return found
    assert ast.dump(function(before["spi_iap_protocol.py"], "_decode_response")) == ast.dump(
        function(after["spi_iap_protocol.py"], "_decode_response")), "Do not loosen frame/CRC validation"
    assert before["iap_protocol.py"] == after["iap_protocol.py"], "UART fix remains unchanged"
    command_tree = function(after["spi_iap_protocol.py"], "command")
    requests = [n for n in ast.walk(command_tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "_exchange"
                and len(n.args) == 1 and isinstance(n.args[0], ast.Name) and n.args[0].id == "request"]
    assert len(requests) == 1
    for loop in (n for n in ast.walk(command_tree) if isinstance(n, ast.While)):
        assert not any(requests[0] is n for n in ast.walk(loop))
    exe = OUT / manifest["exe"]
    assert sha(exe.read_bytes()) == manifest["exe_sha256"]
    archive = CArchiveReader(str(exe))
    pyz_name, = (n for n, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    entry = marshal.loads(archive.extract("main"))
    assert stored_constant(entry, "MONITOR_VERSION") == "5.3.18-spi-upgrade-response-fix"
    updater = pyz.extract("firmware_updater")
    assert stored_constant(updater, "UPGRADER_VERSION") == "5.3.18"
    assert any(c.co_name == "_spi_trace" for c in codes(updater))
    spi = pyz.extract("spi_iap_protocol")
    command, = (c for c in codes(spi) if c.co_name == "command")
    assert "corrupt_polls" in command.co_varnames and "_trace" in command.co_names
    for name, expected in (("CH341DLLA64.DLL", CH341_SHA), ("CH347DLLA64.dll", CH347_SHA),
                           ("TacAI_H3_App_SPI_rxirq_v5.tacai", APP_SHA)):
        actual, = (n for n in archive.toc if n.casefold() == name.casefold())
        assert sha(archive.extract(actual)) == expected
    with zipfile.ZipFile(PACKAGE) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == {OUT.name + "/" + p.name for p in OUT.iterdir() if p.is_file()}
        for p in OUT.iterdir():
            if p.is_file():
                assert package.read(OUT.name + "/" + p.name) == p.read_bytes()
    log = (OUT / "offline_tests.log").read_text(encoding="utf-8")
    assert "\nOK\n" in log and "FAILED" not in log
    report = dict(result="passed", changed_sources=sorted(CHANGED), all_other_sources_byte_identical=True,
                  calibration_profiles_byte_identical=True, strict_response_decoder_ast_identical=True,
                  spi_request_send_outside_poll_loop=True, executable_spi_recovery_and_trace_present=True,
                  upgrade_dialog_version="5.3.18", default_image_sha256=APP_SHA,
                  sensor_firmware_changed=False, hardware_accessed=False, hardware_validation="pending",
                  tests=log.strip().splitlines()[-3:], package_sha256=sha(PACKAGE.read_bytes()))
    target = ROOT / "outputs/spi_upgrade_fix_20261019/release_verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
