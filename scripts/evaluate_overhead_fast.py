"""全原验证集的过顶单次推理候选；只允许确认下拉后跳过通用分类。"""
import argparse,gzip,hashlib,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import numpy as np
from fitness_app.overhead_action_review import OverheadActionReviewer,eligible,accepted_pulldown,version,POLICY,PROMPT
from fitness_app.video_vlm import VideoActionVLM
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.recognition_observations import observations
from fitness_app.research_extract import atomic_json
from scripts.train_chest_expert import cached_baseline
from scripts.evaluate_pull_press_review import images

REPORT=ROOT/'data/verification/round3/overhead-fast-review.json'


def crop_images(entry,times):
    path=ROOT/entry['path']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['source_sha256']:raise ValueError('候选媒体来源改变')
    frames=images(path,list(map(int,times)))
    if entry.get('crop'):
        frames=[np.ascontiguousarray(image[round(entry['crop'][1]*image.shape[0]):round(entry['crop'][3]*image.shape[0]),
            round(entry['crop'][0]*image.shape[1]):round(entry['crop'][2]*image.shape[1])]) for image in frames]
    return frames


def run(resume=False):
    previous=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else None
    if previous and not resume:raise ValueError('已有快速候选报告，需要明确复用原推理结果')
    if resume and previous and previous['state'] not in ('failed','completed'):
        raise ValueError('原任务仍未记终态，先确认进程并核实失败')
    if previous and previous.get('version')==version() and previous['state']=='completed':
        raise ValueError('同一协议已完成，不重复推理')
    folder=ROOT/'data/verification/step-22'
    entries=json.loads((folder/'semantic-video-features.json').read_text(encoding='utf-8'))['entries']
    families={r['id']:r for r in json.loads((folder/'video-family-evaluation.json').read_text(encoding='utf-8'))['rows']}
    calibration={r['id']:r for r in json.loads((folder/'video-release-calibration.json').read_text(encoding='utf-8'))['validation']['predictions']}
    baseline=SemanticPredictor(research=True);reviewer=OverheadActionReviewer(VideoActionVLM('4b',quantized=True))
    document={'state':'running','pid':__import__('os').getpid(),'version':version(),'policy':POLICY,'prompt':PROMPT,
        'scope':'fixed_development_validation_and_user_pair_not_independent_accuracy','test_used':False,
        'private_media_uploaded':False,'paid_api_calls':0,'new_training_performed':False,'validation':[],'user_pair':[],
        **({'previous_runs':previous.get('previous_runs',[])+[{k:v for k,v in previous.items() if k!='previous_runs'}]} if previous else {})}
    atomic_json(REPORT,document)
    for entry in entries:
        if entry['partition']!='validation':continue
        result,arrays,times=cached_baseline(entry,baseline,families,calibration,{})
        facts,_=observations(arrays,int(times[0]),int(times[-1]));routed=eligible(facts);review=None;candidate=result['action_id']
        if routed:
            prior=next((r for r in (previous or {}).get('validation',[]) if r['id']==entry['id'] and r.get('review')),None)
            review=prior['review'] if prior else reviewer.review(crop_images(entry,times),times)
            if accepted_pulldown(review):candidate='lat_pulldown'
            print(json.dumps({'id':entry['id'],'expected':entry['action_id'],'baseline':result['action_id'],
                'fast_action':review['action_id'],'top':review['top_action_id'],'score':review['score'],'candidate':candidate}),flush=True)
        document['validation'].append({'id':entry['id'],'source_sha256':entry['source_sha256'],'group':entry['group'],
            'expected':entry['action_id'],'source_ms':list(map(int,times)),'routing_facts':facts,'eligible':routed,
            'baseline_action':result['action_id'],'candidate_action':candidate,'review':review})
        atomic_json(REPORT,document)
    old=json.loads((ROOT/'data/verification/round3/lat-pulldown-auto-review.json').read_text(encoding='utf-8'))
    manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    for video in old['videos']:
        number=video['demo_id'].split('-')[-1]
        with gzip.open(ROOT/('data/experiments/open-source-correction/user-'+number+'-native-inputs.json.gz'),'rt',encoding='utf-8') as stream:cache=json.load(stream)
        entry={'path':manifest['demos'][video['demo_id']]['video']['path'],'source_sha256':video['source_sha256']}
        for window in video['windows']:
            times=window['source_ms'];rows=[r for r in cache['rows'] if times[0]<=r['t']<=times[-1] and len(r['pose_result']['poses'])==1]
            arrays={'timestamps':np.asarray([r['t'] for r in rows]),'points':np.asarray([[[p[k] for k in ('x','y','z','visibility','presence')]
                for p in r['pose_result']['poses'][0]] for r in rows]),'people':np.ones(len(rows)),
                'sizes':np.asarray([[r['width'],r['height']] for r in rows])}
            facts,_=observations(arrays,times[0],times[-1]);routed=eligible(facts)
            prior=next((r for r in (previous or {}).get('user_pair',[]) if r['source_id']==video['demo_id'] and r['source_ms']==times and r.get('review')),None)
            review=(prior['review'] if prior else reviewer.review(crop_images(entry,times),times)) if routed else None
            document['user_pair'].append({'source_id':video['demo_id'],'source_sha256':video['source_sha256'],
                'source_ms':times,'routing_facts':facts,'eligible':routed,'review':review,
                'accepted_fast':accepted_pulldown(review) if review else False})
            atomic_json(REPORT,document)
            print(json.dumps({'user':video['demo_id'],'start_ms':times[0],'eligible':routed,'action':review['action_id'] if review else None}),flush=True)
    rows=document['validation']
    document.update(state='completed',regressions=[r['id'] for r in rows if r['baseline_action']==r['expected'] and r['candidate_action']!=r['expected']],
        false_pulldown_accepts=[r['id'] for r in rows if r['expected']!='lat_pulldown' and r['candidate_action']=='lat_pulldown'])
    document['safe_nonregression_candidate']=not document['regressions'] and not document['false_pulldown_accepts']
    atomic_json(REPORT,document)
    print(json.dumps({k:document[k] for k in ('state','regressions','false_pulldown_accepts','safe_nonregression_candidate')}),flush=True)


