"""连续视频语义＋可见手腕局部的离线握法候选；只复用冻结模型，不自动发布。"""
import argparse,hashlib,json,sys
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import joblib,numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits
from fitness_app.visual_recognition import VisualRecognizer
from fitness_app.grip_features import local_patches,summarize,select_pose_frames,VERSION
from fitness_app.research_extract import atomic_json
from review_curl_training_inputs import decode,sha
from train_curl_subtype import vector
from fitness_app.action_label_policy import group_balanced_weights

OUT=ROOT/'data/verification/round3'
OLD=ROOT/'data/verification/step-22'

def read(path):return json.loads(path.read_text(encoding='utf-8'))

def local_vector(entry,semantic,pose,vision,sampling,refresh):
    identity={'source_sha256':entry['source_sha256'],'start_ms':entry['start_ms'],'end_ms':entry['end_ms'],'crop':entry.get('crop'),
        'semantic_cache_sha256':sha(semantic),'pose_cache_sha256':sha(pose),'grip_policy':VERSION,'grip_code_sha256':sha(ROOT/'fitness_app/grip_features.py'),
        'media_code_sha256':sha(ROOT/'fitness_app/media.py'),'visual_code_sha256':sha(ROOT/'fitness_app/visual_recognition.py'),
        'backbone_revision':vision.version,'backbone_manifest_sha256':sha(ROOT/'data/models/clip-vit-base-patch32/manifest.json'),'sampling':sampling}
    signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();target=OUT/(entry['id']+'-grip-local.npz')
    if target.exists():
        with np.load(target,allow_pickle=False) as a:
            cached=json.loads(str(a['identity']));old_signature=hashlib.sha256(json.dumps(cached,sort_keys=True).encode()).hexdigest()
            if str(a['signature'])!=old_signature:raise ValueError('已有局部缓存自身签名损坏')
            if old_signature==signature:return a['summary'].copy() if bool(a['available']) else None,json.loads(str(a['observations'])),0
            fixed_keys=set(identity)-{'grip_policy','grip_code_sha256','sampling'}
            if not refresh or any(cached.get(k)!=identity[k] for k in fixed_keys):raise ValueError('局部缓存数据身份改变，或未明确刷新采样策略')
    with np.load(semantic,allow_pickle=False) as a:desired=a['timestamps'].tolist()
    with np.load(pose,allow_pickle=False) as a:times=a['timestamps'].copy();points=a['points'].copy();people=a['people'].copy();sizes=a['sizes'].copy()
    if sampling=='visible_pose':desired=select_pose_frames({'timestamps':times,'points':points,'people':people,'sizes':sizes},desired[0],desired[-1])
    vectors=[];observed=[]
    for stamp,rgb in decode(entry,desired) if len(desired)>=3 else []:
        if entry.get('crop'):
            h,w=rgb.shape[:2];l,t,r,b=entry['crop'];rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
        index=int(np.argmin(abs(times-stamp)))
        if abs(int(times[index])-stamp)>100 or people[index]!=1:continue
        for item in local_patches(rgb,points[index]):
            vectors.append(vision.embed(item['rgb']));observed.append({'timestamp_ms':stamp,'side':item['side'],'box':item['box'],'elbow_angle':item['elbow_angle']})
    valid_times=sorted({v['timestamp_ms'] for v in observed});available=len(valid_times)>=3 and valid_times[-1]-valid_times[0]>=400
    summary=summarize(vectors) if available else np.empty((0,),np.float32)
    partial=target.with_suffix('.npz.part');owned=False
    try:
        with partial.open('xb') as f:owned=True;np.savez_compressed(f,signature=signature,identity=json.dumps(identity,sort_keys=True),available=available,summary=summary,observations=json.dumps(observed,sort_keys=True))
        partial.replace(target)
    finally:
        if owned and partial.exists():partial.unlink()
    return summary if available else None,observed,len(vectors)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--sampling',choices=['uniform','visible_pose'],default='uniform');parser.add_argument('--weighting',choices=['source','group'],default='source');parser.add_argument('--refresh-local',action='store_true');args=parser.parse_args()
    report_path=OUT/'curl-subtype-training.json';baseline=read(report_path);plan=read(OUT/'recognition-probe-inputs.json');probe=read(OUT/'new-source-action-probe.json')
    old={r['id']:r for r in read(OLD/'semantic-video-features.json')['entries']};new={r['id']:r for r in plan['entries']};probe_by={r['id']:r for r in probe['rows']}
    selected=[{**r,'split':'train','expected':r['leaf_label']} for r in baseline['training']]+[{**r,'split':'evaluation'} for r in baseline['evaluation']]
    train_groups={r['group'] for r in selected if r['split']=='train'};evaluation_groups={r['group'] for r in selected if r['split']=='evaluation'}
    if train_groups&evaluation_groups:raise ValueError('训练与诊断/验证作者组交叉')
    production={p:sha(ROOT/'data/models'/p) for p in ('semantic-action-classifier.json','semantic-action-classifier.joblib')}
    vision=VisualRecognizer();consumed=[];new_crops=0;verified={}
    for index,row in enumerate(selected,1):
        entry=old.get(row['id']) or new[row['id']]
        if row['split']=='train' and (entry.get('evaluation_only') or entry['partition']!='train'):raise ValueError('禁止将诊断来源或验证输入回灌训练')
        path=project_path(entry['path'])
        if entry['path'] not in verified:verified[entry['path']]=sha(path)
        if verified[entry['path']]!=entry['source_sha256']:raise ValueError('原视频摘要变化')
        semantic=project_path(entry['cache']) if row['id'] in old else project_path(probe_by[row['id']]['semantic_cache'])
        pose=OLD/(row['id']+'-full-world-features.npz') if row['id'] in old else project_path(probe_by[row['id']]['pose_cache'])
        vector(semantic,entry)
        local,observations,calls=local_vector(entry,semantic,pose,vision,args.sampling,args.refresh_local);new_crops+=calls
        with np.load(semantic,allow_pickle=False) as a:global_vector=a['embedding'].astype(np.float32)
        if global_vector.shape!=(2560,) or not np.isfinite(global_vector).all() or np.linalg.norm(global_vector)<1e-8:raise ValueError('连续语义特征无效')
        feature=np.concatenate((global_vector/np.linalg.norm(global_vector),local)) if local is not None else None
        consumed.append({**row,'feature':feature,'local_available':local is not None,'crops':len(observations),'distinct_frames':len({v['timestamp_ms'] for v in observations})})
        print(json.dumps({'input':index,'total':len(selected),'id':row['id'],'local_available':local is not None,'new_cpu_crops':calls}),flush=True)
    training=[r for r in consumed if r['split']=='train' and r['local_available']];evaluation=[r for r in consumed if r['split']=='evaluation']
    counts=Counter((r['expected'],r['source_sha256']) for r in training);originals=Counter(a for a,_ in counts)
    if len(originals)!=2:raise ValueError('可用局部训练缺少一个握法子类')
    weights=np.array([1/(counts[(r['expected'],r['source_sha256'])]*originals[r['expected']]) for r in training]);weights*=len(weights)/weights.sum()
    if args.weighting=='group':weights=np.asarray(group_balanced_weights(training))
    with threadpool_limits(limits=4):
        model=make_pipeline(StandardScaler(),LogisticRegression(C=.3,max_iter=2000,random_state=20261024))
        model.fit(np.asarray([r['feature'] for r in training]),[r['expected'] for r in training],logisticregression__sample_weight=weights)
    results=[]
    for r in evaluation:
        value={k:r[k] for k in ('id','expected','group','source_sha256','local_available','crops','distinct_frames')}
        if r['feature'] is None:value.update(top=None,accepted=None,score=None,margin=None,reason='local_hand_evidence_insufficient')
        else:
            scores=model.predict_proba(r['feature'][None])[0];order=np.argsort(scores)[::-1];score=float(scores[order[0]]);margin=score-float(scores[order[1]]);top=str(model.classes_[order[0]])
            value.update(top=top,accepted=top if score>=.8 and margin>=.25 else None,score=score,margin=margin,reason=None)
        results.append(value)
    per_class={}
    for action in model.classes_:
        cases=[r for r in results if r['expected']==action];accepted=[r for r in results if r['accepted']==action]
        per_class[str(action)]={'train_clips':sum(r['expected']==action for r in training),'train_groups':len({r['group'] for r in training if r['expected']==action}),
            'evaluation_clips':len(cases),'evaluation_groups':len({r['group'] for r in cases}),'recall_including_rejections':sum(r['accepted']==action for r in cases)/len(cases) if cases else None,
            'accepted_precision':sum(r['expected']==action for r in accepted)/len(accepted) if accepted else None}
    gate=all(v['train_groups']>=2 and v['evaluation_clips']>=3 and v['evaluation_groups']>=2 and (v['recall_including_rejections'] or 0)>=.8 and (v['accepted_precision'] or 0)>=.8 for v in per_class.values())
    target=OUT/'curl-local-candidate.joblib';partial=target.with_suffix('.joblib.part');owned=False
    try:
        with partial.open('xb') as f:owned=True;joblib.dump(model,f)
        partial.replace(target)
    finally:
        if owned and partial.exists():partial.unlink()
    if any(sha(ROOT/'data/models'/name)!=digest for name,digest in production.items()):raise ValueError('实验意外更改生产模型')
    previous=read(OUT/'curl-local-training.json') if (OUT/'curl-local-training.json').exists() else None
    extraction=previous.get('initial_feature_extraction',{'new_clip_cpu_crops':previous['new_clip_cpu_crops']}) if previous else {'new_clip_cpu_crops':new_crops}
    uniform_baseline=previous.get('uniform_baseline') if previous else None
    if previous and uniform_baseline is None and previous.get('sampling','uniform')=='uniform':uniform_baseline={k:previous[k] for k in ('per_class','evaluation','candidate_sha256','initial_feature_extraction')}
    extraction_runs=dict(previous.get('sampling_feature_extractions',{'uniform':extraction['new_clip_cpu_crops']})) if previous else {}
    if new_crops:extraction_runs[args.sampling]=extraction_runs.get(args.sampling,0)+new_crops
    guided_baseline=previous.get('source_weighted_guided_baseline') if previous else None
    if previous and guided_baseline is None and previous.get('sampling')=='visible_pose' and previous.get('weighting','source')=='source':guided_baseline={k:previous[k] for k in ('per_class','evaluation','candidate_sha256')}
    value={'schema_version':1,'policy':VERSION,'scope':'continuous video semantics plus visible wrist-region reference features; diagnostic, not independent full33 or posture truth',
        'fixed_parameters':{'C':.3,'minimum_score':.8,'minimum_margin':.25},'sampling':args.sampling,'weighting':args.weighting,'uniform_baseline':uniform_baseline,'source_weighted_guided_baseline':guided_baseline,'sampling_feature_extractions':extraction_runs,'per_class':per_class,'evaluation':results,
        'training':[{k:r[k] for k in ('id','expected','group','source_sha256','local_available','crops','distinct_frames')} for r in consumed if r['split']=='train'],
        'new_clip_cpu_crops':new_crops,'initial_feature_extraction':extraction,'new_pose_inferences':0,'new_semantic_gpu_inferences':0,'candidate_sha256':sha(target),'candidate_path':target.relative_to(ROOT).as_posix(),
        'base_subtype_report_sha256':sha(report_path),'script_sha256':sha(Path(__file__)),'production_unchanged':True,'development_gate_passed':gate,'runtime_enabled':False,'specific_error_validation_passed':False}
    atomic_json(OUT/'curl-local-training.json',value);print(json.dumps({'per_class':per_class,'development_gate_passed':gate,'runtime_enabled':False,'new_clip_cpu_crops':new_crops}),flush=True)

if __name__=='__main__':main()
