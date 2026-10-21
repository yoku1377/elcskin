"""Causal dual-crossing boundaries and one-pass replay of recorded input evidence."""
from pathlib import Path
import json
import unittest
import numpy as np

from local_state import CausalDualRisingStateLatch, CausalRisingStateLatch


class DualRisingStateTests(unittest.TestCase):
    def setUp(self):
        self.quiet=np.arange(24,dtype=float)*3
        self.direction=np.arange(1,25,dtype=float)

    def prime(self):
        latch=CausalDualRisingStateLatch()
        for i in range(13):latch.feed(i/10,self.quiet,0,i)
        return latch

    def loaded(self):
        latch=self.prime()
        for t,f,token in [(1.3,14,13),(1.4,16,14),(1.5,19,15),(1.6,21,16)]:
            state=latch.feed(t,self.quiet+f*self.direction,f,token)
        self.assertIsNotNone(state.response24)
        return latch,state

    def test_15_is_preserved_until_observed_20_right_frame(self):
        latch=self.prime()
        latch.feed(1.3,self.quiet+14*self.direction,14,13)
        first=latch.feed(1.4,self.quiet+16*self.direction,16,14)
        self.assertIsNone(first.response24)
        np.testing.assert_allclose(first.raw15,self.quiet+15*self.direction)
        np.testing.assert_allclose(first.response15,15*self.direction)
        self.assertEqual((first.first_source15,first.second_source15),(13,14))
        self.assertEqual(first.available_time15,1.4)
        self.assertEqual(first.interpolation_fraction15,.5)
        before=latch.feed(1.5,self.quiet+19*self.direction,19,15)
        self.assertIsNone(before.response24)
        right=latch.feed(1.6,self.quiet+21*self.direction,21,16)
        np.testing.assert_allclose(right.rising_raw24,self.quiet+20*self.direction)
        np.testing.assert_allclose(right.response24,20*self.direction)
        np.testing.assert_array_equal(right.raw15,first.raw15)
        self.assertEqual((right.first_source,right.second_source),(15,16))
        self.assertEqual(right.available_time,1.6)
        self.assertEqual(right.interpolation_fraction,.5)
        self.assertIsNone(before.response24)  # Future feed cannot mutate old snapshots.

    def test_both_crossings_in_one_observed_pair_are_causal(self):
        latch=self.prime();latch.feed(1.3,self.quiet+14*self.direction,14,13)
        s=latch.feed(1.4,self.quiet+22*self.direction,22,14)
        np.testing.assert_allclose(s.response15,15*self.direction)
        np.testing.assert_allclose(s.response24,20*self.direction)
        self.assertEqual(s.available_time,s.available_time15)
        self.assertEqual(s.interpolation_fraction15,1/8)
        self.assertEqual(s.interpolation_fraction,6/8)

    def test_no_before_frame_cannot_invent_crossing(self):
        latch=self.prime()
        s=latch.feed(1.3,self.quiet+22*self.direction,22,13)
        self.assertIsNone(s.response15);self.assertIsNone(s.response24)
        s=latch.feed(1.4,self.quiet+25*self.direction,25,14)
        self.assertIsNone(s.response15);self.assertIsNone(s.response24)

    def test_frozen_state_survives_one_zero_and_later_raw_motion(self):
        latch,s=self.loaded()
        latch.feed(1.7,self.quiet*50,0,17)
        changed=np.full(24,90000.)
        after=latch.feed(1.8,changed,26,18);changed[:]=np.nan
        self.assertIs(after,s)
        np.testing.assert_array_equal(after.quiet_raw24,self.quiet)
        np.testing.assert_allclose(after.response24,20*self.direction)
        np.testing.assert_allclose(after.response15,15*self.direction)

    def test_continuous_low_releases_and_new_load_has_new_quiet(self):
        latch,old=self.loaded();newquiet=self.quiet+600
        for i in range(17,31):state=latch.feed(i/10,newquiet,0,i)
        self.assertIsNone(state.response15);self.assertIsNone(state.response24)
        latch.feed(3.1,newquiet+14*self.direction,14,31)
        new=latch.feed(3.2,newquiet+22*self.direction,22,32)
        np.testing.assert_array_equal(new.quiet_raw24,newquiet)
        np.testing.assert_array_equal(old.quiet_raw24,self.quiet)
        self.assertEqual((new.first_source,new.second_source),(31,32))

    def test_gap_clock_and_explicit_epoch_reset_require_new_quiet(self):
        for kind in ['gap','clock','epoch']:
            with self.subTest(kind=kind):
                latch,_=self.loaded()
                if kind=='epoch':latch.reset();now=1.7
                elif kind=='gap':now=2.0
                else:now=1.5
                s=latch.feed(now,self.quiet+25*self.direction,25,99)
                self.assertIsNone(s.response15);self.assertIsNone(s.response24)
                s=latch.feed(now+.1,self.quiet+25*self.direction,25,100)
                self.assertIsNone(s.response24)
                for i in range(1,16):latch.feed(now+.1+i/10,self.quiet,0,100+i)
                latch.feed(now+1.7,self.quiet+14*self.direction,14,130)
                restored=latch.feed(now+1.8,self.quiet+22*self.direction,22,131)
                self.assertIsNotNone(restored.response24)
                self.assertEqual(restored.first_source,130)

    def test_fault_or_malformed_input_clears_both_anchors(self):
        cases=[dict(valid=False),dict(now=float('nan')),dict(raw=np.zeros(23)),
               dict(raw=np.full(24,np.nan)),dict(original_force=None),dict(source_token=None)]
        for overrides in cases:
            with self.subTest(overrides=str(overrides)):
                latch,_=self.loaded()
                args=dict(now=1.7,raw=self.quiet,original_force=25,source_token=17,valid=True)
                args.update(overrides);s=latch.feed(**args)
                self.assertEqual(s.reason,'INVALID_INPUT')
                self.assertIsNone(s.response15);self.assertIsNone(s.response24)
                self.assertIsNone(latch.previous)

    def test_legacy_15_latch_remains_distinct(self):
        old=CausalRisingStateLatch();new=CausalDualRisingStateLatch()
        for i in range(13):
            old.feed(i/10,self.quiet,0,i);new.feed(i/10,self.quiet,0,i)
        for t,f in [(1.3,14),(1.4,16)]:
            a=old.feed(t,self.quiet+f*self.direction,f,int(t*10))
            b=new.feed(t,self.quiet+f*self.direction,f,int(t*10))
        self.assertIsNotNone(a.response24);self.assertIsNone(b.response24)
        np.testing.assert_array_equal(a.response24,b.response15)


