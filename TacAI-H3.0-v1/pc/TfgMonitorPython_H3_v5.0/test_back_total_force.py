"""Rear force calibration preserves current measurements and profile boundaries."""
import copy,json
from pathlib import Path
import tempfile
import unittest
import numpy as np

from back_total_force import BackTotalForceModel,METHOD
from local_high_force import LocalHighForceRuntime,ORIGINAL_MODEL_SHA256,load_local_model
from local_state import DualRisingState

def state():
    q=np.zeros(24);a15=np.full(24,1500.);a20=np.full(24,2000.)
    return DualRisingState(quiet_raw24=q,raw15=a15,response15=a15,rising_raw24=a20,response24=a20,
      available_time15=1.6,available_time=1.7,first_source15=15,second_source15=16,first_source=16,second_source=17)

def fixture():
    ids=['a','b','c','d'];a=np.full(24,2000.);points=np.zeros((4,48));points[:,0]=[0.,.1,-.1,.2]
    sm=dict(method='TWO_BLOCK_TRACE_STANDARDIZED_NEAREST_NEIGHBOR_V1',training_count=4,training_ids=ids,
      training_points_normalized48=points.tolist(),load_center24=a.tolist(),quiet_center24=[0.]*24,load_scale=100.,quiet_scale=100.,threshold=1.)
    m=dict(method=METHOD,schema=METHOD,eligible=True,training_ids=ids,support_model=sm,kernel_length=1.,coefficient=[0.]*4,beta_center=.6,
      beta_limits=[.5,1.],lower_identity_original_n=20.,local_upper_original_n=31.,original_max_n=35.,not_for_production=False,
      geometry_unit24=(a/np.linalg.norm(a)).tolist(),geometry_reference_norm=float(np.linalg.norm(a)),geometry_angle_degrees=5.,geometry_norm_ratio=[.9,1.1],
      neighborhood_policy='RBF_STATE_RESIDUAL_WITH_POSITION_CALIBRATION_PRIOR')
    return dict(method=METHOD,model=m,profile='back',original_model_sha256=ORIGINAL_MODEL_SHA256,runtime_approved=True,acceptance_passed=False)

