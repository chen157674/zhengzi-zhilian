"""17：与原标签含义区分的杠铃动作开发观测；全部禁止正式发布。

Fitness-AQA补充材料表1定义肘腕对齐及躯干倾斜类别，但未提供本机模型
可直接套用的像素阈值。肩推和划船只输出观测；硬拉显式D-POSE阈值仅验逻辑。
"""
from dataclasses import dataclass
import math
import statistics

from .frame_pipeline import usable_point
from .upstream import calculate_angle

SCHEMA='barbell-quality-image-geometry-1'
LABEL_SOURCE='https://www.ecva.net/papers/eccv_2022/papers_ECCV/papers/136980104-supp.zip'


def _finite(v):return type(v) in (int,float) and math.isfinite(v)


def _xy(frame,indices):
    q=frame.get('framing',{});p=q.get('landmarks',[]);w,h=frame.get('width'),frame.get('height')
    if q.get('person_count')!=1 or frame.get('identity_accepted') is False or len(p)!=33 or not all(_finite(v) and v>0 for v in (w,h)):return None
    if any(not usable_point(p[i]) or p[i].get('valid') is False for i in indices):return None
    return {i:(p[i]['x']*w,p[i]['y']*h) for i in indices}


def _up(value):
    if not isinstance(value,(list,tuple)) or len(value)!=2 or not all(_finite(v) for v in value):return None
    length=math.hypot(*value)
    return tuple(v/length for v in value) if length>1e-6 else None


def _result(code,reason,**values):
    return {'code':code,'status':'unassessable','reason':reason,'publishable':False,'feature_schema_version':SCHEMA,**values}


def observe_press_alignment(frame,*,view,standing_barbell_confirmed,rack_phase_confirmed,orientation_up):
    code='overhead_press.elbow'
    if view!='front' or standing_barbell_confirmed is not True:return _result(code,'variant_or_frontal_view_unconfirmed')
    if rack_phase_confirmed is not True:return _result(code,'rack_phase_unconfirmed')
    up=_up(orientation_up);p=_xy(frame,[11,12,13,14,15,16])
    if up is None or p is None:return _result(code,'orientation_or_arm_parts_missing')
    right=(-up[1],up[0]);project=lambda point,axis:sum(a*b for a,b in zip(point,axis))
    center=(project(p[11],right)+project(p[12],right))/2;arms={}
    for side,s in (('left',0),('right',1)):
        shoulder,elbow,wrist=(p[i+s] for i in (11,13,15));length=math.dist(elbow,wrist);outward=project(shoulder,right)-center
        if length<1e-6 or abs(outward)<1e-6:return _result(code,'arm_scale_or_front_axis_degenerate')
        arms[side]={'elbow_outside_wrist_forearm_ratio':(project(elbow,right)-project(wrist,right))*(1 if outward>0 else -1)/length,
            'elbow_below_wrist_forearm_ratio':(project(wrist,up)-project(elbow,up))/length}
    return _result(code,'label_quantification_and_phase_scope_unvalidated',at_ms=frame.get('captured_ms'),arms=arms,
        method_version='press-alignment-observation-1',label_source=LABEL_SOURCE,
        scope='下方准备示例的正面二维肘腕关系；不等于完整视频Elbow分类器或伸肘不足')


def observe_row_torso(frame,*,view,barbell_row_confirmed,row_phase_confirmed,measurement_side,orientation_up):
    code='barbell_row.trunk'
    if view not in {'side','oblique'} or barbell_row_confirmed is not True:return _result(code,'variant_or_view_unconfirmed')
    if row_phase_confirmed is not True:return _result(code,'row_phase_unconfirmed')
    if measurement_side not in {'left','right'}:return _result(code,'measurement_side_unknown')
    s=0 if measurement_side=='left' else 1;up=_up(orientation_up);p=_xy(frame,[11+s,23+s])
    if up is None or p is None:return _result(code,'orientation_or_trunk_parts_missing')
    vector=tuple(a-b for a,b in zip(p[11+s],p[23+s]));length=math.hypot(*vector)
    if length<1e-6:return _result(code,'torso_scale_degenerate')
    tilt=math.degrees(math.acos(max(-1.,min(1.,sum(a*b for a,b in zip(vector,up))/length))))
    return _result(code,'label_angle_reference_and_threshold_unvalidated',at_ms=frame.get('captured_ms'),torso_tilt_from_up_degrees=tilt,
        measurement_side=measurement_side,method_version='row-torso-observation-1',label_source=LABEL_SOURCE,
        scope='二维肩髋方向相对已核图像上方的角度；不等同脊柱曲度、躯干摆动标签或原数据集角度定义')


