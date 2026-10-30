"""14-B 双脚臀桥工程适配；待匹配真实输入验证，尚未注册正式能力。"""
from collections import deque
from dataclasses import dataclass
import math
import statistics

from .cycles import CycleConfig,CycleTracker
from .frame_pipeline import usable_point
from .upstream import calculate_angle


@dataclass(frozen=True)
class GluteBridgeConfig:
    cycle:CycleConfig
    minimum_knee_angle:float
    maximum_knee_angle:float
    maximum_low_torso_slope:float
    minimum_knee_height:float
    maximum_ankle_elevation:float
    support_motion_tolerance:float
    minimum_hip_lift:float
    smoothing_window:int
    algorithm_version:str

    def __post_init__(self):
        values=(self.minimum_knee_angle,self.maximum_knee_angle,self.maximum_low_torso_slope,self.minimum_knee_height,
            self.maximum_ankle_elevation,self.support_motion_tolerance,self.minimum_hip_lift)
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in values):raise ValueError('臀桥几何参数无效')
        if not 0<self.minimum_knee_angle<self.maximum_knee_angle<180 or self.cycle.direction!=1:raise ValueError('臀桥周期参数无效')
        if type(self.smoothing_window) is not int or self.smoothing_window not in (1,3,5):raise ValueError('平滑窗口无效')
        if not isinstance(self.algorithm_version,str) or not self.algorithm_version:raise ValueError('需要算法版本')


def bridge_observation(frame,side,config):
    points=frame.get('framing',{}).get('landmarks',[]);width=frame.get('width');height=frame.get('height');offset=0 if side=='left' else 1
    # 近侧肩髋链可独立可见，但双脚变式必须有双膝及双足证据，不能补远侧。
    required={11+offset,23+offset,25,26,27,28,29,30,31,32}
    if len(points)!=33 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (width,height)):return None
    if any(not usable_point(points[i]) or points[i].get('valid') is False for i in required):return None
    xy={i:(points[i]['x']*width,points[i]['y']*height) for i in required}
    shoulder,hip=xy[11+offset],xy[23+offset];scale=math.dist(shoulder,hip)
    if scale<1e-6:return None
    try:
        primary=calculate_angle(shoulder,hip,xy[25+offset])
        # 远侧髋缺失时不捏造远侧膝角，只使用膝—踝与足部的实际观察。
        knee_angle=calculate_angle(hip,xy[25+offset],xy[27+offset])
    except ValueError:return None
    body_direction=1 if hip[0]>shoulder[0] else -1
    lateral=abs(hip[0]-shoulder[0])/scale>=.6
    bent=config.minimum_knee_angle<=knee_angle<=config.maximum_knee_angle
    knees_raised=all((xy[27+s][1]-xy[25+s][1])/scale>=config.minimum_knee_height for s in (0,1))
    feet_low=all((shoulder[1]-xy[27+s][1])/scale<=config.maximum_ankle_elevation for s in (0,1))
    feet_forward=all(body_direction*(xy[27+s][0]-hip[0])/scale>.15 for s in (0,1))
    # 足、膝分布只能确认可观察的支撑形态，不能测量脚底接触力或精确触地。
    support_shape=lateral and bent and knees_raised and feet_low and feet_forward
    low=abs(hip[1]-shoulder[1])/scale<=config.maximum_low_torso_slope
    confidence=min(min(points[i]['visibility'],points[i]['presence']) for i in required)
    return {'primary':primary,'hip':hip,'shoulder':shoulder,'scale':scale,'xy':xy,'low':low,
        'support_shape':support_shape,'confidence':confidence}


