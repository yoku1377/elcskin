"""STM32G03/G04 factory-ROM UART programmer used for first deployment.

The implementation follows ST's USART bootloader protocol (AN3155).  It is
deliberately limited to internal flash on the 64 KiB STM32G031 used by TacAI,
so a wrong HEX file is rejected before any erase command is sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
import time
from typing import Callable, Iterable


ACK = 0x79
NACK = 0x1F

CMD_GET = 0x00
CMD_GET_ID = 0x02
CMD_READ_MEMORY = 0x11
CMD_GO = 0x21
CMD_WRITE_MEMORY = 0x31
CMD_ERASE = 0x43
CMD_EXTENDED_ERASE = 0x44

STM32G03_G04_PID = 0x0466
FLASH_START = 0x08000000
FLASH_END = 0x08010000
SRAM_START = 0x20000000
SRAM_END = 0x20002000


class RomBootError(RuntimeError):
    """Raised when the target rejects or times out during ROM programming."""


@dataclass(frozen=True)
class ImageSegment:
    address: int
    data: bytes


@dataclass(frozen=True)
class IntelHexImage:
    path: Path
    segments: tuple[ImageSegment, ...]
    byte_count: int
    minimum_address: int
    maximum_address: int
    initial_sp: int
    reset_handler: int


def _hex_record(line: str, line_number: int) -> bytes:
    text = line.strip()
    if not text:
        return b""
    if not text.startswith(":"):
        raise ValueError(f"HEX 第 {line_number} 行缺少冒号")
    try:
        record = bytes.fromhex(text[1:])
    except ValueError as error:
        raise ValueError(f"HEX 第 {line_number} 行包含非法十六进制字符") from error
    if len(record) < 5 or len(record) != record[0] + 5:
        raise ValueError(f"HEX 第 {line_number} 行长度不正确")
    if sum(record) & 0xFF:
        raise ValueError(f"HEX 第 {line_number} 行校验和错误")
    return record


def parse_intel_hex(path: str | Path) -> IntelHexImage:
    """Parse and strictly validate a full STM32G031 internal-flash HEX file."""

    source = Path(path)
    memory: dict[int, int] = {}
    base = 0
    eof_seen = False

    with source.open("r", encoding="ascii") as stream:
        for line_number, line in enumerate(stream, 1):
            record = _hex_record(line, line_number)
            if not record:
                continue
            count = record[0]
            offset = (record[1] << 8) | record[2]
            record_type = record[3]
            payload = record[4 : 4 + count]

            if eof_seen:
                raise ValueError(f"HEX 第 {line_number} 行位于结束记录之后")
            if record_type == 0x00:
                absolute = base + offset
                for index, value in enumerate(payload):
                    address = absolute + index
                    previous = memory.get(address)
                    if previous is not None and previous != value:
                        raise ValueError(f"HEX 地址 0x{address:08X} 存在冲突数据")
                    memory[address] = value
            elif record_type == 0x01:
                if count != 0:
                    raise ValueError(f"HEX 第 {line_number} 行结束记录不正确")
                eof_seen = True
            elif record_type == 0x02:
                if count != 2:
                    raise ValueError(f"HEX 第 {line_number} 行段地址记录不正确")
                base = int.from_bytes(payload, "big") << 4
            elif record_type == 0x04:
                if count != 2:
                    raise ValueError(f"HEX 第 {line_number} 行线性地址记录不正确")
                base = int.from_bytes(payload, "big") << 16
            elif record_type in (0x03, 0x05):
                # Start-address records carry metadata only; programming data is
                # still taken exclusively from type-00 records.
                continue
            else:
                raise ValueError(f"HEX 第 {line_number} 行记录类型 0x{record_type:02X} 不受支持")

    if not eof_seen:
        raise ValueError("HEX 缺少结束记录")
    if not memory:
        raise ValueError("HEX 中没有可烧录数据")

    minimum = min(memory)
    maximum = max(memory)
    if minimum != FLASH_START:
        raise ValueError(
            f"这不是 NO8 完整固件：首地址为 0x{minimum:08X}，必须为 0x{FLASH_START:08X}"
        )
    if maximum >= FLASH_END:
        raise ValueError(
            f"HEX 超出 STM32G031 64 KiB 内部 Flash：末地址 0x{maximum:08X}"
        )

    vector = bytes(memory.get(FLASH_START + index, 0xFF) for index in range(8))
    initial_sp, reset_handler = struct.unpack("<II", vector)
    if not SRAM_START <= initial_sp <= SRAM_END:
        raise ValueError(f"HEX 初始栈指针异常：0x{initial_sp:08X}")
    if not (reset_handler & 1) or not FLASH_START <= (reset_handler & ~1) < FLASH_END:
        raise ValueError(f"HEX 复位入口异常：0x{reset_handler:08X}")

    segments: list[ImageSegment] = []
    ordered = sorted(memory)
    start = ordered[0]
    previous = start
    data = bytearray([memory[start]])
    for address in ordered[1:]:
        if address == previous + 1:
            data.append(memory[address])
        else:
            segments.append(ImageSegment(start, bytes(data)))
            start = address
            data = bytearray([memory[address]])
        previous = address
    segments.append(ImageSegment(start, bytes(data)))

    # STM32G0 flash is programmed in 64-bit double words.  Keeping the first
    # deployment image contiguous and 8-byte aligned prevents a ROM revision
    # from ACKing an unaligned write without actually changing flash.
    if len(segments) != 1:
        raise ValueError("NO8 完整 HEX 必须是连续镜像，不能包含未定义的数据空洞")
    if segments[0].address & 0x07 or len(segments[0].data) & 0x07:
        raise ValueError("NO8 HEX 的地址和长度必须按 STM32G0 的 8 字节 Flash 粒度对齐")

    return IntelHexImage(
        path=source,
        segments=tuple(segments),
        byte_count=len(memory),
        minimum_address=minimum,
        maximum_address=maximum,
        initial_sp=initial_sp,
        reset_handler=reset_handler,
    )


def _xor(data: Iterable[int]) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


class Stm32RomBootloader:
    """Minimal, strict STM32 USART-ROM protocol client."""

    def __init__(self, uart, progress: Callable[[int, int, str], None] | None = None):
        self.uart = uart
        self.progress = progress or (lambda _done, _total, _text: None)
        self.commands: set[int] = set()
        self.version = 0
        self.pid = 0

    def _read_exact(self, count: int, context: str) -> bytes:
        deadline = time.monotonic() + max(float(getattr(self.uart, "timeout", 1.0) or 1.0), 0.1)
        result = bytearray()
        while len(result) < count and time.monotonic() < deadline:
            chunk = self.uart.read(count - len(result))
            if chunk:
                result.extend(chunk)
        if len(result) != count:
            raise RomBootError(f"{context}超时：需要 {count} 字节，仅收到 {len(result)} 字节")
        return bytes(result)

    def _expect_ack(self, context: str) -> None:
        reply = self._read_exact(1, context)[0]
        if reply == NACK:
            raise RomBootError(f"{context}被芯片拒绝（NACK）")
        if reply != ACK:
            raise RomBootError(f"{context}收到异常响应 0x{reply:02X}，请确认已进入 BOOT 模式")

    def _send_command(self, command: int, context: str) -> None:
        self.uart.write(bytes((command, command ^ 0xFF)))
        self._expect_ack(context)

    def synchronize(self, attempts: int = 4) -> None:
        self.uart.reset_input_buffer()
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            self.progress(0, 1, f"正在同步 STM32 ROM Bootloader（{attempt}/{attempts}）")
            self.uart.write(b"\x7F")
            try:
                self._expect_ack("Bootloader 同步")
                return
            except RomBootError as error:
                last_error = error
                self.uart.reset_input_buffer()
        raise RomBootError(
            "无法同步 STM32 ROM Bootloader。请确认使用 USART2/COM4，"
            "将 BOOT0 拉高后重新上电或复位，再点击烧录。"
        ) from last_error

    def identify(self) -> tuple[int, int, set[int]]:
        self._send_command(CMD_GET, "读取 Bootloader 命令表")
        count_minus_one = self._read_exact(1, "读取命令表长度")[0]
        response = self._read_exact(count_minus_one + 1, "读取命令表")
        self._expect_ack("读取命令表结束")
        if not response:
            raise RomBootError("Bootloader 返回了空命令表")
        self.version = response[0]
        self.commands = set(response[1:])

        self._send_command(CMD_GET_ID, "读取芯片 ID")
        id_length_minus_one = self._read_exact(1, "读取芯片 ID 长度")[0]
        raw_id = self._read_exact(id_length_minus_one + 1, "读取芯片 ID")
        self._expect_ack("读取芯片 ID 结束")
        self.pid = int.from_bytes(raw_id, "big")
        return self.version, self.pid, self.commands

    def mass_erase(self) -> None:
        previous_timeout = self.uart.timeout
        self.uart.timeout = 25.0
        try:
            if CMD_EXTENDED_ERASE in self.commands:
                self._send_command(CMD_EXTENDED_ERASE, "启动扩展擦除")
                self.uart.write(b"\xFF\xFF\x00")
                self._expect_ack("全片擦除")
            elif CMD_ERASE in self.commands:
                self._send_command(CMD_ERASE, "启动擦除")
                self.uart.write(b"\xFF\x00")
                self._expect_ack("全片擦除")
            else:
                raise RomBootError("目标 Bootloader 不支持 Flash 擦除命令")
        finally:
            self.uart.timeout = previous_timeout

    def write_memory(self, address: int, data: bytes) -> None:
        if not 1 <= len(data) <= 256:
            raise ValueError("单次写入长度必须为 1–256 字节")
        if address & 0x07 or len(data) & 0x07:
            raise ValueError("STM32G0 Flash 写入地址和长度必须按 8 字节对齐")
        self._send_command(CMD_WRITE_MEMORY, f"写入 0x{address:08X}")
        address_bytes = address.to_bytes(4, "big")
        self.uart.write(address_bytes + bytes((_xor(address_bytes),)))
        self._expect_ack(f"确认写入地址 0x{address:08X}")
        size = len(data) - 1
        packet = bytes((size,)) + data
        self.uart.write(packet + bytes((_xor(packet),)))
        self._expect_ack(f"写入数据 0x{address:08X}")

    def read_memory(self, address: int, length: int) -> bytes:
        if not 1 <= length <= 256:
            raise ValueError("单次读取长度必须为 1–256 字节")
        self._send_command(CMD_READ_MEMORY, f"校验读取 0x{address:08X}")
        address_bytes = address.to_bytes(4, "big")
        self.uart.write(address_bytes + bytes((_xor(address_bytes),)))
        self._expect_ack(f"确认校验地址 0x{address:08X}")
        size = length - 1
        self.uart.write(bytes((size, size ^ 0xFF)))
        self._expect_ack(f"确认校验长度 0x{address:08X}")
        return self._read_exact(length, f"回读 0x{address:08X}")

    @staticmethod
    def _chunks(image: IntelHexImage, chunk_size: int = 256):
        for segment in image.segments:
            offset = 0
            while offset < len(segment.data):
                data = segment.data[offset : offset + chunk_size]
                yield segment.address + offset, data
                offset += len(data)

    def program_and_verify(self, image: IntelHexImage) -> None:
        if self.pid != STM32G03_G04_PID:
            raise RomBootError(
                f"芯片型号不匹配：读取到 PID 0x{self.pid:04X}，"
                f"NO8 仅允许 STM32G03/G04（PID 0x{STM32G03_G04_PID:04X}）"
            )
        required = {CMD_WRITE_MEMORY, CMD_READ_MEMORY}
        missing = required - self.commands
        if missing:
            names = ", ".join(f"0x{command:02X}" for command in sorted(missing))
            raise RomBootError(f"目标 Bootloader 缺少必要命令：{names}")

        total = image.byte_count * 2
        done = 0
        self.progress(done, total, "芯片识别成功，正在全片擦除")
        self.mass_erase()

        for address, data in self._chunks(image):
            self.write_memory(address, data)
            done += len(data)
            self.progress(done, total, f"正在写入 0x{address:08X}")

        for address, expected in self._chunks(image):
            actual = self.read_memory(address, len(expected))
            if actual != expected:
                mismatch = next(i for i, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1])
                failed_address = address + mismatch
                raise RomBootError(
                    f"回读校验失败：地址 0x{failed_address:08X}，"
                    f"写入 0x{expected[mismatch]:02X}，读回 0x{actual[mismatch]:02X}"
                )
            done += len(expected)
            self.progress(done, total, f"正在校验 0x{address:08X}")

        self.progress(total, total, "写入及逐字节回读校验完成")

