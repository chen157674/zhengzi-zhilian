"""14-G 已知几何与裁切反例；不以这些测试代替匹配的完整真实输入。"""
import math
from pathlib import Path
import sys
import unittest

from fitness_app.overhead_press import OverheadPressConfig,OverheadPressCounter
from fitness_app.cycles import CycleConfig
from fitness_app.counting_resources import create_counter


def settings():
    # 上游肩角起点90°／最短0.8秒只作候选；其顶端即计数没有照搬。
    return OverheadPressConfig(CycleConfig(90.,1,15.,30.,8.,200,100,100,200,500,800,30000,15.),
        140.,95.,130.,1.,.6,120.,.45,20.,.3,3,'D-POSE-overhead-press-test')


def geometry(level,seq=0,seated=False,straight_arm=False,knee_dip=False):
    points=[dict(x=.5,y=.5,z=0.,visibility=.99,presence=.99,valid=True) for unused in range(33)]
    a=math.radians(40+130*level);forearm=a if straight_arm else math.radians(180-10*level)
    for side in (0,1):
        shift=side*.025;shoulder=(.5,.35);elbow=(.5+.15*math.sin(a),.35+.15*math.cos(a));wrist=(elbow[0]+.15*math.sin(forearm),elbow[1]+.15*math.cos(forearm))
        coords={11:shoulder,13:elbow,15:wrist,23:(.5,.62),25:(.57 if knee_dip else .5,.8),27:(.5,.96),31:(.56,.97)}
        if seated:coords.update({25:(.68,.62),27:(.68,.92),31:(.74,.94)})
        for i,(x,y) in coords.items():points[i+side].update(x=x+shift,y=y)
    return {'frame_seq':seq,'captured_ms':seq*150,'width':1000,'height':1000,'framing':{'person_count':1,'landmarks':points}}


def sequence():
    values=[0]*6+[.2,.4,.6,.8,1,1,1,.8,.6,.4,.2]+[0]*5
    return [geometry(v,i) for i,v in enumerate(values)]


def consume(frames,counter=None):
    counter=counter or OverheadPressCounter(settings());events=[];reasons=[]
    for f in frames:
        result=counter.process(f,{'stream_epoch':'press-fixture','identity_accepted':f.get('identity_accepted',True)});events.extend(result['events']);reasons.append(result['observation']['sides']['none']['reason'])
    return events,reasons


class OverheadPressTests(unittest.TestCase):
    def test_complete_rack_press_return_one_event_and_no_bar_path(self):
        events,_=consume(sequence());self.assertEqual(len(events),1);self.assertEqual(events[0]['side'],'none');self.assertEqual(events[0]['assessments'],[])
        self.assertNotIn('bar_path',events[0]);self.assertEqual(consume(sequence()[:13])[0],[])

    def test_seated_and_straight_arm_raise_do_not_match_preparation(self):
        levels=[0]*6+[.2,.4,.6,.8,1,1,1,.8,.6,.4,.2]+[0]*5
        self.assertEqual(consume([geometry(v,i,seated=True) for i,v in enumerate(levels)])[0],[])
        self.assertEqual(consume([geometry(v,i,straight_arm=True) for i,v in enumerate(levels)])[0],[])

    def test_top_wrist_or_foot_out_of_view_discards_partial_cycle(self):
        for parts in ((15,16),(31,32)):
            frames=sequence()
            for i in parts:frames[11]['framing']['landmarks'][i]['valid']=False
            self.assertEqual(consume(frames)[0],[])

    def test_moderate_knee_dip_during_observable_press_is_not_a_quality_veto(self):
        frames=sequence()
        for index in range(8,12):frames[index]=geometry((index-5)/5,index,knee_dip=True)
        self.assertEqual(len(consume(frames)[0]),1)

    def test_measurement_side_cannot_switch_mid_cycle(self):
        frames=sequence();frames[11]['framing']['landmarks'][15]['presence']=0
        self.assertEqual(consume(frames)[0],[])

    def test_nonrack_wrist_coordinates_cannot_invent_a_large_shoulder_excursion(self):
        frames=sequence()
        for index in range(6,17):
            frames[index]=geometry(10/130,index)
            for side in (0,1):frames[index]['framing']['landmarks'][15+side].update(x=.915+side*.025,y=.18)
        self.assertEqual(consume(frames)[0],[],'返回姿态门槛不能反过来制造未实际观察到的上推幅度')

    def test_countdown_keeps_only_ready_start_and_formal_factory_stays_closed(self):
        counter=OverheadPressCounter(settings());frames=sequence()
        for f in frames[:6]:self.assertEqual(counter.prime(f,{'stream_epoch':'press-fixture'})['events'],[])
        counter.begin();tail=[{**f,'captured_ms':f['captured_ms']-900} for f in frames[6:]]
        self.assertEqual(len(consume(tail,counter)[0]),1);self.assertIsNone(create_counter('barbell_overhead_press'))

    def test_real_cropped_reference_never_becomes_a_complete_standing_cycle(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        from prepare_movement_input import cached_frames
        events,reasons=consume(cached_frames('dvids-692514-side'))
        self.assertEqual(events,[]);self.assertIn('required_parts_missing',reasons)


if __name__=='__main__':unittest.main()
