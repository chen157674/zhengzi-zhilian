"""12 四动作的已知几何映射；显式 D-POSE 参数不代表正式识别阈值。"""
from dataclasses import replace
import copy
import math
import unittest

from fitness_app.cycles import CycleConfig
from fitness_app.action_counting import ActionCountConfig,ActionCounter,side_observation


def settings(action):
    config=CycleConfig(100 if action=='conventional_deadlift' else 165 if action=='bodyweight_squat' else 150,
        1 if action=='conventional_deadlift' else -1,10,20,5,100,100,100,100,250,800 if action in {'bodyweight_squat','standing_dumbbell_curl'} else 1000,10000)
    return ActionCountConfig(config,1,5,.02,5,120,'D-POSE-four-actions-test')


def geometry(action,left,right=None,seq=0,stiff_knee=False):
    points=[dict(x=.5,y=.5,z=0.,visibility=.95,presence=.95,valid=True) for unused in range(33)]
    for side,angle in enumerate((left,left if right is None else right)):
        a=math.radians(angle);shift=-.03 if side==0 else .03
        if action=='bodyweight_squat':
            coords={11:(.5,.3),23:(.5,.5),25:(.5+.2*math.sin(a),.5-.2*math.cos(a)),27:(.5,.9)}
        elif action=='standing_dumbbell_curl':
            coords={11:(.5,.25),13:(.5,.45),15:(.5+.2*math.sin(a),.45-.2*math.cos(a)),23:(.5,.65)}
        elif action=='standard_push_up':
            distance=.4*math.sin(a/2);shoulder_y=.85-distance
            coords={11:(.3,shoulder_y),13:(.3+math.sqrt(max(0,.2**2-(distance/2)**2)),(.85+shoulder_y)/2),15:(.3,.85),27:(.95,.85)}
            for index,x in ((23,.55),(25,.75)):coords[index]=(x,shoulder_y+(x-.3)/.65*distance)
        else:
            hip=(.55,.5);knee=(.55+(0 if stiff_knee else (170-angle)/90*.1),.7);ankle=(.55,.9)
            vx,vy=knee[0]-hip[0],knee[1]-hip[1];length=math.hypot(vx,vy)
            shoulder=(hip[0]+.25*(vx*math.cos(a)+vy*math.sin(a))/length,hip[1]+.25*(-vx*math.sin(a)+vy*math.cos(a))/length)
            coords={11:shoulder,13:(shoulder[0],shoulder[1]+.175),15:(shoulder[0],shoulder[1]+.35),23:hip,25:knee,27:ankle}
        for index,(x,y) in coords.items():points[index+side].update(x=x+shift,y=y)
    return {'frame_seq':seq,'captured_ms':seq*150,'width':1000,'height':1000,'framing':{'person_count':1,'landmarks':points}}


def sequence(action):
    values=[80,80,80,115,145,170,170,145,115,80,80] if action=='conventional_deadlift' else [170,170,170,140,110,80,80,110,140,170,170]
    return [geometry(action,value,seq=index) for index,value in enumerate(values)]


def consume(counter,frames,epoch='one'):
    events=[]
    for frame in frames:events.extend(counter.process(frame,{'stream_epoch':epoch})['events'])
    return events


