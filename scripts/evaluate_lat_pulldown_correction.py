"""高位下拉真实输入复核；缓存数值后可重放，用户整片描述不充当逐帧独立真值。"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
os.environ.update(FITNESS_BODY_LEAN='1',FITNESS_MOVEMENT_GUIDANCE='0',FITNESS_PRETRAINED_CORRECTION='0')
from fitness_app.gym_pose import GymPoseService
from fitness_app.frame_pipeline import analysis_framing
from fitness_app.media import probe_video,video_frames
from fitness_app.pretrained_quality import PretrainedObserver
from fitness_app.posture_rules import policy_version
from fitness_app.research_extract import atomic_json
from scripts.prepare_user_demos import digest

REPORT=ROOT/'data/verification/round3/lat-pulldown-correction-review.json'


def collect():
    manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    document={'schema_version':1,'scope':'user_confirmed_development_pair_not_independent_accuracy',
        'private_media_uploaded':False,'paid_api_calls':0,'videos':[]}
    for number in ('1688','1689'):
        item=manifest['demos']['user-'+number];video=ROOT/item['video']['path']
        if digest(video)!=item['video']['sha256']:raise ValueError('输入身份改变')
        rows=[];last=-1000
        with GymPoseService(video=True) as model:
            for stamp,rgb in video_frames(video,probe_video(video)):
                if stamp-last<95:continue
                last=stamp;output=model.detect(rgb,stamp);framing=analysis_framing('lat_pulldown',output)
                rows.append({'t':stamp,'width':rgb.shape[1],'height':rgb.shape[0],
                    'framing':framing,'pretrained':output.get('pretrained')})
        document['videos'].append({'number':number,'source_sha256':digest(video),'action_id':'lat_pulldown',
            'user_reported_quality':item['user_reported_quality'],'rows':rows})
        atomic_json(REPORT,document)
        print(json.dumps({'collected':number,'frames':len(rows)}),flush=True)
    return document


def replay(document):
    document['rule_version']=policy_version()
    for video in document['videos']:
        observer=PretrainedObserver(video['action_id'],document['rule_version']);counts=Counter()
        geometry=Counter();ranges=[];opened=None
        for row in video['rows']:
            frame={'captured_ms':row['t'],'width':row['width'],'height':row['height'],
                   'framing':row['framing'],'pretrained':row['pretrained']}
            items=observer.update(frame,{'coaching_view':'auto'})
            row['items']=items
            for item in items:counts[item['code']+':'+item['status']]+=1
            feature=(row['pretrained'] or {}).get('body_features',{}).get('scene_lean',{})
            geometry[feature.get('reason') or feature.get('status','missing')]+=1
            issue=any(item['status']=='issue' for item in items)
            if issue and opened is None:opened=row['t']
            if not issue and opened is not None:ranges.append([opened,row['t']]);opened=None
        if opened is not None:ranges.append([opened,video['rows'][-1]['t']+100])
        video.update(status_counts=dict(counts),geometry_counts=dict(geometry),issue_ranges_ms=ranges)
        print(json.dumps({k:video[k] for k in ('number','status_counts','geometry_counts','issue_ranges_ms')},ensure_ascii=False),flush=True)
    atomic_json(REPORT,document)


def refresh_scene(document):
    from fitness_app.body_lean import SceneAxisTracker,measure
    manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    for video in document['videos']:
        item=manifest['demos']['user-'+video['number']];path=ROOT/item['video']['path']
        if digest(path)!=video['source_sha256']:raise ValueError('数值缓存不属于当前视频')
        tracker=SceneAxisTracker();rows=iter(video['rows']);row=next(rows,None)
        for stamp,rgb in video_frames(path,probe_video(path)):
            if row is None:break
            if stamp<row['t']:continue
            if stamp!=row['t']:raise ValueError('缓存时间轴改变')
            data=row['pretrained']
            if data and data.get('status')=='observed':
                body=data['body'];reference=tracker.update(rgb,body,stamp)
                data['body_features']['scene_lean']=measure(body,row['width'],row['height'],reference,data.get('source_landmarks'))
            else:tracker.reset()
            row=next(rows,None)
        if row is not None:raise ValueError('缓存未完整重放')
        print(json.dumps({'scene_refreshed':video['number'],'new_pose_inferences':0}),flush=True)
    atomic_json(REPORT,document)


def publish_tracks(document):
    from fitness_app.coaching import coaching_observation,coaching_snapshot,committed_issues
    from fitness_app.demo_feedback import build_demo_feedback,FEEDBACK_SCHEMA_VERSION
    from fitness_app.posture_engine import create_analysis_engine
    from fitness_app.pose import DEFAULT_MODEL
    from scripts.prepare_demo_playlist import reliable_demo_legs
    from scripts.prepare_user_demos import asset
    manifest=ROOT/'data/presentation-assets.json';assets=json.loads(manifest.read_text(encoding='utf-8'))
    for video in document['videos']:
        item=assets['demos']['user-'+video['number']]
        if item['video']['sha256']!=video['source_sha256']:raise ValueError('发布轨迹输入身份不一致')
        engine=create_analysis_engine('lat_pulldown');frames=[];states=Counter();statuses=Counter();shown=Counter()
        for index,row in enumerate(video['rows']):
            frame={'frame_seq':index,'captured_ms':row['t'],'width':row['width'],'height':row['height'],
                'framing':row['framing'],'pretrained':row['pretrained']}
            result=engine.process(frame,{'coaching_view':'auto','stream_epoch':'lat-pulldown-demo-review'})
            observation=coaching_observation(result)
            cue=coaching_snapshot({'status':'running','action_id':'lat_pulldown','supported_items':engine.rules},
                {**frame,'analysis':observation},committed_issues(result))
            feedback=build_demo_feedback(frame,result,engine.rules,cue)
            if feedback['state']=='clear':
                feedback['clear_label']='你标记的问题尚未检出' if item['user_reported_issues'] else '已检查项目未见偏差'
            people=row['framing']['person_count'];points=row['framing']['landmarks']
            original=[[round(p['x'],6),round(p['y'],6)] if p.get('valid') and 0<=p['x']<=1 and 0<=p['y']<=1 else None for p in points] if people==1 and len(points)==33 else [None]*33
            body=(row['pretrained'] or {}).get('body') or {'keypoints':[{'coordinate_valid':False}]*17}
            values=reliable_demo_legs(original,body,row['width'],row['height'])
            for knee in (25,26):
                if values[knee] is not None:shown[str(knee)]+=1
            frames.append({'t':row['t'],'p':values,'model_p':original,'people':people,'feedback':feedback,
                'leg_observation':[{k:p[k] for k in ('x','y','score','coordinate_valid') if k in p} for p in body['keypoints'][11:]]})
            states[feedback['state']]+=1
            for check in feedback['checks']:statuses[check['code']+':'+check['status']]+=1
        track={'schema_version':1,'feedback_schema_version':FEEDBACK_SCHEMA_VERSION,'source_sha256':video['source_sha256'],
            'width':item['width'],'height':item['height'],'mode':'preprocessed_user_source_not_live','action_id':'lat_pulldown',
            'classification_source':'user_confirmed_action','new_classification_inferences':0,
            'pose_model_sha256':digest(DEFAULT_MODEL),'rule_version':engine.version,'new_pose_inferences':0,
            'replay_basis':'same_frame_numeric_cache_with_current_body_lean_reference',
            'numeric_cache_sha256':digest(REPORT),'frames':frames,'events':[],'visible_frames':sum(any(p is not None for p in f['p']) for f in frames),
            'feedback_implementation_sha256':digest(ROOT/'fitness_app/demo_feedback.py'),'feedback_state_counts':dict(states),
            'posture_status_counts':dict(statuses),'accuracy_status':'no_independent_ground_truth',
            'user_reported_issues':item['user_reported_issues'],'user_reported_quality':item['user_reported_quality'],
            'no_personal_training_records_created':True,
            'skeleton_refinement':{'version':1,'scope':'display_lower_limbs_only','model':'rtmpose-body',
                'model_sha256':(video['rows'][0]['pretrained'] or {}).get('body_model_sha256'),
                'confidence_threshold':.5,'whole_leg_requires_hip_knee_ankle':True,
                'unsupported_heel_and_toe_points_omitted':True,'original_coordinates':'frames.model_p',
                'model_feedback_unchanged':True,'new_pose_inferences':0,'visible_leg_frames':dict(shown)}}
        timeline=ROOT/item['track']['path']
        if asset(timeline)!=item['track']:raise ValueError('发布前原轨迹已改变')
        atomic_json(timeline,track);item.update(track=asset(timeline),frames=len(frames),visible_frames=track['visible_frames'],rule_version=engine.version)
        print(json.dumps({'published':item['id'],'real_feedback_states':dict(states)}),flush=True)
    atomic_json(manifest,assets)


def profile_native_models():
    import time
    os.environ['FITNESS_MOVEMENT_GUIDANCE']='1';policy_version.cache_clear()
    manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    item=manifest['demos']['user-1689'];video=ROOT/item['video']['path'];timings={}
    def timed(call,name):
        def run(*args,**kwargs):
            began=time.perf_counter()
            try:return call(*args,**kwargs)
            finally:timings.setdefault(name,[]).append((time.perf_counter()-began)*1000)
        return run
    class SessionTimer:
        def __init__(self,session,name):self.session=session;self.name=name
        def __getattr__(self,name):return getattr(self.session,name)
        def run(self,*args,**kwargs):return timed(self.session.run,self.name)(*args,**kwargs)
    count=0
    with GymPoseService(video=True) as model:
        model.pose.detect=timed(model.pose.detect,'mediapipe')
        model.pretrained.body.session=SessionTimer(model.pretrained.body.session,'rtmpose_body')
        model.pretrained.axis.update=timed(model.pretrained.axis.update,'scene_axis')
        model.pretrained.hands.detector.session=SessionTimer(model.pretrained.hands.detector.session,'hand_detector')
        model.pretrained.hands.pose.session=SessionTimer(model.pretrained.hands.pose.session,'rtmpose_hand')
        for stamp,rgb in video_frames(video,probe_video(video),start_ms=5000,end_ms=6400):
            timed(model.detect,'total')(rgb,stamp);count+=1
            if count==6:timings.clear()
        backends={'body':getattr(model.pretrained.body.session,'execution_backend','onnxruntime_cpu'),
                  'hand':getattr(model.pretrained.hands.pose.session,'execution_backend','onnxruntime_cpu')}
    frames=len(timings['total'])
    rows={name:{'calls':len(values),'mean_ms':sum(values)/len(values),
                'per_frame_ms':sum(values)/frames} for name,values in timings.items()}
    from scripts.prepare_user_demos import asset
    result={'scope':'native_model_latency_only_no_database_or_browser','video':asset(video),'warmup_frames':6,
        'execution_backends':backends,
        'measured_frames':frames,'policy_version':policy_version(),'timings':rows,
        'private_media_uploaded':False,'paid_api_calls':0}
    target=ROOT/'data/verification/round3/native-pose-profile.json'
    previous=json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
    runs=previous.get('backend_profiles',{})
    if previous.get('timings') and not runs:
        runs['onnxruntime_cpu']={k:v for k,v in previous.items() if k!='backend_profiles'}
    key='torch_cuda' if all(v=='torch_cuda_fp32' for v in backends.values()) else 'onnxruntime_cpu'
    runs[key]=dict(result);result['backend_profiles']=runs
    atomic_json(target,result)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--replay',action='store_true')
    parser.add_argument('--refresh-scene',action='store_true');parser.add_argument('--publish-tracks',action='store_true')
    parser.add_argument('--profile-native',action='store_true');args=parser.parse_args()
    if args.profile_native:profile_native_models();sys.exit()
    if args.replay or args.refresh_scene or args.publish_tracks:document=json.loads(REPORT.read_text(encoding='utf-8'))
    elif REPORT.exists():raise ValueError('数值复核已存在；复用--replay，避免重复推理')
    else:document=collect()
    if args.refresh_scene:refresh_scene(document)
    replay(document)
    if args.publish_tracks:publish_tracks(document)
