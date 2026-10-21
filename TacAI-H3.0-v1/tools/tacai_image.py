"""TacAI H3 application image container and factory-combined image builder."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"TACAIH3\0"
FORMAT_VERSION = 1
HEADER_SIZE = 64
MODEL_ID = "H3G031G8"
APP_START = 0x08002000
APP_MAX_SIZE = 0x0000D000
METADATA_ADDRESS = 0x0800F000
FLASH_SIZE = 0x00010000
HEADER_STRUCT = struct.Struct("<8sHH12sIIII20sI")


@dataclass(frozen=True)
class FirmwareImage:
    model: str
    app_start: int
    app_version: int
    payload: bytes
    image_crc32: int

    @classmethod
    def load(cls, path: str | Path) -> "FirmwareImage":
        data = Path(path).read_bytes()
        if len(data) < HEADER_SIZE:
            raise ValueError("镜像短于 64 字节头")
        fields = HEADER_STRUCT.unpack(data[:HEADER_SIZE])
        magic, fmt, header_size, model_raw, start, length, crc, version, reserved, header_crc = fields
        if magic != MAGIC or fmt != FORMAT_VERSION or header_size != HEADER_SIZE:
            raise ValueError("不是受支持的 TacAI H3 镜像格式")
        if any(reserved):
            raise ValueError("镜像保留字段非零")
        if zlib.crc32(data[: HEADER_SIZE - 4]) & 0xFFFFFFFF != header_crc:
            raise ValueError("镜像头 CRC32 错误")
        model = model_raw.split(b"\0", 1)[0].decode("ascii", errors="strict")
        payload = data[HEADER_SIZE:]
        if model != MODEL_ID:
            raise ValueError(f"型号不匹配：镜像 {model!r}，期望 {MODEL_ID}")
        if start != APP_START:
            raise ValueError(f"应用地址错误：0x{start:08X}")
        if length != len(payload) or not 8 <= length <= APP_MAX_SIZE:
            raise ValueError("镜像长度非法或与文件不一致")
        if zlib.crc32(payload) & 0xFFFFFFFF != crc:
            raise ValueError("应用负载 CRC32 错误")
        validate_vector(payload)
        return cls(model, start, version, payload, crc)

    def encode(self) -> bytes:
        model = self.model.encode("ascii")
        if len(model) > 12:
            raise ValueError("型号标识超过 12 字节")
        prefix = HEADER_STRUCT.pack(
            MAGIC,
            FORMAT_VERSION,
            HEADER_SIZE,
            model.ljust(12, b"\0"),
            self.app_start,
            len(self.payload),
            self.image_crc32,
            self.app_version,
            b"\0" * 20,
            0,
        )
        header_crc = zlib.crc32(prefix[:-4]) & 0xFFFFFFFF
        header = prefix[:-4] + struct.pack("<I", header_crc)
        return header + self.payload


def validate_vector(payload: bytes) -> None:
    stack_pointer, reset_handler = struct.unpack_from("<II", payload)
    if not 0x20000000 <= stack_pointer <= 0x20001FF0:
        raise ValueError(f"初始栈指针非法：0x{stack_pointer:08X}")
    if reset_handler & 1 == 0 or not APP_START <= reset_handler < METADATA_ADDRESS:
        raise ValueError(f"复位向量非法：0x{reset_handler:08X}")


def create_container(app_binary: bytes, app_version: int) -> FirmwareImage:
    if len(app_binary) > APP_MAX_SIZE:
        raise ValueError(f"应用 {len(app_binary)} 字节，超过分区上限 {APP_MAX_SIZE}")
    validate_vector(app_binary)
    return FirmwareImage(
        model=MODEL_ID,
        app_start=APP_START,
        app_version=app_version,
        payload=app_binary,
        image_crc32=zlib.crc32(app_binary) & 0xFFFFFFFF,
    )


def build_release(boot_path: Path, app_path: Path, output_dir: Path, app_version: int) -> dict:
    boot = boot_path.read_bytes()
    app = app_path.read_bytes()
    if len(boot) > 0x2000:
        raise ValueError("Bootloader 超过 8 KiB")
    image = create_container(app, app_version)
    output_dir.mkdir(parents=True, exist_ok=True)
    container_path = output_dir / "TacAI_H3_App_v1.tacai"
    container_path.write_bytes(image.encode())

    # Deliberately stop at the end of the application. Programming this BIN at
    # 0x08000000 does not target the metadata or device-configuration pages.
    combined = bytearray(b"\xFF" * (APP_START - 0x08000000 + len(app)))
    combined[: len(boot)] = boot
    combined[APP_START - 0x08000000 : APP_START - 0x08000000 + len(app)] = app
    combined_path = output_dir / "TacAI_H3_FirstDeploy_STLink_preserve_config_v1.bin"
    combined_path.write_bytes(combined)

    result = {
        "model": image.model,
        "app_start": f"0x{image.app_start:08X}",
        "app_version": f"0x{image.app_version:08X}",
        "app_length": len(app),
        "app_crc32": f"0x{image.image_crc32:08X}",
        "bootloader_length": len(boot),
        "container_sha256": hashlib.sha256(container_path.read_bytes()).hexdigest(),
        "factory_combined_sha256": hashlib.sha256(combined).hexdigest(),
        "factory_program_address": "0x08000000",
    }
    (output_dir / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--boot", type=Path, required=True)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", type=lambda value: int(value, 0), default=0x00010000)
    args = parser.parse_args()
    print(json.dumps(build_release(args.boot, args.app, args.output, args.version), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