class GluteBridgeCounter:
    def __init__(self,config:GluteBridgeConfig):
        self.config=config;self.tracker=CycleTracker(config.cycle);self.history=deque(maxlen=config.smoothing_window)
        self.epoch=None;self.retired_epochs=deque(maxlen=16);self.last_seq=None;self.last_stamp=None
        self.reset('created')

    def reset(self,reason):
        self.tracker.reset(reason);self.history.clear();self.measurement_side=None;self.anchor=None;self.maximum_lift=0.;self.last_stamp=None

    def _output(self,reason=None,event=None):
        return {'events':[event] if event else [],'observation':{'sides':{'none':{'phase':self.tracker.phase,'reason':reason or self.tracker.reason,
            'measurement_side':self.measurement_side}},'quality_available':False,'algorithm_version':self.config.algorithm_version}}

    def prime(self,frame,context):
        result=self.process(frame,context)
        if self.tracker.phase not in {'seeking_start','ready'} or result['events']:
            self.reset('prepare_waiting_for_start');result=self._output()
        result['observation']['preparing']=True;return result

    def begin(self):
        ready=self.tracker.phase=='ready';anchor=self.anchor;value=self.tracker.anchor;side=self.measurement_side
        self.reset('analysis_started')
        if ready:
            self.anchor=anchor;self.measurement_side=side;self.tracker.phase='ready';self.tracker.anchor=value;self.tracker.reason=None

    def process(self,frame,context):
        epoch=context.get('stream_epoch',frame.get('stream_epoch','bound-source'))
        if frame.get('stream_epoch',epoch)!=epoch or epoch in self.retired_epochs:return self._output('stale_stream')
        if self.epoch is not None and epoch!=self.epoch:
            self.retired_epochs.append(self.epoch);self.reset('new_stream');self.last_seq=None
        self.epoch=epoch;seq=frame.get('frame_seq');stamp=frame.get('captured_ms')
        if type(seq) is not int or type(stamp) is not int or min(seq,stamp)<0:
            self.reset('invalid_frame_identity');return self._output()
        if self.last_seq is not None and seq<=self.last_seq:return self._output('duplicate_or_old_frame')
        self.last_seq=seq
        if self.last_stamp is not None and stamp<=self.last_stamp:
            self.reset('non_monotonic_time');return self._output()
        if self.last_stamp is not None and stamp-self.last_stamp>self.config.cycle.maximum_gap_ms:self.reset('source_gap')
        self.last_stamp=stamp
        if frame.get('framing',{}).get('person_count')!=1 or context.get('identity_accepted') is False:
            self.reset('target_ambiguous');return self._output()
        observations={side:bridge_observation(frame,side,self.config) for side in ('left','right')}
        if self.measurement_side and observations[self.measurement_side] is None:
            self.reset('required_parts_missing');return self._output()
        if self.measurement_side is None:
            candidates=[side for side in observations if observations[side] and observations[side]['support_shape'] and observations[side]['low']]
            if not candidates:
                self.reset('bilateral_bridge_start_unconfirmed');return self._output()
            self.measurement_side=max(candidates,key=lambda side:observations[side]['confidence'])
        obs=observations[self.measurement_side]
        if not obs['support_shape']:
            self.reset('bilateral_bridge_support_unconfirmed');return self._output()
        if self.anchor is not None:
            scale=self.anchor['scale']
            moving=max(math.dist(obs['xy'][i],self.anchor['xy'][i])/scale for i in (27,28,29,30,31,32))
            shoulder_move=math.dist(obs['shoulder'],self.anchor['shoulder'])/scale
            if max(moving,shoulder_move)>self.config.support_motion_tolerance:
                self.reset('bridge_support_moved');return self._output()
            self.maximum_lift=max(self.maximum_lift,(self.anchor['hip'][1]-obs['hip'][1])/scale)
        if self.tracker.phase=='seeking_start' and not obs['low']:
            self.reset('bilateral_bridge_start_unconfirmed');return self._output()
        self.history.append(obs['primary'])
        if len(self.history)<self.config.smoothing_window:return self._output('collecting_observations')
        before=self.tracker.phase;old_anchor=self.tracker.anchor
        event=self.tracker.observe(float(statistics.median(self.history)),stamp,return_allowed=obs['low'])
        if self.tracker.phase=='ready' and (before=='seeking_start' or old_anchor!=self.tracker.anchor):self.anchor=obs;self.maximum_lift=0.
        if event:
            enough=self.maximum_lift>=self.config.minimum_hip_lift
            result={'side':'none','start_ms':event['start_ms'],'end_ms':event['end_ms'],'algorithm_version':self.config.algorithm_version,
                'feature_schema_version':'glute-bridge-image-geometry-1','assessments':[]} if enough else None
            self.reset('completed' if enough else 'insufficient_hip_lift')
            return self._output(event=result)
        if self.tracker.phase=='seeking_start' and before!='seeking_start':
            reason=self.tracker.reason;self.reset(reason)
        return self._output()
