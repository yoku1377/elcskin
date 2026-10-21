"""Pure ARD loading/inference and source-backed frozen-parameter replay."""
import copy,csv,hashlib,json
from pathlib import Path
import unittest
import numpy as np

from local_high_force import ORIGINAL_MODEL_SHA256
from local_state import DualRisingState
from regional_ard_force import METHOD,RegionalARDHighForceModel,SixHallARDHighForceModel
from test_regional_high_force import synthetic_profile,synthetic_state


def ard_fixture():
    p=synthetic_profile();old=p['model'];em=old['magnetic_components'][1]['model']['embedding']
    gp=lambda width:dict(lengths=[1.,2.],coefficient=(np.arange(4*width).reshape(4,width)/100).tolist(),
                         center_for_training_prediction=[.72]*width)
    p['model']=dict(schema=METHOD,method=METHOD,eligible=True,training_ids=old['training_ids'],
      embedding=em,support_model=old['support_model'],pre_anchor12=[0.]*12,not_for_production=False,
      magnetic_components=[dict(sensor_name=f'S{i+3}',roi_columns=list(range(i*3,i*3+3)),family='ARD_RBF',gp=gp(3)) for i in range(4)],
      beta_component=dict(family='ARD_RBF',gp=gp(1)))
    p.update(acceptance_passed=False,validation_status='CALIBRATED_CANDIDATE')
    return p


class RegionalARDTests(unittest.TestCase):
    def setUp(self):
        self.payload=ard_fixture();self.model=RegionalARDHighForceModel(self.payload)
        self.state=synthetic_state();self.delta=np.arange(1,25)*100.;self.floor=np.full(8,20.)

    def predict(self,model=None,**kwargs):
        args=dict(delta24=self.delta,old_force=27.,tare24=self.state.quiet_raw24,noise_floor=self.floor,state=self.state,timestamp=3.)
        args.update(kwargs);return (model or self.model).predict(**args)

    def test_explicit_candidate_approval_does_not_claim_acceptance(self):
        self.assertFalse(self.model.acceptance_passed);self.assertFalse(self.model.development_only)
        self.assertEqual(self.model.validation_status,'CALIBRATED_CANDIDATE')
        self.assertIn('CALIBRATED_CANDIDATE_ACCEPTANCE_PENDING',self.predict().warnings)
        p=copy.deepcopy(self.payload);p['runtime_approved']=False
        with self.assertRaises(ValueError):RegionalARDHighForceModel(p)
        self.assertTrue(RegionalARDHighForceModel(p,allow_development=True).development_only)
        p['runtime_approved']=True;p['model']['not_for_production']=True
        with self.assertRaises(ValueError):RegionalARDHighForceModel(p)

    def test_lengths_coefficients_mapping_and_original_hash_are_validated(self):
        changes=[lambda p:p.update(original_model_sha256='0'*64),
          lambda p:p['model']['magnetic_components'][0]['gp'].update(lengths=[0.,1.]),
          lambda p:p['model']['beta_component']['gp'].update(coefficient=[[0.]]),
          lambda p:p['model']['magnetic_components'][1].update(roi_columns=[0,1,2]),
          lambda p:p['model']['embedding'].update(score_scale=[float('inf'),1.]),
          lambda p:p['model']['embedding'].update(actual_rank=7)]
        for change in changes:
            p=copy.deepcopy(self.payload);change(p)
            with self.subTest(change=str(change)),self.assertRaises(ValueError):RegionalARDHighForceModel(p)

    def test_magnetic_center_is_unused_beta_center_is_added_once(self):
        first=self.predict();p=copy.deepcopy(self.payload)
        for c in p['model']['magnetic_components']:c['gp']['center_for_training_prediction']=[1e9]*3
        same=self.predict(RegionalARDHighForceModel(p))
        np.testing.assert_array_equal(first.corrected_delta24,same.corrected_delta24)
        self.assertEqual(first.total_n,same.total_n)
        p['model']['beta_component']['gp']['center_for_training_prediction'][0]+=.02
        shifted=self.predict(RegionalARDHighForceModel(p))
        np.testing.assert_array_equal(shifted.corrected_delta24,first.corrected_delta24)
        self.assertAlmostEqual(shifted.total_n-first.total_n,.02*7,places=12)

    def test_low_identity_and_future_fault_bypass(self):
        low=self.predict(old_force=15.,state=None,tare24=None,timestamp=None)
        self.assertEqual(low.total_n,15.);np.testing.assert_array_equal(low.corrected_delta24,self.delta)
        for state,now in [(DualRisingState(),3.),(self.state,1.)]:
            r=self.predict(state=state,timestamp=now);self.assertFalse(r.applied)
            self.assertEqual(r.total_n,27.);np.testing.assert_array_equal(r.corrected_delta24,self.delta)
        invalid=self.predict(delta24=np.full(24,np.nan))
        self.assertEqual(invalid.status,'UNAVAILABLE');self.assertIsNone(invalid.total_n)


