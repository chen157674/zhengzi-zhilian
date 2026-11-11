"""公开坐标纠错候选的特征和训练审计；不注册生产能力。"""
import hashlib
import numpy as np


def fingerprints(values):
    return np.array([hashlib.sha256(np.asarray(row,dtype='<f8').tobytes()).hexdigest() for row in values])


def blocked_split(labels,block=128,purge=16):
    """同标签连续行分块留出并隔离邻近行；源无人物ID，不冒充人物划分。"""
    labels=np.asarray(labels);validation=np.zeros(len(labels),bool)
    start=0
    for end in range(1,len(labels)+1):
        if end<len(labels) and labels[end]==labels[start]:continue
        for a in range(start,end,block):
            if ((a-start)//block)%5==3:validation[a:min(a+block,end)]=True
        start=end
    excluded=np.zeros(len(labels),bool)
    for offset in range(1,purge+1):
        excluded[offset:]|=validation[:-offset];excluded[:-offset]|=validation[offset:]
    train=~validation&~excluded
    if set(labels[train])!=set(labels) or set(labels[validation])!=set(labels):raise ValueError('分块后类别不足')
    return np.flatnonzero(train),np.flatnonzero(validation)


def features(values,columns,normalized=True):
    raw=np.asarray(values,np.float64)
    if raw.ndim!=2 or raw.shape[1]!=len(columns) or not np.isfinite(raw).all():raise ValueError('坐标表无效')
    if not normalized:return raw
    names=[c[:-2] for c in columns if c.endswith('_x')]
    positions=np.stack([raw[:,[columns.index(name+'_'+k) for k in ('x','y','z')]] for name in names],axis=1)
    visibility=np.stack([raw[:,columns.index(name+'_v')] for name in names],axis=1)
    ids={name:i for i,name in enumerate(names)}
    hip=(positions[:,ids['left_hip']]+positions[:,ids['right_hip']])/2
    shoulder=(positions[:,ids['left_shoulder']]+positions[:,ids['right_shoulder']])/2
    scale=np.maximum(np.linalg.norm((shoulder-hip)[:,:2],axis=1),.03)
    centered=(positions-hip[:,None,:])/scale[:,None,None]
    # x/y使用相同原图归一化坐标约定，源文件无宽高，不能称为真实角度。
    return np.concatenate([centered.reshape(len(raw),-1),visibility],axis=1)


def metrics(labels,predicted):
    labels=np.asarray(labels);predicted=np.asarray(predicted);classes=sorted(set(labels));rows={}
    for label in classes:
        tp=int(np.sum((labels==label)&(predicted==label)));fp=int(np.sum((labels!=label)&(predicted==label)));fn=int(np.sum((labels==label)&(predicted!=label)))
        rows[label]={'n':int(np.sum(labels==label)),'precision':tp/(tp+fp) if tp+fp else 0.,'recall':tp/(tp+fn) if tp+fn else 0.}
    bad=labels!='C';flag=predicted!='C';tp=int(np.sum(bad&flag));fp=int(np.sum(~bad&flag));fn=int(np.sum(bad&~flag));tn=int(np.sum(~bad&~flag))
    return {'rows':len(labels),'accuracy':float(np.mean(labels==predicted)), 'macro_recall':float(np.mean([r['recall'] for r in rows.values()])),
        'per_class':rows,'error_detection':{'tp':tp,'fp':fp,'fn':fn,'tn':tn,'precision':tp/(tp+fp) if tp+fp else 0.,
        'recall':tp/(tp+fn) if tp+fn else 0.,'f2':5*tp/(5*tp+4*fn+fp) if tp+fn+fp else 0.}}


def trunk_features(values,columns):
    """弯举躯干相对几何：不输入深度、可见性或人物绝对位置。"""
    raw=np.asarray(values,np.float64);index={c:i for i,c in enumerate(columns)}
    def point(name):return raw[:,[index[name+'_x'],index[name+'_y']]]
    features=[]
    for side in ('left','right'):
        vector=point(side+'_shoulder')-point(side+'_hip')
        norm=np.maximum(np.linalg.norm(vector,axis=1),.03)
        features.append(np.abs(vector[:,0])/norm)
    mid_shoulder=(point('left_shoulder')+point('right_shoulder'))/2
    mid_hip=(point('left_hip')+point('right_hip'))/2
    vector=mid_shoulder-mid_hip;norm=np.maximum(np.linalg.norm(vector,axis=1),.03)
    features.append(np.abs(vector[:,0])/norm)
    return np.stack(features,axis=1)
