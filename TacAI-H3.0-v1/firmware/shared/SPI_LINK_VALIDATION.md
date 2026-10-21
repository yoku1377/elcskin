# H3 SPI2 transport bring-up boundary

The module is a STM32G031 SPI2 slave transport. It does not claim physical-board
validation. The confirmed design mapping is PA0 SCK AF0, PA3 MISO AF0, PA4 MOSI
AF1, PA8 NSS AF1, 3.3 V, Mode 0, MSB first, 8-bit data. USART2 and any external
UART transmitter on PA3 must be disconnected/disabled for this SPI build.

Start with SCK 468750 Hz and at least **1 ms of CS high between every request
and response transaction and between response polls**. These are conservative
software bring-up settings, to be checked with a logic analyser on the actual
16 MHz bootloader and 64 MHz application. They are not a certified maximum SCK
or a measured minimum gap. The driver does not promise service for arbitrarily
short CS-high intervals or a clock burst during GPIO/SPI initialization.

TAI3 requests are one CS transaction, with 12-byte little-endian header,
payload <=520 bytes, and CRC32 little endian. The maximum frame is 536 bytes.
Responses use a separate CS transaction with 536 MOSI FF bytes; the response is
padded with FF. A not-yet-prepared response is all FF. Responses are staged in
an inactive TX buffer and committed only at CS rising, so the first poll after
preparation may still return all FF. The host must keep polling responses and
must not resend a write merely because a response is not ready.

A complete response remains readable until the next TAI3 request. A short
response read does not finish `SPI_LinkSend`; a full read does. CRC-corrupted
responses can therefore be read again without repeating Flash commands.

DMA1 channel 1 receives 537 bytes (one extra byte detects overlength). Channel
2 transmits 536 bytes. Idle FF uses a constant source with memory increment
disabled. The transport has two 536-byte TX buffers and one 536-byte pending
request, for 2145 bytes of buffers. CS ISR only handles length, bounded copies,
DMA/FIFO reload and the prepared-buffer swap. Frame parsing, CRC checks and
Flash operations run in the main loop. SPI deinitialization clears its DMA,
EXTI and pending interrupt state before application entry.

Required physical checks:

- Confirm PB6/PB7 UART upgrade still works after first installing the new dual
  bootloader through SWD, using page erase that preserves configuration.
- Capture SCK/MOSI/MISO/CS during LINK/HELLO, maximum request, snapshot and IAP.
  Verify first response byte, exactly one CS per transaction, complete CRC and
  FF padding. Confirm the configured frequency and >=1 ms high interval.
- Poll while a response is being prepared and during application erase. Observe
  only idle FF or a complete correctly framed response within the valid timing
  contract; never interpret an all-FF poll as a Flash acknowledgement.
- Abort response reads at 1, 15 and `frame_length-1` bytes, then read all 536.
  Verify the same response is retained and the command executes once.
- Re-read a full response before sending a new request; verify identical bytes.
  Send 537-byte requests, wrong CRC, truncated headers and invalid manifests;
  verify no application erase/program occurs for these malformed requests.
- Intentionally reduce the CS-high interval below the contract. The operation
  must fail or resynchronize without accepting a partial Flash request or
  falsely consuming its ACK; restore 1 ms before resuming validation.
- Power-cut at metadata DOWN write, first/middle/final application page, and
  metadata VALID write. Confirm recovery accepts a complete image again on the
  first valid HELLO channel and never interleaves UART/SPI write sessions.
- Verify deferred SPI ENTER/BOOT resets occur after ACK consumption, including
  a host pause longer than the send timeout. Check stack/RAM margin under load.

First deployment changes the resident bootloader at 0x08000000. Existing
app-only UART IAP cannot update that bootloader partition. The application is
at 0x08002000, metadata at 0x0800F000 and device configuration at 0x0800F800;
mass erase does not preserve configuration.
