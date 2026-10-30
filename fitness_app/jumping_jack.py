"""14-E 手脚联合开合周期；返回确认后计数，不推断起跳或触地瞬间。"""
from collections import deque
from dataclasses import dataclass
import math
import statistics

from .cycles import CycleConfig,CycleTracker
from .frame_pipeline import usable_point
from .upstream import calculate_angle


@dataclass(frozen=True)
class JumpingJackConfig:
    cycle:CycleConfig
    maximum_closed_arm_angle:float
    maximum_closed_foot_gap:float
    minimum_closed_wrist_drop:float
    minimum_arm_excursion:float
    minimum_leg_excursion:float
    minimum_each_foot_excursion:float
    minimum_front_width:float
    smoothing_window:int
    algorithm_version:str

    def __post_init__(self):
        values=(self.maximum_closed_arm_angle,self.maximum_closed_foot_gap,self.minimum_closed_wrist_drop,self.minimum_arm_excursion,
            self.minimum_leg_excursion,self.minimum_each_foot_excursion,self.minimum_front_width)
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in values):raise ValueError('开合跳参数无效')
        if self.cycle.direction!=1 or not self.maximum_closed_arm_angle<90 or not self.minimum_arm_excursion<180 or not self.minimum_front_width<1:raise ValueError('开合跳分段参数无效')
        if type(self.smoothing_window) is not int or self.smoothing_window not in (1,3,5):raise ValueError('平滑窗口无效')
        if not isinstance(self.algorithm_version,str) or not self.algorithm_version:raise ValueError('需要算法版本')


def jack_observation(frame,config):
    points=frame.get('framing',{}).get('landmarks',[]);width=frame.get('width');height=frame.get('height')
    required=(11,12,13,14,15,16,23,24,27,28)
    if len(points)!=33 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (width,height)):return None
    if any(not usable_point(points[i]) or points[i].get('valid') is False for i in required):return None
    xy={i:(points[i]['x']*width,points[i]['y']*height) for i in required}
    torso=sum(math.dist(xy[11+s],xy[23+s]) for s in (0,1))/2;body_width=abs(xy[11][0]-xy[12][0])
    if min(torso,body_width)<1e-6:return None
    try:arms=[calculate_angle(xy[13+s],xy[11+s],xy[23+s]) for s in (0,1)]
    except ValueError:return None
    upright=all(xy[11+s][1]<xy[23+s][1]<xy[27+s][1] and abs(xy[11+s][0]-xy[23+s][0])<abs(xy[11+s][1]-xy[23+s][1]) for s in (0,1))
    front=body_width/torso>=config.minimum_front_width
    wrist_drop=[(xy[15+s][1]-xy[11+s][1])/torso for s in (0,1)]
    gap=abs(xy[27][0]-xy[28][0]);center=(xy[23][0]+xy[24][0])/2
    closed=upright and front and max(arms)<=config.maximum_closed_arm_angle and gap/body_width<=config.maximum_closed_foot_gap and min(wrist_drop)>=config.minimum_closed_wrist_drop
    return {'arms':arms,'wrist_drop':wrist_drop,'gap':gap,'center':center,'width':body_width,'ankles':sorted([xy[27][0],xy[28][0]]),
        'upright_front':upright and front,'closed':closed}


