"""步骤15开发候选：完整周期内的二维幅度证据；不注册正式纠错能力。

阈值必须由调用方显式提供并记录依据。当前没有独立技术点标签，所有返回
均标 publishable=False，不能直接写入正式问题事件或触发训练反馈。
"""
from dataclasses import dataclass
import math
import statistics

from .frame_pipeline import usable_point
from .upstream import calculate_angle

SCHEMA='basic-range-image-geometry-1'
SPECS={
    'bodyweight_squat':('squat.range',{'side'},'hip_above_knee_thigh_ratio','greater','bottom'),
    'standard_push_up':('push_up.range',{'side'},'shoulder_above_elbow_arm_ratio','greater','bottom'),
    'reverse_lunge':('reverse_lunge.range',{'oblique'},'rear_knee_degrees','greater','bottom'),
    'bodyweight_glute_bridge':('glute_bridge.range',{'side'},'shoulder_hip_knee_image_degrees','less','top'),
    'bent_knee_sit_up':('sit_up.range',{'side'},'trunk_elevation_degrees','less','top'),
    'standing_dumbbell_curl':('curl.range',{'oblique','side'},'elbow_excursion_degrees','less','flexed'),
}


@dataclass(frozen=True)
class CandidateRule:
    code:str
    feature_schema_version:str
    threshold:float|None
    stage_band:float
    minimum_stage_ms:int
    maximum_gap_ms:int
    version:str
    parameter_source:str

    def __post_init__(self):
        if self.code not in {s[0] for s in SPECS.values()} or self.feature_schema_version!=SCHEMA:raise ValueError('候选判项或特征版本不匹配')
        values=(self.stage_band,) if self.threshold is None else (self.threshold,self.stage_band)
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in values) or self.stage_band<=0:raise ValueError('候选参数必须为有限数')
        if any(type(v) is not int or v<=0 for v in (self.minimum_stage_ms,self.maximum_gap_ms)):raise ValueError('候选源时间参数无效')
        if not all(isinstance(v,str) and v.strip() for v in (self.version,self.parameter_source)):raise ValueError('候选方法必须记录版本和参数依据')


def _measure(action,frame,side):
    """只使用图像x/y与实际宽高；不混用world或归一化z。"""
    points=frame.get('framing',{}).get('landmarks',[]);w=frame.get('width');h=frame.get('height');offset=0 if side=='left' else 1
    if frame.get('framing',{}).get('person_count')!=1 or frame.get('identity_accepted') is False:return None
    if len(points)!=33 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in (w,h)):return None
    base=(11,13,15,23) if action=='standing_dumbbell_curl' else (11,13,15,23,25,27) if action=='standard_push_up' else (11,23,25,27)
    indices=[i+offset for i in base]
    if action=='bodyweight_glute_bridge':indices+=list({25,26,27,28,29,30,31,32}-set(indices))
    if action=='reverse_lunge':indices+=list({23,24,25,26,27,28}-set(indices))
    if any(not usable_point(points[i]) or points[i].get('valid') is False for i in indices):return None
    p=lambda i:(points[i+offset]['x']*w,points[i+offset]['y']*h)
    try:
        shoulder,hip=p(11),p(23);scale=math.dist(shoulder,hip)
        if scale<1e-6:return None
        if action=='standing_dumbbell_curl':
            elbow=calculate_angle(shoulder,p(13),p(15));return {'phase':elbow,'value':elbow}
        knee=calculate_angle(hip,p(25),p(27))
        if action=='bodyweight_squat':
            length=math.dist(hip,p(25))
            return {'phase':knee,'value':(p(25)[1]-hip[1])/length} if length>1e-6 else None
        if action=='standard_push_up':
            length=math.dist(shoulder,p(13));elbow=calculate_angle(shoulder,p(13),p(15))
            return {'phase':elbow,'value':(p(13)[1]-shoulder[1])/length} if length>1e-6 else None
        if action=='reverse_lunge':return {'phase':knee,'value':knee}
        if action=='bodyweight_glute_bridge':
            # 顶端按髋部最高阶段定位，不能挑最接近直线的一帧代替实际顶端。
            extension=calculate_angle(shoulder,hip,p(25));return {'phase':-hip[1]/h,'value':extension}
        elevation=math.degrees(math.atan2(hip[1]-shoulder[1],abs(hip[0]-shoulder[0])))
        return {'phase':elevation,'value':elevation}
    except (ValueError,ZeroDivisionError):return None


