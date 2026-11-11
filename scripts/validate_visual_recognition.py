"""固定真实片段的视觉候选评估；不重新推理姿态、不读取test、不据结果选帧。"""
import hashlib,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av
import numpy as np
from fitness_app.visual_recognition import VisualRecognizer,CONFIG,DIRECTORY
from fitness_app.automatic_recognition import CORE_ACTIONS
from fitness_app.recognition_inputs import extra_inputs
from fitness_app.research_data import load_dataset
from fitness_app.research_extract import atomic_json

REPORT=ROOT/'data/experiments/visual-recognition.json'
CACHE=ROOT/'data/experiments/visual-recognition-features.npz'


def inputs():
    samples=load_dataset();result=[]
    for action in CORE_ACTIONS:
        options=[s for s in samples if s.action_id==action and (s.partition=='validation' or s.partition=='development' and s.validation_fold==3)]
        for sample in sorted(options,key=lambda s:s.key)[:3]:
            result.append({'key':sample.key,'action':action,'role':'heldout_clip_label','sha256':sample.sha256,
                'path':sample.absolute_path.relative_to(ROOT).as_posix(),'start_ms':0,'end_ms':13000})
    for entry in extra_inputs():
        if entry['partition']=='validation' or entry['action'] in {'reverse_lunge','forearm_plank','jumping_jack'}:
            result.append({'key':entry['key'],'action':entry['action'],'sha256':entry['source_sha256'],'path':entry['source'],
                'role':'heldout_clip_label' if entry['partition']=='validation' else 'training_source_engineering_only',
                'start_ms':0,'end_ms':13000})
    source=next(s for s in json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources'] if s['id']=='dvids-767109')
    for start,end,action in ((64000,72000,'bent_knee_sit_up'),(104000,112000,'bent_knee_sit_up'),(25000,30000,None)):
        result.append({'key':source['id']+':'+str(start),'action':action,'sha256':source['sha256'],'path':source['path'],
            'role':'development_annotation_multi_person','start_ms':start,'end_ms':end})
    return result


def run():
    entries=inputs();files={e['path']:e['sha256'] for e in entries}
    for path,digest in files.items():
        if hashlib.sha256(project_path(path).read_bytes()).hexdigest()!=digest:raise ValueError('视觉输入来源摘要改变')
    identity={'inputs':entries,'config':CONFIG,'model_manifest':json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8')),
        'code':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'scripts/validate_visual_recognition.py','fitness_app/visual_recognition.py','fitness_app/content_bounds.py')}}
    signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    if REPORT.exists():
        old=json.loads(REPORT.read_text(encoding='utf-8'))
        if old.get('signature')!=signature:raise ValueError('评估实现或输入已改变，先核对旧结果，不自动重跑')
        print(json.dumps({'cache_hit':True,'metrics':old['metrics']},ensure_ascii=False));return
    model=VisualRecognizer();arrays={};saved=None;times=[]
    if CACHE.exists():
        with np.load(CACHE,allow_pickle=False) as a:
            if str(a['signature'])!=signature:raise ValueError('已有视觉特征缓存身份不一致')
            saved={k:a[k].copy() for k in a.files if k!='signature'}
    results=[];new_frames=0
    for i,entry in enumerate(entries):
        name='clip_'+str(i)
        if saved is not None:
            features=saved[name];stamps=saved[name+'_timestamps']
        else:
            features=[];stamps=[];next_stamp=entry['start_ms']+1000
            with av.open(str(project_path(entry['path']))) as video:
                for frame in video.decode(video=0):
                    stamp=round(float(frame.time)*1000)
                    if stamp>=entry['end_ms']:break
                    if stamp<next_stamp:continue
                    began=time.perf_counter();features.append(model.embed(frame.to_ndarray(format='rgb24')))
                    times.append((time.perf_counter()-began)*1000);stamps.append(stamp);new_frames+=1;next_stamp+=1000
                    if len(features)>=12:break
            features=np.asarray(features,dtype=np.float32);stamps=np.asarray(stamps,dtype=np.int64)
        arrays[name]=features;arrays[name+'_timestamps']=stamps
        predicted=model.classify(features) if len(features) else {'action_id':None,'reason':'no_sampled_frames'}
        result={**entry,'frames':len(features),'timestamps_ms':stamps.tolist(),'prediction':predicted}
        results.append(result);print(json.dumps({'action':entry['action'],'role':entry['role'],'frames':len(features),
            'prediction':predicted.get('action_id'),'top':predicted.get('top_label'),'margin':predicted.get('margin')},ensure_ascii=False),flush=True)
    heldout=[r for r in results if r['role']=='heldout_clip_label']
    correct=sum(r['prediction']['action_id']==r['action'] for r in heldout)
    covered=sum(r['prediction']['action_id'] is not None for r in heldout)
    metrics={'heldout_clips':len(heldout),'accuracy_including_rejections':correct/len(heldout),'coverage':covered/len(heldout),
        'per_action':{action:{'clips':sum(r['action']==action for r in heldout),
            'correct':sum(r['action']==action and r['prediction']['action_id']==action for r in heldout)} for action in sorted({r['action'] for r in heldout})}}
    if saved is None:
        with CACHE.open('xb') as f:np.savez_compressed(f,signature=signature,**arrays)
    atomic_json(REPORT,{'signature':signature,'identity':identity,'metrics':metrics,'clips':results,
        'new_visual_inference_frames':new_frames,'new_pose_inference_frames':0,'fit_performed':False,'test_used':False,
        'latency_ms':{'p50':float(np.percentile(times,50)) if times else None,'p95':float(np.percentile(times,95)) if times else None},
        'production_enabled':False,'full_twelve_action_validation':False,
        'scope':'Zero-shot clip-level diagnostic with predeclared sampling; not live recognition, posture accuracy, or independent twelve-class acceptance.'})
    print(json.dumps(metrics,ensure_ascii=False),flush=True)


if __name__=='__main__':run()
