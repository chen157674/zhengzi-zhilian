"""独立场景方向与躯干/肩线共同侧倾；不拿人体自身方向校准相机。"""
import math
import numpy as np

VERSION='local-scene-body-lean-1'


def _unknown(reason):return {'status':'unassessable','reason':reason,'basis':VERSION}


def body_mask(body,h,w):
    import cv2
    points=body['keypoints'];xy=np.array([[p['x']*w,p['y']*h] for p in points],np.float64)
    core=(5,6,11,12)
    if any(not points[i]['coordinate_valid'] or points[i]['score']<.5 for i in core):return np.full((h,w),255,np.uint8)
    shoulder=xy[[5,6]].mean(0);hip=xy[[11,12]].mean(0)
    radius=max(6,round(float(np.linalg.norm(shoulder-hip))*.12))
    trunk=float(np.linalg.norm(shoulder-hip))
    mask=np.zeros((h,w),np.uint8)
    cv2.fillConvexPoly(mask,np.round(xy[[5,6,12,11]]).astype(np.int32),255)
    for a,b in ((0,1),(0,2),(1,3),(2,4),(5,6),(5,7),(7,9),(6,8),(8,10),(5,11),(6,12),(11,12),(11,13),(13,15),(12,14),(14,16)):
        if all(points[i]['coordinate_valid'] and points[i]['score']>=.5 for i in (a,b)):
            cv2.line(mask,tuple(np.round(xy[a]).astype(int)),tuple(np.round(xy[b]).astype(int)),255,radius*2)
    if points[0]['coordinate_valid'] and points[0]['score']>=.5:
        cv2.circle(mask,tuple(np.round(xy[0]).astype(int)),max(radius,round(trunk*.25)),255,-1)
    mask=cv2.dilate(mask,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(radius+1,radius+1)))
    return mask


def scene_axis(rgb,body):
    import cv2
    height,width=rgb.shape[:2];scale=min(1.,640/width)
    image=cv2.resize(rgb,(round(width*scale),round(height*scale))) if scale<1 else rgb
    h,w=image.shape[:2];points=body['keypoints']
    if min(h,w)<120:return _unknown('scene_reference_unavailable')
    core=(5,6,11,12)
    if any(not points[i]['coordinate_valid'] or points[i]['score']<.5 for i in core):return _unknown('pretrained_parts_not_visible')
    xy=np.array([[p['x']*w,p['y']*h] for p in points],np.float64)
    shoulder=xy[[5,6]].mean(0);hip=xy[[11,12]].mean(0);center=(shoulder+hip)/2
    trunk=float(np.linalg.norm(shoulder-hip));radius=max(6,round(trunk*.12))
    mask=body_mask(body,h,w)
    edges=cv2.Canny(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY),70,160);edges[mask!=0]=0
    lines=cv2.HoughLinesP(edges,1,math.pi/720,threshold=30,minLineLength=max(30,round(.14*h)),maxLineGap=10)
    candidates=[]
    for x0,y0,x1,y1 in np.asarray(lines).reshape(-1,4) if lines is not None else []:
        if y1>y0:x0,y0,x1,y1=x1,y1,x0,y0
        angle=math.degrees(math.atan2(x1-x0,y0-y1));length=math.hypot(x1-x0,y1-y0)
        if abs(angle)>25:continue
        a=(x1-x0)/(y0-y1);b=x0+a*y0
        candidates.append((angle,length,(x0+x1)/2,(y0+y1)/2,a,b))
    # 一根立柱的多条边不应胜过分布在不同位置的方向证据。
    distinct={}
    for row in sorted(candidates,key=lambda r:-r[1]):
        key=(round((row[5]-row[4]*center[1])/(.035*w)),round(row[0]/2.))
        if key not in distinct:distinct[key]=row
    rows=np.asarray(list(distinct.values())[:64],np.float64)
    if len(rows)<4:return _unknown('scene_reference_unavailable')
    angles,lengths,xs,ys,slopes,intercepts=rows.T
    weights=np.minimum(lengths,.5*h);models=[]
    for angle in angles:models.append(('parallel',float(angle),None))
    for i in range(len(rows)):
        for j in range(i):
            den=slopes[i]-slopes[j]
            if abs(den)<1e-3:continue
            vy=(intercepts[i]-intercepts[j])/den;vx=intercepts[i]-slopes[i]*vy
            if abs(vy-center[1])<.8*h or abs(vy-center[1])>30*h:continue
            local=math.degrees(math.atan((vx-center[0])/(center[1]-vy)))
            if abs(local)<=20:models.append(('perspective',local,(vx,vy)))
    best=None
    for kind,local,vp in models:
        predicted=np.full(len(rows),local) if vp is None else np.degrees(np.arctan((vp[0]-xs)/(ys-vp[1])))
        residual=np.abs(predicted-angles);support=residual<=2.
        if support.sum()<4:continue
        fraction=float(weights[support].sum()/weights.sum())
        spread=float(np.ptp(xs[support]))
        if fraction<.65 or spread<.25*w or weights[support].sum()<.8*h:continue
        score=float(weights[support].sum())-(.025*h if vp is not None else 0.)
        if best is None or score>best[0]:best=(score,kind,local,vp,support,residual,fraction,spread)
    if best is None:return _unknown('scene_reference_unavailable')
    _,kind,local,vp,support,residual,fraction,spread=best
    # 平行模型用中位数消除霍夫量化抖动，透视模型保留主体位置的局部方向。
    if vp is None:local=float(np.median(angles[support]))
    return {'status':'observed','basis':VERSION,'image_up_degrees':local,'model':kind,
            'support_lines':int(support.sum()),'support_fraction':fraction,'horizontal_span_fraction':spread/w,
            'median_residual_degrees':float(np.median(residual[support])),
            'vanishing_point_normalized':[vp[0]/w,vp[1]/h] if vp is not None else None,
            'width':width,'height':height,'reference_point':[float(center[0]/w),float(center[1]/h)],
            'limitation':'人体之外长线的局部图像方向参考，不是重力传感器'}


