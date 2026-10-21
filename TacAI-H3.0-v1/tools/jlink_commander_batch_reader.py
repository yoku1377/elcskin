"""Finite, read-only GPIO command files for the installed J-Link Commander.

Output is parsed only after the native process exits: no live stdout flush or
interactive prompt is required.  Import, open() and --self-test do not connect
to hardware.  read_batch() is the only operation which starts Commander.
There are no explicit reset/halt/go/write/flash commands. SEGGER controls the
internal behavior of its native Connect operation.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from jlink_commander_gpio_reader import discover_exe, exe_candidates, _has_error

DEVICE = "STM32G031G8"
ALLOWED_ADDRESSES = frozenset((0x50000000, 0x50000010, 0x50000020, 0x50000024))
MAX_READS = 64
BATCH_TIMEOUT = 8.0
_clock = time.perf_counter


class CommanderBatchReadError(RuntimeError):
    pass


def _parse(stdout, returncode, addresses):
    """Require running/read.../running tokens in the exact scripted order."""
    if returncode != 0:
        raise CommanderBatchReadError(f"Commander batch exited with code {returncode}")
    if _has_error(stdout):
        raise CommanderBatchReadError("Commander batch reported an error; see raw stdout")
    if not re.search(r"Connecting to J-Link via USB[^\r\n]*O\.K\.", stdout, re.IGNORECASE):
        raise CommanderBatchReadError("Commander batch did not confirm USB connection O.K.")
    tokens = []
    values = []
    for line in stdout.splitlines():
        text = line.strip()
        if text.startswith("CPU is "):
            if text != "CPU is not halted.":
                raise CommanderBatchReadError(f"Commander CPU running check failed: {text}")
            tokens.append(("running",))
            continue
        # Include malformed address/value records in validation rather than
        # silently dropping them. VTref is the known non-memory startup field.
        candidate = re.fullmatch(r"(\S+)\s*=\s*(.*?)\s*", text)
        if candidate and candidate[1] != "VTref":
            address_text, value_text = candidate.groups()
            record = re.fullmatch(r"(?:0x)?([0-9A-Fa-f]{8})", address_text)
            value = re.fullmatch(r"[0-9A-Fa-f]{8}", value_text)
            if record is None or value is None:
                raise CommanderBatchReadError(f"Malformed Commander memory record: {text}")
            address = int(record[1], 16)
            tokens.append(("memory", address))
            values.append(int(value_text, 16))
    expected = [("running",), *(("memory", address) for address in addresses), ("running",)]
    if tokens != expected or len(values) != len(addresses):
        raise CommanderBatchReadError("Commander memory addresses/count/order or CPU checks do not match the command file")
    return values


class CommanderBatchGPIOReader:
    def __init__(self, path=None):
        self.exe_path = str(path) if path is not None else None
        self.dll_path = self.exe_path  # Existing witness evidence field.
        self.events = []
        self.first_error = None
        self.diagnostics = {"backend": "native_commander_batch", "device": DEVICE,
                            "exe_path": self.exe_path, "exe_candidates": [], "batches": [],
                            "stage": "not_opened", "native_version": None, "serial_text": [],
                            "connect_behavior": "no explicit reset/halt/go/write/flash; native Connect"}
        self._opened = False
        self._closed = False

    def _remember(self, error):
        if self.first_error is None:
            self.first_error = str(error)
        self.diagnostics["first_error"] = self.first_error
        self.events.append(str(error))

    def open(self):
        if self._closed or self.first_error:
            raise CommanderBatchReadError("Batch reader is closed or has an earlier error")
        if self._opened:
            return self
        try:
            self.diagnostics["exe_candidates"] = [str(path) for path in exe_candidates()]
            path = discover_exe(self.exe_path)
            self.exe_path = self.dll_path = str(path)
            self.diagnostics.update(exe_path=self.exe_path,
                                    exe_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                    stage="located_no_connection")
            self.events.append("Native Commander batch executable: " + self.exe_path)
            self._opened = True
            return self
        except Exception as error:
            self._remember(error)
            raise

    @staticmethod
    def _text(data):
        return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data or "")

    def read_batch(self, addresses):
        process = None
        entry = None
        started = None
        try:
            if not self._opened or self._closed or self.first_error:
                raise CommanderBatchReadError("Batch reader is not open, or an earlier error prevents more reads")
            if not isinstance(addresses, (tuple, list)) or not 1 <= len(addresses) <= MAX_READS:
                raise CommanderBatchReadError("A batch needs a list/tuple of 1..64 GPIO addresses")
            addresses = tuple(addresses)
            if any(type(address) is not int or address not in ALLOWED_ADDRESSES for address in addresses):
                raise CommanderBatchReadError("Only GPIOA MODER/IDR/AFRL/AFRH reads are permitted")
            commands = [f"device {DEVICE}", "si SWD", "speed 1000", "connect", "IsHalted",
                        *(f"mem32 0x{address:08X} 1" for address in addresses), "IsHalted", "exit"]
            command_text = "\n".join(commands) + "\n"
            entry = {"commands": commands, "command_text": command_text,
                     "addresses": [f"0x{address:08X}" for address in addresses], "success": False}
            self.diagnostics["batches"].append(entry)
            self.events.append("COMMAND FILE\n" + command_text)
            with tempfile.TemporaryDirectory(prefix="h3_jlink_gpio_batch_") as temporary:
                command_file = Path(temporary) / "gpio_readonly.jlink"
                command_file.write_bytes(command_text.encode("ascii"))
                arguments = [self.exe_path, "-AutoConnect", "0", "-NoGui", "1", "-ExitOnError", "1",
                             "-CommandFile", str(command_file)]
                entry["arguments"] = arguments
                options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                           "stderr": subprocess.STDOUT, "cwd": str(Path(self.exe_path).parent)}
                if os.name == "nt":
                    options["creationflags"] = subprocess.CREATE_NO_WINDOW
                started = _clock()
                entry["host_start"] = started
                process = subprocess.Popen(arguments, **options)
                try:
                    stdout, _ = process.communicate(timeout=BATCH_TIMEOUT)
                except subprocess.TimeoutExpired as error:
                    # A timeout is always failure, even if cleanup later returns
                    # a complete-looking transcript. Preserve the original cause.
                    self._remember(CommanderBatchReadError(f"Commander batch timeout ({BATCH_TIMEOUT:g}s)"))
                    entry["timeout"] = True
                    stdout = error.output or b""
                    process.terminate()
                    try:
                        stdout, _ = process.communicate(timeout=1.0)
                    except subprocess.TimeoutExpired as second:
                        stdout = second.output or stdout
                        process.kill()
                        try:
                            stdout, _ = process.communicate(timeout=1.0)
                        except subprocess.TimeoutExpired as third:
                            stdout = third.output or stdout
                            entry["cleanup_error"] = "Commander remained alive after terminate/kill deadlines"
                    ended = _clock()
                    entry.update(host_end=ended, returncode=process.poll(), stdout=self._text(stdout))
                    self.events.append("STDOUT\n" + entry["stdout"])
                    raise CommanderBatchReadError(self.first_error) from error
                ended = _clock()  # Process has terminated; parsing time is excluded.
                entry.update(host_end=ended, returncode=process.returncode, stdout=self._text(stdout))
                self.events.append("STDOUT\n" + entry["stdout"])
            text = entry["stdout"]
            version = re.search(r"J-Link Commander[^\r\n]*", text, re.IGNORECASE)
            if version:
                self.diagnostics["native_version"] = version[0].strip()
            for line in text.splitlines():
                if re.search(r"S/N\s*:", line) and line.strip() not in self.diagnostics["serial_text"]:
                    self.diagnostics["serial_text"].append(line.strip())
            values = _parse(text, entry["returncode"], addresses)
            entry.update(success=True, values=values)
            self.diagnostics["stage"] = "batch_completed"
            return {"values": values, "host_start": started, "host_end": ended}
        except Exception as error:
            if entry is not None:
                entry["error"] = str(error)
            self._remember(error)
            raise
        finally:
            if process is not None:
                # communicate() normally closes these; cover Popen/cleanup
                # exceptions without leaving pipe handles behind.
                for stream in (getattr(process, "stdin", None), getattr(process, "stdout", None)):
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception as error:
                            self.events.append(f"Commander pipe close failed: {error}")

    def close(self):
        self._closed = True
        self._opened = False
        self.diagnostics["stage"] = "closed_after_error" if self.first_error else "closed"

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def self_test():
    """All subprocesses below are fake; no native executable or hardware DLL."""
    from unittest.mock import patch

    # Exact startup transcript supplied by the user; redirected mode has no prompt.
    startup = ("SEGGER J-Link Commander V7.54d (Compiled Sep 28 2021 16:20:01)\n"
               "DLL version V7.54d, compiled Sep 28 2021 16:18:43\n\n"
               "J-Link Commander will now exit on Error\n"
               "Connecting to J-Link via USB...O.K.\n"
               "Firmware: J-Link V9 compiled May  7 2021 16:26:12\n"
               "Hardware version: V9.20\nS/N: -1\n"
               "License(s): RDI, GDB, FlashDL, FlashBP, JFlash\nVTref=3.295V\n\n\n"
               "Type \"connect\" to establish a target connection, '?' for help\n")
    addresses = [0x50000000, 0x50000010, 0x50000020, 0x50000024, 0x50000010]

    class FakeProcess:
        def __init__(self, arguments, mode):
            self.mode, self.returncode, self.waits, self.actions = mode, None, [], []
            self.stdin = self.stdout = None
            self.script = Path(arguments[-1]).read_text(encoding="ascii")
        def communicate(self, timeout):
            self.waits.append(timeout)
            if self.mode == "timeout" and not self.actions:
                raise subprocess.TimeoutExpired("fake", timeout, output=startup.encode())
            if self.mode in ("kill", "kill_timeout") and "kill" not in self.actions:
                raise subprocess.TimeoutExpired("fake", timeout, output=startup.encode())
            if self.mode == "kill_timeout":
                raise subprocess.TimeoutExpired("fake", timeout, output=startup.encode())
            self.returncode = 1 if self.mode == "rc_error" else 0
            records = [f"{address:08X} = {number:08X}" for number, address in enumerate(addresses)]
            if self.mode == "wrong_address":
                records[1] = "50000410 = 00000001"
            elif self.mode == "duplicate":
                records.insert(1, records[0])
            elif self.mode == "missing":
                records.pop()
            elif self.mode == "extra":
                records.append("50000010 = 00000000")
            elif self.mode == "bad_address":
                records[1] = "5000001G = 00000001"
            elif self.mode == "bad_value":
                records[1] = "50000010 = 0000000G"
            elif self.mode == "order":
                records[0], records[1] = records[1], records[0]
            before = "CPU is halted." if self.mode == "halted" else "CPU is not halted."
            after = "CPU is halted." if self.mode == "halted_after" else "CPU is not halted."
            body = before + "\n" + "\n".join(records) + "\n" + after + "\n"
            if self.mode == "cpu_missing":
                body = body.replace(after + "\n", "", 1)
            elif self.mode == "cpu_extra":
                body += "CPU is not halted.\n"
            elif self.mode in ("ERROR", "Unknown", "Cannot", "Failed"):
                body += self.mode + " command\n"
            intro = startup.replace("O.K.", "not connected") if self.mode == "usb_missing" else startup
            # Nothing is observable before communicate; all stdout arrives on exit.
            return (intro + body).encode(), None
        def terminate(self):
            self.actions.append("terminate")
        def kill(self):
            self.actions.append("kill")
        def poll(self):
            return self.returncode

    count = 0
    with tempfile.TemporaryDirectory(prefix="h3_jlink_batch_fake_") as temporary:
        path = Path(temporary) / "JLink.exe"
        path.write_bytes(b"fake, not executable")
        with patch(__name__ + ".exe_candidates", return_value=[path]):
            for mode in ("ok", "timeout", "kill", "kill_timeout", "rc_error", "wrong_address", "duplicate",
                         "missing", "extra", "bad_address", "bad_value", "order", "halted", "halted_after",
                         "cpu_missing", "cpu_extra", "ERROR", "Unknown", "Cannot", "Failed", "usb_missing"):
                instances = []
                def factory(arguments, **options):
                    assert options["stdin"] == subprocess.DEVNULL
                    assert options["stderr"] == subprocess.STDOUT
                    if os.name == "nt":
                        assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
                    assert arguments[1:7] == ["-AutoConnect", "0", "-NoGui", "1", "-ExitOnError", "1"]
                    assert arguments[7] == "-CommandFile"
                    fake = FakeProcess(arguments, mode)
                    instances.append(fake)
                    return fake
                reader = CommanderBatchGPIOReader(path)
                with patch(__name__ + ".subprocess.Popen", side_effect=factory) as popen, \
                        patch(__name__ + ".time.monotonic", side_effect=AssertionError("use perf_counter")):
                    reader.open()
                    assert not popen.called, "open must not connect"
                    try:
                        result = reader.read_batch(addresses)
                        assert mode == "ok"
                        assert result["values"] == list(range(len(addresses)))
                        assert result["host_start"] <= result["host_end"]
                        assert reader.diagnostics["serial_text"] == ["S/N: -1"]
                        assert "V7.54d" in reader.diagnostics["native_version"]
                    except CommanderBatchReadError:
                        assert mode != "ok", reader.first_error
                        before = popen.call_count
                        try:
                            reader.read_batch(addresses)
                            raise AssertionError("sticky error was ignored")
                        except CommanderBatchReadError:
                            assert popen.call_count == before
                    finally:
                        reader.close()
                    assert len(instances) == 1
                    assert instances[0].script.splitlines() == [
                        f"device {DEVICE}", "si SWD", "speed 1000", "connect", "IsHalted",
                        *(f"mem32 0x{address:08X} 1" for address in addresses), "IsHalted", "exit"]
                    assert instances[0].waits[0] == 8.0
                    assert len(instances[0].waits) <= 3
                    if mode in ("kill", "kill_timeout"):
                        assert instances[0].actions == ["terminate", "kill"]
                    assert reader.diagnostics["batches"][0]["stdout"]
                    count += 1
            for invalid in ([], [0x50000010] * 65, [0x4000380C], [0x20000000], [0x08000000], [True], "50000010"):
                reader = CommanderBatchGPIOReader(path)
                with patch(__name__ + ".subprocess.Popen", side_effect=AssertionError("forbidden native call")):
                    reader.open()
                    try:
                        reader.read_batch(invalid)
                        raise AssertionError("invalid batch accepted")
                    except CommanderBatchReadError:
                        assert reader.first_error
                    reader.close()
                    count += 1
    print(f"PASS: Commander batch fake-only self-test ({count} scenarios; no native process or hardware DLL)")
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.self_test:
        parser.error("This backend is used by the witness tool; standalone use is --self-test")
    self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
