"""按源时间播放真实原片核实时自动识别／纠错；姿态数值复用，GPU动作异步实推。"""
import argparse,copy,gzip,hashlib,json,os,sys,time
from io import BytesIO
import itertools
from contextlib import ExitStack
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
sys.path.insert(0,str(ROOT/'tests'))
from fitness_app.semantic_model import resources
from fitness_app.semantic_recognition import SemanticRecognizer,shared_predictor
from fitness_app.automatic_recognition import body_frame
from fitness_app.automatic_analysis import AutomaticAnalysis
from fitness_app.automatic_recording import AutomaticRecorder
from fitness_app.frame_pipeline import framing
from fitness_app.research_extract import atomic_json
from fitness_app.media import video_frames,probe_video
from fitness_app import db_schema as s
from test_formal_records import RecordTests

REPORT=ROOT/'data/verification/round3/live-lat-correction-review.json'


def native_capture_frames(path,info,timing):
    """模拟相机只转换实际送检帧；丢弃帧仍核源时间，不额外转换全部30fps RGB。"""
    import av
    from fitness_app.media import source_stamp,display_rgb
    previous=-1;last=-200
    with av.open(str(path),options={'protocol_whitelist':'file'}) as container:
        for frame in container.decode(video=0):
            stamp=source_stamp(frame,info['origin_ms'])
            if stamp<=previous:raise ValueError('实时回放源时间倒退')
            if frame.rotation!=info['rotation'] or any(component.bits!=8 for component in frame.format.components):
                raise ValueError('实时回放源方向或位深改变')
            previous=stamp
            if stamp-last<200:continue
            origin=timing.get('origin')
            if origin is not None and stamp>0 and (time.monotonic()-origin)*1000-stamp>200:
                timing['dropped']+=1
                continue
            last=stamp
            yield stamp,display_rgb(frame)


def profile_native_stages(stack,totals):
    """记录并行时真实分项耗时；包装不改变输入、返回值或源时间。"""
    from fitness_app.pose import PoseService
    from fitness_app.rtmpose_input import RTMPose,RTMHandDetector
    from fitness_app.body_lean import SceneAxisTracker
    from fitness_app import pose_input
    def wrap(original,label):
        def measured(*args,**kwargs):
            began=time.perf_counter()
            try:return original(*args,**kwargs)
            finally:
                key=label(args) if callable(label) else label
                totals[key]=totals.get(key,0.)+(time.perf_counter()-began)*1000
        return measured
    for owner,name,label in ((PoseService,'detect','mediapipe'),
            (RTMPose,'detect',lambda args:'rtmpose_'+args[0].kind),
            (RTMHandDetector,'detect','hand_detector'),(SceneAxisTracker,'update','scene_axis'),
            (pose_input,'detect_content_bounds','content_bounds')):
        stack.enter_context(patch.object(owner,name,wrap(getattr(owner,name),label)))