def measure(body,width,height,reference,source_landmarks=None):
    if reference.get('status')!='observed' or reference.get('basis')!=VERSION:return _unknown('scene_reference_unavailable')
    if (reference.get('width'),reference.get('height'))!=(width,height):return _unknown('scene_reference_unavailable')
    p=body['keypoints']
    if any(not p[i]['coordinate_valid'] or p[i]['score']<.5 for i in (5,6,11,12)):return _unknown('pretrained_parts_not_visible')
    xy=np.array([[v['x']*width,v['y']*height] for v in p],np.float64)
    shoulder=xy[[5,6]].mean(0);hip=xy[[11,12]].mean(0);trunk=shoulder-hip
    if np.linalg.norm(trunk)<.08*height:return _unknown('pretrained_geometry_unreliable')
    ratio=float(np.linalg.norm(xy[6]-xy[5])/np.linalg.norm(trunk))
    if ratio<.55:return _unknown('unsupported_view')
    axis=reference.get('image_up_degrees')
    if type(axis) not in (int,float) or not math.isfinite(axis) or abs(axis)>25:return _unknown('scene_reference_unavailable')
    trunk_angle=math.degrees(math.atan2(float(trunk[0]),float(-trunk[1])))-axis
    shoulder_angle=math.degrees(math.atan2(float(xy[6,1]-xy[5,1]),float(xy[6,0]-xy[5,0])))
    shoulder_angle=((shoulder_angle-axis+90)%180)-90
    agreed=trunk_angle*shoulder_angle>0 and min(abs(trunk_angle),abs(shoulder_angle))>=3.
    signed=(trunk_angle+shoulder_angle)/2
    source_angle=None
    if isinstance(source_landmarks,list) and len(source_landmarks)==33:
        source=[source_landmarks[i] for i in (11,12,23,24)]
        if all(isinstance(p,dict) and p.get('valid') is not False and min(p.get('visibility',0),p.get('presence',p.get('visibility',0)))>=.5 and
               all(type(p.get(k)) in (int,float) and math.isfinite(p[k]) and 0<=p[k]<=1 for k in ('x','y')) for p in source):
            dx=(source[0]['x']+source[1]['x']-source[2]['x']-source[3]['x'])*width/2
            dy=(source[0]['y']+source[1]['y']-source[2]['y']-source[3]['y'])*height/2
            if -dy>=.08*height:source_angle=math.degrees(math.atan2(dx,-dy))-axis
    cross=(source_angle is not None and source_angle*trunk_angle>0 and
           min(abs(source_angle),abs(trunk_angle))>=6. and abs(source_angle-trunk_angle)<=4.)
    if cross:signed=(trunk_angle+source_angle)/2
    anatomical_left=float(xy[5,0]-xy[6,0])
    side='left' if signed*anatomical_left>0 else 'right'
    return {'status':'observed','basis':VERSION,'value':abs(signed),'signed_degrees':signed,
            'trunk_degrees':trunk_angle,'shoulder_degrees':shoulder_angle,'direction_agreed':bool(agreed),
            'source_trunk_degrees':source_angle,'cross_model_agreement':bool(cross),
            'side':side,'view_ratio':ratio,'reference':reference,
            'candidate':bool(cross or agreed and abs(signed)>=6.)}


