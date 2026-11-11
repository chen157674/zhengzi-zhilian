"""提取连续视频的真实姿态与稀疏图像特征；缓存逐片复用，不读取旧 test。"""
import argparse,hashlib,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av,numpy as np
from fitness_app.gym_pose import GymPoseService
from fitness_app.visual_recognition import DIRECTORY,preprocess,normalize
from fitness_app.pose import checked_model_hash

OUT=ROOT/'data/verification/step-22'
FIELDS=('timestamps','points','people','sizes','bounds','features')


class ImageEncoder:
    def __init__(self):
        import onnxruntime as ort
        self.info=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
        path=DIRECTORY/'onnx/vision_model_quantized.onnx';stat=path.stat()
        if not checked_model_hash(str(path),stat.st_mtime_ns,stat.st_size,self.info['files']['onnx/vision_model_quantized.onnx']['sha256']):raise ValueError('图像骨干身份不同')
        self.config=json.loads((DIRECTORY/'preprocessor_config.json').read_text(encoding='utf-8'))
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        self.model=ort.InferenceSession(str(path),options,providers=['CPUExecutionProvider'])
    def embed(self,rgb):return normalize(self.model.run(['image_embeds'],{'pixel_values':preprocess(rgb,self.config)[None]})[0])[0]


def target(segment):return OUT/(segment['id']+'-sequence.npz')


def extract(segment,source,encoder):
    path=project_path(source['path']);start,end=segment['start_ms'],segment['end_ms'];crop=segment['crop']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=segment['source_sha256']:raise ValueError('视频摘要改变')
    policy={'schema':'continuous-gym-features-1','source_sha256':segment['source_sha256'],'start_ms':start,'end_ms':end,
            'crop':crop,'pose_step_ms':100,'image_step_ms':500,'backbone_revision':encoder.info['revision'],
            'pose_model_sha256':json.loads((ROOT/'data/models/manifest.json').read_text(encoding='utf-8'))['sha256'],
            'pose_code':{name:hashlib.sha256((ROOT/'fitness_app'/name).read_bytes()).hexdigest() for name in ('pose.py','pose_input.py','gym_pose.py','content_bounds.py')}}
    destination=target(segment)
    if destination.exists():
        with np.load(destination,allow_pickle=False) as cached:
            previous=json.loads(str(cached['metadata']));previous.pop('reused_frozen_2d_cache_sha256',None)
            if previous!=policy:raise ValueError('连续缓存策略改变，请明确受影响范围')
            return len(cached['timestamps']),True
    legacy=OUT/f"{segment['source_id']}-{start}-{end}.npz"
    arrays=None
    if legacy.exists():
        with np.load(legacy,allow_pickle=False) as cached:
            metadata=json.loads(str(cached['metadata']))
            if metadata['source_sha256']==segment['source_sha256'] and metadata['crop']==crop:
                arrays={k:cached[k].copy() for k in FIELDS}
                # 仅复用原二维输出；新增 world 字段不改变原二维推理。
                policy['reused_frozen_2d_cache_sha256']=hashlib.sha256(legacy.read_bytes()).hexdigest()
    if arrays is None:
        arrays={k:[] for k in FIELDS};next_stamp=start;next_image=start
        with GymPoseService(video=True) as pose,av.open(str(path)) as video:
            video.seek(start*1000)
            for frame in video.decode(video=0):
                if frame.time is None:continue
                stamp=round(float(frame.time)*1000)
                if stamp<next_stamp:continue
                if stamp>=end:break
                rgb=frame.to_ndarray(format='rgb24')
                if crop:
                    h,w=rgb.shape[:2];l,t,r,b=crop;rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
                result=pose.detect(rgb,stamp);points=np.full((33,5),np.nan,np.float32)
                if len(result['poses'])==1:points=np.asarray([[p[k] for k in ('x','y','z','visibility','presence')] for p in result['poses'][0]],np.float32)
                feature=np.full(512,np.nan,np.float32)
                if stamp>=next_image:
                    feature=encoder.embed(rgb);next_image=stamp+500
                for k,v in dict(timestamps=stamp,points=points,people=len(result['poses']),sizes=(rgb.shape[1],rgb.shape[0]),bounds=result['content_bounds'],features=feature).items():arrays[k].append(v)
                while next_stamp<=stamp:next_stamp+=100
        arrays={k:np.asarray(v) for k,v in arrays.items()}
    part=destination.with_suffix('.npz.part');owned=False
    try:
        with part.open('xb') as output:
            owned=True;np.savez_compressed(output,metadata=json.dumps(policy,sort_keys=True),**arrays)
        os.replace(part,destination)
    finally:
        if owned and part.exists():part.unlink()
    return len(arrays['timestamps']),False


def run(only=None,pending_only=False):
    sources={s['id']:s for s in json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources']}
    segments=json.loads((ROOT/'data/datasets/development-movements/gym-segments.json').read_text(encoding='utf-8'))['segments']
    encoder=ImageEncoder()
    for i,segment in enumerate(segments):
        if segment['partition'] not in {'train','validation','diagnostic'}:continue
        if pending_only and target(segment).exists():continue
        if only and not segment['source_id'].startswith(only):continue
        n,reused=extract(segment,sources[segment['source_id']],encoder)
        print(json.dumps({'ready':i+1,'total':len(segments),'segment':segment['id'],'frames':n,'reused':reused}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--only');parser.add_argument('--pending-only',action='store_true');args=parser.parse_args();run(args.only,args.pending_only)
