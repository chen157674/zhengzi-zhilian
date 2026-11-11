"""仅比较简短分类上下文的开发效果/延迟；保留集不读取、不以分数自述验收。"""
import argparse,hashlib,json,sys,time,collections
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.compact_video import CompactVideoVLM,compact_prompt,MAXIMUM_CONTEXT_MS
from fitness_app.research_extract import atomic_json
from evaluate_xclip import sources,OUT
from evaluate_video_vlm import guided_input
from train_semantic_fusion import evaluate
import numpy as np
import av


def frames(entry):
    span=min(MAXIMUM_CONTEXT_MS,entry['end_ms']-entry['start_ms']);center=(entry['start_ms']+entry['end_ms'])/2
    first,last=center-span/2,center+span/2;desired=np.linspace(first,last-100,8);images=[];times=[]
    with av.open(str(project_path(entry['path']))) as video:
        video.seek(round(first)*1000)
        for frame in video.decode(video=0):
            if frame.time is None:continue
            stamp=round(float(frame.time)*1000)
            if stamp<desired[len(images)]:continue
            rgb=frame.to_ndarray(format='rgb24')
            if entry['crop']:
                h,w=rgb.shape[:2];l,t,r,b=entry['crop'];rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
            images.append(rgb);times.append(stamp)
            if len(images)==8:return images,times
    raise ValueError('慢动作上下文不足8个真实画面')


def run(limit=2,variant='4b'):
    model=CompactVideoVLM(variant);entries=[];counts=collections.Counter()
    policy={'version':model.version,'prompt':compact_prompt(),'maximum_context_ms':MAXIMUM_CONTEXT_MS,
            'code':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ('fitness_app/compact_video.py','fitness_app/video_vlm.py','fitness_app/recognition_observations.py','scripts/evaluate_video_vlm.py','scripts/evaluate_compact_video.py')}}
    for entry in sources(True):
        if entry['partition']!='validation' or counts[entry['action_id']]>=limit:continue
        entries.append(entry);counts[entry['action_id']]+=1
    rows=[];scores=[];classes=None;latencies=[]
    for entry in entries:
        identity={'policy':policy,'source':entry['source_sha256'],'start':entry['start_ms'],'end':entry['end_ms'],'crop':entry['crop']}
        signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        target=OUT/(entry['id']+'-compact-'+signature[:10]+'.json')
        if target.exists():
            row=json.loads(target.read_text(encoding='utf-8'))
            if row['signature']!=signature:raise ValueError('简短视频候选缓存归属不符')
        else:
            images,times=frames(entry);facts,detail=guided_input(entry,images,times)
            prediction=model.predict(images,times,facts,detail)
            row={'signature':signature,'entry':entry,'prediction':prediction};atomic_json(target,row)
        prediction=row['prediction'];rows.append(row);classes=list(prediction['scores']);scores.append(list(prediction['scores'].values()));latencies.append(prediction['elapsed_ms'])
        print(json.dumps({'id':entry['id'],'expected':entry['action_id'],'predicted':prediction['action_id'],'ms':prediction['elapsed_ms']}),flush=True)
    metrics=evaluate(entries,np.asarray(scores),classes,.55,.15,[r['prediction']['code_mass']>=.6 for r in rows])
    report={'policy':policy,'validation':metrics,'latency_ms':{'p50':float(np.median(latencies)),'p95':float(np.percentile(latencies,95))},
            'rows':rows,'test_used':False,'runtime_enabled':False,'scope':'source-disjoint development comparison; no independent test/posture-error accuracy claim'}
    atomic_json(OUT/f'compact-video-{variant}.json',report)
    print(json.dumps({k:v for k,v in report.items() if k in ('latency_ms','runtime_enabled')}|{'metrics':{k:v for k,v in metrics.items() if k not in ('per_class','predictions')}}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit-per-class',type=int,default=2);parser.add_argument('--variant',choices=('2b','4b'),default='4b');args=parser.parse_args();run(args.limit_per_class,args.variant)
