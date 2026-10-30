"""后撤弓步：先辨后撤脚，再确认下沉、起身和收回；无器械／质量识别。"""
from collections import deque
from dataclasses import dataclass
import math
import statistics

from .cycles import CycleConfig,CycleTracker
from .frame_pipeline import usable_point
from .upstream import calculate_angle


@dataclass(frozen=True)
class ReverseLungeConfig:
    cycle:CycleConfig
    standing_knee_minimum:float
    minimum_knee_motion:float
    minimum_hip_descent:float
    support_tolerance:float
    return_foot_tolerance:float
    minimum_foot_projection:float
    minimum_horizontal_projection:float
    maximum_standing_separation:float
    minimum_side_separation:float
    swap_margin:float
    smoothing_window:int
    algorithm_version:str

    def __post_init__(self):
        if self.cycle.direction!=1:raise ValueError('后撤投影使用正向进度')
        values=(self.standing_knee_minimum,self.minimum_knee_motion,self.minimum_hip_descent,self.support_tolerance,
            self.return_foot_tolerance,self.minimum_foot_projection,self.minimum_horizontal_projection,self.maximum_standing_separation,self.minimum_side_separation,self.swap_margin)
        if any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in values):raise ValueError('后撤弓步参数无效')
        if not 90<self.standing_knee_minimum<180 or self.minimum_side_separation>=self.maximum_standing_separation:raise ValueError('后撤起点参数无效')
        if self.minimum_horizontal_projection>1:raise ValueError('机位投影比例无效')
        if type(self.smoothing_window) is not int or self.smoothing_window not in (1,3,5):raise ValueError('平滑窗口无效')
        if not isinstance(self.algorithm_version,str) or not self.algorithm_version:raise ValueError('需要算法版本')


def lunge_observation(frame,config):
    points=frame.get('framing',{}).get('landmarks',[]);width=frame.get('width');height=frame.get('height')
    required=(11,12,23,24,25,26,27,28,29,30,31,32)
    if len(points)!=33 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (width,height)):return None
    if any(not usable_point(points[i]) or points[i].get('valid') is False for i in required):return None
    xy={i:(points[i]['x']*width,points[i]['y']*height) for i in required}
    scale=sum(math.dist(xy[11+s],xy[23+s]) for s in (0,1))/2
    if scale<1e-6:return None
    try:knees=[calculate_angle(xy[23+s],xy[25+s],xy[27+s]) for s in (0,1)]
    except ValueError:return None
    # 足跟→脚尖只确定可观察的图像投影朝向，不能当作世界坐标深度。
    vectors=[(xy[31+s][0]-xy[29+s][0],xy[31+s][1]-xy[29+s][1]) for s in (0,1)]
    lengths=[math.hypot(*v) for v in vectors]
    forward=None
    if min(lengths)/scale>=config.minimum_foot_projection:
        directions=[tuple(v/length for v in vector) for vector,length in zip(vectors,lengths)]
        if sum(a*b for a,b in zip(*directions))>=.5:
            average=tuple(sum(v[j] for v in directions)/2 for j in (0,1));length=math.hypot(*average)
            forward=tuple(v/length for v in average)
            # 近正面足部竖向投影混入抬脚高度，不能作为可靠前后深度。
            if abs(forward[0])<config.minimum_horizontal_projection:forward=None
    hips=tuple((xy[23][i]+xy[24][i])/2 for i in (0,1))
    upright=all(xy[11+s][1]<xy[23+s][1] and abs(xy[11+s][0]-xy[23+s][0])<abs(xy[11+s][1]-xy[23+s][1]) for s in (0,1))
    separation=math.dist(xy[27],xy[28])/scale
    standing=upright and min(knees)>=config.standing_knee_minimum and config.minimum_side_separation<=separation<=config.maximum_standing_separation
    return {'xy':xy,'scale':scale,'knees':knees,'hips':hips,'forward':forward,'standing':standing}


