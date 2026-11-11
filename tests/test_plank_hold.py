"""14-D 新增源区间逻辑；髋位质量、未知时间和保持量独立。"""
from pathlib import Path
import sys
import unittest
import json
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from diagnose_plank_hold import development_config
from fitness_app.plank_hold import PlankHoldCounter
from fitness_app.paths import ROOT
from fitness_app.additional_counting_resources import read_manifest


def geometry(seq,hip_y=.43,supported=True):
    points=[dict(x=.5,y=.5,z=0.,visibility=.99,presence=.99,valid=True) for unused in range(33)]
    for side in (0,1):
        coords={11:(.35,.4),13:(.35,.7),15:(.2,.7),23:(.6,hip_y),25:(.78,.52 if supported else .69),27:(.92,.6),31:(.95,.7)}
        for i,(x,y) in coords.items():points[i+side].update(x=x,y=y+side*.015)
    return {'frame_seq':seq,'captured_ms':seq*100,'width':1000,'height':1000,'framing':{'person_count':1,'landmarks':points}}


def consume(frames,counter=None,processing_offset=0,epoch='plank-fixture'):
    counter=counter or PlankHoldCounter(development_config());intervals=[]
    for frame in frames:
        result=counter.process(frame,{'stream_epoch':epoch,'processing_elapsed_ms':processing_offset+frame['frame_seq']*9999})
        if result['events']:raise AssertionError('保持不得输出次数事件')
        intervals.extend(result['intervals'])
    return intervals


def total(intervals):return sum(item['end_ms']-item['start_ms'] for item in intervals)


class PlankHoldTests(unittest.TestCase):
    def test_hold_resource_without_cycle_config_validates_its_own_identity(self):
        manifest=read_manifest('forearm_plank');self.assertNotIn('cycle',manifest['config'])
        self.assertEqual(read_manifest('reverse_lunge')['capability_version'],'count-14-3ea429e6a708')
        with tempfile.TemporaryDirectory(prefix='plank-resource-',dir=ROOT/'.tmp/tests') as folder:
            path=Path(folder)/'resource.json'
            for field,value in (('status','development'),('implementation',{}),('parameters_sha256','0'*64),('calibration_sha256','0'*64)):
                path.write_text(json.dumps({**manifest,field:value}),encoding='utf-8')
                with self.subTest(field=field),self.assertRaises(ValueError):read_manifest('forearm_plank',path)

    def test_confirmed_source_intervals_are_nonoverlapping_and_processing_speed_independent(self):
        frames=[geometry(i) for i in range(11)];intervals=consume(frames)
        self.assertEqual(total(intervals),1000);self.assertEqual(intervals,consume(frames,processing_offset=900000))
        self.assertEqual((intervals[0]['start_ms'],intervals[-1]['end_ms']),(0,1000))
        self.assertTrue(all(a['end_ms']==b['start_ms'] for a,b in zip(intervals,intervals[1:])))
        self.assertTrue(all(i['kind']=='hold' and 'item_code' not in i for i in intervals))
        self.assertEqual(consume(frames[:2]),[])

    def test_hip_high_and_low_do_not_erase_observable_support(self):
        for hip_y in (.25,.58):self.assertEqual(total(consume([geometry(i,hip_y=hip_y) for i in range(11)])),1000)

    def test_no_knee_clearance_or_straight_arm_does_not_accumulate(self):
        self.assertEqual(consume([geometry(i,supported=False) for i in range(11)]),[])
        frames=[geometry(i) for i in range(11)]
        for f in frames:
            for side in (0,1):f['framing']['landmarks'][13+side].update(x=.35,y=.55+side*.015);f['framing']['landmarks'][15+side].update(x=.35,y=.7+side*.015)
        self.assertEqual(consume(frames),[])

    def test_missing_frame_and_long_gap_never_bridge_unknown_time(self):
        frames=[geometry(i) for i in range(11)];frames[3]['framing']['landmarks'][15]['valid']=False
        intervals=consume(frames);self.assertEqual(total(intervals),800)
        self.assertTrue(all(not (i['start_ms']<400 and i['end_ms']>200) for i in intervals))
        intervals=consume([geometry(i) for i in (0,1,2,8,9,10)])
        self.assertEqual(total(intervals),400);self.assertTrue(all(i['end_ms']<=200 or i['start_ms']>=800 for i in intervals))

    def test_pause_and_new_stream_restart_confirmation(self):
        counter=PlankHoldCounter(development_config());before=consume([geometry(i) for i in range(3)],counter)
        counter.reset('paused');after=consume([geometry(i) for i in range(53,56)],counter)
        self.assertEqual(total(before+after),400);self.assertEqual(after[0]['start_ms'],5300)
        same_counter=PlankHoldCounter(development_config());consume([geometry(i) for i in range(3)],same_counter)
        after=consume([geometry(i) for i in range(3)],same_counter,epoch='next');self.assertEqual(total(after),200)
        self.assertEqual(consume([geometry(i) for i in range(3,6)],same_counter,epoch='plank-fixture'),[])

    def test_duplicate_source_frames_and_ambiguous_people_add_nothing(self):
        frames=[geometry(i) for i in range(11)];self.assertEqual(total(consume([f for f in frames for unused in range(2)])),1000)
        for f in frames:f['framing']['person_count']=2
        self.assertEqual(consume(frames),[])

    def test_countdown_time_cannot_leak_into_hold(self):
        counter=PlankHoldCounter(development_config())
        for i in range(30):self.assertEqual(counter.prime(geometry(i),{'stream_epoch':'plank-fixture'})['intervals'],[])
        counter.begin();frames=[{**geometry(i+30),'captured_ms':i*100} for i in range(11)]
        self.assertEqual(total(consume(frames,counter)),1000)


if __name__=='__main__':unittest.main()
