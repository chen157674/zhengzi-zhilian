"""编码留边输入处理；保留全部人体候选并还原原图坐标，按运行工厂接入。"""
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
import numpy as np
from .content_bounds import detect_content_bounds
from .pose import PoseService, DEFAULT_MODEL

INPUT_POLICY_VERSION = 'content-roi-2'
CROP_JITTER_PIXELS = 2


class ContentPoseService:
    def __init__(self, model_path=None, video=False, pose_factory=PoseService):
        self.factory,self.model_path,self.video = pose_factory,model_path,video
        self.pose = pose_factory(model_path=model_path, video=video)
        self.geometry=None;self.last_timestamp=-1

    def detect(self, rgb, timestamp_ms=0):
        if self.pose is None:raise RuntimeError('姿态服务已关闭')
        if self.video and timestamp_ms<=self.last_timestamp:raise ValueError('源时间戳必须递增')
        bounds = detect_content_bounds(rgb)
        height, width = rgb.shape[:2]
        left, top, right, bottom = (round(bounds[0]*width), round(bounds[1]*height),
                                    round(bounds[2]*width), round(bounds[3]*height))
        detected_crop=[left,top,right,bottom]
        geometry=(width,height,left,top,right,bottom)
        # 相对本轮跟踪的固定裁切锚点判断，不能逐帧挪动容差造成累计漂移。
        stabilized=bool(self.video and self.geometry is not None and
                        self.geometry[:2]==geometry[:2] and
                        max(abs(a-b) for a,b in zip(geometry[2:],self.geometry[2:]))<=CROP_JITTER_PIXELS)
        if stabilized:
            geometry=self.geometry
            left,top,right,bottom=geometry[2:]
        reset=self.video and self.geometry is not None and geometry!=self.geometry
        if reset:
            previous=self.pose;self.pose=None;previous.close()
            self.pose=self.factory(model_path=self.model_path,video=self.video)
        self.geometry=geometry;self.last_timestamp=timestamp_ms
        cropped = rgb if (left, top, right, bottom) == (0, 0, width, height) else np.ascontiguousarray(rgb[top:bottom,left:right])
        raw = self.pose.detect(cropped, timestamp_ms)
        # 保留全部人体候选，不选最高分、不去重；外推点也不夹回边缘。
        poses = [[dict(p) for p in pose] for pose in raw['poses']] if cropped is rgb else [[{**p, 'x': (left+p['x']*(right-left))/width,
                   'y': (top+p['y']*(bottom-top))/height,
                   'z': p['z']*(right-left)/width} for p in pose] for pose in raw['poses']]
        return {**raw, 'poses': poses, 'model': raw['model']+'-'+INPUT_POLICY_VERSION,
                'content_bounds': bounds,
                'input_transform': {'version': INPUT_POLICY_VERSION, 'crop_pixels': [left,top,right,bottom],
                                    'detected_crop_pixels': detected_crop, 'crop_stabilized': stabilized and detected_crop!=[left,top,right,bottom],
                                    'original_size': [width,height], 'tracker_reset': reset}}

    def close(self):
        if self.pose is not None:
            previous=self.pose;self.pose=None;previous.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def input_policy_snapshot(factory=PoseService):
    """创建会话时记录服务端配置；不向客户端开放选择或覆盖。"""
    from .gym_pose import GymPoseService,VERSION
    if factory is GymPoseService:
        from .pretrained_policy import enabled,body_enabled
        from .movement_guidance_policy import enabled as guidance_enabled
        return {**input_policy_snapshot(ContentPoseService),'version':INPUT_POLICY_VERSION+'-'+VERSION,
                'upper_body_fallback_sha256':hashlib.sha256(Path(__file__).with_name('gym_pose.py').read_bytes()).hexdigest(),
                'fallback':'zero_people_only; bottom padding 0.5H; original content points only; all candidates retained',
                'pretrained_observations_enabled':enabled(),
                'body_lean_observations_enabled':body_enabled(),
                'movement_guidance_observations_enabled':guidance_enabled(),
                'pretrained_input_sha256':hashlib.sha256(Path(__file__).with_name('pretrained_input.py').read_bytes()).hexdigest() if body_enabled() or guidance_enabled() else None}
    if factory not in (ContentPoseService,PoseService):
        return {'version':'test-override','factory':getattr(factory,'__module__','unknown')+'.'+getattr(factory,'__qualname__',type(factory).__name__)}
    from .content_bounds import bounds_signature
    return {'version':INPUT_POLICY_VERSION if factory is ContentPoseService else 'full-frame-1',
            'implementation_sha256':hashlib.sha256(Path(__file__ if factory is ContentPoseService else DEFAULT_MODEL.parents[2]/'fitness_app/pose.py').read_bytes()).hexdigest(),
            'pose_service_sha256':hashlib.sha256((DEFAULT_MODEL.parents[2]/'fitness_app/pose.py').read_bytes()).hexdigest(),
            'model_sha256':json.loads(DEFAULT_MODEL.with_name('manifest.json').read_text(encoding='utf-8'))['sha256'],
            'mediapipe':version('mediapipe'),'running_mode':'VIDEO','num_poses':2,
            'content_bounds':bounds_signature() if factory is ContentPoseService else None}
