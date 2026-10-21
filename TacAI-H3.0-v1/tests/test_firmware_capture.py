"""Host C fault injection of production matrix.c, app.c and log.c in both transports."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FirmwareCaptureTests(unittest.TestCase):
    def compile_capture_and_run(self, spi=False):
        # Windows environment names are case-insensitive. Keep one PATH entry
        # so vcvars64 cannot leave an older mixed-case Path shadowing its PATH.
        env = {key.upper(): value for key, value in os.environ.items()} if os.name == "nt" else os.environ.copy()
        if os.name == "nt":
            # vcvars64 itself invokes SDK discovery utilities through cmd.
            # Ensure this isolated build environment can execute native tools
            # even if the caller has an intentionally restricted PATHEXT.
            extensions = [entry for entry in env.get("PATHEXT", "").split(";") if entry]
            for extension in (".COM", ".EXE", ".BAT", ".CMD"):
                if extension not in {entry.upper() for entry in extensions}:
                    extensions.append(extension)
            env["PATHEXT"] = ";".join(extensions)
        compiler = shutil.which("cl.exe") if os.name == "nt" else None
        if os.name == "nt":
            vcvars = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat")
            if vcvars.exists():
                # Capture the initialized compiler environment without printing it.
                with tempfile.TemporaryDirectory(prefix="h3_vcenv_") as env_temp:
                    batch = Path(env_temp) / "prepare.cmd"
                    batch.write_text(f'@echo off\ncall "{vcvars}" >nul\nif errorlevel 1 exit /b 1\nset\n', encoding="utf-8")
                    prepared = subprocess.run(
                        ["cmd.exe", "/d", "/c", str(batch)], env=env,
                        capture_output=True, text=True, encoding="mbcs", check=True,
                    )
                for line in prepared.stdout.splitlines():
                    if "=" in line:
                        key, value = line.split("=", 1)
                        if key:
                            env[key.upper()] = value
                # shutil.which("cl") depends on the process PATHEXT, even when
                # a separately initialized PATH is supplied. Resolve the real
                # executable directly so a restricted PATHEXT cannot skip the
                # fault tests despite an installed, initialized compiler.
                candidates = []
                if env.get("VCTOOLSINSTALLDIR"):
                    candidates.append(Path(env["VCTOOLSINSTALLDIR"]) / "bin" / "Hostx64" / "x64" / "cl.exe")
                candidates.extend(Path(entry.strip('"')) / "cl.exe" for entry in env.get("PATH", "").split(os.pathsep) if entry)
                compiler = next((str(path) for path in candidates if path.is_file()), None)
                self.assertIsNotNone(compiler, "vcvars64 succeeded but no cl.exe was found; refusing to skip the installed compiler")
        if compiler is None:
            compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if compiler is None:
            self.skipTest("A host C compiler is required for firmware fault injection")

        stubs = ROOT / "tests" / "firmware_capture"
        includes = ROOT / "firmware" / "application" / "Core" / "Inc"
        with tempfile.TemporaryDirectory(prefix="h3_capture_faults_") as temp:
            executable = Path(temp) / ("capture_harness.exe" if os.name == "nt" else "capture_harness")
            if Path(compiler).name.lower() == "cl.exe":
                args = [compiler, "/nologo", "/std:c11", "/utf-8", "/W3", f"/I{stubs}", f"/I{includes}",
                        str(stubs / "capture_harness.c"), f"/Fe:{executable}", f"/Fo:{Path(temp) / 'capture_harness.obj'}"]
                if spi:
                    args.append("/DAPP_ENABLE_SPI=1")
            else:
                args = [compiler, "-std=c11", "-Wall", "-Wextra", f"-I{stubs}", f"-I{includes}",
                        str(stubs / "capture_harness.c"), "-o", str(executable)]
                if spi:
                    args.append("-DAPP_ENABLE_SPI=1")
            built = subprocess.run(args, env=env, cwd=temp, capture_output=True, text=True, errors="replace")
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            checked = subprocess.run([str(executable)], capture_output=True, text=True, errors="replace")
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            expected = "PASS: 8 production capture/log recovery cases (SPI)" if spi else "PASS: 9 production capture/log recovery cases (UART)"
            self.assertIn(expected, checked.stdout)
            print(f"Firmware fault injection compiler: {compiler}", flush=True)
            print(checked.stdout.strip(), flush=True)

    def test_uart_capture_recovers_transient_faults_and_preserves_fatal_startup(self):
        self.compile_capture_and_run()

    def test_spi_capture_recovers_transient_faults_and_preserves_fatal_startup(self):
        self.compile_capture_and_run(spi=True)


if __name__ == "__main__":
    unittest.main()
