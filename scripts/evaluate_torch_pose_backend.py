"""用已有CUDA核查RTMPose转换的数值和耗时；不替换现有ONNX Runtime。"""
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import av
import numpy as np
import torch
from fitness_app.rtmpose_input import RTMPose
from fitness_app.pose_torch_backend import TorchPoseSession
from fitness_app.pretrained_resources import onnx_asset
from fitness_app.research_extract import atomic_json


def run():
    os.environ['FITNESS_POSE_DEVICE']='cpu'
    if not torch.cuda.is_available():raise RuntimeError('当前CUDA不可用')
    cache=ROOT/'data/experiments/open-source-correction/user-1689-native-inputs.json.gz'
    with gzip.open(cache,'rt',encoding='utf-8') as stream:data=json.load(stream)
    row=next(r for r in data['rows'] if r['t']>=5900 and (r['pose_result'].get('pretrained') or {}).get('hands'))
    video=ROOT/'data/datasets/user-demos/user-1689.mp4'
    if hashlib.sha256(video.read_bytes()).hexdigest()!=data['video_sha256']:raise ValueError('数值缓存来源不符')
    with av.open(str(video)) as container:
        stream=container.streams.video[0];container.seek(int(row['t']/1000/float(stream.time_base)),stream=stream)
        frame=next(f for f in container.decode(stream) if round(float(f.time)*1000)>=row['t'])
        if round(float(frame.time)*1000)!=row['t']:raise ValueError('原图与数值帧时间不一致')
        rgb=frame.to_ndarray(format='rgb24')
    observations=row['pose_result']['pretrained'];results=[]
    old_matmul=torch.backends.cuda.matmul.allow_tf32;old_cudnn=torch.backends.cudnn.allow_tf32
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    try:
        for kind,bbox in [('body',observations['body']['bbox']),('hand',observations['hands'][0]['bbox'])]:
            detector=RTMPose(kind);original=detector.session;captured={}
            class Capture:
                def __getattr__(self,name):return getattr(original,name)
                def run(self,names,feeds):
                    captured['feeds']={k:v.copy() for k,v in feeds.items()}
                    output=original.run(names,feeds);captured['outputs']=output;return output
            detector.session=Capture();detector.detect(rgb,bbox)
            feeds=captured['feeds'];expected=captured['outputs'];model_path,resource=onnx_asset('rtmpose-'+kind)
            accelerated=TorchPoseSession(model_path)
            def infer():return accelerated.run(None,feeds)
            for _ in range(3):actual=infer()
            began=time.perf_counter()
            for _ in range(10):actual=infer()
            gpu_ms=(time.perf_counter()-began)*1000/10
            began=time.perf_counter()
            for _ in range(10):original.run(None,feeds)
            cpu_ms=(time.perf_counter()-began)*1000/10
            errors=[float(np.max(np.abs(a-b))) for a,b in zip(expected,actual)]
            locations=[int(np.max(np.abs(a.argmax(-1)-b.argmax(-1)))) for a,b in zip(expected,actual)]
            passed=max(errors)<1e-3 and max(locations)==0
            results.append({'kind':kind,'model_sha256':resource['sha256'],'input_shape':list(next(iter(feeds.values())).shape),
                'max_output_abs_errors':errors,'maximum_simcc_bin_differences':locations,
                'cpu_ort_mean_ms':cpu_ms,'cuda_torch_mean_ms':gpu_ms,'speedup':cpu_ms/gpu_ms,
                'optional_empty_inputs_trimmed':accelerated.compatibility_rewrites,'numeric_gate_passed':passed})
            print(json.dumps(results[-1]),flush=True)
            accelerated.close();detector.close()
    finally:
        torch.backends.cuda.matmul.allow_tf32=old_matmul;torch.backends.cudnn.allow_tf32=old_cudnn
    result={'schema_version':1,'scope':'same_real_frame_tensor_numeric_and_latency_not_accuracy_benchmark',
        'source_sha256':data['video_sha256'],'source_ms':row['t'],'private_media_uploaded':False,
        'torch_version':torch.__version__,'cuda_version':torch.version.cuda,'gpu':torch.cuda.get_device_name(0),
        'onnx2torch_version':importlib.metadata.version('onnx2torch'),'models':results,
        'numeric_benchmark_only':True,'all_numeric_gates_passed':all(v['numeric_gate_passed'] for v in results)}
    target=ROOT/'data/verification/round3/torch-pose-backend-review.json'
    previous=json.loads(target.read_text(encoding='utf-8')) if target.exists() else None
    result['implementation_sha256']=hashlib.sha256((ROOT/'fitness_app/pose_torch_backend.py').read_bytes()).hexdigest()
    if previous:result['previous_reviews']=previous.get('previous_reviews',[])+[{k:v for k,v in previous.items() if k!='previous_reviews'}]
    atomic_json(target,result)


if __name__=='__main__':run()
