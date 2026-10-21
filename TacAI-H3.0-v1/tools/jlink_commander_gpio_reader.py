"""Restricted GPIO reads using the locally installed SEGGER J-Link Commander.

No process is started on import or by --self-test.  No SEGGER binary is shipped.
The commands below match the user's successful V7.54d Commander session.  This
backend sends no explicit reset/halt/go, exec, write, erase or flash commands.
Native Connect establishes a debug session; its internal behavior is controlled
by SEGGER, so this module does not claim an SDK-level no-reset guarantee.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

# This import defines constants only; it does not load any J-Link DLL.
from jlink_gpio_reader import ALLOWED_ADDRESSES, REGISTER_NAMES, DEVICE

PROMPT = b"J-Link>"
MAX_REPLY_BYTES = 256 * 1024
STARTUP_TIMEOUT = 6.0
COMMAND_TIMEOUT = 5.0
_EOF = object()
_ERROR = re.compile(r"\b(?:ERROR|Unknown|Cannot|Failed)\b", re.IGNORECASE)
_NORMAL_EXIT_NOTICE = "J-Link Commander will now exit on Error"


def _has_error(text):
    # V7.54d prints this exact informational line for -ExitOnError 1.
    # Keep error detection for every other line, including startup failures.
    return bool(_ERROR.search("\n".join(
        line for line in text.splitlines() if line.strip() != _NORMAL_EXIT_NOTICE)))


class CommanderReadError(RuntimeError):
    pass


def exe_candidates():
    roots = {Path(os.environ[name]) for name in
             ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)") if os.environ.get(name)}
    roots.update((Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")))
    paths = {p for root in roots for p in (root / "SEGGER").glob("JLink*/JLink.exe") if p.is_file()}
    return sorted(paths, key=lambda p: (p.stat().st_mtime, str(p)), reverse=True)


def discover_exe(path=None):
    if path is None:
        candidates = exe_candidates()
        if not candidates:
            raise CommanderReadError("未找到已安装的 SEGGER JLink.exe；请指定原厂程序路径。")
        path = candidates[0]
    result = Path(path).expanduser().resolve(strict=True)
    if result.is_dir():
        result = (result / "JLink.exe").resolve(strict=True)
    if result.name.lower() != "jlink.exe" or not result.is_file():
        raise CommanderReadError(f"需要原厂 JLink.exe 路径：{result}")
    return result


class CommanderGPIOReader:
    """One background Commander session, fixed setup and whitelisted read32.

    All target operations must be issued by one caller thread.  A separate
    thread reads stdout, including prompts which have no trailing newline.
    Errors are sticky: an instance cannot resume reading after any failure.
    """
    def __init__(self, path=None, log=None):
        self.exe_path = str(path) if path is not None else None
        self.dll_path = self.exe_path  # Compatibility with witness evidence schema.
        self.events = []
        self.first_error = None
        self.diagnostics = {"backend": "native_commander", "exe_path": self.exe_path,
                            "exe_candidates": [], "device": DEVICE, "stage": "not_opened",
                            "native_version": None, "serial_text": [], "commands": [],
                            "connect_behavior": "no explicit reset/halt/go/write/flash; native Connect"}
        self._log = log
        self._process = None
        self._thread = None
        self._queue = queue.Queue(maxsize=128)
        self._stream_error = None
        self._tail = b""
        self._opened = False
        self._attempted = False
        self._closed = False
        self._owner = None

    @property
    def is_open(self):
        return self._opened

    def _note(self, text):
        text = str(text)
        self.events.append(text)
        if self._log is not None:
            try:
                self._log(text)
            except Exception as error:
                self.events.append(f"log callback failed: {error}")

    def _remember(self, error):
        if self.first_error is None:
            self.first_error = str(error)
        self.diagnostics["first_error"] = self.first_error
        self._note(str(error))

    def _put(self, item):
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self._stream_error = "Commander reply queue exceeded its limit"

    def _read_output(self):
        pending = bytearray()
        try:
            stream = self._process.stdout
            read = getattr(stream, "read1", stream.read)
            while True:
                chunk = read(4096)
                if not chunk:
                    break
                pending.extend(chunk)
                while True:
                    position = pending.find(PROMPT)
                    if position < 0:
                        break
                    if position > MAX_REPLY_BYTES:
                        raise CommanderReadError("Commander reply exceeded its byte limit")
                    self._put(bytes(pending[:position]))
                    del pending[:position + len(PROMPT)]
                if len(pending) > MAX_REPLY_BYTES:
                    raise CommanderReadError("Commander reply exceeded its byte limit without a prompt")
                self._tail = bytes(pending)
        except Exception as error:
            self._stream_error = f"Commander stdout read failed: {error}"
        finally:
            self._tail = bytes(pending[:MAX_REPLY_BYTES])
            self._put(_EOF)

    @staticmethod
    def _decode(data):
        return data.decode("utf-8", errors="replace")

    def _capture(self, text):
        self._note("REPLY " + text)
        if self.diagnostics["native_version"] is None:
            version = re.search(r"J-Link Commander[^\r\n]*", text, re.IGNORECASE)
            if version:
                self.diagnostics["native_version"] = version.group(0).strip()
        for line in text.splitlines():
            if re.search(r"(?:S/N\s*:|Serial(?:\s+number)?\s*[:=])", line, re.IGNORECASE):
                if line.strip() not in self.diagnostics["serial_text"]:
                    self.diagnostics["serial_text"].append(line.strip())

    def _reply(self, timeout, label):
        deadline = time.monotonic() + timeout
        while True:
            if self._stream_error:
                raise CommanderReadError(self._stream_error)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                partial = self._decode(self._tail)
                if partial:
                    self._capture(partial)
                raise CommanderReadError(f"Commander timeout waiting for {label} ({timeout:g}s)")
            try:
                item = self._queue.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if item is _EOF:
                partial = self._decode(self._tail)
                if partial:
                    self._capture(partial)
                raise CommanderReadError(f"Commander exited before the {label} prompt; rc={self._process.poll()}")
            text = self._decode(item)
            self._capture(text)
            if _has_error(text):
                raise CommanderReadError(f"Commander rejected {label}: {text.strip()}")
            return text

    def _assert_thread(self):
        if self._owner is not None and self._owner != threading.get_ident():
            raise CommanderReadError("Commander session must be used from its opening thread")

    @staticmethod
    def _allowed_command(command):
        if command in (f"device {DEVICE}", "si SWD", "speed 1000", "connect", "IsHalted", "exit"):
            return True
        match = re.fullmatch(r"mem32 0x([0-9A-F]{8}) 1", command)
        return bool(match and int(match.group(1), 16) in ALLOWED_ADDRESSES)

    def _send(self, command):
        if not self._allowed_command(command):
            raise CommanderReadError(f"Commander command is not permitted: {command}")
        self.diagnostics["commands"].append(command)
        self._note("COMMAND " + command)
        self._process.stdin.write((command + "\n").encode("ascii"))
        self._process.stdin.flush()

    def _command(self, command):
        self._assert_thread()
        self._send(command)
        return self._reply(COMMAND_TIMEOUT, command)

    def _check_running(self):
        reply = self._command("IsHalted")
        if not re.search(r"(?im)^\s*CPU is not halted\.\s*$", reply):
            raise CommanderReadError("Commander did not confirm 'CPU is not halted.'; no GPIO read is allowed")

    def open(self):
        if self._attempted or self._closed or self.first_error:
            raise CommanderReadError("Commander instance cannot be reopened or retried")
        self._attempted = True
        self._owner = threading.get_ident()
        try:
            self.diagnostics["exe_candidates"] = [str(path) for path in exe_candidates()]
            path = discover_exe(self.exe_path)
            self.exe_path = self.dll_path = str(path)
            self.diagnostics.update(exe_path=self.exe_path,
                                    exe_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                    stage="usb_startup")
            self._note(f"Native Commander: {path}")
            arguments = [str(path), "-AutoConnect", "0", "-NoGui", "1", "-ExitOnError", "1"]
            self.diagnostics["arguments"] = arguments
            options = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                       "stderr": subprocess.STDOUT, "bufsize": 0, "cwd": str(path.parent)}
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            self._process = subprocess.Popen(arguments, **options)
            self._thread = threading.Thread(target=self._read_output, name="jlink-commander-output", daemon=True)
            self._thread.start()
            startup = self._reply(STARTUP_TIMEOUT, "startup")
            self.diagnostics["usb_startup_report"] = startup
            if not re.search(r"Connecting to J-Link via USB[^\r\n]*O\.K\.", startup, re.IGNORECASE):
                raise CommanderReadError("Commander startup did not confirm USB connection O.K.; target setup was not sent")
            self.diagnostics["stage"] = "target_connect"
            for command in (f"device {DEVICE}", "si SWD", "speed 1000", "connect"):
                self._command(command)
            self._check_running()
            self._opened = True
            self.diagnostics["stage"] = "running"
            return self
        except Exception as error:
            self._remember(error)
            self.close()
            raise

    def read32(self, address):
        self._assert_thread()
        if type(address) is not int or address not in ALLOWED_ADDRESSES:
            raise CommanderReadError(f"Address is outside the static GPIO/RCC/ID whitelist: {address!r}")
        if not self._opened or self.first_error:
            raise CommanderReadError("Commander is not open, or an earlier error prevents more reads")
        try:
            self._check_running()
            reply = self._command(f"mem32 0x{address:08X} 1")
            # Require one and only one address/value record; never turn an error into zero.
            records = re.findall(r"(?im)^\s*(?:0x)?([0-9a-f]{8})\s*=\s*([0-9a-f]{8})\s*$", reply)
            if len(records) != 1 or int(records[0][0], 16) != address:
                raise CommanderReadError(f"Malformed/ambiguous Commander mem32 reply for 0x{address:08X}: {reply.strip()}")
            value = int(records[0][1], 16)
            self._check_running()
            self._note(f"READ {REGISTER_NAMES[address]} 0x{address:08X}=0x{value:08X}")
            return value
        except Exception as error:
            self._remember(error)
            raise

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._opened = False
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                try:
                    self._send("exit")
                except Exception as error:
                    self._note(f"Commander exit command failed: {error}")
                try:
                    process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    self._note("Commander did not exit in time; terminating the host process")
                    process.terminate()
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1.0)
            if self._thread is not None:
                self._thread.join(timeout=0.3)
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is not _EOF:
                    self._capture(self._decode(item))
            if self._tail:
                self._capture(self._decode(self._tail))
            self.diagnostics["process_returncode"] = process.poll()
        except Exception as error:
            self.diagnostics["close_error"] = str(error)
            self._remember(error)
        finally:
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except Exception as error:
                    self._note(f"Commander pipe close failed: {error}")
            self.diagnostics["stage"] = "closed_after_error" if self.first_error else "closed"

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def self_test():
    """Fake pipes/processes only: never execute Commander or load a hardware DLL."""
    import tempfile
    from unittest.mock import patch

    class Output:
        def __init__(self):
            self.parts = queue.Queue()
            self.closed = False
        def feed(self, data):
            # Deliberately split every prompt and every response record.
            for position in range(0, len(data), 3):
                self.parts.put(data[position:position + 3])
        def read(self, count):
            return self.parts.get()
        def close(self):
            self.closed = True
            self.parts.put(b"")

    class FakeProcess:
        def __init__(self, mode="ok"):
            self.mode, self.calls, self.rc = mode, [], None
            self.stdout, self.stdin = Output(), self
            self.halt_checks = 0
            # Exact startup transcript supplied by the user, including the
            # informational Error notice which must not trigger rejection.
            startup = (b"SEGGER J-Link Commander V7.54d (Compiled Sep 28 2021 16:20:01)\n"
                       b"DLL version V7.54d, compiled Sep 28 2021 16:18:43\n\n"
                       b"J-Link Commander will now exit on Error\n"
                       b"Connecting to J-Link via USB...O.K.\n"
                       b"Firmware: J-Link V9 compiled May  7 2021 16:26:12\n"
                       b"Hardware version: V9.20\n"
                       b"S/N: -1\n"
                       b"License(s): RDI, GDB, FlashDL, FlashBP, JFlash\n"
                       b"VTref=3.295V\n\n\n"
                       b"Type \"connect\" to establish a target connection, '?' for help\n"
                       b"J-Link>")
            if mode == "usb_error":
                startup = b"Connecting to J-Link via USB...Cannot connect.\nJ-Link>"
            if mode == "no_usb":
                startup = b"J-Link Commander V7.54d\nJ-Link>"
            if mode == "oversize":
                startup = b"x" * (MAX_REPLY_BYTES + 1)
            if mode != "timeout":
                self.stdout.feed(startup)
        def write(self, data):
            command = data.decode().strip()
            self.calls.append(command)
            if command == "exit":
                if self.mode != "exit_stuck":
                    self.rc = 0
                    self.stdout.feed(b"Bye\n")
                    self.stdout.parts.put(b"")
                return len(data)
            if self.mode == "command_timeout" and command.startswith("device"):
                return len(data)
            if command == "IsHalted":
                self.halt_checks += 1
                halted = self.mode == "halted" or (self.mode == "halt_after_read" and self.halt_checks >= 3)
                text = "CPU is halted." if halted else "CPU is not halted."
            elif command.startswith("mem32"):
                address = command.split()[1][2:]
                text = address + " = 00000110"
                if self.mode == "bad_address":
                    text = "50000410 = 00000110"
                elif self.mode == "bad_value":
                    text = address + " = 0000011G"
                elif self.mode == "duplicate":
                    text += "\n" + text
                elif self.mode in ("ERROR", "Unknown", "Cannot", "Failed"):
                    text = self.mode + " read"
            else:
                text = "O.K."
            self.stdout.feed((command + "\n" + text + "\nJ-Link>").encode())
            return len(data)
        def flush(self):
            pass
        def close(self):
            pass
        def poll(self):
            return self.rc
        def wait(self, timeout):
            if self.rc is None:
                raise subprocess.TimeoutExpired("fake", timeout)
            return self.rc
        def terminate(self):
            self.rc = -15
            self.stdout.parts.put(b"")
        def kill(self):
            self.terminate()

    count = 0
    assert not _has_error(_NORMAL_EXIT_NOTICE)
    for text in ("ERROR", "Unknown command", "Cannot connect", "Failed to read",
                 _NORMAL_EXIT_NOTICE + "\nERROR: no target",
                 _NORMAL_EXIT_NOTICE + " unexpected ERROR"):
        assert _has_error(text), text
    with tempfile.TemporaryDirectory(prefix="h3_commander_fake_") as temporary:
        path = Path(temporary) / "JLink.exe"
        path.write_bytes(b"fake, never executable")
        with patch(__name__ + ".exe_candidates", return_value=[path]), \
                patch(__name__ + ".STARTUP_TIMEOUT", 0.08), patch(__name__ + ".COMMAND_TIMEOUT", 0.08):
            for mode in ("ok", "exit_stuck", "usb_error", "no_usb", "timeout", "command_timeout", "oversize",
                         "halted", "halt_after_read", "bad_address", "bad_value", "duplicate",
                         "ERROR", "Unknown", "Cannot", "Failed"):
                fake = FakeProcess(mode)
                reader = CommanderGPIOReader(path)
                succeeded = False
                with patch(__name__ + ".subprocess.Popen", return_value=fake) as popen:
                    try:
                        reader.open()
                        assert reader.read32(0x50000010) == 0x110
                        succeeded = True
                        for bad_address in (0x4000380C, 0x08000000, 0x20000000, 0x50000018, True, "0x50000010"):
                            before = list(fake.calls)
                            try:
                                reader.read32(bad_address)
                                raise AssertionError("forbidden address accepted")
                            except CommanderReadError:
                                assert fake.calls == before
                        assert reader.diagnostics["serial_text"] == ["S/N: -1"]
                        assert "V7.54d" in reader.diagnostics["native_version"]
                    except CommanderReadError:
                        assert mode not in ("ok", "exit_stuck"), (mode, reader.first_error)
                    finally:
                        original_error = reader.first_error
                        reader.close()
                        reader.close()
                    assert succeeded == (mode in ("ok", "exit_stuck")), mode
                    assert reader.first_error == original_error
                    assert fake.rc is not None and fake.stdout.closed
                    assert all(reader._allowed_command(command) for command in fake.calls)
                    assert not any(command.lower().startswith(("exec", "halt", "r ", "w", "erase", "load")) for command in fake.calls)
                    if mode in ("halted", "usb_error", "no_usb", "timeout", "command_timeout", "oversize"):
                        assert not any(command.startswith("mem32") for command in fake.calls)
                    arguments, options = popen.call_args
                    assert arguments[0][1:] == ["-AutoConnect", "0", "-NoGui", "1", "-ExitOnError", "1"]
                    if os.name == "nt":
                        assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
                    count += 1
    print(f"PASS: Commander GPIO fake-only self-test ({count} scenarios; no native executable or hardware DLL run)")
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if not args.self_test:
        parser.error("This backend is used by the GPIO witness tool; only --self-test is standalone")
    self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