class JumpingJackCounter:
    def __init__(self,config:JumpingJackConfig):
        self.config=config;self.tracker=CycleTracker(config.cycle);self.history=deque(maxlen=config.smoothing_window)
        self.epoch=None;self.retired_epochs=deque(maxlen=16);self.last_seq=None;self.last_stamp=None;self.reset('created')

    def reset(self,reason):
        self.tracker.reset(reason);self.history.clear();self.anchor=None;self.joint_open=False;self.last_stamp=None;self.pending_view_stamp=None

    def _output(self,reason=None,event=None):
        return {'events':[event] if event else [],'observation':{'sides':{'none':{'phase':self.tracker.phase,'reason':reason or self.tracker.reason}},
            'quality_available':False,'algorithm_version':self.config.algorithm_version}}

    def prime(self,frame,context):
        result=self.process(frame,context)
        if self.tracker.phase not in {'seeking_start','ready'} or result['events']:
            self.reset('prepare_waiting_for_start');result=self._output()
        result['observation']['preparing']=True;return result

    def begin(self):
        ready=self.tracker.phase=='ready';anchor=self.anchor;self.reset('analysis_started')
        if ready:self.anchor=anchor;self.tracker.phase='ready';self.tracker.anchor=0.;self.tracker.reason=None

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
        if self.last_stamp is not None and stamp-self.last_stamp>self.config.cycle.maximum_gap_ms:self.reset('jack_sampling_gap')
        self.last_stamp=stamp
        if frame.get('framing',{}).get('person_count')!=1 or context.get('identity_accepted') is False:
            self.reset('target_ambiguous');return self._output()
        obs=jack_observation(frame,self.config)
        if obs is None:
            self.reset('required_parts_missing');return self._output()
        if not obs['upright_front']:
            # 仅运动阶段允许一帧完整可见但机位几何不确定；不采纳该帧的运动证据。
            if (self.tracker.phase=='moving' and self.pending_view_stamp is None
                    and self.tracker.last_stamp is not None
                    and stamp-self.tracker.last_stamp<=self.config.cycle.maximum_gap_ms):
                self.pending_view_stamp=stamp;self.tracker.turn_since=None;self.history.clear()
                return self._output('jack_front_view_unconfirmed')
            self.reset('jack_front_view_unconfirmed');return self._output()
        if self.pending_view_stamp is not None:
            if self.tracker.last_stamp is None or stamp-self.tracker.last_stamp>self.config.cycle.maximum_gap_ms:
                self.reset('jack_sampling_gap')
            self.pending_view_stamp=None
        if self.tracker.phase=='seeking_start':
            if not obs['closed']:
                self.reset('jack_closed_start_unconfirmed');return self._output()
            self.tracker.observe(0.,stamp);self.anchor=obs;return self._output()
        anchor=self.anchor
        arm_changes=[now-old for now,old in zip(obs['arms'],anchor['arms'])]
        leg_change=(obs['gap']-anchor['gap'])/anchor['width']
        # 开合跳不输出解剖侧别，双足按画面横向排序；不把 SDK 左右名称互换当成另一人。
        outward=[(-1 if s==0 else 1)*((obs['ankles'][s]-obs['center'])-(anchor['ankles'][s]-anchor['center']))/anchor['width'] for s in (0,1)]
        # 返回取较大进度，避免一边先合拢就提前关闭周期；张开还须同帧联合证据。
        progress=max(*(value/90 for value in arm_changes),leg_change)
        together=(min(arm_changes)>=self.config.minimum_arm_excursion and leg_change>=self.config.minimum_leg_excursion
            and min(outward)>=self.config.minimum_each_foot_excursion and max(obs['wrist_drop'])<=.15)
        self.history.append((progress,together))
        if len(self.history)<self.config.smoothing_window:
            # 已实际收到可靠源帧，只是平滑窗口未满；不能把等待窗口误判成输入断帧。
            self.tracker.last_stamp=stamp
            return self._output('collecting_observations')
        # 合拢已有独立的持续确认窗口；再套移动中位数会把短暂但真实的闭合阶段拖没。
        primary=progress if obs['closed'] else float(statistics.median(item[0] for item in self.history))
        # 几何进度接近起点而手脚尚未一起合拢时仍在返回，不能提前结束或丢掉整次。
        if not obs['closed']:primary=max(primary,self.config.cycle.start_threshold+1e-6)
        if sum(item[1] for item in self.history)>len(self.history)//2:self.joint_open=True
        completed=self.tracker.observe(primary,stamp,return_allowed=obs['closed'])
        if completed:
            event={'side':'none','start_ms':completed['start_ms'],'end_ms':completed['end_ms'],'algorithm_version':self.config.algorithm_version,
                'feature_schema_version':'jumping-jack-image-geometry-1','assessments':[]} if self.joint_open else None
            reason='completed' if event else 'jack_joint_open_unconfirmed'
            # 返回窗口已确认合拢，可作为下一次起点；不再强迫快速连续动作额外静止100ms。
            self.tracker.reset(reason);self.tracker.phase='ready';self.tracker.anchor=0.;self.tracker.last_stamp=stamp
            self.anchor=obs;self.history.clear();self.joint_open=False
            return self._output(event=event)
        if self.tracker.phase=='seeking_start':
            reason=self.tracker.reason;self.reset(reason)
        return self._output()
