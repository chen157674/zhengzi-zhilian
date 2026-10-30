"""在原姿态输入旁增加预训练观察，原识别/计数poses与world_poses保持不变。"""
import math
import numpy as np
from .rtmpose_input import RTMPose,PretrainedHands,quality_landmarks,associate_hands


def body_features(body,width,height):
    indices=(5,6,11,12);points=body['keypoints']
    if any(not points[i]['coordinate_valid'] or points[i]['score']<.5 for i in indices):
        return {'status':'unassessable','reason':'pretrained_parts_not_visible'}
    p={i:np.array([points[i]['x']*width,points[i]['y']*height]) for i in indices}
    shoulder=(p[5]+p[6])/2;hip=(p[11]+p[12])/2;trunk=shoulder-hip
    hip_axis=p[12]-p[11];shoulder_axis=p[6]-p[5]
    height_pixels=float(np.linalg.norm(trunk));hip_width=float(np.linalg.norm(hip_axis))
    if height_pixels<.08*height or hip_width<.04*width:return {'status':'unassessable','reason':'pretrained_geometry_unreliable'}
    ratio=float(np.linalg.norm(shoulder_axis)/height_pixels)
    if ratio<.55:return {'status':'unassessable','reason':'unsupported_view'}
    up=np.array([hip_axis[1],-hip_axis[0]])/hip_width
    if up@trunk<0:up=-up
    sideways=np.array([-up[1],up[0]])
    trunk_angle=math.degrees(math.atan2(float(sideways@trunk),float(up@trunk)))
    skew=math.degrees(math.atan2(float(shoulder_axis[1]),float(shoulder_axis[0]))-
                      math.atan2(float(hip_axis[1]),float(hip_axis[0])))
    skew=((skew+90)%180)-90
    elbows={}
    for name,ids in (('left',(5,7,9)),('right',(6,8,10))):
        if any(not points[i]['coordinate_valid'] or points[i]['score']<.5 for i in ids):continue
        a,b,c=[np.array([points[i]['x']*width,points[i]['y']*height]) for i in ids]
        u=a-b;v=c-b;den=float(np.linalg.norm(u)*np.linalg.norm(v))
        if den>1e-6:elbows[name]=float(np.degrees(np.arccos(np.clip(u@v/den,-1,1))))
    return {'status':'observed','value':max(abs(trunk_angle),abs(skew)),
        'trunk_relative_to_pelvis_degrees':trunk_angle,'shoulder_pelvis_skew_degrees':skew,
        'view_ratio':ratio,'elbow_angles':elbows,'basis':'rtmpose_2d_relative_pelvis_not_measured_gravity'}


class PretrainedFrameModel:
    def __init__(self,include_hands=True):
        from .body_lean import SceneAxisTracker
        self.body=RTMPose('body');self.hands=PretrainedHands() if include_hands else None;self.axis=SceneAxisTracker()

    def observe(self,rgb,result,stamp):
        height,width=rgb.shape[:2]
        if len(result.get('poses',[]))!=1:
            if hasattr(self,'axis'):self.axis.reset()
            return {'status':'unassessable','reason':'pretrained_target_not_unique','timestamp_ms':stamp}
        bounds=result.get('input_transform',{}).get('crop_pixels',[0,0,width,height])
        visible_source=[p for p in result['poses'][0] if min(p.get('visibility',0),p.get('presence',0))>=.5 and
                        0<=p.get('x',-1)<=1 and 0<=p.get('y',-1)<=1]
        if len(visible_source)>=8:
            x0=min(p['x'] for p in visible_source)*width;x1=max(p['x'] for p in visible_source)*width
            y0=min(p['y'] for p in visible_source)*height;y1=max(p['y'] for p in visible_source)*height
            mx=(x1-x0)*.12;my=(y1-y0)*.08
            cropped=[max(bounds[0],x0-mx),max(bounds[1],y0-my),min(bounds[2],x1+mx),min(bounds[3],y1+my)]
            if cropped[2]-cropped[0]>=.1*width and cropped[3]-cropped[1]>=.2*height:bounds=cropped
        body=self.body.detect(rgb,bounds);features=body_features(body,width,height)
        from .body_lean import scene_axis,measure
        reference=self.axis.update(rgb,body,stamp) if hasattr(self,'axis') else scene_axis(rgb,body)
        features['scene_lean']=measure(body,width,height,reference,result['poses'][0])
        points=body['keypoints'];visible=[p for p in points[5:13] if p['coordinate_valid'] and p['score']>=.5]
        hands=[];other=[];raw_count=0;hand_reason='pretrained_parts_not_visible'
        if self.hands is not None and len(visible)>=6:
            shoulder=np.mean([[points[i]['x']*width,points[i]['y']*height] for i in (5,6)],axis=0)
            hip=np.mean([[points[i]['x']*width,points[i]['y']*height] for i in (11,12)],axis=0)
            margin=max(24.,.25*float(np.linalg.norm(shoulder-hip)))
            x0=max(0,int(min(p['x'] for p in visible)*width-margin));x1=min(width,int(max(p['x'] for p in visible)*width+margin))
            y0=max(0,int(min(p['y'] for p in visible)*height-margin));y1=min(height,int(max(p['y'] for p in visible)*height+margin))
            raw=self.hands.detect(np.ascontiguousarray(rgb[y0:y1,x0:x1]));raw_count=len(raw['hands']);hand_reason=raw.get('reason')
            restored=[]
            for hand in raw['hands']:
                keypoints=[{**p,'x':(x0+p['x']*(x1-x0))/width,'y':(y0+p['y']*(y1-y0))/height} for p in hand['keypoints']]
                a,b,c,d=hand['bbox'];restored.append({**hand,'bbox':[a+x0,b+y0,c+x0,d+y0],'keypoints':keypoints})
            hands,other=associate_hands(body,restored,width,height)
        return {'status':'observed','timestamp_ms':stamp,'body':body,'body_features':features,
            'hands':hands,'hand_reason':hand_reason,'raw_hand_candidates':raw_count,'unassociated_hands':len(other),
            'source_landmarks':result['poses'][0],'coordinate_space':'original_image_normalized',
            'body_model_sha256':self.body.resource['sha256'],'hand_model_sha256':self.hands.pose.resource['sha256'] if self.hands else None,
            'hand_detector_sha256':self.hands.detector.resource['sha256'] if self.hands else None,'target_basis':'existing_single_pose_automatic_person_box',
            'depth_available':False,'counting_input_modified':False}

    def close(self):
        self.body=None;self.hands=None
        if hasattr(self,'axis'):self.axis.reset()
