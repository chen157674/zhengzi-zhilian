"""冻结候选后的独立发布者保留集验证；标签仅用于评分，不进入模型输入。"""
import hashlib,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av,numpy as np
from fitness_app.gym_pose import GymPoseService
from fitness_app.media import display_rgb
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.video_vlm import CONFIG
from fitness_app.research_extract import atomic_json
from train_semantic_fusion import metrics_for_predictions,OUT
from extract_semantic_video import seek_safe_frames


def pose_context(entry):
    center=(entry['start_ms']+entry['end_ms'])/2;span=min(6400,entry['end_ms']-entry['start_ms']);first,last=center-span/2,center+span/2
    arrays={k:[] for k in ('timestamps','points','world','people','sizes')};next_stamp=first
    with GymPoseService(video=True) as engine,av.open(str(project_path(entry['path']))) as video:
        # 保留集采用顺序解码；不依赖来源MP4关键帧索引定位。
        for frame in video.decode(video=0):
            if frame.time is None:continue
            stamp=round(float(frame.time)*1000)
            if stamp<next_stamp:continue
            if stamp>=last:break
            rgb=display_rgb(frame)
            if entry['crop']:
                h,w=rgb.shape[:2];l,t,r,b=entry['crop'];rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
            result=engine.detect(rgb,stamp);points=world=np.full((33,5),np.nan,np.float32)
            if len(result['poses'])==1:
                points=np.asarray([[p[k] for k in ('x','y','z','visibility','presence')] for p in result['poses'][0]],np.float32)
                if len(result.get('world_poses',[]))==1:world=np.asarray([[p[k] for k in ('x','y','z','visibility','presence')] for p in result['world_poses'][0]],np.float32)
            for key,value in dict(timestamps=stamp,points=points,world=world,people=len(result['poses']),sizes=(rgb.shape[1],rgb.shape[0])).items():arrays[key].append(value)
            while next_stamp<=stamp:next_stamp+=100
    return {k:np.asarray(v) for k,v in arrays.items()}


def run():
    source_rows=json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources'];by={r['id']:r for r in source_rows}
    segments=json.loads((ROOT/'data/datasets/development-movements/gym-segments.json').read_text(encoding='utf-8'))['segments']
    entries=[{**r,'path':by[r['source_id']]['path']} for r in segments if r['partition']=='holdout']
    development=json.loads((OUT/'semantic-video-features.json').read_text(encoding='utf-8'))
    used_hashes={r['source_sha256'] for r in development['entries']};used_groups={r['group'] for r in development['entries']}
    if any(r['source_sha256'] in used_hashes or r['group'] in used_groups for r in entries):raise ValueError('保留集来源与开发数据交叉')
    model=SemanticPredictor(research=True)
    if not model.info.get('basic_development_gate_passed'):raise ValueError('先冻结满足基础开发门槛的候选')
    policy={'schema':'semantic-heldout-1','version':model.info['version'],'head_sha256':model.info['sha256'],'encoder_policy_sha256':model.info['encoder_policy_sha256'],
            'refinement_families':model.info.get('refinement_families',[]),'refinement_require_primary':model.info.get('refinement_require_primary'),
            'code':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                    ('fitness_app/semantic_model.py','fitness_app/semantic_features.py','fitness_app/world_features.py','fitness_app/gym_pose.py','fitness_app/pose.py','fitness_app/video_refinement.py','fitness_app/compact_video.py','scripts/evaluate_semantic_holdout.py')}}
    rows=[];predictions=[];latencies=[]
    for entry in entries:
        if hashlib.sha256(project_path(entry['path']).read_bytes()).hexdigest()!=entry['source_sha256']:raise ValueError('保留原片摘要改变')
        identity={'policy':policy,'source_sha256':entry['source_sha256'],'start_ms':entry['start_ms'],'end_ms':entry['end_ms'],'crop':entry['crop']}
        signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();path=OUT/(entry['id']+'-heldout-'+signature[:10]+'.json')
        if path.exists():
            row=json.loads(path.read_text(encoding='utf-8'))
            if row['signature']!=signature:raise ValueError('保留集缓存身份不符')
        else:
            images,times=seek_safe_frames(entry);arrays=pose_context(entry);began=time.perf_counter()
            output=model.predict(images,times,arrays)
            row={'signature':signature,'entry':entry,'sampled_timestamps_ms':times,'prediction':output,
                 'classifier_total_ms':round((time.perf_counter()-began)*1000,1),'single_person_pose_fraction':float(np.mean(arrays['people']==1))}
            atomic_json(path,row)
        rows.append(row);out=row['prediction'];latencies.append(row['classifier_total_ms'])
        predictions.append({'id':entry['id'],'source_sha256':entry['source_sha256'],'group':entry['group'],'expected':entry['action_id'],
                            'top':out['top_action_id'] or 'unknown','predicted':out['action_id'],'score':out['score'],'margin':out['margin']})
        print(json.dumps({'id':entry['id'],'expected':entry['action_id'],'predicted':out['action_id'],'ms':row['classifier_total_ms']}),flush=True)
    metrics=metrics_for_predictions(predictions)
    report={'schema_version':1,'policy':policy,'rows':rows,'metrics':metrics,'runtime_enabled':False,'clips':len(entries),
            'scope':'Unseen publisher groups, clip action labels only; 8 actions from two publisher groups, not all33-class or posture-error independent accuracy. No threshold fitting on these outputs.',
            'latency_includes_first_load':True,'classifier_latency_ms':{'p50':float(np.median(latencies)),'p95':float(np.percentile(latencies,95))}}
    atomic_json(OUT/'semantic-holdout.json',report)
    print(json.dumps({'clips':len(entries),'accuracy':metrics['accuracy_including_rejections'],'accepted_precision':metrics['accepted_precision'],'coverage':metrics['coverage']}),flush=True)


if __name__=='__main__':run()
