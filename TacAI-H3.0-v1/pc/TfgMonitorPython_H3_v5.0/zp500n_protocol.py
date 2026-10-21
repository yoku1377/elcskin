"""ZP-500N N-mode streaming profile confirmed by the 2026-10-20 raw capture.

FA selects a repeating36-byte descriptor stream;25 selects measurement output
after finishing the current descriptor. Initialization must not wait for idle.
Three identical, structurally valid descriptors establish a unique byte phase
and confirm unit byte10 as3/6 (N). Only then is25 queued once.

The observed measurement profile is six ASCII bytes: four digits, '.', one
digit,0..500N. Read chunks can contain arbitrary fractions/multiples of frames.
Negative formats, other precisions and non-N unit wire formats are unverified
and deliberately fail closed. Field evidence contains constant16.1N only;
zero/dynamic offline examples validate parsing arithmetic, not metrology.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import re


BAUD_RATE = 2400
DATA_BITS = 8
PARITY = "N"
STOP_BITS = 1
HANDSHAKE = "None"
IDENTITY_QUERY = b"\xfa"
START_NUMERIC_STREAM = b"\x25"
INITIALIZATION_TIMEOUT_S = 3.0
MAX_RECEIVE_BUFFER_BYTES = 4096
DESCRIPTOR_BYTES = 36
DESCRIPTOR_REPEATS = 3
NUMERIC_FIELD_BYTES = 6
DESCRIPTOR_DECIMAL_OFFSETS = (15, 21, 27, 34)
_NUMBER = re.compile(rb"[0-9]{4}\.[0-9]")


class ZPProtocolError(RuntimeError):
    """The confirmed streaming profile could not safely frame/validate data."""


@dataclass(frozen=True)
class ZPForceSample:
    raw: bytes
    value: float
    force_n: float
    unit: str
    scale_to_n: float
    host_received_at: float


class ZP500NProtocol:
    """Pure byte state machine; caller alone owns and writes the serial port.

    ``now`` is monotonic; ``received_at`` is the actual host receipt timestamp.
    Samples sharing a coalesced read retain the same timestamp. No source times
    are fabricated. The reader must suppress backlogged samples independently.
    ``advance`` checks deadlines only; neither idle nor read sizes frame data.
    """

    def __init__(self, *, initialization_timeout=INITIALIZATION_TIMEOUT_S):
        self.initialization_timeout = float(initialization_timeout)
        if not math.isfinite(self.initialization_timeout) or self.initialization_timeout <= 0:
            raise ValueError("Initialization timeout must be positive and finite")
        self.state = "NEW"
        self.metadata_raw = b""
        self.metadata_prefix_raw = b""
        self.metadata_received_at = None
        self.metadata_repeat_count = 0
        self.transition_raw = b""
        self.unit = None
        self.unit_scale_to_n = None
        self.last_error = None
        self.rejected_frames = 0
        self.last_rejected_raw = None
        self.last_rejected_truncated = False
        self._writes = deque()
        self._buffer = bytearray()
        self._started_at = self._last_now = self._buffer_received_at = None
        self._metadata_phase = 0
        self._stream_start_released = False

    @property
    def metadata_phase(self):
        return self._metadata_phase

    @property
    def pending_numeric_bytes(self):
        """The reader may leave backlog only after empty read at a frame boundary."""
        return len(self._buffer) if self.state == "STREAMING" else None

    def _fail(self, message, raw=None):
        if raw is not None:
            self.rejected_frames += 1
            raw = bytes(raw)
            self.last_rejected_raw = raw[:MAX_RECEIVE_BUFFER_BYTES]
            self.last_rejected_truncated = len(raw) > MAX_RECEIVE_BUFFER_BYTES
        self.state = "FAILED"
        self.last_error = message
        self._writes.clear()
        raise ZPProtocolError(message)

    def _clock(self, now):
        now = float(now)
        if not math.isfinite(now):
            self._fail("ZP-500N monotonic clock is not finite")
        if self._last_now is not None and now < self._last_now:
            self._fail("ZP-500N monotonic clock moved backwards")
        self._last_now = now
        return now

    def start(self, now):
        if self.state != "NEW":
            raise ZPProtocolError("ZP-500N session may only be started once")
        self._started_at = self._clock(now)
        self.state = "WAIT_METADATA"
        self._writes.append(IDENTITY_QUERY)

    def drain_writes(self):
        writes = list(self._writes)
        self._writes.clear()
        if START_NUMERIC_STREAM in writes:
            # The reader must successfully write this before its next read.
            # Short/failed writes terminate the reader; they are never retried.
            self._stream_start_released = True
        return writes

    def _validate_running(self, now):
        now = self._clock(now)
        if self.state == "NEW":
            raise ZPProtocolError("ZP-500N session has not started")
        if self.state == "FAILED":
            raise ZPProtocolError(self.last_error or "ZP-500N session failed")
        if self.state in ("WAIT_METADATA", "SWITCHING") and now-self._started_at >= self.initialization_timeout:
            self._fail("ZP-500N initialization timed out before aligned numeric output was confirmed")
        return now

    def advance(self, now):
        self._validate_running(now)
        return []

    @staticmethod
    def _descriptor_shape(frame):
        return (len(frame) == DESCRIPTOR_BYTES
                and all(byte == 46 if index in DESCRIPTOR_DECIMAL_OFFSETS else 48 <= byte <= 57
                        for index, byte in enumerate(frame)))

    def _find_metadata(self):
        data = bytes(self._buffer)
        candidates = {}
        span = DESCRIPTOR_BYTES*DESCRIPTOR_REPEATS
        for start in range(len(data)-span+1):
            frame = data[start:start+DESCRIPTOR_BYTES]
            if (self._descriptor_shape(frame)
                    and data[start:start+span] == frame*DESCRIPTOR_REPEATS):
                candidates.setdefault((start % DESCRIPTOR_BYTES, frame), start)
        if not candidates:
            return
        if len(candidates) != 1:
            self._fail("ZP-500N descriptor repetition has ambiguous byte phases", data)
        (_phase, frame), start = next(iter(candidates.items()))
        self.metadata_raw = frame
        self.metadata_prefix_raw = data[:start]
        self.metadata_received_at = self._buffer_received_at
        if frame[10] not in (ord("3"), ord("6")):
            self._fail("ZP-500N descriptor unit is unsupported; only verified N-mode codes3/6 are accepted", frame)
        stream = data[start:]
        expected = (frame*((len(stream)+DESCRIPTOR_BYTES-1)//DESCRIPTOR_BYTES))[:len(stream)]
        if stream != expected:
            self._fail("ZP-500N descriptor changed before stream selection; alignment is unconfirmed", stream)
        self.metadata_repeat_count = len(stream)//DESCRIPTOR_BYTES
        self._metadata_phase = len(stream) % DESCRIPTOR_BYTES
        self.unit, self.unit_scale_to_n = "N", 1.0
        self._buffer.clear()
        self.state = "SWITCHING"
        self._writes.append(START_NUMERIC_STREAM)

    def _drain_transition(self, length):
        raw = bytes(self._buffer[:length])
        if len(self.transition_raw)+len(raw) > MAX_RECEIVE_BUFFER_BYTES:
            self._fail("ZP-500N transition descriptor drain exceeded4096 bytes", raw)
        self.transition_raw += raw
        del self._buffer[:length]

    def _finish_transition(self):
        while self._buffer:
            if self._metadata_phase:
                length = min(len(self._buffer), DESCRIPTOR_BYTES-self._metadata_phase)
                expected = self.metadata_raw[self._metadata_phase:self._metadata_phase+length]
                if bytes(self._buffer[:length]) != expected:
                    self._fail("ZP-500N descriptor tail changed during stream selection", self._buffer)
                self._drain_transition(length)
                self._metadata_phase = (self._metadata_phase+length) % DESCRIPTOR_BYTES
                continue
            length = min(len(self._buffer), DESCRIPTOR_BYTES)
            if bytes(self._buffer[:length]) == self.metadata_raw[:length]:
                if length < DESCRIPTOR_BYTES:
                    # Numeric fields require '.' at offset4. Five matching
                    # descriptor digits prove this prefix is still metadata.
                    if length < 5:
                        return []
                    self._drain_transition(length)
                    self._metadata_phase = length
                    continue
                self._drain_transition(DESCRIPTOR_BYTES)
                continue
            if len(self._buffer) < NUMERIC_FIELD_BYTES:
                return []
            if not self._stream_start_released:
                self._fail("ZP-500N numeric output preceded release of the start-stream command", self._buffer)
            # Enter numeric mode only at the confirmed descriptor boundary.
            self.state = "STREAMING"
            return self._consume_numeric()
        return []

    def _consume_numeric(self):
        samples = []
        while len(self._buffer) >= NUMERIC_FIELD_BYTES:
            raw = bytes(self._buffer[:NUMERIC_FIELD_BYTES])
            if _NUMBER.fullmatch(raw) is None:
                self._fail("ZP-500N numeric field does not match verified6-byte N-mode format", self._buffer)
            value = float(raw.decode("ascii"))
            if not 0.0 <= value <= 500.0:
                self._fail("ZP-500N numeric field is outside the verified0..500N range", raw)
            del self._buffer[:NUMERIC_FIELD_BYTES]
            samples.append(ZPForceSample(raw, value, value, "N", 1.0, self._buffer_received_at))
        return samples

    def feed(self, data, now, received_at=None):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("ZP-500N receive data must be bytes")
        now = self._validate_running(now)
        data = bytes(data)
        if not data:
            return []
        received_at = now if received_at is None else float(received_at)
        if not math.isfinite(received_at):
            self._fail("ZP-500N host receipt timestamp is not finite")
        self._buffer_received_at = received_at
        if len(self._buffer)+len(data) > MAX_RECEIVE_BUFFER_BYTES:
            self._fail("ZP-500N receive buffer exceeded4096 bytes", bytes(self._buffer)+data[:MAX_RECEIVE_BUFFER_BYTES+1])
        self._buffer.extend(data)
        if self.state == "WAIT_METADATA":
            self._find_metadata()
            return []
        if self.state == "SWITCHING":
            return self._finish_transition()
        return self._consume_numeric()