def run_preview_video(number,manifest,predictor,info):
    """实际PreviewManager工作线程、最新帧队列、姿态与自动保存；原片代替物理相机。"""
    from fitness_app.preview import PreviewManager
    from fitness_app.vision_slot import VisionSlot
    from PIL import Image
    item=manifest['demos']['user-'+number];path=ROOT/item['video']['path']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=item['video']['sha256']:raise ValueError('原片身份变化')
    calls=[]
    class Measured:
        def warm(self):predictor.warm()
        def predict(self,images,times,arrays):
            began=time.perf_counter();result=predictor.predict(images,times,arrays)
            calls.append({'source_ms':list(map(int,times)),'wall_seconds':round(time.perf_counter()-began,3),'action_id':result['action_id']})
            return result
    fx=RecordTests();fx.setUp()
    manager=PreviewManager(VisionSlot(),record_store=fx.store,
        recognizer_factory=lambda:SemanticRecognizer(lambda:Measured(),info=info))
    preparation_started=time.monotonic()
    created=manager.create(fx.user,fx.auth,s.now_ms()+180000,
        {'page_id':s.new_id(),'recognition_mode':'auto','save_training':True,'save_skeleton':True},s.new_id())
    pose_preparation_seconds=time.monotonic()-preparation_started
    preparation_deadline=time.monotonic()+180
    while True:
        prepared=manager.snapshot(fx.user,created['preview_id'])
        if prepared['status']!='active' or prepared['model_preparation']['state']=='unavailable':raise RuntimeError('实际动作模型准备失败')
        if prepared['model_preparation']['state']=='ready':break
        if time.monotonic()>preparation_deadline:raise RuntimeError('实际动作模型准备超时')
        time.sleep(.1)
    model_preparation_seconds=time.monotonic()-preparation_started
    recorder=manager.active['recorder'];states=[];seen=set();record_offsets={};last_print=time.monotonic()
    original_publish=manager._publish
    def publish(item,packet,result,elapsed):
        original_publish(item,packet,result,elapsed)
        with manager.lock:
            row=item.get('result')
            if row is None:return
            stamp=row['captured_ms']
            if stamp in seen:return
            seen.add(stamp);recognition=row.get('recognition') or {};cue=(row.get('coaching') or {}).get('primary_cue')
            states.append({'source_ms':stamp,'processing_ms':row['processing_ms'],'action_id':recognition.get('action_id'),
                'recognition_state':recognition.get('state'),'prediction_age_ms':recognition.get('prediction_age_ms'),
                'cue_code':cue.get('code') if cue else None,
                'pose_execution_backend':(result.get('pretrained') or {}).get('body',{}).get('execution_backend')})
            if recorder.current and recorder.current.get('record_id'):
                record_offsets[recorder.current['record_id']]=recorder.current['offset']
    manager._publish=publish
    timing={'origin':time.monotonic(),'dropped':0};iterator=native_capture_frames(path,probe_video(path),timing)
    try:
        for seq,(stamp,rgb) in enumerate(iterator):
            delay=timing['origin']+stamp/1000-time.monotonic()
            if delay>0:time.sleep(delay)
            memory=BytesIO();Image.fromarray(rgb).save(memory,format='JPEG',quality=85)
            manager.submit(fx.user,created['preview_id'],created['preview_token'],
                {'stream_epoch':created['stream_epoch'],'frame_seq':seq,'captured_ms':stamp,'width':rgb.shape[1],'height':rgb.shape[0]},memory.getvalue())
            # live.js实际等同一帧完成才采集下一帧；不能用无背压生产者制造不同队列负载。
            frame_deadline=time.monotonic()+10
            while time.monotonic()<frame_deadline:
                with manager.lock:
                    result=manager.active.get('result') if manager.active else None
                    completed=result is not None and result['frame_seq']==seq
                if completed:break
                time.sleep(.015)
            else:raise RuntimeError('实际预览帧未在前端原10秒期限内完成')
            if time.monotonic()-last_print>=5:
                print(json.dumps({'video':number,'source_ms':stamp,'preview_worker':True,'processed':len(states),'last':states[-1] if states else None}),flush=True)
                last_print=time.monotonic()
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            with manager.lock:pending=manager.processing or manager.active and manager.active['pending']
            if not pending:break
            time.sleep(.02)
        else:raise RuntimeError('实际预览工作线程未排空')
        queue=manager.snapshot(fx.user,created['preview_id'])['queue']
        manager.release(fx.user,created['preview_id'],
            {'preview_token':created['preview_token'],'stream_epoch':created['stream_epoch']},s.new_id())
        records=[]
        for sid in recorder.record_ids:
            value=fx.store.snapshot(fx.user,sid,True)
            records.append({'action_id':value['action_id'],'status':value['status'],
                'record_source_offset_ms':record_offsets[sid],
                'issues':[{k:v[k] for k in ('code','start_ms','end_ms','side','message')} for v in value['posture_observations'] if v['status']=='issue']})
        value={'source_id':item['id'],'source_sha256':item['video']['sha256'],'states':states,'records':records,
            'inferences':calls,'recognized_actions':sorted({r['action_id'] for r in states if r['action_id']}),
            'lean_cue_frames':sum(r['cue_code']=='lat_pulldown.pretrained_upper_body_balance' for r in states),
            'record_count':len(records),'pose_calls':len(states),'queue':queue,
            'wall_elapsed_seconds':round(time.monotonic()-timing['origin'],2),
            'source_frame_replay_not_physical_camera':True,'pose_preparation_seconds':round(pose_preparation_seconds,3),
            'model_preparation_seconds':round(model_preparation_seconds,3),'source_frames_before_model_ready':0}
        print(json.dumps({k:value[k] for k in ('source_id','recognized_actions','lean_cue_frames','record_count','queue')},ensure_ascii=False),flush=True)
        return value
    finally:
        iterator.close();manager.stop()
        for hook in fx.app.extensions.get('stop_hooks',[]):hook()
        fx.tearDown()


