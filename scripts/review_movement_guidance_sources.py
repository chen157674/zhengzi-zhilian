"""复用已取得的原始轨迹和真实视频输入缓存，核查背景遮罩与新增规则。"""
import argparse,json,sys,os
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from scripts.prepare_pretrained_correction import atomic_json,sha


def coarse():
    from scripts.evaluate_pose_trainer_quality import verify_source,frames_from_sequence,SOURCE_ROOT
    from fitness_app.movement_guidance import MovementGuidance
    verify_source();rows=[]
    for folder,action in [('bicep','standing_dumbbell_curl'),('shoulderpress','dumbbell_shoulder_press'),('frontraise','dumbbell_front_raise')]:
        for path in sorted((SOURCE_ROOT/'poses_compressed'/folder).glob('*.npy')):
            observer=MovementGuidance(action,'public-coarse-fixture');counts=Counter();issues=[]
            for frame in frames_from_sequence(path):
                frame['framing']['usable']=True
                items,findings=observer.update(frame,{})
                for item in items:counts[item['code']+':'+item['status']]+=1
                issues.extend(item for item in findings if item['status']=='issue')
            rows.append({'file':path.relative_to(SOURCE_ROOT).as_posix(),'sha256':sha(path),'action_id':action,'label':'good' if 'good' in path.name else 'bad',
                         'counts':dict(counts),'issue_codes':sorted({i['code'] for i in issues})})
    report={'scope':'original_good_bad_trajectories_not_specific_error_truth','fps':'33ms_engineering_clock_original_fps_not_provided',
            'rows':rows,'new_pose_inferences':0,'script_sha256':sha(Path(__file__))}
    atomic_json(ROOT/'data/verification/round3/movement-guidance-coarse-review.json',report)
    for action in sorted({r['action_id'] for r in rows}):
        selected=[r for r in rows if r['action_id']==action]
        print(action,{label:{'n':sum(r['label']==label for r in selected),'any_issue':sum(r['label']==label and bool(r['issue_codes']) for r in selected)} for label in ('good','bad')},flush=True)


def scene():
    from fitness_app.body_lean import SceneAxisTracker,measure
    from fitness_app.media import video_frames,probe_video
    from fitness_app.pretrained_quality import PretrainedObserver
    cache=ROOT/'data/verification/round3/movement-guidance-review.json';r=json.loads(cache.read_text(encoding='utf-8'))
    assets=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'));reports=[]
    for video in r['videos']:
        asset=assets['demos']['user-'+video['number']];source=ROOT/asset['video']['path']
        if sha(source)!=video['source_sha256']:raise ValueError('原视频身份不符')
        by={f['t']:f for f in video['frames']};tracker=SceneAxisTracker()
        observer=PretrainedObserver('machine_chest_press','background-mask-review',codes={'machine_chest_press.pretrained_upper_body_balance'})
        counts=Counter();rows=[]
        for stamp,rgb in video_frames(source,probe_video(source)):
            if stamp not in by:continue
            frame=by[stamp];body=frame.get('body')
            if not body or not frame['identity_accepted']:
                tracker.reset();observer.reset();rows.append({'t':stamp,'status':'unassessable','reason':'base_identity_unavailable'});continue
            feature=measure(body,rgb.shape[1],rgb.shape[0],tracker.update(rgb,body,stamp),frame['original_landmarks'])
            item=observer.update({'captured_ms':stamp,'pretrained':{'status':'observed','timestamp_ms':stamp,'body_features':{'scene_lean':feature},
                'body_model_sha256':body['model_sha256']}},{'coaching_view':'auto'})[0]
            counts[item['status']]+=1;rows.append({'t':stamp,'status':item['status'],'feature':feature})
        reports.append({'number':video['number'],'status_counts':dict(counts),'frames':rows});print(video['number'],dict(counts),flush=True)
    atomic_json(ROOT/'data/verification/round3/movement-guidance-scene-review.json',{
        'scope':'same_cached_body_points_new_background_mask_not_independent_truth','videos':reports,
        'source_pose_cache':'movement-guidance-review.json','body_lean_implementation_sha256':sha(ROOT/'fitness_app/body_lean.py'),
        'script_sha256':sha(Path(__file__)),'new_pose_inferences':0})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['coarse','scene']);args=p.parse_args()
    coarse() if args.stage=='coarse' else scene()