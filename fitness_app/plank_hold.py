"""前臂支撑源时间区间；支撑类型与髋位质量分开，不按处理墙钟计时。"""
from collections import deque
from dataclasses import dataclass
import math

from .frame_pipeline import usable_point
from .upstream import calculate_angle


@dataclass(frozen=True)
class PlankHoldConfig:
    minimum_arm_angle:float
    maximum_arm_angle:float
    maximum_forearm_slope:float
    minimum_elbow_drop:float
    minimum_body_projection:float
    minimum_knee_angle:float
    minimum_knee_clearance:float
    confirm_ms:int
    maximum_gap_ms:int
    algorithm_version:str

    def __post_init__(self):
        ratios=(self.maximum_forearm_slope,self.minimum_elbow_drop,self.minimum_body_projection,self.minimum_knee_clearance)
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<v<1 for v in ratios):raise ValueError('支撑几何比例无效')
        angles=(self.minimum_arm_angle,self.maximum_arm_angle,self.minimum_knee_angle)
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<v<180 for v in angles) or self.minimum_arm_angle>=self.maximum_arm_angle:raise ValueError('支撑关节范围无效')
        if any(type(v) is not int or v<=0 for v in (self.confirm_ms,self.maximum_gap_ms)):raise ValueError('支撑源时间参数无效')
        if not isinstance(self.algorithm_version,str) or not self.algorithm_version:raise ValueError('需要保持算法版本')


def plank_observation(frame,side,config):
    points=frame.get('framing',{}).get('landmarks',[]);width=frame.get('width');height=frame.get('height');s=0 if side=='left' else 1
    required=(11+s,13+s,15+s,23+s,25+s,27+s,31+s)
    if len(points)!=33 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (width,height)):return None
    if any(not usable_point(points[i]) or points[i].get('valid') is False for i in required):return None
    shoulder,elbow,wrist,hip,knee,ankle,toe=[(points[i]['x']*width,points[i]['y']*height) for i in required]
    # 不用肩髋长度归一，避免抬髋本身改变尺度从而抹掉保持量。
    scale=math.dist(shoulder,ankle);forearm=math.dist(elbow,wrist)
    if min(scale,forearm)<1e-6 or abs(toe[0]-elbow[0])<scale*.5:return None
    try:arm_angle=calculate_angle(shoulder,elbow,wrist);knee_angle=calculate_angle(hip,knee,ankle)
    except ValueError:return None
    knee_ground_y=elbow[1]+(toe[1]-elbow[1])*(knee[0]-elbow[0])/(toe[0]-elbow[0])
    values={'arm_angle':arm_angle,'knee_angle':knee_angle,'forearm_slope':abs(elbow[1]-wrist[1])/forearm,
        'elbow_drop':(elbow[1]-shoulder[1])/scale,'body_projection':abs(ankle[0]-shoulder[0])/scale,
        'knee_clearance':(knee_ground_y-knee[1])/scale}
    supported=(config.minimum_arm_angle<=arm_angle<=config.maximum_arm_angle
        and values['forearm_slope']<=config.maximum_forearm_slope and values['elbow_drop']>=config.minimum_elbow_drop
        and values['body_projection']>=config.minimum_body_projection and knee_angle>=config.minimum_knee_angle
        and values['knee_clearance']>=config.minimum_knee_clearance)
    confidence=min(min(points[i]['visibility'],points[i]['presence']) for i in required)
    return {'supported':supported,'values':values,'confidence':confidence}


class PlankHoldCounter:
    def __init__(self,config:PlankHoldConfig):
        self.config=config;self.epoch=None;self.retired_epochs=deque(maxlen=16);self.last_seq=None;self.last_stamp=None
        self.reset('created')

    def reset(self,reason):
        self.reason=reason;self.measurement_side=None;self.candidate_start=None;self.previous_valid=None;self.confirmed=False;self.last_stamp=None

    def _output(self,intervals=None):
        phase='holding' if self.confirmed else 'confirming_support' if self.candidate_start is not None else 'seeking_support'
        return {'events':[],'intervals':intervals or [],'observation':{'sides':{'none':{'phase':phase,'reason':self.reason,
            'measurement_side':self.measurement_side}},'quality_available':False,'algorithm_version':self.config.algorithm_version}}

    def prime(self,frame,context):
        result=self.process(frame,context);result['intervals']=[];result['observation']['preparing']=True
        return result

    def begin(self):
        # 只从分析开始后的源帧累计，不能追溯倒计时的支撑时间。
        self.reset('analysis_started')

    def process(self,frame,context):
        epoch=context.get('stream_epoch',frame.get('stream_epoch','bound-source'))
        if frame.get('stream_epoch',epoch)!=epoch or epoch in self.retired_epochs:
            result=self._output();result['observation']['reason']='stale_stream';return result
        if self.epoch is not None and epoch!=self.epoch:
            self.retired_epochs.append(self.epoch);self.reset('new_stream');self.last_seq=None
        self.epoch=epoch;seq=frame.get('frame_seq');stamp=frame.get('captured_ms')
        if type(seq) is not int or type(stamp) is not int or min(seq,stamp)<0:
            self.reset('invalid_frame_identity');return self._output()
        if self.last_seq is not None and seq<=self.last_seq:
            result=self._output();result['observation']['reason']='duplicate_or_old_frame';return result
        self.last_seq=seq
        if self.last_stamp is not None and stamp<=self.last_stamp:
            self.reset('non_monotonic_time');return self._output()
        if self.last_stamp is not None and stamp-self.last_stamp>self.config.maximum_gap_ms:self.reset('source_gap')
        self.last_stamp=stamp
        if frame.get('framing',{}).get('person_count')!=1 or context.get('identity_accepted') is False:
            self.reset('target_ambiguous');return self._output()
        observations={side:plank_observation(frame,side,self.config) for side in ('left','right')}
        if self.measurement_side and observations[self.measurement_side] is None:
            self.reset('required_parts_missing');return self._output()
        if self.measurement_side is None:
            candidates=[side for side in observations if observations[side] and observations[side]['supported']]
            if not candidates:
                self.reset('forearm_support_unconfirmed');return self._output()
            self.measurement_side=max(candidates,key=lambda side:observations[side]['confidence'])
        if not observations[self.measurement_side]['supported']:
            self.reset('forearm_support_unconfirmed');return self._output()
        self.reason=None
        if self.candidate_start is None:self.candidate_start=stamp
        intervals=[]
        if self.confirmed:
            if self.previous_valid is not None and stamp>self.previous_valid:
                intervals=[{'kind':'hold','start_ms':self.previous_valid,'end_ms':stamp,'algorithm_version':self.config.algorithm_version}]
        elif stamp-self.candidate_start>=self.config.confirm_ms:
            # 缓存的确认窗口每一帧均可靠且没有超 gap；仅补这段已观察时间。
            self.confirmed=True
            intervals=[{'kind':'hold','start_ms':self.candidate_start,'end_ms':stamp,'algorithm_version':self.config.algorithm_version}]
        self.previous_valid=stamp
        return self._output(intervals)
