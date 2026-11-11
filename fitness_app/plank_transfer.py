"""研究用平板二维共同特征；保留源标签，不假装三维模型可直接接相机。"""
import hashlib,json
from collections import deque
import numpy as np
from .frame_pipeline import usable_point

# 上游utils.py绘图骨链与BODY25：右肩肘腕髋膝踝=2/3/4/9/10/11，左=5/6/7/12/13/14。
SOURCE_SIDES=((2,3,4,9,10,11),(5,6,7,12,13,14))
IMAGE_SIDES={'right':(12,14,16,24,26,28),'left':(11,13,15,23,25,27)}
FEATURE_VERSION='plank-common-2d-geometry-1'


def frame_features(points):
    p=np.asarray(points,np.float64)
    if p.ndim!=3 or p.shape[1:]!=(6,2) or not np.isfinite(p).all():raise ValueError('平板共同输入须为有限T×6×2')
    shoulder,elbow,wrist,hip,knee,ankle=[p[:,i] for i in range(6)]
    axis=ankle-shoulder;scale=np.linalg.norm(axis,axis=1)
    if np.any(scale<1e-6):raise ValueError('肩踝轴退化')
    horizontal=axis/scale[:,None];normal=np.stack([-horizontal[:,1],horizontal[:,0]],axis=1)
    # 法向图像向上；镜像不改变高／低投影符号，拒绝近竖直的人体主轴。
    if np.any(np.abs(horizontal[:,0])<.5):raise ValueError('不是适用的平板侧面投影')
    normal*=np.where(normal[:,1]<0,1.,-1.)[:,None]
    relative=(p-shoulder[:,None,:])/scale[:,None,None]
    along=np.einsum('tij,tj->ti',relative,horizontal);offset=np.einsum('tij,tj->ti',relative,normal)
    def cosine(a,b,c):
        u=a-b;v=c-b;den=np.linalg.norm(u,axis=1)*np.linalg.norm(v,axis=1)
        if np.any(den<1e-8):raise ValueError('退化关节')
        return np.sum(u*v,axis=1)/den
    angles=np.stack([cosine(shoulder,hip,ankle),cosine(hip,knee,ankle),cosine(shoulder,elbow,wrist),cosine(elbow,shoulder,hip)],axis=1)
    return np.concatenate([along[:,1:5],offset[:,1:5],angles],axis=1)


def summarize(points):
    values=frame_features(points)
    if len(values)<4:raise ValueError('至少四个真实观察')
    # 不使用fps未知的速度；序列统计不输入人物、背景、深度和绝对位置。
    return np.concatenate([np.percentile(values,[10,50,90],axis=0).ravel(),values.std(axis=0)])


def source_views(poses,yaws=(-25.,0.,25.)):
    raw=np.asarray(poses,np.float64)
    if raw.ndim!=3 or raw.shape[1:]!=(3,25) or not np.isfinite(raw).all():raise ValueError('源三维姿态结构无效')
    views=[]
    for side,indices in enumerate(SOURCE_SIDES):
        for yaw in yaws:
            angle=np.radians(yaw);horizontal=raw[:,2,indices]*np.cos(angle)+raw[:,0,indices]*np.sin(angle)
            # 源侧面投影：纵向z、竖向y；不将合成投影称真实相机标定。
            points=np.stack([horizontal,raw[:,1,indices]],axis=-1)
            try:feature=summarize(points)
            except ValueError:continue
            views.append({'side':side,'yaw_degrees':yaw,'feature':feature})
    return views


def camera_points(frame,side):
    q=frame.get('framing',{});p=q.get('landmarks',[]);w,h=frame.get('width'),frame.get('height')
    if side not in IMAGE_SIDES or len(p)!=33 or q.get('person_count')!=1 or frame.get('identity_accepted') is False:return None
    if any(type(v) not in (int,float) or not np.isfinite(v) or v<=0 for v in (w,h)):return None
    indices=IMAGE_SIDES[side]
    if any(not usable_point(p[i]) or p[i].get('valid') is False for i in indices):return None
    return np.asarray([[p[i]['x']*w,p[i]['y']*h] for i in indices],np.float64)


def classify(probabilities,classes,threshold):
    values=np.asarray(probabilities,np.float64);classes=np.asarray(classes)
    if (values.ndim!=2 or values.shape[1]!=len(classes) or set(classes)!={1,7,8} or not np.isfinite(values).all()
        or np.any(values<0) or np.any(values>1) or not np.allclose(values.sum(1),1.,atol=1e-6)
        or type(threshold) not in (int,float) or not np.isfinite(threshold) or not 0<threshold<1):raise ValueError('概率结构无效')
    correct=int(np.flatnonzero(classes==1)[0]);errors=np.flatnonzero(classes!=1)
    result=np.ones(len(values),int);bad=1-values[:,correct]>=threshold
    result[bad]=classes[errors[np.argmax(values[bad][:,errors],axis=1)]]
    return result


class PlankGeometryEnsemble:
    """相同共同特征上的梯度树与随机树等权概率；只在训练数据拟合。"""
    def __init__(self):self.models=[]

    def fit(self,x,y):
        from sklearn.ensemble import HistGradientBoostingClassifier,ExtraTreesClassifier
        self.models=[HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=7,l2_regularization=10.,
            class_weight='balanced',random_state=20261104),
            ExtraTreesClassifier(n_estimators=160,max_depth=6,min_samples_leaf=4,
                class_weight='balanced',n_jobs=1,random_state=42)]
        for model in self.models:model.fit(x,y)
        self.classes_=self.models[0].classes_.copy()
        if not all(np.array_equal(model.classes_,self.classes_) for model in self.models):raise ValueError('组合模型类别顺序不一致')
        return self

    def predict_proba(self,x):
        if len(self.models)!=2:raise ValueError('组合模型尚未拟合')
        return (self.models[0].predict_proba(x)+self.models[1].predict_proba(x))/2


