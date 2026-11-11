"""14-G 输入边界修正审计：开发原片解码＋既有姿态，不推理／调参／读取独立测试视频。"""
from collections import Counter
import hashlib
import inspect
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
from fitness_app.content_bounds import detect_content_bounds,bounds_signature
from fitness_app.frame_pipeline import framing,REQUIRED,FRAME_SCHEMA_VERSION,VISIBILITY_THRESHOLD
from fitness_app.research_data import load_dataset
from fitness_app.research_extract import probe_research_video,atomic_json
from fitness_app.media import video_frames
from fitness_app.sessions import IdentityContinuity
from fitness_app.cycles import CycleConfig
from fitness_app.action_counting import ActionCountConfig,ActionCounter
from fitness_app.reverse_lunge import ReverseLungeConfig,ReverseLungeCounter
from fitness_app.plank_hold import PlankHoldConfig,PlankHoldCounter
from fitness_app.jumping_jack import JumpingJackConfig,JumpingJackCounter
from fitness_app.counting_resources import implementation_signature as four_signature,used_dependencies
from fitness_app.additional_counting_resources import implementation_signature as additional_signature
from fitness_app.database import json_text
from diagnose_cycles import cached_frames as four_frames
from prepare_movement_input import cached_frames as movement_frames

OUT=ROOT/'data/verification/step-14/content-bounds-validation.json'
ADDITIONAL=[('reverse_lunge','reverse-lunge.json','dvids-551833','reverse-lunge-development.json'),
    ('forearm_plank','plank-hold.json','dvids-640237-side','plank-hold-development.json'),
    ('jumping_jack','jumping-jack.json','commons-jumping-jacks-segment','jumping-jack-development.json')]


def implementations():
    return {'four_actions':used_dependencies(four_signature()),**{action:additional_signature(action) for action,_,_,_ in ADDITIONAL}}


def case_signature(action,config):
    if config is None:
        implementation={'framing_code':hashlib.sha256(inspect.getsource(framing).encode()).hexdigest(),'content_bounds':bounds_signature(),
            'frame_schema':FRAME_SCHEMA_VERSION,'visibility_threshold':VISIBILITY_THRESHOLD}
    else:implementation=four_signature().copy() if action in {'bodyweight_squat','standard_push_up','standing_dumbbell_curl','conventional_deadlift'} else additional_signature(action).copy()
    implementation['framing_requirements']=REQUIRED[action]
    return hashlib.sha256(json_text({'action':action,'implementation':implementation,'config':config}).encode()).hexdigest()


def case_current(case,key,action,sha,config):
    if not case or case.get('input_implementation_sha256')!=case_signature(action,config) or case.get('source_sha256')!=sha:return False
    try:
        source=project_path(key).stat();cache=project_path(case['pose_cache_path']).stat()
        return (source.st_size,source.st_mtime_ns,cache.st_size,cache.st_mtime_ns)==(case['source_bytes'],case['source_modified_ns'],case['pose_cache_bytes'],case['pose_cache_modified_ns'])
    except (OSError,KeyError):return False


def pose_provenance(report,baseline,samples,sources):
    by_path={s.get('path'):s for s in sources if s.get('pose_cache')}
    samples={s.absolute_path.relative_to(ROOT).as_posix():s for s in samples.values()}
    for key,case in report['cases'].items():
        if key in samples:
            sample=samples[key];cache=ROOT/'data/experiments/poses'/f"{baseline['extractions'][sample.key]['pose_key']}.npz"
        else:cache=project_path(by_path[key]['pose_cache'])
        stat=cache.stat();case.update(pose_cache_path=cache.relative_to(ROOT).as_posix(),pose_cache_bytes=stat.st_size,pose_cache_modified_ns=stat.st_mtime_ns)


def reusable(report,parameters,expected_paths,configs):
    if report.get('content_bounds_signature')!=bounds_signature() or report.get('resource_parameters')!=parameters or report.get('resource_implementations')!=implementations() or set(report.get('cases',{}))!=expected_paths:return False
    try:
        for key,case in report['cases'].items():
            config=configs.get(case['action_id']) if case.get('counter_validated') else None
            if not case_current(case,key,case['action_id'],case['source_sha256'],config):return False
    except (OSError,KeyError):return False
    return True


