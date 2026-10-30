"""只读已训练的健身域视频候选；未注册生产推理，不自动训练或下载模型。"""
import hashlib,json,time
import numpy as np
from .paths import ROOT

DIRECTORY=ROOT/'data/models/xclip-fitness'
REPORT=ROOT/'data/verification/step-22/xclip-fitness-training.json'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_resources():
    report=json.loads(REPORT.read_text(encoding='utf-8'));manifest=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
    signature=hashlib.sha256(json.dumps(report['identity'],sort_keys=True).encode()).hexdigest()
    if signature!=report['signature'] or manifest['signature']!=signature or manifest['sha256']!=report['checkpoint_sha256'] or digest(DIRECTORY/'candidate.pt')!=report['checkpoint_sha256']:raise ValueError('健身域候选身份或参数摘要不同')
    for relative,expected in report['identity']['code'].items():
        if digest(ROOT/relative)!=expected:raise ValueError('健身域候选的采样/网络源码不同，不能沿用')
    return report,manifest


def reusable_prediction(entry,snapshot,prediction,actual_ms,frozen_ms):
    if snapshot is None or prediction is None:return False
    if any(snapshot.get(k)!=entry.get(k) for k in ('id','source_sha256','start_ms','end_ms','crop','group')):return False
    if snapshot.get('partition')!='validation' or prediction.get('id')!=entry['id'] or prediction.get('source_group')!=entry['group']:return False
    if not np.array_equal(actual_ms,frozen_ms):return False
    score,margin=prediction.get('score'),prediction.get('margin')
    if not all(type(v) in (int,float) and np.isfinite(v) and 0<=v<=1 for v in (score,margin)):return False
    return True


class DomainVideoAdviser:
    def __init__(self,report,manifest,*,research=False):
        if not research and not (manifest.get('runtime_enabled',False) and manifest.get('development_gate_passed',False) and manifest.get('release_gate_passed',False)):
            raise RuntimeError('健身域视频候选未完成发布门槛，普通推理不能加载')
        self.report=report;self.manifest=manifest;self.labels=manifest['labels'];self.net=None;self.calls=0;self.latencies=[]
        self.config=json.loads((ROOT/'data/models/xclip-base-patch32/preprocessor_config.json').read_text(encoding='utf-8'))
    def _load(self):
        import torch
        from scripts.train_xclip_fitness import FitnessVideoNetwork
        if not torch.cuda.is_available():raise ValueError('已有健身域视频候选需要既有CUDA设备')
        checkpoint=torch.load(DIRECTORY/'candidate.pt',map_location='cpu',weights_only=True)
        if checkpoint['signature']!=self.manifest['signature'] or checkpoint['labels']!=self.labels or checkpoint['config']!=self.manifest['config']:raise ValueError('视频候选检查点内容不同')
        self.net=FitnessVideoNetwork(len(self.labels)).cuda().eval();self.net.load_state_dict(checkpoint['state_dict'],strict=True)
    def predict(self,images):
        import torch
        from .video_sampling import fit_pixels
        if len(images)!=8:raise ValueError('健身域视频候选需要8个实际画面')
        torch.set_num_threads(4)
        if self.net is None:self._load()
        pixels=fit_pixels(images,self.config).astype(np.float16);started=time.perf_counter()
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
            values=self.net(torch.from_numpy(pixels[None]).cuda().float()).float().softmax(-1)[0].cpu().numpy()
        torch.cuda.synchronize();self.calls+=1;self.latencies.append((time.perf_counter()-started)*1000)
        if values.shape!=(len(self.labels),) or not np.isfinite(values).all():raise ValueError('健身域视频候选输出无效')
        order=np.argsort(values)[::-1];top=self.labels[order[0]];score=float(values[order[0]]);margin=score-float(values[order[1]])
        return {'top':top,'score':score,'margin':margin,'score_is_calibrated_probability':False}
    def extract_head_features(self,images):
        """保存此前未保留的已训练时序/LN特征，编码器无梯度，标签不作为输入。"""
        import torch
        from .video_sampling import fit_pixels
        if len(images)!=8:raise ValueError('分类头适配需要8个实际画面')
        torch.set_num_threads(4)
        if self.net is None:self._load()
        pixels=fit_pixels(images,self.config).astype(np.float16)
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
            pooled=self.net.encoder.get_video_features(pixel_values=torch.from_numpy(pixels[None]).cuda().float()).pooler_output
            normalized=self.net.head[0](pooled);values=self.net.head[2](normalized).float().softmax(-1)
        feature=normalized[0].float().cpu().numpy();probability=values[0].cpu().numpy();self.calls+=1
        if feature.shape!=(512,) or probability.shape!=(len(self.labels),) or not np.isfinite(feature).all() or not np.isfinite(probability).all():raise ValueError('分类头特征无效')
        return feature,probability
    def close(self):
        if self.net is not None:
            import gc,torch
            self.net=None;gc.collect();torch.cuda.empty_cache()
