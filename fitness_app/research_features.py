"""研究片段的固定投影角度特征；不接收标签、人物或文件名作为数值输入。"""
import hashlib
import math
import numpy as np

from .database import json_text
from .frame_pipeline import usable_point
from .upstream import calculate_angle

FEATURE_SCHEMA='rgb-clip-projected-angles-1'
STATISTICS=('mean','std','p10','p50','p90')
COLUMNS={
    'bodyweight_squat':('knee','hip','trunk_vertical'),
    'standard_push_up':('elbow','hip','trunk_vertical'),
    'conventional_deadlift':('hip','knee','trunk_vertical'),
    'standing_dumbbell_curl':('left_elbow','right_elbow','left_arm_trunk','right_arm_trunk','trunk_vertical'),
}
CONFIG={'schema_version':FEATURE_SCHEMA,'statistics':list(STATISTICS),'columns':{key:list(value) for key,value in COLUMNS.items()},
    'minimum_observations':3,'minimum_coverage':.7,'visibility':.5,'quantile_method':'linear','std_ddof':0,
    'coordinates':'image_xy_scaled_by_frame_width_height','side_policy':'clip_max_complete_valid_frames_tie_left',
    'missing_policy':'reject_not_impute','source_sampling':'all_decoded_frames'}
CONFIG_SHA256=hashlib.sha256(json_text(CONFIG).encode()).hexdigest()


def _point(points,index,width,height):
    point=points[index]
    if not usable_point(point):raise ValueError('必要关节不可用')
    return np.array((point['x']*width,point['y']*height),dtype=float)


def _vector_angle(left,right):return calculate_angle(left,(0.,0.),right)


def _side_angles(action,points,width,height,side):
    offset=0 if side=='left' else 1
    def point(index):return _point(points,index+offset,width,height)
    shoulder,hip=point(11),point(23)
    trunk=_vector_angle(shoulder-hip,(0.,-1.))
    if action=='standing_dumbbell_curl':return (calculate_angle(shoulder,point(13),point(15)),calculate_angle(point(13),shoulder,hip))
    knee=point(25);hip_angle=calculate_angle(shoulder,hip,knee)
    if action=='standard_push_up':return (calculate_angle(shoulder,point(13),point(15)),hip_angle,trunk)
    knee_angle=calculate_angle(hip,knee,point(27))
    return (hip_angle,knee_angle,trunk) if action=='conventional_deadlift' else (knee_angle,hip_angle,trunk)


def frame_angles(action,frame):
    """返回每侧完整列或 None；身份／多人数拒判由姿态预处理标识。"""
    if action not in COLUMNS:raise ValueError('无已确认研究特征定义')
    empty={'left':None,'right':None} if action!='standing_dumbbell_curl' else {'both':None}
    poses=frame.get('poses',[]);width=frame.get('width',0);height=frame.get('height',0)
    if len(poses)!=1 or not frame.get('identity_accepted',True) or not all(isinstance(value,(int,float)) and math.isfinite(value) and value>0 for value in (width,height)):return empty
    points=poses[0]
    if len(points)!=33:return empty
    if action=='standing_dumbbell_curl':
        try:
            left=_side_angles(action,points,width,height,'left');right=_side_angles(action,points,width,height,'right')
            shoulders=(_point(points,11,width,height)+_point(points,12,width,height))/2
            hips=(_point(points,23,width,height)+_point(points,24,width,height))/2
            return {'both':(left[0],right[0],left[1],right[1],_vector_angle(shoulders-hips,(0.,-1.)))}
        except (ValueError,TypeError,IndexError):return empty
    result={}
    for side in ('left','right'):
        try:result[side]=_side_angles(action,points,width,height,side)
        except (ValueError,TypeError,IndexError):result[side]=None
    return result


def aggregate_angles(action,rows):
    total=len(rows);sides=('both',) if action=='standing_dumbbell_curl' else ('left','right')
    observed={side:[row[side] for row in rows if row.get(side) is not None] for side in sides}
    side=max(sides,key=lambda candidate:len(observed[candidate]))
    usable=len(observed[side]);coverage=usable/total if total else 0.
    result={'schema_version':FEATURE_SCHEMA,'config_sha256':CONFIG_SHA256,'action_id':action,'side':side,'decoded_frames':total,
        'valid_frames':usable,'coverage':coverage,'columns':[f'{column}.{stat}' for column in COLUMNS[action] for stat in STATISTICS],
        'features':None,'rejection_reason':None}
    if usable<CONFIG['minimum_observations']:result['rejection_reason']='INSUFFICIENT_VALID_OBSERVATIONS'
    elif coverage<CONFIG['minimum_coverage']:result['rejection_reason']='INSUFFICIENT_VALID_COVERAGE'
    else:
        values=np.asarray(observed[side],dtype=float)
        if values.shape!=(usable,len(COLUMNS[action])) or not np.isfinite(values).all():raise ValueError('特征列维数或有限性不符')
        aggregate=np.stack((values.mean(axis=0),values.std(axis=0,ddof=0),*np.percentile(values,[10,50,90],axis=0,method='linear')),axis=1)
        result['features']=aggregate.reshape(-1).tolist()
    return result
