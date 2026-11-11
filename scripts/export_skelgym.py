"""导出已核对的MIT世界坐标分类器；训练集归一化折入ONNX，不在应用加载PyTorch。"""
import json,hashlib
from pathlib import Path
from evaluate_skelgym import model,MODEL_DIR,OUT,ROOT
import numpy as np
import torch


def run():
    net,labels,mean,std=model()
    class Wrapped(torch.nn.Module):
        def __init__(self):
            super().__init__();self.net=net
            self.register_buffer('mean',torch.as_tensor(mean,dtype=torch.float32));self.register_buffer('std',torch.as_tensor(std,dtype=torch.float32))
        def forward(self,x):return torch.softmax(self.net((x-self.mean)/self.std),dim=-1)
    wrapper=Wrapped().eval();torch.backends.mha.set_fastpath_enabled(False)
    path=MODEL_DIR/'world-transformer.onnx';manifest_path=MODEL_DIR/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if path.exists() and manifest.get('onnx',{}).get('sha256')==hashlib.sha256(path.read_bytes()).hexdigest():
        print(json.dumps({'reused':True,'sha256':manifest['onnx']['sha256']}));return
    torch.onnx.export(wrapper,torch.zeros(1,32,39),str(path),opset_version=17,dynamo=False,input_names=['world_sequence'],output_names=['probabilities'])
    import onnxruntime as ort
    options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
    session=ort.InferenceSession(str(path),options,providers=['CPUExecutionProvider'])
    with np.load(OUT/'commons-bench-fitnessscape-0-7100-full-world.npz',allow_pickle=False) as data:x=data['world'][30:62].reshape(1,32,39).astype(np.float32)
    with torch.inference_mode():reference=wrapper(torch.from_numpy(x)).numpy()
    actual=session.run(None,{'world_sequence':x})[0];difference=float(np.max(np.abs(reference-actual)))
    if difference>1e-5:raise ValueError('ONNX与原模型不等价')
    manifest['onnx']={'path':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size,
                      'input_shape':[1,32,39],'labels':labels,'normalization':'included_train_only_seed42_world','maximum_conversion_error':difference,
                      'pytorch':'2.8.0+cpu','onnx':'1.18.0','opset':17}
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'bytes':path.stat().st_size,'maximum_conversion_error':difference}))


if __name__=='__main__':run()
