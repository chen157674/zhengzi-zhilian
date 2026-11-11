"""OpenMMLab官方ONNX权重输入适配，仅提供2D定位与可见状态候选。

坐标变换与SimCC解码依据MMPose官方ONNX示例（Apache-2.0），
RTMPose 关节点坐标转换与解码适配模块。
"""
import json
import math
import os
from pathlib import Path
import numpy as np
from .pretrained_resources import onnx_asset


def _image(rgb):
    if not isinstance(rgb,np.ndarray) or rgb.dtype!=np.uint8 or rgb.ndim!=3 or rgb.shape[2]!=3 or min(rgb.shape[:2])<2:
        raise ValueError('预训练视觉输入须为RGB uint8图像')


def cpu_thread_count():
    """小型姿态图的有界CPU并行度；不更改系统或Torch全局线程。"""
    requested=os.environ.get('FITNESS_POSE_CPU_THREADS')
    if requested is not None:
        if requested not in {'1','2','3','4','5','6','7','8'}:raise ValueError('姿态CPU线程数须为1～8的整数')
        return int(requested)
    return max(1,min(4,(os.cpu_count() or 4)//4))


def _session(name,device=None):
    import onnxruntime as ort
    path,record=onnx_asset(name);options=ort.SessionOptions()
    options.intra_op_num_threads=cpu_thread_count();options.inter_op_num_threads=1
    def cpu():return ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])
    from .pose_torch_backend import optional_cuda_session
    if device not in (None,'cpu'):raise ValueError('会话级姿态后端只支持默认或CPU隔离')
    session=(None if device=='cpu' else optional_cuda_session(path,name,cpu)) or cpu()
    shapes=session.get_inputs()
    if len(shapes)!=1 or len(shapes[0].shape)!=4 or shapes[0].shape[1]!=3:raise ValueError('ONNX视觉输入结构不符')
    return session,record


class RTMPose:
    def __init__(self,kind='body',device=None):
        if kind not in ('body','hand'):raise ValueError('不支持的RTMPose类别')
        self.kind=kind;self.session,self.resource=_session('rtmpose-'+kind,device)
        shape=self.session.get_inputs()[0].shape;self.height,self.width=map(int,shape[-2:])
        self.points_count=17 if kind=='body' else 21
        if len(self.session.get_outputs())!=2:raise ValueError('RTMPose应输出两个SimCC分布')

    def detect(self,rgb,bbox):
        import cv2
        _image(rgb);height,width=rgb.shape[:2]
        if len(bbox)!=4 or any(isinstance(v,bool) or not math.isfinite(float(v)) for v in bbox):raise ValueError('人体/手框无效')
        x0,y0,x1,y1=map(float,bbox)
        if not 0<=x0<x1<=width or not 0<=y0<y1<=height:raise ValueError('输入框超出图像')
        center=np.array([(x0+x1)/2,(y0+y1)/2],dtype=np.float32)
        scale=np.array([x1-x0,y1-y0],dtype=np.float32)*1.25;ratio=self.width/self.height
        if scale[0]/scale[1]>ratio:scale[1]=scale[0]/ratio
        else:scale[0]=scale[1]*ratio
        matrix=np.array([[self.width/scale[0],0,self.width/2-center[0]*self.width/scale[0]],
                         [0,self.height/scale[1],self.height/2-center[1]*self.height/scale[1]]],dtype=np.float32)
        transformed=cv2.warpAffine(rgb,matrix,(self.width,self.height),flags=cv2.INTER_LINEAR)
        normalized=(transformed.astype(np.float32)-np.array([123.675,116.28,103.53],np.float32))/np.array([58.395,57.12,57.375],np.float32)
        inputs=np.ascontiguousarray(normalized.transpose(2,0,1)[None])
        sx,sy=self.session.run(None,{self.session.get_inputs()[0].name:inputs})
        if sx.shape[1]!=self.points_count or sy.shape[1]!=self.points_count:raise ValueError('关键点数量不符')
        # 官方SimCC split_ratio=2，score为两个轴最大响应的较小值，不冒充已校准概率。
        locations=np.stack((sx[0].argmax(-1),sy[0].argmax(-1)),axis=-1).astype(np.float32)/2.
        locations=locations/np.array([self.width,self.height])*scale+center-scale/2
        scores=np.minimum(sx[0].max(-1),sy[0].max(-1));points=[]
        for point,score in zip(locations,scores):
            x,y=map(float,point);score=float(score)
            valid=all(math.isfinite(v) for v in (x,y,score)) and 0<=x<=width and 0<=y<=height and score>0
            points.append({'x':x/width,'y':y/height,'score':score,'coordinate_valid':valid})
        return {'keypoints':points,'bbox':list(bbox),'model_sha256':self.resource['sha256'],
            'execution_backend':getattr(self.session,'execution_backend','onnxruntime_cpu'),
            'coordinate_space':'original_image_normalized','depth_available':False,
            'input_size':[self.width,self.height],'score_is_calibrated_probability':False}

    def close(self):
        if self.session is not None:
            close=getattr(self.session,'close',None)
            if close:close()
            self.session=None


