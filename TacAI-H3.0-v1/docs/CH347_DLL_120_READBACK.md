# CH347 DLL 1.20 SPI readback compatibility

The monitor previously compared `CH347SPI_GetCfg().iByteOrder` directly with
the `CH347SPI_Init()` argument `1` (MSB). The distributed WCH DLL does not
return the API enum in that field. Its readback returns the hardware
`SPI_FirstBit & 0x80` value: `0` is MSB and `0x80` is LSB. Thus correctly
configured MSB hardware fails the old monitor's equality check.

This was established by static inspection of the distributed binary, without
loading the DLL or accessing a device:

- SHA256: `00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375`
- File version: 1.20, AMD64. Original file remains in
  `SPI_预联调_20261014/references/sdk/CH347DLLA64.dll` in the workspace.
- `CH347SPI_Init` export VA `0x40F830` calls configuration conversion at
  `0x40E320`. At `0x40E38A`, the input byte-order field is masked with `1`;
  `0x40E38F..0x40E399` writes hardware `FirstBit=0` for input `1`, or
  `FirstBit=0x80` for input `0`.
- `CH347SPI_GetCfg` export VA `0x40E5B0` reads hardware configuration. At
  `0x40E6A2..0x40E6A9`, it copies `FirstBit & 0x80` into the output field.
  It does not translate that mask back into the public enum.
- `CH347SPI_SetDataBits` VA `0x40F7D0` only updates a DLL cache. `Init` uses
  that cache at `0x40E3F8..0x40E40C`. Set the 8-bit selection before Init.
- Init rewrites CS2 selector `0x81` in place to `0x8000`; GetCfg returns the
  latter mask. CS0/SCS0 remains `0x80` in both representations.

The wrapper applies the inverted readback interpretation only when the loaded
DLL has the exact SHA256 above. Other binaries retain the documented API enum
(`1` means MSB). It does not blindly accept both `0` and `1` as MSB.
Mode, clock, bit order, single CS selection, polarity, and manual CS checks
still reject real mismatches before an SPI payload is sent. The exact response
is retained as `raw_readback`; `config` uses the public API representation.
Logs include requested and actual mode, clock, byte-order value, CS, the
decoding convention, and the DLL hash.

Regression tests inject the observed DLL response `byteOrder=0`, verify that
MSB Init is retained, and reject actual LSB `0x80`, unexpected values, unknown
DLL enum `0`, mode/clock mismatches, and invalid CS masks. They also exercise
CS2 in-place normalization and verify the 8-bit setup occurs before Init.
These are software/ABI checks; physical SPI traffic and IAP remain pending.

The public API definition is documented by WCH in its
[application development manual](https://files.waveshare.com/upload/2/2b/CH347-applicationa-development-manual-en.pdf).
The same API enum and hardware FirstBit distinction is visible in WCH's
[published header](https://github.com/WCHSoftGroup/ch341par_linux/blob/main/lib/x86/static/ch347_lib.h),
though that Linux ABI is not used by the Windows wrapper.
