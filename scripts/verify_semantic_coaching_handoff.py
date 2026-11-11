"""真实视频缓存的分类到纠错交接检查；不把粗bad标签或本次提示当独立错误真值。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.world_features import WorldFeatures
from fitness_app.automatic_analysis import AutomaticAnalysis
from fitness_app.frame_pipeline import analysis_framing
from fitness_app.skeleton import skeleton_snapshot
from fitness_app.research_extract import atomic_json
from evaluate_video_vlm import pose_cache_path
from evaluate_xclip import OUT


def run():
    data=json.loads((OUT/'semantic-video-features.json').read_text(encoding='utf-8'));model=SemanticPredictor(research=True);world_encoder=WorldFeatures();attempts=[];examples=[]
    entries=sorted([r for r in data['entries'] if r['partition']=='validation' and '/bad/' in r['path'] and r['action_id'] in
                    {'bodyweight_squat','standing_dumbbell_curl','standard_push_up'}],key=lambda r:r['id'])
    for entry in entries:
        with np.load(project_path(entry['cache']),allow_pickle=False) as c:embedding=c['embedding'].copy();visual=json.loads(str(c['prediction']));times=c['timestamps'].copy()
        with np.load(OUT/(entry['id']+'-full-world-features.npz'),allow_pickle=False) as c:world,visibility=world_encoder.extract({k:c[k] for k in ('timestamps','world','people')},times[0],times[-1])
        recognition=model.from_features(embedding,visual,world,visibility)
        if recognition['action_id']!=entry['action_id']:attempts.append({'id':entry['id'],'reason':'classifier_did_not_confirm_expected_clip_type'});continue
        recognition.update(available=True,state='recognized');router=AutomaticAnalysis();found=None;observed=0
        with np.load(pose_cache_path(entry),allow_pickle=False) as c:
            stamps=c['timestamps_ms'] if 'timestamps_ms' in c.files else c['timestamps'];people=c['person_counts'] if 'person_counts' in c.files else c['people']
            for i,stamp in enumerate(stamps):
                # 仅在真实分类窗口结束并预留推理/确认时间后交接，不将后面画面作为过去证据。
                if stamp<times[-1]+3000:continue
                p=[dict(zip(('x','y','z','visibility','presence'),map(float,v))) for v in c['points'][i]]
                detected={'poses':[p] if people[i]==1 else [[] for _ in range(int(people[i]))]}
                if 'bounds' in c.files:detected['content_bounds']=c['bounds'][i].tolist()
                width,height=map(int,c['sizes'][i]);frame={'frame_seq':i,'captured_ms':int(stamp),'width':width,'height':height,
                    'framing':analysis_framing(recognition['action_id'],detected)}
                output=router.process(frame,recognition);observed+=1;coaching=output['coaching'];cue=coaching.get('primary_cue')
                if cue and cue.get('message') and cue.get('suggestion'):
                    skeleton=skeleton_snapshot(frame,coaching,time_base='source_ms')
                    if not skeleton.get('highlight'):continue
                    found={'id':entry['id'],'source_sha256':entry['source_sha256'],'action_id':recognition['action_id'],'after_classifier_source_ms':int(times[-1]),
                           'at_source_ms':int(stamp),'cue':cue,'highlight':skeleton['highlight'],'visible_joint_count':len(skeleton['joints'])}
                    break
        attempts.append({'id':entry['id'],'frames_after_handoff':observed,'cue_with_body_region_and_suggestion':found is not None})
        if found:examples.append(found);break
    report={'schema_version':1,'head_sha256':model.info['sha256'],'model_version':model.info['version'],'passed':bool(examples),'examples':examples,'attempts':attempts,
            'new_video_inferences':0,'new_pose_inferences':0,'hardware_camera_opened':False,'independent_posture_accuracy':None,
            'scope':'Engineering handoff on original real pose caches after an actual cached video classification window. A source bad label does not establish the specific error; geometric reference feedback is not expert ground truth.'}
    atomic_json(OUT/'semantic-coaching-handoff.json',report)
    print(json.dumps({'passed':report['passed'],'checked_clips':len(attempts),'examples':[{k:v for k,v in e.items() if k in ('id','action_id','at_source_ms','cue')} for e in examples]},ensure_ascii=False),flush=True)


if __name__=='__main__':run()
