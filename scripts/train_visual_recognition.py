"""冻结本地图像骨干，训练动作分类层；固定分区、复用图像特征、不读test。"""
import hashlib,json,sys,time,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av
import joblib
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits
from fitness_app.automatic_recognition import ACTIONS,decision
from fitness_app.visual_recognition import VisualRecognizer,normalize,DIRECTORY,CONFIG as VISUAL_CONFIG
from fitness_app.research_data import load_dataset
from fitness_app.recognition_inputs import extra_inputs
from fitness_app.research_extract import atomic_json

REPORT=ROOT/'data/experiments/visual-action-classifier.json'
CACHE=ROOT/'data/experiments/visual-action-features.npz'
MODEL=ROOT/'data/models/visual-action-classifier.joblib'
CONFIG={'schema':'clip-linear-nine-1','frames_per_clip':8,'step_ms':1000,'C':1.,'max_iter':2000,'class_weight':'balanced','minimum_score':.8,'minimum_margin':.2}


def samples():
    result=[]
    for s in load_dataset():
        if s.action_id not in ACTIONS or s.partition not in {'train','validation','development'}:continue
        val=s.partition=='validation' or s.partition=='development' and s.validation_fold==3
        result.append({'key':s.key,'action':s.action_id,'partition':'validation' if val else 'train',
                       'group':s.dataset_id+':'+s.subject_group,'sha256':s.sha256,'path':s.absolute_path.relative_to(ROOT).as_posix()})
    for e in extra_inputs():result.append({'key':e['key'],'action':e['action'],'partition':e['partition'],'group':e['group'],'sha256':e['source_sha256'],'path':e['source']})
    if {e['group'] for e in result if e['partition']=='train'} & {e['group'] for e in result if e['partition']=='validation'}:
        raise ValueError('视觉训练验证人物或来源交叉')
    if len({e['sha256'] for e in result})!=len(result):raise ValueError('视觉训练出现重复原片')
    return result


def save_cache(signature,arrays):
    partial=CACHE.with_suffix('.npz.part')
    if partial.exists():raise ValueError('待核对的视觉缓存中间文件存在')
    try:
        with partial.open('xb') as output:np.savez_compressed(output,signature=signature,**arrays)
        os.replace(partial,CACHE)
    finally:
        if partial.exists():partial.unlink()


