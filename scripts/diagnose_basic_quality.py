"""15：少量既有开发周期接新幅度观测，复用姿态，不训练／预测粗分类模型。"""
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
from fitness_app.quality_candidates import CandidateRule,SCHEMA,SPECS,evaluate_cycle
from fitness_app.counting_resources import create_counter
from fitness_app.catalogue import get_action
from fitness_app.research_data import load_dataset
from fitness_app.database import json_text
from diagnose_cycles import cached_frames
from prepare_movement_input import cached_frames as movement_frames

OUT=ROOT/'data/verification/step-15/basic-range-development.json'


def inputs():
    old=json.loads((ROOT/'data/verification/step-12/development-cycles.json').read_text(encoding='utf-8'))
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'));samples={s.path:s for s in load_dataset()};chosen=[]
    for action in ('bodyweight_squat','standard_push_up','standing_dumbbell_curl'):
        # 连接检查需完整事件；按既有固定顺序选首个，不按新质量输出挑样。
        eligible=[c for c in old['actions'][action]['cases'] if c['events'] and ('diagonal' in c['view'] if action=='standing_dumbbell_curl' else 'side' in c['view'])]
        case=eligible[0];sample=samples[case['source_path']]
        if sample.partition not in {'train','development'}:raise ValueError('不得读取独立测试姿态调质量候选')
        pose=ROOT/'data/experiments/poses'/f"{baseline['extractions'][sample.key]['pose_key']}.npz"
        chosen.append({'action':action,'source':sample.absolute_path.relative_to(ROOT).as_posix(),'source_sha256':sample.sha256,'cache':pose,
            'view':'oblique' if 'diagonal' in case['view'] else 'side','expected_events':case['events'],'loader':lambda sample=sample:cached_frames(sample,baseline)})
    sources=json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources'];source=next(s for s in sources if s['id']=='dvids-551833')
    prior=json.loads((ROOT/'data/verification/step-14/reverse-lunge-development.json').read_text(encoding='utf-8'))
    chosen.append({'action':'reverse_lunge','source':source['path'],'source_sha256':source['sha256'],'cache':ROOT/'data/verification/step-14/dvids-551833-poses.npz',
        'view':'oblique','expected_events':prior['events'],'loader':lambda:movement_frames('dvids-551833')})
    return chosen


def diagnose():
    chosen=inputs();rules={c['action']:CandidateRule(SPECS[c['action']][0],SCHEMA,None,6.,100,500,'basic-range-observation-1',
        '阶段带6度／持续100ms／最大gap500ms为观测工程初值；没有质量判错阈值，不使用good/bad推断具体幅度') for c in chosen}
    signature={'module_sha256':hashlib.sha256((ROOT/'fitness_app/quality_candidates.py').read_bytes()).hexdigest(),
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'rules':{a:asdict(r) for a,r in rules.items()},
        'input_audit_sha256':hashlib.sha256((ROOT/'data/verification/step-14/content-bounds-validation.json').read_bytes()).hexdigest(),
        'inputs':[{'action':c['action'],'source':c['source'],'source_sha256':c['source_sha256'],'source_stat':(project_path(c['source']).stat().st_size,project_path(c['source']).stat().st_mtime_ns),
            'pose_cache':c['cache'].relative_to(ROOT).as_posix(),'cache_stat':(c['cache'].stat().st_size,c['cache'].stat().st_mtime_ns),
            'counting_version':get_action(c['action'])['capability_version']} for c in chosen]}
    identity=hashlib.sha256(json_text(signature).encode()).hexdigest()
    if OUT.exists():
        previous=json.loads(OUT.read_text(encoding='utf-8'))
        if previous['identity']==identity:
            print(json.dumps({'cache_hit':True,'new_pose_inference':False,'summary':previous['summary']},ensure_ascii=False));return
    cases=[]
    for case in chosen:
        action=case['action'];frames=case['loader']();counter=create_counter(action);events=[];results=[]
        for f in frames:
            if not f['framing']['usable']:
                counter.reset('tracking_lost');continue
            output=counter.process(f,{'stream_epoch':'quality-development','identity_accepted':f['identity_accepted']})
            for event in output['events']:
                side=event['side'] if event['side']!='none' else output['observation']['sides']['none'].get('measurement_side')
                result=evaluate_cycle(action,event,frames,measurement_side=side,view=case['view'],variant_confirmed=True,rule=rules[action])
                events.append(event);results.append(result)
        if events!=case['expected_events']:raise ValueError(f'{action}计数输出与引用证据不同，需先核查而非覆盖旧结论')
        if any(r['status'] in {'issue','not_observed'} or r['publishable'] for r in results):raise ValueError('无标签观测不得输出问题或正常结论')
        cases.append({'action':action,'source':case['source'],'view':case['view'],'context_basis':'来源机位和步骤12/14变式开发核查；不是自动机位／变式识别或独立真值',
            'frames':len(frames),'count_events_unchanged':True,'events':events,'candidates':results})
    summary={c['action']:{'cycles':len(c['events']),'measured_cycles':sum('value' in r for r in c['candidates']),
        'reasons':dict(Counter(r['reason'] for r in c['candidates']))} for c in cases}
    report={'schema_version':1,'identity':identity,'dependencies':signature,'cases':cases,'summary':summary,
        'missing_real_inputs':['bodyweight_glute_bridge','bent_knee_sit_up'],'quality_truth':'absent_for_all_six_specific_issues','formal_feedback_enabled':False,
        'scope':'新幅度观测至既有计数事件连接；4段开发缓存，不重新解码或提取姿态，不读取独立测试视频，不计算准确率'}
    OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cache_hit':False,'new_pose_inference':False,'summary':summary},ensure_ascii=False))


if __name__=='__main__':diagnose()
