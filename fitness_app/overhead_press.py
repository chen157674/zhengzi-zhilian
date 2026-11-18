"""14-G 站姿推举人体周期候选；器械由所选变式限定，不伪造杠铃检测或轨迹。"""
from collections import deque
from dataclasses import dataclass
import math
import statistics

from .cycles import CycleConfig,CycleTracker
from .frame_pipeline import usable_point
from .upstream import calculate_angle


@dataclass(frozen=True)
class OverheadPressConfig:
    cycle:CycleConfig
    minimum_start_knee:float
    minimum_continuing_knee:float
    minimum_start_hip:float
    minimum_standing_leg_span:float
    minimum_continuing_leg_span:float
    maximum_rack_elbow:float
    maximum_rack_wrist_offset:float
    minimum_elbow_extension:float
    minimum_wrist_above_shoulder:float
    smoothing_window:int
    algorithm_version:str

    def __post_init__(self):
        values=(self.minimum_start_knee,self.minimum_continuing_knee,self.minimum_start_hip,self.minimum_standing_leg_span,
            self.minimum_continuing_leg_span,self.maximum_rack_elbow,self.maximum_rack_wrist_offset,self.minimum_elbow_extension,self.minimum_wrist_above_shoulder)
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in values):raise ValueError('站姿肩推参数无效')
        if not 0<self.minimum_continuing_knee<=self.minimum_start_knee<180 or not self.minimum_start_hip<180 or not self.maximum_rack_elbow<180 or self.cycle.direction!=1:raise ValueError('站姿肩推分段参数无效')
        if self.minimum_continuing_leg_span>self.minimum_standing_leg_span:raise ValueError('请扩大持续站姿的观察范围')
        if type(self.smoothing_window) is not int or self.smoothing_window not in (1,3,5):raise ValueError('平滑窗口无效')
        if not isinstance(self.algorithm_version,str) or not self.algorithm_version:raise ValueError('需要肩推算法版本')


def press_observation(frame,side,config):
    points=frame.get('framing',{}).get('landmarks',[]);width=frame.get('width');height=frame.get('height');s=0 if side=='left' else 1
    required=(11+s,13+s,15+s,23+s,25+s,27+s,31+s)
    if len(points)!=33 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (width,height)):return None
    if any(not usable_point(points[i]) or points[i].get('valid') is False for i in required):return None
    shoulder,elbow,wrist,hip,knee,ankle,toe=[(points[i]['x']*width,points[i]['y']*height) for i in required]
    scale=math.dist(shoulder,hip)
    if scale<1e-6:return None
    try:
        primary=calculate_angle(hip,shoulder,elbow);elbow_angle=calculate_angle(shoulder,elbow,wrist)
        hip_angle=calculate_angle(shoulder,hip,knee);knee_angle=calculate_angle(hip,knee,ankle)
    except ValueError:return None
    vertical=shoulder[1]<hip[1]<ankle[1] and abs(hip[0]-shoulder[0])<1.5*abs(hip[1]-shoulder[1])
    leg_span=(ankle[1]-hip[1])/scale;wrist_height=(shoulder[1]-wrist[1])/scale
    standing=vertical and knee_angle>=config.minimum_start_knee and hip_angle>=config.minimum_start_hip and leg_span>=config.minimum_standing_leg_span
    continuation=vertical and knee_angle>=config.minimum_continuing_knee and leg_span>=config.minimum_continuing_leg_span
    rack=standing and elbow_angle<=config.maximum_rack_elbow and abs(wrist_height)<=config.maximum_rack_wrist_offset
    confidence=min(min(points[i]['visibility'],points[i]['presence']) for i in required)
    return {'primary':primary,'elbow':elbow_angle,'wrist_height':wrist_height,'rack':rack,'continuation':continuation,'confidence':confidence}


class OverheadPressCounter:
    def __init__(self,config:OverheadPressConfig):
        self.config=config;self.tracker=CycleTracker(config.cycle);self.history=deque(maxlen=config.smoothing_window)
        self.epoch=None;self.retired_epochs=deque(maxlen=16);self.last_seq=None;self.last_stamp=None;self.reset('created')

    def reset(self,reason):
        self.tracker.reset(reason);self.history.clear();self.measurement_side=None;self.anchor=None;self.motion=None;self.last_stamp=None

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
        if ready:self.anchor=anchor;self.measurement_side=side;self.tracker.phase='ready';self.tracker.anchor=value;self.tracker.reason=None

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
        observations={side:press_observation(frame,side,self.config) for side in ('left','right')}
        if self.measurement_side and observations[self.measurement_side] is None:
            self.reset('required_parts_missing');return self._output()
        if self.measurement_side is None:
            candidates=[side for side in observations if observations[side] and observations[side]['rack']]
            if not candidates:
                self.reset('standing_press_start_unconfirmed');return self._output()
            self.measurement_side=max(candidates,key=lambda side:observations[side]['confidence'])
        obs=observations[self.measurement_side]
        if not obs['continuation']:
            self.reset('standing_press_variant_unconfirmed');return self._output()
        if self.tracker.phase=='seeking_start' and not obs['rack']:
            self.reset('standing_press_start_unconfirmed');return self._output()
        self.history.append(obs['primary'])
        if len(self.history)<self.config.smoothing_window:
            self.tracker.last_stamp=stamp;return self._output('collecting_observations')
        primary=float(statistics.median(self.history));before=self.tracker.phase;old_anchor=self.tracker.anchor
        # 返回接近低位但尚未恢复站姿准备形态时仍等待，不把其他姿态当完成。
        if self.tracker.phase in {'returning','confirming_return'} and not obs['rack']:primary=max(primary,self.config.cycle.start_threshold+1e-6)
        completed=self.tracker.observe(primary,stamp,return_allowed=obs['rack'])
        if self.tracker.phase=='ready' and (before=='seeking_start' or old_anchor!=self.tracker.anchor):self.anchor=obs;self.motion=None
        if before=='ready' and self.tracker.phase=='departing':self.motion={'initial_elbow':self.anchor['elbow'],'maximum_elbow':obs['elbow'],'maximum_wrist_height':obs['wrist_height'],'initial_primary':self.anchor['primary'],'maximum_primary':obs['primary']}
        if self.motion:
            self.motion['maximum_elbow']=max(self.motion['maximum_elbow'],obs['elbow']);self.motion['maximum_wrist_height']=max(self.motion['maximum_wrist_height'],obs['wrist_height'])
            self.motion['maximum_primary']=max(self.motion['maximum_primary'],obs['primary'])
        if completed:
            enough=self.motion is not None and self.motion['maximum_primary']-self.motion['initial_primary']>=self.config.cycle.minimum_excursion and self.motion['maximum_elbow']-self.motion['initial_elbow']>=self.config.minimum_elbow_extension and self.motion['maximum_wrist_height']>=self.config.minimum_wrist_above_shoulder
            event={'side':'none','start_ms':completed['start_ms'],'end_ms':completed['end_ms'],'algorithm_version':self.config.algorithm_version,
                'feature_schema_version':'overhead-press-image-geometry-1','assessments':[]} if enough else None
            self.reset('completed' if enough else 'press_extension_unconfirmed');return self._output(event=event)
        if self.tracker.phase=='seeking_start' and before!='seeking_start':
            reason=self.tracker.reason;self.reset(reason)
        return self._output()