def construct(action,config):
    if action in {'bodyweight_squat','standard_push_up','standing_dumbbell_curl','conventional_deadlift'}:
        return ActionCounter(action,ActionCountConfig(cycle=CycleConfig(**config['cycle']),**{k:v for k,v in config.items() if k!='cycle'}))
    types={'reverse_lunge':(ReverseLungeConfig,ReverseLungeCounter),'forearm_plank':(PlankHoldConfig,PlankHoldCounter),'jumping_jack':(JumpingJackConfig,JumpingJackCounter)}
    kind,counter=types[action];values=config.copy()
    if 'cycle' in values:values['cycle']=CycleConfig(**values['cycle'])
    return counter(kind(**values))


def audit_case(action,path,sha,frames,config,previous_events=None,previous_hold=None):
    path=project_path(path);info=probe_research_video(path);stat=path.stat();counter=construct(action,config) if config else None
    identity=IdentityContinuity();runs=[];events=[];holds=[];outside=Counter();reasons=Counter();changed=0;decoded=0;usable=0
    for seq,(stamp,rgb) in enumerate(video_frames(path,info)):
        if seq>=len(frames):raise ValueError('解码帧比缓存多')
        old=frames[seq]
        if stamp!=old['captured_ms'] or rgb.shape[:2]!=(old['height'],old['width']):raise ValueError('解码时间或尺寸与原姿态缓存不匹配')
        bounds=detect_content_bounds(rgb)
        if runs and runs[-1]['bounds']==bounds:runs[-1]['end_seq']=seq+1
        else:runs.append({'start_seq':seq,'end_seq':seq+1,'bounds':bounds})
        count=old['framing']['person_count'];points=old['framing']['landmarks']
        q=framing(action,{'poses':[points] if count==1 else [[] for unused in range(count)],'content_bounds':bounds})
        frame={**old,'framing':q};decoded+=1
        outside.update(q['outside_content_indices']);changed+=bool(q['outside_content_indices']);reasons[q['code']]+=1
        accepted=q['usable'] and identity.accept(frame)
        if accepted:
            usable+=1
            if counter:
                result=counter.process(frame,{'stream_epoch':'bounds-audit','identity_accepted':True});events.extend(result.get('events',[]));holds.extend(result.get('intervals',[]))
        elif counter:counter.reset('tracking_lost')
    if decoded!=len(frames):raise ValueError('原片解码不完整')
    hold_ms=sum(i['end_ms']-i['start_ms'] for i in holds)
    return {'action_id':action,'source_sha256':sha,'source_bytes':stat.st_size,'source_modified_ns':stat.st_mtime_ns,
        'counter_validated':config is not None,'input_implementation_sha256':case_signature(action,config),
        'frames':decoded,'bounds_runs':runs,'invalidated_frames':changed,'invalidated_landmark_counts':dict(outside),'framing_counts':dict(reasons),'usable_frames':usable,
        'events':events,'hold_ms':hold_ms,'previous_event_count':len(previous_events) if previous_events is not None else None,
        'events_unchanged':events==previous_events if previous_events is not None else None,'previous_hold_ms':previous_hold,
        'hold_unchanged':hold_ms==previous_hold if previous_hold is not None else None}


