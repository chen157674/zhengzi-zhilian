"""冻结本地视频语义编码器，缓存连续视频/可见骨架语义，供项目分类头训练复用。"""
import argparse,hashlib,json,os,sys,time,inspect
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from fitness_app.video_vlm import VideoActionVLM,CONFIG,prompt
from fitness_app.research_extract import atomic_json
from evaluate_xclip import sources,OUT
from evaluate_video_vlm import frames,guided_input,pose_cache_path
import av

# 已逐片审核757个完成缓存的首帧误差；只有此原片的分片MP4索引会向前跳关键帧。
# 原站SHA原样保存，不能依据动作标签选择修复。
FORWARD_SEEK_SOURCES={'3715fe200094e768866254f132a9245ceaf6cde522fed8a801a603fb6626f7b3'}


def seek_safe_frames(entry):
    span=min(3200,entry['end_ms']-entry['start_ms']);center=(entry['start_ms']+entry['end_ms'])/2
    desired=np.linspace(center-span/2,center+span/2-100,CONFIG['frames']);images=[];times=[]
    with av.open(str(project_path(entry['path']))) as video:
        # 从真实第0帧顺序解码，不依赖该原片错误的关键帧索引。
        for frame in video.decode(video=0):
            if frame.time is None:continue
            stamp=round(float(frame.time)*1000)
            if stamp<desired[len(images)]:continue
            rgb=frame.to_ndarray(format='rgb24')
            if entry['crop']:
                h,w=rgb.shape[:2];l,t,r,b=entry['crop'];rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
            images.append(rgb);times.append(stamp)
            if len(images)==CONFIG['frames']:return images,times
    raise ValueError('修复定位后仍不足真实8帧')


def run(partition=None,limit=None):
    model=VideoActionVLM('4b',quantized=True);entries=sources(include_legacy=True)
    policy={'schema':'semantic-video-fitness-1','base_revision':model.info['revision'],'base_repo':model.info['repo'],
            'video_config':CONFIG,'prompt':prompt(),'quantization':'NF4 double quant BF16 compute; visual/lm_head excluded',
            'embedding':'last language decoder RMSNorm state at classification query; no ground-truth token in input',
            'code':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('fitness_app/video_vlm.py','fitness_app/recognition_observations.py','scripts/evaluate_video_vlm.py')}}
    policy_sha=hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest();results=[];began=time.perf_counter();new=0;reused=0;verified={}
    for entry in entries:
        if entry['partition'] not in {'train','validation'} or partition and entry['partition']!=partition:continue
        if limit is not None and len(results)>=limit:break
        source_path=project_path(entry['path'])
        if source_path not in verified:verified[source_path]=hashlib.sha256(source_path.read_bytes()).hexdigest()
        if verified[source_path]!=entry['source_sha256']:raise ValueError('视频语义编码的原片摘要改变')
        pose_path=pose_cache_path(entry)
        if pose_path is None:raise ValueError('需先完成真实姿态缓存: '+entry['id'])
        identity={'policy_sha256':policy_sha,'source_sha256':entry['source_sha256'],'start_ms':entry['start_ms'],'end_ms':entry['end_ms'],
                  'crop':entry['crop'],'pose_sha256':hashlib.sha256(pose_path.read_bytes()).hexdigest()}
        repaired=entry['source_sha256'] in FORWARD_SEEK_SOURCES
        if repaired:identity['sampling_repair']={'schema':'sequential-decode-from-zero-1','helper_sha256':hashlib.sha256(inspect.getsource(seek_safe_frames).encode()).hexdigest()}
        signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        target=OUT/(entry['id']+'-semantic-'+policy_sha[:10]+('-'+signature[:10] if repaired else '')+'.npz')
        if target.exists():
            with np.load(target,allow_pickle=False) as cached:
                if str(cached['signature'])!=signature:raise ValueError('视频语义特征输入身份不符')
                size=int(cached['embedding'].size)
            reused+=1
        else:
            images,times=(seek_safe_frames(entry) if repaired else frames(entry));observed,detail=guided_input(entry,images,times)
            prediction=model.predict(images,times,observed,detail,return_embedding=True);embedding=prediction.pop('_embedding')
            if embedding.ndim!=1 or not np.isfinite(embedding).all() or np.linalg.norm(embedding)<1e-8:raise ValueError('视频语义特征无效')
            size=int(embedding.size);partial=target.with_suffix('.npz.part');owned=False
            try:
                with partial.open('xb') as output:
                    owned=True;np.savez_compressed(output,signature=signature,identity=json.dumps(identity,sort_keys=True),embedding=embedding,
                                                  prediction=json.dumps(prediction,sort_keys=True),timestamps=times)
                os.replace(partial,target)
            finally:
                if owned and partial.exists():partial.unlink()
            new+=1
        results.append({**entry,'cache':target.relative_to(ROOT).as_posix(),'feature_signature':signature,'embedding_dimensions':size})
        if len(results)%10==0:print(json.dumps({'ready':len(results),'partition':entry['partition'],'new':new,'reused':reused,'seconds':round(time.perf_counter()-began,1),'last':entry['id']}),flush=True)
        # 每50片保存一次可恢复索引，实际缓存逐片原子保存；中断不丢失已完成推理。
        if len(results)%50==0:atomic_json(OUT/'semantic-video-features.json',{'schema_version':1,'policy':policy,'policy_sha256':policy_sha,'status':'extracting','entries':results,'new_inferences':new,'reused':reused})
    if partition or limit:
        previous=OUT/'semantic-video-features.json'
        if previous.exists():
            old=json.loads(previous.read_text(encoding='utf-8'))
            if old['policy_sha256']==policy_sha:
                keys={r['id'] for r in results};results=[r for r in old['entries'] if r['id'] not in keys]+results
    atomic_json(OUT/'semantic-video-features.json',{'schema_version':1,'policy':policy,'policy_sha256':policy_sha,'status':'completed' if not partition and limit is None else 'partial',
                                                 'entries':results,'new_inferences':new,'reused':reused,'elapsed_seconds':round(time.perf_counter()-began,1),'test_used':False})
    print(json.dumps({'ready':len(results),'new':new,'reused':reused,'seconds':round(time.perf_counter()-began,1)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--partition');parser.add_argument('--limit',type=int);args=parser.parse_args();run(args.partition,args.limit)
