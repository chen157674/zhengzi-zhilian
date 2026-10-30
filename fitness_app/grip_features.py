"""实验用上肢局部视觉输入；只按可见关节裁图，不接收动作标签，不由生产识别加载。"""
import math
import numpy as np

VERSION='grip-local-2'
MINIMUM_ANGLE=35.
MAXIMUM_ANGLE=150.

def _regions(points,width,height):
    points=np.asarray(points)
    if points.shape!=(33,5):raise ValueError('局部特征需要33个关节点及可见性')
    result=[]
    for side,indices in [('left',(11,13,15)),('right',(12,14,16))]:
        joint=points[list(indices)]
        if not np.isfinite(joint).all() or (joint[:,3:]<.5).any() or (joint[:,3:]>1).any() or (joint[:,:2]<0).any() or (joint[:,:2]>1).any():continue
        shoulder,elbow,wrist=joint[:,:2]*[width,height];upper=shoulder-elbow;forearm=wrist-elbow
        a=np.linalg.norm(upper);b=np.linalg.norm(forearm)
        if min(a,b)<12:continue
        angle=math.degrees(math.acos(float(np.clip(np.dot(upper,forearm)/(a*b),-1,1))))
        if not MINIMUM_ANGLE<=angle<=MAXIMUM_ANGLE:continue
        radius=max(b*1.15,a*.65,24.)
        left,top=np.maximum(np.floor(wrist-radius),[0,0]).astype(int);right,bottom=np.minimum(np.ceil(wrist+radius),[width,height]).astype(int)
        if min(right-left,bottom-top)<48:continue
        # 保留原图方向；不能通过按标签旋转哑铃或填造被遮挡的手掌来制造握法。
        result.append({'side':side,'box':[int(left),int(top),int(right),int(bottom)],'elbow_angle':angle,'confidence':float(joint[:,3:].min())})
    return result

def local_patches(rgb,points):
    if not isinstance(rgb,np.ndarray) or rgb.dtype!=np.uint8 or rgb.ndim!=3 or rgb.shape[2]!=3 or min(rgb.shape[:2])<32:raise ValueError('需要有效RGB画面')
    rows=_regions(points,rgb.shape[1],rgb.shape[0]);result=[]
    for row in rows:
        l,t,r,b=row['box'];result.append({**row,'rgb':np.ascontiguousarray(rgb[t:b,l:r])})
    return result

def select_pose_frames(arrays,start_ms,end_ms,maximum=8):
    """同一语义窗口分时槽挑可见屈肘时刻；只读姿态，不读图像答案/动作标签。"""
    if type(maximum) is not int or not 1<=maximum<=8 or not start_ms<end_ms:raise ValueError('选帧范围无效')
    times=np.asarray(arrays['timestamps']);points=np.asarray(arrays['points']);people=np.asarray(arrays['people']);sizes=np.asarray(arrays['sizes'])
    n=len(times)
    if times.ndim!=1 or points.shape!=(n,33,5) or people.shape!=(n,) or sizes.shape!=(n,2) or not np.isfinite(times).all() or (np.diff(times)<=0).any():raise ValueError('选帧姿态与时间不一致')
    bins=np.linspace(start_ms,end_ms+1,maximum+1);chosen=[]
    for left,right in zip(bins,bins[1:]):
        best=None
        for i in np.flatnonzero((times>=left)&(times<right)):
            if people[i]!=1 or not np.isfinite(sizes[i]).all() or (sizes[i]<32).any():continue
            regions=_regions(points[i],int(sizes[i,0]),int(sizes[i,1]))
            if not regions:continue
            quality=(len(regions),sum(r['confidence'] for r in regions),-abs(float(times[i])-(left+right)/2))
            if best is None or quality>best[0]:best=(quality,int(times[i]))
        if best is not None:chosen.append(best[1])
    return chosen

def summarize(embeddings):
    matrix=np.asarray(embeddings,np.float32)
    if matrix.ndim!=2 or matrix.shape[1]!=512 or matrix.shape[0]<3 or not np.isfinite(matrix).all():raise ValueError('至少需要3个实际局部视觉特征')
    lengths=np.linalg.norm(matrix,axis=1,keepdims=True)
    if (lengths<1e-8).any():raise ValueError('局部特征为空')
    matrix=matrix/lengths
    return np.concatenate((matrix.mean(axis=0),matrix.std(axis=0))).astype(np.float32)