def run(native=False,verification_trial=None,preview_worker=False,cold_start=False):
    target=ROOT/'data/verification/round3/live-preview-lat-correction-review.json' if preview_worker else ROOT/'data/verification/round3/live-native-lat-correction-review.json' if native else REPORT
    previous=json.loads(target.read_text(encoding='utf-8')) if target.exists() else None
    if previous and previous.get('state')!='completed':raise ValueError('原实时任务未结束，核验原任务，不重复启动')
    info,reason=resources()
    if not info:raise RuntimeError(reason)
    execution_paths=('fitness_app/semantic_recognition.py','fitness_app/semantic_model.py',
        'fitness_app/body_lean.py','fitness_app/pose_torch_backend.py',
        'fitness_app/gym_pose.py','fitness_app/pretrained_input.py','fitness_app/rtmpose_input.py',
        'fitness_app/pose.py','fitness_app/pose_input.py','fitness_app/preview.py')
    import cv2
    execution_environment={'pose_device':os.environ.get('FITNESS_POSE_DEVICE','auto'),'opencv_threads':cv2.getNumThreads(),
        'verification_trial':verification_trial,'preview_worker':preview_worker,'cold_start':cold_start}
    execution_sha256=hashlib.sha256(b''.join((ROOT/name).read_bytes() for name in execution_paths)+
        Path(__file__).read_bytes()+json.dumps(execution_environment,sort_keys=True).encode()).hexdigest()
    if previous and previous['production_recognition_model']==info['version'] and previous.get('runtime_execution_sha256')==execution_sha256:raise ValueError('同一识别及执行版本已有实时复核')
    manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    predictor=shared_predictor();began=time.monotonic()
    if not cold_start:predictor.warm()
    warm_seconds=time.monotonic()-began
    document={'state':'running','pid':__import__('os').getpid(),'scope':'wall_clock_paced_real_video_not_physical_camera',
        'new_pose_inferences':0,'input_mode':'actual_native_pose_jpeg85' if native else 'native_pose_numeric_replay','production_recognition_model':info['version'],'runtime_execution_sha256':execution_sha256,'execution_environment':execution_environment,'cold_warm_seconds':round(warm_seconds,2),
        'private_media_uploaded':False,'paid_api_calls':0,'synthetic_warmup_forward_passes':getattr(predictor,'fast_warmup_forwards',0),'videos':[],
        **({'previous_runs':previous.get('previous_runs',[])+[{k:v for k,v in previous.items() if k!='previous_runs'}]} if previous else {})}
    atomic_json(target,document)
    for number in ('1688','1689'):
        if preview_worker:
            value=run_preview_video(number,manifest,predictor,info)
            document['videos'].append(value);document['new_pose_inferences']+=value['pose_calls']
            atomic_json(target,document);continue
        item=manifest['demos']['user-'+number];path=ROOT/item['video']['path']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['video']['sha256']:raise ValueError('原片身份变化')
        rows={}
        if not native:
            with gzip.open(ROOT/('data/experiments/open-source-correction/user-'+number+'-native-inputs.json.gz'),'rt',encoding='utf-8') as stream:cache=json.load(stream)
            if cache['video_sha256']!=item['video']['sha256']:raise ValueError('真实姿态数值源不符')
            rows={r['t']:r for r in cache['rows']}
        timing={'origin':None,'dropped':0}
        pose=None;first_result=None;profile_stack=ExitStack();phase_totals={}
        iterator=native_capture_frames(path,probe_video(path),timing) if native else video_frames(path,probe_video(path))
        first=next(iterator)
        pose_initialization_seconds=0.;pose_calls=0;dropped=0
        def jpeg_rgb(rgb):
            from PIL import Image
            from fitness_app.frame_pipeline import decode_jpeg
            memory=BytesIO();Image.fromarray(rgb).save(memory,format='JPEG',quality=85)
            return decode_jpeg(memory.getvalue(),(rgb.shape[1],rgb.shape[0]))
        if native:
            from fitness_app.gym_pose import GymPoseService
            profile_native_stages(profile_stack,phase_totals)
            setup=time.monotonic();pose=GymPoseService(video=True,realtime=True)
            first_result=pose.detect(jpeg_rgb(first[1]),first[0]);pose_calls=1;document['new_pose_inferences']+=1
            pose_initialization_seconds=time.monotonic()-setup
        calls=[]
        class Measured:
            info=predictor.info
            def warm(self):predictor.warm()
            def predict(self,images,times,arrays):
                start=time.monotonic();result=predictor.predict(images,times,arrays)
                calls.append({'source_ms':list(map(int,times)),'wall_seconds':round(time.monotonic()-start,3),
                    'action_id':result['action_id'],'cross_review':result.get('cross_review'),
                    'overhead_fast_review':result.get('overhead_fast_review')})
                return result
        fx=RecordTests();fx.setUp();recognizer=SemanticRecognizer(lambda:Measured(),info=info)
        recorder=AutomaticRecorder(fx.store,fx.user,fx.auth,s.new_id(),save_skeleton=True)
        router=AutomaticAnalysis(recorder=recorder);states=[];record_offsets={};last=-200;origin=time.monotonic();last_print=origin
        timing['origin']=origin
        try:
            for stamp,rgb in itertools.chain((first,),iterator):
                if stamp-last<200:continue
                last=stamp
                delay=origin+stamp/1000-time.monotonic()
                if delay>0:time.sleep(delay)
                pose_ms=None
                if native:
                    rgb=jpeg_rgb(rgb)
                    phase_totals.clear()
                    if stamp==first[0]:result=first_result;first_result=None
                    else:
                        pose_started=time.perf_counter();result=pose.detect(rgb,stamp)
                        pose_ms=round((time.perf_counter()-pose_started)*1000,2)
                        pose_calls+=1;document['new_pose_inferences']+=1
                else:result=copy.deepcopy(rows[stamp]['pose_result'])
                width,height=rgb.shape[1],rgb.shape[0]
                generic=body_frame(result,stamp,width,height,len(states))
                recognized=recognizer.process(generic,recognizer.extract(rgb,stamp));action=recognized.get('action_id')
                q=framing(action,result) if action else generic['framing']
                frame={'captured_ms':stamp,'frame_seq':len(states),'width':width,'height':height,
                    'model_version':result.get('model'),'framing':q,'pretrained':result.get('pretrained')}
                output=router.process(frame,recognized)
                cue=output['coaching'].get('primary_cue')
                if recorder.current and recorder.current.get('record_id'):
                    record_offsets[recorder.current['record_id']]=recorder.current['offset']
                states.append({'source_ms':stamp,'wall_elapsed_ms':round((time.monotonic()-origin)*1000),
                    'action_id':action,'recognition_state':recognized['state'],'reason':recognized.get('reason'),'native_pose_ms':pose_ms,
                    'native_phase_ms':{k:round(v,2) for k,v in phase_totals.items()} if native else None,
                    'pose_execution_backend':(result.get('pretrained') or {}).get('body',{}).get('execution_backend'),
                    'prediction_age_ms':recognized.get('prediction_age_ms'),'coaching_state':output['coaching']['state'],
                    'cue_code':cue.get('code') if cue else None,'saved_record_id':output['automatic_analysis'].get('saved_record_id')})
                if time.monotonic()-last_print>=5:
                    print(json.dumps({'video':number,'source_ms':stamp,'action_id':action,'state':recognized['state'],
                        'age_ms':recognized.get('prediction_age_ms'),'cue':states[-1]['cue_code']}),flush=True);last_print=time.monotonic()
            recognizer.close()
            if recognizer.thread:recognizer.thread.join(30)
            router.reset('video_complete')
            records=[]
            for sid in recorder.record_ids:
                value=fx.store.snapshot(fx.user,sid,True)
                records.append({'action_id':value['action_id'],'status':value['status'],
                    'record_source_offset_ms':record_offsets[sid],
                    'issue_time_base':'session_elapsed_ms; add record_source_offset_ms for source time',
                    'issues':[{k:v[k] for k in ('code','start_ms','end_ms','side','message')} for v in value['posture_observations'] if v['status']=='issue']})
            value={'source_id':item['id'],'source_sha256':item['video']['sha256'],'states':states,'inferences':calls,
                'records':records,'recognized_actions':sorted({r['action_id'] for r in states if r['action_id']}),
                'lean_cue_frames':sum(r['cue_code']=='lat_pulldown.pretrained_upper_body_balance' for r in states),
                'record_count':len(records),'pose_calls':pose_calls,'dropped_late_source_frames':timing['dropped'] if native else dropped,
                'capture_rgb_policy':'selected_actual_source_frames_only' if native else 'all_decoded_source_frames',
                'pose_setup_and_first_real_frame_seconds':round(pose_initialization_seconds,3),
                'wall_elapsed_seconds':round(time.monotonic()-origin,2)}
            document['videos'].append(value);atomic_json(target,document)
            print(json.dumps({k:value[k] for k in ('source_id','recognized_actions','lean_cue_frames','record_count','wall_elapsed_seconds')},ensure_ascii=False),flush=True)
        finally:
            profile_stack.close()
            if pose is not None:pose.close()
            if hasattr(iterator,'close'):iterator.close()
            recognizer.close()
            if recognizer.thread:recognizer.thread.join(30)
            for hook in fx.app.extensions.get('stop_hooks',[]):hook()
            fx.tearDown()
    document['state']='completed';document['pair_passed']=len(document['videos'])==2 and all(
        v['recognized_actions']==['lat_pulldown'] and
        (bool(v['lean_cue_frames']) and any(issue['code']=='lat_pulldown.pretrained_upper_body_balance'
            for record in v['records'] for issue in record['issues']) if v['source_id']=='user-1688' else
            not v['lean_cue_frames'] and not any(record['issues'] for record in v['records']))
        for v in document['videos'])
    atomic_json(target,document);print(json.dumps({'completed':True,'pair_passed':document['pair_passed']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native-pose',action='store_true')
    parser.add_argument('--preview-worker',action='store_true')
    parser.add_argument('--cold-start',action='store_true')
    parser.add_argument('--verification-trial',help='同版本复验条件说明，报告保留此前运行')
    args=parser.parse_args();run(args.native_pose or args.preview_worker,args.verification_trial,args.preview_worker,args.cold_start)
