"""17：两段开发硬拉与一段划船接新观测，既有粗标签不参与质量判定。"""
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
from fitness_app.research_data import load_dataset
from fitness_app.database import json_text
from fitness_app.barbell_quality import DeadliftExtensionRule,evaluate_deadlift_extension,observe_row_torso,LABEL_SOURCE
from diagnose_cycles import cached_frames
from diagnose_barbell_row import prepare_frames

OUT=ROOT/'data/verification/step-17/barbell-quality-development.json'


def diagnose():
    old=json.loads((ROOT/'data/verification/step-12/development-cycles.json').read_text(encoding='utf-8'))
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'));samples={s.path:s for s in load_dataset()}
    selected=[c for c in old['actions']['conventional_deadlift']['cases'] if c['events']]
    rule=DeadliftExtensionRule(None,None,8.,100,500,'deadlift-extension-observation-1','髋膝同阶段投影观测；8度／100ms／500ms为阶段工程初值，无质量阈值，不读取GB/PB作伸展标签')
    dependencies={'module_sha256':hashlib.sha256((ROOT/'fitness_app/barbell_quality.py').read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'rule':asdict(rule),'counting_versions':{a:get_action(a)['capability_version'] for a in ('conventional_deadlift','barbell_bent_over_row')},
        'audit_sha256':hashlib.sha256((ROOT/'data/verification/step-14/content-bounds-validation.json').read_bytes()).hexdigest(),
        'row_evidence_sha256':hashlib.sha256((ROOT/'data/verification/step-14/barbell-row-development.json').read_bytes()).hexdigest(),
        'label_reference':{'url':LABEL_SOURCE,'member':'supple/supplementary_material_0.pdf','page':2,'table':1,
            'local_path':'data/verification/step-17/fitness-aqa-supplementary.pdf','sha256':hashlib.sha256((ROOT/'data/verification/step-17/fitness-aqa-supplementary.pdf').read_bytes()).hexdigest()},'inputs':[]}
    for case in selected:
        sample=samples[case['source_path']]
        if sample.partition not in {'train','development'}:raise ValueError('不得使用独立测试样本选质量规则')
        pose=ROOT/'data/experiments/poses'/f"{baseline['extractions'][sample.key]['pose_key']}.npz"
        dependencies['inputs'].append({'path':sample.absolute_path.relative_to(ROOT).as_posix(),'sha256':sample.sha256,
            'source_stat':(sample.absolute_path.stat().st_size,sample.absolute_path.stat().st_mtime_ns),'pose_stat':(pose.stat().st_size,pose.stat().st_mtime_ns)})
    source=ROOT/'data/datasets/development-movements/barbell-row-dvids-639914-side.mp4';pose=ROOT/'data/verification/step-14/dvids-639914-side-poses.npz'
    dependencies['row_input_stat']={'source':(source.stat().st_size,source.stat().st_mtime_ns),'pose':(pose.stat().st_size,pose.stat().st_mtime_ns)}
    identity=hashlib.sha256(json_text(dependencies).encode()).hexdigest()
    if OUT.exists():
        prior=json.loads(OUT.read_text(encoding='utf-8'))
        if prior['identity']==identity:print(json.dumps({'cache_hit':True,'summary':prior['summary'],'new_pose_inference':False},ensure_ascii=False));return
    lifts=[]
    for case in selected:
        sample=samples[case['source_path']];frames=cached_frames(sample,baseline);counter=create_counter('conventional_deadlift');events=[];items=[]
        for f in frames:
            if not f['framing']['usable']:
                counter.reset('tracking_lost');continue
            output=counter.process(f,{'stream_epoch':'barbell-quality-development','identity_accepted':f['identity_accepted']})
            for event in output['events']:
                side=output['observation']['sides']['none']['measurement_side'];events.append(event)
                items.append(evaluate_deadlift_extension(event,frames,view='side',conventional_barbell_confirmed=True,measurement_side=side,rule=rule))
        if events!=case['events']:raise ValueError('硬拉周期与引用证据不一致')
        lifts.append({'source':case['source_path'],'source_class_ignored_for_extension':True,'count_events_unchanged':True,'events':events,'candidates':items})
    frames,_,hit=prepare_frames()
    if not hit:raise ValueError('划船已核图像范围应命中缓存')
    counter=create_counter('barbell_bent_over_row');row_events=[];observations=[]
    for f in frames:
        if not f['framing']['usable']:
            counter.reset('tracking_lost');continue
        before=counter.measurement_side;output=counter.process(f,{'stream_epoch':'barbell-quality-development','identity_accepted':f['identity_accepted']})
        side=counter.measurement_side or before;row_events.extend(output['events'])
        if side and counter.tracker.phase in {'ready','departing','moving','returning','confirming_return'}:
            observations.append(observe_row_torso(f,view='oblique',barbell_row_confirmed=True,row_phase_confirmed=True,measurement_side=side,orientation_up=(0,-1)))
    row_prior=json.loads((ROOT/'data/verification/step-14/barbell-row-development.json').read_text(encoding='utf-8'))
    if row_events!=row_prior['events']:raise ValueError('划船周期与引用证据不一致')
    row=[]
    for event in row_events:
        values=[o for o in observations if event['start_ms']<=o.get('at_ms',-1)<=event['end_ms'] and 'torso_tilt_from_up_degrees' in o]
        row.append({'event':event,'observable_frames':len(values),'tilt_range_from_up_degrees':[min(o['torso_tilt_from_up_degrees'] for o in values),max(o['torso_tilt_from_up_degrees'] for o in values)] if values else None,
            'sampled_observations':values[::6],'status':'unassessable','reason':'label_angle_reference_and_threshold_unvalidated','publishable':False})
    summary={'deadlift_cycles':sum(len(c['events']) for c in lifts),'deadlift_reasons':dict(Counter(i['reason'] for c in lifts for i in c['candidates'])),
        'row_cycles':len(row),'row_observable_frames':sum(c['observable_frames'] for c in row),'press':'no_matching_full_standing_frontal_input'}
    report={'schema_version':1,'identity':identity,'dependencies':dependencies,'deadlift':lifts,'row':row,'summary':summary,
        'formal_feedback_enabled':False,'quality_truth':'no_independent_labels_for_the_three_minimum_issues',
        'scope':'新观测接既有3段开发姿态，不重新解码／推理，不训练／重跑GB-PB实验，不用周期数证明质量正确'}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps({'cache_hit':False,'summary':summary,'new_pose_inference':False},ensure_ascii=False))


if __name__=='__main__':diagnose()
