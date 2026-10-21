"""Working RAW rebasing preserves nonlinear measurements and replay coordinates."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5 import QtWidgets

import main
from h3_v3_3_force_model import REGION_PROTOTYPES
from no8_raw_tare_core import AcquisitionDiagnostic, PairedFrame, evaluate_raw_force


class ReleaseBaselineMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        environment=mock.patch.dict(os.environ,TACAI_FORCE_TRACE_DIR=str(Path(self.temp.name)/'trace'))
        environment.start();self.addCleanup(environment.stop)
        self.now=1700000000.;self.device_ms=0
        clock=mock.patch('main.time.time',side_effect=lambda:self.now)
        clock.start();self.addCleanup(clock.stop)
        self.w=main.Monitor();self.w.timeout_timer.stop();self.w.reader=None
        self.addCleanup(self.w.close)

    def feed(self,raw,count=1,*,interval=.1,diagnostic=True,valid=True,baseline_epoch=1):
        for _ in range(count):
            self.now+=interval;self.device_ms+=round(interval*1000)
            diag=(AcquisitionDiagnostic(self.device_ms,self.now,'rxirq_v5',255 if valid else 254,0,baseline_epoch)
                  if diagnostic else None)
            frame=PairedFrame(self.device_ms,self.now,np.asarray(raw,float).copy(),np.zeros(3),diag)
            self.w._on_paired(frame)
        return frame

    def prime_tare(self,*,diagnostic=True):
        self.feed(np.zeros(24),21,diagnostic=diagnostic)
        self.w._tare()
        self.assertIsNotNone(self.w.tare_raw)
        return self.w.tare_raw.copy()

    def commit_release(self,*,diagnostic=True):
        self.prime_tare(diagnostic=diagnostic)
        self.feed(REGION_PROTOTYPES[2]*20000.,3,diagnostic=diagnostic)
        before=self.w.raw_baseline_tracker.baseline_epoch
        return self.finish_release(REGION_PROTOTYPES[2]*10000.,before,diagnostic=diagnostic)

    def finish_release(self,raw,before,*,diagnostic=True):
        for _ in range(15):
            frame=self.feed(raw,diagnostic=diagnostic)
            if self.w.raw_baseline_tracker.baseline_epoch!=before:
                self.assertTrue(self.w._current_raw_baseline_result().changed)
                return frame
        self.fail('The real, stable release trajectory did not commit a RAW work reference')

    def test_actual_nonlinear_model_uses_new_raw_reference_for_next_small_load(self):
        frame=self.commit_release()
        offset=frame.raw.copy();manual=self.w.tare_raw.copy();host_epoch=self.w.host_tare_epoch
        self.assertEqual(self.w.device_fz,0.)
        self.assertEqual(self.w.model_raw_fz,0.)
        self.assertGreater(self.w.manual_reference_model_n,.05)
        np.testing.assert_allclose(self.w.raw_baseline_tracker.offset_raw24,offset)
        np.testing.assert_array_equal(self.w.tare_raw,manual)
        local=self.w._current_local_force_result()
        self.assertEqual(local.evidence['baseline_quality']['status'],'READY')
        self.assertFalse(local.evidence['baseline_quality']['physical_zero_confirmed'])
        self.assertIn('RELEASE_BASELINE_UPDATED',self.w.quality_flags)
        delta=REGION_PROTOTYPES[2]*10000.
        expected=evaluate_raw_force(delta,np.zeros(24))
        self.assertGreater(expected.fz_n,.05);self.assertLess(expected.fz_n,2.)
        frame=self.feed(offset+delta)
        self.assertAlmostEqual(self.w.model_raw_fz,expected.fz_n,places=12)
        self.assertAlmostEqual(self.w.device_fz,expected.fz_n,places=12)
        self.assertGreater(self.w.device_fz,0.)  # No scalar <=2 N clipping remains.
        self.assertAlmostEqual(self.w.model_load_score,expected.load_score,places=12)
        self.assertEqual(self.w.model_gate_reason,expected.gate_reason)
        np.testing.assert_allclose(self.w._current_local_force_result().original_delta24,delta,atol=1e-10)
        self.assertAlmostEqual(self.w.numeric_zone_forces.sum(),expected.fz_n)
        self.assertGreater(np.max(self.w.numeric_zone_forces),0.)
        self.assertEqual(self.w.host_tare_epoch,host_epoch)
        np.testing.assert_array_equal(self.w.tare_raw,manual)
        self.assertIn('RELEASE_BASELINE_COMPENSATED',self.w.quality_flags)
        self.assertNotIn('RELEASE_RESIDUAL_ZERO',self.w.quality_flags)

    def test_uart_ch347_ch341_and_legacy_uart_use_the_same_raw_coordinate_path(self):
        for transport,diagnostic in ((0,True),(1,True),(2,True),(0,False)):
            with self.subTest(transport=transport,diagnostic=diagnostic):
                self.w.transport_box.setCurrentIndex(transport)
                self.w._invalidate_host_tare()
                frame=self.commit_release(diagnostic=diagnostic)
                offset=frame.raw.copy()
                delta=REGION_PROTOTYPES[2]*10000.
                self.feed(offset+delta,diagnostic=diagnostic)
                expected=evaluate_raw_force(delta,np.zeros(24)).fz_n
                self.assertAlmostEqual(self.w.device_fz,expected,places=12)
                self.assertGreater(self.w.device_fz,.05)

    def test_second_press_keeps_the_offset_and_next_release_does_not_add_it_twice(self):
        frame=self.commit_release();offset=frame.raw.copy()
        host_epoch=self.w.host_tare_epoch
        expected=evaluate_raw_force(REGION_PROTOTYPES[2]*20000.,np.zeros(24)).fz_n
        self.feed(offset+REGION_PROTOTYPES[2]*20000.,12)
        np.testing.assert_allclose(self.w.raw_baseline_tracker.offset_raw24,offset)
        self.assertAlmostEqual(self.w.device_fz,expected,places=12)
        before=self.w.raw_baseline_tracker.baseline_epoch
        self.finish_release(offset,before)
        np.testing.assert_allclose(self.w.raw_baseline_tracker.offset_raw24,offset)
        self.assertEqual(self.w.device_fz,0.)
        self.assertEqual(self.w.host_tare_epoch,host_epoch)

    def test_held_load_and_low_force_without_prior_release_do_not_move_the_zero(self):
        self.prime_tare()
        manual=self.w.tare_raw.copy();host_epoch=self.w.host_tare_epoch
        high=REGION_PROTOTYPES[2]*20000.
        self.feed(high,150,interval=.2)
        self.assertGreater(self.w.device_fz,2.)
        np.testing.assert_array_equal(self.w.raw_baseline_tracker.offset_raw24,np.zeros(24))
        self.w._reset_local_force('NEW_TEST_INTERACTION')
        low=REGION_PROTOTYPES[2]*10000.
        self.feed(low,100,interval=.2)
        self.assertAlmostEqual(self.w.device_fz,evaluate_raw_force(low,manual).fz_n)
        self.assertGreater(self.w.device_fz,.05)
        np.testing.assert_array_equal(self.w.raw_baseline_tracker.offset_raw24,np.zeros(24))
        np.testing.assert_array_equal(self.w.tare_raw,manual)
        self.assertEqual(self.w.host_tare_epoch,host_epoch)

    def test_commit_warm_start_uses_real_frames_and_current_local_feed_occurs_once(self):
        self.prime_tare()
        self.feed(REGION_PROTOTYPES[2]*20000.,3)
        before=self.w.raw_baseline_tracker.baseline_epoch
        with mock.patch.object(self.w.local_force_runtime,'feed',wraps=self.w.local_force_runtime.feed) as feed:
            frame=self.finish_release(REGION_PROTOTYPES[2]*10000.,before)
        current=[call for call in feed.call_args_list if call.kwargs['source_token']==frame.device_ms]
        self.assertEqual(len(current),1)
        np.testing.assert_allclose(current[0].args[1],self.w._effective_tare_raw())
        evidence=self.w._current_local_force_result().evidence
        self.assertEqual(evidence['baseline_quality']['status'],'READY')
        self.assertGreaterEqual(evidence['baseline_quality']['span_s'],1.-1e-6)
        self.assertEqual(evidence['acquisition_epoch'][-1],self.w.raw_baseline_tracker.baseline_epoch)
        committed=dict(self.w.raw_baseline_tracker.last_commit_frames)
        matches={f.device_ms:f for f in self.w.paired_frames if f.received_at in committed}
        for call in feed.call_args_list:
            if np.array_equal(call.args[1],self.w._effective_tare_raw()):
                source=matches[call.kwargs['source_token']]
                self.assertEqual(call.args[4],source.received_at)
                self.assertEqual(call.args[3],evaluate_raw_force(source.raw,self.w._effective_tare_raw()).fz_n)

    def test_ui_redisplay_never_feeds_tracker_or_local_history(self):
        frame=self.commit_release()
        offset=self.w.raw_baseline_tracker.offset_raw24.copy()
        epoch=self.w.raw_baseline_tracker.baseline_epoch
        count=len(self.w.force_trace)
        with mock.patch.object(self.w.raw_baseline_tracker,'feed',wraps=self.w.raw_baseline_tracker.feed) as tracker, \
             mock.patch.object(self.w.local_force_runtime,'feed',wraps=self.w.local_force_runtime.feed) as local:
            for _ in range(5):
                self.w._update_force_display();self.w._render(frame.device_ms)
            tracker.assert_not_called();local.assert_not_called()
        np.testing.assert_array_equal(self.w.raw_baseline_tracker.offset_raw24,offset)
        self.assertEqual(self.w.raw_baseline_tracker.baseline_epoch,epoch)
        self.assertEqual(len(self.w.force_trace),count)

    def test_manual_tare_and_disabled_feature_restore_explicit_manual_coordinates(self):
        self.commit_release();manual=self.w.tare_raw.copy()
        expected=evaluate_raw_force(self.w.raw,manual).fz_n
        with mock.patch.object(self.w.raw_baseline_tracker,'feed',wraps=self.w.raw_baseline_tracker.feed) as feed:
            self.w.release_zero_box.setChecked(False);feed.assert_not_called()
        self.assertFalse(self.w.release_zero_enabled)
        np.testing.assert_array_equal(self.w.raw_baseline_tracker.offset_raw24,np.zeros(24))
        self.assertAlmostEqual(self.w.device_fz,expected)
        self.assertGreater(self.w.device_fz,.05)
        self.w.release_zero_box.setChecked(True)
        self.feed(self.w.raw,21)
        self.assertAlmostEqual(self.w.device_fz,expected)  # A new cycle needs a new press.
        old_epoch=self.w.host_tare_epoch;new_manual=self.w.raw.copy()
        with mock.patch.object(self.w.raw_baseline_tracker,'feed',wraps=self.w.raw_baseline_tracker.feed) as feed:
            self.w._tare();feed.assert_not_called()
        self.assertEqual(self.w.host_tare_epoch,old_epoch+1)
        np.testing.assert_array_equal(self.w.tare_raw,new_manual)
        np.testing.assert_array_equal(self.w.raw_baseline_tracker.offset_raw24,np.zeros(24))
        self.assertEqual(self.w.device_fz,0.)

    def test_gap_invalid_frame_and_stream_fault_preserve_offset_but_explicit_invalidation_clears_it(self):
        self.commit_release();offset=self.w.raw_baseline_tracker.offset_raw24.copy()
        low=offset+REGION_PROTOTYPES[2]*10000.
        for kind in ('gap','invalid','stream_fault'):
            with self.subTest(kind=kind):
                if kind=='gap':self.feed(low,interval=.5)
                elif kind=='invalid':self.feed(low,valid=False)
                else:self.w._on_stream_fault('test gap','',self.now)
                np.testing.assert_array_equal(self.w.raw_baseline_tracker.offset_raw24,offset)
                self.feed(low)
                self.assertGreater(self.w.device_fz,.05)
                self.assertAlmostEqual(self.w.device_fz,evaluate_raw_force(low,offset).fz_n)
        self.w._invalidate_host_tare()
        self.assertIsNone(self.w.tare_raw)
        np.testing.assert_array_equal(self.w.raw_baseline_tracker.offset_raw24,np.zeros(24))

    def test_record_protocol_and_calibration_freeze_updates_without_forgetting_offset(self):
        self.commit_release();offset=self.w.raw_baseline_tracker.offset_raw24.copy()
        for kind in ('record','protocol','calibration'):
            with self.subTest(kind=kind):
                self.w.recording=kind=='record';self.w.protocol.active=kind=='protocol'
                if kind=='calibration':
                    self.w.calibration_dialog=mock.Mock(capturing=True)
                    self.w.calibration_dialog.isVisible.return_value=False
                before=self.w.raw_baseline_tracker.baseline_epoch
                self.feed(offset+REGION_PROTOTYPES[2]*20000.,3)
                self.feed(offset+REGION_PROTOTYPES[2]*10000.,15)
                self.assertFalse(self.w._raw_baseline_updates_allowed())
                self.assertFalse(self.w.force_trace.frames[-1]['raw_baseline_update_allowed'])
                self.assertEqual(self.w.raw_baseline_tracker.baseline_epoch,before)
                np.testing.assert_array_equal(self.w.raw_baseline_tracker.offset_raw24,offset)
                self.assertGreater(self.w.device_fz,.05)
                self.w.recording=False;self.w.protocol.active=False;self.w.calibration_dialog=None
                self.feed(offset+REGION_PROTOTYPES[2]*10000.,15)
                self.assertEqual(self.w.raw_baseline_tracker.baseline_epoch,before)
                self.w.records=[]

    def test_capturing_calibration_gets_effective_tare_and_blocks_reference_changes(self):
        self.commit_release();offset=self.w.raw_baseline_tracker.offset_raw24.copy()
        dialog=self.w.calibration_dialog=mock.Mock(capturing=True)
        dialog.isVisible.return_value=True
        try:
            self.feed(offset+REGION_PROTOTYPES[2]*10000.)
            np.testing.assert_allclose(dialog.feed_frame.call_args.args[1],offset)
            self.assertAlmostEqual(dialog.feed_frame.call_args.args[2],self.w.model_raw_fz)
            self.w.release_zero_box.setChecked(False)
            self.assertTrue(self.w.release_zero_enabled)
            self.w._tare()
            np.testing.assert_array_equal(self.w.raw_baseline_tracker.offset_raw24,offset)
        finally:
            self.w.calibration_dialog=None

    def test_csv_and_trace_keep_manual_and_effective_coordinates_and_event_is_not_a_measurement(self):
        self.commit_release();offset=self.w.raw_baseline_tracker.offset_raw24.copy()
        delta=REGION_PROTOTYPES[2]*10000.
        frame=self.feed(offset+delta)
        header=self.w._csv_header();row=self.w._record_base('PAIRED',frame.device_ms,frame.received_at)
        self.assertEqual(header[-len(main.RAW_BASELINE_CSV_COLUMNS):],main.RAW_BASELINE_CSV_COLUMNS)
        self.assertEqual(len(row),len(header));named=dict(zip(header,row))
        self.assertEqual(named['effective_baseline_epoch'],self.w.raw_baseline_tracker.baseline_epoch)
        self.assertEqual(named['host_tare_epoch'],self.w.host_tare_epoch)
        self.assertAlmostEqual(named['manual_reference_model_n'],evaluate_raw_force(frame.raw,np.zeros(24)).fz_n)
        self.assertEqual(named['release_zero_applied'],0)
        for i in range(8):
            for j,axis in enumerate('xyz'):
                k=i*3+j;prefix=f'S{i+1}'
                self.assertEqual(named[f'{prefix}_raw_dB{axis}'],frame.raw[k])
                self.assertAlmostEqual(named[f'{prefix}_retared_dB{axis}'],delta[k])
                self.assertEqual(named[f'{prefix}_manual_tare_dB{axis}'],0.)
                self.assertEqual(named[f'{prefix}_manual_retared_dB{axis}'],frame.raw[k])
                self.assertEqual(named[f'{prefix}_release_offset_dB{axis}'],offset[k])
                self.assertAlmostEqual(named[f'{prefix}_corrected_retared_dB{axis}'],delta[k])
        sample=self.w.force_trace.frames[-1]
        self.assertEqual(sample['tare_raw24'],offset.tolist())
        self.assertEqual(sample['manual_tare_raw24'],np.zeros(24).tolist())
        np.testing.assert_allclose(sample['delta_raw24'],delta)
        self.assertEqual(sample['manual_delta_raw24'],frame.raw.tolist())
        self.assertEqual(sample['release_offset_raw24'],offset.tolist())
        self.assertGreater(sample['manual_reference_model_n'],sample['original_model_force_n'])
        self.assertFalse(sample['release_zero_applied'])
        event=dict(zip(header,self.w._record_base('RAW_BASELINE_UPDATED',frame.device_ms,frame.received_at)))
        self.assertEqual(event['selected_resultant_or_diagnostic_n'],'')
        self.assertEqual(event['S1_raw_dBx'],'')
        self.assertEqual(event['S1_retared_dBx'],'')
        self.assertEqual(event['manual_reference_model_n'],'')
        self.assertEqual(event['S1_manual_tare_dBx'],'')
        self.assertEqual(event['effective_baseline_epoch'],self.w.raw_baseline_tracker.baseline_epoch)


if __name__=='__main__':
    unittest.main()
