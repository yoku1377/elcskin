import os
import time
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PyQt5 import QtWidgets

from main import (
    ForceField, Monitor, PAIRED_DATA_TIMEOUT_S, RAW_TARE_MODE,
    chamfered_side_mesh, merge_static_meshes, sensor_label_texture, spatial_map_for_total,
)
from no8_raw_tare_core import PairedFrame


class ProductMonitorV45Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = Monitor()
        self.window.timeout_timer.stop()

    def tearDown(self):
        self.window.close()

    @staticmethod
    def frame(device_ms, raw=None, force=None, received_at=None):
        return PairedFrame(
            device_ms=device_ms,
            received_at=time.time() if received_at is None else received_at,
            raw=np.zeros(24, dtype=float) if raw is None else np.asarray(raw, dtype=float),
            force=np.zeros(3, dtype=float) if force is None else np.asarray(force, dtype=float),
        )

    def establish_tare(self):
        base_time = time.time()-1.9
        for index in range(20):
            raw = np.full(24, index % 2, dtype=float)
            self.window._on_paired(self.frame(index, raw=raw, received_at=base_time + index * 0.1))
        self.window._tare()

    def test_raw_mode_requires_tare_before_recording(self):
        self.window.force_mode_box.setCurrentText(RAW_TARE_MODE)
        self.window._on_paired(self.frame(1))
        self.window._start_recording()
        self.assertFalse(self.window.recording)

    def test_one_record_is_written_for_each_paired_frame(self):
        self.establish_tare()
        self.window._start_recording()
        loaded = np.full(24, 5000.0, dtype=float)
        self.window._on_paired(self.frame(100, raw=loaded, force=[0.0, 0.0, 5.0]))
        self.assertEqual(len(self.window.records), 1)
        self.assertEqual(self.window.records[0][0], "PAIRED")
        self.assertEqual(self.window.records[0][2], 100)

    def test_high_force_candidate_can_be_disabled_and_is_recorded(self):
        self.assertTrue(self.window.provisional_high_force_enabled)
        self.window.high_force_correction_box.setChecked(False)
        self.assertFalse(self.window.provisional_high_force_enabled)
        row = self.window._record_base("MARK", 0, time.time())
        self.assertTrue(row[6].split("|", 1)[0].endswith("_CORRECTION_DISABLED"))

        self.window.recording = True
        self.window.high_force_correction_box.setChecked(True)
        self.assertFalse(self.window.provisional_high_force_enabled)
        self.assertFalse(self.window.high_force_correction_box.isChecked())

    def test_timeout_clears_old_force(self):
        self.establish_tare()
        self.window._on_paired(self.frame(200, force=[0.0, 0.0, 5.0]))
        self.window.device_fz = 5.0
        self.window.front_fz = 3.0
        self.window.back_fz = 2.0
        self.window.last_paired_at = time.time() - PAIRED_DATA_TIMEOUT_S - 0.1
        self.window.reader = object()
        self.window._check_data_timeout()
        self.window.reader = None
        self.assertTrue(self.window.data_stale)
        self.assertEqual(self.window.device_fz, 0.0)
        self.assertEqual(self.window.front_fz, 0.0)
        self.assertEqual(self.window.back_fz, 0.0)

    def test_physical_map_has_eight_fixed_sensor_positions(self):
        raw = np.zeros(24, dtype=float)
        raw[3:6] = [3000.0, 0.0, 0.0]
        force_map, _source = spatial_map_for_total(
            raw, np.zeros(24, dtype=float), 8.0, np.full(8, 20.0)
        )
        self.assertEqual(force_map.shape, (4, 2, 3))
        self.assertAlmostEqual(float(force_map[..., 2].sum()), 8.0, places=6)
        self.assertEqual(int(np.argmax(force_map[..., 2].reshape(-1))), 1)

    def test_spatial_deck_atlas_has_expected_texture_layout(self):
        centers = [(x, y) for y in (1.5, 0.5, -0.5, -1.5) for x in (-0.72, 0.72)]
        texture = sensor_label_texture(centers)
        self.assertEqual(texture.shape, (512, 1024, 4))
        self.assertEqual(texture.dtype, np.uint8)
        self.assertTrue(texture.flags.c_contiguous)

    def test_continuous_deck_keeps_eight_physical_hall_positions(self):
        view = self.window.unified_panel.view
        expected = np.asarray([[x, -0.28, y] for y in (1.2, 0.4, -0.4, -1.2)
                               for x in (-0.42, 0.42)])
        np.testing.assert_allclose(np.asarray(view.positions), expected)
        self.assertFalse(hasattr(view, "arrows"))
        self.assertFalse(hasattr(view, "nodes"))
        self.assertEqual(view.point_cloud.weights.shape[1], 8)
        self.assertEqual(len(view.point_cloud.positions), 34 * 72)
        self.assertFalse(hasattr(view, "sensor_labels"))
        self.assertEqual(view.opts["azimuth"], 242)
        self.assertFalse(hasattr(view, "front_label"))
        self.assertFalse(hasattr(view, "rear_label"))

    def test_zero_force_clears_h22_point_cloud_load(self):
        view = self.window.unified_panel.view
        view._display_zones[0] = 5.0
        view._render_field()
        self.assertGreater(float(np.max(-view.point_cloud.positions[:, 1] -
                                        view.point_cloud.base_z)), 0.0)
        view._display_zones[:] = 0.0
        view._render_field()
        np.testing.assert_allclose(-view.point_cloud.positions[:, 1],
                                   view.point_cloud.base_z)

    def test_directional_side_tones_survive_static_mesh_batching(self):
        meshes = [chamfered_side_mesh(x, 0, 0.49, 0.35, 0.08, 0.031, 0.048,
                                       base_color=(0.17, 0.21, 0.23, 1.0)) for x in (-0.72, 0.72)]
        combined = merge_static_meshes(meshes)
        self.assertEqual(combined.faceCount(), 32)
        self.assertEqual(combined.faceColors().shape, (32, 4))
        self.assertGreater(float(np.ptp(combined.faceColors()[:, 0])), 0.01)

    def test_legacy_firmware_fz_does_not_override_calibrated_host_animation(self):
        self.establish_tare()
        self.window._on_paired(self.frame(300, raw=np.zeros(24), force=[0.0, 0.0, 4.0]))
        self.assertAlmostEqual(float(self.window.reconstructed_force_map[..., 2].sum()), 0.0, places=5)

    def test_60hz_field_spring_is_monotonic_and_returns_to_zero(self):
        value = 0.0
        velocity = 0.0
        rising = []
        for _ in range(60):
            value, velocity = ForceField._spring_step(value, velocity, 1.0, 1.0 / 60.0, 0.075)
            rising.append(value)
        self.assertTrue(all(left <= right for left, right in zip(rising, rising[1:])))
        self.assertLessEqual(max(rising), 1.000001)
        self.assertGreater(value, 0.999)

        falling = []
        for _ in range(90):
            value, velocity = ForceField._spring_step(value, velocity, 0.0, 1.0 / 60.0, 0.120)
            falling.append(value)
        self.assertTrue(all(left >= right for left, right in zip(falling, falling[1:])))
        self.assertGreaterEqual(min(falling), -0.000001)
        self.assertLess(value, 0.001)


if __name__ == "__main__":
    unittest.main()