def evaluate_row_trunk(event, frames, threshold=15.):
    """比较持续窗口中值；双侧投影结论冲突时拒判，不用单帧极值定错。"""
    selected=[f for f in frames if event['start_ms']<=f['captured_ms']<=event['end_ms']]
    if len(selected)<5:return {'status':'unassessable','reason':'cycle_evidence_missing'}
    times=[f['captured_ms'] for f in selected]
    if times[0]-event['start_ms']>500 or event['end_ms']-times[-1]>500 or any(b<=a or b-a>500 for a,b in zip(times,times[1:])):
        return {'status':'unassessable','reason':'cycle_evidence_gap'}
    ranges={};raw_ranges={};windows={};confidence={}
    for side,s in (('left',0),('right',1)):
        values=[];scores=[]
        for f in selected:
            p=_xy(f,[11+s,23+s])
            if p is None or math.dist(p[11+s],p[23+s])<1e-6:break
            values.append(calculate_angle(p[11+s],p[23+s],(p[23+s][0],p[23+s][1]-1)))
            scores.append(min(min(f['framing']['landmarks'][i+s][k] for k in ('visibility','presence')) for i in (11,23)))
        if len(values)!=len(selected):continue
        stable=[]
        for i,start in enumerate(times):
            j=i
            while j+1<len(times) and (times[j]-start<100 or j-i<2):j+=1
            if times[j]-start<100 or j-i<2:continue
            stable.append({'start_ms':start,'end_ms':times[j],'degrees':statistics.median(values[i:j+1])})
        if len(stable)<2:continue
        low=min(stable,key=lambda x:x['degrees']);high=max(stable,key=lambda x:x['degrees'])
        ranges[side]=high['degrees']-low['degrees'];raw_ranges[side]=max(values)-min(values)
        windows[side]={'low':low,'high':high};confidence[side]=min(scores)
    if not ranges:return {'status':'unassessable','reason':'required_parts_or_identity_missing'}
    side=max(ranges,key=lambda x:confidence[x]);decisions={x:v>threshold for x,v in ranges.items()}
    status='issue' if decisions[side] else 'not_observed';reason=None
    if len(set(decisions.values()))>1:status='unassessable';reason='bilateral_projection_disagreement'
    return {'status':status,'reason':reason,'threshold':threshold,'trunk_change_degrees':ranges[side],
            'measurement_side':side,'range_by_side':ranges,'raw_range_by_side':raw_ranges,
            'extreme_windows':windows,'minimum_window_ms':100,
            'measurement_basis':'同侧肩髋图像方向；至少3帧且100ms窗口中值，双侧可见时结论须一致'}


@dataclass(frozen=True)
class DeadliftExtensionRule:
    minimum_hip_degrees:float|None
    minimum_knee_degrees:float|None
    stage_band_degrees:float
    minimum_stage_ms:int
    maximum_gap_ms:int
    version:str
    parameter_source:str

    def __post_init__(self):
        thresholds=(self.minimum_hip_degrees,self.minimum_knee_degrees)
        if any(v is None for v in thresholds) and not all(v is None for v in thresholds):raise ValueError('髋膝质量阈值必须同时给定或保持缺失')
        if any(v is not None and (not _finite(v) or not 0<v<180) for v in thresholds):raise ValueError('伸展阈值无效')
        if not _finite(self.stage_band_degrees) or self.stage_band_degrees<=0 or any(type(v) is not int or v<=0 for v in (self.minimum_stage_ms,self.maximum_gap_ms)):raise ValueError('顶端阶段参数无效')
        if not all(isinstance(v,str) and v.strip() for v in (self.version,self.parameter_source)):raise ValueError('伸展候选必须记录依据与版本')


