"""Binary UART-IAP protocol used by the TacAI H3 bootloader."""

from __future__ import annotations

import math
import struct
import time
import zlib
from pathlib import Path

from tacai_image import APP_START, FirmwareImage, MODEL_ID

FRAME_MAGIC_BYTES = b"TAI3"
FRAME_MAGIC = 0x33494154
HEADER = struct.Struct("<IBBHHH")
CMD_HELLO = 0x01
CMD_BEGIN = 0x02
CMD_DATA = 0x03
CMD_END = 0x04
CMD_BOOT = 0x05
CMD_ABORT = 0x06
STATUS_NAMES = {
    0: "OK", 1: "BAD_FRAME", 2: "BAD_MANIFEST", 3: "NOT_READY",
    4: "BAD_SEQUENCE", 5: "FLASH_ERROR", 6: "CRC_MISMATCH", 7: "INVALID_VECTOR",
}
CONFIRM_XOR = 0xA5A55A5A


class IapError(RuntimeError):
    pass


def encode_frame(command: int, sequence: int, payload: bytes = b"", flags: int = 0) -> bytes:
    header = HEADER.pack(FRAME_MAGIC, command, flags, sequence & 0xFFFF, len(payload), 0)
    crc = zlib.crc32(payload, zlib.crc32(header)) & 0xFFFFFFFF
    return header + payload + struct.pack("<I", crc)


