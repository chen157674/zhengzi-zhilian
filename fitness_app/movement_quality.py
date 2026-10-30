"""16：支撑髋位与动态幅度开发候选；不注册正式反馈，不修改保持或次数。"""
from dataclasses import dataclass
import math
import statistics

from .frame_pipeline import usable_point
from .upstream import calculate_angle
from .records import union_segments,interval_length

SCHEMA='movement-quality-image-geometry-1'
DYNAMIC={'jumping_jack.arm_range':('jumping_jack','arm_open_degrees'),
    'jumping_jack.leg_range':('jumping_jack','feet_gap_reference_leg_ratio'),
    'high_knees.height':('high_knees','knee_above_hip_reference_leg_ratio')}


def _finite(value):return type(value) in (int,float) and math.isfinite(value)


def _up(value):
    if not isinstance(value,(tuple,list)) or len(value)!=2 or any(not _finite(v) for v in value):return None
    length=math.hypot(*value)
    return (value[0]/length,value[1]/length) if length>1e-6 else None


def _points(frame,indices):
    q=frame.get('framing',{});points=q.get('landmarks',[]);w=frame.get('width');h=frame.get('height')
    if q.get('person_count')!=1 or frame.get('identity_accepted') is False or len(points)!=33:return None
    if not all(_finite(v) and v>0 for v in (w,h)):return None
    if any(not usable_point(points[i]) or points[i].get('valid') is False for i in indices):return None
    return {i:(points[i]['x']*w,points[i]['y']*h) for i in indices}


def _dot(a,b):return sum(x*y for x,y in zip(a,b))
def _sub(a,b):return (a[0]-b[0],a[1]-b[1])


@dataclass(frozen=True)
class PlankHipRule:
    high_threshold:float|None
    low_threshold:float|None
    minimum_duration_ms:int
    maximum_gap_ms:int
    version:str
    parameter_source:str

    def __post_init__(self):
        values=(self.high_threshold,self.low_threshold)
        if any(v is None for v in values) and not all(v is None for v in values):raise ValueError('髋位两项必须显式给定或同时保留阈值缺失')
        if any(v is not None and (not _finite(v) or v<=0) for v in values):raise ValueError('髋位阈值使用正的归一化幅度')
        if any(type(v) is not int or v<=0 for v in (self.minimum_duration_ms,self.maximum_gap_ms)):raise ValueError('髋位持续参数无效')
        if not all(isinstance(v,str) and v.strip() for v in (self.version,self.parameter_source)):raise ValueError('髋位候选需要版本与依据')


def hip_offset(frame,side,up):
    if side not in {'left','right'} or (up:=_up(up)) is None:return None
    s=0 if side=='left' else 1;p=_points(frame,[11+s,23+s,27+s])
    if not p:return None
    shoulder,hip,ankle=(p[i+s] for i in (11,23,27));line=_sub(ankle,shoulder);scale=math.hypot(*line)
    if scale<1e-6:return None
    unit=(line[0]/scale,line[1]/scale);normal=(-unit[1],unit[0]);vertical_projection=_dot(normal,up)
    if abs(vertical_projection)<.5:return None  # 近竖直体轴不能支持侧面平板高低语义。
    along=_dot(_sub(hip,shoulder),unit);nearest=(shoulder[0]+along*unit[0],shoulder[1]+along*unit[1])
    if not 0<along<scale:return None
    # 法向始终朝已确认的图像上方；反朝向／镜像不会改变高低符号。
    signed=_dot(_sub(hip,nearest),normal)*(1 if vertical_projection>0 else -1)/scale
    return signed


