"""仅提取冻结新来源的真实8帧视频/Full姿态；复用缓存，不更改运行权重。"""
import argparse,hashlib,json,os,re,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av,numpy as np
from fitness_app.gym_pose import GymPoseService
from fitness_app.pose_input import input_policy_snapshot
from fitness_app.media import display_rgb
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.recognition_observations import observations
from fitness_app.video_refinement import VideoFamilyRefiner,choose_family,combine,policy_version as family_policy
from fitness_app.research_extract import atomic_json

OUT=ROOT/'data/verification/round3'
PLAN=OUT/'recognition-probe-inputs.json'


def validate_plan(plan):
    entries=plan.get('entries')
    if not isinstance(entries,list) or not entries or len(entries)>200:raise ValueError('探针输入清单无效')
    from fitness_app.video_vlm import LABELS
    known={a or 'unknown' for a in LABELS};ids=set()
    for entry in entries:
        key=entry.get('id')
        if not isinstance(key,str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}',key) is None or key in ids:raise ValueError('探针输入标识非法或重复')
        ids.add(key)
        if entry.get('action_id') not in known or entry.get('partition') not in {'train','validation','diagnostic'} or not isinstance(entry.get('group'),str) or not entry['group']:raise ValueError('探针类别或分区无效')
        if entry['partition']=='train' and (entry.get('evaluation_only') or entry.get('exclude_from_training')):raise ValueError('评估专用或排除来源不能进入训练计划')
        source_sha=entry.get('source_sha256')
        if not isinstance(source_sha,str) or re.fullmatch(r'[0-9a-f]{64}',source_sha) is None:raise ValueError('探针原片摘要无效')
        start,end=entry.get('start_ms'),entry.get('end_ms')
        if type(start) is not int or type(end) is not int or not 0<=start<end or end-start<1200:raise ValueError('探针时间窗无效')
        crop=entry.get('crop')
        if crop is not None and (not isinstance(crop,list) or len(crop)!=4 or any(type(v) not in (int,float) or not np.isfinite(v) for v in crop) or not 0<=crop[0]<crop[2]<=1 or not 0<=crop[1]<crop[3]<=1):raise ValueError('探针裁剪范围无效')
        project_path(entry['path'])
    from fitness_app.source_partitions import enforce_source_policy
    enforce_source_policy(entries)
    enforce_source_policy(entries,operation='inference')


def validate_cached_row(entry,row):
    if any(row.get(k)!=entry[k] for k in ('id','source_sha256','partition','group')) or row.get('expected')!=entry['action_id']:
        raise ValueError('缓存输出的标签/分区/来源与冻结计划不同，拒绝沿用旧结果')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def arrays_cache(entry):
    path=project_path(entry['path'])
    if digest(path)!=entry['source_sha256']:raise ValueError('新来源原片摘要改变')
    identity={'source_sha256':entry['source_sha256'],'start_ms':entry['start_ms'],'end_ms':entry['end_ms'],'crop':entry['crop'],
        'pose':input_policy_snapshot(GymPoseService),'sampling':'middle6.4s_10hz_sequential_decode_display_rgb_v1',
        'display_code':digest(ROOT/'fitness_app/media.py')}
    signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();target=OUT/(entry['id']+'-probe-pose.npz')
    if target.exists():
        with np.load(target,allow_pickle=False) as a:
            if str(a['signature'])!=signature:raise ValueError('新来源姿态缓存身份改变，不覆盖')
            return {k:a[k].copy() for k in ('timestamps','points','world','people','sizes')},target,0
    center=(entry['start_ms']+entry['end_ms'])/2;span=min(6400,entry['end_ms']-entry['start_ms']);start=round(center-span/2);end=round(center+span/2)
    values={k:[] for k in ('timestamps','points','world','people','sizes')};next_stamp=start;calls=0
    with GymPoseService(video=True) as pose,av.open(str(path)) as video:
        for f in video.decode(video=0):
            if f.time is None:continue
            stamp=round(float(f.time)*1000)
            if stamp<next_stamp:continue
            if stamp>=end:break
            rgb=display_rgb(f)
            if entry['crop']:
                h,w=rgb.shape[:2];l,t,r,b=entry['crop'];rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
            result=pose.detect(rgb,stamp);calls+=1;points=np.full((33,5),np.nan,np.float32);world=points.copy()
            if len(result['poses'])==1:
                points=np.asarray([[p[k] for k in ('x','y','z','visibility','presence')] for p in result['poses'][0]],np.float32)
                if len(result.get('world_poses',[]))==1:world=np.asarray([[p[k] for k in ('x','y','z','visibility','presence')] for p in result['world_poses'][0]],np.float32)
            for key,value in dict(timestamps=stamp,points=points,world=world,people=len(result['poses']),sizes=(rgb.shape[1],rgb.shape[0])).items():values[key].append(value)
            while next_stamp<=stamp:next_stamp+=100
    arrays={k:np.asarray(v) for k,v in values.items()};partial=target.with_suffix('.npz.part');owned=False
    try:
        with partial.open('xb') as f:owned=True;np.savez_compressed(f,signature=signature,identity=json.dumps(identity,sort_keys=True),**arrays)
        partial.replace(target)
    finally:
        if owned and partial.exists():partial.unlink()
    return arrays,target,calls


