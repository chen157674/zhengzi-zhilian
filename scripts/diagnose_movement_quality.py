"""16：新增质量观测接两段既有公开开发姿态，无新解码／推理／独立效果预测。"""
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app.counting_resources import create_counter
from fitness_app.catalogue import get_action
from fitness_app.database import json_text
from fitness_app.movement_quality import PlankHipRule,DynamicRangeRule,evaluate_plank,evaluate_dynamic
from fitness_app.jumping_jack import jack_observation
from prepare_movement_input import cached_frames

OUT=ROOT/'data/verification/step-16/movement-quality-development.json'


def diagnose():
    definitions=[('forearm_plank','dvids-640237-side','plank-hold-development.json'),('jumping_jack','commons-jumping-jacks-segment','jumping-jack-development.json')]
    rules={'forearm_plank':PlankHipRule(None,None,500,500,'plank-hip-observation-1','有向肩髋踝投影观测；持续500ms为工程初值，无高低髋质量阈值'),
        'jumping_jack.arm_range':DynamicRangeRule('jumping_jack.arm_range',None,10.,33,200,'jack-arm-observation-1','完整周期最大开合阶段观测；10度阶段带／33ms为工程初值，无质量阈值'),
        'jumping_jack.leg_range':DynamicRangeRule('jumping_jack.leg_range',None,.05,33,200,'jack-leg-observation-1','完整周期最大开合阶段观测；0.05腿长阶段带／33ms为工程初值，无质量阈值')}
    sources=json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources']
    dependency={'module_sha256':hashlib.sha256((ROOT/'fitness_app/movement_quality.py').read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'rules':{k:asdict(v) for k,v in rules.items()},'input_audit_sha256':hashlib.sha256((ROOT/'data/verification/step-14/content-bounds-validation.json').read_bytes()).hexdigest(),'sources':[]}
    for action,sid,prior in definitions:
        source=next(s for s in sources if s['id']==sid);file=ROOT/source['path'];cache=ROOT/f'data/verification/step-14/{sid}-poses.npz'
        dependency['sources'].append({'source_id':sid,'sha256':source['sha256'],'source_stat':(file.stat().st_size,file.stat().st_mtime_ns),
            'cache_stat':(cache.stat().st_size,cache.stat().st_mtime_ns),'capability_version':get_action(action)['capability_version'],
            'prior_sha256':hashlib.sha256((ROOT/'data/verification/step-14'/prior).read_bytes()).hexdigest()})
    identity=hashlib.sha256(json_text(dependency).encode()).hexdigest()
    if OUT.exists():
        old=json.loads(OUT.read_text(encoding='utf-8'))
        if old['identity']==identity:print(json.dumps({'cache_hit':True,'summary':old['summary'],'new_pose_inference':False},ensure_ascii=False));return
    cases={}
    for action,sid,prior_name in definitions:
        frames=cached_frames(sid);counter=create_counter(action);prior=json.loads((ROOT/'data/verification/step-14'/prior_name).read_text(encoding='utf-8'))
        intervals=[];events=[];bindings=[];references=[];last_closed=None
        for f in frames:
            if not f['framing']['usable']:
                counter.reset('tracking_lost');last_closed=None;continue
            if action=='jumping_jack':
                observation=jack_observation(f,counter.config)
                if observation and observation['closed']:last_closed=f
                if counter.tracker.phase=='ready' and last_closed is not None:reference=last_closed
            output=counter.process(f,{'stream_epoch':'movement-quality-development','identity_accepted':f['identity_accepted']})
            for item in output.get('intervals',[]):
                intervals.append(item);bindings.append((item,output['observation']['sides']['none']['measurement_side']))
            for event in output['events']:
                events.append(event);references.append(reference if 'reference' in locals() else None)
        if action=='forearm_plank':
            if intervals!=prior['intervals']:raise ValueError('支撑区间与引用证据不同，先核查')
            groups=[]
            for item,side in bindings:
                if groups and groups[-1]['side']==side and groups[-1]['intervals'][-1]['end_ms']==item['start_ms']:groups[-1]['intervals'].append(item)
                else:groups.append({'side':side,'intervals':[item]})
            candidates=[evaluate_plank(frames,g['intervals'],measurement_side=g['side'],view='side',orientation_up=(0,-1),orientation_confirmed=True,rule=rules[action]) for g in groups]
            if sum(c['hold_ms'] for c in candidates)!=prior['hold_ms']:raise ValueError('质量观测不得改写保持量')
            cases[action]={'source_id':sid,'hold_ms':prior['hold_ms'],'intervals_unchanged':True,'candidates':candidates}
        else:
            if events!=prior['events']:raise ValueError('开合周期与引用证据不同，先核查')
            candidates=[]
            for event,reference in zip(events,references):
                candidates.append({'event':event,'reference_at_ms':reference['captured_ms'] if reference else None,'items':[
                    evaluate_dynamic(event,frames,reference,view='front',variant_confirmed=True,orientation_up=(0,-1),orientation_confirmed=True,rule=rules[code])
                    for code in ('jumping_jack.arm_range','jumping_jack.leg_range')]})
            cases[action]={'source_id':sid,'events_unchanged':True,'cycles':len(events),'candidates':candidates}
    summary={'forearm_plank':{'hold_ms':cases['forearm_plank']['hold_ms'],'support_groups':len(cases['forearm_plank']['candidates']),
        'quality_intervals':{code:sum(len(c['intervals'][code]) for c in cases['forearm_plank']['candidates']) for code in ('plank.hip_high','plank.hip_low')}},
        'jumping_jack':{'cycles':cases['jumping_jack']['cycles'],'candidate_reasons':dict(Counter(i['reason'] for c in cases['jumping_jack']['candidates'] for i in c['items']))}}
    report={'schema_version':1,'identity':identity,'dependencies':dependency,'cases':cases,'summary':summary,
        'scope':'两段既有开发姿态的新质量观测连接，不重新解码／推理，不读取独立测试集','formal_feedback_enabled':False,
        'orientation_context':'既有原片直立方向已核，向上为图像负y；非自动相机姿态估计','quality_truth':'absent_for_all_five_issues','missing_real_input':'stationary_alternating_high_knees'}
    OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cache_hit':False,'summary':summary,'new_pose_inference':False},ensure_ascii=False))


if __name__=='__main__':diagnose()