def execution_check():
    output=ROOT/'data/verification/round3/overhead-fast-execution-review.json'
    if output.exists():raise ValueError('执行策略数值对照已有结果，先核原运行')
    old=json.loads(REPORT.read_text(encoding='utf-8'));row=old['user_pair'][0]
    manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    item=manifest['demos'][row['source_id']]
    entry={'path':item['video']['path'],'source_sha256':row['source_sha256']}
    frames=crop_images(entry,row['source_ms']);times=np.asarray(row['source_ms'])
    base=VideoActionVLM('4b',quantized=True)
    doc={'state':'running','pid':__import__('os').getpid(),'source_id':row['source_id'],
        'source_sha256':row['source_sha256'],'source_ms':row['source_ms'],'version':version(),
        'private_media_uploaded':False,'scope':'same_pixels_and_weights_all_class_score_numeric_equivalence_not_accuracy',
        'implementation_sha256':hashlib.sha256((ROOT/'fitness_app/overhead_action_review.py').read_bytes()).hexdigest()}
    atomic_json(output,doc)
    try:
        legacy=OverheadActionReviewer(base,low_latency=False).review(frames,times)
        modern=OverheadActionReviewer(base,low_latency=True).review(frames,times)
        keys=sorted(legacy['scores'])
        error=max(abs(legacy['scores'][k]-modern['scores'][k]) for k in keys)
        if error>1e-5 or legacy['action_id']!=modern['action_id']:raise ValueError('实时执行策略数值不一致')
        doc.update(state='completed',legacy=legacy,low_latency=modern,all_class_max_abs_error=error,numeric_gate_passed=True)
        atomic_json(output,doc)
        print(json.dumps({'state':'completed','max_abs_error':error,'classes':len(keys),
            'action':modern['action_id'],'legacy_ms':legacy['elapsed_ms'],'low_latency_ms':modern['elapsed_ms']}),flush=True)
    except BaseException as error:
        doc.update(state='failed',error={'type':type(error).__name__,'message':str(error)[:1000]})
        atomic_json(output,doc);raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--resume',action='store_true');parser.add_argument('--execution-check',action='store_true')
    args=parser.parse_args()
    try:
        if args.execution_check:execution_check()
        else:run(args.resume)
    except BaseException as error:
        if REPORT.exists():
            report=json.loads(REPORT.read_text(encoding='utf-8'))
            if report.get('state')=='running' and report.get('pid')==__import__('os').getpid():
                report.update(state='failed',error={'type':type(error).__name__,'message':str(error)[:1000]})
                atomic_json(REPORT,report)
        raise
