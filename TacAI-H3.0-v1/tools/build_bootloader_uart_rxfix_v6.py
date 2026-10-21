"""Compile the resident UART header fix and combine it with unchanged v5 app.

Only builds and validates files. Never opens a probe or programs a device.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile

from build_spi_recovery_release import check_image
from tacai_image import FirmwareImage

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "release/bootloader_uart_rxfix_v6_20261019"
BUILD = ROOT / "tmp/bootloader_uart_rxfix_v6_20261019_build"
ARM = Path(r"C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\12.2 mpacbti-rel1\bin")
BASE = ROOT / "release/spi_rxirq_v5_20261016"
BOOT_NAME = "TacAI_H3_Dual_SPI_UART_Bootloader_v6"
FULL_NAME = "TacAI_H3_BLv6_Appv5_FULL_08000000.bin"
BASE_FULL_SHA = "86e25f2620f8fdbf08087457b4f268b2d11f05f64f344449ef73a95a960e7b42"
OLD_BOOT_SHA = "3dcbe3b04d3a77b6e560608a925d8661e7aa853e6d776d3ad0aee2726af9a936"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    assert not OUT.exists() and not BUILD.exists(), "Never overwrite a firmware release"
    baseline = (BASE / "TacAI_H3_SPI_RXIRQ_v5_FULL_08000000.bin").read_bytes()
    assert sha(baseline) == BASE_FULL_SHA
    image = FirmwareImage.load(BASE / "TacAI_H3_App_SPI_rxirq_v5.tacai")
    assert image.app_version == 0x20005 and image.payload == (BASE / "TacAI_H3_App_SPI_rxirq_v5.bin").read_bytes()
    assert sha(baseline[:5404]) == OLD_BOOT_SHA
    OUT.mkdir(parents=True)
    BUILD.mkdir(parents=True)
    source = BUILD / "source"
    input_names = ["firmware/bootloader/src/main.c", "firmware/bootloader/src/system_boot.c",
                   "firmware/bootloader/include/iap_layout.h", "firmware/bootloader/bootloader.ld",
                   "firmware/shared/spi_link.c", "firmware/shared/spi_link.h"]
    for name in input_names:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    boot = source / "firmware/bootloader"
    shared = source / "firmware/shared"
    app = ROOT / "firmware/application"
    common = ["-mcpu=cortex-m0plus", "-mthumb", "-Os", "-ffunction-sections", "-fdata-sections",
              "-fno-common", "-Wall", "-Wextra", "-Werror", "-DSTM32G031xx", "-DTACAI_ENABLE_SPI",
              f"-I{boot / 'include'}", f"-I{shared}", f"-I{app / 'Drivers/CMSIS/Include'}",
              f"-I{app / 'Drivers/CMSIS/Device/ST/STM32G0xx/Include'}"]
    with (OUT / "arm_build.log").open("w", encoding="utf-8") as log:
        def run(args):
            subprocess.run([str(a) for a in args], check=True, stdout=log, stderr=subprocess.STDOUT)
        for file, name in ((boot / "src/main.c", "main"), (boot / "src/system_boot.c", "system"),
                           (shared / "spi_link.c", "spi_link")):
            run([ARM / "arm-none-eabi-gcc.exe", *common, "-c", file, "-o", BUILD / (name + ".o")])
        startup = app / "Drivers/CMSIS/Device/ST/STM32G0xx/Source/Templates/gcc/startup_stm32g031xx.s"
        run([ARM / "arm-none-eabi-gcc.exe", *common, "-x", "assembler-with-cpp", "-c", startup,
             "-o", BUILD / "startup.o"])
        elf = OUT / (BOOT_NAME + ".elf")
        run([ARM / "arm-none-eabi-gcc.exe", "-mcpu=cortex-m0plus", "-mthumb", "-nostartfiles",
             "-Wl,--gc-sections", "-Wl,--fatal-warnings", f"-T{boot / 'bootloader.ld'}",
             "--specs=nano.specs", "--specs=nosys.specs", f"-Wl,-Map={OUT / (BOOT_NAME + '.map')}",
             *(BUILD / (name + ".o") for name in ("startup", "system", "main", "spi_link")), "-o", elf])
        for format, suffix in (("binary", "bin"), ("ihex", "hex")):
            run([ARM / "arm-none-eabi-objcopy.exe", "-O", format, elf, OUT / (BOOT_NAME + "." + suffix)])
        run([ARM / "arm-none-eabi-size.exe", elf])
    binary = (OUT / (BOOT_NAME + ".bin")).read_bytes()
    assert len(binary) <= 8192 and sha(binary) != OLD_BOOT_SHA
    symbols = subprocess.check_output([str(ARM / "arm-none-eabi-nm.exe"), str(elf)], text=True)
    symbol_map = {p[2]: int(p[0], 16) for line in symbols.splitlines() if len(p := line.split()) == 3}
    assert symbol_map["_ebss"] + 0x500 <= symbol_map["_estack"]
    assert "BOOTLOADER_UART_BYTE_TEST" not in symbols
    for function in ("SPI_LinkInit", "SPI_LinkReceive", "SPI_LinkSend", "SysTick_Handler"):
        assert function in symbol_map
    assert struct.unpack_from("<I", binary, 4)[0] == symbol_map["Reset_Handler"] | 1
    full = bytearray(baseline)
    full[:0x2000] = b"\xFF" * 0x2000
    full[:len(binary)] = binary
    assert full[0x2000:] == baseline[0x2000:], "Application and metadata must stay byte identical"
    check_image(full, binary, image)
    (OUT / FULL_NAME).write_bytes(full)
    check_image((OUT / FULL_NAME).read_bytes(), binary, image)
    with (OUT / "uart_rx_phase_tests.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p",
                        "test_uart_bootloader_rx.py", "-v"], cwd=ROOT, check=True,
                       stdout=log, stderr=subprocess.STDOUT)
    (OUT / "烧录说明.txt").write_text(
        "TacAI H3 常驻 Bootloader v6 UART 接收修复 — 2026-10-19\n\n"
        "完整烧录文件：" + FULL_NAME + "\n"
        "J-Flash选择STM32G031G8 / SWD，加载本BIN，起始地址0x08000000。\n"
        "按页擦除本镜像覆盖的0x08000000–0x0800F7FF，编程并Verify，最后Reset/Run。\n"
        "镜像长度0xF800，未包含0x0800F800配置页；需要保留配置时不要整片擦除。\n"
        "只有配置允许恢复默认时，才使用整片擦除（设备地址恢复默认1）。\n\n"
        "修复位于常驻Bootloader，普通.tacai应用升级覆盖不到，需SWD更新一次。\n"
        "收到TAI3前缀后，接收余下魔数字节改为有界500ms等待，防止2ms轮询切断帧头。\n"
        "无UART输入时仍2ms返回，保留双接口SPI响应能力。其余擦写/CRC/ACK协议不变。\n"
        "应用仍h3_spi_20261016_rxirq_v5，完整采集/力计算与之前v5逐字节一致。\n"
        "升级时先保持USB连接。仅在尚未同步Bootloader时，才可能需重置/重新上电传感器。\n"
        "出现擦除/写入进度后不要拔USB或断电。成功前旧应用可能处于未完成状态。\n"
        "已通过生产C帧头相位故障注入、ARM编译及镜像布局/CRC验证；实机升级待验证。\n",
        encoding="utf-8-sig")
    with zipfile.ZipFile(OUT / "source.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for name in input_names:
            archive.write(source / name, name)
        for file in (ROOT / "tests/uart_bootloader_rx_harness.c", ROOT / "tests/test_uart_bootloader_rx.py",
                     ROOT / "tests/test_spi_firmware.py", *sorted((ROOT / "tests/uart_bootloader_stubs").glob("*.h")),
                     Path(__file__)):
            archive.write(file, file.relative_to(ROOT).as_posix())
    release = dict(bootloader_version=6, application_version=5, application_firmware_id="h3_spi_20261016_rxirq_v5",
                   reason="Fix2ms UART magic prefix truncation at poll boundary", full_bin=FULL_NAME,
                   flash_address="0x08000000", image_bytes=len(full), bootloader_bytes=len(binary),
                   bootloader_sha256=sha(binary), full_image_sha256=sha(full), previous_full_sha256=BASE_FULL_SHA,
                   app_and_metadata_byte_identical=True, config_page_excluded=True,
                   ram_static_end=hex(symbol_map["_ebss"]), stack_top=hex(symbol_map["_estack"]),
                   hardware_accessed=False, hardware_validation="pending",
                   files={p.name: dict(bytes=p.stat().st_size, sha256=sha(p.read_bytes()))
                          for p in sorted(OUT.iterdir()) if p.is_file()})
    (OUT / "manifest.json").write_text(json.dumps(release, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(release, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
