"""供界面连线的短期骨架契约；不推测隐藏关节，不另存逐帧身体数据。"""
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
}


from .gym_catalogue import RULES as GYM_RULES
REGIONS.update({code: (11,12,23,24) if code.endswith('trunk_sway') else
                (11,12,13,14) if code.endswith('upper_arm_swing') else (13,14,15,16) for code in GYM_RULES})
from .movement_guidance_policy import RULES as GUIDANCE_RULES
REGIONS.update({code:(11,12,13,14,23,24) if code.endswith('elbow_drift') else (13,14,15,16) for code in GUIDANCE_RULES})


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
