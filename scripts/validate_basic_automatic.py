"""真实视频JPEG→工作线程→基础分类→对应分析；复用姿态缓存，不调用摄像头。"""
import io,json,sys,time,hashlib
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from PIL import Image
from fitness_app.research_data import load_dataset
from fitness_app.research_extract import probe_research_video,atomic_json
from fitness_app.media import video_frames
from fitness_app.db_schema import new_id
from diagnose_cycles import cached_frames
from test_formal_records import RecordTests

REPORT=ROOT/'data/verification/step-19/basic-automatic.json'


def run(recheck=False):
    previous=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else None
    if previous and not recheck:raise ValueError('已有基础链验证结果，先核对变更范围，不自动重复视频验证')
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'));samples=load_dataset()
    selected=[sorted([s for s in samples if s.partition=='validation' and s.action_id==action],key=lambda s:s.key)[0]
              for action in ('bodyweight_squat','standing_dumbbell_curl')]
    results=[]
    for sample in selected:
        frames=cached_frames(sample,baseline,apply_bounds=False);by_stamp={f['captured_ms']:f for f in frames};closed=[]
        class ReplayPose:
            def detect(self,rgb,stamp):
                f=by_stamp[stamp];q=f['framing'];n=q['person_count']
                return {'poses':[q['landmarks']] if n==1 else [[] for _ in range(n)],'model':'actual_pose_cache_replay'}
            def close(self):closed.append(True)
        fx=RecordTests();fx.setUp();manager=fx.app.extensions['preview_manager'];manager.pose_factory=lambda **kwargs:ReplayPose()
        states=Counter();recognized=Counter();analyzed=0;cycles=0;first=None;processed=0;elapsed=[]
        try:
            created=fx.post(fx.client,'/api/v1/previews',{'page_id':new_id()})
            assert created.status_code==201,created.json
            data=created.json['data'];pid=data['preview_id'];assert data['recognition_mode']=='auto'
            csrf=fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
            for stamp,rgb in video_frames(sample.absolute_path,probe_research_video(sample.absolute_path)):
                if stamp not in by_stamp:continue
                fx.mono[0]=stamp/1000;seq=processed
                image=Image.fromarray(rgb);scale=min(1,1280/image.width,720/image.height)
                image=image.resize((max(1,int(image.width*scale)),max(1,int(image.height*scale))))
                buf=io.BytesIO();image.save(buf,format='JPEG',quality=85);buf.seek(0)
                meta={'stream_epoch':data['stream_epoch'],'frame_seq':seq,'captured_ms':stamp,'width':image.width,'height':image.height}
                response=fx.client.post('/api/v1/previews/'+pid+'/frames',data={'metadata':json.dumps(meta),'frame':(buf,'frame.jpg','image/jpeg')},
                    headers={'X-CSRFToken':csrf,'X-Preview-Token':data['preview_token']})
                assert response.status_code==202,response.json
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    reply=fx.client.get('/api/v1/previews/'+pid).json['data']
                    assert reply['status']=='active',reply.get('reason')
                    value=reply.get('result')
                    if value and value['frame_seq']==seq:break
                    manager.result_ready.wait(.02);manager.result_ready.clear()
                else:raise AssertionError('实时基础分类处理超时')
                recognition=value['recognition'];states[recognition.get('reason') or recognition['state']]+=1
                if recognition['action_id']:
                    recognized[recognition['action_id']]+=1
                    if first is None:first=stamp
                if value['automatic_analysis']['state']=='analyzing':analyzed+=1
                cycles=max(cycles,value['automatic_analysis'].get('reference_completed_cycles') or 0)
                elapsed.append(value['processing_ms']);processed+=1
            assert fx.client.get('/api/v1/records').json['data']['total']==0
            reply=fx.client.delete('/api/v1/previews/'+pid,json={'preview_token':data['preview_token'],'stream_epoch':data['stream_epoch']},
                headers={'X-CSRFToken':csrf,'Idempotency-Key':new_id()})
            assert reply.status_code==200
        finally:
            for key in ('video_jobs','session_manager','preview_manager','media_inputs'):fx.app.extensions[key].stop()
            fx.tearDown()
        import numpy as np
        result={'action':sample.action_id,'key':sample.key,'frames':processed,'confirmed_actions':dict(recognized),
                'states':dict(states),'first_confirmation_ms':first,'analyzed_frames':analyzed,'maximum_segment_reference_cycles':cycles,
                'pose_engine_closed':bool(closed),'processing_ms_p95':float(np.percentile(elapsed,95)),
                'no_action_id_supplied':True,'new_pose_inference_frames':0,'image_encoder':'actual_quantized_CLIP_CPU',
                'independent_error_labels':False}
        results.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
    passed=all(r['analyzed_frames']>0 and r['confirmed_actions'].get(r['action'],0)>0 and
               not(set(r['confirmed_actions'])-{r['action']}) and r['pose_engine_closed'] for r in results)
    report={'passed':passed,'clips':results,'scope':'Two fixed heldout clips via actual JPEG HTTP/background pipeline; cached real pose, no camera; not twelve-class or posture accuracy acceptance',
        'implementation_identity':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'fitness_app/basic_recognition.py','fitness_app/motion_recognition.py','fitness_app/automatic_analysis.py','fitness_app/preview.py')}}
    if previous:report['prior_runs']=previous.get('prior_runs',[])+[{k:v for k,v in previous.items() if k!='prior_runs'}]
    atomic_json(REPORT,report)
    if not passed:raise AssertionError('基础自动分析没有在两个固定片段均接通')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--recheck',action='store_true')
    run(parser.parse_args().recheck)
