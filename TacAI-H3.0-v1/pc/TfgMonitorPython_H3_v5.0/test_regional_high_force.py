"""Frozen regional adapter contracts and parity with independently saved folds."""
import copy
import csv
import json
from pathlib import Path
import unittest
import numpy as np

from local_high_force import ORIGINAL_MODEL_SHA256, allocate_regions
from local_state import DualRisingState
from regional_high_force import METHOD, RegionalHighForceModel


def synthetic_profile():
    ids=['synthetic_a','synthetic_b','synthetic_c','synthetic_d']
    z=np.array([[1,0,0],[0,1,0],[0,0,1],[-1,-1,-1]],float)
    embedding=dict(block_A_center24=[0.]*24,block_B_center24=[0.]*24,
      block_A_scale=20.,block_B_scale=20.,pca_center48=[0.]*48,
      pc3x48=np.eye(3,48).tolist(),score_scale3=[1.]*3,training_z3=z.tolist(),
      kernel_length=2.,kernel_column_mean=[.2]*4,kernel_grand_mean=.2)
    common=dict(eligible=True,training_ids=ids,embedding=embedding,
      magnetic_alpha_nx12=(np.arange(48).reshape(4,12)/100).tolist(),
      beta_alpha_n=[.02,-.03,.01,.01],beta_training_mean=.75,
      magnetic_target_training_mean12_for_audit_only=[99999.]*12)
    dual=copy.deepcopy(common)
    dual['embedding']=dict(block_centers3x24=np.zeros((3,24)).tolist(),block_scales3=[20.]*3,
      pca_center72=[0.]*72,pc_rankx72=np.eye(2,72).tolist(),score_scale=[1.]*2,
      actual_rank=2,training_z=z[:,:2].tolist(),kernel_family='RBF',kernel_length=2.,
      kernel_column_mean=[.2]*4,kernel_grand_mean=.2)
    linear=copy.deepcopy(common)
    linear.update(linear_feature_center3=[.1,.2,.3],linear_magnetic_coef3x12=(np.arange(36).reshape(3,12)/300).tolist(),
      magnetic_residual_alpha_nx12=common['magnetic_alpha_nx12'],linear_beta_coef3=[.01,.02,-.01],
      beta_residual_alpha_n=common['beta_alpha_n'],beta_residual_mean=.015,
      magnetic_training_mean12_for_audit_only=[77777.]*12,magnetic_residual_mean12_for_audit_only=[88888.]*12)
    pairs=[('SINGLE20_RBF',common),('DUAL15_20_RBF',dual),('SINGLE20_LINEAR_RBF',linear),('SINGLE20_RBF',common)]
    model=dict(schema=METHOD,method=METHOD,eligible=True,training_ids=ids,
      magnetic_components=[dict(sensor_index=i+2,roi_columns=list(range(3*i,3*i+3)),family=f,model=copy.deepcopy(m)) for i,(f,m) in enumerate(pairs)],
      beta_component=dict(family='SINGLE20_LINEAR_RBF',model=copy.deepcopy(linear)),pre_anchor12=[0.]*12,
      required_anchor_old_output_indices=[15.,20.],not_for_production=False,
      support_model=dict(method='THREE_BLOCK_TRACE_NEAREST_NEIGHBOR_V1',training_count=4,training_ids=ids,
        training_points_normalized72=np.zeros((4,72)).tolist(),block_centers3x24=np.zeros((3,24)).tolist(),
        block_scales3=[20.]*3,threshold=100.))
    return dict(model=model,runtime_approved=True,not_for_production=False,profile='front',original_model_sha256=ORIGINAL_MODEL_SHA256)


def synthetic_state():
    q=np.arange(24,dtype=float);raw15=q+15;raw20=q+20
    return DualRisingState(quiet_raw24=q,rising_raw24=raw20,response24=raw20-q,available_time=2.,
      first_source=19,second_source=20,interpolation_fraction=.5,raw15=raw15,response15=raw15-q,
      available_time15=1.5,first_source15=14,second_source15=15,interpolation_fraction15=.5)


class RegionalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.payload=synthetic_profile();self.model=RegionalHighForceModel(self.payload)
        self.state=synthetic_state();self.delta=np.arange(1,25,dtype=float)*100
        self.tare=self.state.quiet_raw24.copy();self.floor=np.full(8,20.)

    def predict(self,model=None,**kw):
        args=dict(delta24=self.delta,old_force=27.,tare24=self.tare,noise_floor=self.floor,state=self.state,timestamp=3.)
        args.update(kw);return (model or self.model).predict(**args)

    def test_approval_sha_and_component_schema_fail_closed(self):
        bad=copy.deepcopy(self.payload);bad['not_for_production']=True
        with self.assertRaises(ValueError):RegionalHighForceModel(bad)
        self.assertTrue(RegionalHighForceModel(bad,allow_development=True).development_only)
        for mutate in [lambda p:p.pop('original_model_sha256'),
                       lambda p:p.update(original_model_sha256='0'*64),
                       lambda p:p['model']['magnetic_components'][1].update(sensor_index=2),
                       lambda p:p['model']['beta_component'].update(family='UNKNOWN'),
                       lambda p:p['model']['support_model'].update(block_scales3=[0,1,1]),
                       lambda p:p['model']['magnetic_components'][0]['model']['embedding'].update(training_z3=[[0,0,0]]),
                       lambda p:p['model']['magnetic_components'][0]['model'].update(beta_training_mean=float('nan'))]:
            p=copy.deepcopy(self.payload);mutate(p)
            with self.subTest(mutation=str(mutate)),self.assertRaises(ValueError):RegionalHighForceModel(p,allow_development=True)

    def test_low_force_identity_precedes_missing_state_and_tare(self):
        for old in [0.,.02,15.,20.]:
            r=self.predict(old_force=old,tare24=None,state=object(),timestamp=None)
            self.assertFalse(r.applied);self.assertEqual(r.total_n,old)
            np.testing.assert_array_equal(r.corrected_delta24,self.delta)
            np.testing.assert_array_equal(r.zones,allocate_regions(self.delta,old,self.floor))

    def test_missing_fault_future_unordered_and_unsupported_preserve_original(self):
        states=[DualRisingState(),None,
          DualRisingState(**{**self.state.__dict__,'available_time':4.}),
          DualRisingState(**{**self.state.__dict__,'available_time15':2.5}),
          DualRisingState(**{**self.state.__dict__,'raw15':self.state.raw15+100})]
        for state in states:
            r=self.predict(state=state);self.assertFalse(r.applied)
            np.testing.assert_array_equal(r.corrected_delta24,self.delta);self.assertEqual(r.total_n,27.)
        p=copy.deepcopy(self.payload);p['model']['support_model']['threshold']=0
        r=self.predict(RegionalHighForceModel(p));self.assertEqual(r.reason,'OUTSIDE_INPUT_NEIGHBORHOOD')
        self.assertEqual(r.total_n,27.)
        self.assertFalse(self.predict(timestamp=None).applied)
        self.assertFalse(self.predict(tare24=None).applied)

    def test_current_response_conservation_and_frozen_state(self):
        r=self.predict();self.assertTrue(r.applied)
        self.assertAlmostEqual(r.zones.sum(),r.total_n,places=12)
        other=np.r_[0:6,18:24];np.testing.assert_array_equal(r.corrected_delta24[other],self.delta[other])
        perturb=np.linspace(-.3,.3,24);shift=self.predict(delta24=self.delta+perturb)
        np.testing.assert_allclose(shift.corrected_delta24-r.corrected_delta24,perturb,rtol=0,atol=1e-12)
        later=self.predict(timestamp=900.)
        self.assertEqual(later.beta,r.beta);np.testing.assert_array_equal(later.corrected_delta24,r.corrected_delta24)
        up=self.predict(old_force=28.)
        self.assertAlmostEqual(up.total_n-r.total_n,r.beta,places=12)

    def test_independent_components_do_not_consume_audit_templates(self):
        r=self.predict();p=copy.deepcopy(self.payload)
        for c in [*p['model']['magnetic_components'],p['model']['beta_component']]:
            m=c['model']
            for field in ['magnetic_target_training_mean12_for_audit_only','magnetic_training_mean12_for_audit_only','magnetic_residual_mean12_for_audit_only']:
                m[field]=[1e20]*12
        same=self.predict(RegionalHighForceModel(p))
        np.testing.assert_array_equal(same.corrected_delta24,r.corrected_delta24);self.assertEqual(same.total_n,r.total_n)
        p=copy.deepcopy(self.payload);p['model']['magnetic_components'][0]['model']['magnetic_alpha_nx12']=(np.ones((4,12))*700).tolist()
        changed=self.predict(RegionalHighForceModel(p))
        self.assertFalse(np.array_equal(changed.corrected_delta24[6:9],r.corrected_delta24[6:9]))
        np.testing.assert_array_equal(changed.corrected_delta24[np.r_[0:6,9:24]],r.corrected_delta24[np.r_[0:6,9:24]])
        self.assertEqual(changed.total_n,r.total_n)

    def test_invalid_current_has_no_stale_measurement(self):
        for kwargs in [dict(delta24=np.full(24,np.nan)),dict(old_force=float('inf')),dict(noise_floor=np.full(8,-1.))]:
            r=self.predict(**kwargs);self.assertEqual(r.status,'UNAVAILABLE')
            self.assertIsNone(r.total_n);self.assertIsNone(r.zones)
            self.assertEqual(r.export_dict()['local_force_corrected_total_n'],'')


