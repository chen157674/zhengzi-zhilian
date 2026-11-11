"""14-H：只解码新增划船原片，复用姿态；结果为开发诊断而非独立正确率。"""
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
from fitness_app.barbell_row import BarbellRowConfig,BarbellRowCounter,row_observation
from fitness_app.cycles import CycleConfig
from fitness_app.database import json_text
from fitness_app.frame_pipeline import framing
from fitness_app.content_bounds import bounds_signature
from fitness_app.additional_counting_resources import implementation_signature,read_manifest
from prepare_movement_input import cached_frames
from audit_content_bounds import audit_case,case_current

OUT=ROOT/'data/verification/step-14/barbell-row-development.json'
SOURCE='dvids-639914-side'


def development_config():
    return BarbellRowConfig(CycleConfig(145.,-1,12.,25.,8.,200,80,80,100,500,600,30000,15.),30.,20.,100.,80.,.6,.15,3,'barbell-row-development-1')


def prepare_frames():
    source=next(s for s in json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources'] if s['id']==SOURCE)
    frames=cached_frames(SOURCE,apply_bounds=False);previous=json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {}
    case=previous.get('input_validation');key=source['path'];cache=ROOT/f'data/verification/step-14/{SOURCE}-poses.npz';stat=cache.stat()
    hit=case_current(case,key,source['action_id'],source['sha256'],None)
    if not hit:
        case=audit_case(source['action_id'],key,source['sha256'],frames,None)
        case.update(pose_cache_path=cache.relative_to(ROOT).as_posix(),pose_cache_bytes=stat.st_size,pose_cache_modified_ns=stat.st_mtime_ns)
    for run in case['bounds_runs']:
        for f in frames[run['start_seq']:run['end_seq']]:
            count=f['framing']['person_count'];points=f['framing']['landmarks']
            f['framing']=framing(source['action_id'],{'poses':[points] if count==1 else [[] for _ in range(count)],'content_bounds':run['bounds']})
    return frames,case,hit


def run(frames,config):
    counter=BarbellRowCounter(config);events=[];reasons=Counter();transitions=[];previous=None
    for frame in frames:
        if not frame['framing']['usable']:
            counter.reset('tracking_lost');result=counter._output()
        else:result=counter.process(frame,{'stream_epoch':'row-development','identity_accepted':frame['identity_accepted']})
        events.extend(result['events']);reason=result['observation']['sides']['none']['reason'];reasons[reason or 'observable']+=1
        state=(counter.tracker.phase,reason)
        if state!=previous:transitions.append({'at_ms':frame['captured_ms'],'phase':state[0],'reason':reason});previous=state
    return {'frames':len(frames),'events':events,'reason_frame_counts':dict(reasons),'transitions':transitions}


def diagnose():
    if (ROOT/'data/models/barbell-row.json').exists():
        frozen=read_manifest('barbell_bent_over_row')
        source=next(s for s in json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources'] if s['id']==SOURCE)
        previous=json.loads(OUT.read_text(encoding='utf-8'))
        if frozen['config']!=asdict(development_config()) or not case_current(previous['input_validation'],source['path'],source['action_id'],source['sha256'],None):
            raise ValueError('已冻结开发证据发生变化，先核查影响，不自动重跑或覆盖')
        print(json.dumps({'cache_hit':True,'events':len(previous['events']),'capability_version':frozen['capability_version'],'decoded_frames':0,'new_pose_inference':False}));return
    frames,case,hit=prepare_frames();config=development_config();result=run(frames,config);raw=asdict(config)
    report={'schema_version':1,'action_id':'barbell_bent_over_row','source_id':SOURCE,'config':raw,'parameters_sha256':hashlib.sha256(json_text(raw).encode()).hexdigest(),
        'content_bounds_signature':bounds_signature(),'input_validation':case,'input_decode_cache_hit':hit,**result,
        'observations':[{'at_ms':f['captured_ms'],'sides':{s:row_observation(f,s,config) for s in ('left','right')}} for f in frames[::6]],
        'parameter_basis':'候选145度伸臂起点、25度肘屈伸幅度、0.15起点躯干长的手肩距离变化用于可分周期，不是幅度合格线。俯身范围用于区别站起结束，较宽持续范围允许躯干摆动；质量交步骤17。参数仅在本开发素材上诊断，尚未通过独立效果验收。',
        'evidence_status':'development_input_only_no_independent_ground_truth','limitations':['单一人物、斜侧面；模型关键点并不能证明被器械遮挡的部位真实可见','不识别器械种类、杠铃路径、重量或脊柱曲度','无逐次／质量独立标签；本次观察和输出不能充当准确率']}
    # 同一份实际像素范围＋姿态＋周期输出证据供本动作加载校验；不改写原七动作审计。
    report.update(resource_parameters={'barbell_bent_over_row':report['parameters_sha256']},
        resource_implementations={'barbell_bent_over_row':implementation_signature('barbell_bent_over_row')},
        actions={'barbell_bent_over_row':{'counter_cases':1,'events':len(result['events']),'hold_ms':0,'frames':len(frames)}})
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'events':result['events'],'reasons':result['reason_frame_counts'],'input_decode_cache_hit':hit,'framing':case['framing_counts']},ensure_ascii=False))


if __name__=='__main__':diagnose()
