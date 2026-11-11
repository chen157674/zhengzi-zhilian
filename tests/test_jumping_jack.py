"""14-E 联合开合与快速采样专属反例，不重复共用鉴权／保存矩阵。"""
import copy
import math
from pathlib import Path
import sys
import unittest
import json
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from diagnose_jumping_jack import development_config
from fitness_app.jumping_jack import JumpingJackCounter
from fitness_app.paths import ROOT
from fitness_app.additional_counting_resources import read_manifest


def geometry(arms,legs=None,seq=0,right_arm=None,step_ms=50):
    legs=arms if legs is None else legs
    points=[dict(x=.5,y=.5,z=0.,visibility=.99,presence=.99,valid=True) for unused in range(33)]
    for side,amount in enumerate((arms,arms if right_arm is None else right_arm)):
        x=.6 if side==0 else .4;sign=1 if side==0 else -1;a=math.radians(15+125*amount)
        coords={11:(x,.3),13:(x+sign*.15*math.sin(a),.3+.15*math.cos(a)),15:(x+sign*.3*math.sin(a),.3+.3*math.cos(a)),
            23:(x,.55),27:(.5+sign*(.06+.16*legs),.88)}
        for i,(px,py) in coords.items():points[i+side].update(x=px,y=py)
    return {'frame_seq':seq,'captured_ms':seq*step_ms,'width':1000,'height':1000,'framing':{'person_count':1,'landmarks':points}}


def sequence(start=0,step_ms=50):
    levels=[0]*5+[.2,.4,.6,.8,1,1,1,.8,.6,.4,.2]+[0]*4
    return [geometry(v,seq=start+i,step_ms=step_ms) for i,v in enumerate(levels)]


def consume(frames,counter=None):
    counter=counter or JumpingJackCounter(development_config());events=[];reasons=[]
    for f in frames:
        result=counter.process(f,{'stream_epoch':'jack-fixture'});events.extend(result['events']);reasons.append(result['observation']['sides']['none']['reason'])
    return events,reasons


def uncertain_view(frame):
    frame=copy.deepcopy(frame)
    for index,x in ((11,.51),(12,.49)):
        frame['framing']['landmarks'][index]['x']=x
    return frame


class ViewObservationTests(unittest.TestCase):
    def test_one_uncertain_moving_frame_cannot_advance_phase_or_confirm_turn(self):
        frames=sequence();counter=JumpingJackCounter(development_config())
        consume(frames[:10],counter)
        self.assertEqual(counter.tracker.phase,'moving')
        extreme=counter.tracker.maximum_excursion;last=counter.tracker.last_stamp
        result=counter.process(uncertain_view(frames[10]),{'stream_epoch':'jack-fixture'})
        self.assertEqual(result['events'],[])
        self.assertEqual(result['observation']['sides']['none']['reason'],'jack_front_view_unconfirmed')
        self.assertEqual(counter.tracker.maximum_excursion,extreme)
        self.assertEqual(counter.tracker.last_stamp,last)
        self.assertIsNone(counter.tracker.turn_since)
        self.assertEqual(list(counter.history),[])
        # 同一不可用帧重传不变成第二个观测，也不推进阶段。
        self.assertEqual(counter.process(uncertain_view(frames[10]),{'stream_epoch':'jack-fixture'})['events'],[])
        self.assertEqual(len(consume(frames[11:],counter)[0]),1)

    def test_continuous_bad_view_and_missing_open_stage_still_reject(self):
        for indices in ((10,11),tuple(range(7,15)),tuple(range(5))):
            frames=sequence()
            for i in indices:frames[i]=uncertain_view(frames[i])
            with self.subTest(indices=indices):self.assertEqual(consume(frames)[0],[])

    def test_uncertainty_cannot_bridge_return_or_identity_or_keypoint_loss(self):
        for kind in ('return','person','identity','missing'):
            frames=sequence();counter=JumpingJackCounter(development_config());events=[]
            for i,frame in enumerate(frames):
                context={'stream_epoch':'jack-fixture'}
                if kind=='return' and i==17:frame=uncertain_view(frame)
                if i==10:
                    if kind=='person':frame['framing']['person_count']=2
                    if kind=='identity':context['identity_accepted']=False
                    if kind=='missing':frame['framing']['landmarks'][15]['valid']=False
                events.extend(counter.process(frame,context)['events'])
            with self.subTest(kind=kind):self.assertEqual(events,[])

    def test_gap_budget_is_measured_between_valid_observations(self):
        for added,expected in ((100,1),(140,0)):
            frames=sequence();frames[10]=uncertain_view(frames[10])
            for frame in frames[11:]:frame['captured_ms']+=added
            with self.subTest(added=added):self.assertEqual(len(consume(frames)[0]),expected)


