"""Reproduce starting a recording while already holding an unmatched25 N load."""
import json,os,time,unittest
from dataclasses import replace
from unittest.mock import Mock
from pathlib import Path
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5 import QtWidgets
from main import Monitor
from local_high_force import LocalForceResult,allocate_regions
from no8_raw_tare_core import PairedFrame


class RecordingAvailabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self):
        w=Monitor();w.timeout_timer.stop();self.addCleanup(w.close)
        f=json.loads((Path(__file__).parent/'testdata/back25_domain_frame.json').read_text(encoding='utf8'))
        w.tare_raw=np.array(f['tare']);w.tare_noise_floor=np.array(f['floor'])
        w._test_loaded_raw=np.array(f['delta'])+w.tare_raw
        w.data_stale=False;w.local_force_runtime.latch.quiet.loaded=True
        w._write_log=Mock()
        return w

    def feed(self,w,reason,applied=False,quality_status='READY',ms=100):
        def prediction(raw,tare,floor,old,timestamp,**kw):
            delta=raw-tare;zones=allocate_regions(delta,old,floor)
            total=25. if applied else old
            quality=dict(status=quality_status,reason='BASELINE_VARIANCE_HIGH' if quality_status=='REJECTED' else
                         'BASELINE_INSUFFICIENT_HISTORY' if quality_status=='WAITING' else 'BASELINE_STABLE')
            return LocalForceResult(old,total,zones,allocate_regions(delta,total,floor),delta,delta,
                'APPLIED' if applied else 'BYPASS',reason,applied,'auto',signature=w.automatic_compensation_model.signature,
                timestamp=timestamp,source_token=kw['source_token'],evidence=dict(baseline_quality=quality),
                support=dict(selected_profile='back' if applied else None))
        w.local_force_runtime.feed=prediction
        frame=PairedFrame(ms,time.time(),w._test_loaded_raw.copy(),np.zeros(3))
        w._on_paired(frame)
        self.assertGreater(w.model_raw_fz,20.)
        return frame

    def test_unmatched_history_domain_and_bad_baseline_do_not_block_capture(self):
        for reason,status in [('AUTO_NO_SUPPORTED_PROFILE','READY'),('AUTO_NO_SUPPORTED_PROFILE','WAITING'),
                              ('AUTO_AMBIGUOUS_PROFILES','READY'),('BASELINE_VARIANCE_HIGH','REJECTED')]:
            with self.subTest(reason=reason,status=status):
                w=self.window();self.feed(w,reason,quality_status=status)
                w._start_recording();self.assertTrue(w.recording);self.assertEqual(w.local_force_box.currentIndex(),6)
                frame=self.feed(w,reason,quality_status=status,ms=200)
                w._stop_recording()
                self.assertEqual(len(w.records),1)
                row=dict(zip(w._csv_header(),w.records[0]))
                self.assertEqual(row['record_type'],'PAIRED');self.assertEqual(row['local_force_applied'],0)
                self.assertEqual(row['local_force_reason'],reason)
                self.assertEqual(row['baseline_quality_status'],status)
                self.assertEqual(row['selected_resultant_or_diagnostic_n'],w.model_raw_fz)
                np.testing.assert_array_equal([row[f'S{i}_raw_dB{a}'] for i in range(1,9) for a in 'xyz'],frame.raw)
                self.assertTrue(any('记录提示：' in call.args[0] for call in w._write_log.call_args_list))

    def test_applied_compensation_stays_on_and_corrected_values_are_recorded(self):
        w=self.window();self.feed(w,'AUTO_SUPPORTED_BACK',applied=True)
        w._start_recording();self.assertTrue(w.recording)
        self.feed(w,'AUTO_SUPPORTED_BACK',applied=True,ms=200);w._stop_recording()
        row=dict(zip(w._csv_header(),w.records[0]))
        self.assertEqual(row['local_force_applied'],1);self.assertEqual(row['selected_resultant_or_diagnostic_n'],25.)
        self.assertAlmostEqual(sum(row[f'S{i}_resultant_allocation_estimated_n'] for i in range(1,9)),25.)
        self.assertEqual(json.loads(row['local_force_support_json'])['selected_profile'],'back')

    def test_missing_tare_stale_and_mixed_configuration_remain_separate_guards(self):
        for problem in ['no_tare','stale','mixed']:
            with self.subTest(problem=problem):
                w=self.window();self.feed(w,'AUTO_NO_SUPPORTED_PROFILE')
                if problem=='no_tare':w.tare_raw=None
                elif problem=='stale':w.data_stale=True
                else:w._recording_configuration_conflict=lambda:True
                w._start_recording();self.assertFalse(w.recording)

if __name__=='__main__':unittest.main()
