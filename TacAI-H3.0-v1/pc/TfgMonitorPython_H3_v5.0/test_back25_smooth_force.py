"""Candidate isolation, exact nonlinear predictor, audit and invalid-state checks."""
import copy
import json
from pathlib import Path
import unittest
import numpy as np
from back25_smooth_force import Back25SmoothForceModel, METHOD, SPATIAL_METHOD
from local_high_force import load_local_model, allocate_regions
from test_back25_consistency_force import fixture, state


def smooth_fixture():
    p=fixture();p['method']=METHOD;p['spatial']['method']=SPATIAL_METHOD
    s=p['spatial']['model'];points=np.zeros((4,48));points[:,0]=[0,.1,-.1,.2]
    coef=np.zeros((4,24));coef[:,12]=[.01,-.01,.02,-.02]
    s.update(training_x=points.tolist(),training_z=points[:,:2].tolist(),kernel_length=.7,coefficient=coef.tolist())
    return p


class Back25SmoothTests(unittest.TestCase):
    def test_exact_prediction_live_input_and_audit(self):
        p=smooth_fixture();m=Back25SmoothForceModel(p);st=state();delta=np.arange(1.,25.)*100;floor=np.ones(8)*10
        r=m.predict(delta,29.,np.zeros(24),floor,st,timestamp=2.)
        self.assertTrue(r.applied);self.assertTrue(r.signature.startswith(METHOD+':'))
        s=p['spatial']['model'];x=np.zeros(48);x[0]=.05
        distances=np.linalg.norm(np.array(s['training_x'])-x,axis=1)
        shift=np.exp(-.5*(distances/s['kernel_length'])**2)@np.array(s['coefficient'])
        expected=delta-(np.ones(24)+np.linalg.norm(delta-1)*shift)
        np.testing.assert_allclose(r.corrected_delta24,expected,rtol=0,atol=1e-10)
        np.testing.assert_allclose(r.zones,allocate_regions(expected,r.total_n,floor),rtol=0,atol=1e-11)
        np.testing.assert_array_equal(r.original_delta24,delta)
        moved=delta.copy();moved[18]+=500
        other=m.predict(moved,29.1,np.zeros(24),floor,st,timestamp=2.)
        self.assertGreater(np.max(np.abs(r.zones-other.zones)),.01)
        self.assertGreater(other.total_n,r.total_n)

    def test_reject_bad_kernel_or_inconsistent_support(self):
        for change in [lambda s:s.update(kernel_length=0),lambda s:s.update(coefficient=[[1]]),
                       lambda s:s.update(training_z=[[9,9]]*4),lambda s:s.update(training_x=[[0]*47]*4)]:
            p=smooth_fixture();change(p['spatial']['model'])
            with self.assertRaises(ValueError):Back25SmoothForceModel(p)

    def test_unknown_history_and_out_of_domain_preserve_entire_original(self):
        m=Back25SmoothForceModel(smooth_fixture());st=state();d=np.arange(1.,25.)*100;f=np.ones(8)*10
        for current in (None,st):
            if current is not None:object.__setattr__(current,'response24',np.full(24,1e7));object.__setattr__(current,'rising_raw24',current.quiet_raw24+current.response24)
            r=m.predict(d,29.,np.zeros(24),f,current,timestamp=2.)
            self.assertFalse(r.applied);self.assertEqual(r.total_n,29.)
            np.testing.assert_array_equal(r.corrected_delta24,d)
            np.testing.assert_array_equal(r.zones,r.original_zones)

    def test_shipped_profiles_are_distinct_and_total_guards_preserved(self):
        root=Path(__file__).resolve().parent
        old=json.loads((root/'back25_consistency_profile.json').read_text(encoding='utf8'))
        new=json.loads((root/'back25_smooth_profile.json').read_text(encoding='utf8'))
        self.assertEqual(old['total'],new['total']);self.assertEqual(old['domain_guard'],new['domain_guard'])
        self.assertNotEqual(old['method'],new['method']);self.assertFalse(new['acceptance_passed'])
        self.assertIsInstance(load_local_model(root/'back25_smooth_profile.json'),Back25SmoothForceModel)


if __name__=='__main__':unittest.main()
