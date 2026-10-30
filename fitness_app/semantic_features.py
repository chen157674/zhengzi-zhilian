"""动作融合层的固定数值契约；缺失观测保留掩码，不把缺点当作正常关节。"""
import numpy as np
from .video_vlm import LABELS

VERSION='semantic-pose-fusion-features-1'
MOTION_KEYS=('trunk_tilt_from_vertical_deg','elbow_median_deg','elbow_range_deg','upper_arm_to_trunk_median_deg','upper_arm_range_deg',
             'wrist_above_shoulder_min_torso','wrist_above_shoulder_max_torso','hand_separation_range_torso','hip_vertical_range_torso',
             'estimated_body_wrist_lateral_range_torso','estimated_body_wrist_depth_range_torso','upper_arm_from_image_down_deg',
             'wrist_height_min_estimated_torso','wrist_height_max_estimated_torso')
SEMANTIC_DIM=2560
WORLD_DIM=112


def feature_blocks(embedding,prediction,world=None,world_visibility=0.):
    embedding=np.asarray(embedding,np.float32)
    if embedding.shape!=(SEMANTIC_DIM,) or not np.isfinite(embedding).all():raise ValueError('视频语义维度或数值无效')
    norm=float(np.linalg.norm(embedding))
    if norm<1e-8:raise ValueError('视频语义不能为零')
    semantic=embedding/norm
    scores=prediction['scores'];visual=np.asarray([scores[a or 'unknown'] for a in LABELS],np.float32)
    if not np.isfinite(visual).all() or np.any(visual<0) or np.any(visual>1):raise ValueError('视觉候选分数无效')
    visual=np.log(np.maximum(visual,1e-6))
    geometry=geometry_block(prediction.get('pose_observations',{}),world,world_visibility)
    return {'semantic':semantic,'visual':visual,'geometry':geometry,'full':np.concatenate((semantic,visual,geometry)).astype(np.float32)}


def geometry_block(observations,world=None,world_visibility=0.):
    motion=np.asarray([observations.get(k,np.nan) for k in MOTION_KEYS],np.float32)
    mask=np.isfinite(motion).astype(np.float32)
    motion=np.where(mask,motion,0)
    for i,key in enumerate(MOTION_KEYS):motion[i]=np.clip(motion[i],-180,180) if key.endswith('_deg') else np.clip(motion[i],-8,8)
    world_available=world is not None
    if world_available:
        world=np.asarray(world,np.float32)
        if world.shape!=(WORLD_DIM,) or not np.isfinite(world).all():raise ValueError('世界坐标编码特征不符')
    else:world=np.zeros(WORLD_DIM,np.float32)
    return np.concatenate((motion,mask,world,[float(world_available),float(world_visibility)])).astype(np.float32)
