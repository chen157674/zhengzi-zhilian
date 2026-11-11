"""参考动作识别及公开类别扩展训练；不加载原test，不重跑姿态模型。"""
import sys,json,hashlib
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
import joblib
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from fitness_app.automatic_recognition import CONFIG,MODEL,MANIFEST,window_features,decision,ACTIONS,prepare_frame
from fitness_app.research_data import load_dataset
from fitness_app.research_extract import atomic_json
from fitness_app.recognition_inputs import extra_inputs,extra_frames

REPORT=ROOT/'data/experiments/action-recognition.json'

def make_windows(frames,key,action):
    windows=[];times=np.asarray([f['captured_ms'] for f in frames])
    if not len(times):return windows
    for end in range(int(times[0])+2000,int(times[-1])+1,1000):
        subset=[frames[i] for i in np.where((times>=end-2000)&(times<=end))[0]]
        feature,reason=window_features(subset)
        windows.append({'key':key,'action':action,'end_ms':end,'feature':feature,'reason':reason})
    usable=sum(w['feature'] is not None for w in windows)
    for w in windows:w['weight']=1/max(1,usable)
    return windows

def run(allow_train=False):
    samples=[s for s in load_dataset() if s.partition in ('train','validation','development') and s.action_id in ACTIONS]
    def validation(s):return s.partition=='validation' or s.partition=='development' and s.validation_fold==3
    groups=lambda val:{(s.dataset_id,s.subject_group) for s in samples if validation(s)==val}
    if groups(True)&groups(False):raise ValueError('动作训练和验证人物交叉')
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'))
    identity={'config':CONFIG,'code':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
        ('fitness_app/automatic_recognition.py','scripts/train_action_recognition.py','fitness_app/upstream.py','fitness_app/frame_pipeline.py')},
        'split':hashlib.sha256((ROOT/'data/experiments/split.json').read_bytes()).hexdigest()}
    paths={}
    for s in samples:
        entry=baseline['extractions'][s.key];feature=json.loads((ROOT/'data/experiments/features'/f"{entry['feature_key']}.json").read_text(encoding='utf-8'))
        path=project_path(feature['pose_cache']);path.relative_to(ROOT/'data/experiments/poses');paths[s.key]=path
    identity['poses']={s.key:hashlib.sha256(paths[s.key].read_bytes()).hexdigest() for s in samples}
    extra=extra_inputs();identity['extra_inputs']=extra
    identity['code']['fitness_app/recognition_inputs.py']=hashlib.sha256((ROOT/'fitness_app/recognition_inputs.py').read_bytes()).hexdigest()
    signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    previous={}
    if REPORT.exists():
        previous=json.loads(REPORT.read_text(encoding='utf-8'))
        if previous.get('signature')==signature or signature in previous.get('equivalent_signatures',[]):
            print(json.dumps({'cache_hit':True,'metrics':previous['metrics']}));return
    if not allow_train:raise ValueError('没有匹配结果；仅显式 --train 才运行新训练')
    train=[];valid=[];rejected=Counter()
    for s in samples:
        with np.load(paths[s.key],allow_pickle=False) as cache:
            meta=json.loads(str(cache['metadata']));stat=s.absolute_path.stat()
            if meta['source']!={'source_sha256':s.sha256,'bytes':stat.st_size,'modified_ns':stat.st_mtime_ns}:raise ValueError('姿态缓存与来源不匹配')
            frames=[]
            for i,t in enumerate(cache['timestamps_ms']):
                points=[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in cache['points'][i]]
                frames.append(prepare_frame({'captured_ms':int(t),'width':int(cache['sizes'][i,0]),'height':int(cache['sizes'][i,1]),
                    'identity_accepted':bool(cache['identity_accepted'][i]),'framing':{'person_count':int(cache['person_counts'][i]),'landmarks':points}}))
        windows=make_windows(frames,s.key,s.action_id)
        rejected.update(w['reason'] for w in windows if w['reason'])
        (valid if validation(s) else train).extend(windows)
    for entry in extra:
        windows=make_windows(extra_frames(entry),entry['key'],entry['action'])
        rejected.update(w['reason'] for w in windows if w['reason'])
        (valid if entry['partition']=='validation' else train).extend(windows)
    usable=[w for w in train if w['feature'] is not None]
    clips_per_class={a:len({w['key'] for w in usable if w['action']==a}) for a in ACTIONS}
    model=make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True),
        ExtraTreesClassifier(n_estimators=CONFIG['trees'],max_depth=CONFIG['max_depth'],min_samples_leaf=CONFIG['min_samples_leaf'],
                             random_state=CONFIG['seed'],n_jobs=2))
    weights=[w['weight']/clips_per_class[w['action']] for w in usable]
    model.fit([w['feature'] for w in usable],[w['action'] for w in usable],extratreesclassifier__sample_weight=weights)
    predictions=[]
    for w in valid:
        guess=decision(model,w['feature']) if w['feature'] is not None else {'candidate_action_id':None,'reason':w['reason']}
        predictions.append({k:w[k] for k in ('key','action','end_ms')}|guess)
    n=len(predictions);covered=sum(w['candidate_action_id'] is not None for w in predictions)
    metrics={'windows':n,'accuracy_including_rejections':sum(w['action']==w['candidate_action_id'] for w in predictions)/n,'coverage':covered/n,
        'recall':{a:sum(w['candidate_action_id']==a for w in predictions if w['action']==a)/sum(w['action']==a for w in predictions) if any(w['action']==a for w in predictions) else None for a in ACTIONS}}
    passed=metrics['accuracy_including_rejections']>=.85 and metrics['coverage']>=.8 and all(v is not None and v>=.7 for v in metrics['recall'].values())
    # 文件只由此本地训练写入，产品加载前核摘要；未过开发线不能启用。
    joblib.dump(model,MODEL,compress=3)
    manifest={'version':'action-reference-'+signature[:12],'config':CONFIG,'sha256':hashlib.sha256(MODEL.read_bytes()).hexdigest(),
              'development_gate_passed':passed,'actions':list(model.classes_),'independent_unknown_validation':False}
    atomic_json(MANIFEST,manifest)
    result={'signature':signature,'identity':identity,'metrics':metrics,'development_gate_passed':passed,'train_windows':len(usable),
        'trained_actions':list(model.classes_),'missing_validation_actions':[a for a,v in metrics['recall'].items() if v is None],
        'class_weight_totals':{a:sum(weight for w,weight in zip(usable,weights) if w['action']==a) for a in ACTIONS},
        'train_clips':len({w['key'] for w in usable}),'validation_clips':len({w['key'] for w in valid}),
        'fit_keys':sorted({w['key'] for w in usable}),'validation_keys':sorted({w['key'] for w in valid}),
        'predictions':predictions,'window_rejections':dict(rejected),'test_used':False,'new_pose_inference_frames':0,
        'scope':'nine-action candidate; missing independent validation classes remain explicit; no live-camera acceptance'}
    if previous:
        result['prior_runs']=previous.get('prior_runs',[])+[{k:v for k,v in previous.items() if k!='prior_runs'}]
    atomic_json(REPORT,result)
    print(json.dumps({'cache_hit':False,'metrics':metrics,'development_gate_passed':passed,'train_windows':len(usable)},ensure_ascii=False))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--train',action='store_true')
    run(parser.parse_args().train)
