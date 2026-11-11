"""固定开源平板模型的数值研究适配；默认不接生产纠错。"""
import io,json,pickle,hashlib
from pathlib import Path
import numpy as np
from .paths import ROOT,project_path
from .exercise_feedback import _Stored
DIRECTORY=ROOT/'data/models/open-source-correction/plank-reference'
INDICES=(0,11,12,13,14,15,16,23,24,25,26,27,28,29,30,31,32)
LABELS={0:'C',1:'H',2:'L'}


class _Reader(pickle.Unpickler):
    def find_class(self,module,name):
        if (module,name) in {('sklearn.linear_model._logistic','LogisticRegression'),('sklearn.preprocessing._data','StandardScaler')}:return _Stored
        if module in ('numpy.core.multiarray','numpy._core.multiarray') and name in ('_reconstruct','scalar'):
            from numpy._core import multiarray
            return getattr(multiarray,name)
        if module=='numpy' and name in ('ndarray','dtype'):return getattr(np,name)
        raise pickle.UnpicklingError('禁止模型调用未登记类型：'+module+'.'+name)


class PlankReference:
    def __init__(self):
        manifest=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
        if manifest['commit']!='202a0a802d8d2e3ea42f00e6a8c47da9cafc09d7' or manifest['runtime_enabled']:raise ValueError('研究资源身份或状态改变')
        self.resources={Path(row['source_path']).name:row for row in manifest['files']}
        values={}
        for name in ('LR_model.pkl','input_scaler.pkl'):
            row=self.resources[name];path=project_path(row['path']);raw=path.read_bytes()
            if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('平板参考权重摘要不符')
            values[name]=_Reader(io.BytesIO(raw)).load().state
        model=values['LR_model.pkl'];scaler=values['input_scaler.pkl']
        self.classes=np.asarray(model['classes_']);self.coef=np.asarray(model['coef_'],np.float64);self.intercept=np.asarray(model['intercept_'],np.float64)
        self.mean=np.asarray(scaler['mean_'],np.float64);self.scale=np.asarray(scaler['scale_'],np.float64)
        self.feature_names=list(scaler['feature_names_in_'])
        if self.classes.tolist()!=[0,1,2] or self.coef.shape!=(3,68) or self.intercept.shape!=(3,) or self.mean.shape!=(68,) or self.scale.shape!=(68,):raise ValueError('平板参考结构不符')
        if not all(np.isfinite(v).all() for v in (self.coef,self.intercept,self.mean,self.scale)) or np.any(self.scale<=0):raise ValueError('平板参考参数无效')
        self.manifest=manifest

    def predict_array(self,features):
        value=np.asarray(features,np.float64)
        if value.ndim!=2 or value.shape[1]!=68 or not 1<=len(value)<=100000 or not np.isfinite(value).all():raise ValueError('平板输入须为有限N×68原始MediaPipe坐标')
        logits=((value-self.mean)/self.scale)@self.coef.T+self.intercept
        logits-=logits.max(1,keepdims=True);probabilities=np.exp(logits);probabilities/=probabilities.sum(1,keepdims=True)
        return self.classes[probabilities.argmax(1)],probabilities

    def observe(self,landmarks):
        if len(landmarks)!=33:return {'status':'unassessable','reason':'required_parts_not_visible'}
        selected=[landmarks[i] for i in INDICES]
        if any(not isinstance(p,dict) or p.get('valid') is False or p.get('depth_available') is False or
            any(type(p.get(k)) not in (int,float) or not np.isfinite(p[k]) for k in ('x','y','z','visibility')) or
            p['visibility']<.5 or p.get('presence',p['visibility'])<.5 or not 0<=p['x']<=1 or not 0<=p['y']<=1 for p in selected):
            return {'status':'unassessable','reason':'required_parts_not_visible'}
        labels,probabilities=self.predict_array([[p[k] for p in selected for k in ('x','y','z','visibility')]])
        return {'status':'candidate' if probabilities[0].max()>=.6 else 'unassessable',
            'reason':None if probabilities[0].max()>=.6 else 'model_uncertain','label':LABELS[int(labels[0])],
            'score':float(probabilities[0].max()),'class_scores':{LABELS[int(k)]:float(v) for k,v in zip(self.classes,probabilities[0])},
            'model_sha256':self.resources['LR_model.pkl']['sha256'],'runtime_enabled':False,
            'source':'NgoQuocBao1010/Exercise-Correction','whole_exercise_correctness':None}
