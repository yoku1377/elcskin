"""Export a complete SWD-programmable image; never connect to hardware.

The image contains bootloader, audited acquisition application and a matching
VALID metadata record. It deliberately ends before the device config page.
"""
import hashlib
import json
from pathlib import Path
import struct
import zipfile
import zlib

from tacai_image import FirmwareImage

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release/spi_full_v4_20261016"
BOOT = ROOT / "firmware/bootloader/build-spi/TacAI_H3_Dual_SPI_UART_Bootloader.bin"
APP_RELEASE = ROOT / "release/spi_audit_v4_20261016"
APP_NAME = "TacAI_H3_App_SPI_audit_v4"
OUTPUT_NAME = "TacAI_H3_SPI_v4_FULL_08000000.bin"
FLASH_BASE = 0x08000000
APP_OFFSET = 0x2000
METADATA_OFFSET = 0xF000
CONFIG_OFFSET = 0xF800
META_MAGIC = 0x3341544D
META_VALID = 0x56414C44


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check_image(data, boot, image):
    assert len(data) == CONFIG_OFFSET
    assert 8 <= len(boot) <= APP_OFFSET
    assert data[:len(boot)] == boot
    assert data[len(boot):APP_OFFSET] == b"\xff" * (APP_OFFSET - len(boot))
    assert data[APP_OFFSET:APP_OFFSET + len(image.payload)] == image.payload
    assert APP_OFFSET + len(image.payload) <= METADATA_OFFSET
    assert data[APP_OFFSET + len(image.payload):METADATA_OFFSET] == b"\xff" * (METADATA_OFFSET - APP_OFFSET - len(image.payload))
    sp, reset = struct.unpack_from("<II", data)
    assert 0x20000000 < sp <= 0x20001FF0 and sp % 8 == 0
    assert reset & 1 and FLASH_BASE <= (reset & ~1) < FLASH_BASE + len(boot)
    sp, reset = struct.unpack_from("<II", data, APP_OFFSET)
    assert 0x20000000 < sp <= 0x20001FF0 and sp % 8 == 0
    assert reset & 1 and image.app_start <= (reset & ~1) < image.app_start + len(image.payload)
    record = data[METADATA_OFFSET:METADATA_OFFSET + 40]
    magic, state, start, length, crc, version, model, record_crc = struct.unpack("<6I12sI", record)
    assert (magic, state, start, length, crc, version) == (
        META_MAGIC, META_VALID, image.app_start, len(image.payload), image.image_crc32, image.app_version)
    assert model == image.model.encode("ascii").ljust(12, b"\0")
    assert record_crc == zlib.crc32(record[:36]) & 0xFFFFFFFF
    assert crc == zlib.crc32(data[APP_OFFSET:APP_OFFSET + length]) & 0xFFFFFFFF
    assert data[METADATA_OFFSET + 40:] == b"\xff" * (CONFIG_OFFSET - METADATA_OFFSET - 40)


