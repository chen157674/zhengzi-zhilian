"""时序动作角度特征与基准模型相似度推理模块。"""
import json
import numpy as np
from .pretrained_resources import repo_asset,registry,checked
from .angle_baseline_features import extract_landmarks


class _AngleNetwork:
    def __init__(self,path,kind):
        import h5py
        self.kind=kind;self.maxlen=800 if kind=='usefulness' else 300
        with h5py.File(path,'r') as source:
            weights={}
            def collect(name,value):
                if isinstance(value,h5py.Dataset):weights[name]=np.asarray(value,dtype=np.float32)
            source.visititems(collect)
        consumed=set()
        def one(shape,contains=None):
            keys=[key for key,value in weights.items() if value.shape==shape and (contains is None or contains in key)]
            if len(keys)!=1:raise ValueError('时序角度权重结构不唯一：'+str((shape,contains)))
            return keys[0]
        def pair(kernel):
            bias=kernel.replace('kernel:0','bias:0');consumed.update((kernel,bias))
            return weights[kernel],weights[bias]
        self.projection=pair(one((23,64)))
        self.query=pair(one((64,4,64),'/query/'))
        self.key=pair(one((64,4,64),'/key/'))
        self.value=pair(one((64,4,64),'/value/'))
        self.attention_output=pair(one((4,64,64),'/attention_output/'))
        ff=sorted(key for key,value in weights.items() if value.shape==(64,64))
        if len(ff)!=2:raise ValueError('时序角度前馈层数量不符')
        self.ff1,self.ff2=[pair(key) for key in ff]
        norms=sorted(key for key in weights if key.endswith('gamma:0'))
        if len(norms)!=2:raise ValueError('时序角度归一化层数量不符')
        self.norms=[]
        for key in norms:
            beta=key.replace('gamma:0','beta:0');consumed.update((key,beta));self.norms.append((weights[key],weights[beta]))
        self.embedding=pair(one((64,32)));self.output=pair(one((32,23)))
        if consumed!=set(weights):raise ValueError('时序角度含未消费权重，拒绝静默忽略')
        self.weight_tensors=len(weights)
        # 已发布H5没有Embedding参数，不能给它凭空增加当前源码的新位置编码。
        self.position_embedding_present=False

    def _inputs(self,angles,tail=False):
        values=np.asarray(angles,dtype=np.float32)
        if values.ndim!=2 or values.shape[1]!=23 or not 1<=len(values)<=100000 or not np.isfinite(values).all() or (np.abs(values)>360).any():raise ValueError('角度特征输入必须为有限T×23角度，范围不超过360度')
        padded=np.zeros((1,self.maxlen,23),dtype=np.float32)
        selected=values[-self.maxlen:] if tail else values[:self.maxlen]
        padded[0,:len(selected)]=selected
        return padded

    def encode_numpy(self,values):
        def dense(x,pair):return x@pair[0]+pair[1]
        def norm(x,pair):return (x-x.mean(-1,keepdims=True))/np.sqrt(x.var(-1,keepdims=True)+1e-6)*pair[0]+pair[1]
        x=dense(values,self.projection)
        q=np.einsum('bti,ihd->bthd',x,self.query[0])+self.query[1]
        k=np.einsum('bti,ihd->bthd',x,self.key[0])+self.key[1]
        v=np.einsum('bti,ihd->bthd',x,self.value[0])+self.value[1]
        scores=np.einsum('bthd,bshd->bhts',q/8.,k);scores-=scores.max(-1,keepdims=True)
        scores=np.exp(scores);scores/=scores.sum(-1,keepdims=True)
        a=np.einsum('bhts,bshd->bthd',scores,v)
        a=np.einsum('bthd,hdo->bto',a,self.attention_output[0])+self.attention_output[1]
        x=norm(x+a,self.norms[0]);ff=dense(np.maximum(dense(x,self.ff1),0),self.ff2)
        x=norm(x+ff,self.norms[1]);return np.maximum(dense(x.mean(1),self.embedding),0)

    def encode(self,values):
        import torch
        import torch.nn.functional as F
        def tensor(x):return torch.from_numpy(x)
        def dense(x,pair):return x@tensor(pair[0])+tensor(pair[1])
        def norm(x,pair):return F.layer_norm(x,(64,),tensor(pair[0]),tensor(pair[1]),1e-6)
        with torch.inference_mode():
            x=dense(tensor(values),self.projection)
            q=torch.einsum('bti,ihd->bthd',x,tensor(self.query[0]))+tensor(self.query[1])
            k=torch.einsum('bti,ihd->bthd',x,tensor(self.key[0]))+tensor(self.key[1])
            v=torch.einsum('bti,ihd->bthd',x,tensor(self.value[0]))+tensor(self.value[1])
            scores=torch.einsum('bthd,bshd->bhts',q/8.,k).softmax(-1)
            a=torch.einsum('bhts,bshd->bthd',scores,v)
            a=torch.einsum('bthd,hdo->bto',a,tensor(self.attention_output[0]))+tensor(self.attention_output[1])
            x=norm(x+a,self.norms[0]);x=norm(x+dense(F.relu(dense(x,self.ff1)),self.ff2),self.norms[1])
            return F.relu(dense(x.mean(1),self.embedding)).numpy()

    def predict(self,first,second=None):
        embedding=self.encode(self._inputs(first))
        if self.kind=='similarity':
            if second is None:raise ValueError('相似性模型需要参考序列与观察序列')
            embedding=np.abs(embedding-self.encode(self._inputs(second,tail=True)))
        logits=embedding@self.output[0]+self.output[1]
        return (1/(1+np.exp(-np.clip(logits,-80,80))))[0]


