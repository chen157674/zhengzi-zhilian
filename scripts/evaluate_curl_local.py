"""从已有真实缓存重放当前分类器和握法补充分支；不调用视觉模型、不发布权重。"""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import joblib,numpy as np
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.action_label_policy import route
from fitness_app.research_extract import atomic_json
from review_curl_training_inputs import sha
from train_curl_subtype import vector

OUT=ROOT/'data/verification/round3';OLD=ROOT/'data/verification/step-22'

def read(path):return json.loads(path.read_text(encoding='utf-8'))
def main():
    trained=read(OUT/'curl-local-training.json');candidate=project_path(trained['candidate_path'])
    if sha(candidate)!=trained['candidate_sha256']:raise ValueError('候选权重与训练记录不一致')
    model=joblib.load(candidate);primary_model=SemanticPredictor();old={r['id']:r for r in read(OLD/'semantic-video-features.json')['entries']};new={r['id']:r for r in read(OUT/'new-source-action-probe.json')['rows']}
    plan={r['id']:r for r in read(OUT/'recognition-probe-inputs.json')['entries']};rows=[]
    for case in trained['evaluation']:
        key=case['id'];source=old.get(key) or new[key];semantic=project_path(source['cache']) if key in old else project_path(source['semantic_cache'])
        pose=OLD/(key+'-full-world-features.npz') if key in old else project_path(source['pose_cache'])
        vector(semantic,old.get(key) or plan[key])
        with np.load(semantic,allow_pickle=False) as a:embedding=a['embedding'].copy();prediction=json.loads(str(a['prediction']));times=a['timestamps'].copy()
        with np.load(pose,allow_pickle=False) as a:arrays={k:a[k].copy() for k in ('timestamps','points','world','people','sizes')}
        world,visible=primary_model.world.extract(arrays,int(times[0]),int(times[-1]));primary=primary_model._with_motion(primary_model.from_features(embedding,prediction,world,visible),arrays,times)
        with np.load(OUT/(key+'-grip-local.npz'),allow_pickle=False) as a:local=a['summary'].copy() if bool(a['available']) else None
        if local is None:result=dict(primary)
        else:
            feature=np.concatenate((embedding/np.linalg.norm(embedding),local));scores=model.predict_proba(feature[None])[0];order=np.argsort(scores)[::-1];top=str(model.classes_[order[0]])
            result=route(primary,top,float(scores[order[0]]),float(scores[order[0]]-scores[order[1]]))
            if result!=primary:result.update(model_version='experimental-curl-local-'+trained['candidate_sha256'][:12],base_model_version=primary['model_version'])
        rows.append({'id':key,'expected':case['expected'],'group':case['group'],'source_sha256':case['source_sha256'],'primary':primary,'candidate':result,'local_available':local is not None})
    per_class={}
    for action in ('standing_dumbbell_curl','hammer_curl'):
        cases=[r for r in rows if r['expected']==action]
        per_class[action]={'clips':len(cases),'groups':len({r['group'] for r in cases}),'before_correct':sum(r['primary']['action_id']==action for r in cases),'after_correct':sum(r['candidate']['action_id']==action for r in cases)}
        per_class[action]['after_recall_including_rejections']=per_class[action]['after_correct']/len(cases) if cases else None
    before=sum(r['primary']['action_id']==r['expected'] for r in rows);after=sum(r['candidate']['action_id']==r['expected'] for r in rows)
    comparisons={}
    for name,key in [('uniform','uniform_baseline'),('visible_pose_source','source_weighted_guided_baseline')]:
        saved=trained.get(key)
        if not saved:continue
        old_cases={r['id']:r for r in saved['evaluation']}
        if set(old_cases)!={r['id'] for r in rows} or any(old_cases[r['id']]['source_sha256']!=r['source_sha256'] for r in rows):
            comparisons[name]={'comparable':False,'reason':'input identity set changed'};continue
        correct=0
        for r in rows:
            case=old_cases[r['id']];decision=route(r['primary'],case['top'],case['score'],case['margin']) if case['local_available'] else r['primary']
            correct+=decision['action_id']==r['expected']
        comparisons[name]={'comparable':True,'correct':correct,'clips':len(rows),'usable_training_inputs':sum(v['train_clips'] for v in saved['per_class'].values()),'source':'retained candidate predictions; no repeated visual inference'}
    comparisons[trained.get('sampling','uniform')+'_'+trained.get('weighting','source')]={'comparable':True,'correct':after,'clips':len(rows),'usable_training_inputs':sum(v['train_clips'] for v in trained['per_class'].values())}
    failures=[]
    for action,metrics in per_class.items():
        if metrics['after_recall_including_rejections'] is None or metrics['after_recall_including_rejections']<.8:failures.append(action+': recall below 80 percent')
        if metrics['clips']<3 or metrics['groups']<2:failures.append(action+': insufficient evaluation inputs/source groups')
    failures.append('this curl-only diagnostic replay does not establish full33 or independent posture-error quality')
    value={'schema_version':1,'scope':'seven previously observed development/diagnostic inputs with bounded subtype labels; not independent held-out full33 test',
        'rows':rows,'clips':len(rows),'before_correct':before,'after_correct':after,'per_class':per_class,'sampling_comparisons':comparisons,
        'candidate_report_sha256':sha(OUT/'curl-local-training.json'),'production_manifest_sha256':sha(ROOT/'data/models/semantic-action-classifier.json'),
        'production_weight_sha256':sha(ROOT/'data/models/semantic-action-classifier.joblib'),'script_sha256':sha(Path(__file__)),
        'gate_passed':not failures,'gate_failures':failures,
        'new_pose_inferences':0,'new_semantic_gpu_inferences':0,'new_clip_inferences':0,'runtime_enabled':False}
    atomic_json(OUT/'curl-local-replay.json',value);print(json.dumps({k:value[k] for k in ('clips','before_correct','after_correct','per_class','gate_passed','runtime_enabled')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