class BackTotalTests(unittest.TestCase):
    def setUp(self):self.payload=fixture();self.model=BackTotalForceModel(self.payload);self.state=state();self.delta=np.arange(1.,25.)*200;self.floor=np.full(8,20.)
    def predict(self,**kwargs):
        args=dict(delta24=self.delta,old_force=30.,tare24=np.zeros(24),noise_floor=self.floor,state=self.state,timestamp=2.)
        args.update(kwargs);return self.model.predict(**args)

    def test_loading_dispatch_and_position_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'back.json';p.write_text(json.dumps(self.payload),encoding='utf8')
            self.assertIsInstance(load_local_model(p),BackTotalForceModel)
        with self.assertRaises(ValueError):LocalHighForceRuntime(self.model,profile='front')
        with self.assertRaises(ValueError):LocalHighForceRuntime(profile='back')
        rt=LocalHighForceRuntime(self.model,profile='back');self.assertEqual(rt.profile,'back')
        for change in [lambda p:p.update(profile='front'),lambda p:p.update(runtime_approved=False),
                       lambda p:p.update(original_model_sha256='0'*64),lambda p:p['model'].update(geometry_angle_degrees=50.),
                       lambda p:p['model'].update(coefficient=[1.])]:
            p=copy.deepcopy(self.payload);change(p)
            with self.subTest(change=str(change)),self.assertRaises(ValueError):BackTotalForceModel(p)

    def test_current_raw_and_region_proportions_preserved(self):
        r=self.predict();self.assertTrue(r.applied);self.assertEqual(r.total_n,26.)
        np.testing.assert_array_equal(r.corrected_delta24,self.delta)
        np.testing.assert_allclose(r.zones/r.total_n,r.original_zones/30.,rtol=0,atol=1e-12)
        self.assertAlmostEqual(r.zones.sum(),r.total_n,places=12)
        later=self.predict(old_force=30.1);self.assertAlmostEqual(later.total_n-r.total_n,.06,places=12)
        shifted=self.predict(delta24=self.delta+1);np.testing.assert_array_equal(shifted.corrected_delta24-r.corrected_delta24,np.ones(24))

    def test_low_future_and_wrong_geometry_bypass(self):
        bad=copy.deepcopy(self.state);object.__setattr__(bad,'response24',np.full(24,-2000.));object.__setattr__(bad,'rising_raw24',np.full(24,-2000.))
        for args in [dict(old_force=15.,state=None,tare24=None,timestamp=None),dict(timestamp=1.),dict(state=bad)]:
            r=self.predict(**args);self.assertFalse(r.applied);self.assertEqual(r.total_n,r.original_total_n)
            np.testing.assert_array_equal(r.corrected_delta24,self.delta)

    def test_neighborhood_prior_is_exposed_and_does_not_erase_response(self):
        self.model.threshold=0.;self.model.points+=.1
        r=self.predict();self.assertTrue(r.applied)
        self.assertIn('RBF_CALIBRATION_PRIOR_OUTSIDE_NEIGHBORHOOD',r.warnings)
        self.assertIn('CALIBRATED_CANDIDATE_ACCEPTANCE_PENDING',r.warnings)
        self.assertFalse(self.model.acceptance_passed);self.assertEqual(r.total_n,26.)

    def test_mapping_continuity_positive_slope_and_full_range(self):
        for beta in (.5,.6,1.):
            grid=np.linspace(0,35,3501);y=np.array([self.model.correct_total(v,beta) for v in grid])
            self.assertTrue(np.all(np.diff(y)>0));self.assertEqual(y[-1],35.)
            np.testing.assert_array_equal(y[grid<=20],grid[grid<=20])
            for knot in (20.,31.,35.):
                self.assertLess(abs(self.model.correct_total(knot-1e-7,beta)-self.model.correct_total(knot+1e-7,beta)),1e-6)
        self.assertIn('OUTSIDE_BACK25_VALIDATED_FORCE_RANGE',self.predict(old_force=32.).warnings)

    def test_gap_and_epoch_invalidate_past_load_state(self):
        def load(rt,start=0.):
            for i in range(15):rt.feed(np.zeros(24),np.zeros(24),self.floor,0.,start+i*.1,source_token=i,epoch=(1,1))
            for time,force in [(1.5,10.),(1.6,16.),(1.7,22.),(1.8,30.)]:
                r=rt.feed(np.full(24,100.*force),np.zeros(24),self.floor,force,start+time,source_token=100+int(time*10),epoch=(1,1))
            return r
        for fault in ('gap','epoch'):
            rt=LocalHighForceRuntime(self.model,profile='back');self.assertTrue(load(rt).applied)
            r=rt.feed(np.full(24,3000.),np.zeros(24),self.floor,30.,2.2 if fault=='gap' else 1.9,source_token=120,epoch=(1,2) if fault=='epoch' else (1,1))
            self.assertFalse(r.applied);self.assertEqual(r.total_n,30.)

    def test_private_source_backed_predictions(self):
        root=Path(__file__).resolve().parents[2];study=root/'tmp/back_total_20261015/geometry_prior'
        if not (study/'03_frozen_back_profile.json').exists():self.skipTest('Private calibration sources are not distributed')
        read=lambda p:json.loads(p.read_text(encoding='utf8'))
        p=read(study/'03_frozen_back_profile.json');model=BackTotalForceModel(p,allow_development=True)
        inp=read(study/'00_inputs.json')['trial_inputs'];folds=read(study/'01_whole_round_loo.json');count=0
        arrays=np.load(root/'tmp/diagnose_20261014_back25N/sensor_full.npz');raw=arrays['raw'].reshape(-1,24);delta=arrays['retared'].reshape(-1,24)
        with np.load(study/'01_loo_predictions.npz') as saved,np.load(study/'04_frozen_predictions.npz') as same:
            for index,entry in enumerate(inp):
                payload=copy.deepcopy(p);payload['model']=folds['models'][index];outer=BackTotalForceModel(payload,allow_development=True)
                self.assertNotIn(entry['id'],outer.support_ids);s=dict(entry['state'])
                for k in ('quiet_raw24','raw15','response15','rising_raw24','response24'):s[k]=np.array(s[k])
                st=DualRisingState(**s);key=entry['id']+'__';ix=saved[key+'sensor_indices'];floor=np.full(8,0.)
                # Read the real per-channel floors from the recorded source.
                import csv
                with Path(entry['sensor_path']).open(encoding='utf-8-sig',newline='') as f:first=next(csv.DictReader(f))
                floor=np.array([float(first[f'S{i}_spatial_noise_floor']) for i in range(1,9)])
                for j,i in enumerate(ix):
                    for current,expected in [(outer,saved),(model,same)]:
                        r=current.predict(delta[i],arrays['total'][i],entry['tare24'],floor,st,timestamp=arrays['t'][i])
                        self.assertAlmostEqual(r.total_n,expected[key+'new_total'][j],places=9)
                        np.testing.assert_allclose(r.zones,expected[key+'new_zones'][j],rtol=0,atol=1e-9)
                        np.testing.assert_array_equal(r.corrected_delta24,delta[i])
                    count+=1
        self.assertGreater(count,2000)

if __name__=='__main__':unittest.main()
