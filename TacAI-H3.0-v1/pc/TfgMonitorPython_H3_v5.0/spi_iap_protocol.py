"""H3 SPI application snapshots and IAP using the existing TAI3 image protocol.

Every request and every 536-byte response poll has its own uninterrupted CS.
Pending/all-FF, stale or corrupt replies cause response polls only within the
original deadline, never retransmission of a flash-writing request. Read-only
snapshots may issue a fresh request after a persistently repeated old snapshot
ACK; this bounded recovery never applies to the upgrade commands. Tests use
injected exchanges, not a physical board.

Before the next snapshot request, one additional dummy transaction drains the
last accepted snapshot ACK. A valid MISO frame does not prove that the slave
received its all-FF MOSI acknowledgement without an RX overrun. Without this
drain the slave may ignore the next request while still awaiting that ACK.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import struct
import time
import zlib

from iap_protocol import (
    APP_START, CMD_BOOT, CMD_HELLO, FRAME_MAGIC, HEADER,
    IapClient, IapError, STATUS_NAMES, encode_frame, load_image,
)

CMD_SNAPSHOT = 0x20
CMD_APP_ENTER = 0x21
APP_ENTER_TOKEN = b"H3G031G8"
SPI_FRAME_BYTES = 536
SPI_MAX_PAYLOAD = 520
SNAPSHOT_HEADER = struct.Struct("<HHIIIIB3x")
SNAPSHOT_BYTES = 164
SNAPSHOT_MAX_ATTEMPTS = 3
SNAPSHOT_STALE_MIN_POLLS = 3
SNAPSHOT_STALE_MIN_SECONDS = 0.18


class SnapshotStaleResponse(IapError):
    """A read-only request is stuck polling a previous snapshot's valid ACK."""

    def __init__(self, sequence, old_sequence, polls, stale_polls, elapsed):
        self.sequence = sequence
        self.old_sequence = old_sequence
        self.polls = polls
        self.stale_polls = stale_polls
        self.elapsed = elapsed
        super().__init__(
            f"SPI 只读快照持续返回旧响应（cmd=0x20, seq={sequence}, "
            f"旧 seq={old_sequence}, 轮询={polls}, 连续旧帧={stale_polls}, "
            f"等待={elapsed:.3f}s）")


@dataclass(frozen=True)
class Snapshot:
    version: int
    status: int
    device_ms: int
    frame_counter: int
    error_count: int
    baseline_epoch: int
    valid_mask: int
    firmware_id: str
    raw: tuple[float, ...]
    force: tuple[float, ...]


def decode_snapshot(payload: bytes) -> Snapshot:
    if len(payload) != SNAPSHOT_BYTES:
        raise IapError(f"SPI snapshot 长度错误：期望 164 字节，实际 {len(payload)}")
    version, status, device_ms, counter, errors, epoch, mask = SNAPSHOT_HEADER.unpack_from(payload)
    if version != 1 or payload[21:24] != b"\0\0\0":
        raise IapError("SPI snapshot 版本或保留字段错误")
    name_field = payload[24:56]
    name, separator, padding = name_field.partition(b"\0")
    if not name or not separator or any(padding):
        raise IapError("SPI snapshot 固件 ID 必须为非空、以零结尾的 ASCII")
    try:
        firmware_id = name.decode("ascii")
    except UnicodeDecodeError as error:
        raise IapError("SPI snapshot 固件 ID 不是 ASCII") from error
    if any(not (character.isalnum() or character in "_.-") for character in firmware_id):
        raise IapError("SPI snapshot 固件 ID 含非法字符")
    raw = struct.unpack_from("<24f", payload, 56)
    force = struct.unpack_from("<3f", payload, 152)
    if not all(math.isfinite(value) for value in (*raw, *force)):
        raise IapError("SPI snapshot 含 NaN 或无穷浮点值")
    return Snapshot(version, status, device_ms, counter, errors, epoch, mask, firmware_id, raw, force)