class RecordedRegionalParityTests(unittest.TestCase):
    def test_all14_frozen_outer_folds_match_saved_predictions(self):
        root=Path(__file__).resolve().parents[2]
        d=root/'tmp/front25_repair_20261014/regional_independent'
        if not (d/'01_nested_loo_predictions.npz').exists():
            self.skipTest('Private offline fold fixtures are not distributed with the application')
        read=lambda p:json.loads(p.read_text(encoding='utf8'))
        folds=read(d/'01_nested_whole_round_leave_one_out.json')
        provenance=read(d/'00_inputs_and_method.json')
        inputs={x['id']:x for x in provenance['state_inputs']}
        count=0
        with np.load(d/'01_nested_loo_predictions.npz') as data:
            for row,parameters in zip(folds['rows'],folds['fold_models']):
                key=row['id'];inp=inputs[key];q=np.asarray(inp['quiet24']);tare=np.asarray(inp['tare24'])
                source=inp['dual_source'];a=source['old15'];b=source['old20'];available=inp['anchor_available_time_aligned_s']
                raw15=np.asarray(a['interpolated_raw_anchor24']);raw20=np.asarray(b['interpolated_raw_anchor24'])
                state=DualRisingState(quiet_raw24=q,rising_raw24=raw20,response24=raw20-q,available_time=available,
                  first_source=b['frames'][0]['csv_line'],second_source=b['frames'][1]['csv_line'],interpolation_fraction=b['interpolation_fraction'],
                  raw15=raw15,response15=raw15-q,available_time15=available+a['anchor_available_time_s']-b['anchor_available_time_s'],
                  first_source15=a['frames'][0]['csv_line'],second_source15=a['frames'][1]['csv_line'],interpolation_fraction15=a['interpolation_fraction'])
                model=RegionalHighForceModel(dict(model=parameters,not_for_production=True,original_model_sha256=ORIGINAL_MODEL_SHA256),allow_development=True)
                prefix=key+'__';delta=data[prefix+'original_delta'];old=data[prefix+'old_total']
                # Preserve the real recorded per-channel floor; never substitute a fitted/default floor.
                with Path(b['source']).open(encoding='utf-8-sig',newline='') as stream:
                    first_row=next(csv.DictReader(stream))
                    floor=np.asarray([float(first_row[f'S{i}_spatial_noise_floor']) for i in range(1,9)])
                for i,(x,f) in enumerate(zip(delta,old)):
                    now=available+1 if data[prefix+'anchor_available_mask'][i] else available-1
                    r=model.predict(x,f,tare,floor,state,timestamp=now)
                    np.testing.assert_allclose(r.corrected_delta24,data[prefix+'safe_delta'][i],rtol=0,atol=1e-9,err_msg=key)
                    self.assertAlmostEqual(r.total_n,data[prefix+'safe_total'][i],places=9,msg=key)
                    np.testing.assert_allclose(r.zones,data[prefix+'safe_zones'][i],rtol=0,atol=1e-9,err_msg=key)
                    count+=1
        self.assertEqual(len(folds['rows']),14)
        self.assertEqual(count,4920)


if __name__=='__main__':unittest.main()
