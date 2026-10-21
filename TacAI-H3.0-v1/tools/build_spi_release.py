"""Package the H3 SPI candidate without replacing existing UART releases."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess

from tacai_image import APP_START, APP_MAX_SIZE, FirmwareImage, create_container

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release" / "spi_trial_20261014"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    app = ROOT / "firmware/application/build_spi/TacAI_H3_App_SPI_v2.bin"
    boot = ROOT / "firmware/bootloader/build-spi/TacAI_H3_Dual_SPI_UART_Bootloader.bin"
    if boot.stat().st_size > 8192:
        raise ValueError("Bootloader exceeds 8 KiB")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    image = create_container(app.read_bytes(), 0x00020000)
    target = OUTPUT / "TacAI_H3_App_SPI_v2.tacai"
    target.write_bytes(image.encode())
    assert FirmwareImage.load(target).payload == app.read_bytes()
    combined = bytearray(b"\xFF" * (APP_START - 0x08000000 + len(image.payload)))
    combined[:boot.stat().st_size] = boot.read_bytes()
    combined[APP_START - 0x08000000:] = image.payload
    assert 0x08000000 + len(combined) <= 0x0800F000
    combined_bin = OUTPUT / "TacAI_H3_FirstDeploy_Dual_SPI_UART_v2.bin"
    combined_bin.write_bytes(combined)
    tool = Path(r"C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\12.2 mpacbti-rel1\bin")
    subprocess.run([str(tool / "arm-none-eabi-objcopy.exe"), "-I", "binary", "-O", "ihex",
                    "--change-addresses=0x08000000", str(combined_bin),
                    str(OUTPUT / "TacAI_H3_FirstDeploy_Dual_SPI_UART_v2.hex")], check=True)
    for source in (app, boot):
        shutil.copy2(source, OUTPUT / source.name)
    dll = ROOT.parent / "SPI_预联调_20261014/references/sdk/CH347DLLA64.dll"
    if dll.is_file():
        shutil.copy2(dll, OUTPUT / dll.name)
    files = {p.name: {"size": p.stat().st_size, "sha256": sha(p)}
             for p in OUTPUT.iterdir() if p.is_file() and p.name != "manifest.json"}
    manifest = {
        "candidate": "H3_SPI_UART_IAP_v2_20261014", "hardware_validation": "pending",
        "model": image.model, "app_start": hex(APP_START), "app_max_size": APP_MAX_SIZE,
        "app_version": hex(image.app_version), "app_crc32": hex(image.image_crc32),
        "bootloader_size": boot.stat().st_size, "app_size": len(image.payload),
        "combined_start": "0x08000000", "combined_end_exclusive": hex(0x08000000 + len(combined)),
        "metadata_start": "0x0800f000", "config_start": "0x0800f800",
        "spi": {"instance": "SPI2", "mode": 0, "word_bits": 8, "first_bit": "MSB",
                "clock_hz": 468750, "host_cs_high_ms": 2, "mcu": {"SCK": "PA0", "MISO": "PA3", "MOSI": "PA4", "NSS": "PA8"}},
        "files": files,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: manifest[key] for key in ("bootloader_size", "app_size", "combined_end_exclusive")}, indent=2))


if __name__ == "__main__":
    main()