def evaluate_cycle(action,event,frames,*,measurement_side,view,variant_confirmed,rule=None):
    """只接受已完成事件；准备／正常过渡传None，不生成问题。

view与variant_confirmed是研究调用方核实的上下文，不是骨架自动证明机位／
变式。缺少上下文时拒判；没有默认质量阈值或默认正常结论。
"""
    if event is None:return None
    if action not in SPECS:raise ValueError('未定义的基础动作判项')
    code,views,feature,comparison,stage=SPECS[action]
    result={'code':code,'status':'unassessable','reason':None,'side':event.get('side'),'feature_schema_version':SCHEMA,
        'feature_name':feature,'phase':stage,'publishable':False,'evidence_scope':'development_candidate_only',
        'phase_unit':'image_height_ratio' if action=='bodyweight_glute_bridge' else 'image_angle_degrees',
        'cycle_start_ms':event.get('start_ms'),'cycle_end_ms':event.get('end_ms'),'method_version':rule.version if rule else None}
    def reject(reason,status='unassessable'):
        return {**result,'status':status,'reason':reason}
    start,end=event.get('start_ms'),event.get('end_ms')
    if any(type(v) is not int or v<0 for v in (start,end)) or start>=end:return reject('invalid_cycle_range')
    if view not in views:return reject('unsupported_view','unsupported')
    if variant_confirmed is not True:return reject('variant_not_confirmed','unsupported')
    if measurement_side not in {'left','right'}:return reject('measurement_side_unknown')
    if action in {'reverse_lunge','standing_dumbbell_curl'} and event.get('side')!=measurement_side:return reject('event_side_mismatch')
    if action not in {'reverse_lunge','standing_dumbbell_curl'} and event.get('side')!='none':return reject('event_side_mismatch')
    if rule is None:return reject('label_definition_or_threshold_missing')
    if rule.code!=code:raise ValueError('规则属于其他动作')
    selected=[f for f in frames if type(f.get('captured_ms')) is int and start<=f['captured_ms']<=end]
    if len(selected)<3:return reject('cycle_evidence_missing')
    times=[f['captured_ms'] for f in selected];seqs=[f.get('frame_seq') for f in selected]
    if any(type(v) is not int for v in seqs) or any(b<=a for a,b in zip(times,times[1:])) or any(b<=a for a,b in zip(seqs,seqs[1:])):return reject('non_monotonic_cycle_evidence')
    if times[0]-start>rule.maximum_gap_ms or end-times[-1]>rule.maximum_gap_ms or any(b-a>rule.maximum_gap_ms for a,b in zip(times,times[1:])):return reject('cycle_evidence_gap')
    observations=[_measure(action,f,measurement_side) for f in selected]
    if any(o is None for o in observations):return reject('required_parts_or_identity_missing')
    # 中值只抑制孤立尖峰；不补缺帧。阶段必须在周期内部且有持续源时间。
    smooth=[{'phase':statistics.median(o['phase'] for o in observations[i-1:i+2]),
        'value':statistics.median(o['value'] for o in observations[i-1:i+2])} for i in range(1,len(observations)-1)]
    maximize=stage=='top';index=(max if maximize else min)(range(len(smooth)),key=lambda i:smooth[i]['phase'])
    extreme=smooth[index]['phase'];left=right=index
    while left>0 and abs(smooth[left-1]['phase']-extreme)<=rule.stage_band:left-=1
    while right+1<len(smooth) and abs(smooth[right+1]['phase']-extreme)<=rule.stage_band:right+=1
    # 单调过渡或整段相同姿势没有独立的内部极值，不能冒充底部／顶端阶段。
    if left==0 or right==len(smooth)-1:return reject('extreme_not_bracketed_by_cycle')
    evidence_start,evidence_end=times[left+1],times[right+1]
    if right-left+1<2 or evidence_end-evidence_start<rule.minimum_stage_ms:return reject('extreme_duration_insufficient')
    value=statistics.median(o['value'] for o in smooth[left:right+1])
    if action=='standing_dumbbell_curl':value=max(o['value'] for o in smooth)-value
    measured={**result,'value':value,'threshold':rule.threshold,'parameter_source':rule.parameter_source,
        'start_ms':evidence_start,'end_ms':evidence_end,'measurement_side':measurement_side}
    if rule.threshold is None:return {**measured,'status':'unassessable','reason':'quality_threshold_unavailable'}
    issue=value>rule.threshold if comparison=='greater' else value<rule.threshold
    return {**measured,'status':'issue' if issue else 'not_observed','reason':None}
