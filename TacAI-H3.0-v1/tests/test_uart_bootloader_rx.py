"""Compile the production UART parser and inject exact UART/SysTick phases."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_spi_firmware import compiler_environment

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "firmware/bootloader/src/main.c"
HARNESS = ROOT / "tests/uart_bootloader_rx_harness.c"


class UartBootloaderRxTests(unittest.TestCase):
    def compile_and_run(self, *, legacy=False):
        compiler, env = compiler_environment(self)
        includes = [ROOT / "tests/uart_bootloader_stubs", ROOT / "firmware/bootloader/include"]
        with tempfile.TemporaryDirectory(prefix="h3_uart_rx_phase_") as directory:
            temp = Path(directory)
            production_source = SOURCE
            if legacy:
                # Restore only the two old magic-loop lines. This verifies
                # that the actual previous implementation fails the same
                # deterministic boundary case which the fixed source passes.
                source = SOURCE.read_text(encoding="utf-8")
                current = ("uint32_t remaining = matched != 0u ? 500u : first_byte_timeout_ms;\n"
                           "        if (matched == 0u && first_byte_timeout_ms != 0xFFFFFFFFu) {")
                previous = ("uint32_t remaining = first_byte_timeout_ms;\n"
                            "        if (first_byte_timeout_ms != 0xFFFFFFFFu) {")
                self.assertEqual(source.count(current), 1)
                production_source = temp / "legacy_main.c"
                production_source.write_text(source.replace(current, previous), encoding="utf-8")
            wrapper = temp / "parser_test.c"
            wrapper.write_text(
                f'#define UART_BOOTLOADER_SOURCE "{production_source.as_posix()}"\n'
                f'#include "{HARNESS.as_posix()}"\n', encoding="utf-8")
            executable = temp / ("parser_test.exe" if os.name == "nt" else "parser_test")
            if Path(compiler).name.lower() == "cl.exe":
                args = [compiler, "/nologo", "/std:c11", "/utf-8", "/W3"]
                args += [f"/I{path}" for path in includes]
                args += [str(wrapper), f"/Fe:{executable}", f"/Fo:{temp / 'parser_test.obj'}"]
            else:
                args = [compiler, "-std=c11", "-Wall", "-Wextra"]
                args += [f"-I{path}" for path in includes]
                args += [str(wrapper), "-o", str(executable)]
            built = subprocess.run(args, cwd=temp, env=env, capture_output=True, text=True, errors="replace")
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            command = [str(executable)] + (["--boundary-only"] if legacy else [])
            result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=10)
            print(f"UART parser injection compiler: {compiler}", flush=True)
            print((result.stdout + result.stderr).strip(), flush=True)
            return result

    def test_production_rx_handles_poll_boundary_and_bounded_faults(self):
        result = self.compile_and_run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: production UART bootloader RX", result.stdout)

    def test_old_magic_deadline_reproduces_silent_data_loss(self):
        result = self.compile_and_run(legacy=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("magic-window boundary must retain TAI prefix", result.stderr)


if __name__ == "__main__":
    unittest.main()
