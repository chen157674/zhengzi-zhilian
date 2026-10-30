"""14-F 分腿起落工程适配；高低幅度与完成量分离，待原地真实输入校准。"""
from collections import deque
from dataclasses import dataclass
import math
import statistics

from .cycles import CycleConfig,CycleTracker
from .frame_pipeline import usable_point


@dataclass(frozen=True)
class HighKneesConfig:
    cycle:CycleConfig
    minimum_knee_lift:float
    minimum_ankle_lift:float
    minimum_front_width:float
    maximum_center_shift:float
    maximum_scale_change:float
    side_swap_margin:float
    smoothing_window:int
    algorithm_version:str

    def __post_init__(self):
        values=(self.minimum_knee_lift,self.minimum_ankle_lift,self.minimum_front_width,self.maximum_center_shift,self.maximum_scale_change,self.side_swap_margin)
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in values):raise ValueError('高抬腿几何参数无效')
        if self.cycle.direction!=1 or self.cycle.start_threshold>=0 or self.maximum_scale_change>=1:raise ValueError('高抬腿起落参数无效')
        if type(self.smoothing_window) is not int or self.smoothing_window not in (1,3,5):raise ValueError('平滑窗口无效')
        if not isinstance(self.algorithm_version,str) or not self.algorithm_version:raise ValueError('需要算法版本')


def high_knee_observation(frame,config):
    points=frame.get('framing',{}).get('landmarks',[]);width=frame.get('width');height=frame.get('height')
    shared=(11,12,23,24)
    if len(points)!=33 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (width,height)):return None
    if any(not usable_point(points[i]) or points[i].get('valid') is False for i in shared):return None
    xy=lambda i:(points[i]['x']*width,points[i]['y']*height)
    scale=sum(math.dist(xy(11+s),xy(23+s)) for s in (0,1))/2
    if scale<1e-6:return None
    center=(xy(23)[0]+xy(24)[0])/2
    upright=all(xy(11+s)[1]<xy(23+s)[1] and abs(xy(11+s)[0]-xy(23+s)[0])<abs(xy(11+s)[1]-xy(23+s)[1]) for s in (0,1))
    front=abs(xy(11)[0]-xy(12)[0])/scale>=config.minimum_front_width
    legs={}
    for side,s in (('left',0),('right',1)):
        if any(not usable_point(points[i+s]) or points[i+s].get('valid') is False for i in (25,27)):
            legs[side]=None;continue
        hip,knee,ankle=xy(23+s),xy(25+s),xy(27+s)
        legs[side]={'primary':(hip[1]-knee[1])/scale,'hip':hip,'knee':knee,'ankle':ankle,'scale':scale,
            'down':knee[1]>hip[1] and ankle[1]>knee[1]}
    return {'legs':legs,'scale':scale,'center':center,'upright_front':upright and front,'orientation':1 if xy(11)[0]>xy(12)[0] else -1}


