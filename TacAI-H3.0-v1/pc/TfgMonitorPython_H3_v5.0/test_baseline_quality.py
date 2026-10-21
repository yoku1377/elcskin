"""Quality gates reject bad inputs without inventing force truth or losing RAW."""
import json
import os
from pathlib import Path
import time
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5 import QtWidgets
from baseline_quality import DEFAULT_POLICY, POLICY_ID, evaluate_window, audit_saved_baselines, quality_text
from local_high_force import LocalHighForceRuntime, LocalForceResult, allocate_regions
from local_state import CausalRisingStateLatch
from main import Monitor
from no8_raw_tare_core import AcquisitionDiagnostic, PairedFrame, evaluate_raw_force
from h3_v3_3_force_model import REGION_PROTOTYPES


class BaselineQualityTests(unittest.TestCase):
    def test_variance_label_identifies_raw_statistic_without_changing_threshold(self):
        text = quality_text(dict(status="READY", variance_sum24=4024, variance_limit=10100))
        self.assertIn("RAW 方差 4024 / 上限 10100", text)
        self.assertIn("非力值", text)
        self.assertEqual(DEFAULT_POLICY.variance_limit, 10100)

    def test_population_variance_boundary_and_translation_invariance(self):
        t=np.arange(11)*.1;a=np.tile(np.arange(11)[:,None],(1,24)).astype(float)
        score=float(np.var(a,axis=0).sum())
        at=evaluate_window(a,t,policy=replace(DEFAULT_POLICY,variance_limit=score))
        over=evaluate_window(a,t,policy=replace(DEFAULT_POLICY,variance_limit=score-1e-6))
        self.assertEqual(at['status'],'READY');self.assertEqual(over['reason'],'BASELINE_VARIANCE_HIGH')
        self.assertFalse(at['physical_zero_confirmed'])
        self.assertAlmostEqual(evaluate_window(a+1e6,t)['variance_sum24'],score)

    def test_spike_gate_cannot_be_diluted_by_high_rate_sample_count(self):
        t=np.linspace(0,1,101);a=np.zeros((101,24));a[50,0]=191
        q=evaluate_window(a,t)
        self.assertLess(q['variance_sum24'],DEFAULT_POLICY.variance_limit)
        self.assertEqual(q['reason'],'BASELINE_STEP_HIGH')

    def test_missing_discontinuous_stale_and_invalid_inputs(self):
        a=np.zeros((11,24));t=np.arange(11)*.1
        self.assertEqual(evaluate_window(a[:9],t[:9])['status'],'WAITING')
        for ts,reason in [(t*.9,'BASELINE_INSUFFICIENT_HISTORY'),
                          (np.r_[t[:5],t[5:]+.3],'BASELINE_NONCONTINUOUS'),
                          (np.r_[t[:5],t[4],t[6:]],'BASELINE_NONCONTINUOUS')]:
            self.assertEqual(evaluate_window(a,ts)['reason'],reason)
        self.assertEqual(evaluate_window(a,t,now=12)['reason'],'BASELINE_STALE')
        for bad in [np.full((11,24),np.nan),np.full((11,24),1e308)*np.where(np.arange(11)[:,None]%2,1,-1),np.zeros((11,23))]:
            self.assertEqual(evaluate_window(bad,t)['reason'],'BASELINE_INVALID_INPUT')

    def test_expired_stable_island_cannot_hide_current_movement(self):
        latch=CausalRisingStateLatch(DEFAULT_POLICY)
        for i in range(30):latch.feed(i*.1,np.zeros(24),0,i)
        for i in range(30,153):latch.feed(i*.1,np.full(24,1000*(i%2)),0,i)
        s=latch.feed(15.3,np.full(24,6000),10,153)
        self.assertIsNone(s.quiet_raw24)
        self.assertEqual(s.reason,'BASELINE_VARIANCE_HIGH')
        self.assertIsNone(latch.feed(15.4,np.full(24,9000),25,154).response24)
        for i in range(155,180):latch.feed(i*.1,np.ones(24),0,i)
        latch.feed(18.,np.full(24,1400),14,180)
        self.assertIsNotNone(latch.feed(18.1,np.full(24,1600),16,181).response24)

    def test_normal_loading_ramp_does_not_invalidate_past_stable_window(self):
        latch=CausalRisingStateLatch(DEFAULT_POLICY)
        for i in range(25):latch.feed(i*.1,np.zeros(24),0,i)
        for i in range(25,34):latch.feed(i*.1,np.full(24,(i-24)*300),0,i)
        latch.feed(3.4,np.full(24,3000),10,34)
        state=latch.feed(3.5,np.full(24,6000),16,35)
        self.assertIsNotNone(state.response24)
        self.assertEqual(latch.quiet.state.quality['status'],'READY')

    def test_real_stable_and_unstable_raw_windows(self):
        fixtures=json.loads((Path(__file__).parent/'testdata/baseline_windows.json').read_text(encoding='utf8'))
        for f in fixtures:
            q=evaluate_window(f['raw24'],f['times'])
            self.assertEqual(q['status'],f['expected_status'],f['source_name'])
            self.assertAlmostEqual(q['variance_sum24'],f['expected_variance'],places=6)

    def test_bad_baseline_blocks_model_even_if_model_would_apply(self):
        model=Mock(profile='back',development_only=False,requires_dual_anchor=False,signature='fixture',method='fixture')
        model.predict.side_effect=lambda delta,old,tare,floor,state,**kw:LocalForceResult(
            old,old,allocate_regions(delta,old,floor),allocate_regions(delta,old,floor),delta,delta,'BYPASS','LOW_FORCE_IDENTITY')
        r=LocalHighForceRuntime(model,profile='back')
        for i in range(15):r.feed(np.full(24,500*(i%2)),np.zeros(24),np.ones(8)*20,0,i*.1,source_token=i)
        model.predict.reset_mock()
        out=r.feed(np.full(24,6000.),np.zeros(24),np.ones(8)*20,25,1.5,source_token=15)
        model.predict.assert_not_called()
        self.assertFalse(out.applied);self.assertEqual(out.reason,'BASELINE_VARIANCE_HIGH')
        self.assertEqual(out.total_n,25.);np.testing.assert_array_equal(out.corrected_delta24,np.full(24,6000))
        self.assertEqual(out.evidence['baseline_quality']['status'],'REJECTED')

    def test_saved_legacy_or_new_evidence_never_claims_false_quality_pass(self):
        rows=[dict(local_force_state_json=json.dumps(dict(quiet_variance_sum24=92197.00945))),
              dict(local_force_state_json=json.dumps(dict(quiet_variance_sum24=2400))),
              dict(baseline_quality_status='REJECTED'),dict(baseline_quality_status='WAITING')]
        original=json.dumps(rows)
        result=audit_saved_baselines(rows)
        self.assertTrue(result['rejected']);self.assertEqual(result['counts']['REJECTED'],2)
        self.assertEqual(result['counts']['VARIANCE_ONLY_PASS'],1)
        self.assertEqual(json.dumps(rows),original)

    def test_fault_reconnect_tare_and_configuration_require_new_history(self):
        for fault in ('invalid','gap','clock','tare','profile'):
            with self.subTest(fault=fault):
                r=LocalHighForceRuntime()
                for i in range(15):r.feed(np.zeros(24),np.zeros(24),np.ones(8)*20,0,i*.1,source_token=i)
                r.feed(np.ones(24)*1400,np.zeros(24),np.ones(8)*20,14,1.5,source_token=15)
                out=r.feed(np.ones(24)*1600,np.zeros(24),np.ones(8)*20,16,1.6,source_token=16)
                self.assertEqual(out.evidence['baseline_quality']['status'],'READY')
                now=1.7;tare=np.zeros(24);valid=True
                if fault=='invalid':valid=False
                elif fault=='gap':now=2.
                elif fault=='clock':now=1.4
                elif fault=='tare':tare+=10
                elif fault=='profile':r.configure()
                out=r.feed(np.ones(24)*6000,tare,np.ones(8)*20,25,now,valid=valid,source_token=99)
                self.assertFalse(out.applied)
                if out.status!='UNAVAILABLE':self.assertNotEqual(out.evidence['baseline_quality']['status'],'READY')


class BaselineMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.w=Monitor();self.w.timeout_timer.stop();self.w.reader=None
        self.addCleanup(self.w.close)

    def prime(self,kind='stable'):
        now=time.time()
        for i in range(21):
            raw=np.full(24,float(i%2)*(500 if kind=='noisy' else 1))
            self.w._on_paired(PairedFrame(i*100,now-2+i*.1,raw,np.zeros(3)))

    def test_tare_accepts_stable_and_refuses_noisy_without_replacing_old_tare(self):
        self.prime();self.w._tare();self.assertIsNotNone(self.w.tare_raw)
        self.assertEqual(self.w._current_local_force_result().evidence['baseline_quality']['status'],'READY')
        self.assertIn('基线稳定性检查通过',self.w.baseline_quality_label.text())
        old=self.w.tare_raw.copy();epoch=self.w.host_tare_epoch
        self.prime('noisy');self.w._tare()
        np.testing.assert_array_equal(self.w.tare_raw,old);self.assertEqual(self.w.host_tare_epoch,epoch)
        self.assertFalse(self.w.tare_valid_for_new_test)
        self.assertIn('置零未执行',self.w.baseline_quality_label.text())

    def test_tare_rejects_many_frames_in_too_short_time(self):
        now=time.time()
        for i in range(20):self.w._on_paired(PairedFrame(i,now-.019+i*.001,np.zeros(24),np.zeros(3)))
        self.w._tare();self.assertIsNone(self.w.tare_raw)

    def test_tare_handles_high_sample_rate_with_one_second_history(self):
        now=time.time()
        for i in range(121):self.w._on_paired(PairedFrame(i,now-1.2+i*.01,np.zeros(24),np.zeros(3)))
        self.w._tare();self.assertIsNotNone(self.w.tare_raw)
        history=self.w.local_force_runtime.latch.quiet.history
        self.assertGreater(len(history),20)
        self.assertGreaterEqual(history[-1][0]-history[0][0],1.)
        self.assertEqual(self.w._current_local_force_result().evidence['baseline_quality']['status'],'READY')

    def test_tare_seeds_real_sources_and_model_force_without_replaying_acquisition(self):
        self.prime()
        frames=list(self.w.paired_frames)
        runtime=self.w.local_force_runtime
        def actual_estimate_with_small_output(*args,**kwargs):
            return replace(evaluate_raw_force(*args,**kwargs),fz_n=.03)
        with patch('main.evaluate_raw_force',side_effect=actual_estimate_with_small_output), \
             patch.object(runtime,'feed',wraps=runtime.feed) as feed, \
             patch.object(self.w,'_append_record') as append_record, \
             patch.object(self.w.protocol,'feed') as protocol_feed:
            self.w._tare()
            self.assertEqual(feed.call_count,len(frames))
            for call,frame in zip(feed.call_args_list,frames):
                np.testing.assert_array_equal(call.args[0],frame.raw)
                self.assertEqual(call.args[3],.03)  # Do not invent a zero model result.
                self.assertEqual(call.args[4],frame.received_at)
                self.assertEqual(call.kwargs['source_token'],frame.device_ms)
                self.assertEqual(call.kwargs['epoch'],(self.w.host_tare_epoch,'LEGACY_DIAG_UNAVAILABLE',None,
                                                     self.w.raw_baseline_tracker.baseline_epoch))
            count=feed.call_count
            for _ in range(3):
                self.w._update_force_display()
                self.w._render(self.w.last_device_ms)
            self.assertEqual(feed.call_count,count)
            append_record.assert_not_called();protocol_feed.assert_not_called()
            self.assertEqual(list(self.w.paired_frames),frames)

    def test_tare_history_is_available_to_immediate_loading_and_never_erases_a_held_load(self):
        self.prime();self.w._tare()
        last=self.w.paired_frames[-1]
        tare=self.w.tare_raw.copy();epoch=self.w.host_tare_epoch
        loaded_raw=tare+REGION_PROTOTYPES[2]*10000.
        for i in range(1,62):
            self.w._on_paired(PairedFrame(last.device_ms+i*100,last.received_at+i*.1,loaded_raw,np.zeros(3)))
            if i==1:
                state=self.w.local_force_runtime.latch.quiet.state
                self.assertEqual(state.quality['status'],'READY')
                self.assertEqual(state.source_last,last.device_ms)
                self.assertGreaterEqual(state.last_time-state.first_time,1.)
                first_force=self.w.device_fz
            self.assertGreater(self.w.device_fz,.05)
            self.assertAlmostEqual(self.w.device_fz,first_force)
        np.testing.assert_array_equal(self.w.tare_raw,tare)
        self.assertEqual(self.w.host_tare_epoch,epoch)

    def test_tare_rejects_epoch_or_reset_mixture_without_replacing_old_coordinates(self):
        self.prime();self.w._tare()
        old=self.w.tare_raw.copy();epoch=self.w.host_tare_epoch
        for kind in ('epoch','missing_diag','frame_reset','diag_reset'):
            with self.subTest(kind=kind):
                now=time.time();self.w.paired_frames.clear()
                for i in range(21):
                    diag=AcquisitionDiagnostic(i*100,now-2+i*.1,'fixture',255,0,
                        2 if kind=='epoch' and i>=10 else 1,
                        reset_reason='BASELINE_CHANGED' if kind=='diag_reset' and i==10 else '')
                    if kind=='missing_diag' and i==10:diag=None
                    self.w.paired_frames.append(PairedFrame(i*100,now-2+i*.1,np.zeros(24),np.zeros(3),diag,
                        'DEVICE_RESET' if kind=='frame_reset' and i==10 else ''))
                with patch.object(self.w.local_force_runtime,'feed') as feed:
                    self.w._tare();feed.assert_not_called()
                np.testing.assert_array_equal(self.w.tare_raw,old)
                self.assertEqual(self.w.host_tare_epoch,epoch)
                self.assertIn('跨越设备状态变化',self.w.baseline_quality_label.text())

    def test_failed_build_tare_keeps_previous_baseline_and_does_not_warm_start(self):
        self.prime();self.w._tare()
        old=self.w.tare_raw.copy();epoch=self.w.host_tare_epoch
        now=time.time();self.w.paired_frames.clear()
        for i in range(21):
            self.w.paired_frames.append(PairedFrame(i*100,now-2+i*.1,np.zeros(24),np.array([0.,0.,i%2])))
        with patch.object(self.w.local_force_runtime,'feed') as feed:
            self.w._tare();feed.assert_not_called()
        np.testing.assert_array_equal(self.w.tare_raw,old)
        self.assertEqual(self.w.host_tare_epoch,epoch)

    def test_slow_sampling_checks_older_frames_used_by_build_tare(self):
        self.prime();self.w._tare()
        old=self.w.tare_raw.copy();epoch=self.w.host_tare_epoch
        now=time.time();self.w.paired_frames.clear()
        # The recent two seconds are valid; an older missing diagnostic still
        # participates in the twenty-frame tare at five samples per second.
        for i in range(20):
            diag=AcquisitionDiagnostic(i*200,now-3.8+i*.2,'fixture',255,0,1) if i else None
            self.w.paired_frames.append(PairedFrame(i*200,now-3.8+i*.2,np.zeros(24),np.zeros(3),diag))
        self.w._tare()
        np.testing.assert_array_equal(self.w.tare_raw,old)
        self.assertEqual(self.w.host_tare_epoch,epoch)
        self.assertIn('跨越设备状态变化',self.w.baseline_quality_label.text())

    def test_insufficient_history_with_positive_model_explains_manual_zero_requirement(self):
        self.prime();self.w._tare()
        self.w._reset_local_force('TEST_NO_PRELOAD_HISTORY')
        last=self.w.paired_frames[-1]
        self.w._on_paired(PairedFrame(last.device_ms+100,last.received_at+.1,
            self.w.tare_raw+REGION_PROTOTYPES[2]*10000.,np.zeros(3)))
        self.assertGreater(self.w.device_fz,.05)
        self.assertIn('未取得加载前空载历史',self.w.baseline_quality_label.text())
        self.assertIn('静置不会自动改零点',self.w.baseline_quality_label.text())

    def test_bad_loaded_baseline_warns_but_allows_recording(self):
        self.prime();self.w._tare();self.w.recording=True
        # Feed a baseline failure through the real runtime; do not mock the quality result.
        now=time.time()
        self.w._reset_local_force('TEST_NEW_CYCLE')
        for i in range(15):
            self.w._on_paired(PairedFrame(3000+i*100,now+i*.1,np.full(24,500*(i%2)),np.zeros(3)))
        frame=PairedFrame(4500,now+1.5,np.full(24,6000.),np.zeros(3))
        # Force model estimates are irrelevant to this integration assertion.
        runtime=self.w.local_force_runtime
        for i in range(15):runtime.feed(np.full(24,500*(i%2)),self.w.tare_raw,self.w.tare_noise_floor,0,10+i*.1,source_token=i)
        out=runtime.feed(frame.raw,self.w.tare_raw,self.w.tare_noise_floor,25,11.5,source_token=frame.device_ms)
        self.w.last_paired_at=11.5;self.w.last_device_ms=frame.device_ms;self.w.model_raw_fz=25
        self.w.local_force_result=out;self.w.raw=frame.raw.copy();self.w.data_stale=False
        self.w.quality_flags=('BASELINE_QUALITY_REJECTED',)
        self.w._append_record('PAIRED',frame.device_ms,11.5)
        self.assertTrue(self.w.recording)
        row=dict(zip(self.w._csv_header(),self.w.records[-1]))
        self.assertEqual(row['baseline_quality_status'],'REJECTED')
        self.assertEqual(row['baseline_quality_policy'],POLICY_ID)
        self.assertEqual(row['S1_raw_dBx'],6000.)
        self.assertIn('BASELINE_QUALITY_REJECTED',row['quality_flags'])
        self.w.recording=False;self.w.records=[]
        self.w._start_recording();self.assertTrue(self.w.recording)
        self.w.recording=False
        self.w._render(frame.device_ms);self.assertIn('基线波动过大',self.w.baseline_quality_label.text())

    def test_unmatched_high_force_compensation_allows_manual_recording(self):
        self.prime();self.w._tare()
        self.w.last_paired_at=time.time();self.w.last_device_ms=3000;self.w.model_raw_fz=25.
        self.w.local_force_result=LocalForceResult(25.,25.,np.ones(8)*25/8,np.ones(8)*25/8,np.ones(24),np.ones(24),
            'BYPASS','OUTSIDE_BACK25_CALIBRATION_DOMAIN',timestamp=self.w.last_paired_at,source_token=3000,
            evidence=dict(baseline_quality=dict(status='READY',reason='BASELINE_STABLE')))
        self.w._start_recording();self.assertTrue(self.w.recording)
        self.w._stop_recording()
        # Switching modes is optional, not a prerequisite for recording.
        self.w.local_force_box.setCurrentIndex(0)
        self.w._start_recording();self.assertTrue(self.w.recording)
        self.w.recording=False

if __name__=='__main__':unittest.main()
