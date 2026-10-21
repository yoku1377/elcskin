"""Automatic default and unique profile selection preserve calibrated formulas."""
import json,os,time,unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5 import QtWidgets
from automatic_compensation import AutomaticCompensationModel,past15_state
from local_high_force import LocalForceResult,allocate_regions,load_local_model,LocalHighForceRuntime
from local_state import DualRisingState,RisingState
from main import Monitor
from no8_raw_tare_core import PairedFrame

ROOT=Path(__file__).resolve().parent


def fake(profile,applied,*,broken=False):
    def predict(delta,old,tare,floor,state,**kw):
        total=old-.5 if applied else old
        original=allocate_regions(delta,old,floor);zones=allocate_regions(delta,total,floor)
        if broken:zones[0]=np.nan
        return LocalForceResult(old,total,original,zones,delta,delta,'APPLIED' if applied else 'BYPASS',
                                'SUPPORTED' if applied else 'OUTSIDE_INPUT_NEIGHBORHOOD',applied=applied)
    return SimpleNamespace(profile=profile,signature='fixture_'+profile,method='fixture',development_only=False,
                           requires_dual_anchor=profile=='front',predict=predict)


class AutomaticCompensationTests(unittest.TestCase):
    def setUp(self):self.args=(np.arange(1.,25.)*300,26.,np.zeros(24),np.ones(8)*20,None)

    def test_exactly_one_supported_candidate_and_no_cumulative_correction(self):
        for chosen in ['front','back']:
            model=AutomaticCompensationModel(fake('front',chosen=='front'),fake('back',chosen=='back'))
            r=model.predict(*self.args,timestamp=3)
            self.assertTrue(r.applied);self.assertEqual(r.support['selected_profile'],chosen)
            self.assertEqual(r.total_n,25.5)
            self.assertAlmostEqual(r.zones.sum(),r.total_n)
            np.testing.assert_array_equal(model.predict(*self.args,timestamp=4).zones,r.zones)

    def test_ambiguous_unsupported_and_failed_predictions_keep_original(self):
        for front,back,reason in [(fake('front',True),fake('back',True),'AUTO_AMBIGUOUS_PROFILES'),
                                  (fake('front',False),fake('back',False),'AUTO_NO_SUPPORTED_PROFILE'),
                                  (fake('front',True,broken=True),fake('back',True),'AUTO_MODEL_FAILURE')]:
            r=AutomaticCompensationModel(front,back).predict(*self.args,timestamp=3)
            self.assertFalse(r.applied);self.assertEqual(r.reason,reason);self.assertEqual(r.total_n,26.)
            np.testing.assert_array_equal(r.zones,r.original_zones)

    def test_low_force_and_saturation_do_not_force_25(self):
        m=AutomaticCompensationModel(back=fake('back',True))
        for old in [0.,3.,15.,20.,35.]:
            r=m.predict(self.args[0],old,*self.args[2:],timestamp=3)
            self.assertFalse(r.applied);self.assertEqual(r.total_n,old)

    def test_dual_to_single_preserves_observed15_and_never_substitutes20(self):
        q=np.arange(24,dtype=float)
        dual=DualRisingState(quiet_raw24=q,rising_raw24=q+20,response24=np.ones(24)*20,available_time=2.,
            raw15=q+15,response15=np.ones(24)*15,available_time15=1.5,first_source15=10,second_source15=11,
            interpolation_fraction15=.3)
        s=past15_state(dual)
        np.testing.assert_array_equal(s.response24,np.ones(24)*15)
        self.assertEqual(s.available_time,1.5);self.assertEqual(s.second_source,11)
        self.assertIsNone(past15_state(replace(dual,response15=None)).response24)

    def test_recorded_back_frame_matches_manual_calibrated_model_exactly(self):
        f=json.loads((ROOT/'testdata/back25_domain_frame.json').read_text(encoding='utf8'))
        front=load_local_model(ROOT/'local_force_profile.json');back=load_local_model(ROOT/'back25_domain_profile.json')
        st=RisingState(np.array(f['quiet']),np.array(f['rising']),np.array(f['response']),f['available_time'])
        args=(np.array(f['delta']),f['old'],np.array(f['tare']),np.array(f['floor']),st)
        manual=back.predict(*args,timestamp=f['now']);auto=AutomaticCompensationModel(front,back).predict(*args,timestamp=f['now'])
        self.assertTrue(auto.applied);self.assertEqual(auto.support['selected_profile'],'back')
        np.testing.assert_array_equal(auto.zones,manual.zones);self.assertEqual(auto.total_n,manual.total_n)

    def test_recorded_front_dual_state_matches_manual_model_exactly(self):
        f=json.loads((ROOT/'testdata/auto_front_frame.json').read_text(encoding='utf8'))
        front=load_local_model(ROOT/'local_force_profile.json');back=load_local_model(ROOT/'back25_domain_profile.json')
        values={k:np.array(v) if isinstance(v,list) else v for k,v in f['state'].items()}
        st=DualRisingState(**values)
        args=(np.array(f['delta']),f['old'],np.array(f['tare']),np.array(f['floor']),st)
        manual=front.predict(*args,timestamp=f['now']);auto=AutomaticCompensationModel(front,back).predict(*args,timestamp=f['now'])
        self.assertTrue(auto.applied);self.assertEqual(auto.support['selected_profile'],'front')
        np.testing.assert_array_equal(auto.zones,manual.zones);self.assertEqual(auto.total_n,manual.total_n)

    def test_runtime_quality_and_reset_require_fresh_history(self):
        r=LocalHighForceRuntime(AutomaticCompensationModel(back=fake('back',True)),profile='auto')
        for i in range(15):r.feed(np.full(24,500*(i%2)),np.zeros(24),np.ones(8)*20,0,i*.1)
        bad=r.feed(np.ones(24)*5000,np.zeros(24),np.ones(8)*20,25,1.5)
        self.assertFalse(bad.applied);self.assertEqual(bad.reason,'BASELINE_VARIANCE_HIGH')
        r.reset('TARE_CHANGED');self.assertEqual(r.profile,'auto')
        self.assertIsNone(r.latch.state.response15)


class AutomaticMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self):
        w=Monitor();w.timeout_timer.stop();self.addCleanup(w.close);return w

    def test_default_is_auto_and_locked_during_recording(self):
        w=self.window();self.assertEqual(w.local_force_box.currentIndex(),6)
        self.assertEqual(w.local_force_runtime.profile,'auto');self.assertIn('LOCAL_AUTO:',w._active_model_status())
        self.assertEqual(list(w.automatic_compensation_model.models),['front','back'])
        w.recording=True;w.local_force_box.setCurrentIndex(4)
        self.assertEqual(w.local_force_box.currentIndex(),6);w.recording=False

    def test_applied_auto_frame_values_and_actual_model_are_exported(self):
        w=self.window();w.tare_raw=np.zeros(24);w.data_stale=False
        f=PairedFrame(100,time.time(),np.arange(24)*1000.,np.zeros(3))
        def prediction(raw,tare,floor,old,timestamp,**kw):
            delta=raw-tare;original=allocate_regions(delta,old,floor)
            return LocalForceResult(old,24.5,original,allocate_regions(delta,24.5,floor),delta,delta,'APPLIED',
                'AUTO_SUPPORTED_BACK',True,'auto',signature=w.automatic_compensation_model.signature,
                source_token=kw['source_token'],timestamp=timestamp,support=dict(selected_profile='back'),
                evidence=dict(baseline_quality=dict(status='READY',reason='BASELINE_STABLE')))
        w.local_force_runtime.feed=prediction;w._on_paired(f)
        self.assertEqual(w.device_fz,24.5);self.assertIn('自动后25 N校正已应用',w.force_display_source)
        row=dict(zip(w._csv_header(),w._record_base('PAIRED',100,f.received_at)))
        self.assertEqual(row['local_force_profile'],'auto');self.assertEqual(row['local_force_applied'],1)
        self.assertEqual(json.loads(row['local_force_support_json'])['selected_profile'],'back')
        self.assertAlmostEqual(sum(row[f'S{i}_resultant_allocation_estimated_n'] for i in range(1,9)),24.5)

if __name__=='__main__':unittest.main()
