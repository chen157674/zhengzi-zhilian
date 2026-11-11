"""复用真实帧数值缓存重放新规则；不重复姿态/手部推理。"""
import json,os,sys
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
os.environ.update(FITNESS_BODY_LEAN='1',FITNESS_MOVEMENT_GUIDANCE='1',FITNESS_PRETRAINED_CORRECTION='0')
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.posture_rules import policy_version
from fitness_app.coaching import coaching_snapshot,coaching_observation,committed_issues
from fitness_app.demo_feedback import build_demo_feedback
from fitness_app.frame_pipeline import analysis_framing
from scripts.prepare_pretrained_correction import sha,atomic_json
from fitness_app.movement_guidance import MovementGuidance

def run(refresh=False):
    policy_version.cache_clear();target=ROOT/'data/verification/round3/movement-guidance-review.json'
    report=json.loads(target.read_text(encoding='utf-8'));mask=json.loads((ROOT/'data/verification/round3/movement-guidance-scene-review.json').read_text(encoding='utf-8'))
    assets_path=ROOT/'data/presentation-assets.json';assets=json.loads(assets_path.read_text(encoding='utf-8'))
    previous={k:report[k] for k in ('policy_version','videos')};report.setdefault('previous_replays',[]).append({'policy_version':previous['policy_version'],
        'video_counts':[{k:v.get(k) for k in ('number','status_counts','visible_feedback_counts')} for v in previous['videos']]})
    for video in report['videos']:
        n=video['number'];asset=assets['demos']['user-'+n];engine=create_analysis_engine(asset['action_id']);status=Counter();visible=Counter();last=[];last_stamp=0;demo=[]
        old_scene=next(v for v in mask['videos'] if v['number']==n);features={f['t']:f.get('feature') for f in old_scene['frames']}
        for i,row in enumerate(video['frames']):
            t=row['t'];good=row['identity_accepted'];points=row.get('original_landmarks')
            if points is None:raise ValueError('缓存没有原姿态，不能假装真实回放')
            body_features=row.get('body_features') or {}
            if features.get(t):body_features={**body_features,'scene_lean':features[t]}
            data={'status':'observed','timestamp_ms':t,'body':row.get('body'),'body_features':body_features,'hands':row.get('hands',[]),
                'body_model_sha256':(row.get('body') or {}).get('model_sha256')}
            if row.get('body') is None:data['status']='unassessable'
            frame={'captured_ms':t,'frame_seq':i,'width':asset['width'],'height':asset['height'],'identity_accepted':good,
                'framing':{'usable':good,'person_count':row['base_people'],'code':row['base_framing'],'landmarks':points},'pretrained':data}
            if good:output=engine.process(frame,{'coaching_view':'auto','stream_epoch':'cached-guidance-'+n})
            else:engine.reset('identity_unavailable');output={}
            items=output.get('observation',{}).get('posture',{}).get('items',[])
            for item in items:status[item['code']+':'+item['status']]+=1
            pending=committed_issues(output)
            if pending:last=pending;last_stamp=t
            frame['analysis']=coaching_observation(output)
            cue=coaching_snapshot({'status':'running','action_id':asset['action_id'],'supported_items':engine.rules},frame,last,t-last_stamp)
            feedback=build_demo_feedback(frame,output,engine.rules,cue)
            if feedback['state']=='clear' and n!='1575':feedback['clear_label']='你标记的问题尚未检出' if asset.get('user_reported_issues') else '已检查项目未见偏差'
            row.update(body_features=body_features,items=items,cue_state=cue['state'],cue_code=(cue.get('primary_cue') or {}).get('code'),
                       visible_feedback_state=feedback['state'],highlighted=feedback.get('highlight') is not None)
            visible[feedback['state']]+=1
            demo.append({'t':t,'p':[[round(p['x'],6),round(p['y'],6)] if p.get('valid') else None for p in points] if len(points)==33 and good else [None]*33,
                         'people':row['base_people'],'cue':cue.get('primary_cue'),'feedback':feedback})
        video.update(status_counts=dict(status),visible_feedback_counts=dict(visible))
        if refresh:
            path=ROOT/asset['track']['path']
            if sha(path)!=asset['track']['sha256']:raise ValueError('原演示轨迹已变化')
            original=json.loads(path.read_text(encoding='utf-8'))
            original.update(frames=demo,rule_version=engine.version,frames_count=len(demo),visible_frames=sum(any(p is not None for p in f['p']) for f in demo),
                posture_status_counts=dict(status),feedback_state_counts=dict(visible),movement_guidance=True,body_lean_only=False,
                feedback_implementation_sha256=sha(ROOT/'fitness_app/demo_feedback.py'),new_pose_inferences=0,
                replay_basis='same_source_timestamp_aligned_real_pose_hand_cache',validation_report=target.relative_to(ROOT).as_posix())
            atomic_json(path,original);asset.update(track={**asset['track'],'bytes':path.stat().st_size,'sha256':sha(path)},rule_version=engine.version,frames=len(demo),visible_frames=original['visible_frames'])
            atomic_json(assets_path,assets)
        print(json.dumps({'number':n,'status_counts':dict(status),'visible':dict(visible)},ensure_ascii=False),flush=True)
    report.update(policy_version=policy_version(),replay_script_sha256=sha(Path(__file__)),new_pose_inferences=0,
                  replay_basis='original_source_aligned_numeric_inputs_and_new_scene_reference',movement_guidance=True)
    atomic_json(target,report)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--refresh-demos',action='store_true');run(p.parse_args().refresh_demos)