class ReverseLungeCounter:
    def __init__(self,config:ReverseLungeConfig):
        self.config=config;self.tracker=CycleTracker(config.cycle);self.history=deque(maxlen=config.smoothing_window)
        self.epoch=None;self.retired_epochs=deque(maxlen=16);self.last_seq=None;self.last_stamp=None
        self.reset('created')

    def reset(self,reason):
        self.tracker.reset(reason);self.history.clear();self.anchor=None;self.previous=None;self.active_side=None
        self.minimum_knees=None;self.maximum_descent=0.;self.last_stamp=None;self.waiting_step_since=None

    def _output(self,reason=None,events=None):
        states={side:{'phase':self.tracker.phase if self.active_side==index else 'seeking_start',
            'reason':reason or self.tracker.reason} for index,side in enumerate(('left','right'))}
        return {'events':events or [],'observation':{'sides':states,'reason':reason or self.tracker.reason,
            'quality_available':False,'algorithm_version':self.config.algorithm_version}}

    def prime(self,frame,context):
        result=self.process(frame,context)
        if self.tracker.phase not in {'seeking_start','ready'} or result['events']:
            self.reset('prepare_waiting_for_start');result=self._output('prepare_waiting_for_start')
        result['observation']['preparing']=True
        return result

    def begin(self):
        ready=self.tracker.phase=='ready';anchor=self.anchor
        self.reset('analysis_started')
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
        if self.last_stamp is not None and stamp-self.last_stamp>self.config.cycle.maximum_gap_ms:self.reset('source_gap')
        self.last_stamp=stamp
        if frame.get('framing',{}).get('person_count')!=1 or context.get('identity_accepted') is False:
            self.reset('target_ambiguous');return self._output()
        obs=lunge_observation(frame,self.config)
        if obs is None:
            self.reset('required_parts_missing');return self._output()
        if self.previous:
            direct=sum(math.dist(obs['xy'][i],self.previous['xy'][i]) for i in (23,24,25,26,27,28))
            swapped=sum(math.dist(obs['xy'][i],self.previous['xy'][i+1 if i%2 else i-1]) for i in (23,24,25,26,27,28))
            if swapped+self.config.swap_margin*obs['scale']<direct:
                self.reset('side_identity_ambiguous');return self._output()
        self.previous=obs
        if self.tracker.phase=='seeking_start':
            if not obs['standing'] or obs['forward'] is None:
                self.reset('backward_direction_unconfirmed');return self._output()
            self.tracker.observe(0.,stamp);self.anchor=obs
            return self._output()
        anchor=self.anchor;scale=anchor['scale'];direction=anchor['forward']
        displacement=[tuple((obs['xy'][27+s][i]-anchor['xy'][27+s][i])/scale for i in (0,1)) for s in (0,1)]
        progress=[-sum(v*d for v,d in zip(delta,direction)) for delta in displacement]
        movement=[math.hypot(*delta) for delta in displacement]
        if self.active_side is None:
            candidates=[s for s in (0,1) if progress[s]>=self.config.cycle.exit_delta and movement[1-s]<=self.config.support_tolerance]
            if any(p<=-self.config.cycle.exit_delta for p in progress) or len(candidates)>1 or min(movement)>self.config.support_tolerance:
                self.reset('backward_step_unconfirmed');return self._output()
            if candidates:
                self.active_side=candidates[0];self.minimum_knees=anchor['knees'].copy();self.maximum_descent=0.
            else:
                if not obs['standing']:
                    # 提脚时双踝投影可能先接近、膝先弯曲。短暂等待后撤证据，
                    # 不能在脚尚未后撤时计侧，也不能立刻丢掉已确认站立锚点。
                    if self.waiting_step_since is None:self.waiting_step_since=stamp
                    if stamp-self.waiting_step_since>self.config.cycle.maximum_gap_ms:
                        self.reset('backward_step_unconfirmed');return self._output()
                    self.tracker.observe(0.,stamp)
                    return self._output('waiting_for_backward_step')
                self.waiting_step_since=None
                # 保留已经确认的脚部起点；不能随下沉或移动逐帧追随。
                self.tracker.observe(0.,stamp)
                return self._output()
        side=self.active_side;support=1-side
        if movement[support]>self.config.support_tolerance:
            self.reset('support_foot_moved');return self._output()
        self.minimum_knees=[min(a,b) for a,b in zip(self.minimum_knees,obs['knees'])]
        self.maximum_descent=max(self.maximum_descent,(obs['hips'][1]-anchor['hips'][1])/scale)
        self.history.append(progress[side])
        if len(self.history)<self.config.smoothing_window:return self._output('collecting_observations')
        returned=obs['standing'] and movement[side]<=self.config.return_foot_tolerance
        completed=self.tracker.observe(float(statistics.median(self.history)),stamp,return_allowed=returned)
        if completed:
            enough=min(a-b for a,b in zip(anchor['knees'],self.minimum_knees))>=self.config.minimum_knee_motion and self.maximum_descent>=self.config.minimum_hip_descent
            event={'side':('left','right')[side],'start_ms':completed['start_ms'],'end_ms':completed['end_ms'],
                'algorithm_version':self.config.algorithm_version,'feature_schema_version':'reverse-lunge-image-geometry-1','assessments':[]}
            self.reset('completed' if enough else 'insufficient_correlated_motion')
            return self._output(events=[event] if enough else [])
        if self.tracker.phase=='seeking_start':
            reason=self.tracker.reason;self.reset(reason)
        return self._output()