def per_subject_metrics(rows,prediction):
    """增强投影仍归属原人物；报告每个人的误报与召回，不能仅看总体。"""
    from .ec3d_training import metrics
    if len(rows)!=len(prediction) or not rows:raise ValueError('人物观察与预测不一致')
    result=[]
    for subject in sorted({r['subject'] for r in rows}):
        indices=[i for i,r in enumerate(rows) if r['subject']==subject]
        score=metrics([rows[i]['label'] for i in indices],np.asarray(prediction)[indices])
        e=score['error_detection'];den=e['fp']+e['tn']
        result.append({'held_subject':subject,'scope':'correlated_projected_views_not_independent_sequences',
            'metrics':score,'normal_false_positive_rate':e['fp']/den if den else None})
    return result


def choose_operating_point(options):
    """先限制每人的误报；没有合格点时如实标失败，禁止以整体均值掩盖。"""
    if not options:raise ValueError('没有阈值候选')
    feasible=[r for r in options if r['normal_false_positive_rate']<=.2 and
        all(f['normal_false_positive_rate'] is not None and f['normal_false_positive_rate']<=.25 for f in r['per_subject'])]
    def rank(row):
        recalls=[f['metrics']['error_detection']['recall'] for f in row['per_subject']]
        return (row['metrics']['error_detection']['f2'],min(recalls),row['metrics']['macro_recall'])
    selected=max(feasible,key=rank) if feasible else min(options,key=lambda r:max(f['normal_false_positive_rate'] if f['normal_false_positive_rate'] is not None else 1. for f in r['per_subject']))
    return {**selected,'subject_constraints_passed':bool(feasible)}


class PlankTransferProbe:
    """实际相机共同特征的研究适配器；永不生成正式纠错finding。"""
    def __init__(self,bundle=None):
        if bundle is None:
            import joblib
            from .paths import ROOT,project_path
            manifest=json.loads((ROOT/'data/models/open-source-correction/plank-transfer/manifest.json').read_text(encoding='utf-8'))
            path=project_path(manifest['model_path'])
            if manifest['runtime_enabled'] or hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['sha256']:raise ValueError('候选身份或启用状态异常')
            bundle=joblib.load(path)
        if bundle.get('feature_version')!=FEATURE_VERSION or bundle.get('runtime_enabled') is not False:raise ValueError('研究模型输入或启用状态不符')
        self.bundle=bundle;self.reset()

    def reset(self):
        self.rows=deque();self.last_stamp=None;self.side=None;self.pending_label=None;self.pending_start=None;self.pending_observations=0

    def update(self,frame,*,side,view,forearm_supported):
        stamp=frame.get('captured_ms')
        unknown={'status':'unassessable','publishable':False,'runtime_enabled':False}
        if type(stamp) is not int or stamp<0:
            self.reset();return {**unknown,'reason':'invalid_timestamp'}
        if view!='side' or not forearm_supported:
            self.reset();return {**unknown,'reason':'view_or_forearm_support_unconfirmed'}
        points=camera_points(frame,side)
        if points is None:
            self.reset();return {**unknown,'reason':'required_near_side_parts_missing'}
        if self.last_stamp is not None and (stamp<=self.last_stamp or stamp-self.last_stamp>500 or side!=self.side):self.reset()
        self.last_stamp=stamp;self.side=side
        try:frame_features(points[None])
        except ValueError:
            self.reset();return {**unknown,'reason':'geometry_unreliable'}
        self.rows.append((stamp,points))
        while self.rows and stamp-self.rows[0][0]>1600:self.rows.popleft()
        if len(self.rows)<4 or stamp-self.rows[0][0]<1200:return {**unknown,'reason':'awaiting_continuous_window'}
        feature=summarize(np.asarray([p for _,p in self.rows]))
        probabilities=self.bundle['model'].predict_proba(feature[None])[0]
        classes=self.bundle['model'].classes_
        label=int(classify(probabilities[None],classes,self.bundle['threshold'])[0])
        # 静态研究候选也要跨当前真实帧连续确认，不能把单帧类别直接当作指导。
        if label==1:
            self.pending_label=self.pending_start=None;self.pending_observations=0
        elif label!=self.pending_label:
            self.pending_label=label;self.pending_start=stamp;self.pending_observations=1
        else:self.pending_observations+=1
        confirmed=label==1 or (self.pending_observations>=3 and stamp-self.pending_start>=300)
        return {'status':'candidate' if confirmed else 'unassessable',
            'reason':None if confirmed else 'candidate_duration_unconfirmed',
            'candidate_label':label,'confirmation_observations':self.pending_observations,
            'confirmation_duration_ms':stamp-self.pending_start if self.pending_start is not None else 0,
            'label':label if confirmed else None,'source_label':{1:'Correct',7:'Banana back',8:'Rolled back'}[label],
            'probabilities':{str(int(c)):float(p) for c,p in zip(classes,probabilities)},'threshold':self.bundle['threshold'],
            'source_window_ms':[self.rows[0][0],stamp],'observed_frames':len(self.rows),'side':side,
            'publishable':False,'runtime_enabled':False,'no_overall_correctness':True}
