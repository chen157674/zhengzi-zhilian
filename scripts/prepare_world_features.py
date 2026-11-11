"""复用已固定的MIT骨架Transformer，导出分类前112维轨迹语义。"""
import sys,json,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import numpy as np,torch
from evaluate_skelgym import model,MODEL_DIR
from fitness_app.research_extract import atomic_json


def run():
    target=MODEL_DIR/'world-features.onnx';metadata=MODEL_DIR/'world-features.json'
    if target.exists() and metadata.exists():
        info=json.loads(metadata.read_text())
        if hashlib.sha256(target.read_bytes()).hexdigest()!=info['sha256']:raise ValueError('世界特征编码器摘要不同')
        print(json.dumps({'reused':True}));return
    net,labels,mean,std=model();torch.backends.mha.set_fastpath_enabled(False)
    class Encoder(torch.nn.Module):
        def __init__(self):
            super().__init__();self.net=net;self.register_buffer('mean',torch.tensor(mean,dtype=torch.float32));self.register_buffer('std',torch.tensor(std,dtype=torch.float32))
        def forward(self,x):
            h=self.net.in_norm((x-self.mean)/self.std);h=self.net.input_proj(h);h=self.net.input_drop(h);h=self.net.pos_encoder(h)
            h=self.net.transformer_encoder(h);h=self.net.norm(h).mean(1)
            return self.net.fc[:-1](h)
    encoder=Encoder().eval();torch.onnx.export(encoder,torch.zeros(1,32,39),str(target),opset_version=17,dynamo=False,input_names=['world_sequence'],output_names=['features'])
    import onnxruntime as ort
    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
    runtime=ort.InferenceSession(str(target),options,providers=['CPUExecutionProvider'])
    generator=np.random.default_rng(20261023);x=generator.normal(0,.4,(1,32,39)).astype(np.float32)
    with torch.inference_mode():expected=encoder(torch.from_numpy(x)).numpy()
    actual=runtime.run(['features'],{'world_sequence':x})[0];difference=float(abs(actual-expected).max())
    if actual.shape!=(1,112) or difference>1e-4:raise ValueError('骨架特征导出不等价')
    atomic_json(metadata,{'path':target.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'dimensions':112,'maximum_conversion_error':difference,
                          'source':'SkelGym MIT fixed world checkpoint and exact train-only normalization','input':[1,32,39],'joints':[0,11,12,13,14,15,16,23,24,25,26,27,28]})
    print(json.dumps({'features':112,'maximum_error':difference}),flush=True)


if __name__=='__main__':run()
