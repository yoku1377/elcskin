import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PyQt5 import QtWidgets

from calibration_acquisition import (
    build_steps,
    force_calibration_steps,
    location_mapping_steps,
    parse_gauge_value,
    repeatability_steps,
    CalibrationAcquisitionDialog,
)


class CalibrationAcquisitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_quick_force_plan_covers_three_positions_and_loading_directions(self):
        steps = force_calibration_steps(repeats=1, quick=True)
        self.assertEqual({step.position for step in steps}, {"前", "中", "后"})
        self.assertIn("加载", {step.direction for step in steps})
        self.assertIn("卸载", {step.direction for step in steps})
        self.assertEqual(steps[0].target_n, 0.0)
        self.assertEqual(steps[-1].target_n, 0.0)

    def test_complete_plan_contains_low_force_points(self):
        levels = {step.target_n for step in build_steps("完整力值标定")}
        self.assertTrue({0.01, 0.02, 0.05, 30.0}.issubset(levels))

    def test_location_and_repeatability_plan_sizes(self):
        self.assertEqual(len(location_mapping_steps()), 12 * 3 * 3)
        self.assertEqual(len(repeatability_steps()), 3 * 3 * 10)

    def test_ascii_gauge_parser_uses_final_number(self):
        self.assertAlmostEqual(parse_gauge_value("ST,NT,+0012.345 N"), 12.345)
        self.assertAlmostEqual(parse_gauge_value("value=-2.5e-2"), -0.025)
        with self.assertRaises(ValueError):
            parse_gauge_value("OVERLOAD")

    def test_session_creation_immediately_creates_recoverable_csv_files(self):
        with tempfile.TemporaryDirectory() as folder:
            dialog = CalibrationAcquisitionDialog()
            dialog.device_sn.setText("H3-TEST")
            dialog.output_dir.setText(folder)
            dialog._start_session()
            self.assertTrue(dialog.session_started)
            self.assertTrue(Path(dialog.detail_path).is_file())
            self.assertTrue(Path(dialog.summary_path).is_file())
            dialog.close()


if __name__ == "__main__":
    unittest.main()
