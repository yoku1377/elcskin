"""Export a complete acquisition firmware candidate with CPU RXNE reception.

This packages files only; no probe, UART or SPI device is opened.
"""
from pathlib import Path
import hashlib
import json
import shutil
import struct
import subprocess
import zipfile
import zlib

from tacai_image import FirmwareImage, create_container
from build_spi_audit_release import read_hex
from build_spi_recovery_release import check_image

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "firmware/application/build_spi_rxirq_v5"
OUTPUT = ROOT / "release/spi_rxirq_v5_20261016"
NAME = "TacAI_H3_App_SPI_rxirq_v5"
FIRMWARE_ID = "h3_spi_20261016_rxirq_v5"
BOOT = ROOT / "firmware/bootloader/build-spi/TacAI_H3_Dual_SPI_UART_Bootloader.bin"
ARM = Path(r"C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\12.2 mpacbti-rel1\bin")
FULL_NAME = "TacAI_H3_SPI_RXIRQ_v5_FULL_08000000.bin"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    payload = (BUILD / f"{NAME}.bin").read_bytes()
    assert FIRMWARE_ID.encode() in payload
    assert b"SPI_DIAG," in payload and b"SPI_READY," in payload
    assert b"RAW,%lu" in payload and b"FORCE,%lu," in payload
    image = create_container(payload, 0x00020005)
    container = OUTPUT / f"{NAME}.tacai"
    container.write_bytes(image.encode())
    assert FirmwareImage.load(container).payload == payload
    for suffix in ("bin", "hex", "elf", "map"):
        shutil.copy2(BUILD / f"{NAME}.{suffix}", OUTPUT / f"{NAME}.{suffix}")
    memory = read_hex(OUTPUT / f"{NAME}.hex")
    assert min(memory) == image.app_start and max(memory) < 0x0800F000
    assert bytes(memory.get(image.app_start + i, 255) for i in range(len(payload))) == payload
    symbols = subprocess.check_output([str(ARM / "arm-none-eabi-nm.exe"), str(BUILD / f"{NAME}.elf")], text=True)
    names = {parts[2]: int(parts[0], 16) for line in symbols.splitlines()
             if len(parts := line.split()) == 3}
    for handler, irq in (("EXTI4_15_IRQHandler", 7), ("SPI2_IRQHandler", 26)):
        assert struct.unpack_from("<I", payload, (16 + irq) * 4)[0] == names[handler] | 1
    assert "SPI_LinkRxIRQ" in names and "MATRIX_Update_Measurement" in names
    assert "MX_USART2_UART_Init" not in names
    assert names["_ebss"] < names["_estack"]

    boot = BOOT.read_bytes()
    assert sha(boot) == "3dcbe3b04d3a77b6e560608a925d8661e7aa853e6d776d3ad0aee2726af9a936"
    prefix = struct.pack("<6I12s", 0x3341544D, 0x56414C44, image.app_start,
                         len(payload), image.image_crc32, image.app_version,
                         image.model.encode("ascii").ljust(12, b"\0"))
    record = prefix + struct.pack("<I", zlib.crc32(prefix) & 0xFFFFFFFF)
    full = bytearray(b"\xff" * 0xF800)
    full[:len(boot)] = boot
    full[0x2000:0x2000 + len(payload)] = payload
    full[0xF000:0xF000 + len(record)] = record
    check_image(full, boot, image)
    (OUTPUT / FULL_NAME).write_bytes(full)
    check_image((OUTPUT / FULL_NAME).read_bytes(), boot, image)

    readme = """H3 完整采集固件：RXNE 中断接收验证版 v5 — 2026-10-16

固件ID：h3_spi_20261016_rxirq_v5，应用版本0x00020005。
用途：软件接收路径对照。SPI2 RX由CPU在RXNE中断中逐字节读DR，绕开原RX DMA；
TX仍由DMA发送。保留八颗MLX90393实际采集、基线、力计算和UART RAW/FORCE/DIAG。
CS、TAI3快照、CRC和ACK规则保持原协议。不是仅通信固件。
新增现场GPIO/AF、DMA和RTC相关状态读回用于软件定位；不清RTC配置或backup域。
这是候选验证版，尚未证明能解决现场全FF；原v4发布文件保持原样。

烧录文件与地址
- TacAI_H3_SPI_RXIRQ_v5_FULL_08000000.bin：完整镜像，SWD烧录起始0x08000000。
  包含已验证的同一Bootloader、v5完整采集应用、匹配CRC的VALID元数据，长度0xF800。
  按页擦除0–30可保留0x0800F800配置页；允许配置恢复默认时也可整片擦除后烧完整BIN。
  整片擦除会使Modbus地址恢复默认1。
- TacAI_H3_App_SPI_rxirq_v5.hex/.bin：仅应用；BIN地址0x08002000，依赖完整Bootloader/元数据。
- .tacai：应用升级容器；仅在原UART升级链路可用时使用。
只有PB6→CH347 RXD可接收日志，不能以这根单线完成烧录。此包不会自动烧录。

现有接线不变：PA0/SCK，PA3/MISO，PA4/MOSI，PA8/NSS；CH347 CS0。
串口PB6→UART1 RXD、共地，115200/8N1。软件只读配置审计工具无需此包也可运行。
验证顺序：先完成CH347底层收发方向检查；若要对照固件接收后端，再用本候选。
启动日志应识别rxirq_v5，随后仍有有效DIAG/RAW/FORCE及SPI诊断。
现有旧SPI检查工具的expected_firmware仍写死audit_v4；验证本候选时，
其中firmware_match可能仅因版本变化而为false。请结合实际rxirq_v5 ID、
CRC/序号结果与原始UART日志判断，不把旧工具总判定直接作为本候选失败证据。
仅软件编译/生产C回归/向量/镜像CRC验证不等于实机SPI通过。
"""
    (OUTPUT / "README_软件接收验证与烧录地址.txt").write_text(readme, encoding="utf-8-sig")
    sources = []
    for base in (ROOT / "firmware/application", ROOT / "firmware/shared", ROOT / "tests"):
        sources += [p for p in base.rglob("*") if p.is_file() and p.suffix in (".c", ".h", ".s", ".py", ".ps1", ".ld")
                    and not any(part.startswith("build") for part in p.relative_to(ROOT).parts)]
    sources += [Path(__file__), ROOT / "tools/tacai_image.py", ROOT / "tools/build_spi_audit_release.py",
                ROOT / "tools/build_spi_recovery_release.py"]
    with zipfile.ZipFile(OUTPUT / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(set(sources)):
            archive.write(p, p.relative_to(ROOT).as_posix())
    manifest = {"firmware_id": FIRMWARE_ID, "purpose": "RX DMA versus CPU RXNE receive comparison",
                "hardware_validation": "pending", "full_acquisition": True, "calibration_profile": 8,
                "application_start": hex(image.app_start), "application_length": len(payload),
                "application_crc32": hex(image.image_crc32), "application_version": hex(image.app_version),
                "program_start": "0x08000000", "full_image_length": len(full), "metadata_state": "VALID",
                "bootloader_changed": False, "bootloader_sha256": sha(boot), "config_page_excluded": "0x0800F800-0x0800FFFF",
                "ram_static_end": hex(names["_ebss"]), "stack_top": hex(names["_estack"]),
                "files": {p.name: {"size": p.stat().st_size, "sha256": sha(p.read_bytes())}
                          for p in sorted(OUTPUT.iterdir()) if p.is_file() and p.name != "manifest.json"}}
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    target = ROOT / "release/TacAI_H3_SPI_RXIRQ_v5_20261016.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(OUTPUT.iterdir()):
            if p.is_file():
                archive.write(p, p.name)
    print(json.dumps({"archive": str(target), "application_length": len(payload),
                      "application_crc32": hex(image.image_crc32), "full_image_sha256": sha(full)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
