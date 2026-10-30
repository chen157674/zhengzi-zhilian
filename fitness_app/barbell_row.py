"""14-H 俯身划船人体周期候选；不把腕点称为杠铃轨迹或腰椎曲度。"""
from collections import deque
from dataclasses import dataclass
import math
import statistics

from .cycles import CycleConfig,CycleTracker
from .frame_pipeline import usable_point
from .upstream import calculate_angle


@dataclass(frozen=True)
class BarbellRowConfig:
    cycle:CycleConfig
    minimum_start_tilt:float
    minimum_continuing_tilt:float
    maximum_tilt:float
    minimum_knee:float
    minimum_leg_span:float
    minimum_hand_travel:float
    smoothing_window:int
    algorithm_version:str

    def __post_init__(self):
        values=(self.minimum_start_tilt,self.minimum_continuing_tilt,self.maximum_tilt,self.minimum_knee,self.minimum_leg_span,self.minimum_hand_travel)
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in values):raise ValueError('划船参数无效')
        if not 0<self.minimum_continuing_tilt<=self.minimum_start_tilt<self.maximum_tilt<110 or self.minimum_knee>=180 or self.cycle.direction!=-1:raise ValueError('划船分段参数无效')
        if type(self.smoothing_window) is not int or self.smoothing_window not in (1,3,5):raise ValueError('平滑窗口无效')
        if not isinstance(self.algorithm_version,str) or not self.algorithm_version:raise ValueError('需要划船算法版本')


def row_observation(frame,side,config):
    points=frame.get('framing',{}).get('landmarks',[]);width=frame.get('width');height=frame.get('height');s=0 if side=='left' else 1
    required=(11+s,13+s,15+s,23+s,25+s,27+s,31+s)
    if len(points)!=33 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (width,height)):return None
    if any(not usable_point(points[i]) or points[i].get('valid') is False for i in required):return None
    shoulder,elbow,wrist,hip,knee,ankle,toe=[(points[i]['x']*width,points[i]['y']*height) for i in required]
    scale=math.dist(shoulder,hip)
    if scale<1e-6:return None
    try:primary=calculate_angle(shoulder,elbow,wrist);knee_angle=calculate_angle(hip,knee,ankle)
    except ValueError:return None
    tilt=math.degrees(math.atan2(abs(shoulder[0]-hip[0]),hip[1]-shoulder[1]))
    support=hip[1]<knee[1]<ankle[1] and (ankle[1]-hip[1])/scale>=config.minimum_leg_span and knee_angle>=config.minimum_knee
    start=support and config.minimum_start_tilt<=tilt<=config.maximum_tilt and primary>=config.cycle.start_threshold and wrist[1]>shoulder[1]
    continuation=support and config.minimum_continuing_tilt<=tilt<=config.maximum_tilt
    confidence=min(min(points[i]['visibility'],points[i]['presence']) for i in required)
    return {'primary':primary,'start':start,'continuation':continuation,'confidence':confidence,'scale':scale,'hand_distance':math.dist(shoulder,wrist)}


class BarbellRowCounter:
    def __init__(self,config:BarbellRowConfig):
        self.config=config;self.tracker=CycleTracker(config.cycle);self.history=deque(maxlen=config.smoothing_window)
        self.epoch=None;self.retired_epochs=deque(maxlen=16);self.last_seq=None;self.last_stamp=None;self.reset('created')

    def reset(self,reason):
        self.tracker.reset(reason);self.history.clear();self.measurement_side=None;self.anchor=None;self.motion=None;self.last_stamp=None
        self.preferred_side=None;self.preferred_since=None

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
        observations={side:row_observation(frame,side,self.config) for side in ('left','right')}
        if self.measurement_side and observations[self.measurement_side] is None:
            self.reset('required_parts_missing');return self._output()
        if self.measurement_side is None:
            candidates=[side for side in observations if observations[side] and observations[side]['start']]
            if not candidates:
                self.reset('bent_row_start_unconfirmed');return self._output()
            self.measurement_side=max(candidates,key=lambda side:observations[side]['confidence'])
        # 准备期间才允许重选持续更清楚的一侧，并重新确认起点。
        # 已经离开原侧起点或进入周期后锁侧，不能用另一侧补完半次。
        current=observations[self.measurement_side]
        at_rest=self.tracker.phase in {'seeking_start','ready'} and current['start'] and (
            self.tracker.anchor is None or self.tracker.anchor-current['primary']<self.config.cycle.exit_delta)
        candidates=[s for s,o in observations.items() if o and o['start']] if at_rest else []
        best=max(candidates,key=lambda s:observations[s]['confidence']) if candidates else self.measurement_side
        if best!=self.measurement_side:
            if self.preferred_side!=best:self.preferred_side=best;self.preferred_since=stamp
            elif stamp-self.preferred_since>=self.config.cycle.start_confirm_ms:
                self.reset('measurement_side_prepared');self.measurement_side=best;self.last_stamp=stamp
        else:self.preferred_side=None;self.preferred_since=None
        obs=observations[self.measurement_side]
        # 站起结束直接清除半周期；不以改变肩髋参考系制造返回。
        if not obs['continuation']:
            self.reset('bent_row_variant_unconfirmed');return self._output()
        if self.tracker.phase=='seeking_start' and not obs['start']:
            self.reset('bent_row_start_unconfirmed');return self._output()
        self.history.append(obs['primary'])
        if len(self.history)<self.config.smoothing_window:
            self.tracker.last_stamp=stamp;return self._output('collecting_observations')
        primary=float(statistics.median(self.history));before=self.tracker.phase;old_anchor=self.tracker.anchor
        completed=self.tracker.observe(primary,stamp,return_allowed=obs['start'])
        if self.tracker.phase=='ready' and (before=='seeking_start' or old_anchor!=self.tracker.anchor):self.anchor=obs;self.motion=None
        if before=='ready' and self.tracker.phase=='departing':self.motion={'initial_primary':self.anchor['primary'],'minimum_primary':obs['primary'],
            'initial_distance':self.anchor['hand_distance'],'minimum_distance':obs['hand_distance'],'scale':self.anchor['scale']}
        if self.motion:
            self.motion['minimum_primary']=min(self.motion['minimum_primary'],obs['primary']);self.motion['minimum_distance']=min(self.motion['minimum_distance'],obs['hand_distance'])
        if completed:
            enough=self.motion is not None and self.motion['initial_primary']-self.motion['minimum_primary']>=self.config.cycle.minimum_excursion and (self.motion['initial_distance']-self.motion['minimum_distance'])/self.motion['scale']>=self.config.minimum_hand_travel
            event={'side':'none','start_ms':completed['start_ms'],'end_ms':completed['end_ms'],'algorithm_version':self.config.algorithm_version,
                'feature_schema_version':'barbell-row-image-geometry-1','assessments':[]} if enough else None
            self.reset('completed' if enough else 'pull_displacement_unconfirmed');return self._output(event=event)
        if self.tracker.phase=='seeking_start' and before!='seeking_start':
            reason=self.tracker.reason;self.reset(reason)
        return self._output()
