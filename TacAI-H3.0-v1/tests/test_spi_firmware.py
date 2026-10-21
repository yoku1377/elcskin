"""Compile and inject faults into the actual SPI application C implementation."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def compiler_environment(testcase):
    env = {key.upper(): value for key, value in os.environ.items()} if os.name == "nt" else os.environ.copy()
    compiler = None
    if os.name == "nt":
        extensions = [entry for entry in env.get("PATHEXT", "").split(";") if entry]
        for extension in (".COM", ".EXE", ".BAT", ".CMD"):
            if extension not in {entry.upper() for entry in extensions}:
                extensions.append(extension)
        env["PATHEXT"] = ";".join(extensions)
        vcvars = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat")
        if vcvars.exists():
            with tempfile.TemporaryDirectory(prefix="h3_spi_vcenv_") as temp:
                batch = Path(temp) / "prepare.cmd"
                batch.write_text(f'@echo off\ncall "{vcvars}" >nul\nif errorlevel 1 exit /b 1\nset\n', encoding="utf-8")
                prepared = subprocess.run(["cmd.exe", "/d", "/c", str(batch)], env=env, capture_output=True,
                                          text=True, encoding="mbcs", check=True)
            for line in prepared.stdout.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    if key:
                        env[key.upper()] = value
            candidates = []
            if env.get("VCTOOLSINSTALLDIR"):
                candidates.append(Path(env["VCTOOLSINSTALLDIR"]) / "bin" / "Hostx64" / "x64" / "cl.exe")
            candidates.extend(Path(entry.strip('"')) / "cl.exe" for entry in env.get("PATH", "").split(os.pathsep) if entry)
            compiler = next((str(path) for path in candidates if path.is_file()), None)
            testcase.assertIsNotNone(compiler, "vcvars64 succeeded but no cl.exe was found")
        else:
            compiler = shutil.which("cl.exe")
    compiler = compiler or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        testcase.skipTest("A host C compiler is required for production SPI firmware injection")
    return compiler, env


class SpiFirmwareTests(unittest.TestCase):
    def compile_and_run(self, harness, expected):
        compiler, env = compiler_environment(self)
        stubs = ROOT / "tests" / "firmware_spi"
        includes = [stubs, ROOT / "firmware" / "application" / "Core" / "Inc", ROOT / "firmware" / "shared"]
        with tempfile.TemporaryDirectory(prefix="h3_spi_app_faults_") as temp:
            executable = Path(temp) / (harness + (".exe" if os.name == "nt" else ""))
            if Path(compiler).name.lower() == "cl.exe":
                args = [compiler, "/nologo", "/std:c11", "/utf-8", "/W3"]
                args += [f"/I{path}" for path in includes]
                args += [str(stubs / (harness + ".c")), f"/Fe:{executable}",
                         f"/Fo:{Path(temp) / (harness + '.obj')}"]
            else:
                args = [compiler, "-std=c11", "-Wall", "-Wextra"]
                args += [f"-I{path}" for path in includes]
                args += [str(stubs / (harness + ".c")), "-o", str(executable)]
            built = subprocess.run(args, env=env, cwd=temp, capture_output=True, text=True, errors="replace")
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(executable)], capture_output=True, text=True, errors="replace", timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn(expected, checked.stdout)
            print(f"SPI firmware injection compiler: {compiler}", flush=True)
            print(checked.stdout.strip(), flush=True)

    def test_production_application_snapshot_and_upgrade_faults(self):
        self.compile_and_run("spi_application_harness", "PASS: 9 production SPI application")

    def test_production_link_cs_staging_timeout_and_ack(self):
        self.compile_and_run("spi_link_harness", "PASS: 12 production SPI link")

    def test_link_diagnostics_preserve_request_and_response_evidence(self):
        self.compile_and_run("spi_link_diagnostics_harness", "PASS: 13 production SPI link diagnostic")

    def test_rxne_backend_fifo_boundaries_overflow_and_readback(self):
        self.compile_and_run("spi_link_rxirq_harness", "PASS: 7 production SPI RXNE backend")

    def test_application_diagnostic_log_is_read_only_and_rate_limited(self):
        self.compile_and_run("spi_app_diagnostics_harness", "PASS: production SPI diagnostic ID/UART")

    def test_rxirq_application_state_log_preserves_original_diagnostics(self):
        self.compile_and_run("spi_app_rxirq_diagnostics_harness", "PASS: production SPI RX-IRQ diagnostic")


if __name__ == "__main__":
    unittest.main()
