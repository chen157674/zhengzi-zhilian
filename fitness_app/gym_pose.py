"""上肢近景回退：只在原图零人体时补空白画布检测，丢弃画面外推断点。"""
import numpy as np
import os
from .pose_input import ContentPoseService
from .pose import PoseService

VERSION='upper-body-padding-2-video'


class GymPoseService(ContentPoseService):
    def __init__(self,model_path=None,video=False,pose_factory=PoseService,scene_reference=False,realtime=False):
        super().__init__(model_path,video,pose_factory)
        self.fallback=None
        self.fallback_geometry=None;self.fallback_stamp=None
        self.observe_scene_reference=scene_reference
        from .pretrained_policy import enabled,body_enabled
        from .movement_guidance_policy import enabled as guidance_enabled
        self.pretrained=None
        if body_enabled() or guidance_enabled():
            from .pretrained_input import PretrainedFrameModel
            # 实时4B与姿态图争用GPU会产生数百毫秒尖峰；默认隔离到CPU。
            # 离线沿用auto后端，显式cuda选择继续有效。
            device='cpu' if realtime and os.environ.get('FITNESS_POSE_DEVICE','auto')=='auto' else None
            self.pretrained=PretrainedFrameModel(include_hands=enabled() or guidance_enabled(),device=device)

    def detect(self,rgb,timestamp_ms=0):
        result=super().detect(rgb,timestamp_ms)
        if not result['poses']:result=self.recover(rgb,timestamp_ms,result)
        if self.observe_scene_reference:
            from .scene_reference import estimate_vertical
            result['scene_reference']=(estimate_vertical(rgb,result['poses'][0]) if len(result['poses'])==1 else
                                       {'status':'unavailable','reason':'required_parts_or_identity_missing'})
        if self.pretrained is not None:
            result['pretrained']=self.pretrained.observe(rgb,result,timestamp_ms)
        return result

    def recover(self,rgb,timestamp_ms,result):
        """允许离线核查只补旧缓存的零人体帧，不重算已通过的原图检测。"""
        if result['poses']:return result
        h,w=rgb.shape[:2];left,top,right,bottom=result['input_transform']['crop_pixels']
        crop=rgb[top:bottom,left:right];ch,cw=crop.shape[:2];padding=ch//2
        geometry=(h,w,left,top,right,bottom,padding)
        if self.fallback is not None and (geometry!=self.fallback_geometry or
                self.video and self.fallback_stamp is not None and timestamp_ms-self.fallback_stamp>500):
            previous=self.fallback;self.fallback=None;previous.close()
        if self.fallback is None:self.fallback=self.factory(model_path=self.model_path,video=self.video)
        self.fallback_geometry=geometry;self.fallback_stamp=timestamp_ms
        padded=np.pad(crop,((0,padding),(0,0),(0,0)),mode='constant')
        raw=self.fallback.detect(np.ascontiguousarray(padded),timestamp_ms)
        poses=[]
        for pose in raw['poses']:
            restored=[]
            for p in pose:
                x=p['x']*cw;y=p['y']*(ch+padding)
                visible=0<=x<=cw and 0<=y<=ch
                restored.append({**p,'x':(left+x)/w,'y':(top+y)/h,'z':p['z']*cw/w,
                                 'visibility':p['visibility'] if visible else 0.,'presence':p['presence'] if visible else 0.})
            poses.append(restored)
        # 保留全部候选，后续共同多人门控处理，不能从多人中挑一个。
        world_poses=[[{**point,'visibility':poses[n][i]['visibility'],'presence':poses[n][i]['presence']}
                      for i,point in enumerate(pose)] for n,pose in enumerate(raw.get('world_poses',[])) if n<len(poses)]
        return {**result,'poses':poses,'world_poses':world_poses,'model':result['model']+'-'+VERSION,
                'input_transform':{**result['input_transform'],'upper_body_fallback':VERSION,
                                   'bottom_padding_pixels':padding,'padded_points_discarded':True}}

    def close(self):
        try:super().close()
        finally:
            if self.fallback is not None:
                fallback=self.fallback;self.fallback=None;fallback.close()
            if self.pretrained is not None:
                pretrained=self.pretrained;self.pretrained=None;pretrained.close()