def evaluate_deadlift_extension(event,frames,*,view,conventional_barbell_confirmed,measurement_side,rule):
    if event is None:return None
    code='deadlift.extension';base={'side':event.get('side'),'cycle_start_ms':event.get('start_ms'),'cycle_end_ms':event.get('end_ms'),
        'method_version':rule.version,'parameter_source':rule.parameter_source}
    def reject(reason):return _result(code,reason,**base)
    if view!='side' or conventional_barbell_confirmed is not True:return reject('conventional_variant_or_side_view_unconfirmed')
    if measurement_side not in {'left','right'} or event.get('side')!='none':return reject('measurement_side_unconfirmed')
    a,b=event.get('start_ms'),event.get('end_ms')
    if any(type(v) is not int or v<0 for v in (a,b)) or a>=b:return reject('invalid_cycle_range')
    selected=[f for f in frames if type(f.get('captured_ms')) is int and a<=f['captured_ms']<=b]
    if len(selected)<5:return reject('cycle_evidence_missing')
    times=[f['captured_ms'] for f in selected];seqs=[f.get('frame_seq') for f in selected]
    if any(type(v) is not int for v in seqs) or any(y<=x for x,y in zip(times,times[1:])) or any(y<=x for x,y in zip(seqs,seqs[1:])):return reject('non_monotonic_cycle_evidence')
    if times[0]-a>rule.maximum_gap_ms or b-times[-1]>rule.maximum_gap_ms or any(y-x>rule.maximum_gap_ms for x,y in zip(times,times[1:])):return reject('cycle_evidence_gap')
    s=0 if measurement_side=='left' else 1;values=[]
    for f in selected:
        p=_xy(f,[11+s,23+s,25+s,27+s])
        if p is None:return reject('top_or_cycle_parts_unobservable')
        try:hip=calculate_angle(p[11+s],p[23+s],p[25+s]);knee=calculate_angle(p[23+s],p[25+s],p[27+s])
        except ValueError:return reject('joint_projection_degenerate')
        values.append({'hip':hip,'knee':knee,'joint_extension':min(hip,knee)})
    smooth=[{k:statistics.median(v[k] for v in values[i-1:i+2]) for k in ('hip','knee','joint_extension')} for i in range(1,len(values)-1)]
    peak=max(range(len(smooth)),key=lambda i:smooth[i]['joint_extension']);score=smooth[peak]['joint_extension'];left=right=peak
    while left>0 and score-smooth[left-1]['joint_extension']<=rule.stage_band_degrees:left-=1
    while right+1<len(smooth) and score-smooth[right+1]['joint_extension']<=rule.stage_band_degrees:right+=1
    if score-values[0]['joint_extension']<=rule.stage_band_degrees or score-values[-1]['joint_extension']<=rule.stage_band_degrees:return reject('top_not_bracketed_by_complete_cycle')
    start,end=times[left+1],times[right+1]
    if right-left+1<2 or end-start<rule.minimum_stage_ms:return reject('top_duration_insufficient')
    # 在同一顶端阶段报告髋膝，不拼接两个不同时间的各自最大值。
    hip=statistics.median(v['hip'] for v in smooth[left:right+1]);knee=statistics.median(v['knee'] for v in smooth[left:right+1])
    result=_result(code,'quality_threshold_unavailable',**base,start_ms=start,end_ms=end,hip_image_degrees=hip,knee_image_degrees=knee,measurement_side=measurement_side)
    if rule.minimum_hip_degrees is not None:
        run_start=None;longest=0
        for i in range(left+1,right+2):
            v=values[i]
            if v['hip']>=rule.minimum_hip_degrees and v['knee']>=rule.minimum_knee_degrees:
                if run_start is None:run_start=times[i]
                longest=max(longest,times[i]-run_start)
            else:run_start=None
        result.update(status='not_observed' if longest>=rule.minimum_stage_ms else 'issue',reason=None,
                      minimum_hip_degrees=rule.minimum_hip_degrees,minimum_knee_degrees=rule.minimum_knee_degrees,
                      longest_joint_extension_ms=longest,minimum_stage_ms=rule.minimum_stage_ms)
    return result
