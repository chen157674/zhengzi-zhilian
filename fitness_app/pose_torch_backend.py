"""复用已安装CUDA执行固定RTMPose图；失败时返回明确的CPU后端。

不修改原ONNX权重或现有ONNX Runtime；只转换两个已登记、校验过的姿态图。
"""
from contextlib import nullcontext
import logging
import importlib.metadata
import os
import threading
from types import SimpleNamespace
import numpy as np

_MATH_LOCK=threading.RLock()
_WARNED=set()
logger=logging.getLogger(__name__)


class TorchPoseSession:
    def __init__(self,path,cpu_factory=None):
        import onnx
        import torch
        from onnx2torch import convert
        self.lock=threading.RLock();self.cpu_factory=cpu_factory;self.cpu=None
        self.network=None;self.execution_backend='torch_cuda_fp32'
        graph=onnx.load(str(path),load_external_data=False)
        self.inputs=[]
        for value in graph.graph.input:
            shape=[d.dim_value for d in value.type.tensor_type.shape.dim]
            if shape and shape[0]==0:shape[0]=1
            if len(shape)!=4 or any(v<=0 for v in shape):raise ValueError('请核对姿态图输入尺寸')
            self.inputs.append(SimpleNamespace(name=value.name,shape=shape,type='tensor(float)'))
        self.outputs=[SimpleNamespace(name=v.name) for v in graph.graph.output]
        # ONNX以空字符串表达省略的可选参数，转换器需要删除末尾的空参数。
        self.compatibility_rewrites={}
        for node in graph.graph.node:
            while node.input and node.input[-1]=='':
                node.input.pop();self.compatibility_rewrites[node.op_type]=self.compatibility_rewrites.get(node.op_type,0)+1
        onnx.checker.check_model(graph)
        self.network=convert(graph).eval().to('cuda')
        # 小型姿态推理使用独立优先流，不排在整个视觉语言窗口之后。
        self.cuda_stream=torch.cuda.Stream(priority=-1)
        self.cuda_stream.wait_stream(torch.cuda.current_stream())

    def get_inputs(self):return self.inputs
    def get_outputs(self):return self.outputs
    def get_providers(self):return [self.execution_backend]

    def run(self,names,feeds):
        with self.lock:
            if self.cpu is not None:return self.cpu.run(names,feeds)
            if self.network is None:raise RuntimeError('姿态推理会话已关闭')
            if names is not None or set(feeds)!={self.inputs[0].name}:raise ValueError('姿态图输入不符')
            value=feeds[self.inputs[0].name]
            if value.dtype!=np.float32 or list(value.shape)!=self.inputs[0].shape or not np.isfinite(value).all():
                raise ValueError('姿态图张量结构不符')
            import torch
            try:
                stream=getattr(self,'cuda_stream',None)
                with _MATH_LOCK,torch.inference_mode(),(torch.cuda.stream(stream) if stream is not None else nullcontext()):
                    old_matmul=torch.backends.cuda.matmul.allow_tf32;old_cudnn=torch.backends.cudnn.allow_tf32
                    try:
                        torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
                        tensor=torch.as_tensor(value,device='cuda')
                        output=self.network(tensor)
                        result=[v.detach().cpu().numpy() for v in output]
                    finally:
                        torch.backends.cuda.matmul.allow_tf32=old_matmul;torch.backends.cudnn.allow_tf32=old_cudnn
                if not all(np.isfinite(v).all() for v in result):raise RuntimeError('CUDA_NONFINITE_OUTPUT')
                return result
            except RuntimeError as error:
                if self.cpu_factory is None or not any(word in str(error).lower() for word in ('cuda','cudnn','cublas','out of memory')):
                    raise
                logger.warning('姿态CUDA执行未完成，改用CPU；type=%s',type(error).__name__)
                self._release_network();self.cpu=self.cpu_factory();self.execution_backend='onnxruntime_cpu_fallback'
                return self.cpu.run(names,feeds)

    def _release_network(self):
        network=self.network;self.network=None;self.cuda_stream=None
        if network is not None:
            try:network.to('cpu')
            except RuntimeError as error:
                logger.warning('姿态CUDA资源转移未完成；type=%s',type(error).__name__)
            del network
            import torch
            try:torch.cuda.empty_cache()
            except RuntimeError as error:logger.warning('姿态CUDA缓存释放未完成；type=%s',type(error).__name__)

    def close(self):
        with self.lock:self._release_network();self.cpu=None


def optional_cuda_session(path,name,cpu_factory):
    preference=os.environ.get('FITNESS_POSE_DEVICE','auto')
    if preference not in {'auto','cpu','cuda'}:raise ValueError('姿态后端只能为auto/cpu/cuda')
    if preference=='cpu' or name not in {'rtmpose-body','rtmpose-hand'}:return None
    try:
        import torch
        import onnx2torch
        if not torch.cuda.is_available():return None
        if importlib.metadata.version('onnx2torch')!='1.5.15':return None
        # 当前转换器在2.9改变索引语义；未做对照的新版本继续使用CPU。
        if not torch.__version__.split('+')[0].startswith('2.8.'):return None
        return TorchPoseSession(path,cpu_factory)
    except (ImportError,NotImplementedError,RuntimeError,KeyError,ValueError) as error:
        if name not in _WARNED:
            logger.warning('姿态CUDA初始化未通过，使用CPU；model=%s type=%s',name,type(error).__name__);_WARNED.add(name)
        return None
