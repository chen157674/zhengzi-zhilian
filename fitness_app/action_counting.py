"""四动作周期适配；人体几何与质量判断分离，参数须经开发诊断后提供。"""
from collections import deque
from dataclasses import dataclass
import math
import statistics

from .cycles import CycleConfig,CycleTracker
from .frame_pipeline import usable_point
from .upstream import calculate_angle

ACTIONS={'bodyweight_squat','standard_push_up','standing_dumbbell_curl','conventional_deadlift'}


@dataclass(frozen=True)
class ActionCountConfig:
    cycle:CycleConfig
    smoothing_window:int
    minimum_knee_motion:float
    minimum_body_descent:float
    minimum_trunk_motion:float
    support_knee_minimum:float
    algorithm_version:str

    def __post_init__(self):
        if type(self.smoothing_window) is not int or self.smoothing_window not in (1,3,5,7):raise ValueError('平滑窗口需为已登记奇数')
        values=(self.minimum_knee_motion,self.minimum_body_descent,self.minimum_trunk_motion,self.support_knee_minimum)
        if any(type(value) not in (int,float) or not math.isfinite(value) or value<0 for value in values):raise ValueError('运动证据参数无效')
        if not isinstance(self.algorithm_version,str) or not self.algorithm_version:raise ValueError('需要实际算法版本')


def side_observation(action,points,width,height,side,config):
    if action not in ACTIONS:raise ValueError('尚未实现该动作适配')
    offset=0 if side=='left' else 1
    required=[11,23] if action=='standing_dumbbell_curl' else [11,23,25,27]
    if action!='bodyweight_squat':required.extend([13,15])
    if len(points)!=33 or any(type(value) not in (int,float) or not math.isfinite(value) or value<=0 for value in (width,height)):return None
    if any(not usable_point(points[index+offset]) or points[index+offset].get('valid') is False for index in required):return None
    def point(index):return (points[index+offset]['x']*width,points[index+offset]['y']*height)
    shoulder,hip=point(11),point(23);torso=math.dist(shoulder,hip)
    if torso<1e-6:return None
    try:
        trunk=calculate_angle(shoulder,hip,(hip[0],hip[1]-1))
        values={'trunk':trunk}
        if action!='standing_dumbbell_curl':
            knee,ankle=point(25),point(27)
            values.update(hip=calculate_angle(shoulder,hip,knee),knee=calculate_angle(hip,knee,ankle))
        if action!='bodyweight_squat':
            elbow,wrist=point(13),point(15);values['elbow']=calculate_angle(shoulder,elbow,wrist)
        upright=shoulder[1]<hip[1] and abs(shoulder[1]-hip[1])>=abs(shoulder[0]-hip[0])
        if action=='bodyweight_squat':
            values['primary']=values['hip'];start_posture=upright and hip[1]<ankle[1]
        elif action=='standard_push_up':
            values.update(primary=values['elbow'],body_descent=(shoulder[1]-wrist[1])/torso)
            start_posture=abs(shoulder[0]-hip[0])>abs(shoulder[1]-hip[1]) and wrist[1]>=shoulder[1] and values['knee']>=config.support_knee_minimum
        elif action=='standing_dumbbell_curl':
            values['primary']=values['elbow'];start_posture=upright and elbow[1]>shoulder[1] and wrist[1]>elbow[1]
        else:
            values['primary']=values['hip'];start_posture=hip[1]<ankle[1] and shoulder[1]<ankle[1] and wrist[1]>hip[1]
        confidence=min(min(points[index+offset]['visibility'],points[index+offset]['presence']) for index in required)
        return {'values':values,'start_posture':start_posture,'confidence':confidence}
    except ValueError:return None


