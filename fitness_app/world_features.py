"""世界坐标时序特征的懒加载CPU编码器，不提供显示或纠错用的补造关节。"""
import hashlib,json
import numpy as np
from .paths import ROOT

JOINTS=(0,11,12,13,14,15,16,23,24,25,26,27,28)


class WorldFeatures:
    def __init__(self):self.session=None
    def extract(self,arrays,start,end):
        indices=np.flatnonzero((arrays['timestamps']>=start)&(arrays['timestamps']<=end))
        if len(indices)<16:return None,0.
        times=arrays['timestamps'][indices]
        if np.diff(times).max()>500 or times[-1]-times[0]<1200:return None,0.
        if np.any(arrays['people'][indices]!=1):return None,0.
        values=arrays['world'][indices][:,JOINTS,:3]
        visible=arrays['world'][indices][:,JOINTS,3:5].min(-1)>=.5
        visibility=float(visible.mean())
        if not np.isfinite(values).all() or np.mean(visible[:,[1,2,7,8]])<.7:return None,visibility
        values=values.reshape(len(indices),39);desired=np.linspace(times[0],times[-1],32)
        values=np.stack([np.interp(desired,times,values[:,j]) for j in range(39)],axis=1).astype(np.float32)[None]
        if self.session is None:
            import onnxruntime as ort
            info=json.loads((ROOT/'data/models/skelgym/world-features.json').read_text());path=ROOT/info['path']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=info['sha256']:raise ValueError('骨架语义模型摘要不同')
            options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
            self.session=ort.InferenceSession(str(path),options,providers=['CPUExecutionProvider'])
        output=self.session.run(['features'],{'world_sequence':values})[0][0]
        if output.shape!=(112,) or not np.isfinite(output).all():raise ValueError('骨架语义输出无效')
        return output,visibility
