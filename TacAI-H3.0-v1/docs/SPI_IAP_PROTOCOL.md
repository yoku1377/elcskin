# H3 CH347F SPI application and IAP protocol

The SPI candidate reuses the UART `TAI3` binary frame and `.tacai` image
container. It does not implement the earlier V0.1 `A5/5A` register draft;
that draft's RAW scaling and control map remain unfrozen. The preparatory
`SPI_预联调_20261014/host` CRC16 tools belong to that earlier draft and must
not be used against this IAP/snapshot firmware.

Each complete request is one CS-low transaction. After CS rises, the master
polls using a separate CS-low transaction containing exactly 536 `FF` bytes.
Every CS-high gap is at least 2 ms in the CH347 backend. Initial SCK is
468750 Hz, Mode 0, 8-bit, MSB first. The first response poll may be all `FF`
while the inactive TX buffer is committed; keep polling without replaying
the request. A framed response is followed by `FF` padding. A complete last
response remains readable until a new request. A bad response CRC is a
reported error; Flash commands are never automatically retransmitted.

## Frame layout

The 12-byte header is `<IBBHHH` little endian: magic `0x33494154` (`TAI3`),
command, flags/status, sequence, payload length, and zero reserved word.
Payload length is at most 520. The final four bytes are CRC32 of header and
payload, little endian. Responses set command bit 7 and echo sequence.
UART IAP commands 01 HELLO, 02 BEGIN, 03 DATA, 04 END, 05 BOOT, 06 ABORT and
all manifest/Flash validation rules are unchanged. Bootloader sessions lock
to one transport. A malformed BEGIN does not replace an active manifest.

Application commands are `0x20` snapshot (empty request payload) and `0x21`
enter SPI IAP (exact payload ASCII `H3G031G8`). The entry ACK echoes that
model. Reset is deferred until its full ACK is clocked, including a response
read after the send timeout. The SPI entry SRAM magic is `0x33535049`; the
old UART magic `0x33415049` remains supported. Entry and BOOT are never
triggered by an ordinary sensor poll.

## Snapshot payload

| Offset | Type | Meaning |
| --- | --- | --- |
| 0 | uint16 | snapshot version, 1 |
| 2 | uint16 | bit 0 valid, bit 1 baseline ready, bit 4 sensor fault; others reserved |
| 4 | uint32 | device milliseconds |
| 8 | uint32 | successful sample counter |
| 12 | uint32 | accumulated sensor errors |
| 16 | uint32 | baseline epoch |
| 20 | uint8 | valid mask, bits B00,B01,B10,B11,B20,B21,B30,B31 |
| 21 | 3 bytes | zero reserved |
| 24 | 32 bytes | zero-terminated ASCII firmware ID, zero padded |
| 56 | 24 float32 | baseline-subtracted RAW, column/row/XYZ order |
| 152 | 3 float32 | legacy firmware XYZ diagnostic values |

The snapshot is 164 bytes, little endian. The host emits DIAG then matching
RAW/FORCE into the existing quality and pairing path. Invalid samples emit
diagnostics only and cannot relabel an older force as fresh. RAW retains
float32 precision; the PC's calibrated model and coefficient files are not
changed by the SPI integration.

## Installation and verification

Existing app-only UART IAP cannot replace the resident bootloader. First
install the dual UART/SPI Bootloader plus SPI application through SWD using
target-page erase and preserve the configuration at `0x0800F800`. Subsequent
SPI IAP changes application pages only. Power loss after erase requires
resending the full image; there is no A/B rollback.

See `firmware/shared/SPI_LINK_VALIDATION.md` for the transport contract and
physical timing checklist. Production C tests simulate register/HAL/service
boundaries; they do not prove DMA, FIFO, electrical timing or board wiring.