def main():
    image = FirmwareImage.load(APP_RELEASE / f"{APP_NAME}.tacai")
    assert image.payload == (APP_RELEASE / f"{APP_NAME}.bin").read_bytes()
    assert b"h3_spi_20261016_audit_v4" in image.payload and image.app_version == 0x20004
    boot = BOOT.read_bytes()
    prefix = struct.pack("<6I12s", META_MAGIC, META_VALID, image.app_start,
                         len(image.payload), image.image_crc32, image.app_version,
                         image.model.encode("ascii").ljust(12, b"\0"))
    assert len(prefix) == 36
    record = prefix + struct.pack("<I", zlib.crc32(prefix) & 0xFFFFFFFF)
    data = bytearray(b"\xff" * CONFIG_OFFSET)
    data[:len(boot)] = boot
    data[APP_OFFSET:APP_OFFSET + len(image.payload)] = image.payload
    data[METADATA_OFFSET:METADATA_OFFSET + len(record)] = record
    check_image(data, boot, image)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    binary = OUTPUT / OUTPUT_NAME
    binary.write_bytes(data)
    check_image(binary.read_bytes(), boot, image)
    (OUTPUT / "SHA256.txt").write_text(f"{sha(data)}  {OUTPUT_NAME}\n", encoding="ascii")
    readme = """TacAI H3 SPI v4 完整恢复 BIN — 2026-10-16

唯一烧录文件：TacAI_H3_SPI_v4_FULL_08000000.bin
烧录起始地址：0x08000000。文件长度 63488 字节（0xF800）。
适用芯片：STM32G031G8U6，SWD/J-Link/ST-Link 烧录。
这不是 PB6 单线 UART 可直接烧录的文件。

文件内容
0x08000000–0x08001FFF：SPI + UART 双接口 Bootloader 区域。
0x08002000 起：完整八传感器采集应用 v4；固件 ID h3_spi_20261016_audit_v4。
0x0800F000–0x0800F7FF：与应用长度/CRC/版本匹配的 VALID 元数据。
0x0800F800–0x0800FFFF：设备配置页，本文件不包含该区域。

交给烧录人员的操作要求
1. 选择 STM32G031G8 / SWD，加载此 BIN，起始地址填 0x08000000。
   不要填应用地址 0x08002000，也不要只烧之前的 app-only HEX。
2. 若保留设备配置：只按页擦除 0x08000000–0x0800F7FF（2KB 页 0–30），再编程、校验。
   保留配置页 31：0x0800F800–0x0800FFFF。
   若允许设备配置恢复默认，也可以整片擦除后烧入本完整 BIN。
   整片擦除会清空末页原配置；当前代码的保存项是 Modbus 从站地址，默认值为 1。
   Bootloader、完整采集应用和 VALID 启动记录都会由此 BIN 写回。
3. 编程校验完成后，复位并运行；断开调试器后验证独立上电运行。
4. 使用现有供电。仅观察 UART 时：PB6 -> CH347 UART1 RXD，GND 共地，115200/8N1。
   Bootloader 正常等待约 2 秒，再启动应用。应用启动有 BOOT/SPI_READY；
   进入主循环后 SPI_DIAG 约每秒输出，DIAG 持续输出；采样有效才有 RAW/FORCE。
   若 CH347 USB 重新枚举，重新打开设备并设置串口后仍可收到持续日志。

用途和边界
相较 app-only HEX，本包同时补齐从 0x08000000 启动的 Bootloader，
并用正确 VALID 元数据替换可能遗留的 DOWN 状态；无需依赖旧 Bootloader 是否还存在。
应用与刚交付的完整采集 v4 逐字节一致，包含瞬时错误后恢复采集和异步 SPI 响应修复。
BIN 与 HEX 只是容器格式，格式本身不会解决运行故障。
本包已通过严格 Bootloader 编译、组合布局、双向量、CRC 和元数据核对；尚无实物验收。
如果本次故障来自启动链或旧 DOWN，本包覆盖了这些条件；不能保证其他硬件/运行故障一并消失。
"""
    (OUTPUT / "README_烧录地址与步骤.txt").write_text(readme, encoding="utf-8-sig")
    with zipfile.ZipFile(APP_RELEASE / "source.zip") as source, zipfile.ZipFile(OUTPUT / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in source.namelist():
            archive.writestr(name, source.read(name))
        boot_sources = [ROOT / "firmware/bootloader/build.ps1", ROOT / "firmware/bootloader/bootloader.ld"]
        boot_sources += sorted((ROOT / "firmware/bootloader/src").glob("*.c"))
        boot_sources += sorted((ROOT / "firmware/bootloader/include").glob("*.h"))
        for path in boot_sources + [Path(__file__)]:
            archive.write(path, path.relative_to(ROOT).as_posix())
    manifest = {
        "file": OUTPUT_NAME, "firmware_id": "h3_spi_20261016_audit_v4", "model": image.model,
        "program_start": hex(FLASH_BASE), "program_end_inclusive": hex(FLASH_BASE + len(data) - 1),
        "length": len(data), "sha256": sha(data),
        "bootloader_length": len(boot), "bootloader_sha256": sha(boot),
        "application_start": hex(image.app_start), "application_length": len(image.payload),
        "application_crc32": hex(image.image_crc32), "application_version": hex(image.app_version),
        "application_sha256": sha(image.payload), "metadata_address": hex(FLASH_BASE + METADATA_OFFSET),
        "metadata_state": "VALID", "metadata_record_crc32": hex(zlib.crc32(prefix) & 0xFFFFFFFF),
        "config_page_excluded": "0x0800F800-0x0800FFFF", "erase_pages_inclusive": [0, 30],
        "full_chip_erase_supported": True,
        "full_chip_erase_effect": "Existing device config is lost; Modbus slave ID defaults to 1",
        "bootloader_replaced": True, "hardware_validation": "pending",
        "files": {p.name: {"length": p.stat().st_size, "sha256": sha(p.read_bytes())}
                  for p in sorted(OUTPUT.iterdir()) if p.is_file() and p.name != "manifest.json"},
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    target = ROOT / "release/TacAI_H3_SPI_v4_FULL_08000000_20261016.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(OUTPUT.iterdir()):
            if p.is_file():
                archive.write(p, p.name)
    print(json.dumps({key: manifest[key] for key in ("file", "length", "sha256", "program_start", "program_end_inclusive", "metadata_state")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