def evaluate_plank(frames,support_intervals,*,measurement_side,view,orientation_up,orientation_confirmed,rule):
    """支撑区间由14提供；此处只裁切质量证据，不重算或扣减支撑保持量。"""
    spans=[]
    for item in support_intervals:
        a,b=item.get('start_ms'),item.get('end_ms')
        if any(type(v) is not int or v<0 for v in (a,b)) or a>=b:raise ValueError('支撑区间无效')
        spans.append({'start_ms':a,'end_ms':b,'algorithm_version':'support-input'})
    spans=union_segments(spans);times=[f.get('captured_ms') for f in frames];seqs=[f.get('frame_seq') for f in frames]
    if any(type(v) is not int or v<0 for v in times+seqs) or any(b<=a for a,b in zip(times,times[1:])) or any(b<=a for a,b in zip(seqs,seqs[1:])):raise ValueError('髋位证据帧必须按源时间递增')
    up=_up(orientation_up) if orientation_confirmed is True else None
    supported=view=='side' and up is not None and measurement_side in {'left','right'}
    values=[hip_offset(f,measurement_side,up) if supported else None for f in frames]
    def state(value):
        if not supported:return ('unassessable','view_or_orientation_unconfirmed')
        if value is None:return ('unassessable','required_parts_or_scale_missing')
        if rule.high_threshold is None:return ('unassessable','quality_threshold_unavailable')
        return ('high',None) if value>rule.high_threshold else ('low',None) if value<-rule.low_threshold else ('within_band',None)
    joint=[]
    def add(a,b,status,reason):
        if a>=b:return
        if joint and joint[-1]['end_ms']==a and (joint[-1]['status'],joint[-1]['reason'])==(status,reason):joint[-1]['end_ms']=b
        else:joint.append({'start_ms':a,'end_ms':b,'status':status,'reason':reason})
    for span in spans:
        cursor=span['start_ms']
        for i,(a,b) in enumerate(zip(times,times[1:])):
            left,right=max(a,span['start_ms']),min(b,span['end_ms'])
            if left>=right:continue
            if cursor<left:add(cursor,left,'unassessable','quality_frames_missing')
            first,second=state(values[i]),state(values[i+1])
            status,reason=first if first==second else ('unassessable','quality_transition_or_missing')
            if b-a>rule.maximum_gap_ms:status,reason='unassessable','quality_source_gap'
            add(left,right,status,reason);cursor=right
        if cursor<span['end_ms']:add(cursor,span['end_ms'],'unassessable','quality_frames_missing')
    outputs={code:[] for code in ('plank.hip_high','plank.hip_low')}
    for item in joint:
        for code,trigger in (('plank.hip_high','high'),('plank.hip_low','low')):
            status='unassessable' if item['status']=='unassessable' else 'issue' if item['status']==trigger else 'not_observed';reason=item['reason']
            if status=='issue' and item['end_ms']-item['start_ms']<rule.minimum_duration_ms:status,reason='unassessable','quality_duration_unconfirmed'
            outputs[code].append({**item,'code':code,'status':status,'reason':reason,'method_version':rule.version,'publishable':False})
    return {'feature_schema_version':SCHEMA,'publishable':False,'hold_ms':interval_length(spans),'support_intervals':spans,
        'intervals':outputs,'observations':[{'at_ms':t,'hip_signed_body_ratio':v} for t,v in zip(times,values)],
        'parameter_source':rule.parameter_source,'quality_truth':'not_independently_verified'}


@dataclass(frozen=True)
class DynamicRangeRule:
    code:str
    threshold:float|None
    stage_band:float
    minimum_stage_ms:int
    maximum_gap_ms:int
    version:str
    parameter_source:str

    def __post_init__(self):
        if self.code not in DYNAMIC:raise ValueError('动态候选判项不支持')
        if self.threshold is not None and not _finite(self.threshold):raise ValueError('动态候选阈值无效')
        if not _finite(self.stage_band) or self.stage_band<=0 or any(type(v) is not int or v<=0 for v in (self.minimum_stage_ms,self.maximum_gap_ms)):raise ValueError('动态阶段参数无效')
        if not all(isinstance(v,str) and v.strip() for v in (self.version,self.parameter_source)):raise ValueError('动态候选需要版本与依据')


