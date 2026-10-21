"""Inspect the delivered GUI archive without launching it or opening devices."""
from __future__ import annotations

import ast
import dis
import hashlib
import json
import marshal
from pathlib import Path
import types
import zipfile

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "release/TacAI_H3_v5_3_14_RecordingFix_20261016"
OUT = ROOT / "release/TacAI_H3_v5_3_15_SPI_CSFix_20261019"
PACKAGE = ROOT / "release/TacAI_H3_v5_3_15_SPI_CSFix_20261019.zip"
PROFILES = ("local_force_profile.json", "back_force_profile.json", "back25_consistency_profile.json",
            "back25_smooth_profile.json", "back25_domain_profile.json")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def codes(code):
    yield code
    for item in code.co_consts:
        if isinstance(item, types.CodeType):
            yield from codes(item)


def stored_constant(code, name):
    instructions = list(dis.get_instructions(code))
    result = []
    for i, instruction in enumerate(instructions):
        if instruction.opname in ("STORE_NAME", "STORE_GLOBAL") and instruction.argval == name:
            assert i and instructions[i - 1].opname == "LOAD_CONST", name
            result.append(instructions[i - 1].argval)
    assert len(result) == 1, (name, result)
    return result[0]


def main():
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "5.3.15-spi-cs-fix"
    for name, meta in manifest["files"].items():
        data = (OUT / name).read_bytes()
        assert len(data) == meta["size"] and sha(data) == meta["sha256"], name
    with zipfile.ZipFile(BASE / "source.zip") as before_zip, zipfile.ZipFile(OUT / "source.zip") as after_zip:
        assert after_zip.testzip() is None
        before = {name: before_zip.read(name) for name in before_zip.namelist()}
        after = {name: after_zip.read(name) for name in after_zip.namelist()}
    changed = {name for name, data in before.items() if data != after[name]}
    assert changed == {"ch347_spi.py", "main.py", "test_ch347_spi.py"}, changed
    assert set(after) - set(before) == {"testdata/ch347_dll120_interfaces.json"}
    for name in PROFILES:
        assert (OUT / name).read_bytes() == (BASE / name).read_bytes() == before[name] == after[name]
    # Independently reverse only the intended physical-CS correction in the AST.
    original_driver = ast.parse(before["ch347_spi.py"].decode("utf-8-sig"))
    repaired_driver = ast.parse(after["ch347_spi.py"].decode("utf-8-sig"))
    repaired_driver.body = [item for item in repaired_driver.body if not (
        isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and
        target.id in {"CS_ASSERT_LEVEL", "CS_DEASSERT_LEVEL"} for target in item.targets))]
    for node in ast.walk(repaired_driver):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "CH347SPI_ChangeCS":
            if isinstance(node.args[1], ast.Name):
                node.args[1] = ast.Constant({"CS_ASSERT_LEVEL": 1, "CS_DEASSERT_LEVEL": 0}[node.args[1].id])
    assert ast.dump(original_driver) == ast.dump(repaired_driver), "Other driver logic changed"
    expected_main = before["main.py"].decode("utf-8-sig")
    for old, new in (
        ('MONITOR_VERSION = "5.3.14-recording-availability-fix"', 'MONITOR_VERSION = "5.3.15-spi-cs-fix"'),
        ('self.setWindowTitle("TacAI H3 v5.3.14 Recording Fix")', 'self.setWindowTitle("TacAI H3 v5.3.15 SPI CS Fix")'),
        ('"v5.3.14 自动补偿默认开启 · 补偿旁路可记录 · 原值追溯"', '"v5.3.15 自动补偿默认开启 · 补偿旁路可记录 · 原值追溯"'),
    ):
        assert expected_main.count(old) == 1, old
        expected_main = expected_main.replace(old, new, 1)
    assert expected_main == after["main.py"].decode("utf-8-sig")
    executable = OUT / manifest["exe"]
    assert sha(executable.read_bytes()) == manifest["exe_sha256"]
    archive = CArchiveReader(str(executable))
    pyz_name, = (name for name, info in archive.toc.items() if info[-1] == "z")
    pyz = archive.open_embedded_archive(pyz_name)
    driver_code = pyz.extract("ch347_spi")
    assert stored_constant(driver_code, "CS_ASSERT_LEVEL") == 0
    assert stored_constant(driver_code, "CS_DEASSERT_LEVEL") == 1
    deassert, = (code for code in codes(driver_code) if code.co_name == "_deassert")
    exchange, = (code for code in codes(driver_code) if code.co_name == "exchange")
    assert "CS_DEASSERT_LEVEL" in deassert.co_names and "CS_ASSERT_LEVEL" in exchange.co_names
    entry_code = marshal.loads(archive.extract("main"))
    assert stored_constant(entry_code, "MONITOR_VERSION") == "5.3.15-spi-cs-fix"
    dll_name, = (name for name in archive.toc if name.lower() == "ch347dlla64.dll")
    assert sha(archive.extract(dll_name)) == "00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375"
    with zipfile.ZipFile(PACKAGE) as package:
        assert package.testzip() is None
        expected_names = {OUT.name + "/" + p.name for p in OUT.iterdir() if p.is_file()}
        assert set(package.namelist()) == expected_names
        for path in OUT.iterdir():
            if path.is_file():
                assert package.read(OUT.name + "/" + path.name) == path.read_bytes(), path.name
    report = dict(result="passed", changed_source_files=sorted(changed),
                  added_test_fixture="testdata/ch347_dll120_interfaces.json",
                  calibration_profiles_byte_identical=True, other_production_sources_byte_identical=True,
                  executable_embedded_cs_assert=0, executable_embedded_cs_deassert=1,
                  executable_embedded_version="5.3.15-spi-cs-fix", executable_dll_hash_verified=True,
                  package_sha256=sha(PACKAGE.read_bytes()), hardware_accessed=False,
                  software_fixture_replay="outputs/spi_audit_20261019/gui_v5_fixture_replay.json")
    target = ROOT / "outputs/spi_audit_20261019/gui_cs_fix_release_verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