class ActionCounter:
    def __init__(self,action,config:ActionCountConfig):
        if action not in ACTIONS:raise ValueError('该动作尚无周期适配')
        self.action=action;self.config=config;self.bilateral=action=='standing_dumbbell_curl'
        self.trackers={side:CycleTracker(config.cycle) for side in (('left','right') if self.bilateral else ('none',))}
        self.histories={side:deque(maxlen=config.smoothing_window) for side in self.trackers}
        self.anchors={};self.motion={};self.measurement_side=None;self.last_seq=None;self.last_stamp=None;self.epoch=None;self.retired_epochs=deque(maxlen=16)

    def reset(self,reason):
        for tracker in self.trackers.values():tracker.reset(reason)
        for history in self.histories.values():history.clear()
        self.anchors.clear();self.motion.clear();self.measurement_side=None

    def _clear_side(self,side,reason):
        self.trackers[side].reset(reason);self.histories[side].clear();self.anchors.pop(side,None);self.motion.pop(side,None)

    def _evidence(self,side):
        motion=self.motion.get(side)
        if not motion:return False
        anchor,low,high=motion
        if self.action=='bodyweight_squat':return anchor['knee']-low['knee']>=self.config.minimum_knee_motion
        if self.action=='standard_push_up':return high['body_descent']-anchor['body_descent']>=self.config.minimum_body_descent
        if self.action=='conventional_deadlift':return high['knee']-anchor['knee']>=self.config.minimum_knee_motion and anchor['trunk']-low['trunk']>=self.config.minimum_trunk_motion
        return True

    def prime(self,frame,context):
        """倒计时只保留稳定起点；一旦离开便清候选，不把准备动作接入正式周期。"""
        result=self.process(frame,context)
        if result['events']:raise RuntimeError('准备阶段不应形成完整周期')
        for side,tracker in self.trackers.items():
            if tracker.phase not in {'seeking_start','ready'}:
                self._clear_side(side,'prepare_waiting_for_start')
                if side in result['observation'].get('sides',{}):result['observation']['sides'][side]={'phase':'seeking_start','reason':'prepare_waiting_for_start'}
        result['observation']['preparing']=True
        return result

    def begin(self):
        # 会话运动时间与预览流时间分开；保留已确认起点和因果窗口，清空计时。
        self.last_stamp=None;self.motion.clear()
        for side,tracker in self.trackers.items():
            ready=tracker.phase=='ready';anchor=tracker.anchor
            tracker.reset('analysis_started')
            if ready:tracker.phase='ready';tracker.anchor=anchor;tracker.reason=None
            else:self.histories[side].clear();self.anchors.pop(side,None)

    def process(self,frame,context):
        events=[];states={};epoch=context.get('stream_epoch',frame.get('stream_epoch','bound-source'))
        if frame.get('stream_epoch',epoch)!=epoch or epoch in self.retired_epochs:return {'events':[],'observation':{'reason':'stale_stream'}}
        if self.epoch is not None and epoch!=self.epoch:
            self.retired_epochs.append(self.epoch);self.reset('new_stream');self.last_seq=None;self.last_stamp=None
        self.epoch=epoch
        seq=frame.get('frame_seq');stamp=frame.get('captured_ms')
        if type(seq) is not int or type(stamp) is not int or min(seq,stamp)<0:
            self.reset('invalid_frame_identity');return {'events':[],'observation':{'reason':'invalid_frame_identity'}}
        if self.last_seq is not None and seq<=self.last_seq:return {'events':[],'observation':{'reason':'duplicate_or_old_frame'}}
        self.last_seq=seq
        if self.last_stamp is not None and stamp<=self.last_stamp:
            self.reset('non_monotonic_time');return {'events':[],'observation':{'reason':'non_monotonic_time'}}
        if self.last_stamp is not None and stamp-self.last_stamp>self.config.cycle.maximum_gap_ms:self.reset('source_gap')
        self.last_stamp=stamp
        quality=frame.get('framing',{});points=quality.get('landmarks',[])
        if quality.get('person_count')!=1 or context.get('identity_accepted') is False:
            self.reset('target_ambiguous');return {'events':[],'observation':{'reason':'target_ambiguous'}}
        observations={side:side_observation(self.action,points,frame.get('width'),frame.get('height'),side,self.config) for side in ('left','right')}
        if not self.bilateral:
            if self.measurement_side and observations[self.measurement_side] is None:
                self._clear_side('none','side_lost');self.measurement_side=None
            if self.measurement_side is None:
                candidates=[side for side in ('left','right') if observations[side] is not None]
                if candidates:self.measurement_side=max(candidates,key=lambda side:observations[side]['confidence'])
            bindings={'none':self.measurement_side}
        else:bindings={'left':'left','right':'right'}
        for event_side,measurement_side in bindings.items():
            tracker=self.trackers[event_side];obs=observations.get(measurement_side)
            if obs is None:
                self._clear_side(event_side,'required_parts_missing');states[event_side]={'phase':'unobservable','reason':'required_parts_missing'};continue
            values=obs['values']
            # 变式起点检查只用于建立起点，不以中途质量变化抹掉可观察完整周期。
            leaving_ready=tracker.phase=='ready' and self.config.cycle.direction*(values['primary']-tracker.anchor)>=self.config.cycle.exit_delta
            if (tracker.phase=='seeking_start' or tracker.phase=='ready' and not leaving_ready) and not obs['start_posture']:
                self._clear_side(event_side,'start_posture_unconfirmed');states[event_side]={'phase':'seeking_start','reason':'start_posture_unconfirmed'};continue
            history=self.histories[event_side];history.append(values.copy())
            if len(history)<self.config.smoothing_window:
                states[event_side]={'phase':'seeking_start','reason':'collecting_observations'};continue
            values={key:float(statistics.median(item[key] for item in history)) for key in values}
            before=tracker.phase;old_anchor=tracker.anchor;primary=values['primary']
            completed=tracker.observe(primary,stamp,return_allowed=obs['start_posture'])
            if tracker.reason=='return_posture_unconfirmed':
                self._clear_side(event_side,'return_posture_unconfirmed');states[event_side]={'phase':'seeking_start','reason':'return_posture_unconfirmed'};continue
            if tracker.phase=='ready' and (before=='seeking_start' or tracker.anchor!=old_anchor):self.anchors[event_side]=values.copy()
            if before=='ready' and tracker.phase=='departing':
                anchor=self.anchors.get(event_side,values).copy();self.motion[event_side]=(anchor,anchor.copy(),anchor.copy())
            if event_side in self.motion:
                anchor,low,high=self.motion[event_side]
                for key,value in values.items():low[key]=min(low[key],value);high[key]=max(high[key],value)
            reason=tracker.reason
            if completed:
                if self._evidence(event_side):
                    events.append({'side':event_side,'start_ms':completed['start_ms'],'end_ms':completed['end_ms'],
                        'algorithm_version':self.config.algorithm_version,'feature_schema_version':'cycle-image-geometry-1','assessments':[]})
                else:reason='insufficient_correlated_motion'
                self.motion.pop(event_side,None)
            if tracker.phase=='seeking_start':self.motion.pop(event_side,None)
            states[event_side]={'phase':tracker.phase,'reason':reason,'measurement_side':measurement_side}
        return {'events':events,'observation':{'sides':states,'quality_available':False,'algorithm_version':self.config.algorithm_version}}
