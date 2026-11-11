"""严格杠铃推举屈膝候选的CPU数值适配；尚未注册进生产判项。

网络和兼容特征依据 evlo-malik/exercise-advisor（MIT，固定提交
522e939f5eac9622430096e1911e91e7132945e6）；许可随资源保留。
改为NumPy推理，并在模型之前拒绝缺失、稀疏、未完成或动作不匹配的输入。
上游兼容角度特征不作为实际腕／踝解剖测量展示。
"""
import json
import math
import threading
import time
import zipfile
import numpy as np
from .paths import ROOT,project_path
from .pretrained_resources import checked

MANIFEST=ROOT/'data/models/open-source-correction/exercise-advisor/manifest.json'
TRIPLETS=((13,11,15),(14,12,16),(11,23,13),(12,24,14),
          (23,11,25),(24,12,26),(25,23,27),(26,24,28),
          (27,25,23),(28,26,24),(15,13,11),(16,14,12))
CRITICAL=(11,12,23,24,25,26,27,28)


def temporal_features(landmarks,length=100):
    """保留上游222维定义；不以插值补造不可见关节。"""
    points=np.asarray(landmarks,dtype=np.float32)
    if points.ndim!=3 or points.shape[1:]!=(33,3) or len(points)<2 or not np.isfinite(points).all():
        raise ValueError('时序候选需要有限的33点真实观测')
    points=points.copy()
    points-=(points[:,23]+points[:,24])[:,None,:]/2
    scale=np.linalg.norm(points[:,11]-points[:,12],axis=1)
    if (scale<1e-6).any():raise ValueError('肩部尺度不足')
    points/=scale[:,None,None]
    angles=[]
    for joint,proximal,distal in TRIPLETS:
        a=points[:,proximal]-points[:,joint];b=points[:,distal]-points[:,joint]
        cosine=(a*b).sum(-1)/(np.linalg.norm(a,axis=-1)*np.linalg.norm(b,axis=-1)+1e-8)
        angles.append(np.degrees(np.arccos(np.clip(cosine,-1,1))))
    values=np.concatenate((points.reshape(len(points),99),np.stack(angles,axis=1)),axis=1)
    velocity=np.zeros_like(values);velocity[1:]=values[1:]-values[:-1]
    values=np.concatenate((values,velocity),axis=1).astype(np.float32)
    if len(values)==length:return values
    old=np.linspace(0.,1.,len(values));new=np.linspace(0.,1.,length)
    return np.stack([np.interp(new,old,values[:,i]) for i in range(values.shape[1])],axis=1).astype(np.float32)


