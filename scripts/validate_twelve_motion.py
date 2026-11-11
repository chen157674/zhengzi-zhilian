"""十二类姿态补充在现有真实缓存上的诊断；不重新推理、不把缺数据当通过。"""
import json,sys,hashlib
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app.basic_recognition import BasicRecognizer
from fitness_app.automatic_recognition import CORE_ACTIONS
from fitness_app.recognition_inputs import extra_inputs,extra_frames
from fitness_app.research_data import load_dataset
from fitness_app.research_extract import atomic_json
from diagnose_cycles import cached_frames

REPORT=ROOT/'data/verification/step-19/twelve-motion.json'


def run(actions=None):
    identity={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
        'fitness_app/motion_recognition.py','fitness_app/basic_recognition.py','scripts/validate_twelve_motion.py')}
    old=None
    if REPORT.exists():
        old=json.loads(REPORT.read_text(encoding='utf-8'))
        if old['identity']==identity:print('cache_hit');return
        if not actions:raise ValueError('原诊断已存在，先通过--actions明确受影响范围')
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'));samples=load_dataset();inputs=[]
    for action in CORE_ACTIONS:
        selected=sorted([s for s in samples if s.action_id==action and (s.partition=='validation' or s.partition=='development' and s.validation_fold==3)],key=lambda s:s.key)[0]
        inputs.append((action,selected.key,'heldout_source',cached_frames(selected,baseline,apply_bounds=False)))
    for e in extra_inputs():inputs.append((e['action'],e['source_id'],e['partition'],extra_frames(e)))
    inputs.append(('bent_knee_sit_up','dvids-767109-target','dataset_target_annotation_not_live_multi_person',
                   extra_frames({'cache':'data/verification/step-14/sit-up-dvids-767109-target-poses.npz'})))
    results=[]
    for action,key,role,frames in inputs:
        if actions and action not in actions:continue
        recognizer=BasicRecognizer();confirmed=Counter();reasons=Counter();first=None
        for f in frames:
            result=recognizer.process(f)
            reasons[result.get('reason') or result['state']]+=1
            if result['action_id']:
                confirmed[result['action_id']]+=1
                if first is None:first=f['captured_ms']
        entry={'action':action,'key':key,'role':role,'frames':len(frames),'confirmed_actions':dict(confirmed),'reasons':dict(reasons),'first_confirmation_ms':first,'implementation_identity':identity}
        results.append(entry);print(json.dumps(entry,ensure_ascii=False),flush=True)
    if old:
        replaced={r['key'] for r in results}
        results += [{**r,'implementation_identity':r.get('implementation_identity',old['identity'])} for r in old['clips'] if r['key'] not in replaced]
    report={'identity':identity,'clips':results,'new_pose_inference_frames':0,'new_visual_inference_frames':0,
        'classifier':'temporal geometry path only; learned image features not supplied',
        'missing_real_input_classes':['bodyweight_glute_bridge','high_knees'],
        'scope':'12 engineering-class paths tested separately; these existing real caches do not establish independent twelve-class accuracy or live multi-person target selection'}
    if old:report['prior_runs']=old.get('prior_runs',[])+[{k:v for k,v in old.items() if k!='prior_runs'}]
    atomic_json(REPORT,report)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--actions',nargs='+')
    run(parser.parse_args().actions)