class IapClient:
    def __init__(self, serial_port, progress=None):
        self.serial = serial_port
        self.sequence = 1
        self.progress = progress or (lambda _done, _total, _message: None)
        self._rx_buffer = bytearray()
        self._last_frame_issue = None
        self._upgrade_total = 0

    def _write_once(self, wire: bytes, description: str) -> None:
        """A partial/uncertain write must never replay an erase or write command."""
        try:
            written = self.serial.write(wire)
            if written != len(wire):
                raise IapError(f"串口短写：期望 {len(wire)} 字节，实际 {written}")
            self.serial.flush()
        except Exception as error:
            raise IapError(f"{description} 发送失败：{error}；请求没有自动重发") from error

    def _extract_frame(self):
        """Keep partial packets and find a complete CRC-valid frame after noise.

        A corrupt length may precede a complete reply. Search later candidates
        too, rather than waiting for all bytes claimed by that damaged header.
        """
        cursor = 0
        incomplete = None
        while True:
            start = self._rx_buffer.find(FRAME_MAGIC_BYTES, cursor)
            if start < 0:
                break
            cursor = start + 1
            if len(self._rx_buffer) - start < HEADER.size:
                incomplete = start if incomplete is None else incomplete
                break
            header = bytes(self._rx_buffer[start:start + HEADER.size])
            _magic, command, status, sequence, length, reserved = HEADER.unpack(header)
            if reserved != 0 or length > 520:
                self._last_frame_issue = f"非法帧头（length={length}, reserved={reserved}）"
                continue
            end = start + HEADER.size + length + 4
            if len(self._rx_buffer) < end:
                incomplete = start if incomplete is None else incomplete
                continue
            payload = bytes(self._rx_buffer[start + HEADER.size:end - 4])
            wire_crc = struct.unpack_from("<I", self._rx_buffer, end - 4)[0]
            calc_crc = zlib.crc32(payload, zlib.crc32(header)) & 0xFFFFFFFF
            if calc_crc != wire_crc:
                self._last_frame_issue = "响应 CRC32 错误"
                continue
            del self._rx_buffer[:end]
            return command, status, sequence, payload
        if incomplete is not None:
            del self._rx_buffer[:incomplete]
        elif len(self._rx_buffer) > len(FRAME_MAGIC_BYTES) - 1:
            # Preserve a possible split magic prefix at the end of this read.
            del self._rx_buffer[:-(len(FRAME_MAGIC_BYTES) - 1)]
        return None

    def _read_frame(self, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self._extract_frame()
            if frame is not None:
                return frame
            waiting = getattr(self.serial, "in_waiting", 0)
            size = min(536, max(1, waiting)) if isinstance(waiting, int) else 1
            block = self.serial.read(size)
            if block:
                self._rx_buffer.extend(block)
        detail = f"；最后帧错误：{self._last_frame_issue}" if self._last_frame_issue else ""
        raise IapError(f"等待 Bootloader 有效响应超时（缓冲 {len(self._rx_buffer)} 字节）{detail}")

    def command(self, command: int, payload: bytes = b"", timeout: float = 3.0) -> bytes:
        if isinstance(command, bool) or not isinstance(command, int) or not 0 <= command <= 0x7F:
            raise IapError("UART 请求命令必须为 0..127")
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise IapError("UART 请求 payload 必须为 bytes")
        payload = bytes(payload)
        if len(payload) > 520:
            raise IapError("UART 请求 payload 超过 520 字节")
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
            raise IapError("UART 响应 timeout 必须为正数")
        sequence = self.sequence
        self.sequence = (self.sequence + 1) & 0xFFFF
        context = f"cmd=0x{command:02X}, seq={sequence}"
        deadline = time.monotonic() + timeout
        self._last_frame_issue = None
        self._write_once(encode_frame(command, sequence, payload), context)
        stale = 0
        last_stale = None
        last_error = None
        while time.monotonic() < deadline:
            try:
                response_command, status, response_sequence, response = self._read_frame(
                    max(0.0, deadline - time.monotonic()))
            except Exception as error:
                last_error = error
                break
            if response_command != (command | 0x80) or response_sequence != sequence:
                stale += 1
                last_stale = f"cmd=0x{response_command:02X}, seq={response_sequence}"
                continue
            if status != 0:
                raise IapError(f"Bootloader 拒绝命令（{context}）：{STATUS_NAMES.get(status, status)}")
            return response
        detail = f"旧帧/其他命令={stale}"
        if last_stale is not None:
            detail += f"，最后旧帧 {last_stale}"
        if last_error is not None:
            detail += f"；{last_error}"
        raise IapError(f"UART 命令未确认（{context}，{detail}）；请求没有自动重发") from last_error

    def enter(self, sync_timeout: float = 12.0) -> dict:
        if not math.isfinite(sync_timeout) or sync_timeout <= 0:
            raise IapError("UART 同步 timeout 必须为正数")
        deadline = time.monotonic() + sync_timeout
        self.serial.reset_input_buffer()
        self._rx_buffer.clear()
        self._write_once(b"TACAI:IAP:ENTER:H3G031G8\n", "UART ENTER")
        time.sleep(min(0.35, max(0.0, deadline - time.monotonic())))
        self.serial.reset_input_buffer()
        self._rx_buffer.clear()
        last_error = None
        while time.monotonic() < deadline:
            try:
                payload = self.command(CMD_HELLO, timeout=min(1.0, deadline - time.monotonic()))
                if len(payload) != 16:
                    raise IapError(f"Bootloader HELLO 长度错误：期望 16 字节，实际 {len(payload)}")
                protocol, app_start, app_max, state = struct.unpack("<IIII", payload)
                if protocol != 1 or app_start != APP_START:
                    raise IapError(f"Bootloader 型号或协议不匹配（protocol={protocol}, app_start=0x{app_start:08X}）")
                return {"protocol": protocol, "app_start": app_start, "app_max": app_max, "state": state}
            except IapError as error:
                last_error = error
                time.sleep(min(0.15, max(0.0, deadline - time.monotonic())))
        raise IapError(f"无法进入 H3 Bootloader；若旧应用无进入命令，请在同步期间重新上电。最后错误：{last_error}")

    def upgrade(self, image: FirmwareImage) -> None:
        model = image.model.encode("ascii").ljust(12, b"\0")
        token = image.image_crc32 ^ len(image.payload) ^ image.app_start ^ CONFIRM_XOR
        manifest = struct.pack(
            "<12sIIIII", model, image.app_start, len(image.payload),
            image.image_crc32, image.app_version, token,
        )
        self._upgrade_total = len(image.payload)
        self.progress(0, len(image.payload), "BEGIN：校验清单并擦除应用；本次擦写不自动重启")
        try:
            self.command(CMD_BEGIN, manifest, timeout=12.0)
        except Exception as error:
            raise IapError(f"BEGIN（清单/擦除）未确认：{error}；本次升级已停止，未自动重新擦除") from error
        padded = image.payload + b"\xFF" * ((-len(image.payload)) % 8)
        for offset in range(0, len(padded), 512):
            block = padded[offset : offset + 512]
            try:
                response = self.command(CMD_DATA, struct.pack("<I", offset) + block, timeout=4.0)
                if len(response) != 4:
                    raise IapError(f"DATA 确认长度错误：期望 4 字节，实际 {len(response)}")
                accepted = struct.unpack("<I", response)[0]
                if accepted != offset + len(block):
                    raise IapError(f"Bootloader 写入偏移确认不一致：期望 {offset + len(block)}，实际 {accepted}")
            except Exception as error:
                raise IapError(f"DATA 写入未确认（offset=0x{offset:08X}, length={len(block)}）：{error}；本次升级已停止，未自动重写") from error
            self.progress(min(accepted, len(image.payload)), len(image.payload),
                          f"DATA：已确认 {min(accepted, len(image.payload))}/{len(image.payload)} 字节；待整镜像校验")
        self.progress(len(image.payload), len(image.payload), "END：写入已达 100%，正在校验整镜像 CRC32 与向量表")
        try:
            self.command(CMD_END, timeout=8.0)
        except Exception as error:
            raise IapError(f"END（整镜像校验）未确认：{error}；本次升级已停止，未自动重新擦除") from error
        self.progress(len(image.payload), len(image.payload), "END 已确认：整镜像 CRC32 与向量表校验通过；等待 BOOT 启动确认")

    def boot(self) -> None:
        total = getattr(self, "_upgrade_total", 0)
        self.progress(total, total, "BOOT：镜像已校验，正在请求启动应用")
        try:
            self.command(CMD_BOOT, timeout=2.0)
        except Exception as error:
            raise IapError(f"BOOT 启动确认未收到：{error}；若 END 已确认，镜像校验已完成；未自动重新升级") from error
        self.progress(total, total, "BOOT ACK 已收到：设备已确认启动请求")


def load_image(path: str | Path) -> FirmwareImage:
    return FirmwareImage.load(path)
