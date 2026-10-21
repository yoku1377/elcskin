# CH347F enumeration compatibility in DLL 1.20

The previous filter omitted the distributed DLL's `FuncType=3`, and expected
CH347F `ChipMode=4, IF=2` based on an older public header. This can discard a
working CH347F synchronous interface and leave the GUI selector empty.

The signed DLL with SHA256
`00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375`
contains a 14-entry descriptor table at RVA `0x159F0`, record size `0x65`.
Static conversion code at VA `0x40841D..0x40848F` copies these table fields
into `mDeviceInforS`. `CH347GetDeviceInfor` copies `0x221` (545) bytes to its
caller. No DLL execution or device access was needed to inspect these facts.

Relevant entries from the actual DLL:

| ID match | UsbClass | FuncType | IF | ChipMode | Function description |
| --- | --- | --- | --- | --- | --- |
| PID_55DE&MI_00 | 3 | 0 | 0 | 0 | CH347F.M0 VCP UART0 |
| PID_55DE&MI_02 | 3 | 0 | 2 | 0 | CH347F.M0 VCP UART1 |
| PID_55DE&MI_04 | 0 | 3 | 4 | 0 | CH347F.M0 SPI&I2C&JTAG&SWD |
| PID_55E5&MI_01 | 2 | 3 | 1 | 1 | CH347F.M1 HID SPI&I2C |
| PID_55DD&MI_02 | 0 | 1 | 2 | 3 | CH347T.M3 JTAG&I2C |

The last row is another reason that `FuncType=1` alone is insufficient:
CH347T Mode 3 is JTAG/I2C. The filter now accepts the combined F type 3,
retains the legacy F type-2/mode-4 representation, and excludes UART,
T Mode 3, and unknown interface types. No filter result bypasses subsequent
SPI initialization, parameter readback, or response validation.

The fixture `pc/TfgMonitorPython_H3_v5.0/testdata/ch347_dll120_interfaces.json`
was extracted from the DLL, preserving all 14 records. Regression tests check
all entries, a literal 545-byte CH347F composite metadata buffer, sparse SDK
indices, handle cleanup without SPI transfers, and GUI selection/connect flow.

The GUI logs every opened interface's FuncType, ChipMode, IF, UsbClass,
device ID, function description, and exclusion reason before filtering.
Enumeration only queries USB metadata; it does not configure SPI, change CS,
send sensor commands, or write sensor Flash.

The earlier Mode-4/logical-IF-2 comments are present in WCH's
[published Windows source](https://github.com/WCHSoftGroup/ch347/blob/main/OpenOCD_SourceCode_CH347/src/jtag/drivers/ch347.c).
The distributed binary's actual table is the basis for this fix.

## Next stage: snapshot timeout

Once USB and parameter readback succeed, a snapshot timeout means no matching
valid sensor response was returned. It does not prove a driver failure.
Check the actual sensor's firmware first. The SPI acquisition profile is
`TacAI_H3_App_SPI_v2.tacai`; a regular UART app does not respond to SPI.
An SPI-capable application is sufficient for acquisition. SPI firmware upgrade
additionally requires the dual UART/SPI Bootloader initially deployed via SWD.

The current SPI firmware uses PA0=SCK, PA3=MISO, PA4=MOSI, PA8=NSS, plus common
GND, Mode 0, 8-bit, MSB. Start with SCS0/CS0 and 468750 Hz at 3.3 V logic.
The CH347 MISO input connects to sensor PA3 output; MOSI connects to PA4.
These are the implemented firmware pin assignments, not a claim that the
tester's physical wiring or power configuration has been verified.

Timeout logs now count all-FF polls versus valid stale/other-command frames,
and include command/sequence. All FF does not identify the cause by itself:
an inactive slave, unsupported firmware, wiring, or power can produce it.
Only response polls are repeated; the original request is never replayed
automatically, including Flash-writing commands.