def main():
    began=time.perf_counter();four=json.loads((ROOT/'data/models/cycle-counting.json').read_text(encoding='utf-8'))
    old=json.loads((ROOT/'data/verification/step-12/development-cycles.json').read_text(encoding='utf-8'))
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'));samples={s.path:s for s in load_dataset()}
    sources=json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources']
    parameters={'four_actions':four['parameters_sha256'],**{action:json.loads((ROOT/'data/models'/name).read_text(encoding='utf-8'))['parameters_sha256'] for action,name,_,_ in ADDITIONAL}}
    configs={**four['actions'],**{action:json.loads((ROOT/'data/models'/name).read_text(encoding='utf-8'))['config'] for action,name,_,_ in ADDITIONAL}}
    expected_paths={samples[case['source_path']].absolute_path.relative_to(ROOT).as_posix() for group in old['actions'].values() for case in group['cases']}|{s['path'] for s in sources if s['id'] in {x[2] for x in ADDITIONAL}|{'dvids-692514-side'}}
    previous=json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {}
    if previous and reusable(previous,parameters,expected_paths,configs):
        print(json.dumps({'cache_hit':True,'cases':len(expected_paths),'path':str(OUT)},ensure_ascii=False));return
    report={'schema_version':1,'content_bounds_signature':bounds_signature(),'resource_parameters':{'four_actions':four['parameters_sha256']},
        'resource_implementations':implementations(),'scope':'development_inputs_only_no_new_pose_inference_no_independent_test','cases':{},'actions':{}}
    reuse_count=0;decode_count=0
    def obtain(action,path,sha,loader,config,events=None,hold=None):
        nonlocal reuse_count,decode_count
        key=project_path(path).relative_to(ROOT).as_posix();prior=previous.get('cases',{}).get(key)
        if case_current(prior,key,action,sha,config):reuse_count+=1;return prior
        decode_count+=1
        return audit_case(action,path,sha,loader(),config,events,hold)
    for action,group in old['actions'].items():
        before_decode,before_reuse=decode_count,reuse_count
        for case in group['cases']:
            sample=samples[case['source_path']]
            if sample.partition not in {'train','development'}:raise ValueError('禁止把独立测试视频用于输入规则调整')
            result=obtain(action,sample.absolute_path,sample.sha256,lambda:four_frames(sample,baseline,apply_bounds=False),four['actions'][action],events=case['events'])
            report['cases'][sample.absolute_path.relative_to(ROOT).as_posix()]=result
        print(json.dumps({'action':action,'cases':len(group['cases']),'decoded':decode_count-before_decode,'reused':reuse_count-before_reuse},ensure_ascii=False),flush=True)
    for action,name,sid,diagnosis in ADDITIONAL:
        before_decode,before_reuse=decode_count,reuse_count
        doc=json.loads((ROOT/'data/models'/name).read_text(encoding='utf-8'));prior=json.loads((ROOT/'data/verification/step-14'/diagnosis).read_text(encoding='utf-8'));source=next(s for s in sources if s['id']==sid)
        if source['role']!='development_candidate':raise ValueError('不能用取景反例替代正式动作开发输入')
        report['resource_parameters'][action]=doc['parameters_sha256']
        report['cases'][source['path']]=obtain(action,source['path'],source['sha256'],lambda:movement_frames(sid,apply_bounds=False),doc['config'],prior.get('events'),prior.get('hold_ms'))
        print(json.dumps({'action':action,'decoded':decode_count-before_decode,'reused':reuse_count-before_reuse},ensure_ascii=False),flush=True)
    source=next(s for s in sources if s['id']=='dvids-692514-side')
    report['cases'][source['path']]=obtain('barbell_overhead_press',source['path'],source['sha256'],lambda:movement_frames(source['id'],apply_bounds=False),None)
    for action in four['actions'].keys()|{x[0] for x in ADDITIONAL}:
        cases=[v for v in report['cases'].values() if v['action_id']==action]
        report['actions'][action]={'cases':len(cases),'counter_cases':sum(c['counter_validated'] for c in cases),'frames':sum(c['frames'] for c in cases),'invalidated_frames':sum(c['invalidated_frames'] for c in cases),
            'events':sum(len(c['events']) for c in cases),'hold_ms':sum(c['hold_ms'] for c in cases),
            'changed_event_cases':sum(c['events_unchanged'] is False for c in cases),'changed_hold_cases':sum(c['hold_unchanged'] is False for c in cases)}
    pose_provenance(report,baseline,samples,sources)
    report.update(elapsed_ms=round((time.perf_counter()-began)*1000),decoded_cases=decode_count,reused_cases=reuse_count);atomic_json(OUT,report)
    print(json.dumps({'actions':report['actions'],'counterexample':report['cases'][source['path']]['framing_counts'],'audit_sha256':hashlib.sha256(OUT.read_bytes()).hexdigest()},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
