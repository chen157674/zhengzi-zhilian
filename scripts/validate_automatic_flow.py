"""既有真实姿态的连续自动链诊断；候选仅在离线显式加载，不改变产品门槛。"""
import hashlib
import json
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import joblib
from fitness_app.automatic_recognition import ActionRecognizer,CORE_ACTIONS,MODEL,MANIFEST,CONFIG
from fitness_app.automatic_analysis import AutomaticAnalysis
from fitness_app.frame_pipeline import framing,analysis_framing
from fitness_app.recognition_inputs import extra_inputs,extra_frames
from fitness_app.research_data import load_dataset
from fitness_app.research_extract import atomic_json
from diagnose_cycles import cached_frames

REPORT=ROOT/'data/verification/step-19/automatic-flow.json'


def run():
    manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
    if manifest['config']!=CONFIG or hashlib.sha256(MODEL.read_bytes()).hexdigest()!=manifest['sha256']:
        raise ValueError('候选模型身份不一致')
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'))
    samples=load_dataset();inputs=[]
    for action in CORE_ACTIONS:
        options=[s for s in samples if s.action_id==action and (s.partition=='validation' or s.partition=='development' and s.validation_fold==3)]
        sample=sorted(options,key=lambda s:s.key)[0]
        inputs.append({'key':sample.key,'action':action,'role':'heldout_clip_label','sha256':sample.sha256,
                       'frames':cached_frames(sample,baseline,apply_bounds=False)})
    for entry in extra_inputs():
        if entry['partition']=='validation' or entry['action'] in {'reverse_lunge','forearm_plank','jumping_jack'}:
            inputs.append({'key':entry['key'],'action':entry['action'],'sha256':entry['source_sha256'],
                'role':'heldout_clip_label' if entry['partition']=='validation' else 'training_source_engineering_only',
                'frames':extra_frames(entry)})
    identity={'model_sha256':manifest['sha256'],'config':CONFIG,
        'inputs':[{k:v for k,v in entry.items() if k!='frames'} for entry in inputs],
        'code':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'scripts/validate_automatic_flow.py','fitness_app/automatic_recognition.py','fitness_app/automatic_analysis.py',
            'fitness_app/preview.py','fitness_app/coaching.py','fitness_app/frame_pipeline.py','fitness_app/posture_engine.py')}}
    signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    if REPORT.exists():
        old=json.loads(REPORT.read_text(encoding='utf-8'))
        if old.get('signature')==signature:
            print(json.dumps({'cache_hit':True,'clips':len(old['clips'])}));return
        raise ValueError('诊断实现已变化；核对旧证据后再明确安排受影响验证，不自动覆盖')
    model=joblib.load(MODEL);results=[]
    for entry in inputs:
        recognizer=ActionRecognizer()
        # 仅此离线命令加载失败候选，用于找失败原因；HTTP与正式启动不能绕过门槛。
        recognizer.model=model;recognizer.version=manifest['version'];recognizer.reason=None
        router=AutomaticAnalysis();states=Counter();confirmed=Counter();reasons=Counter();cues=Counter()
        first=None;segments=set();routed=0
        for frame in entry['frames']:
            q=frame['framing'];count=q['person_count']
            poses=[q['landmarks']] if count==1 else [[] for _ in range(count)]
            detected={'poses':poses,'content_bounds':q.get('content_bounds',[0,0,1,1])}
            generic={**frame,'framing':framing('bodyweight_squat',detected)}
            recognition=recognizer.process(generic)
            action=recognition['action_id']
            analysis_frame={**frame,'framing':analysis_framing(action,detected) if action else generic['framing']}
            output=router.process(analysis_frame,recognition)
            states[recognition['state']]+=1;reasons[recognition.get('reason') or 'accepted']+=1
            if action:
                confirmed[action]+=1
                if first is None:first=frame['captured_ms']-entry['frames'][0]['captured_ms']
            if output['automatic_analysis']['state']=='analyzing':
                routed+=1;segments.add(output['automatic_analysis']['segment_id'])
            cue=output['coaching']['primary_cue']
            if cue:cues[cue['code']]+=1
        result={k:v for k,v in entry.items() if k!='frames'}
        result.update(frames=len(entry['frames']),recognition_states=dict(states),confirmed_actions=dict(confirmed),
            reasons=dict(reasons),first_confirmation_ms=first,analyzed_frames=routed,analysis_segments=len(segments),
            cue_frames=dict(cues),independent_specific_error_truth=False)
        results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
    atomic_json(REPORT,{'signature':signature,'identity':identity,'clips':results,'test_used':False,
        'new_pose_inference_frames':0,'model_retrained':False,'production_model_enabled':False,
        'scope':'Continuous replay of existing pose caches; selected by key, not outcomes. Training sources are engineering diagnostics only. Clip action labels are not frame-level correctness truth. No natural twelve-action transition acceptance.'})


if __name__=='__main__':run()
