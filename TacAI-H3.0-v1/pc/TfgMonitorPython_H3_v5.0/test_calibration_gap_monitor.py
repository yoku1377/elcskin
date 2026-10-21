"""Recoverable SPI gaps must not discard an active calibration session."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
from PyQt5 import QtWidgets

import main
from no8_raw_tare_core import AcquisitionDiagnostic, PairedFrame
from spi_iap_protocol import Snapshot


class CalibrationGapMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.wall_clock = mock.patch.object(main.time, "time", return_value=100.4)
        self.wall_clock.start()
        self.addCleanup(self.wall_clock.stop)

    def make_window(self):
        window = main.Monitor()
        window._save_force_trace = mock.Mock()
        window.last_paired_at = 100.
        window.last_device_ms = 10000
        window.acquisition_diagnostic = self.frame().diagnostic
        window.tare_raw = np.arange(24, dtype=float)
        reference_epoch=window._raw_baseline_reference_epoch(window.acquisition_diagnostic)
        tracker=window.raw_baseline_tracker
        tracker.feed(window.tare_raw+100.,4.,98.,manual_tare24=window.tare_raw,epoch=reference_epoch)
        for i in range(12):
            tracker.feed(window.tare_raw+.5,1.,98.1+i*.1,
                         manual_tare24=window.tare_raw,epoch=reference_epoch)
        np.testing.assert_allclose(tracker.offset_raw24,np.full(24,.5))
        window.data_stale = False
        dialog = mock.Mock()
        dialog.session_started = True
        dialog.capturing = True
        dialog.stream_paused = False
        dialog.isVisible.return_value = True
        def pause(_message):
            dialog.stream_paused = True
            dialog.capturing = False
        def resume():
            dialog.stream_paused = False
        def finish(*, stopped):
            dialog.session_started = False
            dialog.stream_paused = False
            dialog.capturing = False
        dialog.pause_for_stream_gap.side_effect = pause
        dialog.resume_after_stream_gap.side_effect = resume
        dialog._finish_session.side_effect = finish
        window.calibration_dialog = dialog
        self.addCleanup(window.close)
        return window, dialog

    @staticmethod
    def frame(ms=10000, received=100., *, baseline=1, firmware="test_firmware", mask=255, reset=""):
        diag = AcquisitionDiagnostic(ms, received, firmware, mask, 0, baseline, reset)
        return PairedFrame(ms, received, np.arange(24, dtype=float), np.zeros(3), diag, reset)

    def test_both_spi_readers_emit_gap_and_deduplicate_before_recovery(self):
        for reader in (main.SpiForceReader(0, 468750), main.CH341ForceReader(0)):
            with self.subTest(adapter=reader.bridge_name), tempfile.TemporaryDirectory() as directory:
                first = Snapshot(1, 3, 100, 1, 0, 1, 255, "firmware", tuple(range(24)), (0., 0., 1.))
                new = Snapshot(1, 3, 300, 2, 0, 1, 255, "firmware", tuple(range(24)), (0., 0., 1.))
                ordered, faults = [], []
                reader.paired_received.connect(lambda frame: ordered.append(frame.device_ms))
                reader.stream_gap.connect(lambda *_args: ordered.append("gap"))
                reader.stream_fault.connect(lambda *_args: faults.append("fatal"))
                calls = []
                def snapshot(**_kwargs):
                    calls.append(1)
                    if len(calls) == 1:
                        return first
                    if len(calls) == 2:
                        reader._snapshot_trace(dict(event="snapshot_retry", old_sequence=1, next_sequence=3))
                        return first
                    self.assertTrue(reader._spi_recovering)
                    reader.stop()
                    return new
                with mock.patch.dict(os.environ, {"TACAI_STREAM_LOG_DIR": directory}), \
                     mock.patch.object(reader, "_open_spi") as opened, \
                     mock.patch.object(reader, "msleep"), \
                     mock.patch.object(main, "SPIIapClient") as client:
                    client.return_value.snapshot.side_effect = snapshot
                    reader.run()
                    opened.assert_called_once()
                self.assertEqual(ordered, [100, "gap", 300])
                self.assertEqual(faults, [])

    def test_gap_keeps_tare_offset_history_and_records_unavailable_event(self):
        window, dialog = self.make_window()
        window.recording = True
        tare = window.tare_raw.copy()
        offset = window.raw_baseline_tracker.offset_raw24.copy()
        runtime = window.local_force_runtime
        state_epoch = runtime.state_epoch
        with mock.patch.object(runtime, "reset") as reset:
            window._on_stream_gap("old sequence", "", 100.1)
            reset.assert_not_called()
        dialog.pause_for_stream_gap.assert_called_once()
        dialog._finish_session.assert_not_called()
        self.assertTrue(dialog.session_started)
        self.assertTrue(window.data_stale)
        self.assertEqual(window.total_readout.text(), "— N")
        np.testing.assert_array_equal(window.tare_raw, tare)
        np.testing.assert_array_equal(window.raw_baseline_tracker.offset_raw24, offset)
        self.assertEqual(runtime.state_epoch, state_epoch)
        row = window.records[-1]
        self.assertEqual(row[0], "STREAM_GAP")
        self.assertEqual(row[3], "")
        self.assertEqual(row[46:94], [""] * 48)

    def test_resume_only_on_fresh_same_epoch_pair_then_wait_for_manual_recapture(self):
        window, dialog = self.make_window()
        window._on_stream_gap("old sequence", "", 100.1)
        window._on_paired(self.frame())
        dialog.resume_after_stream_gap.assert_not_called()
        self.assertTrue(window.data_stale)
        window._on_paired(self.frame(10400, 100.4))
        dialog.resume_after_stream_gap.assert_called_once()
        self.assertFalse(dialog.capturing)
        self.assertTrue(dialog.session_started)
        self.assertFalse(window.data_stale)
        self.assertIsNone(window._stream_gap_context)

    def test_epoch_reset_invalid_and_slow_recovery_end_session(self):
        cases = dict(
            baseline=self.frame(10400, 100.4, baseline=2),
            firmware=self.frame(10400, 100.4, firmware="new"),
            clock_reset=self.frame(50, 100.4, reset="DEVICE_CLOCK_RESET"),
            invalid=self.frame(10400, 100.4, mask=127),
            slow=self.frame(11500, 101.5),
        )
        for name, frame in cases.items():
            with self.subTest(case=name):
                window, dialog = self.make_window()
                window._on_stream_gap("old sequence", "", 100.1)
                window._on_paired(frame)
                dialog._finish_session.assert_called_once_with(stopped=True)
                dialog.resume_after_stream_gap.assert_not_called()
                self.assertIsNone(window._stream_gap_context)

    def test_timeout_ends_session_even_while_gap_display_already_stale(self):
        window, dialog = self.make_window()
        window.reader = mock.Mock()
        self.addCleanup(setattr, window, "reader", None)
        window._on_stream_gap("old sequence", "", 100.1)
        with mock.patch.object(main.time, "time", return_value=101.5):
            window._check_data_timeout()
        dialog._finish_session.assert_called_once_with(stopped=True)
        self.assertIsNone(window._stream_gap_context)

    def test_queued_frame_cannot_resume_after_real_wall_clock_deadline(self):
        window, dialog = self.make_window()
        window._on_stream_gap("old sequence", "", 100.1)
        with mock.patch.object(main.time, "time", return_value=101.5):
            window._on_paired(self.frame(10400, 100.4))
        dialog._finish_session.assert_called_once_with(stopped=True)
        dialog.resume_after_stream_gap.assert_not_called()
        self.assertTrue(window.data_stale)
        self.assertEqual(window.acquisition_diag_status, "TIMEOUT")

    def test_fatal_fault_ends_paused_session(self):
        window, dialog = self.make_window()
        window._on_stream_gap("old sequence", "", 100.1)
        window._on_stream_fault("USB lost", "", 100.3)
        dialog._finish_session.assert_called_once_with(stopped=True)
        self.assertFalse(dialog.session_started)

    def test_whole_calibration_session_freezes_raw_offset_and_settings(self):
        window, dialog = self.make_window()
        for capturing, paused in ((True, False), (False, True), (False, False)):
            with self.subTest(capturing=capturing, paused=paused):
                dialog.capturing, dialog.stream_paused = capturing, paused
                self.assertFalse(window._raw_baseline_updates_allowed())
                tare, offset = window.tare_raw.copy(), window.raw_baseline_tracker.offset_raw24.copy()
                high_force = window.provisional_high_force_enabled
                release = window.release_zero_enabled
                selection = window.local_force_selection_index
                force_mode = window.active_force_mode
                window._tare()
                window._on_release_zero_changed(not release)
                window._on_high_force_correction_changed(not high_force)
                window._on_local_force_changed(0 if selection else 1)
                window._on_force_mode_changed(main.FIRMWARE_COMPAT_MODE)
                np.testing.assert_array_equal(window.tare_raw, tare)
                np.testing.assert_array_equal(window.raw_baseline_tracker.offset_raw24, offset)
                self.assertEqual(window.release_zero_enabled, release)
                self.assertEqual(window.provisional_high_force_enabled, high_force)
                self.assertEqual(window.local_force_selection_index, selection)
                self.assertEqual(window.active_force_mode, force_mode)


if __name__ == "__main__":
    unittest.main()
