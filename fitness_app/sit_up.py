"""14-C 屈膝仰卧起坐工程适配；完整躯干起落与卷腹／抬腿分开。"""
from collections import deque
from dataclasses import dataclass
import math
import statistics

from .cycles import CycleConfig,CycleTracker
from .frame_pipeline import usable_point
from .upstream import calculate_angle


@dataclass(frozen=True)
class SitUpConfig:
    cycle:CycleConfig
    minimum_knee_angle:float
    maximum_knee_angle:float
    minimum_knee_height:float
    minimum_upper_elevation:float
    support_motion_tolerance:float
    smoothing_window:int
    algorithm_version:str

    def __post_init__(self):
        values=(self.minimum_knee_angle,self.maximum_knee_angle,self.minimum_knee_height,self.minimum_upper_elevation,self.support_motion_tolerance)
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in values):raise ValueError('仰卧起坐参数无效')
        if not 0<self.minimum_knee_angle<self.maximum_knee_angle<180 or not self.cycle.start_threshold<self.minimum_upper_elevation<=90 or self.cycle.direction!=1:raise ValueError('仰卧起坐分段参数无效')
        if type(self.smoothing_window) is not int or self.smoothing_window not in (1,3,5):raise ValueError('平滑窗口无效')
        if not isinstance(self.algorithm_version,str) or not self.algorithm_version:raise ValueError('需要算法版本')


def sit_up_observation(frame,side,config):
    points=frame.get('framing',{}).get('landmarks',[]);width=frame.get('width');height=frame.get('height');offset=0 if side=='left' else 1
    required=(11+offset,23+offset,25+offset,27+offset)
    if len(points)!=33 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (width,height)):return None
    if any(not usable_point(points[i]) or points[i].get('valid') is False for i in required):return None
    shoulder,hip,knee,ankle=[(points[i]['x']*width,points[i]['y']*height) for i in required]
    scale=math.dist(shoulder,hip)
    if scale<1e-6:return None
    try:knee_angle=calculate_angle(hip,knee,ankle)
    except ValueError:return None
    elevation=math.degrees(math.atan2(hip[1]-shoulder[1],abs(hip[0]-shoulder[0])))
    leg_shape=config.minimum_knee_angle<=knee_angle<=config.maximum_knee_angle and (ankle[1]-knee[1])/scale>=config.minimum_knee_height
    # 只核实侧面可观察的屈膝／足部形态，不推断腹肌用力、颈部压力或触地力。
    start_shape=leg_shape and abs(elevation)<=config.cycle.start_threshold and (ankle[1]-hip[1])/scale>=-.15
    confidence=min(min(points[i]['visibility'],points[i]['presence']) for i in required)
    return {'primary':elevation,'hip':hip,'ankle':ankle,'scale':scale,'leg_shape':leg_shape,'start_shape':start_shape,'confidence':confidence}


class SitUpCounter:
    def __init__(self,config:SitUpConfig):
        self.config=config;self.tracker=CycleTracker(config.cycle);self.history=deque(maxlen=config.smoothing_window)
        self.epoch=None;self.retired_epochs=deque(maxlen=16);self.last_seq=None;self.last_stamp=None;self.reset('created')

    def reset(self,reason):
        self.tracker.reset(reason);self.history.clear();self.measurement_side=None;self.anchor=None;self.maximum_elevation=-90.;self.last_stamp=None

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
        observations={side:sit_up_observation(frame,side,self.config) for side in ('left','right')}
        if self.measurement_side and observations[self.measurement_side] is None:
            self.reset('required_parts_missing');return self._output()
        if self.measurement_side is None:
            candidates=[side for side in observations if observations[side] and observations[side]['start_shape']]
            if not candidates:
                self.reset('supine_bent_knee_start_unconfirmed');return self._output()
            self.measurement_side=max(candidates,key=lambda side:observations[side]['confidence'])
        obs=observations[self.measurement_side]
        if not obs['leg_shape']:
            self.reset('bent_knee_variant_unconfirmed');return self._output()
        if self.anchor is not None:
            moving=max(math.dist(obs[key],self.anchor[key])/self.anchor['scale'] for key in ('hip','ankle'))
            if moving>self.config.support_motion_tolerance:
                self.reset('sit_up_support_moved');return self._output()
        if self.tracker.phase=='seeking_start' and not obs['start_shape']:
            self.reset('supine_bent_knee_start_unconfirmed');return self._output()
        self.history.append(obs['primary'])
        if len(self.history)<self.config.smoothing_window:return self._output('collecting_observations')
        primary=float(statistics.median(self.history));self.maximum_elevation=max(self.maximum_elevation,primary)
        before=self.tracker.phase;old_anchor=self.tracker.anchor
        completed=self.tracker.observe(primary,stamp,return_allowed=obs['start_shape'])
        if self.tracker.phase=='ready' and (before=='seeking_start' or old_anchor!=self.tracker.anchor):self.anchor=obs;self.maximum_elevation=primary
        if completed:
            enough=self.maximum_elevation>=self.config.minimum_upper_elevation
            event={'side':'none','start_ms':completed['start_ms'],'end_ms':completed['end_ms'],'algorithm_version':self.config.algorithm_version,
                'feature_schema_version':'sit-up-image-geometry-1','assessments':[]} if enough else None
            self.reset('completed' if enough else 'full_trunk_rise_unconfirmed');return self._output(event=event)
        if self.tracker.phase=='seeking_start' and before!='seeking_start':
            reason=self.tracker.reason;self.reset(reason)
        return self._output()