class HighKneesCounter:
    def __init__(self,config:HighKneesConfig):
        self.config=config;self.trackers={s:CycleTracker(config.cycle) for s in ('left','right')}
        self.histories={s:deque(maxlen=config.smoothing_window) for s in self.trackers};self.epoch=None;self.retired_epochs=deque(maxlen=16)
        self.last_seq=None;self.last_stamp=None;self.origin=None;self.orientation=None;self.previous=None;self.reset('created')

    def _clear_side(self,side,reason):
        self.trackers[side].reset(reason);self.histories[side].clear();self.anchors.pop(side,None);self.lifts.pop(side,None)

    def reset(self,reason):
        self.anchors={};self.lifts={};self.last_stamp=None;self.previous=None
        for side in self.trackers:self.trackers[side].reset(reason);self.histories[side].clear()

    def _output(self,states=None,events=None,reason=None):
        states=states or {side:{'phase':tracker.phase,'reason':reason or tracker.reason} for side,tracker in self.trackers.items()}
        return {'events':events or [],'observation':{'sides':states,'quality_available':False,'algorithm_version':self.config.algorithm_version}}

    def prime(self,frame,context):
        result=self.process(frame,context)
        for side,tracker in self.trackers.items():
            if tracker.phase not in {'seeking_start','ready'}:self._clear_side(side,'prepare_waiting_for_start')
        result['events']=[];result['observation']=self._output()['observation'];result['observation']['preparing']=True;return result

    def begin(self):
        self.last_stamp=None;self.previous=None;self.lifts.clear()
        for side,tracker in self.trackers.items():
            ready=tracker.phase=='ready';value=tracker.anchor;tracker.reset('analysis_started');self.histories[side].clear()
            if ready:tracker.phase='ready';tracker.anchor=value;tracker.reason=None
            else:self.anchors.pop(side,None)

    def process(self,frame,context):
        epoch=context.get('stream_epoch',frame.get('stream_epoch','bound-source'))
        if frame.get('stream_epoch',epoch)!=epoch or epoch in self.retired_epochs:return self._output(reason='stale_stream')
        if self.epoch is not None and epoch!=self.epoch:
            self.retired_epochs.append(self.epoch);self.reset('new_stream');self.last_seq=None;self.origin=None;self.orientation=None
        self.epoch=epoch;seq=frame.get('frame_seq');stamp=frame.get('captured_ms')
        if type(seq) is not int or type(stamp) is not int or min(seq,stamp)<0:
            self.reset('invalid_frame_identity');return self._output()
        if self.last_seq is not None and seq<=self.last_seq:return self._output(reason='duplicate_or_old_frame')
        self.last_seq=seq
        if self.last_stamp is not None and stamp<=self.last_stamp:
            self.reset('non_monotonic_time');return self._output()
        if self.last_stamp is not None and stamp-self.last_stamp>self.config.cycle.maximum_gap_ms:self.reset('high_knee_sampling_gap')
        self.last_stamp=stamp
        if frame.get('framing',{}).get('person_count')!=1 or context.get('identity_accepted') is False:
            self.reset('target_ambiguous');return self._output()
        obs=high_knee_observation(frame,self.config)
        if obs is None or not obs['upright_front']:
            self.reset('high_knee_view_unconfirmed');return self._output()
        if self.orientation is None:self.orientation=obs['orientation']
        if self.orientation!=obs['orientation']:
            self.reset('high_knee_side_ambiguous');return self._output()
        if self.origin is None:self.origin=(obs['center'],obs['scale'])
        center,scale=self.origin
        if abs(obs['center']-center)/scale>self.config.maximum_center_shift or abs(obs['scale']/scale-1)>self.config.maximum_scale_change:
            # 同流保留原位置；不能一边行进一边重建起点而冒充原地。
            self.reset('high_knee_position_changed');return self._output()
        if self.previous and all(obs['legs'][s] and self.previous['legs'][s] for s in self.trackers):
            direct=sum(math.dist(obs['legs'][s][key],self.previous['legs'][s][key]) for s in self.trackers for key in ('hip','knee','ankle'))
            swapped=sum(math.dist(obs['legs'][s][key],self.previous['legs']['right' if s=='left' else 'left'][key]) for s in self.trackers for key in ('hip','knee','ankle'))
            if swapped+self.config.side_swap_margin*scale<direct:
                self.reset('high_knee_side_ambiguous');return self._output()
        self.previous=obs;events=[];states={}
        for side,tracker in self.trackers.items():
            leg=obs['legs'][side]
            if leg is None:
                self._clear_side(side,'required_parts_missing');states[side]={'phase':'unobservable','reason':'required_parts_missing'};continue
            if tracker.phase=='seeking_start' and not leg['down']:
                self._clear_side(side,'high_knee_down_unconfirmed');states[side]={'phase':'seeking_start','reason':'high_knee_down_unconfirmed'};continue
            history=self.histories[side];history.append(leg['primary'])
            if len(history)<self.config.smoothing_window:
                tracker.last_stamp=stamp;states[side]={'phase':tracker.phase,'reason':'collecting_observations'};continue
            primary=float(statistics.median(history));before=tracker.phase;old_anchor=tracker.anchor
            if side in self.anchors:
                anchor=self.anchors[side];lift=self.lifts.setdefault(side,[0.,0.])
                for index,key in enumerate(('knee','ankle')):lift[index]=max(lift[index],(anchor[key][1]-leg[key][1])/anchor['scale'])
            event=tracker.observe(primary,stamp,return_allowed=leg['down'])
            if tracker.phase=='ready' and (before=='seeking_start' or old_anchor!=tracker.anchor):self.anchors[side]=leg;self.lifts[side]=[0.,0.]
            reason=tracker.reason
            if event:
                knee_lift,ankle_lift=self.lifts.get(side,[0.,0.])
                if knee_lift>=self.config.minimum_knee_lift and ankle_lift>=self.config.minimum_ankle_lift:
                    events.append({'side':side,'start_ms':event['start_ms'],'end_ms':event['end_ms'],'algorithm_version':self.config.algorithm_version,
                        'feature_schema_version':'high-knees-image-geometry-1','assessments':[]})
                else:reason='high_knee_lift_unconfirmed'
                self.anchors[side]=leg;self.lifts[side]=[0.,0.];self.histories[side].clear()
            if tracker.phase=='seeking_start':self.anchors.pop(side,None);self.lifts.pop(side,None)
            states[side]={'phase':tracker.phase,'reason':reason}
        return self._output(states,events)
