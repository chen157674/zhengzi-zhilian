"""14-B 仅 D-POSE 工程检查；没有匹配真实视频前不启用正式能力。"""
import unittest

from fitness_app.glute_bridge import GluteBridgeConfig,GluteBridgeCounter
from fitness_app.cycles import CycleConfig
from fitness_app.counting_resources import create_counter


def settings():
    # 上游低位 <120°／最短0.8秒仅作逻辑夹具初值；不是发布参数。
    return GluteBridgeConfig(CycleConfig(120.,1,10.,20.,5.,200,100,100,200,500,800,30000,8.),
        35.,155.,.3,.15,.3,.2,.1,3,'D-POSE-glute-bridge-test')


def geometry(level,seq=0):
    points=[dict(x=.5,y=.5,z=0.,visibility=.99,presence=.99,valid=True) for unused in range(33)]
    for side in (0,1):
        shift=side*.025
        coords={11:(.2,.75),23:(.5-.04*level,.75-.22*level),25:(.6,.49),27:(.7,.82),29:(.71,.84),31:(.76,.84)}
        for i,(x,y) in coords.items():points[i+side].update(x=x,y=y+shift)
    return {'frame_seq':seq,'captured_ms':seq*100,'width':1000,'height':1000,'framing':{'person_count':1,'landmarks':points}}


def sequence():
    levels=[0]*6+[.2,.4,.6,.8,1,1,1,.8,.6,.4,.2]+[0]*7
    return [geometry(level,i) for i,level in enumerate(levels)]


def consume(frames,counter=None):
    counter=counter or GluteBridgeCounter(settings());events=[];reasons=[]
    for frame in frames:
        result=counter.process(frame,{'stream_epoch':'bridge-fixture'});events.extend(result['events']);reasons.append(result['observation']['sides']['none']['reason'])
    return events,reasons


class GluteBridgeTests(unittest.TestCase):
    def test_complete_cycle_counts_only_after_return_not_at_top(self):
        frames=sequence();events,_=consume(frames)
        self.assertEqual(len(events),1);self.assertEqual(events[0]['side'],'none');self.assertEqual(events[0]['assessments'],[])
        self.assertEqual(consume(frames[:13])[0],[])
        self.assertGreater(events[0]['end_ms'],frames[13]['captured_ms'])

    def test_single_leg_straight_or_bent_raised_is_not_bilateral_bridge(self):
        for straight in (False,True):
            frames=sequence()
            for frame in frames:
                p=frame['framing']['landmarks']
                for i in (28,30,32):p[i]['y']-=.3
                if straight:p[26].update(x=.65,y=.45)
            self.assertEqual(consume(frames)[0],[])

    def test_lifting_one_foot_during_cycle_and_moving_shoulders_clears_candidate(self):
        for parts in ((28,30,32),(11,12)):
            frames=sequence()
            for frame in frames[10:13]:
                for i in parts:frame['framing']['landmarks'][i]['y']-=.1
            self.assertEqual(consume(frames)[0],[])

    def test_other_foot_occluded_cannot_be_imputed_from_near_side(self):
        frames=sequence();frames[10]['framing']['landmarks'][32]['valid']=False
        self.assertEqual(consume(frames)[0],[])
        frames=sequence()
        for frame in frames:frame['framing']['landmarks'][32]['presence']=.1
        self.assertEqual(consume(frames)[0],[])

    def test_leg_extension_alone_and_upright_body_do_not_count(self):
        frames=sequence()
        for frame in frames:
            p=frame['framing']['landmarks']
            for side in (0,1):p[23+side].update(x=.5,y=.75+side*.025)
        self.assertEqual(consume(frames)[0],[])
        frames=sequence()
        for frame in frames:
            for side in (0,1):frame['framing']['landmarks'][11+side].update(x=.5,y=.15)
        self.assertEqual(consume(frames)[0],[])

    def test_near_chain_can_be_used_but_cannot_switch_mid_cycle(self):
        frames=sequence()
        for frame in frames:
            for i in (12,24):frame['framing']['landmarks'][i]['valid']=False
        self.assertEqual(len(consume(frames)[0]),1)
        frames=sequence();frames[10]['framing']['landmarks'][11]['valid']=False
        self.assertEqual(consume(frames)[0],[])

    def test_countdown_keeps_low_start_and_production_remains_disabled(self):
        counter=GluteBridgeCounter(settings());frames=sequence();context={'stream_epoch':'bridge-fixture'}
        for frame in frames[:6]:self.assertEqual(counter.prime(frame,context)['events'],[])
        counter.begin();events=[]
        for frame in frames[6:]:
            frame={**frame,'captured_ms':frame['captured_ms']-600};events.extend(counter.process(frame,context)['events'])
        self.assertEqual(len(events),1);self.assertIsNone(create_counter('bodyweight_glute_bridge'))


if __name__=='__main__':unittest.main()