class JumpingJackTests(unittest.TestCase):
    def test_joint_resource_uses_own_config_without_revising_prior_versions(self):
        resource=read_manifest('jumping_jack')
        self.assertEqual(read_manifest('reverse_lunge')['capability_version'],'count-14-3ea429e6a708')
        self.assertEqual(read_manifest('forearm_plank')['capability_version'],'count-14-6746cc77338d')
        with tempfile.TemporaryDirectory(prefix='jack-resource-',dir=ROOT/'.tmp/tests') as folder:
            p=Path(folder)/'resource.json'
            for key,value in (('status','development'),('parameters_sha256','0'*64),('implementation',{})):
                p.write_text(json.dumps({**resource,key:value}),encoding='utf-8')
                with self.subTest(key=key),self.assertRaises(ValueError):read_manifest('jumping_jack',p)

    def test_complete_return_and_rapid_repeated_cycles(self):
        frames=sequence();second=sequence(start=len(frames))[5:]
        # 下一次紧接上次已确认返回，不再要求额外起点静止。
        second=[{**f,'frame_seq':len(frames)+i,'captured_ms':(len(frames)+i)*50} for i,f in enumerate(second)]
        events,_=consume(frames+second);self.assertEqual(len(events),2);self.assertTrue(all(e['side']=='none' and e['assessments']==[] for e in events))
        self.assertEqual(consume(frames[:12])[0],[])

    def test_only_arms_only_legs_and_one_arm_cannot_make_joint_open(self):
        levels=[0]*5+[.2,.4,.6,.8,1,1,1,.8,.6,.4,.2]+[0]*4
        for mode in ('arms','legs','one_arm'):
            frames=[geometry(v if mode!='legs' else 0,legs=v if mode!='arms' else 0,seq=i,right_arm=0 if mode=='one_arm' else None) for i,v in enumerate(levels)]
            events,_=consume(frames);self.assertEqual(events,[],mode)

    def test_return_requires_both_hands_and_feet_closed(self):
        for held in ('arms','legs'):
            frames=sequence()[:12]
            for i in range(12,24):frames.append(geometry(1 if held=='arms' else 0,legs=1 if held=='legs' else 0,seq=i))
            self.assertEqual(consume(frames)[0],[])

    def test_missing_open_phase_reports_sampling_gap_without_invented_count(self):
        frames=sequence();kept=frames[:5]+frames[16:];events,reasons=consume(kept)
        self.assertEqual(events,[]);self.assertIn('jack_sampling_gap',reasons)
        for f in frames[8:12]:f['framing']['landmarks'][15]['valid']=False
        self.assertEqual(consume(frames)[0],[])

    def test_image_mirror_and_global_sdk_side_names_do_not_change_total(self):
        original=sequence();mirrored=copy.deepcopy(original)
        for f in mirrored:
            for p in f['framing']['landmarks']:p['x']=1-p['x']
        self.assertEqual(len(consume(original)[0]),1);self.assertEqual(len(consume(mirrored)[0]),1)
        swapped=copy.deepcopy(original)
        for f in swapped[8:11]:
            points=f['framing']['landmarks']
            for i in (11,13,15,23,27):points[i],points[i+1]=points[i+1],points[i]
        self.assertEqual(len(consume(swapped)[0]),1)

    def test_smoothing_warmup_is_not_a_false_source_gap_at_fifteen_fps(self):
        events,reasons=consume(sequence(step_ms=67));self.assertEqual(len(events),1);self.assertNotIn('source_gap',reasons);self.assertNotIn('jack_sampling_gap',reasons)

    def test_countdown_primes_closed_start_but_not_partial_opening(self):
        c=JumpingJackCounter(development_config());frames=sequence()
        for f in frames[:5]:self.assertEqual(c.prime(f,{'stream_epoch':'jack-fixture'})['events'],[])
        c.begin();tail=[{**f,'captured_ms':f['captured_ms']-250} for f in frames[5:]]
        self.assertEqual(len(consume(tail,c)[0]),1)
        c=JumpingJackCounter(development_config())
        for f in frames[:12]:self.assertEqual(c.prime(f,{'stream_epoch':'jack-fixture'})['events'],[])
        c.begin();self.assertEqual(consume(frames[12:],c)[0],[])


if __name__=='__main__':unittest.main()
