"""Selected-force residual suppression is shared, auditable and reversible."""
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtWidgets

import main
from baseline_quality import BASELINE_CSV_COLUMNS
from h3_v3_3_force_model import REGION_PROTOTYPES
from no8_raw_tare_core import AcquisitionDiagnostic, PairedFrame, evaluate_raw_force
from residual_zero import MAX_RESIDUAL_N


class ResidualZeroMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment=mock.patch.dict(os.environ,TACAI_FORCE_TRACE_DIR=str(Path(self.temp.name)/'trace'))
        environment.start();self.addCleanup(environment.stop)
        self.now=1700000000.;self.device_ms=0
        clock=mock.patch('main.time.time',side_effect=lambda:self.now)
        clock.start();self.addCleanup(clock.stop)
        self.w=main.Monitor();self.w.timeout_timer.stop();self.w.reader=None
        self.addCleanup(self.w.close)

    def feed(self,raw,count=1,*,interval=.1,diagnostic=True,valid=True,epoch=1):
        for _ in range(count):
            self.now+=interval;self.device_ms+=round(interval*1000)
            diag=(AcquisitionDiagnostic(self.device_ms,self.now,'rxirq_v5',255 if valid else 254,0,epoch)
                  if diagnostic else None)
            frame=PairedFrame(self.device_ms,self.now,np.asarray(raw,float).copy(),np.zeros(3),diag)
            self.w._on_paired(frame)
        return frame

    def prime_tare(self,*,diagnostic=True):
        self.feed(np.zeros(24),21,diagnostic=diagnostic)
        self.w._tare()
        self.assertIsNotNone(self.w.tare_raw)
        return self.w.tare_raw.copy()

    def press_release(self,*,diagnostic=True):
        self.prime_tare(diagnostic=diagnostic)
        self.feed(REGION_PROTOTYPES[2]*20000.,3,diagnostic=diagnostic)
        frame=self.feed(REGION_PROTOTYPES[2]*10000.,12,diagnostic=diagnostic)
        self.assertTrue(self.w._current_release_zero_result().applied)
        return frame

    def test_uart_ch347_ch341_share_selected_force_suppression_and_preserve_evidence(self):
        for transport in (0,1,2):
            with self.subTest(transport=transport):
                self.w.transport_box.setCurrentIndex(transport)
                self.w._invalidate_host_tare()
                frame=self.press_release()
                original=evaluate_raw_force(frame.raw,self.w.tare_raw).fz_n
                self.assertGreater(original,.05)
                self.assertEqual(self.w.device_fz,0.)
                self.assertEqual(self.w.model_raw_fz,original)
                np.testing.assert_array_equal(self.w.raw,frame.raw)
                np.testing.assert_array_equal(self.w.tare_raw,np.zeros(24))
                np.testing.assert_array_equal(self.w.numeric_zone_forces,np.zeros(8))
                np.testing.assert_array_equal(self.w.last_zones,np.zeros(8))
                np.testing.assert_array_equal(self.w.reconstructed_force_map,np.zeros((4,2,3)))
                self.assertEqual((self.w.front_fz,self.w.back_fz),(0.,0.))
                self.assertIn('RELEASE_RESIDUAL_ZERO',self.w.quality_flags)
                self.assertIn('松手低力段已软件归零',self.w.baseline_quality_label.text())
                sample=self.w.force_trace.frames[-1]
                self.assertEqual(sample['selected_force_n'],0.)
                self.assertEqual(sample['original_model_force_n'],original)
                self.assertEqual(sample['release_zero_input_force_n'],original)
                self.assertTrue(sample['release_zero_enabled'])
                self.assertTrue(sample['release_zero_applied'])
                self.assertEqual(sample['release_zero_threshold_n'],2.)
                self.assertEqual(sample['raw24'],frame.raw.tolist())

    def test_legacy_uart_without_diag_uses_accepted_frames_and_legacy_epoch(self):
        self.press_release(diagnostic=False)
        self.assertEqual(self.w.device_fz,0.)
        self.assertIn('LEGACY_DIAG_UNAVAILABLE',self.w.release_zero_sample_key[2])
        self.assertGreater(self.w.model_raw_fz,.05)

    def test_long_held_press_and_small_load_without_prior_peak_are_not_zeroed(self):
        self.prime_tare()
        tare=self.w.tare_raw.copy();epoch=self.w.host_tare_epoch
        held=REGION_PROTOTYPES[2]*20000.
        expected=evaluate_raw_force(held,tare).fz_n
        self.feed(held,180,interval=.2)
        self.assertGreater(expected,MAX_RESIDUAL_N)
        self.assertEqual(self.w.device_fz,expected)
        self.assertFalse(self.w._current_release_zero_result().applied)
        np.testing.assert_array_equal(self.w.tare_raw,tare)
        self.assertEqual(self.w.host_tare_epoch,epoch)
        self.w._reset_local_force('NEW_INTERACTION')
        low=REGION_PROTOTYPES[2]*10000.
        expected=evaluate_raw_force(low,tare).fz_n
        self.feed(low,80,interval=.2)
        self.assertGreater(expected,.05)
        self.assertEqual(self.w.device_fz,expected)
        self.assertFalse(self.w._current_release_zero_result().applied)

    def test_checkbox_off_restores_current_original_value_without_another_feed(self):
        self.press_release()
        original=self.w.model_raw_fz
        with mock.patch.object(self.w.release_zero_filter,'feed',wraps=self.w.release_zero_filter.feed) as feed:
            self.w.release_zero_box.setChecked(False)
            feed.assert_not_called()
            self.assertFalse(self.w.release_zero_enabled)
            self.assertEqual(self.w.device_fz,original)
            self.assertNotIn('RELEASE_RESIDUAL_ZERO',self.w.quality_flags)
            self.w.release_zero_box.setChecked(True)
            feed.assert_not_called()
        self.feed(self.w.raw,15)
        self.assertEqual(self.w.device_fz,original)
        self.assertFalse(self.w._current_release_zero_result().applied)

    def test_redisplay_only_reads_result_and_never_advances_filter_or_records(self):
        frame=self.press_release()
        trace_count=len(self.w.force_trace)
        record_count=len(self.w.records)
        with mock.patch.object(self.w.release_zero_filter,'feed',wraps=self.w.release_zero_filter.feed) as feed:
            for _ in range(5):
                self.w._update_force_display()
                self.w._render(frame.device_ms)
            self.w._apply_residual_zero(frame)
            feed.assert_not_called()
        self.assertEqual(self.w.device_fz,0.)
        self.assertEqual(len(self.w.force_trace),trace_count)
        self.assertEqual(len(self.w.records),record_count)

    def test_suppressed_low_force_noise_stays_zero_and_keeps_each_original_value(self):
        self.press_release()
        raw=self.w.raw.copy()
        estimate=evaluate_raw_force(raw,self.w.tare_raw)
        for force in (.2,1.74,.45,1.5,.3,1.7):
            with mock.patch('main.evaluate_raw_force',return_value=replace(estimate,fz_n=force)):
                self.feed(raw)
            self.assertEqual(self.w.device_fz,0.)
            self.assertEqual(self.w.model_raw_fz,force)
            np.testing.assert_array_equal(self.w.last_zones,np.zeros(8))
            self.assertTrue(self.w._current_release_zero_result().applied)
            self.assertEqual(self.w.force_trace.frames[-1]['original_model_force_n'],force)
            self.assertEqual(self.w.force_trace.frames[-1]['selected_force_n'],0.)

    def test_recording_or_active_protocol_blocks_checkbox_changes(self):
        for kind in ('recording','protocol'):
            with self.subTest(kind=kind):
                self.w.recording=kind=='recording';self.w.protocol.active=kind=='protocol'
                with mock.patch.object(self.w.release_zero_filter,'reset') as reset:
                    self.w.release_zero_box.setChecked(False)
                    reset.assert_not_called()
                self.assertTrue(self.w.release_zero_enabled)
                self.assertTrue(self.w.release_zero_box.isChecked())
                self.w.recording=False;self.w.protocol.active=False

    def test_new_raw_response_exits_zero_and_old_peak_cannot_erase_new_small_load(self):
        self.press_release()
        touched=REGION_PROTOTYPES[2]*10500.
        self.feed(touched,15)
        self.assertGreater(self.w.device_fz,.05)
        self.assertFalse(self.w._current_release_zero_result().applied)
        self.assertEqual(self.w.device_fz,self.w.model_raw_fz)

    def test_gap_and_invalid_diagnostic_drop_previous_suppression(self):
        for fault in ('gap','invalid'):
            with self.subTest(fault=fault):
                self.w._invalidate_host_tare()
                self.press_release()
                if fault=='gap':
                    self.feed(self.w.raw,interval=.5)
                else:
                    raw=self.w.raw.copy()
                    self.feed(raw,valid=False)
                    self.assertIsNone(self.w.release_zero_result)
                    self.feed(raw)
                self.assertGreater(self.w.device_fz,.05)
                self.assertFalse(self.w._current_release_zero_result().applied)

    def test_successful_tare_resets_filter_and_warm_history_never_advances_it(self):
        self.press_release()
        self.feed(self.w.raw,21)
        with mock.patch.object(self.w.release_zero_filter,'feed',wraps=self.w.release_zero_filter.feed) as feed:
            self.w._tare();feed.assert_not_called()
        self.assertIsNone(self.w.release_zero_result)
        self.assertEqual(self.w.release_zero_filter.peak_force_n,0.)
        self.assertEqual(self.w.device_fz,0.)

    def test_csv_only_appends_four_fields_and_nonmeasurement_events_are_blank(self):
        frame=self.press_release()
        header=self.w._csv_header()
        self.assertEqual(header[-4:],main.RELEASE_ZERO_CSV_COLUMNS)
        self.assertEqual(header[-9:-4],BASELINE_CSV_COLUMNS)
        row=self.w._record_base('PAIRED',frame.device_ms,frame.received_at)
        self.assertEqual(len(row),len(header))
        named=dict(zip(header,row))
        self.assertEqual(row[-4:],[1,1,'SOFTWARE_RESIDUAL_ZERO',2.])
        self.assertGreater(named['scalar_resultant_raw_model_n'],.05)
        self.assertEqual(named['selected_resultant_or_diagnostic_n'],0.)
        self.assertEqual(named['S1_raw_dBx'],frame.raw[0])
        for event in ('TARE_CHANGED','STREAM_ERROR','DATA_TIMEOUT','DIAG_INVALID','RAW_NO_TARE'):
            event_row=self.w._record_base(event,frame.device_ms,frame.received_at)
            self.assertEqual(len(event_row),len(header))
            self.assertEqual(event_row[-4:],['']*4,event)


if __name__=='__main__':
    unittest.main()