class ReferenceSimilarity:
    def __init__(self):
        useful,self.useful_resource=repo_asset('angle_baseline','weights/angle_usefulness_model_L800.h5')
        similarity,self.similarity_resource=repo_asset('angle_baseline','weights/angle_similarity_model_L300.h5')
        self.usefulness=_AngleNetwork(useful,'usefulness');self.similarity=_AngleNetwork(similarity,'similarity')
        data=registry().get('datasets',{}).get('angle_baseline',{})
        if data.get('state')!='ready':raise ValueError('请准备基准角度数据')
        item=next(row for row in data['files'] if row['upstream_path']=='dataset/exercise_angles.json')
        labels=json.loads(checked(item['path'],item).read_text(encoding='utf-8'))
        self.angle_names=sorted({name for names in labels.values() for name in names})
        if len(self.angle_names)!=23:raise ValueError('基准角度定义不是23维')

    def angles(self,frames,normalized=False):
        selected=[]
        for frame in frames:
            points=frame.get('landmarks',[])
            if len(points)!=33:continue
            values=np.asarray([[p[k] for k in ('x','y','z')] for p in points],dtype=np.float64)
            if not np.isfinite(values).all():continue
            if not normalized:
                center=(values[23]+values[24])/2;scale=abs(values[11,0]-values[12,0])
                if scale<.01:continue
                values[:,:2]=(values[:,:2]-center[:2])/scale;values[:,2]-=center[2]
                points=[{'x':p[0],'y':p[1],'z':p[2]} for p in values]
            selected.append({'landmarks':points})
        if not selected:raise ValueError('没有有效的33点三维输入')
        # 发布的训练笔记本使用sagittal；保持与其原始JSON前处理一致。
        values,_=extract_landmarks({'frames':selected},self.angle_names,'sagittal')
        if not np.isfinite(values).all():raise ValueError('角度前处理存在非有限值')
        return values.astype(np.float32)

    def compare(self,reference,observed,action_id,reference_action_id):
        if action_id!=reference_action_id:return {'status':'unassessable','reason':'reference_action_mismatch'}
        useful=self.usefulness.predict(reference);scores=self.similarity.predict(reference,observed)
        return {'status':'candidate','source':'angle_benchmark','action_id':action_id,'angles':[
            {'name':name,'usefulness':float(u),'similarity':float(s)} for name,u,s in zip(self.angle_names,useful,scores)],
            'correctness':None,'independent_accuracy_validated':False,'angle_contract':'sagittal_23',
            'model_sha256':self.similarity_resource['sha256'],'usefulness_model_sha256':self.useful_resource['sha256'],'reference_required':True}
