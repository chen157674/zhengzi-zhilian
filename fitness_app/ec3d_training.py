"""EC3D本地研究：受限数值读取、整段分组和人物隔离；不接生产姿态。"""
import io,pickle,hashlib,json
from collections import defaultdict
import numpy as np
from .paths import ROOT,project_path

DIRECTORY=ROOT/'data/datasets/open-source-correction/ec3d'
JOINTS=list(range(15))+[19,21,22,24]
LABELS={1:'Correct',2:'Feets too wide',3:'Knees inward',4:'Not low enough',5:'Front bended',
        6:'Knees pass toes',7:'Banana back',8:'Rolled back',10:'Unknown'}
KNOWN={'SQUAT':{1,2,3,4,5},'Lunges':{1,4,6},'Plank':{1,7,8}}
SPLIT={'train':('Hugues','Isinsu'),'validation':('Sena',),'test':('Vidit',)}

class NumericReader(pickle.Unpickler):
    def find_class(self,module,name):
        if module in ('numpy.core.multiarray','numpy._core.multiarray') and name in ('_reconstruct','scalar'):
            from numpy._core import multiarray
            return getattr(multiarray,name)
        if module=='numpy' and name in ('ndarray','dtype'):return getattr(np,name)
        raise pickle.UnpicklingError('EC3D禁止执行未登记类型：'+module+'.'+name)


def load():
    manifest=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
    if manifest['commit']!='1c8d28ddc8a346634f9c86335e3a261f90d6b9b2':raise ValueError('EC3D版本不符')
    entry=next(row for row in manifest['files'] if row['source_path']=='data/EC3D/data_3D.pickle')
    raw=project_path(entry['path']).read_bytes()
    if len(raw)!=entry['bytes'] or hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('EC3D文件摘要不符')
    data=NumericReader(io.BytesIO(raw)).load()
    if not isinstance(data,dict) or set(data)!={'labels','poses'}:raise ValueError('EC3D字典结构变化')
    labels=np.asarray(data['labels']);poses=np.asarray(data['poses'])
    if labels.shape!=(29789,5) or labels.dtype.kind!='U' or poses.shape!=(29789,3,25) or poses.dtype.kind!='f' or not np.isfinite(poses).all():raise ValueError('EC3D数值结构不符')
    return labels,poses,entry


def sequences(labels,poses):
    groups=defaultdict(list)
    for index,row in enumerate(labels):groups[tuple(map(str,row[:4]))].append(index)
    output=[];excluded=[]
    for (action,subject,label,rep),indices in groups.items():
        code=int(label);partition=next((key for key,values in SPLIT.items() if subject in values),None)
        if partition is None or action not in KNOWN:raise ValueError('EC3D出现未知人物或动作')
        stamps=np.asarray([int(labels[i,4]) for i in indices]);order=np.argsort(stamps);indices=np.asarray(indices)[order]
        if len(np.unique(stamps))!=len(stamps):raise ValueError('同动作／人物／标签／次序含重复帧')
        row={'id':action+'/'+subject+'/'+label+'/'+rep,'action':action,'subject':subject,'label':code,
            'source_label_text':LABELS.get(code,'Unmapped'),'rep':rep,'partition':partition,'frames':len(indices)}
        if code not in KNOWN[action]:excluded.append(row);continue
        values=poses[indices][:,:,JOINTS].copy();row['pose_sha256']=hashlib.sha256(values.astype('<f8').tobytes()).hexdigest()
        output.append((row,values))
    return output,excluded


def sequence_features(values):
    poses=np.asarray(values,np.float64)
    if poses.ndim!=3 or poses.shape[1:]!=(3,19) or len(poses)<8 or not np.isfinite(poses).all():raise ValueError('EC3D序列无效')
    # 以整段非零关节距离的中位数定尺度，统计特征不使用人物／帧号／标签。
    a,b=np.triu_indices(19,1);distance=np.linalg.norm(poses[:,:,a]-poses[:,:,b],axis=1)
    positive=distance[distance>1e-8]
    if not len(positive):raise ValueError('退化骨架')
    scale=float(np.median(positive))
    if not np.isfinite(scale) or scale<1e-8:raise ValueError('退化骨架')
    distance=distance/scale
    # 序列速度以帧序号为参考，源fps未知，不称真实运动速度。
    change=np.abs(np.diff(distance,axis=0))
    return np.concatenate([np.percentile(distance,[10,50,90],axis=0).ravel(),distance.std(axis=0),
        np.mean(change,axis=0),np.percentile(change,90,axis=0)])


def metrics(actual,prediction):
    actual=np.asarray(actual);prediction=np.asarray(prediction);classes=sorted(set(actual));per={}
    for label in classes:
        tp=int(np.sum((actual==label)&(prediction==label)));fp=int(np.sum((actual!=label)&(prediction==label)))
        per[str(label)]={'label':LABELS[int(label)],'n':int(np.sum(actual==label)),
            'recall':tp/int(np.sum(actual==label)),'precision':tp/(tp+fp) if tp+fp else 0.}
    bad=actual!=1;flag=prediction!=1;tp=int(np.sum(bad&flag));fn=int(np.sum(bad&~flag));fp=int(np.sum(~bad&flag));tn=int(np.sum(~bad&~flag))
    return {'sequences':len(actual),'accuracy':float(np.mean(actual==prediction)),
        'macro_recall':float(np.mean([row['recall'] for row in per.values()])), 'per_class':per,
        'error_detection':{'tp':tp,'fn':fn,'fp':fp,'tn':tn,'recall':tp/(tp+fn) if tp+fn else None,
            'precision':tp/(tp+fp) if tp+fp else None,'f2':5*tp/(5*tp+4*fn+fp) if tp+fn+fp else 0.},
        'confusion':{str(a):{str(b):int(np.sum((actual==a)&(prediction==b))) for b in classes} for a in classes}}