def run(allow_train=False):
    entries=samples()
    identity={'inputs':entries,'config':CONFIG,'visual_config':VISUAL_CONFIG,
        'model_manifest':json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8')),
        'code':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'scripts/train_visual_recognition.py','fitness_app/visual_recognition.py','fitness_app/content_bounds.py')}}
    signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    if REPORT.exists():
        old=json.loads(REPORT.read_text(encoding='utf-8'))
        if old.get('signature')==signature:
            print(json.dumps({'cache_hit':True,'metrics':old['metrics']},ensure_ascii=False));return
        raise ValueError('已有视觉训练结果与本次实现不同，请先核对受影响范围')
    if not allow_train:raise ValueError('仅显式 --train 才提取缺少特征并训练新分类层')
    arrays={};reused_frames=0;new_frames=0;completed=0;encoder=None;latency=[]
    if CACHE.exists():
        with np.load(CACHE,allow_pickle=False) as a:
            if str(a['signature'])!=signature:raise ValueError('视觉训练缓存身份不同')
            arrays={k:a[k].copy() for k in a.files if k!='signature'}
    prior=json.loads((ROOT/'data/experiments/visual-recognition.json').read_text(encoding='utf-8'))
    reusable={r['key']:i for i,r in enumerate(prior['clips']) if r['start_ms']==0}
    if prior['identity']['config']!=json.loads(json.dumps(VISUAL_CONFIG)):raise ValueError('旧视觉特征配置不一致')
    prior_arrays=np.load(ROOT/'data/experiments/visual-recognition-features.npz',allow_pickle=False)
    try:
        for i,entry in enumerate(entries):
            name=hashlib.sha256(entry['key'].encode()).hexdigest()[:24]
            if name in arrays:continue
            path=project_path(entry['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('视觉视频摘要改变')
            if entry['key'] in reusable:
                p='clip_'+str(reusable[entry['key']]);features=prior_arrays[p][:8];stamps=prior_arrays[p+'_timestamps'][:8]
                reused_frames+=len(features)
            else:
                if encoder is None:encoder=VisualRecognizer()
                features=[];stamps=[];next_stamp=1000
                with av.open(str(path)) as source:
                    for frame in source.decode(video=0):
                        stamp=round(float(frame.time)*1000)
                        if stamp<next_stamp:continue
                        began=time.perf_counter();features.append(encoder.embed(frame.to_ndarray(format='rgb24')))
                        latency.append((time.perf_counter()-began)*1000);new_frames+=1;stamps.append(stamp);next_stamp+=1000
                        if len(features)>=8:break
                features=np.asarray(features,dtype=np.float32);stamps=np.asarray(stamps,dtype=np.int64)
            arrays[name]=features;arrays[name+'_timestamps']=stamps;completed+=1
            if completed%16==0:
                save_cache(signature,arrays);print(json.dumps({'clips_ready':i+1,'clips_total':len(entries),'new_frames':new_frames,'reused_frames':reused_frames}),flush=True)
        save_cache(signature,arrays)
    finally:prior_arrays.close()
    available=[];excluded=[]
    for entry in entries:
        features=arrays[hashlib.sha256(entry['key'].encode()).hexdigest()[:24]]
        if not len(features):excluded.append(entry['key']);continue
        available.append({**entry,'feature':normalize(features.mean(axis=0))})
    training=[e for e in available if e['partition']=='train'];validation=[e for e in available if e['partition']=='validation']
    classifier=make_pipeline(StandardScaler(),LogisticRegression(C=1.,class_weight='balanced',max_iter=2000,random_state=20261021))
    with threadpool_limits(limits=2):classifier.fit([e['feature'] for e in training],[e['action'] for e in training])
    predictions=[{k:v for k,v in e.items() if k!='feature'}|decision(classifier,e['feature']) for e in validation]
    n=len(predictions)
    metrics={'clips':n,'accuracy_including_rejections':sum(p['candidate_action_id']==p['action'] for p in predictions)/n,
        'coverage':sum(p['candidate_action_id'] is not None for p in predictions)/n,
        'recall':{a:sum(p['candidate_action_id']==a for p in predictions if p['action']==a)/sum(p['action']==a for p in predictions) if any(p['action']==a for p in predictions) else None for a in ACTIONS}}
    passed=metrics['accuracy_including_rejections']>=.85 and metrics['coverage']>=.8 and all(v is not None and v>=.7 for v in metrics['recall'].values())
    joblib.dump(classifier,MODEL,compress=3)
    atomic_json(MODEL.with_suffix('.json'),{'config':CONFIG,'sha256':hashlib.sha256(MODEL.read_bytes()).hexdigest(),'development_gate_passed':passed,
        'actions':list(classifier.classes_),'visual_backbone_revision':identity['model_manifest']['revision'],'production_enabled':False,
        'independent_unknown_validation':False,'version':'visual-reference-'+signature[:12]})
    atomic_json(REPORT,{'signature':signature,'identity':identity,'metrics':metrics,'predictions':predictions,
        'train_clips':len(training),'fit_keys':[e['key'] for e in training],'validation_keys':[e['key'] for e in validation],
        'excluded_empty_clips':excluded,'new_visual_inference_frames':new_frames,'reused_visual_frames':reused_frames,'new_pose_inference_frames':0,
        'development_gate_passed':passed,'test_used':False,'production_enabled':False,
        'latency_ms':{'p50':float(np.percentile(latency,50)) if latency else None,'p95':float(np.percentile(latency,95)) if latency else None},
        'scope':'Frozen CLIP backbone, trained nine-class linear head; clip-level validation, not continuous automatic recognition or twelve-action acceptance.'})
    print(json.dumps({'metrics':metrics,'development_gate_passed':passed,'train_clips':len(training)},ensure_ascii=False),flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--train',action='store_true')
    run(parser.parse_args().train)