class RecordedDualReplayTests(unittest.TestCase):
    def test_one_pass_actual_14_front25_events_matches_frozen_both_anchors(self):
        root=Path(__file__).resolve().parents[2];tmp=root/'tmp'
        p15=tmp/'local_front25_20261014/residual_review/rising15_inputs.json'
        p20=tmp/'front25_repair_20261014/anchor20/source_rows.json'
        if not p15.exists() or not p20.exists():
            self.skipTest('Private recorded stream audit fixtures are not distributed with the application')
        read=lambda p:json.loads(p.read_text(encoding='utf8'))
        ref15={r['id']:r for r in read(p15)['rows']}
        ref20={r['id']:r for r in read(p20)['rows']}
        checked=[];central_frames=0

        def replay(t,raw,force,lines,keys):
            nonlocal central_frames
            latch=CausalDualRisingStateLatch();observed={};frozen={}
            for now,x,f,line in zip(t,np.asarray(raw).reshape(-1,24),force,lines):
                state=latch.feed(now,x,f,int(line))
                for key in keys:
                    a,b=ref15[key],ref20[key]
                    if not(a['load_latch_time_s']<=now<=b['audit_episode_end_s']):continue
                    if now<b['anchor_available_time_s']:
                        self.assertIsNone(state.response24,key)
                    if int(line)==a['frames'][1]['csv_line']:
                        np.testing.assert_allclose(state.raw15,a['interpolated_raw_anchor24'],rtol=0,atol=1e-9)
                        np.testing.assert_allclose(state.response15,a['raw_anchor_minus_quiet24'],rtol=0,atol=1e-9)
                        self.assertEqual(state.available_time15,a['anchor_available_time_s'])
                        self.assertEqual((state.first_source15,state.second_source15),tuple(z['csv_line'] for z in a['frames']))
                        self.assertAlmostEqual(state.interpolation_fraction15,a['interpolation_fraction'],places=12)
                    if int(line)==b['frames'][1]['csv_line']:
                        np.testing.assert_allclose(state.rising_raw24,b['interpolated_raw_anchor24'],rtol=0,atol=1e-9)
                        np.testing.assert_allclose(state.response24,b['raw_anchor_minus_quiet24'],rtol=0,atol=1e-9)
                        np.testing.assert_allclose(state.raw15,a['interpolated_raw_anchor24'],rtol=0,atol=1e-9)
                        np.testing.assert_allclose(state.quiet_raw24,np.asarray(b['interpolated_raw_anchor24'])-np.asarray(b['raw_anchor_minus_quiet24']),rtol=0,atol=1e-9)
                        self.assertEqual(state.available_time,b['anchor_available_time_s'])
                        self.assertEqual((state.first_source,state.second_source),tuple(z['csv_line'] for z in b['frames']))
                        self.assertAlmostEqual(state.interpolation_fraction,b['interpolation_fraction'],places=12)
                        observed[key]=True;frozen[key]=state
                    start=b['central10_start_sensor_time_s']
                    if start<=now<=start+10:
                        self.assertIs(state,frozen[key],key);central_frames+=1
            self.assertEqual(set(observed),set(keys));checked.extend(keys)

        for a in read(tmp/'diagnose_20261014_25N/sensor_arrays.json'):
            replay(a['t'],a['raw'],a['total'],a['csv_line'],[f'morning25_{a["id"]:02d}'])
        with np.load(tmp/'diagnose_20261014_full/sensor_full.npz') as a:
            replay(a['t'],a['raw'],a['total'],a['csv_lines'],['full_02','full_03','full_04','full_06'])
        self.assertEqual(len(checked),14)
        self.assertEqual(central_frames,1384)


if __name__=='__main__':unittest.main()
