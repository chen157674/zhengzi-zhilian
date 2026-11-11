"""供界面连线的短期骨架契约；不推测隐藏关节，不另存逐帧身体数据。"""
import math
from .frame_pipeline import usable_point

CONNECTIONS=((11,12),(11,13),(13,15),(12,14),(14,16),(11,23),(12,24),
             (23,24),(23,25),(25,27),(24,26),(26,28),(27,29),(29,31),(28,30),(30,32))
JOINT_NAMES={11:'left_shoulder',12:'right_shoulder',13:'left_elbow',14:'right_elbow',
             15:'left_wrist',16:'right_wrist',23:'left_hip',24:'right_hip',25:'left_knee',
             26:'right_knee',27:'left_ankle',28:'right_ankle',29:'left_heel',30:'right_heel',
             31:'left_foot_index',32:'right_foot_index'}
REGIONS={
    'squat.range':(23,24,25,26), 'push_up.range':(11,12,13,14),
    'push_up.hip_high':(11,12,23,24), 'push_up.hip_low':(11,12,23,24),
    'reverse_lunge.range':(23,24,25,26), 'glute_bridge.range':(23,24),
    'sit_up.range':(11,12,23,24), 'curl.range':(13,14),
    'curl.trunk_sway':(11,12,23,24), 'curl.upper_arm_swing':(11,12,13,14),
    'plank.hip_high':(11,12,23,24), 'plank.hip_low':(11,12,23,24),
    'jumping_jack.arm_range':(11,12,13,14,15,16),
    'jumping_jack.leg_range':(23,24,25,26,27,28),
    'high_knees.height':(23,24,25,26), 'overhead_press.elbow':(13,14,15,16),
    'barbell_row.trunk':(11,12,23,24), 'deadlift.extension':(23,24,25,26),
    'machine_chest_press.pretrained_upper_body_balance':(11,12,23,24),
    'lat_pulldown.pretrained_upper_body_balance':(11,12,23,24),
    'machine_chest_press.pretrained_finger_extension':(15,16),
    'barbell_bench_press.pretrained_posture':(11,12,13,14,15,16,23,24),
    'conventional_deadlift.pretrained_posture':(11,12,15,16,23,24,25,26),
    'bodyweight_squat.pretrained_posture':(11,12,23,24,25,26,27,28),
}


from .gym_catalogue import RULES as GYM_RULES
REGIONS.update({code: (11,12,23,24) if code.endswith('trunk_sway') else
                (11,12,13,14) if code.endswith('upper_arm_swing') else (13,14,15,16) for code in GYM_RULES})
from .movement_guidance_policy import RULES as GUIDANCE_RULES
REGIONS.update({code:(11,12,13,14,23,24) if code.endswith('elbow_drift') else (13,14,15,16) for code in GUIDANCE_RULES})

def reliable_lower_limb_points(points,body,width,height,threshold=.5):
    """同帧第二模型复核腿点；不可靠的整条腿省略，不补造脚跟或脚尖。"""
    output=list(points[:25])+[None]*8
    raw=body.get('keypoints',[])
    if len(points)!=33 or len(raw)!=17:return output
    for hip,knee,ankle,source in ((23,25,27,(11,13,15)),(24,26,28,(12,14,16))):
        candidates=[raw[i] for i in source]
        if points[hip] is None or any(not p.get('coordinate_valid') or p.get('score',0)<threshold or
            any(not isinstance(p.get(k),(int,float)) or not math.isfinite(p[k]) for k in ('x','y','score')) or
            not 0<=p['x']<=1 or not 0<=p['y']<=1 for p in candidates):continue
        if math.hypot((candidates[0]['x']-points[hip][0])*width,
                      (candidates[0]['y']-points[hip][1])*height)>.08*height:continue
        for target,p in zip((knee,ankle),candidates[1:]):output[target]=[round(p['x'],6),round(p['y'],6)]
    return output