def _dynamic_value(code,frame,reference,side,up):
    if code=='jumping_jack.arm_range':
        p=_points(frame,[11,12,13,14,15,16,23,24])
        if not p:return None
        try:return min(calculate_angle(p[j+s],p[11+s],p[23+s]) for s in (0,1) for j in (13,15))
        except ValueError:return None
    sides=(0,1) if code=='jumping_jack.leg_range' else (0 if side=='left' else 1,)
    indices=list({11,12,23,24}|{i+s for i in (23,25,27) for s in sides})
    p=_points(frame,indices);ref=_points(reference,indices)
    if not p or not ref:return None
    # 使用已确认落位参考的腿链投影尺度，不能随抬膝缩短的投影重新归一。
    scale=statistics.mean(math.dist(ref[23+s],ref[25+s])+math.dist(ref[25+s],ref[27+s]) for s in sides)
    if scale<1e-6 or any(_dot(_sub(ref[23+s],ref[25+s]),up)<=0 or _dot(_sub(ref[25+s],ref[27+s]),up)<=0 for s in sides):return None
    right=(-up[1],up[0])
    if code=='jumping_jack.leg_range':return abs(_dot(_sub(p[27],p[28]),right))/scale
    # 解剖侧名翻转不能被当作同一腿连续观测；不改为左右画面排序。
    before=_dot(_sub(ref[11],ref[12]),right);after=_dot(_sub(p[11],p[12]),right)
    if abs(before)<1e-6 or before*after<=0:return None
    s=sides[0];return _dot(_sub(p[25+s],p[23+s]),up)/scale


def evaluate_dynamic(event,frames,reference_frame,*,view,variant_confirmed,orientation_up,orientation_confirmed,rule):
    if event is None:return None
    action,feature=DYNAMIC[rule.code];side=event.get('side');a,b=event.get('start_ms'),event.get('end_ms')
    result={'code':rule.code,'side':side,'feature_name':feature,'feature_schema_version':SCHEMA,'publishable':False,
        'cycle_start_ms':a,'cycle_end_ms':b,'method_version':rule.version,'parameter_source':rule.parameter_source}
    def reject(reason,status='unassessable'):return {**result,'status':status,'reason':reason}
    if variant_confirmed is not True or view not in ({'front'} if action=='jumping_jack' else {'front','oblique'}):return reject('variant_or_view_unconfirmed','unsupported')
    up=_up(orientation_up) if orientation_confirmed is True else None
    if up is None:return reject('image_orientation_unknown')
    if any(type(v) is not int or v<0 for v in (a,b)) or a>=b:return reject('invalid_cycle_range')
    if side not in ({'none'} if action=='jumping_jack' else {'left','right'}):return reject('event_side_unknown')
    if rule.code!='jumping_jack.arm_range' and (not isinstance(reference_frame,dict) or type(reference_frame.get('captured_ms')) is not int or reference_frame['captured_ms']>a):return reject('start_reference_missing')
    selected=[f for f in frames if type(f.get('captured_ms')) is int and a<=f['captured_ms']<=b]
    if len(selected)<5:return reject('cycle_evidence_missing')
    times=[f['captured_ms'] for f in selected];seqs=[f.get('frame_seq') for f in selected]
    if any(type(v) is not int for v in seqs) or any(y<=x for x,y in zip(times,times[1:])) or any(y<=x for x,y in zip(seqs,seqs[1:])):return reject('non_monotonic_cycle_evidence')
    if times[0]-a>rule.maximum_gap_ms or b-times[-1]>rule.maximum_gap_ms or any(y-x>rule.maximum_gap_ms for x,y in zip(times,times[1:])):return reject('cycle_evidence_gap')
    values=[_dynamic_value(rule.code,f,reference_frame,side,up) for f in selected]
    if any(v is None for v in values):return reject('item_parts_or_reference_unobservable')
    smooth=[statistics.median(values[i-1:i+2]) for i in range(1,len(values)-1)];peak=max(range(len(smooth)),key=lambda i:smooth[i]);left=right=peak
    while left>0 and smooth[peak]-smooth[left-1]<=rule.stage_band:left-=1
    while right+1<len(smooth) and smooth[peak]-smooth[right+1]<=rule.stage_band:right+=1
    # 事件从已确认离开起点处开始。三帧中值会吃掉两端各一帧，不能因此
    # 丢弃原始首尾仍明确低于内部峰值的小幅完整周期。
    if smooth[peak]-values[0]<=rule.stage_band or smooth[peak]-values[-1]<=rule.stage_band:return reject('extreme_not_bracketed_by_cycle')
    start,end=times[left+1],times[right+1]
    if right-left+1<2 or end-start<rule.minimum_stage_ms:return reject('extreme_duration_insufficient')
    value=statistics.median(smooth[left:right+1]);status='unassessable' if rule.threshold is None else 'issue' if value<rule.threshold else 'not_observed'
    return {**result,'status':status,'reason':'quality_threshold_unavailable' if rule.threshold is None else None,
        'value':value,'threshold':rule.threshold,'start_ms':start,'end_ms':end}
