"""纠错低帧率前后对照与实际能力清单，未验证项如实保留。"""
import copy,hashlib,inspect,json,os,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
os.environ.update(FITNESS_BODY_LEAN='1',FITNESS_MOVEMENT_GUIDANCE='1',FITNESS_PRETRAINED_CORRECTION='0')
from fitness_app import gym_motion,gym_quality
from fitness_app.gym_quality import evaluate
from fitness_app.catalogue import definitions
from fitness_app.posture_rules import rules_for,policy_version
from fitness_app.research_extract import atomic_json
from scripts.evaluate_pose_trainer_quality import EXPECTED_SOURCE,SOURCE_ROOT,source_bundle,sha,frames_from_sequence

def previous_evaluator():
    source=inspect.getsource(gym_motion).replace('def measurements(frames,require_wrist=True,*,minimum_frames=8):','def measurements(frames,require_wrist=True):').replace('len(frames)<minimum_frames','len(frames)<8').replace('len(rows)<minimum_frames','len(rows)<8')
    ns={'__name__':'fitness_app._previous_motion','__package__':'fitness_app'};exec(compile(source,'previous_motion_reference','exec'),ns)
    source=inspect.getsource(gym_quality)
    start=source.index("    intervals=[b['captured_ms']-a['captured_ms']")
    end=source.index('    # 自动估计',start)
    source=source[:start]+'    values=measurements(frames)\n    if not values:return missing\n'+source[end:]
    source=source.replace('values=measurements(frames[-continuous:],minimum_frames=minimum_frames)','values=measurements(frames[-continuous:])').replace('continuous<minimum_frames','continuous<8').replace("row['stamp']-last>500","row['stamp']-last>250")
    start=source.index("            'observed_frames':len(frames)");end=source.index("            'feature_name':feature",start);source=source[:start]+source[end:]
    old={'__name__':'fitness_app._previous_quality','__package__':'fitness_app'};exec(compile(source,'previous_quality_reference','exec'),old);old['measurements']=ns['measurements']
    return old['evaluate']

def main():
    actual={'license_sha256':sha(SOURCE_ROOT/'LICENSE'),'readme_sha256':sha(SOURCE_ROOT/'README.md'),
        'pose_sha256':sha(SOURCE_ROOT/'pose.py'),'utils_sha256':sha(SOURCE_ROOT/'utils.py'),**source_bundle()}
    if actual!=EXPECTED_SOURCE:raise ValueError('公开轨迹固定摘要改变')
    previous=previous_evaluator();reviews=[]
    for folder,action,codes in [('bicep','standing_dumbbell_curl',['curl.trunk_sway','curl.upper_arm_swing']),
            ('shoulderpress','dumbbell_shoulder_press',['dumbbell_shoulder_press.trunk_sway']),
            ('frontraise','dumbbell_front_raise',['dumbbell_front_raise.trunk_sway'])]:
        for path in sorted((SOURCE_ROOT/'poses_compressed'/folder).glob('*.npy')):
            frames=frames_from_sequence(path)
            for cadence in (33,200,333,400):
                chosen=[];last=None
                for frame in frames:
                    if last is not None and frame['captured_ms']-last<cadence:continue
                    chosen.append(frame);last=frame['captured_ms']
                result={}
                for name,fn in [('before',previous),('after',evaluate)]:
                    counts=Counter();seen=set()
                    for index,current in enumerate(chosen):
                        window=[f for f in chosen[:index+1] if current['captured_ms']-f['captured_ms']<=2400]
                        for code in codes:
                            threshold=next(r['threshold'] for r in rules_for(action) if r['code']==code)
                            item=fn(code,window,'side',threshold,require_continuous=action=='standing_dumbbell_curl')
                            counts[code+':'+item['status']]+=1
                            if item['status']=='issue':seen.add(code)
                    result[name]={'counts':dict(counts),'issue_codes':sorted(seen)}
                reviews.append({'id':path.relative_to(SOURCE_ROOT).as_posix(),'sha256':sha(path),'action':action,
                    'label':'good' if 'good' in path.name else 'bad','cadence_ms':cadence,'conditions':result})
    actions=[]
    for action in definitions():
        rules=rules_for(action['action_id'])
        actions.append({'action_id':action['action_id'],'name':action['name'],'reference_rules':[
            {k:r[k] for k in ('code','title','views','validation_status')} for r in rules],
            'all_error_types_verified':False})
    report={'scope':'open_source_coarse_trajectory_regression_not_specific_error_truth','policy_version':policy_version(),
        'source_verified_by_frozen_byte_hashes':True,'source_registry_missing':not (ROOT/'third_party/sources.json').exists(),
        'new_pose_inferences':0,'private_media_uploaded':False,'paid_api_calls':0,'actions':actions,'reviews':reviews,
        'implementation_sha256':{n:sha(ROOT/'fitness_app'/n) for n in ('gym_quality.py','gym_motion.py','posture_engine.py')}}
    atomic_json(ROOT/'data/verification/round3/correction-coverage-review.json',report)
    for cadence in (33,200,333,400):
        rows=[r for r in reviews if r['cadence_ms']==cadence]
        print(json.dumps({'cadence':cadence,'conditions':{name:{label:{'n':sum(r['label']==label for r in rows),
            'with_issue':sum(r['label']==label and bool(r['conditions'][name]['issue_codes']) for r in rows)} for label in ('good','bad')} for name in ('before','after')}}),flush=True)
    print('actions',len(actions),'rules',sum(len(a['reference_rules']) for a in actions),flush=True)

if __name__=='__main__':main()