def display_landmarks(frame,points):
    data=frame.get('pretrained')
    if data is None:return points,None
    body=data.get('body') or {}
    stamp=frame.get('source_captured_ms',frame.get('captured_ms'))
    width,height=frame.get('width'),frame.get('height')
    current=(data.get('status')=='observed' and data.get('timestamp_ms')==stamp and len(body.get('keypoints',[]))==17 and
        all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in (width,height)))
    # 已开启二次复核时，缺失／过期结果不能使座椅支架上的旧腿点重新出现。
    if not current:return list(points[:25])+[{'valid':False} for _ in range(8)],'lower-reference-unavailable-v1'
    original=[[p['x'],p['y']] if p.get('valid') and usable_point(p) else None for p in points]
    refined=reliable_lower_limb_points(original,body,width,height)
    lower = data.get('lower_display') or {}
    supplement = None
    if (lower.get('status') == 'observed' and lower.get('timestamp_ms') == stamp and
            lower.get('target_basis') == 'unique_same_frame_anatomical_shoulders_and_hips'):
        from .locked_lower_pose import matching_target
        selected, _ = matching_target([lower.get('body') or {}], data.get('source_landmarks', []), width, height)
        if selected is not None:
            supplement = reliable_lower_limb_points(original,selected,width,height,threshold=.6)
    supplemental_indices = set()
    if supplement is not None:
        for knee, ankle in ((25,27),(26,28)):
            if (refined[knee] is None or refined[ankle] is None) and supplement[knee] is not None and supplement[ankle] is not None:
                refined[knee], refined[ankle] = supplement[knee], supplement[ankle]
                supplemental_indices.update((knee,ankle))
    output=list(points);sources={25:13,26:14,27:15,28:16}
    for i in range(25,33):
        output[i]={'valid':False}
        if refined[i] is not None:
            source_body = lower['body'] if i in supplemental_indices else body
            score=min(1.,max(0.,source_body['keypoints'][sources[i]]['score']))
            output[i]={'x':refined[i][0],'y':refined[i][1],'z':0.,'visibility':score,'presence':score,
                       'valid':True,'depth_available':False}
    sha=body.get('model_sha256') or data.get('body_model_sha256') or 'unversioned'
    return output,'rtmpose2d-lower-v1-'+sha[:12]+('-locked-yolo-lower-v1' if supplemental_indices else '')


def skeleton_snapshot(frame=None,coaching=None,*,fresh=True,time_base='captured_ms',unavailable_reason=None):
    """每次从同一帧生成，主要提示只能来自已提交的 coaching。"""
    frame=frame or {};quality=frame.get('framing',{})
    result={'schema_version':1,'available':False,'reason':unavailable_reason or 'no_frame',
            'frame_seq':frame.get('frame_seq'),'captured_ms':frame.get('captured_ms'),
            'time_base':time_base,'width':frame.get('width'),'height':frame.get('height'),
            'model_version':frame.get('model_version'),
            'coordinate_space':'image_normalized','origin':'top_left','mirrored':False,
            'side_basis':'person_anatomical','depth_is_metric':False,
            'persistence':'memory_only_latest_frame','joints':[],'connections':[],'highlight':None}
    if unavailable_reason:return result
    if not fresh:result['reason']='stale_frame';return result
    if quality.get('code')=='TARGET_AMBIGUOUS' or quality.get('person_count',0)>1:
        result['reason']='target_ambiguous';return result
    points=quality.get('landmarks',[])
    if len(points)!=33:result['reason']='no_landmarks';return result
    points,display_model=display_landmarks(frame,points)
    if display_model:result['model_version']=(frame.get('model_version') or 'pose')+'+'+display_model
    joints=[{'index':i,'name':name,**{k:points[i][k] for k in ('x','y','z','visibility','presence')}}
            for i,name in JOINT_NAMES.items() if points[i].get('valid') and usable_point(points[i])]
    valid={j['index'] for j in joints}
    connections=[list(edge) for edge in CONNECTIONS if set(edge)<=valid]
    result.update(available=bool(joints),reason=None if joints else 'no_visible_joints',joints=joints,connections=connections)
    cue=(coaching or {}).get('primary_cue') if (coaching or {}).get('state')=='needs_adjustment' else None
    if cue and cue.get('code') in REGIONS:
        side=cue.get('side','none');indices=set(REGIONS[cue['code']]) & valid
        evidence=cue.get('evidence') or {}
        affected=[side] if side in ('left','right') else []
        if cue['code']=='overhead_press.elbow' and evidence.get('affected_sides'):
            affected=[s for s in evidence.get('affected_sides',[]) if s in ('left','right')]
        if affected:
            indices={i for i in indices if any(JOINT_NAMES[i].startswith(s+'_') for s in affected)}
        if cue['code']=='deadlift.extension' and evidence.get('limited_joints'):
            joints={'髋':(23,24),'膝':(25,26)}
            limited={i for name in evidence['limited_joints'] for i in joints.get(name,())}
            if limited:indices &= limited
        if indices:
            result['highlight']={'cue_id':cue.get('cue_id'),'code':cue['code'],'side':side,
                'affected_sides':affected,
                'joint_indices':sorted(indices),'connections':[edge for edge in connections if set(edge)<=indices],
                'evidence_start_ms':cue.get('start_ms'),'evidence_end_ms':cue.get('end_ms'),
                'evidence_time_base':'source_ms' if time_base=='source_ms' else 'session_elapsed_ms',
                'evidence_level':cue.get('evidence_level','reference'),
                'meaning':'cue_body_region_not_current_frame_error_proof'}
    return result
