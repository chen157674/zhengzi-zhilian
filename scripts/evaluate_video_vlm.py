"""用本地真实短视频比较Qwen动作候选；不把模型自述或自己标注当独立真值。"""
import argparse,collections,hashlib,json,sys
from functools import lru_cache
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av,numpy as np
from fitness_app.video_vlm import VideoActionVLM,CONFIG,prompt
from fitness_app.research_extract import atomic_json
from evaluate_xclip import sources,OUT


def frames(entry):
    start,end=entry['start_ms'],entry['end_ms'];span=min(3200,end-start);center=(start+end)/2;start=center-span/2;end=center+span/2
    count=CONFIG['frames'];desired=np.linspace(start,end-100,count);result=[];timestamps=[];index=0
    with av.open(str(project_path(entry['path']))) as video:
        video.seek(round(start)*1000)
        for frame in video.decode(video=0):
            if frame.time is None:continue
            stamp=round(float(frame.time)*1000)
            if stamp<desired[index]:continue
            rgb=frame.to_ndarray(format='rgb24')
            if entry['crop']:
                h,w=rgb.shape[:2];l,t,r,b=entry['crop'];rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
            result.append(rgb);timestamps.append(stamp);index+=1
            if index==count:return result,timestamps
    raise ValueError('不足所需真实视频画面')


@lru_cache(maxsize=1)
def legacy_pose_paths():
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'));result={}
    allowed={r['key'] for r in json.loads((ROOT/'data/experiments/visual-action-classifier.json').read_text(encoding='utf-8'))['identity']['inputs'] if r['partition'] in {'train','validation'}}
    for key in allowed:
        row=baseline['extractions'].get(key,{})
        if not row.get('feature_key'):continue
        path=ROOT/'data/experiments/features'/f"{row['feature_key']}.json"
        if path.exists():result[key]=json.loads(path.read_text(encoding='utf-8'))['pose_cache']
    from fitness_app.recognition_inputs import extra_inputs
    result.update({r['key']:r['cache'] for r in extra_inputs()});return result


def pose_cache_path(entry):
    if entry['id'].startswith('legacy-'):
        value=legacy_pose_paths().get(entry['key']);return project_path(value) if value else None
    path=OUT/(entry['id']+'-sequence.npz')
    return path if path.exists() else None


def guided_input(entry,images,times):
    from fitness_app.recognition_observations import observations
    path=pose_cache_path(entry)
    if path is None:return {},None
    with np.load(path,allow_pickle=False) as a:
        arrays={key:a[old].copy() for key,old in dict(timestamps='timestamps_ms' if 'timestamps_ms' in a.files else 'timestamps',points='points',people='person_counts' if 'person_counts' in a.files else 'people',sizes='sizes').items()}
    facts,box=observations(arrays,times[0],times[-1]);detail=None
    if box:
        image=images[len(images)//2];h,w=image.shape[:2];left,top,right,bottom=box
        detail=np.ascontiguousarray(image[round(top*h):round(bottom*h),round(left*w):round(right*w)])
    return facts,detail


def run(limit=2,include_legacy=False,only=None,variant='2b',quantized=False,adapter=None,guided=False):
    model=VideoActionVLM(variant,quantized,adapter);entries=sources(include_legacy);counts=collections.Counter();results=[]
    for entry in entries:
        if only and entry['source_id']!=only:continue
        if not only and entry['partition']!='validation':continue
        if counts[entry['action_id']]>=limit and not only:continue
        counts[entry['action_id']]+=1
        signature=hashlib.sha256(json.dumps({'source':entry['source_sha256'],'start':entry['start_ms'],'end':entry['end_ms'],'crop':entry['crop'],
                                           'config':CONFIG,'prompt':prompt(),'revision':model.info['revision'],'quantized':model.quantized,
                                           'adapter_sha256':hashlib.sha256((model.adapter/'adapter_model.safetensors').read_bytes()).hexdigest() if model.adapter else None,
                                           'guided':guided,'observation_code':hashlib.sha256((ROOT/'fitness_app/recognition_observations.py').read_bytes()).hexdigest() if guided else None},sort_keys=True).encode()).hexdigest()
        suffix='-'+hashlib.sha256((model.adapter/'adapter_model.safetensors').read_bytes()).hexdigest()[:8] if model.adapter else ''
        legacy=OUT/(entry['id']+f"-qwen{variant}-{CONFIG['frames']}f"+suffix+'.json')
        path=OUT/(entry['id']+f"-qwen{variant}-{CONFIG['frames']}f"+suffix+'-'+signature[:10]+'.json')
        if not path.exists() and legacy.exists():
            cached=json.loads(legacy.read_text(encoding='utf-8'))
            if cached.get('signature')==signature:path=legacy
        if path.exists():
            row=json.loads(path.read_text(encoding='utf-8'))
            if row['signature']!=signature:raise ValueError('视觉模型比较缓存策略已改变')
        else:
            images,times=frames(entry);facts,detail=guided_input(entry,images,times) if guided else ({},None)
            prediction=model.predict(images,times,facts,detail)
            row={'signature':signature,'expected':entry['action_id'],'partition':entry['partition'],'source_group':entry['group'],
                 'source_sha256':entry['source_sha256'],'sampled_timestamps_ms':times,'id':entry['id'],'prediction':prediction,
                 'ground_truth':'publisher action label plus segment review; not independent expert posture-error ground truth'}
            atomic_json(path,row)
        results.append(row);p=row['prediction']
        print(json.dumps({'id':entry['id'],'expected':row['expected'],'top':p['top_action_id'],'accepted':p['action_id'],'score':p['score'],'elapsed_ms':p['elapsed_ms']}),flush=True)
    positive=[r for r in results if not r['expected'].startswith('unknown')];negative=[r for r in results if r['expected'].startswith('unknown')]
    n=len(positive);metrics={'clips':n,'top1':sum(r['prediction']['top_action_id']==r['expected'] for r in positive)/max(1,n),
                           'accuracy_including_rejections':sum(r['prediction']['action_id']==r['expected'] for r in positive)/max(1,n),
                           'coverage':sum(r['prediction']['action_id'] is not None for r in positive)/max(1,n),
                           'negative_clips':len(negative),'negative_false_accepts':sum(r['prediction']['action_id'] is not None for r in negative)}
    report={'scope':'development comparison; full frame clip, no real-time person/motion gate; no independent test or posture-error accuracy claim',
            'metrics':metrics,'per_class':{a:{'clips':counts[a],'correct':sum(r['prediction']['action_id']==a for r in positive if r['expected']==a)} for a in counts},'results':results,'runtime_enabled':False}
    report.update(feature_config=CONFIG,prompt=prompt(),model_revision=model.info['revision'],variant=variant,quantized=model.quantized,pose_guided=guided)
    report_path=OUT/'video-vlm-comparison.json'
    if report_path.exists():
        previous=json.loads(report_path.read_text(encoding='utf-8'))
        report['previous_comparisons']=previous.get('previous_comparisons',[])+[{k:v for k,v in previous.items() if k not in ('results','previous_comparisons')}]
    atomic_json(report_path,report);print(json.dumps(metrics),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit-per-class',type=int,default=2);parser.add_argument('--include-legacy',action='store_true');parser.add_argument('--only');parser.add_argument('--variant',choices=['2b','4b'],default='2b');parser.add_argument('--quantized',action='store_true');parser.add_argument('--adapter');parser.add_argument('--pose-guided',action='store_true');args=parser.parse_args();run(args.limit_per_class,args.include_legacy,args.only,args.variant,args.quantized,args.adapter,args.pose_guided)
