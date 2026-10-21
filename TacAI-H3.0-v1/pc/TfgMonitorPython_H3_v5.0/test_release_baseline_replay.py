"""Working-zero CSV coordinates and report boundaries remain explicit."""
import csv
from pathlib import Path
import tempfile
import unittest

from folder_report import H3_COLUMNS, load_folder
from recording_report import audit_effective_baseline, load_recordings


OFFSET_COLUMNS = [f"S{i}_release_offset_dB{axis}" for i in range(1, 9) for axis in "xyz"]


def sample(epoch="1", *, offset=0., **extra):
    return {
        "record_type": "PAIRED", "selected_resultant_or_diagnostic_n": "3",
        "selected_value_mode": "H3 Fz 候选模型", "host_tare_epoch": "1",
        "effective_baseline_epoch": epoch, "frame_state": "PAIRED",
        "quality_flags": "MODEL_VALIDATION_PENDING", "position_mark": "前3N_01",
        **dict.fromkeys(H3_COLUMNS, ".375"),
        **dict.fromkeys(OFFSET_COLUMNS, str(offset)), **extra,
    }


def write_samples(folder, samples, *, trial=1):
    path = Path(folder) / f"前3N_{trial:02}.csv"
    fields = list(dict.fromkeys(key for row in samples for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(samples)
    return path


class ReleaseBaselineReplayTests(unittest.TestCase):
    def test_legacy_recordings_remain_legal_without_new_epoch(self):
        rows = [sample(), sample()]
        for row in rows:
            row.pop("effective_baseline_epoch")
            for column in OFFSET_COLUMNS:
                row.pop(column)
        self.assertEqual(audit_effective_baseline(rows), "")
        with tempfile.TemporaryDirectory() as directory:
            path = write_samples(directory, rows)
            trials, _ = load_recordings(path)
            _, entries = load_folder(directory)
            self.assertEqual(trials[0]["mean"], 3.)
            self.assertEqual(entries[0]["status"], "已导入")

    def test_mixed_working_zero_cannot_be_one_trial_in_either_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_samples(directory, [sample("1"), sample("2")])
            trials, sources = load_recordings(path, allow_empty=True)
            self.assertEqual(trials, [])
            self.assertEqual(sources[0]["reason_code"], "mode")
            self.assertIn("effective_baseline_epoch", sources[0]["detail"])
            with self.assertRaisesRegex(ValueError, "effective_baseline_epoch.*不能混合评价"):
                load_folder(directory)

    def test_zero_epoch_must_be_present_finite_nonnegative_integer(self):
        for epoch in ("", "NaN", "Inf", "-1", "1.5"):
            with self.subTest(epoch=epoch):
                with self.assertRaisesRegex(ValueError, "effective_baseline_epoch.*无效"):
                    audit_effective_baseline([sample("1"), sample(epoch)])
        self.assertEqual(audit_effective_baseline([sample("1"), sample("1.0")]), "")

    def test_v23_cannot_omit_working_zero_audit_fields(self):
        row = sample(monitor_version="5.3.23-release-baseline")
        row.pop("effective_baseline_epoch")
        with self.assertRaisesRegex(ValueError, "effective_baseline_epoch.*缺失"):
            audit_effective_baseline([row])
        row = sample(monitor_version="5.3.23-release-baseline")
        for column in OFFSET_COLUMNS:
            row.pop(column)
        with self.assertRaisesRegex(ValueError, "补偿记录缺失"):
            audit_effective_baseline([row])

    def test_offsets_cannot_change_under_same_epoch_or_be_incomplete(self):
        with self.assertRaisesRegex(ValueError, "RAW 工作零点补偿发生变化"):
            audit_effective_baseline([sample(offset=25), sample(offset=26)])
        broken = sample(offset=25)
        broken[OFFSET_COLUMNS[-1]] = ""
        with self.assertRaisesRegex(ValueError, "补偿记录缺失或无效"):
            audit_effective_baseline([sample(offset=25), broken])

    def test_fixed_pretrial_compensation_is_retained_and_disclosed(self):
        rows = [sample(offset=42), sample(offset=42)]
        for row in rows:
            row["quality_flags"] += "|RELEASE_BASELINE_COMPENSATED"
        with tempfile.TemporaryDirectory() as directory:
            path = write_samples(directory, rows)
            trials, sources = load_recordings(path)
            _, entries = load_folder(directory)
            self.assertEqual(trials[0]["mean"], 3.)
            self.assertEqual(trials[0]["used_frames"], 2)
            self.assertIn("不能证明传感器自然回零", sources[0]["warnings"])
            self.assertEqual(entries[0]["means"], [.375] * 8)
            self.assertIn("不能证明传感器自然回零", entries[0]["warnings"])

    def test_epochs_are_local_to_each_trial_and_do_not_conflict_across_files(self):
        with tempfile.TemporaryDirectory() as directory:
            write_samples(directory, [sample("1", offset=10), sample("1", offset=10)], trial=1)
            write_samples(directory, [sample("7", offset=70), sample("7", offset=70)], trial=2)
            trials, _ = load_recordings(directory)
            _, entries = load_folder(directory)
            self.assertEqual(len(trials), 2)
            self.assertTrue(all(entry["status"] == "已导入" for entry in entries))

    def test_invalid_capture_and_event_rows_cannot_change_baseline_audit(self):
        invalid = sample("99", offset=90, acquisition_diag_available="1",
                         firmware_valid_mask="254", firmware_baseline_epoch="1")
        event = {"record_type": "RELEASE_BASELINE_UPDATED", "effective_baseline_epoch": ""}
        self.assertEqual(audit_effective_baseline([sample(), invalid, event, sample()]), "")


if __name__ == "__main__":
    unittest.main()
