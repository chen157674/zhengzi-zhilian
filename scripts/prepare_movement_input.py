"""14：显式登记的开发正例／反例只提取一次，缓存真实姿态供适配诊断复用。"""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import sys
from importlib.metadata import version

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
import av
import numpy as np
from fitness_app.pose import PoseService,DEFAULT_MODEL
from fitness_app.frame_pipeline import framing,usable_point
from fitness_app.sessions import IdentityContinuity

DIRECTORY=ROOT/'data/verification/step-14'
MANIFEST=ROOT/'data/datasets/development-movements/sources.json'


def prepare(source_id):
    source=next(item for item in json.loads(MANIFEST.read_text(encoding='utf-8'))['sources'] if item['id']==source_id)
    if source['role'] not in {'development_candidate','development_counterexample'}:raise ValueError('只提取已登记用途的开发正例或反例')
    path=project_path(source['path']);stat=path.stat()
    if stat.st_size!=source['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('开发输入身份不符')
    identity={'source_id':source_id,'source_sha256':source['sha256'],'source_bytes':stat.st_size,'source_modified_ns':stat.st_mtime_ns,
        'model_sha256':json.loads(DEFAULT_MODEL.with_name('manifest.json').read_text(encoding='utf-8'))['sha256'],
        'mediapipe':version('mediapipe'),'av':version('av'),'pose_code':hashlib.sha256(inspect.getsource(PoseService).encode()).hexdigest(),
        'identity_code':hashlib.sha256(inspect.getsource(IdentityContinuity).encode()).hexdigest(),'schema_version':1}
    output=project_path(DIRECTORY/f'{source_id}-poses.npz')
    if output.exists():
        with np.load(output,allow_pickle=False) as cached:
            if json.loads(str(cached['metadata']))!=identity:raise ValueError('姿态缓存身份改变，请显式核查后再重提取')
            return {'cache_hit':True,'frames':len(cached['timestamps_ms']),'path':str(output)}
    stamps=[];points=[];sizes=[];counts=[];accepted=[];continuity=IdentityContinuity()
    with av.open(str(path)) as container,PoseService(video=True) as pose:
        stream=container.streams.video[0]
        if stream.codec_context.name!='h264':raise ValueError('此开发输入必须与公共 H.264 视频路径相符')
        for seq,decoded in enumerate(container.decode(stream)):
            stamp=round(float(decoded.pts*decoded.time_base)*1000)
            if stamps and stamp<=stamps[-1]:raise ValueError('开发视频源时间不递增')
            result=pose.detect(np.ascontiguousarray(decoded.to_ndarray(format='rgb24')),stamp)
            quality=framing(source['action_id'],result)
            frame={'framing':quality,'width':decoded.width,'height':decoded.height}
            count=len(result['poses']);array=np.full((33,5),np.nan,dtype=np.float32)
            if count==1:
                for index,point in enumerate(result['poses'][0]):array[index]=[point[key] for key in ('x','y','z','visibility','presence')]
            stamps.append(stamp);points.append(array);sizes.append((decoded.width,decoded.height));counts.append(count)
            accepted.append(count==1 and continuity.accept(frame))
    output.parent.mkdir(exist_ok=True)
    np.savez_compressed(output,metadata=json.dumps(identity,sort_keys=True),timestamps_ms=np.array(stamps),points=np.array(points),
        sizes=np.array(sizes),person_counts=np.array(counts),identity_accepted=np.array(accepted))
    return {'cache_hit':False,'frames':len(stamps),'path':str(output),'bytes':output.stat().st_size}


def cached_frames(source_id,apply_bounds=True):
    source=next(item for item in json.loads(MANIFEST.read_text(encoding='utf-8'))['sources'] if item['id']==source_id)
    with np.load(project_path(DIRECTORY/f'{source_id}-poses.npz'),allow_pickle=False) as cache:
        meta=json.loads(str(cache['metadata']));stat=project_path(source['path']).stat()
        if (meta['source_sha256'],meta['source_bytes'],meta['source_modified_ns'])!=(source['sha256'],stat.st_size,stat.st_mtime_ns):raise ValueError('缓存与登记原片不匹配')
        frames=[]
        for seq,stamp in enumerate(cache['timestamps_ms']):
            count=int(cache['person_counts'][seq]);points=[]
            if count==1:
                for raw in cache['points'][seq]:
                    point=dict(zip(('x','y','z','visibility','presence'),map(float,raw)));point['valid']=usable_point(point);points.append(point)
            frames.append({'frame_seq':seq,'captured_ms':int(stamp),'width':int(cache['sizes'][seq,0]),'height':int(cache['sizes'][seq,1]),
                'identity_accepted':bool(cache['identity_accepted'][seq]),'framing':{'person_count':count,'landmarks':points}})
        if apply_bounds:
            from development_content_bounds import apply_verified_bounds
            return apply_verified_bounds(frames,source['action_id'],source['path'],source['sha256'])
        return frames


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source_id');args=parser.parse_args()
    print(json.dumps(prepare(args.source_id),ensure_ascii=False))
