"""Six-Hall schema, identity boundaries and atomic failure checks."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np

from local_high_force import load_local_model
from regional_ard_force import RegionalARDHighForceModel,SixHallARDHighForceModel,SIX_HALL_METHOD
from test_regional_ard_force import ard_fixture
from test_regional_high_force import synthetic_state


def fixture():
    p=ard_fixture();p['method']=SIX_HALL_METHOD
    m=p['model'];m['method']=m['schema']=SIX_HALL_METHOD
    m['additional_S1_S2_pre_anchor6']=[0.]*6
    m['additional_S1_S2_components']=[dict(sensor_name=f'S{i+1}',roi_columns=list(range(3*i,3*i+3)),
      family='ARD_RBF',gp=copy.deepcopy(m['magnetic_components'][i]['gp'])) for i in range(2)]
    return p


class SixHallTests(unittest.TestCase):
    def setUp(self):
        self.payload=fixture();self.model=SixHallARDHighForceModel(self.payload)
        self.state=synthetic_state();self.delta=np.arange(1.,25.)*100.;self.floor=np.full(8,20.)

    def predict(self,**kwargs):
        args=dict(delta24=self.delta,old_force=27.,tare24=self.state.quiet_raw24,noise_floor=self.floor,state=self.state,timestamp=3.)
        args.update(kwargs);return self.model.predict(**args)

    def test_explicit_schema_and_invalid_mapping_reject(self):
        with self.assertRaises(ValueError):RegionalARDHighForceModel(self.payload)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'profile.json';path.write_text(json.dumps(self.payload),encoding='utf8')
            self.assertIsInstance(load_local_model(path),SixHallARDHighForceModel)
        changes=[lambda p:p['model']['additional_S1_S2_components'].pop(),
          lambda p:p['model']['additional_S1_S2_components'][0].update(sensor_name='S3'),
          lambda p:p['model'].update(additional_S1_S2_pre_anchor6=[0.]*5),
          lambda p:p['model']['additional_S1_S2_components'][0]['gp'].update(coefficient=[[0.]]),
          lambda p:p['model']['additional_S1_S2_components'][1]['gp'].update(lengths=[0.,1.])]
        for change in changes:
            p=copy.deepcopy(self.payload);change(p)
            with self.subTest(change=str(change)),self.assertRaises(ValueError):SixHallARDHighForceModel(p)
        p=fixture();p.pop('method');p['model']['method']=p['model']['schema']='FRONT_REGIONAL_ARD_RBF_V1'
        with self.assertRaises(ValueError):RegionalARDHighForceModel(p)

    def test_low_force_and_future_state_return_original(self):
        for args in [dict(old_force=15.,tare24=None,state=None,timestamp=None),dict(timestamp=1.)]:
            r=self.predict(**args);self.assertFalse(r.applied)
            np.testing.assert_array_equal(r.corrected_delta24,self.delta)
            np.testing.assert_array_equal(r.zones,r.original_zones)

    def test_six_axes_change_current_increment_and_total_preserved(self):
        r=self.predict();self.assertTrue(r.applied)
        prior=RegionalARDHighForceModel(ard_fixture()).predict(self.delta,27.,self.state.quiet_raw24,self.floor,self.state,timestamp=3.)
        self.assertEqual(r.total_n,prior.total_n)
        np.testing.assert_array_equal(r.corrected_delta24[6:],prior.corrected_delta24[6:])
        self.assertTrue(np.any(r.corrected_delta24[:6]!=prior.corrected_delta24[:6]))
        step=np.linspace(-.1,.1,24);shift=self.predict(delta24=self.delta+step)
        np.testing.assert_allclose(shift.corrected_delta24-r.corrected_delta24,step,rtol=0,atol=1e-9)
        self.assertAlmostEqual(sum(r.zones),r.total_n,places=12)

    def test_additional_magnetic_training_means_are_audit_only(self):
        r=self.predict()
        for c in self.payload['model']['additional_S1_S2_components']:c['gp']['center_for_training_prediction']=[1e9]*3
        self.model=SixHallARDHighForceModel(self.payload);same=self.predict()
        np.testing.assert_array_equal(same.corrected_delta24,r.corrected_delta24)
        self.assertEqual(same.total_n,r.total_n)

    def test_additional_failure_bypasses_both_groups_atomically(self):
        class Failed:
            def predict(self,blocks):return np.full(12,np.nan),0.,np.zeros(2)
        self.model.additional_components[0]=Failed();r=self.predict()
        self.assertFalse(r.applied);self.assertEqual(r.status,'BYPASS')
        self.assertEqual(r.reason,'NONFINITE_MODEL_OUTPUT');self.assertEqual(r.total_n,27.)
        np.testing.assert_array_equal(r.corrected_delta24,self.delta)
        np.testing.assert_array_equal(r.zones,r.original_zones)


if __name__=='__main__':unittest.main()
