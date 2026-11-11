"""用许可明确的本地公开视频回放实际HTTP自动分析；不启用硬件摄像头。"""
import collections,hashlib,json,sys,time
from io import BytesIO
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av,numpy as np
from PIL import Image
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.semantic_recognition import SemanticRecognizer
from fitness_app.db_schema import new_id,now_ms
from fitness_app.media import display_rgb
from fitness_app.frame_pipeline import analysis_framing
from fitness_app.research_extract import atomic_json
from evaluate_video_vlm import pose_cache_path
from evaluate_xclip import OUT
from test_formal_records import RecordTests


def select_cases(entries):
    selected=[]
    for action in ('barbell_overhead_press','barbell_bent_over_row','conventional_deadlift'):
        candidates=[]
        for e in entries:
            if e['action_id']!=action or e['partition'] not in {'train','validation'} or e['end_ms']-e['start_ms']<7000:continue
            with np.load(pose_cache_path(e),allow_pickle=False) as c:
                people=c['people'] if 'people' in c.files else c['person_counts'];single=float(np.mean(people==1))
                assessable=[]
                for points,count in zip(c['points'],people):
                    raw=[dict(zip(('x','y','z','visibility','presence'),map(float,p))) for p in points]
                    assessable.append(analysis_framing(action,{'poses':[raw] if count==1 else []})['usable'])
            if single>=.9 and np.mean(assessable)>=.5:candidates.append(e)
        if not candidates:raise ValueError('缺少单人连续回放素材: '+action)
        chosen=dict(sorted(candidates,key=lambda r:(r['partition']!='validation',-(r['end_ms']-r['start_ms']),r['id']))[0])
        if action=='barbell_overhead_press':
            with av.open(str(project_path(chosen['path']))) as video:duration=round(video.duration/1000)
            chosen['end_ms']=min(duration-100,max(chosen['end_ms'],17000))
        selected.append(chosen)
    return selected