class RTMHandDetector:
    def __init__(self):
        self.session,self.resource=_session('rtmdet-hand')
        shape=self.session.get_inputs()[0].shape;self.height,self.width=map(int,shape[-2:])

    def detect(self,rgb,threshold=.3):
        import cv2
        _image(rgb);height,width=rgb.shape[:2]
        ratio=min(self.width/width,self.height/height)
        resized=cv2.resize(rgb[:,:,::-1],(int(width*ratio+.5),int(height*ratio+.5)))
        padded=np.full((self.height,self.width,3),114,dtype=np.uint8)
        padded[:resized.shape[0],:resized.shape[1]]=resized
        inputs=(padded.astype(np.float32)-np.array([103.53,116.28,123.675],np.float32))/np.array([57.375,57.12,58.395],np.float32)
        outputs=self.session.run(None,{self.session.get_inputs()[0].name:np.ascontiguousarray(inputs.transpose(2,0,1)[None])})
        detections=outputs[0]
        if detections.ndim!=3 or detections.shape[-1]!=5:raise ValueError('RTMDet输出不符')
        results=[]
        for detection in detections[0]:
            *coords,score=map(float,detection)
            if score<threshold:continue
            x0,y0,x1,y1=np.array(coords)/ratio
            box=[max(0.,x0),max(0.,y0),min(float(width),x1),min(float(height),y1)]
            if box[0]>=box[2] or box[1]>=box[3] or not np.isfinite(box).all():continue
            results.append({'bbox':box,'score':score})
        return results


def finger_observation(points,width,height,threshold=.3,wrist_anchor=None):
    fingers=[]
    for name,ids in (('index',(5,6,8)),('middle',(9,10,12)),('ring',(13,14,16)),('pinky',(17,18,20))):
        required=list(ids) if wrist_anchor is not None else [0,*ids]
        if any(not points[i]['coordinate_valid'] or points[i]['score']<threshold for i in required):
            fingers.append({'name':name,'state':'unknown'});continue
        xy={i:np.array([points[i]['x']*width,points[i]['y']*height]) for i in required}
        if wrist_anchor is not None:xy[0]=np.array(wrist_anchor,dtype=np.float64)
        mcp,pip,tip=[xy[i] for i in ids];a=mcp-pip;b=tip-pip;den=np.linalg.norm(a)*np.linalg.norm(b)
        if den<1e-6:fingers.append({'name':name,'state':'unknown'});continue
        angle=float(np.degrees(np.arccos(np.clip(a@b/den,-1,1))))
        extended=angle>160 and np.linalg.norm(tip-xy[0])>1.2*np.linalg.norm(mcp-xy[0])
        fingers.append({'name':name,'state':'extended' if extended else 'flexed_or_projected','pip_angle_degrees':angle})
    assessed=sum(f['state']!='unknown' for f in fingers);extended=sum(f['state']=='extended' for f in fingers)
    return {'state':'unassessable' if assessed<2 else 'fingers_extended' if extended>=3 else 'partial_fingers_extended' if extended>=2 else 'no_extension_pattern',
            'fingers':fingers,'assessed_fingers':assessed,'extended_fingers':extended,
            'grip_correctness':None,'contact_with_handle_verified':False,'thumb_contact_verified':False,
            'wrist_anchor_source':'body_model' if wrist_anchor is not None else 'hand_model'}


class PretrainedHands:
    def __init__(self,device=None):self.detector=RTMHandDetector();self.pose=RTMPose('hand',device=device)

    def detect(self,rgb):
        _image(rgb);height,width=rgb.shape[:2];boxes=self.detector.detect(rgb)
        if len(boxes)>8:return {'status':'unassessable','reason':'too_many_hand_candidates','detected_count':len(boxes),'hands':[]}
        hands=[]
        for box in boxes:
            pose=self.pose.detect(rgb,box['bbox'])
            hands.append({**box,**pose,'observation':finger_observation(pose['keypoints'],width,height)})
        return {'status':'observed' if hands else 'unassessable','reason':None if hands else 'hands_not_detected',
                'hands':hands,'width':width,'height':height,'model':'rtmdet-hand+rtmpose-hand',
                'detector_sha256':self.detector.resource['sha256'],'hand_pose_sha256':self.pose.resource['sha256'],
                'no_hand_does_not_mean_correct_grip':True}

    def close(self):
        self.pose.close();self.detector.session=None


COCO_TO_MEDIAPIPE={0:0,1:2,2:5,3:7,4:8,5:11,6:12,7:13,8:14,9:15,10:16,
                   11:23,12:24,13:25,14:26,15:27,16:28}


def associate_hands(body,hands,width,height):
    selected=[];used=set()
    for wrist_id in (9,10):
        wrist=body['keypoints'][wrist_id]
        if not wrist['coordinate_valid'] or wrist['score']<.5:continue
        x,y=wrist['x']*width,wrist['y']*height;candidates=[]
        for index,hand in enumerate(hands):
            if index in used:continue
            a,b,c,d=hand['bbox'];scale=max(12.,math.hypot(c-a,d-b))
            distance=math.hypot(x-(a+c)/2,y-(b+d)/2)/scale
            if distance<=1.25:candidates.append((distance,index))
        if candidates:
            _,index=min(candidates);used.add(index)
            hand={**hands[index],'body_wrist_index':wrist_id}
            hand['observation_with_body_wrist']=finger_observation(hand['keypoints'],width,height,wrist_anchor=(x,y))
            selected.append(hand)
    return selected,[hand for index,hand in enumerate(hands) if index not in used]