class AdapterTests(unittest.TestCase):
    def test_four_geometries_complete_and_stop_at_extreme_is_not_complete(self):
        for action in ('bodyweight_squat','standard_push_up','standing_dumbbell_curl','conventional_deadlift'):
            with self.subTest(action=action):
                cfg=settings(action);frames=sequence(action);events=consume(ActionCounter(action,cfg),frames)
                self.assertEqual([event['side'] for event in events],['left','right'] if action=='standing_dumbbell_curl' else ['none'])
                self.assertEqual(consume(ActionCounter(action,cfg),frames[:7]),[])
                self.assertTrue(all(event['assessments']==[] for event in events))
                observed=side_observation(action,frames[0]['framing']['landmarks'],1000,1000,'left',cfg)
                self.assertAlmostEqual(observed['values']['primary'],80 if action=='conventional_deadlift' else 170)

    def test_curl_sides_are_independent_missing_side_is_not_inferred(self):
        action='standing_dumbbell_curl';frames=sequence(action)
        for frame in frames:
            for index in (12,14,16):frame['framing']['landmarks'][index]['presence']=0
        self.assertEqual([e['side'] for e in consume(ActionCounter(action,settings(action)),frames)],['left'])
        frames=sequence(action)
        for index,frame in enumerate(frames):
            if index>=7:
                held=geometry(action,170,80,seq=index)
                for joint in (12,14,16,24):frame['framing']['landmarks'][joint]=held['framing']['landmarks'][joint]
        self.assertEqual([e['side'] for e in consume(ActionCounter(action,settings(action)),frames)],['left'])

    def test_wrong_start_posture_and_missing_knee_motion_rejected(self):
        # 站立屈肘不能被当成标准俯卧撑。
        self.assertEqual(consume(ActionCounter('standard_push_up',settings('standard_push_up')),sequence('standing_dumbbell_curl')),[])
        frames=sequence('standard_push_up')
        for frame in frames:
            for offset in (0,1):
                knee=frame['framing']['landmarks'][25+offset]
                frame['framing']['landmarks'][27+offset].update(x=knee['x']-.05,y=knee['y']-.2)
        self.assertEqual(consume(ActionCounter('standard_push_up',settings('standard_push_up')),frames),[])
        # 动态证据门槛独立于质量：缺少要求的膝参与不能只凭髋序列算一次。
        values=[80,80,80,115,145,170,170,145,115,80,80]
        frames=[geometry('conventional_deadlift',value,seq=index,stiff_knee=True) for index,value in enumerate(values)]
        self.assertEqual(consume(ActionCounter('conventional_deadlift',settings('conventional_deadlift')),frames),[])

    def test_duplicate_old_epoch_mirror_and_missing_reset(self):
        action='bodyweight_squat';frames=sequence(action);counter=ActionCounter(action,settings(action))
        expected=consume(counter,frames);self.assertEqual(consume(counter,frames),[])
        mirrored=copy.deepcopy(frames)
        for frame in mirrored:frame['display_mirrored']=True
        self.assertEqual(consume(ActionCounter(action,settings(action)),mirrored),expected)
        self.assertEqual(len(consume(counter,frames,epoch='two')),1);self.assertEqual(consume(counter,frames,epoch='one'),[])
        counter=ActionCounter(action,settings(action));consume(counter,frames[:7]);counter.reset('paused')
        self.assertEqual(consume(counter,frames[7:]),[])

    def test_long_gap_clears_smoothing_and_corroborating_motion(self):
        action='standing_dumbbell_curl';counter=ActionCounter(action,replace(settings(action),smoothing_window=7))
        consume(counter,[geometry(action,170,seq=index) for index in range(12)])
        after=[geometry(action,value,seq=index+30) for index,value in enumerate([80,80,80,80,80,110,140,170,170,170,170,170,170])]
        self.assertEqual(consume(counter,after),[],'不能使用长 gap 前的起点平滑历史补成半个周期')

    def test_pushup_standing_up_does_not_close_unfinished_cycle(self):
        action='standard_push_up';frames=sequence(action)[:8]
        for index in range(8,12):
            frame=geometry('standing_dumbbell_curl',170,seq=index)
            for offset in (0,1):
                hip=frame['framing']['landmarks'][23+offset]
                frame['framing']['landmarks'][25+offset].update(x=hip['x'],y=.8)
                frame['framing']['landmarks'][27+offset].update(x=hip['x'],y=.95)
            frames.append(frame)
        self.assertEqual(consume(ActionCounter(action,settings(action)),frames),[])

    def test_curl_leaving_start_can_cross_low_arm_posture_between_frames(self):
        action='standing_dumbbell_curl';values=[170,170,170,80,40,40,80,130,170,170,170]
        frames=[geometry(action,value,seq=index) for index,value in enumerate(values)]
        events=consume(ActionCounter(action,settings(action)),frames)
        self.assertEqual([event['side'] for event in events],['left','right'])

    def test_countdown_primes_first_cycle_but_cannot_carry_a_half_cycle(self):
        action='bodyweight_squat';counter=ActionCounter(action,settings(action));context={'stream_epoch':'camera'}
        for index in range(20):self.assertEqual(counter.prime(geometry(action,170,seq=index),context)['events'],[])
        counter.begin();events=[]
        for index,value in enumerate([140,110,80,80,110,140,170,170]):
            frame=geometry(action,value,seq=index+20);frame['captured_ms']=index*150
            events.extend(counter.process(frame,context)['events'])
        self.assertEqual(len(events),1);self.assertEqual(events[0]['start_ms'],0)
        counter=ActionCounter(action,settings(action));frames=sequence(action)
        for frame in frames[:7]:self.assertEqual(counter.prime(frame,context)['events'],[])
        counter.begin();self.assertEqual(consume(counter,frames[7:],epoch='camera'),[])


if __name__=='__main__':unittest.main()