class SceneAxisTracker:
    """以独立背景点跟踪方向，拒绝无图像证据、跳帧和过期校准。"""
    def __init__(self):self.reset()

    def reset(self):
        self.gray=None;self.points=None;self.reference=None;self.stamp=None;self.anchor=None;self.last_search=None

    def update(self,rgb,body,stamp):
        import cv2
        height,width=rgb.shape[:2];scale=min(1.,640/width)
        small=cv2.resize(rgb,(round(width*scale),round(height*scale))) if scale<1 else rgb
        gray=cv2.cvtColor(small,cv2.COLOR_RGB2GRAY);h,w=gray.shape
        if self.stamp is not None and (stamp<=self.stamp or stamp-self.stamp>250 or gray.shape!=self.gray.shape):self.reset()
        # 排除真实躯干/骨段而非整个人体外接框，避免双臂展开时把可用背景全部遮掉。
        mask=255-body_mask(body,h,w)
        tracked=None
        if self.reference and self.points is not None and self.anchor is not None and stamp-self.anchor<=3000:
            moved,status,error=cv2.calcOpticalFlowPyrLK(self.gray,gray,self.points,None,winSize=(21,21),maxLevel=3,
                criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,30,.01))
            if moved is not None:
                a=self.points.reshape(-1,2);b=moved.reshape(-1,2)
                valid=(status.ravel()!=0)&(error.ravel()<15)&np.isfinite(b).all(1)
                a,b=a[valid],b[valid]
                inside=(b[:,0]>=0)&(b[:,0]<w)&(b[:,1]>=0)&(b[:,1]<h);a,b=a[inside],b[inside]
                if len(b):outside=mask[b[:,1].astype(int),b[:,0].astype(int)]!=0;a,b=a[outside],b[outside]
                if len(b)>=12:
                    matrix,inliers=cv2.estimateAffinePartial2D(a,b,method=cv2.RANSAC,ransacReprojThreshold=2.,maxIters=1000,confidence=.99)
                    if matrix is not None and inliers is not None:
                        good=inliers.ravel()!=0;fraction=float(good.mean());spread=np.ptp(b[good],axis=0) if good.any() else np.zeros(2)
                        rotation=math.degrees(math.atan2(float(matrix[1,0]),float(matrix[0,0])))
                        zoom=math.hypot(float(matrix[0,0]),float(matrix[1,0]))
                        if good.sum()>=12 and fraction>=.8 and spread[0]>=.25*w and spread[1]>=.2*h and abs(rotation)<=8 and .9<=zoom<=1.1:
                            old=self.reference;vp=old.get('vanishing_point_normalized')
                            center=np.mean([[p['x']*w,p['y']*h] for p in body['keypoints'][5:7]+body['keypoints'][11:13]],axis=0)
                            if vp is not None:
                                transformed=matrix@np.array([vp[0]*w,vp[1]*h,1.]);up=math.degrees(math.atan((transformed[0]-center[0])/(center[1]-transformed[1])))
                                vp=[float(transformed[0]/w),float(transformed[1]/h)]
                            else:up=old['image_up_degrees']+rotation
                            if abs(up)<=25:
                                tracked={**old,'image_up_degrees':up,'vanishing_point_normalized':vp,
                                    'tracking_inliers':int(good.sum()),'tracking_fraction':fraction,
                                    'calibration_age_ms':stamp-self.anchor,'direction_source':'tracked_background'}
        fresh=None
        if self.last_search is None or stamp-self.last_search>=400 or tracked is None:
            fresh=scene_axis(rgb,body);self.last_search=stamp
        if fresh and fresh.get('status')=='observed' and (tracked is None or abs(fresh['image_up_degrees']-tracked['image_up_degrees'])<=2):
            result={**fresh,'calibration_age_ms':0,'direction_source':'background_lines'};self.anchor=stamp
        else:result=tracked or _unknown('scene_reference_unavailable')
        self.gray=gray;self.stamp=stamp
        self.points=cv2.goodFeaturesToTrack(gray,maxCorners=150,qualityLevel=.02,minDistance=10,mask=mask,blockSize=7)
        self.reference=result if result.get('status')=='observed' else None
        return result
