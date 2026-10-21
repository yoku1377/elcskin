"""Updated-domain runtime: fixture replay, limits, isolation and invalid input."""
import copy,json
from pathlib import Path
import unittest
import numpy as np
from local_high_force import load_local_model
from local_state import RisingState
from back25_domain_force import Back25DomainForceModel

ROOT=Path(__file__).resolve().parent


class Back25DomainTests(unittest.TestCase):
    def setUp(self):
        self.doc=json.loads((ROOT/'back25_domain_profile.json').read_text(encoding='utf-8'))
        self.m=Back25DomainForceModel(self.doc)
        f=json.loads((ROOT/'testdata/back25_domain_frame.json').read_text(encoding='utf-8'))
        self.f=f
        self.args=(np.array(f['delta']),f['old'],np.array(f['tare']),np.array(f['floor']))
        self.state=RisingState(np.array(f['quiet']),np.array(f['rising']),np.array(f['response']),f['available_time'])

    def predict(self,**kwargs):return self.m.predict(*self.args,kwargs.pop('state',self.state),timestamp=kwargs.pop('timestamp',self.f['now']),**kwargs)

    def test_saved_real_frame_matches_independent_frozen_calculation(self):
        r=self.predict();self.assertTrue(r.applied)
        self.assertEqual(r.support['calibration_domain'],'updated_20261016')
        np.testing.assert_allclose(r.zones,self.f['expected_zones'],atol=1e-10,rtol=0)
        self.assertAlmostEqual(r.total_n,self.f['expected_total'],places=10)
        np.testing.assert_array_equal(r.original_delta24,self.args[0])
        self.assertAlmostEqual(r.zones.sum(),r.total_n)

    def test_gain_above_one_is_bounded_monotonic_and_continuous(self):
        for beta in (.5,1.,1.35):
            x=np.linspace(0,35,3501);y=np.array([self.m.correct_total(v,beta) for v in x])
            self.assertTrue(np.all(np.diff(y)>=-1e-12))
            self.assertTrue(np.all((0<=y)&(y<=35)))
            for junction in (20.,31.,35.):
                self.assertLess(abs(self.m.correct_total(junction-1e-8,beta)-self.m.correct_total(junction+1e-8,beta)),1e-6)
        self.assertGreater(self.m.correct_total(24.,1.2),24.)

    def test_missing_future_or_inconsistent_history_never_applies(self):
        for state,now in [(None,self.f['now']),(self.state,self.f['available_time']-1)]:
            self.assertFalse(self.m.predict(*self.args,state,timestamp=now).applied)
        broken=RisingState(self.state.quiet_raw24,self.state.rising_raw24+1,self.state.response24,self.state.available_time)
        self.assertFalse(self.predict(state=broken).applied)

    def test_outside_both_domains_preserves_original(self):
        state=RisingState(np.full(24,1e8),np.full(24,2e8),np.full(24,1e8),1.)
        r=self.predict(state=state)
        self.assertFalse(r.applied);self.assertEqual(r.reason,'OUTSIDE_BACK25_CALIBRATION_DOMAIN')
        self.assertEqual(r.total_n,self.args[1]);np.testing.assert_array_equal(r.zones,r.original_zones)

    def test_under20_is_identity(self):
        for old in (3.,15.,20.):
            r=self.m.predict(self.args[0],old,*self.args[2:],None,timestamp=self.f['now'])
            self.assertFalse(r.applied);self.assertEqual(r.total_n,old)

    def test_current_force_and_magnetic_input_remain_live(self):
        a=self.predict();d=self.args[0].copy();d[18]+=300
        b=self.m.predict(d,self.args[1]+.2,*self.args[2:],self.state,timestamp=self.f['now'])
        self.assertTrue(b.applied);self.assertGreater(b.total_n,a.total_n)
        self.assertGreater(float(np.max(np.abs(b.zones-a.zones))),.001)

    def test_reject_corrupt_parameters_and_false_acceptance(self):
        for change in [lambda d:d.update(acceptance_passed=True),
                       lambda d:d['updated_domain'].update(guard_scales=[0,1]),
                       lambda d:d['updated_domain']['spatial'].update(coefficient=[[1]]),
                       lambda d:d['updated_domain'].update(beta_limits=[.5,1.5])]:
            d=copy.deepcopy(self.doc);change(d)
            with self.assertRaises((ValueError,KeyError,TypeError)):Back25DomainForceModel(d)

    def test_loader_and_acceptance_metadata(self):
        m=load_local_model(ROOT/'back25_domain_profile.json')
        self.assertIsInstance(m,Back25DomainForceModel)
        self.assertFalse(m.acceptance_passed)
        self.assertFalse(m.requires_dual_anchor)


if __name__=='__main__':unittest.main()
