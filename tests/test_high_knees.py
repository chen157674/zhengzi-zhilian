"""14-F 分侧／总量／低幅度逻辑，参数只作 D-POSE 夹具，尚未发布。"""
import unittest

from fitness_app.high_knees import HighKneesConfig,HighKneesCounter
from fitness_app.cycles import CycleConfig
from fitness_app.counting_resources import create_counter


def settings():
    return HighKneesConfig(CycleConfig(-.35,1,.06,.12,.03,100,33,33,50,200,200,30000,.04),
        .04,.03,.3,.6,.25,.12,3,'D-POSE-high-knees-test')


def geometry(left,right=0,seq=0):
    points=[dict(x=.5,y=.5,z=0.,visibility=.99,presence=.99,valid=True) for unused in range(33)]
    for side,amount in enumerate((left,right)):
        x=.6 if side==0 else .4
        coords={11:(x,.2),23:(x,.5),25:(x,.72-.2*amount),27:(x,.92-.25*amount)}
        for i,(px,py) in coords.items():points[i+side].update(x=px,y=py)
    return {'frame_seq':seq,'captured_ms':seq*50,'width':1000,'height':1000,'framing':{'person_count':1,'landmarks':points}}


def sequence(side='left',start=0,amplitude=1):
    levels=[0]*7+[.2,.4,.6,.8,1,1,1,.8,.6,.4,.2]+[0]*6
    return [geometry(v*amplitude if side=='left' else 0,v*amplitude if side=='right' else 0,start+i) for i,v in enumerate(levels)]


def consume(frames,counter=None):
    counter=counter or HighKneesCounter(settings());events=[];reasons=[]
    for f in frames:
        result=counter.process(f,{'stream_epoch':'high-knees-fixture'});events.extend(result['events']);reasons.extend(s['reason'] for s in result['observation']['sides'].values())
    return events,reasons


class HighKneesTests(unittest.TestCase):
    def test_three_each_side_is_six_total_and_top_without_return_is_incomplete(self):
        frames=[]
        for side in ('left','right')*3:frames.extend(sequence(side,len(frames)))
        events,_=consume(frames);self.assertEqual(len(events),6);self.assertEqual([e['side'] for e in events],['left','right']*3)
        self.assertTrue(all(e['assessments']==[] for e in events));self.assertEqual(consume(sequence()[:14])[0],[])

    def test_low_knee_below_hip_can_still_complete_a_clear_cycle(self):
        frames=sequence(amplitude=.25)
        self.assertTrue(all(f['framing']['landmarks'][25]['y']>f['framing']['landmarks'][23]['y'] for f in frames))
        self.assertEqual([e['side'] for e in consume(frames)[0]],['left'])

    def test_missing_other_leg_does_not_invent_or_erase_visible_leg_in_adapter(self):
        frames=sequence()
        for f in frames:
            for i in (26,28):f['framing']['landmarks'][i]['valid']=False
        self.assertEqual([e['side'] for e in consume(frames)[0]],['left'])
        frames=sequence();frames[10]['framing']['landmarks'][25]['valid']=False
        self.assertEqual(consume(frames)[0],[])

    def test_anatomical_side_swap_and_travelling_cannot_relabel_completed_cycles(self):
        frames=sequence()
        for f in frames[10:]:
            p=f['framing']['landmarks']
            for i in (11,23,25,27):p[i],p[i+1]=p[i+1],p[i]
        events,reasons=consume(frames);self.assertEqual(events,[]);self.assertIn('high_knee_side_ambiguous',reasons)
        frames=sequence()
        for f in frames[10:]:
            for p in f['framing']['landmarks']:p['x']-=.2
        events,reasons=consume(frames);self.assertEqual(events,[]);self.assertIn('high_knee_position_changed',reasons)

    def test_body_bounce_or_heel_kick_without_knee_rise_is_not_a_knee_cycle(self):
        frames=sequence()
        for f in frames:f['framing']['landmarks'][25]['y']=.72
        self.assertEqual(consume(frames)[0],[])
        frames=sequence()
        for f in frames:
            p=f['framing']['landmarks'];rise=.72-p[25]['y']
            for i in (11,12,23,24):p[i]['y']-=rise
            for i in (26,28):p[i]['y']-=rise
        self.assertEqual(consume(frames)[0],[])

    def test_countdown_and_missing_entire_lift_never_supply_half_cycles(self):
        counter=HighKneesCounter(settings());frames=sequence()
        for f in frames[:7]:self.assertEqual(counter.prime(f,{'stream_epoch':'high-knees-fixture'})['events'],[])
        counter.begin();tail=[{**f,'captured_ms':f['captured_ms']-350} for f in frames[7:]]
        self.assertEqual([e['side'] for e in consume(tail,counter)[0]],['left'])
        self.assertEqual(consume(frames[:7]+frames[18:])[0],[])
        self.assertIsNone(create_counter('high_knees'))


if __name__=='__main__':unittest.main()
