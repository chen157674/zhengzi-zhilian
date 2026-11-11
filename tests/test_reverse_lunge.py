"""14-A 专属后撤／侧别／返回反例，不重复共用会话与周期状态矩阵。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from diagnose_reverse_lunge import development_config
from fitness_app.reverse_lunge import ReverseLungeCounter


def geometry(amount,side=0,seq=0,forward=False):
    points=[dict(x=.5,y=.5,z=0.,visibility=.99,presence=.99,valid=True) for unused in range(33)]
    for s in (0,1):
        x=.6 if s==0 else .5;active=s==side;hip_y=.45+.16*amount
        ankle_x=x+(.32 if forward else -.32)*amount if active else x
        coords={11:(x,hip_y-.3),23:(x,hip_y),25:(x+(-.02 if active else .1)*amount,.65+.1*amount if active else .65+.03*amount),
            27:(ankle_x,.85-.05*amount if active else .85),29:(ankle_x-.025,.87),31:(ankle_x+.06,.87)}
        for index,(px,py) in coords.items():points[index+s].update(x=px,y=py)
    return {'frame_seq':seq,'captured_ms':seq*100,'width':1000,'height':1000,'framing':{'person_count':1,'landmarks':points}}


def sequence(side=0,start=0,forward=False):
    levels=[0]*5+[.2,.4,.6,.8,1,1,1,.8,.6,.4,.2]+[0]*6
    return [geometry(value,side,index+start,forward) for index,value in enumerate(levels)]


def consume(frames):
    counter=ReverseLungeCounter(development_config());events=[];reasons=[]
    for frame in frames:
        result=counter.process(frame,{'stream_epoch':'fixture'});events.extend(result['events']);reasons.append(result['observation']['reason'])
    return events,reasons


class ReverseLungeTests(unittest.TestCase):
    def test_alternating_retreating_leg_two_each_and_half_cycle(self):
        frames=[]
        for side in (0,1,0,1):frames.extend(sequence(side,len(frames)))
        events,_=consume(frames)
        self.assertEqual([e['side'] for e in events],['left','right','left','right'])
        self.assertTrue(all(e['assessments']==[] for e in events))
        self.assertEqual(consume(sequence()[:12])[0],[])

    def test_forward_lunge_and_both_feet_translation_are_not_reverse(self):
        self.assertEqual(consume(sequence(forward=True))[0],[])
        frames=sequence()
        for frame in frames:
            p=frame['framing']['landmarks'];shift=p[27]['x']-.6
            for i in (28,30,32):p[i]['x']+=shift
        events,reasons=consume(frames);self.assertEqual(events,[]);self.assertIn('backward_step_unconfirmed',reasons)

    def test_rise_without_foot_return_and_step_without_descent(self):
        frames=sequence()[:12]
        for seq in range(12,22):
            frame=geometry(0,seq=seq)
            for i in (27,29,31):frame['framing']['landmarks'][i]['x']-=.32
            frames.append(frame)
        self.assertEqual(consume(frames)[0],[])
        frames=sequence()
        for frame in frames:
            p=frame['framing']['landmarks']
            for i in (11,12):p[i]['y']=.15
            for i in (23,24):p[i]['y']=.45
        self.assertEqual(consume(frames)[0],[])

    def test_side_swap_and_occluded_direction_do_not_guess(self):
        frames=sequence()
        for frame in frames[9:]:
            p=frame['framing']['landmarks']
            for i in (11,23,25,27,29,31):p[i],p[i+1]=p[i+1],p[i]
        events,reasons=consume(frames);self.assertEqual(events,[]);self.assertIn('side_identity_ambiguous',reasons)
        frames=sequence()
        for frame in frames:frame['framing']['landmarks'][31]['valid']=False
        self.assertEqual(consume(frames)[0],[])

    def test_front_projection_and_missing_half_cycle_do_not_complete(self):
        for vertical in (.002,.08):
            frames=sequence()
            for frame in frames:
                p=frame['framing']['landmarks']
                for s in (0,1):p[31+s].update(x=p[29+s]['x']+.002,y=p[29+s]['y']+vertical)
            self.assertEqual(consume(frames)[0],[])
        frames=sequence();frames[10]['framing']['landmarks'][27]['presence']=0
        self.assertEqual(consume(frames)[0],[])

    def test_short_initial_foot_approach_keeps_anchor_without_guessing(self):
        frames=sequence()
        # 开始提脚时投影先交会，之后才有可确认的后撤，不把等待帧算成次数。
        points=frames[5]['framing']['landmarks'];points[27]['x']=points[28]['x']+.005
        events,_=consume(frames);self.assertEqual([e['side'] for e in events],['left'])

    def test_countdown_preserves_only_standing_start(self):
        counter=ReverseLungeCounter(development_config());context={'stream_epoch':'fixture'}
        frames=sequence()
        for frame in frames[:5]:self.assertEqual(counter.prime(frame,context)['events'],[])
        counter.begin();events=[]
        for frame in frames[5:]:
            frame={**frame,'captured_ms':frame['captured_ms']-500}
            events.extend(counter.process(frame,context)['events'])
        self.assertEqual([e['side'] for e in events],['left'])
        counter=ReverseLungeCounter(development_config())
        for frame in sequence()[:12]:self.assertEqual(counter.prime(frame,context)['events'],[])
        counter.begin();events=[]
        for frame in sequence()[12:]:events.extend(counter.process(frame,context)['events'])
        self.assertEqual(events,[])


if __name__=='__main__':unittest.main()