def _decode_response(raw: bytes):
    if len(raw) != SPI_FRAME_BYTES:
        raise IapError(f"SPI 响应事务必须为 536 字节，实际 {len(raw)}")
    if raw == b"\xFF" * SPI_FRAME_BYTES:
        return None
    magic, command, status, sequence, length, reserved = HEADER.unpack_from(raw)
    if magic != FRAME_MAGIC or reserved != 0 or length > SPI_MAX_PAYLOAD:
        raise IapError("SPI 返回非法 TAI3 帧头")
    end = HEADER.size + length
    payload = raw[HEADER.size:end]
    wire_crc = struct.unpack_from("<I", raw, end)[0]
    calc_crc = zlib.crc32(payload, zlib.crc32(raw[:HEADER.size])) & 0xFFFFFFFF
    if wire_crc != calc_crc:
        raise IapError("SPI 响应 CRC32 错误")
    if any(value != 0xFF for value in raw[end + 4:]):
        raise IapError("SPI 响应帧后的填充必须为 FF")
    return command, status, sequence, payload


class SPIIapClient:
    """Uses CH347Spi.exchange or an injected exchange without a COM abstraction."""

    def __init__(self, spi, progress=None, wait_between: float = 0.002, trace=None):
        self.exchange = getattr(spi, "exchange", spi)
        if not callable(self.exchange):
            raise TypeError("SPI backend must expose exchange(bytes) -> bytes")
        if isinstance(wait_between, bool) or not isinstance(wait_between, (float, int)):
            raise ValueError("wait_between must be finite nonnegative seconds")
        if not math.isfinite(wait_between) or wait_between < 0:
            raise ValueError("wait_between must be finite nonnegative seconds")
        self.wait_between = float(wait_between)
        self.sequence = 1
        self.progress = progress or (lambda _done, _total, _message: None)
        if trace is not None and not callable(trace):
            raise TypeError("trace must be callable or None")
        self.trace = trace
        self._pending_snapshot_ack = None

    def _trace(self, event: str, **details) -> None:
        """Observational logging must never change whether a request is sent."""
        if self.trace is not None:
            try:
                self.trace({"event": event, **details})
            except Exception:
                pass

    def _exchange(self, transmitted: bytes) -> bytes:
        try:
            received = self.exchange(transmitted)
        except IapError:
            raise
        except Exception as error:
            raise IapError(f"SPI USB 事务失败：{error}") from error
        if not isinstance(received, (bytes, bytearray, memoryview)):
            raise IapError("SPI backend 必须返回 bytes")
        received = bytes(received)
        if len(received) != len(transmitted):
            raise IapError("SPI backend 返回长度与完整 CS 事务不一致")
        return received

    def command(self, command: int, payload: bytes = b"", timeout: float = 3.0) -> bytes:
        # Upgrade requests must keep their single-send contract even if the
        # peer keeps presenting an older, otherwise valid response.
        return self._command(command, payload, timeout)

    def _command(self, command: int, payload: bytes = b"", timeout: float = 3.0,
                 *, snapshot_stale_recovery: bool = False) -> bytes:
        if isinstance(command, bool) or not isinstance(command, int) or not 0 <= command <= 0x7F:
            raise IapError("SPI 请求命令必须为 0..127")
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise IapError("SPI 请求 payload 必须为 bytes")
        payload = bytes(payload)
        if len(payload) > SPI_MAX_PAYLOAD:
            raise IapError("SPI 请求 payload 超过 520 字节")
        if not math.isfinite(timeout) or timeout <= 0:
            raise IapError("SPI 响应 timeout 必须为正数")
        sequence = self.sequence
        self.sequence = (self.sequence + 1) & 0xFFFF
        request = encode_frame(command, sequence, payload)
        started = time.monotonic()
        deadline = started + timeout
        context = {"command": command, "sequence": sequence}
        self._trace("request", **context, payload_length=len(payload),
                    request_length=len(request), timeout=timeout)
        try:
            self._exchange(request)
        except IapError as error:
            self._trace("transport_error", **context, phase="request", error=str(error))
            raise
        polls = idle_polls = stale_polls = corrupt_polls = 0
        last_stale = None
        repeated_snapshot_sequence = None
        repeated_snapshot_polls = 0
        last_corrupt_error = last_corrupt_prefix = None
        while time.monotonic() < deadline:
            if self.wait_between:
                time.sleep(min(self.wait_between, max(0.0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                break
            # A complete USB transaction is required. Short or failed transfers
            # are transport failures, not a frame that can safely be polled past.
            try:
                raw = self._exchange(b"\xFF" * SPI_FRAME_BYTES)
            except IapError as error:
                self._trace("transport_error", **context, phase="poll", poll=polls + 1,
                            error=str(error))
                raise
            polls += 1
            try:
                response = _decode_response(raw)
            except IapError as error:
                corrupt_polls += 1
                repeated_snapshot_polls = 0
                last_corrupt_error = str(error)
                last_corrupt_prefix = raw[:32].hex(" ").upper()
                # Keep a useful first example without a log entry for every
                # pending, stale or corrupt poll. The timeout retains the last.
                if corrupt_polls == 1:
                    self._trace("poll_corrupt", **context, poll=polls,
                                error=last_corrupt_error, raw_prefix_hex=last_corrupt_prefix)
                continue
            if response is None:
                idle_polls += 1
                repeated_snapshot_polls = 0
                continue
            response_command, status, response_sequence, answer = response
            if response_command != (command | 0x80) or response_sequence != sequence:
                stale_polls += 1
                last_stale = (response_command, response_sequence)
                if (snapshot_stale_recovery and command == CMD_SNAPSHOT
                        and response_command == (CMD_SNAPSHOT | 0x80)
                        and response_sequence != sequence and status == 0):
                    if repeated_snapshot_sequence == response_sequence:
                        repeated_snapshot_polls += 1
                    else:
                        repeated_snapshot_sequence = response_sequence
                        repeated_snapshot_polls = 1
                    elapsed = time.monotonic() - started
                    # An app acquisition cycle takes about 103 ms. A handful
                    # of old replies while it builds the new one is normal.
                    # Require both sustained time and repeated valid old ACKs.
                    if (repeated_snapshot_polls >= SNAPSHOT_STALE_MIN_POLLS
                            and elapsed >= SNAPSHOT_STALE_MIN_SECONDS):
                        raise SnapshotStaleResponse(sequence, response_sequence,
                                                    polls, repeated_snapshot_polls, elapsed)
                else:
                    repeated_snapshot_polls = 0
                continue
            if status != 0:
                self._trace("response", **context, poll=polls, status=status,
                            payload_length=len(answer), idle_polls=idle_polls,
                            stale_polls=stale_polls, corrupt_polls=corrupt_polls)
                raise IapError(f"SPI 设备拒绝命令：{STATUS_NAMES.get(status, status)}")
            self._trace("response", **context, poll=polls, status=0,
                        payload_length=len(answer), idle_polls=idle_polls,
                        stale_polls=stale_polls, corrupt_polls=corrupt_polls)
            return answer
        detail = (f"cmd=0x{command:02X}, seq={sequence}, 轮询={polls}, "
                  f"全 FF={idle_polls}, 旧帧/其他命令={stale_polls}, 坏帧={corrupt_polls}")
        if last_stale is not None:
            detail += f", 最后旧帧 cmd=0x{last_stale[0]:02X}, seq={last_stale[1]}"
        if last_corrupt_error is not None:
            detail += f", 最后坏帧={last_corrupt_error}, 前32字节={last_corrupt_prefix}"
        self._trace("timeout", **context, polls=polls, idle_polls=idle_polls,
                    stale_polls=stale_polls, corrupt_polls=corrupt_polls,
                    last_corrupt_error=last_corrupt_error,
                    raw_prefix_hex=last_corrupt_prefix)
        raise IapError(f"等待 SPI 响应超时（{detail}）；请求没有自动重发")

    def snapshot(self, timeout: float = 1.0) -> Snapshot:
        if not math.isfinite(timeout) or timeout <= 0:
            raise IapError("SPI 响应 timeout 必须为正数")
        deadline = time.monotonic() + timeout
        if self._pending_snapshot_ack is not None:
            sequence = self._pending_snapshot_ack
            # The previous snapshot was already fully decoded and delivered.
            # This extra CS transaction only acknowledges that old response;
            # never expose its bytes as a new sample or replay a request.
            try:
                self._exchange(b"\xFF" * SPI_FRAME_BYTES)
            except IapError as error:
                self._trace("transport_error", command=CMD_SNAPSHOT,
                            sequence=sequence, phase="snapshot_ack_guard", error=str(error))
                raise
            self._pending_snapshot_ack = None
            self._trace("snapshot_ack_guard", command=CMD_SNAPSHOT,
                        sequence=sequence, length=SPI_FRAME_BYTES)
        for attempt in range(1, SNAPSHOT_MAX_ATTEMPTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IapError("SPI 只读快照恢复超时；已达到本次总等待期限")
            try:
                frame = decode_snapshot(self._command(
                    CMD_SNAPSHOT, timeout=remaining,
                    snapshot_stale_recovery=attempt < SNAPSHOT_MAX_ATTEMPTS))
            except SnapshotStaleResponse as error:
                # This is a new read-only request, with a fresh sequence. Do
                # not clear USB buffers, reopen the device, or replay writes.
                if time.monotonic() >= deadline:
                    raise
                self._trace("snapshot_retry", command=CMD_SNAPSHOT,
                            sequence=error.sequence, old_sequence=error.old_sequence,
                            next_sequence=self.sequence, attempt=attempt,
                            next_attempt=attempt + 1, polls=error.polls,
                            stale_polls=error.stale_polls, elapsed=error.elapsed,
                            reason="repeated_old_snapshot_ack")
                continue
            if attempt > 1:
                self._trace("snapshot_recovered", command=CMD_SNAPSHOT,
                            sequence=(self.sequence - 1) & 0xFFFF,
                            attempts=attempt, device_ms=frame.device_ms,
                            frame_counter=frame.frame_counter)
            self._pending_snapshot_ack = (self.sequence - 1) & 0xFFFF
            return frame
        raise IapError("SPI 只读快照恢复失败；已达到本次请求次数上限")

    def enter(self, sync_timeout: float = 12.0) -> dict:
        if not math.isfinite(sync_timeout) or sync_timeout <= 0:
            raise IapError("SPI 同步 timeout 必须为正数")
        deadline = time.monotonic() + sync_timeout
        last_error = None
        try:
            answer = self.command(CMD_APP_ENTER, APP_ENTER_TOKEN, timeout=min(1.0, sync_timeout))
            if answer != APP_ENTER_TOKEN:
                raise IapError("SPI 应用进入升级 ACK 型号错误")
            time.sleep(min(0.35, max(0.0, deadline - time.monotonic())))
        except IapError as error:
            # A board already in the bootloader cannot ACK application cmd21.
            # HELLO is safe to repeat; BEGIN and DATA remain single requests.
            last_error = error
        while time.monotonic() < deadline:
            try:
                answer = self.command(CMD_HELLO, timeout=min(1.0, deadline - time.monotonic()))
                if len(answer) != 16:
                    raise IapError("SPI Bootloader HELLO 长度错误")
                protocol, app_start, app_max, state = struct.unpack("<IIII", answer)
                if protocol != 1 or app_start != APP_START:
                    raise IapError("SPI Bootloader 型号或协议不匹配")
                return {"protocol": protocol, "app_start": app_start, "app_max": app_max, "state": state}
            except IapError as error:
                last_error = error
                time.sleep(min(0.15, max(0.0, deadline - time.monotonic())))
        raise IapError(f"无法进入 H3 SPI Bootloader；请检查片选、接线或重新上电。最后错误：{last_error}")

    def upgrade(self, image) -> None:
        # The UART implementation's image/BEGIN/DATA/END algorithm uses only
        # command() and progress(); it does not use serial methods here.
        IapClient.upgrade(self, image)

    def boot(self) -> None:
        IapClient.boot(self)
