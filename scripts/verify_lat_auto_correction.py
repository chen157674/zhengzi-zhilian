"""真实下拉原片自动识别／分段／纠错保存；复用已核验数值姿态，动作仍由生产模型推理。"""
import argparse
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
# 与实际start.ps1默认参考规则一致；显式0仍保留，不能用隐藏开关改变测试结论。
os.environ.setdefault('FITNESS_BODY_LEAN','1')
os.environ.setdefault('FITNESS_MOVEMENT_GUIDANCE','1')
sys.path.insert(0,str(ROOT/'tests'))
from fitness_app.media import video_frames
from fitness_app.semantic_recognition import shared_predictor
from fitness_app.semantic_model import resources
from fitness_app.research_extract import atomic_json
from fitness_app.posture_rules import policy_version
from fitness_app import db_schema as s
from test_formal_records import RecordTests

REPORT=ROOT/'data/verification/round3/lat-pulldown-auto-review.json'


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def run(native=False,output=None):
    target=project_path(output) if output else ROOT/'data/verification/round3/lat-pulldown-native-auto-review.json' if native else REPORT
    if not target.parent.exists():raise ValueError('输出父目录不存在，未创建近似目录')
    previous=json.loads(target.read_text(encoding='utf-8')) if target.exists() else None
    if previous and previous.get('state')!='completed':raise ValueError('原自动任务尚未结束，不重复启动')
    manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    info,reason=resources()
    if not info:raise RuntimeError('生产识别资源不可用：'+str(reason))
    execution_sha256=hashlib.sha256(Path(__file__).read_bytes()+b''.join((ROOT/'fitness_app'/name).read_bytes()
        for name in ('offline_video.py','gym_pose.py','pretrained_input.py','rtmpose_input.py','body_lean.py','pose_torch_backend.py'))).hexdigest()
    if previous and previous['recognition_model_version']==info['version'] and previous.get('posture_policy_version')==policy_version() and previous.get('execution_sha256')==execution_sha256:
        raise ValueError('相同生产模型及姿态执行版本已经复核，不重复推理')
    document={'schema_version':1,'state':'running','pid':__import__('os').getpid(),
        'scope':'actual_native_pose_and_action_full_upload_not_independent_accuracy' if native else 'real_media_and_production_action_inference_with_verified_native_pose_replay_not_independent_accuracy',
        'execution_sha256':execution_sha256,'runtime_flags':{key:os.environ.get(key) for key in ('FITNESS_BODY_LEAN','FITNESS_MOVEMENT_GUIDANCE','FITNESS_PRETRAINED_CORRECTION')},'input_mode':'actual_native_models' if native else 'verified_pose_replay',
        'new_pose_inferences':0,'new_action_classification_inferences':0,'recognition_model_version':info['version'],
        'posture_policy_version':policy_version(),'private_media_uploaded':False,'paid_api_calls':0,'videos':[],
        **({'previous_runs':previous.get('previous_runs',[])+[{k:v for k,v in previous.items() if k!='previous_runs'}]} if previous else {})}
    atomic_json(target,document)
    for number in ('1688','1689'):
        item=manifest['demos']['user-'+number];path=project_path(item['video']['path'])
        cached=ROOT/'data/experiments/open-source-correction'/('user-'+number+'-native-inputs.json.gz')
        with gzip.open(cached,'rb') as stream:raw=stream.read(64*1024*1024+1)
        if len(raw)>64*1024*1024:raise ValueError('真实输入缓存超限')
        data=json.loads(raw)
        if data['owner']!='fitness-native-input-cache' or data['video_sha256']!=digest(path) or data['video_sha256']!=item['video']['sha256']:
            raise ValueError('真实数值缓存与原视频身份不符')
        rows={row['t']:row for row in data['rows']};decisions=[];video_pose_calls=0
        class ReplayPose:
            def __init__(self,**kwargs):
                from fitness_app.gym_pose import GymPoseService
                self.actual=GymPoseService(**kwargs) if native else None
            def detect(self,rgb,stamp):
                nonlocal video_pose_calls
                if self.actual is not None:
                    video_pose_calls+=1
                    document['new_pose_inferences']+=1
                    return self.actual.detect(rgb,stamp)
                row=rows.get(stamp)
                if row is None or (row['width'],row['height'])!=(rgb.shape[1],rgb.shape[0]):
                    raise ValueError('实际解码源时间／尺寸与姿态缓存不符')
                return copy.deepcopy(row['pose_result'])
            def close(self):
                if self.actual is not None:self.actual.close()
        class ObservePredictor:
            def __init__(self):
                self.predictor=shared_predictor();self.info=self.predictor.info
            def predict(self,images,times,arrays):
                value=self.predictor.predict(images,times,arrays)
                decisions.append({'source_ms':list(map(int,times)),
                    'result':{key:value.get(key) for key in ('action_id','top_action_id','score','reason','model_version','decision_basis','motion_evidence')}})
                document['new_action_classification_inferences']+=1
                return value
        fx=RecordTests();fx.config_overrides={'PREVIEW_POSE_FACTORY':ReplayPose,'VIDEO_ANALYSIS_PREDICTOR':ObservePredictor,
            'VIDEO_ANALYSIS_FRAMES':video_frames};fx.setUp()
        manager=fx.app.extensions['video_analyses'];inputs=fx.app.extensions['media_inputs'];began=time.monotonic()
        try:
            token=fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
            with path.open('rb') as stream:
                response=fx.client.post('/api/v1/inputs',data={'file':(stream,'training.mp4')},
                    headers={'X-CSRFToken':token,'Idempotency-Key':s.new_id()})
            assert response.status_code==202,response.json;iid=response.json['data']['input_id']
            inputs.threads[iid].join(20);assert inputs.snapshot(fx.user,iid)['status']=='ready'
            response=fx.post(fx.client,'/api/v1/video-analyses',{'input_id':iid,'mode':'auto','save_skeleton':True})
            assert response.status_code==202,response.json;pid=response.json['data']['analysis_id'];task=manager.runs[pid]
            last_print=0
            while task.thread.is_alive():
                task.thread.join(1)
                if time.monotonic()-last_print>=30:
                    state=manager.snapshot(fx.user,fx.auth,pid)
                    print(json.dumps({'video':number,'status':state['status'],'phase':state['phase'],
                        'processed_ms':state['processed_ms'],'elapsed_seconds':round(time.monotonic()-began,1)}),flush=True)
                    last_print=time.monotonic()
            state=manager.snapshot(fx.user,fx.auth,pid)
            segments=[]
            for segment in state['segments']:
                current=segment['current_revision'];record=fx.store.snapshot(fx.user,current['session_id'],True) if current else None
                issues=[v for v in (record or {}).get('posture_observations',[]) if v['status']=='issue']
                segments.append({key:segment[key] for key in ('start_ms','end_ms','action_id','effective_action_id','kind','reason')}|{
                    'revision_status':current['status'] if current else None,
                    'issue_codes':sorted({v['code'] for v in issues}),
                    'issues':[{key:v[key] for key in ('code','start_ms','end_ms','side','message')} for v in issues]})
            result={'demo_id':item['id'],'source_sha256':item['video']['sha256'],'native_cache_sha256':digest(cached),
                'user_confirmed_action':'lat_pulldown','status':state['status'],'segments':segments,'windows':decisions,
                'elapsed_seconds':round(time.monotonic()-began,1),'source_copy_cleaned':not inputs._path(iid).exists(),
                'new_action_windows':len(decisions),'new_pose_inferences':video_pose_calls,
                'automatic_actions':sorted({v['effective_action_id'] for v in segments if v['effective_action_id']}),
                'detected_lean':any('lat_pulldown.pretrained_upper_body_balance' in v['issue_codes'] for v in segments)}
            document['videos'].append(result);atomic_json(target,document)
            print(json.dumps({key:result[key] for key in ('demo_id','status','automatic_actions','detected_lean','new_action_windows')},ensure_ascii=False),flush=True)
            if state['status']!='completed':raise RuntimeError('实际自动父任务未完成，不能当作成功')
        finally:
            for hook in fx.app.extensions.get('stop_hooks',[]):hook()
            fx.tearDown()
    document['state']='completed'
    document['development_pair_acceptance_passed']=all(v['automatic_actions']==['lat_pulldown'] and
        v['detected_lean']==(v['demo_id']=='user-1688') and
        (v['demo_id']!='user-1689' or not any(segment['issues'] for segment in v['segments']))
        for v in document['videos'])
    atomic_json(target,document)
    print(json.dumps({'completed':True,'development_pair_acceptance_passed':document['development_pair_acceptance_passed']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native-pose',action='store_true');parser.add_argument('--output')
    args=parser.parse_args();run(args.native_pose,args.output)