def recover_wrist_hands(body,hands,pose,rgb):
    """身体腕点引导两种裁剪复核；不降低响应门槛，不补造被遮挡的掌骨。"""
    _image(rgb);height,width=rgb.shape[:2];points=body.get('keypoints',[])
    if len(points)!=17:return hands,{'attempted':0,'recovered':0}
    xy=lambda p:np.array([p['x']*width,p['y']*height],np.float64)
    def visible(p,threshold=.5):
        return p.get('coordinate_valid') and all(type(p.get(k)) in (int,float) and math.isfinite(p[k]) for k in ('x','y','score')) and p['score']>=threshold and 0<=p['x']<=1 and 0<=p['y']<=1
    core=(0,5,9,13);kept=list(hands);attempted=recovered=0
    if not all(visible(points[i]) for i in (5,6,11,12)):return kept,{'attempted':0,'recovered':0}
    torso=float(np.linalg.norm(np.mean([xy(points[i]) for i in (5,6)],axis=0)-np.mean([xy(points[i]) for i in (11,12)],axis=0)))
    if torso<.08*height:return kept,{'attempted':0,'recovered':0}
    def geometry(k,wrist):
        if len(k)!=21 or not all(visible(k[i],.3) for i in core):return None
        palm_width=float(np.linalg.norm(xy(k[5])-xy(k[13])))
        if palm_width<3 or np.linalg.norm(xy(k[0])-xy(wrist))>max(6.,palm_width*.8):return None
        return palm_width
    for wrist_id,elbow_id,other_id in ((9,7,10),(10,8,9)):
        if not all(visible(points[i]) for i in (wrist_id,elbow_id)):continue
        if any(h.get('body_wrist_index')==wrist_id and geometry(h.get('keypoints',[]),points[wrist_id]) is not None for h in kept):continue
        wrist=xy(points[wrist_id]);forearm=wrist-xy(points[elbow_id]);length=float(np.linalg.norm(forearm))
        if length<12:continue
        center=wrist+.18*forearm;radius=max(12.,min(.38*torso,.65*length));candidates=[];attempted+=1
        for factor in (1.,1.25,1.5):
            size=radius*factor
            box=[max(0.,float(center[0]-size)),max(0.,float(center[1]-size)),min(float(width),float(center[0]+size)),min(float(height),float(center[1]+size))]
            if min(box[2]-box[0],box[3]-box[1])<16:break
            candidate=pose.detect(rgb,box);palm_width=geometry(candidate.get('keypoints',[]),points[wrist_id])
            if palm_width is None:continue
            k=candidate['keypoints']
            if visible(points[other_id]) and np.linalg.norm(xy(k[0])-xy(points[other_id]))<=np.linalg.norm(xy(k[0])-wrist)+palm_width*.5:continue
            candidates.append((candidate,palm_width))
            if len(candidates)>=2:
                a,aw=candidates[-2];b,bw=candidates[-1]
                disagreement=max(float(np.linalg.norm(xy(a['keypoints'][i])-xy(b['keypoints'][i]))) for i in core)
                if disagreement<=max(4.,min(aw,bw)*.5):break
        if len(candidates)<2:continue
        first,palm_width=candidates[-2];second=candidates[-1][0]
        disagreement=max(float(np.linalg.norm(xy(first['keypoints'][i])-xy(second['keypoints'][i]))) for i in core)
        if disagreement>max(4.,min(palm_width,candidates[-1][1])*.5):continue
        hand={**first,'score':min(first['keypoints'][i]['score'] for i in core),
            'score_basis':'minimum_palm_response_not_detector_probability',
            'body_wrist_index':wrist_id,'localization_basis':'body_wrist_two_crop_agreement',
            'crop_agreement_pixels':disagreement,'crop_agreement_count':2,
            'observation':finger_observation(first['keypoints'],width,height),
            'observation_with_body_wrist':finger_observation(first['keypoints'],width,height,wrist_anchor=wrist)}
        kept=[h for h in kept if h.get('body_wrist_index')!=wrist_id]+[hand];recovered+=1
    return kept,{'attempted':attempted,'recovered':recovered,'basis':'same_frame_body_wrist_two_crop_agreement'}


def quality_landmarks(result,threshold=.5):
    """仅映射真正存在的2D点给姿态规则，不补造手指/深度或修改计数输入。"""
    output=[{'valid':False} for _ in range(33)]
    for source,target in COCO_TO_MEDIAPIPE.items():
        point=result['keypoints'][source]
        if point['coordinate_valid'] and point['score']>=threshold:
            confidence=min(1.,max(0.,point['score']))
            output[target]={'x':point['x'],'y':point['y'],'z':0.,'visibility':confidence,'presence':confidence,
                            'valid':True,'depth_available':False}
    return output