class TemporalKneeCandidate:
    def __init__(self):
        self.resource=json.loads(MANIFEST.read_text(encoding='utf-8'))
        if (self.resource['source_repo'],self.resource['source_commit'],self.resource['license'])!=(
            'evlo-malik/exercise-advisor','522e939f5eac9622430096e1911e91e7132945e6','MIT'):
            raise ValueError('时序候选来源不符')
        self.arrays=None;self.lock=threading.RLock()
        self.weight=next(v for v in self.resource['files'] if v['source_path']=='checkpoints/OHP_error_knees.pt')

    def load(self):
        with self.lock:
            if self.arrays is not None:return
            path=checked(self.weight['path'],self.weight)
            import torch
            with zipfile.ZipFile(path) as archive:
                if len(archive.infolist())>1000 or sum(v.file_size for v in archive.infolist())>16*1024*1024:
                    raise ValueError('时序权重结构异常')
            payload=torch.load(path,map_location='cpu',weights_only=True)
            cfg=payload['config']
            if payload['format_version']!=1 or payload['exercise']!='OHP' or payload['class_name']!='error_knees':
                raise ValueError('时序权重任务不符')
            expected={'fixed_len':100,'use_angles':True,'use_velocity':True,'hidden_dim':128,'n_layers':6,'kernel_size':3}
            if any(cfg[k]!=v for k,v in expected.items()) or payload['threshold']!=.55:
                raise ValueError('时序权重配置不符')
            state=payload['model_state']
            if len(state)!=94 or state['input_proj.weight'].shape!=(128,222) or state['cls_head.3.weight'].shape!=(1,128):
                raise ValueError('时序网络结构不符')
            arrays={k:v.detach().cpu().numpy().copy() for k,v in state.items()}
            if any(not np.isfinite(v).all() for v in arrays.values()):raise ValueError('时序权重含非有限值')
            if any((v<0).any() for k,v in arrays.items() if k.endswith('running_var')):raise ValueError('归一化方差无效')
            for value in arrays.values():value.flags.writeable=False
            self.arrays=arrays

    def _linear(self,x,prefix):
        return x@self.arrays[prefix+'.weight'].T+self.arrays[prefix+'.bias']

    def _conv(self,x,prefix,dilation):
        weights=self.arrays[prefix+'.weight'];length=x.shape[1]
        padded=np.pad(x,((0,0),(2*dilation,0)))
        result=np.zeros((128,length),np.float32)
        for k in range(3):result+=weights[:,:,k]@padded[:,k*dilation:k*dilation+length]
        return result+self.arrays[prefix+'.bias'][:,None]

    def _norm(self,x,prefix):
        a=self.arrays
        return ((x-a[prefix+'.running_mean'][:,None])/np.sqrt(a[prefix+'.running_var'][:,None]+1e-5)
                *a[prefix+'.weight'][:,None]+a[prefix+'.bias'][:,None])

    def logits(self,features):
        """独立于Torch的推理，仅用于已经核查的固定网络。"""
        self.load();x=np.asarray(features,dtype=np.float32)
        if x.shape!=(100,222) or not np.isfinite(x).all():raise ValueError('时序特征须为100×222')
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=2,user_api='blas'):
            h=self._linear(x,'input_proj').T
            for i in range(6):
                prefix='tcn.'+str(i);dilation=2**i
                value=np.maximum(self._norm(self._conv(h,prefix+'.conv1',dilation),prefix+'.norm1'),0)
                value=np.maximum(self._norm(self._conv(value,prefix+'.conv2',dilation),prefix+'.norm2'),0)
                h=np.maximum(h+value,0)
            attention=self._linear(np.tanh(self._linear(h.T,'attn_pool.attn.0')),'attn_pool.attn.2')[:,0]
            attention=np.exp(attention-attention.max());attention/=attention.sum()
            pooled=attention@h.T
            return float(self._linear(np.maximum(self._linear(pooled,'cls_head.0'),0),'cls_head.3')[0])

    def observe(self,landmarks,timestamps,visibility,*,action_id,phase_complete,single_person=True):
        reason=None;points=np.asarray(landmarks,dtype=np.float32);times=np.asarray(timestamps,dtype=np.float64)
        scores=np.asarray(visibility,dtype=np.float32)
        if action_id!='barbell_overhead_press':reason='unsupported_exercise'
        elif phase_complete is not True:reason='awaiting_complete_cycle'
        elif single_person is not True:reason='target_not_unique'
        elif points.ndim!=3 or points.shape[1:]!=(33,3) or not 12<=len(points)<=1200:reason='insufficient_observations'
        elif times.shape!=(len(points),) or scores.shape!=(len(points),33):reason='invalid_observation_shape'
        elif not np.isfinite(points).all() or not np.isfinite(times).all() or not np.isfinite(scores).all():reason='missing_observations'
        elif ((scores<0)|(scores>1)).any():reason='invalid_visibility'
        elif (scores[:,CRITICAL]<.5).any():reason='critical_joints_not_visible'
        elif (np.linalg.norm(points[:,11]-points[:,12],axis=1)<.02).any():reason='geometry_unreliable'
        elif (np.diff(times)<=0).any() or (np.diff(times)>250).any():reason='source_gap'
        elif not 400<=times[-1]-times[0]<=15000:reason='cycle_duration_not_validated'
        elif (len(times)-1)*1000/(times[-1]-times[0])<20:reason='sampling_rate_not_validated'
        if reason:return {'status':'unassessable','reason':reason,'production_enabled':False}
        began=time.perf_counter()
        value=self.logits(temporal_features(points))
        if not math.isfinite(value):return {'status':'unassessable','reason':'non_finite_prediction','production_enabled':False}
        score=1/(1+math.exp(-max(-80,min(80,value))))
        return {'status':'candidate','class_name':'error_knees','score':score,'threshold':.55,
            'candidate_detected':score>=.55,'score_is_calibrated_probability':False,
            'source_sha256':self.weight['sha256'],'source_commit':self.resource['source_commit'],
            'time_range_ms':[float(times[0]),float(times[-1])],'temporal_location':'whole_completed_rep_not_frame_label',
            'elapsed_ms':round((time.perf_counter()-began)*1000,2),'production_enabled':False,
            'independent_specific_error_validation':False}