class ActualARDReplayTests(unittest.TestCase):
    def test_six_hall_source_replay_and_protected_fifteen(self):
        self._source_replay('s1_s2_margin','00_inputs_and_method.json',check_reference=True,model_class=SixHallARDHighForceModel)

    def test_loo_and_one_frozen_profile_against_source_frames(self):
        self._source_replay('regional_ard','00_inputs_and_method.json')

    def test_fixed_prior_reference_profile_against_source_frames(self):
        self._source_replay('fixed_reference_ard_noise','00_reference_and_inputs.json',check_reference=True)

    def _source_replay(self,study_name,provenance_name,check_reference=False,model_class=RegionalARDHighForceModel):
        root=Path(__file__).resolve().parents[2];study=root/'tmp/front25_repair_20261014'/study_name
        if not (study/'03_frozen_all14_candidate.json').exists():self.skipTest('Private calibration fixtures are not distributed')
        read=lambda p:json.loads(p.read_text(encoding='utf8'))
        sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
        folds=read(study/'01_nested_whole_round_leave_one_out.json')
        provenance=read(study/provenance_name)
        audits={r['id']:r for r in provenance['trial_input_audit']}
        inputs={r['id']:r for r in read(study.parent/'regional_independent/00_inputs_and_method.json')['state_inputs']}
        frozen_doc=read(study/'03_frozen_all14_candidate.json');frozen=model_class(frozen_doc,allow_development=True)
        same_rows={r['id']:r for r in read(study/'05_same_frozen_profile_calibration_replay.json')['rows']}
        cache={};source_hashes={};count=0;boundary_checks=0;reference_checks=0
        def source_rows(path):
            if path not in cache:
                source_hashes[path]=sha(path)
                with Path(path).open(encoding='utf-8-sig',newline='') as f:cache[path]=list(csv.DictReader(f))
            return cache[path]
        def check_transport(parameters):
            nonlocal reference_checks
            transport=parameters['historical_reference_transport']
            shift=np.asarray(transport['training_retared_mean12'])-np.asarray(transport['canonical_retared24'])[6:18]
            shift-=np.asarray(transport['weak_magnetic_slope12'])*(transport['reference_training_center_n']-transport['reference_calibration_n'])
            np.testing.assert_allclose(shift,transport['shift12'],rtol=0,atol=1e-9)
            np.testing.assert_allclose(np.asarray(parameters['pre_anchor12_before_reference_transport'])-shift,parameters['pre_anchor12'],rtol=0,atol=1e-9)
            self.assertEqual(transport['source']['rows'],6)
            self.assertEqual(sha(transport['source']['path']),transport['source']['sha256'])
            reference_checks+=1
        if check_reference:
            check_transport(frozen_doc['model'])
            upstream=study.parent/'regional_ard_noise_range'
            originals=read(upstream/'01_nested_whole_round_leave_one_out.json')['fold_models']
            originals.append(read(upstream/'03_frozen_all14_candidate.json')['model'])
            for prior,shifted in zip(originals,folds['fold_models']+[frozen_doc['model']]):
                for field in ('training_ids','embedding','support_model','magnetic_components','beta_component'):
                    self.assertEqual(prior[field],shifted[field],field)
        # Research dependencies are deliberately absent from the application
        # environment. Compare against its saved, frozen-parameter predictions.
        with np.load(study/'01_nested_loo_predictions.npz') as saved, np.load(study/'06_same_frozen_profile_predictions.npz') as frozen_saved:
            for row,parameters in zip(folds['rows'],folds['fold_models']):
                key=row['id'];inp=inputs[key];q=np.asarray(inp['quiet24']);tare=np.asarray(inp['tare24'])
                self.assertNotIn(key,parameters['training_ids'])
                if check_reference:check_transport(parameters)
                a=inp['dual_source']['old15'];b=inp['dual_source']['old20'];available=inp['anchor_available_time_aligned_s']
                raw15=np.asarray(a['interpolated_raw_anchor24']);raw20=np.asarray(b['interpolated_raw_anchor24'])
                state=DualRisingState(quiet_raw24=q,raw15=raw15,response15=raw15-q,rising_raw24=raw20,response24=raw20-q,
                  available_time15=available+a['anchor_available_time_s']-b['anchor_available_time_s'],available_time=available,
                  first_source15=a['frames'][0]['csv_line'],second_source15=a['frames'][1]['csv_line'],interpolation_fraction15=a['interpolation_fraction'],
                  first_source=b['frames'][0]['csv_line'],second_source=b['frames'][1]['csv_line'],interpolation_fraction=b['interpolation_fraction'])
                path=b['source']
                rows=source_rows(path);lo,hi=audits[key]['loaded_audit']['csv_lines'];selected=rows[lo-2:hi-1]
                delta=np.asarray([[float(r[f'S{ch}_retared_dB{axis}']) for ch in range(1,9) for axis in 'xyz'] for r in selected])
                old=np.asarray([float(r['scalar_resultant_raw_model_n']) for r in selected])
                floor=np.asarray([float(rows[0][f'S{ch}_spatial_noise_floor']) for ch in range(1,9)])
                prefix=key+'__';np.testing.assert_array_equal(delta,saved[prefix+'original_delta']);np.testing.assert_array_equal(old,saved[prefix+'old_total'])
                model=model_class(dict(model=parameters,not_for_production=True,original_model_sha256=ORIGINAL_MODEL_SHA256),allow_development=True)
                mask=saved[prefix+'anchor_available_mask']
                np.testing.assert_array_equal(delta,frozen_saved[prefix+'original_delta'])
                np.testing.assert_array_equal(old,frozen_saved[prefix+'old_total'])
                np.testing.assert_array_equal(mask,frozen_saved[prefix+'anchor_available_mask'])
                np.testing.assert_array_equal(saved[prefix+'middle'],frozen_saved[prefix+'middle'])
                frozen_zones=[]
                for i,(x,force) in enumerate(zip(delta,old)):
                    now=available+1 if mask[i] else available-1
                    got=model.predict(x,force,tare,floor,state,timestamp=now)
                    np.testing.assert_allclose(got.corrected_delta24,saved[prefix+'safe_delta'][i],rtol=0,atol=1e-9)
                    self.assertAlmostEqual(got.total_n,saved[prefix+'safe_total'][i],places=9)
                    np.testing.assert_allclose(got.zones,saved[prefix+'safe_zones'][i],rtol=0,atol=1e-9)
                    same=frozen.predict(x,force,tare,floor,state,timestamp=now)
                    np.testing.assert_allclose(same.corrected_delta24,frozen_saved[prefix+'safe_delta'][i],rtol=0,atol=1e-9)
                    self.assertAlmostEqual(same.total_n,frozen_saved[prefix+'safe_total'][i],places=9)
                    np.testing.assert_allclose(same.zones,frozen_saved[prefix+'safe_zones'][i],rtol=0,atol=1e-9)
                    frozen_zones.append(same.zones);count+=1
                mean=np.asarray(frozen_zones)[saved[prefix+'middle']].mean(axis=0)
                np.testing.assert_allclose(mean,same_rows[key]['safe_candidate'],rtol=0,atol=1e-9)
                if check_reference:
                    index=int(np.flatnonzero(mask&(old>20))[0]);x=delta[index].copy();force=old[index]
                    for missing,now in [(None,available+1),(state,available-1)]:
                        bypass=frozen.predict(x,force,tare,floor,missing,timestamp=now)
                        self.assertFalse(bypass.applied);self.assertEqual(bypass.total_n,force)
                        np.testing.assert_array_equal(bypass.corrected_delta24,x)
                    before=frozen.predict(x,force,tare,floor,state,timestamp=available+1)
                    self.assertTrue(before.applied)
                    increment=np.linspace(-.1,.1,24)
                    after=frozen.predict(x+increment,force,tare,floor,state,timestamp=available+1)
                    np.testing.assert_allclose(after.corrected_delta24-before.corrected_delta24,increment,rtol=0,atol=1e-9)
                    self.assertEqual(before.total_n,after.total_n)
                    np.testing.assert_array_equal(x,delta[index])
                    boundary_checks+=1
        self.assertEqual(count,4920);self.assertEqual(len(folds['rows']),14)
        low_count=0
        if check_reference:
            def check_low(rows):
                nonlocal low_count
                for r in rows:
                    delta=np.asarray([float(r[f'S{ch}_retared_dB{axis}']) for ch in range(1,9) for axis in 'xyz'])
                    force=float(r['scalar_resultant_raw_model_n'])
                    floor=np.asarray([float(r[f'S{ch}_spatial_noise_floor']) for ch in range(1,9)])
                    self.assertLessEqual(force,20.)
                    got=frozen.predict(delta,force,None,floor,None,timestamp=None)
                    self.assertFalse(got.applied);self.assertEqual(got.total_n,force)
                    np.testing.assert_array_equal(got.corrected_delta24,delta)
                    np.testing.assert_array_equal(got.zones,got.original_zones)
                    low_count+=1
            for trial in read(root/'tmp/diagnose_20261014/sensor_arrays.json'):
                check_low(source_rows(trial['path']))
            full_path=inputs['full_02']['dual_source']['old20']['source'];full_rows=source_rows(full_path)
            with np.load(root/'tmp/diagnose_20261014_full/sensor_full.npz') as full:
                for record in read(root/'tmp/diagnose_20261014_full/aligned_full.json')['records']:
                    if record['nominal_n']!=15:continue
                    lo,hi=record['sensor_episode']['sensor_time_s']
                    indices=full['csv_lines'][(full['t']>=lo-15)&(full['t']<=hi+15)]
                    check_low([full_rows[int(line)-2] for line in indices])
            self.assertEqual(low_count,8010)
        for path,digest in source_hashes.items():self.assertEqual(sha(path),digest)
        self.replay_audit=dict(study=study_name,frames=count,loo_models=14,frozen_rounds=14,
            absolute_tolerance=1e-9,source_sha256=source_hashes,source_files_unchanged=True,
            frozen_profile_sha256=sha(study/'03_frozen_all14_candidate.json'),
            loo_predictions_sha256=sha(study/'01_nested_loo_predictions.npz'),
            same_frozen_predictions_sha256=sha(study/'06_same_frozen_profile_predictions.npz'),
            frozen_pre_anchor12=frozen_doc['model']['pre_anchor12'],
            upstream_gp_coefficients_embedding_support_unchanged=bool(check_reference),
            historical_pre_anchor_transports_checked=reference_checks,
            current_increment_and_missing_future_checks=boundary_checks,
            actual_low_force_frames_unchanged=low_count,
            same_frozen_central_means_match_json=True)


if __name__=='__main__':unittest.main()
