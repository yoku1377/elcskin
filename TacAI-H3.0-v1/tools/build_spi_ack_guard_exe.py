"""Build the v27 snapshot ACK guard on frozen v26, without device access."""
from __future__ import annotations

import json

import build_calibration_trace_exe as builder


ROOT = builder.ROOT


def main():
    builder.BASE_SOURCE = ROOT / "tmp/zp500n_gauge_20261020_build/source"
    builder.BASE_MANIFEST = ROOT / "outputs/zp500n_gauge_20261020/manifest.json"
    builder.BASE_VERSION = "5.3.26-zp500n-gauge"
    builder.OUT = ROOT / "release/TacAI_H3_v5_3_27_SPIAckGuard_20261020"
    builder.WORK = ROOT / "tmp/spi_ack_guard_20261020_build"
    builder.REPORT = ROOT / "outputs/spi_ack_guard_20261020"
    builder.NAME = "TacAI H3 v5.3.27 SPI ACK Guard"
    builder.VERSION = "5.3.27-spi-ack-guard"
    builder.CHANGED = {"main.py", "firmware_updater.py", "spi_iap_protocol.py"}
    builder.ADDED = {"test_spi_snapshot_ack_guard.py"}
    baseline = json.loads(builder.BASE_MANIFEST.read_text(encoding="utf-8"))
    builder.CHECKS = [*baseline["tests"], "test_spi_snapshot_ack_guard"]
    firmware_paths = ["firmware/shared/spi_link.c", "firmware/application/Core/Src/app_spi.c",
                      "tests/firmware_spi/spi_snapshot_stale_ack_harness.c",
                      "tests/test_spi_snapshot_stale_firmware.py"]
    firmware_hashes = {name: builder.sha((ROOT / name).read_bytes()) for name in firmware_paths}
    builder.main()
    assert firmware_hashes == {name: builder.sha((ROOT / name).read_bytes()) for name in firmware_paths}
    manifest_path = builder.REPORT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        iap_protocols_dlls_and_firmware_unchanged=False,
        spi_snapshot_protocol_changed=True,
        iap_write_commands_dlls_and_firmware_unchanged=True,
        snapshot_ack_guard_after_success_before_next_request=True,
        snapshot_ack_guard_bytes=536, snapshot_ack_guard_mosi="FF",
        snapshot_ack_guard_uses_original_total_deadline=True,
        snapshot_ack_guard_response_never_delivered_as_sample=True,
        snapshot_ack_guard_transport_failure_is_fatal=True,
        actual_stream_gap_cancels_incomplete_capture=True,
        calibration_data_quality_thresholds_unchanged=True,
        calibration_gauge_source_and_protocol_unchanged=True,
        snapshot_requests_remain_16_bytes=True,
        physical_rx_fault_root_cause_not_proven=True,
        hardware_validation="pending; production C state-machine injection and fake USB only",
    )
    c_log = (builder.REPORT / "firmware_state_repro.log").read_text(encoding="utf-8")
    assert "PASS: 7 production RXIRQ/mailbox/snapshot ACK-drain guard cases" in c_log
    manifest["firmware_guard_cases"] = 7
    manifest["firmware_existing_recovery_cases"] = 3
    manifest["firmware_injection_source_sha256"] = firmware_hashes
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
