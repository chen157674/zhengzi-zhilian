"""14-C 已知几何专属反例，尚不代表真实视频或独立识别效果。"""
import math
import unittest
from fitness_app.sit_up import SitUpConfig,SitUpCounter
from fitness_app.cycles import CycleConfig
from fitness_app.counting_resources import create_counter


def settings():
    return SitUpConfig(CycleConfig(20.,1,10.,30.,5.,200,100,100,200,500,800,30000,10.),
        30.,150.,.15,55.,.2,3,'D-POSE-sit-up-test')


def geometry(elevation,seq=0):
    points=[dict(x=.5,y=.5,z=0.,visibility=.99,presence=.99,valid=True) for unused in range(33)]
    angle=math.radians(elevation)
    for side in (0,1):
        coords={11:(.5-.3*math.cos(angle),.75-.3*math.sin(angle)),23:(.5,.75),25:(.65,.5),27:(.8,.8)}
        for i,(x,y) in coords.items():points[i+side].update(x=x,y=y+side*.025)
    return {'frame_seq':seq,'captured_ms':seq*100,'width':1000,'height':1000,'framing':{'person_count':1,'landmarks':points}}


def sequence(maximum=80):
    levels=[0]*6+[.2,.4,.6,.8,1,1,1,.8,.6,.4,.2]+[0]*7
    return [geometry(value*maximum,i) for i,value in enumerate(levels)]


def consume(frames,counter=None):
    counter=counter or SitUpCounter(settings());events=[]
    for frame in frames:events.extend(counter.process(frame,{'stream_epoch':'sit-up-fixture'})['events'])
    return events


class SitUpTests(unittest.TestCase):
    def test_full_trunk_rise_and_return_one_event_but_top_end_is_incomplete(self):
        events=consume(sequence());self.assertEqual(len(events),1);self.assertEqual(events[0]['side'],'none');self.assertEqual(events[0]['assessments'],[])
        self.assertEqual(consume(sequence()[:13]),[])

    def test_shoulder_crunch_and_middle_rise_are_not_full_situps(self):
        self.assertEqual(consume(sequence(25)),[]);self.assertEqual(consume(sequence(45)),[])

    def test_feet_lift_and_hip_lift_variants_reset(self):
        for parts in ((27,28),(23,24)):
            frames=sequence()
            for frame in frames[9:14]:
                for i in parts:frame['framing']['landmarks'][i]['y']-=.1
            self.assertEqual(consume(frames),[])

    def test_straight_leg_variant_and_bottom_crop_are_not_accepted(self):
        frames=sequence()
        for frame in frames:
            for s in (0,1):frame['framing']['landmarks'][25+s].update(x=.65,y=.775+s*.025)
        self.assertEqual(consume(frames),[])
        frames=sequence()
        for frame in frames[:6]+frames[18:]:frame['framing']['landmarks'][11]['valid']=False;frame['framing']['landmarks'][12]['valid']=False
        self.assertEqual(consume(frames),[])

    def test_near_side_allowed_but_loss_cannot_switch_to_far_side(self):
        frames=sequence()
        for frame in frames:
            for i in (12,24,26,28):frame['framing']['landmarks'][i]['valid']=False
        self.assertEqual(len(consume(frames)),1)
        frames=sequence();frames[10]['framing']['landmarks'][11]['valid']=False
        self.assertEqual(consume(frames),[])

    def test_countdown_and_formal_factory_remain_separate(self):
        counter=SitUpCounter(settings());frames=sequence()
        for frame in frames[:6]:self.assertEqual(counter.prime(frame,{'stream_epoch':'sit-up-fixture'})['events'],[])
        counter.begin()
        self.assertEqual(len(consume([{**f,'captured_ms':f['captured_ms']-600} for f in frames[6:]],counter)),1)
        self.assertIsNone(create_counter('bent_knee_sit_up'))


if __name__=='__main__':unittest.main()