def run():
    data=json.loads((OUT/'semantic-video-features.json').read_text(encoding='utf-8'));cases=select_cases(data['entries'])
    report_path=OUT/'semantic-realtime-flow.json';previous=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else None
    model=SemanticPredictor(research=True);began=time.perf_counter();model.warm();warm_ms=round((time.perf_counter()-began)*1000,1)
    recognizer_hash=hashlib.sha256((ROOT/'fitness_app/semantic_recognition.py').read_bytes()).hexdigest()
    retained=[r for r in previous['clips'] if r['passed']] if previous and previous['head_sha256']==model.info['sha256'] and previous['model_version']==model.info['version'] and previous.get('recognizer_code_sha256')==recognizer_hash else []
    cases=[r for r in cases if r['action_id'] not in {p['expected'] for p in retained}]
    measured=[]
    class Provider:
        def warm(self):model.warm()
        def predict(self,*args):
            start=time.perf_counter();result=model.predict(*args);measured.append(round((time.perf_counter()-start)*1000,1));return result
    provider=Provider();fx=RecordTests();fx.setUp();manager=fx.app.extensions['preview_manager'];results=list(retained)
    manager.recognizer_factory=lambda:SemanticRecognizer(lambda:provider,info=model.info)
    temporary=Path(fx.tmp.name)
    try:
        for entry in cases:
            if hashlib.sha256(project_path(entry['path']).read_bytes()).hexdigest()!=entry['source_sha256']:raise ValueError('HTTP回放原片摘要改变')
            fx.now[0]=now_ms();fx.mono[0]=time.monotonic()
            reply=fx.post(fx.client,'/api/v1/previews',{'page_id':new_id(),'recognition_mode':'auto','save_training':True})
            if reply.status_code!=201:raise ValueError('自动预览创建失败: '+str(reply.json))
            created=reply.json['data'];pid=created['preview_id'];recognizer=manager.active['recognizer'];token=fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
            counts=collections.Counter();analyzed=0;max_joints=max_connections=0;cues={};last_processed=None;first=None;submitted=0;seen=0;transitions=[];reasons=collections.Counter();processed_times=[]
            start,end=entry['start_ms'],entry['end_ms'];next_stamp=start;playback_origin=None;capture_origin=None
            with av.open(str(project_path(entry['path']))) as video:
                for source in video.decode(video=0):
                    if source.time is None:continue
                    stamp=round(float(source.time)*1000)
                    if stamp<next_stamp:continue
                    if stamp>=end:break
                    rgb=display_rgb(source)
                    if entry['crop']:
                        h,w=rgb.shape[:2];l,t,r,b=entry['crop'];rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
                    image=Image.fromarray(rgb);image.thumbnail((1280,720));width,height=image.size
                    if playback_origin is None:playback_origin=time.perf_counter();capture_origin=stamp
                    due=playback_origin+(stamp-capture_origin)/1000
                    if due>time.perf_counter():time.sleep(due-time.perf_counter())
                    fx.now[0]=now_ms();fx.mono[0]=time.monotonic();output=BytesIO();image.save(output,format='JPEG',quality=95)
                    meta={'stream_epoch':created['stream_epoch'],'frame_seq':submitted,'captured_ms':stamp-capture_origin,'width':width,'height':height}
                    reply=fx.client.post('/api/v1/previews/'+pid+'/frames',data={'metadata':json.dumps(meta),'frame':(BytesIO(output.getvalue()),'frame.jpg')},
                        headers={'X-CSRFToken':token,'X-Preview-Token':created['preview_token']})
                    if reply.status_code!=202:raise ValueError('HTTP回放帧失败: '+str(reply.json))
                    submitted+=1
                    while next_stamp<=stamp:next_stamp+=100
                    snapshot=fx.client.get('/api/v1/previews/'+pid).json['data'];result=snapshot['result']
                    if not result or result['frame_seq']==last_processed:continue
                    last_processed=result['frame_seq'];seen+=1;recognition=result['recognition'];action=recognition['action_id']
                    processed_times.append(result['captured_ms'])
                    if not transitions or transitions[-1]['action_id']!=action:transitions.append({'captured_ms':result['captured_ms'],'action_id':action,'state':recognition['state']})
                    reasons[result.get('automatic_analysis',{}).get('reason') or result['framing']['code']]+=1
                    if action:
                        counts[action]+=1
                        if first is None:first=result['captured_ms']
                    analyzed+=int(result.get('automatic_analysis',{}).get('state')=='analyzing')
                    skeleton=result.get('skeleton',{});max_joints=max(max_joints,len(skeleton.get('joints',[])));max_connections=max(max_connections,len(skeleton.get('connections',[])))
                    cue=result.get('coaching',{}).get('primary_cue')
                    if cue:cues[cue['code']]={k:cue.get(k) for k in ('code','title','message','advice','side')}
            fx.now[0]=now_ms();fx.mono[0]=time.monotonic()
            with recognizer.lock:
                internal={'generation_resets':recognizer.generation,'buffered_rgb':len(recognizer.images),
                          'buffered_span_ms':recognizer.images[-1][0]-recognizer.images[0][0] if recognizer.images else 0,
                          'last_queued_ms':recognizer.last_queued,'last_completed_ms':recognizer.completed[1] if recognizer.completed else None,
                          'max_observed_source_gap_ms':max(np.diff(processed_times)) if len(processed_times)>1 else None}
            released=fx.client.delete('/api/v1/previews/'+pid,json={'preview_token':created['preview_token'],'stream_epoch':created['stream_epoch']},
                headers={'X-CSRFToken':token,'Idempotency-Key':new_id()})
            if released.status_code!=200:raise ValueError('自动预览释放失败: '+str(released.json))
            recognizer.thread.join(timeout=15) if recognizer.thread else None
            record={'id':entry['id'],'source_sha256':entry['source_sha256'],'expected':entry['action_id'],'partition':entry['partition'],'role':'development_engineering_replay',
                    'source_start_ms':entry['start_ms'],'source_end_ms':entry['end_ms'],
                    'submitted_frames':submitted,'observed_unique_results':seen,'recognized_frames':dict(counts),'first_recognition_source_ms':first,
                    'analyzing_results':analyzed,'max_skeleton_joints':max_joints,'max_skeleton_connections':max_connections,'cues':cues,
                    'recognition_transitions':transitions,'analysis_reasons':dict(reasons),
                    'stream_diagnostics':{k:int(v) if v is not None else None for k,v in internal.items()},
                    'rgb_buffers_after_release':len(recognizer.images),'worker_closed':recognizer.thread is None or not recognizer.thread.is_alive()}
            record['passed']=bool(counts[entry['action_id']]>0 and sum(n for a,n in counts.items() if a!=entry['action_id'])==0 and analyzed>0 and max_connections>=8 and not recognizer.images and record['worker_closed'])
            results.append(record);print(json.dumps(record,ensure_ascii=False),flush=True)
        saved=fx.client.get('/api/v1/records').json['data']['total']
    finally:
        for key in ('video_jobs','session_manager','preview_manager','media_inputs'):fx.app.extensions[key].stop()
        fx.tearDown()
        if model.idle_timer:model.idle_timer.cancel()
    report={'schema_version':1,'model_version':model.info['version'],'head_sha256':model.info['sha256'],'clips':results,'passed':len(results)==3 and all(r['passed'] for r in results),
            'recognizer_code_sha256':recognizer_hash,
            'cold_warm_ms':warm_ms,'steady_classifier_latency_ms':{'count':len(measured),'p50':float(np.median(measured)),'p95':float(np.percentile(measured,95))} if measured else None,
            'temporary_database_removed':not temporary.exists(),'saved_test_records':saved,'reused_passed_actions':[r['expected'] for r in retained],
            'previous_replays':(previous.get('previous_replays',[])+[r for r in previous['clips'] if not r['passed']]) if previous else [],
            'hardware_camera_opened':False,'frontend_modified':False,
            'scope':'Actual JPEG HTTP stream -> Full pose -> asynchronous video action -> posture coaching/skeleton -> temporary private records. Development engineering replay, not independent classification or posture-error accuracy.'}
    atomic_json(OUT/'semantic-realtime-flow.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('clips','scope')}),flush=True)


if __name__=='__main__':run()