def images(entry):
    center=(entry['start_ms']+entry['end_ms'])/2;span=min(3200,entry['end_ms']-entry['start_ms']);desired=np.linspace(center-span/2,center+span/2-100,8);frames=[];times=[]
    with av.open(str(project_path(entry['path']))) as video:
        for f in video.decode(video=0):
            if f.time is None:continue
            stamp=round(float(f.time)*1000)
            if stamp<desired[len(frames)]:continue
            if stamp-desired[len(frames)]>100:raise ValueError('采样向未来偏移，不接纳错误关键帧')
            rgb=display_rgb(f)
            if entry['crop']:
                h,w=rgb.shape[:2];l,t,r,b=entry['crop'];rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
            frames.append(rgb);times.append(stamp)
            if len(frames)==8:return frames,times
    raise ValueError('不足8个真实时序画面')


def main(plan_path=PLAN,report_path=OUT/'new-source-action-probe.json'):
    plan_path=project_path(plan_path);report_path=project_path(report_path)
    if plan_path.parent!=OUT or report_path.parent!=OUT or plan_path.suffix!='.json' or report_path.suffix!='.json' or plan_path==report_path:raise ValueError('探针计划和报告必须位于原round3目录，且不能覆盖同一文件')
    plan=json.loads(plan_path.read_text(encoding='utf-8'));validate_plan(plan)
    if report_path.exists() and json.loads(report_path.read_text(encoding='utf-8')).get('plan_sha256')!=digest(plan_path):raise ValueError('目标报告属于其他冻结计划，不覆盖')
    import torch
    torch.set_num_threads(4)
    model=SemanticPredictor();rows=[];new_pose=0;new_semantic=0;new_review=0
    runtime_before=digest(ROOT/'data/models/semantic-action-classifier.json');weight_before=model.info['sha256'];refiner=None
    for index,entry in enumerate(plan['entries'],1):
        arrays,pose_path,pose_calls=arrays_cache(entry);new_pose+=pose_calls
        identity={'source_sha256':entry['source_sha256'],'start_ms':entry['start_ms'],'end_ms':entry['end_ms'],'crop':entry['crop'],
            'pose_sha256':digest(pose_path),'encoder_policy_sha256':model.info['encoder_policy_sha256'],'sampling':'actual8_middle3.2s_sequential_display_rgb_v1','display_code':digest(ROOT/'fitness_app/media.py')}
        signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();cache=OUT/(entry['id']+'-probe-semantic.npz')
        frames=times=facts=detail=None;began=time.perf_counter()
        if cache.exists():
            with np.load(cache,allow_pickle=False) as a:
                if str(a['signature'])!=signature:raise ValueError('视频语义缓存身份改变，不覆盖')
                embedding=a['embedding'].copy();visual=json.loads(str(a['prediction']));timestamps=list(map(int,a['timestamps']))
        else:
            frames,timestamps=images(entry);facts,box=observations(arrays,timestamps[0],timestamps[-1]);detail=None
            if box:
                h,w=frames[4].shape[:2];l,t,r,b=box;detail=np.ascontiguousarray(frames[4][round(t*h):round(b*h),round(l*w):round(r*w)])
            visual=model._video().predict(frames,timestamps,facts,detail,return_embedding=True);embedding=visual.pop('_embedding');new_semantic+=1
            partial=cache.with_suffix('.npz.part');owned=False
            try:
                with partial.open('xb') as f:owned=True;np.savez_compressed(f,signature=signature,identity=json.dumps(identity,sort_keys=True),embedding=embedding,prediction=json.dumps(visual,sort_keys=True),timestamps=timestamps)
                partial.replace(cache)
            finally:
                if owned and partial.exists():partial.unlink()
        result_identity={'semantic_signature':signature,'head_sha256':model.info['sha256'],'runtime_version':model.info['version'],'family_policy':family_policy()}
        result_signature=hashlib.sha256(json.dumps(result_identity,sort_keys=True).encode()).hexdigest();result_path=OUT/(entry['id']+'-probe-result.json')
        if result_path.exists():
            row=json.loads(result_path.read_text(encoding='utf-8'))
            if row['signature']!=result_signature:raise ValueError('探针结果基准改变，不覆盖')
            validate_cached_row(entry,row)
        else:
            world,visibility=model.world.extract(arrays,timestamps[0],timestamps[-1]);primary=model.from_features(embedding,visual,world,visibility);deployed=dict(primary);review=None;curl_review=None
            family=choose_family(primary,visual);review_families=[]
            if family in model.info.get('refinement_families',[]) and primary['action_id'] is not None:review_families.append(family)
            if primary['action_id'] in ('standing_dumbbell_curl','hammer_curl'):review_families.append('curl')
            if review_families:
                if frames is None:frames,timestamps=images(entry)
                if facts is None:
                    facts,box=observations(arrays,timestamps[0],timestamps[-1]);detail=None
                    if box:
                        h,w=frames[4].shape[:2];l,t,r,b=box;detail=np.ascontiguousarray(frames[4][round(t*h):round(b*h),round(l*w):round(r*w)])
                if refiner is None:refiner=VideoFamilyRefiner(model._video())
                for selected in review_families:
                    result=refiner.review(frames,timestamps,selected,facts,detail);new_review+=1
                    if selected=='curl':curl_review=result
                    else:review=result;deployed=combine(primary,result,selected)
            deployed=model._with_motion(deployed,arrays,timestamps)
            row={'signature':result_signature,'id':entry['id'],'expected':entry['action_id'],'partition':entry['partition'],'group':entry['group'],'source_sha256':entry['source_sha256'],
                'semantic_cache':cache.relative_to(ROOT).as_posix(),'pose_cache':pose_path.relative_to(ROOT).as_posix(),'sampled_timestamps_ms':timestamps,'primary':primary,'deployed':deployed,
                'visual':visual,'review':review,'curl_review_candidate_not_enabled':curl_review,'elapsed_seconds':round(time.perf_counter()-began,3),'independent_specific_error_truth':False}
            atomic_json(result_path,row)
        rows.append(row)
        print(json.dumps({'ready':index,'total':len(plan['entries']),'id':entry['id'],'expected':row['expected'],'deployed':row['deployed']['action_id'],'curl_review':(row.get('curl_review_candidate_not_enabled') or {}).get('action_id'),'new_pose_calls':pose_calls,'seconds':row['elapsed_seconds']},ensure_ascii=False),flush=True)
        atomic_json(report_path,{'schema_version':1,'status':'running','scope':plan['scope'],'plan_sha256':digest(plan_path),'rows':rows,'new_pose_calls':new_pose,'new_semantic_inferences':new_semantic,'new_family_reviews':new_review,'runtime_modified':False})
        frames=facts=detail=None
    if runtime_before!=digest(ROOT/'data/models/semantic-action-classifier.json') or weight_before!=digest(ROOT/'data/models/semantic-action-classifier.joblib'):raise ValueError('运行模型身份意外改变')
    atomic_json(report_path,{'schema_version':1,'status':'completed','scope':plan['scope'],'plan_sha256':digest(plan_path),'rows':rows,'new_pose_calls':new_pose,'new_semantic_inferences':new_semantic,'new_family_reviews':new_review,'runtime_modified':False})
    print('New-source probe completed; production model unchanged.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--plan',default=str(PLAN));parser.add_argument('--report',default=str(OUT/'new-source-action-probe.json'))
    args=parser.parse_args();main(args.plan,args.report)
