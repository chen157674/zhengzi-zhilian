"""已下载真实素材的上传自动分段回归；不重新下载，不冒充独立识别/具体纠错真值。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import argparse,hashlib,json,math,secrets,time,tempfile
from fractions import Fraction
import av,numpy as np
from fitness_app.media import probe_video,video_frames
from fitness_app import create_app
from fitness_app.auth import initialize_admin
from fitness_app.semantic_model import resources
from fitness_app.posture_rules import policy_version
from fitness_app.offline_video import OfflineScan,NumericFrame

OUT=project_path('data/verification/round3')
VIDEO=OUT/'upload-real-regression.mp4'
RECIPE=OUT/'upload-real-source.json'
REPORT=OUT/'upload-real-regression.json'
CACHE=OUT/'upload-real-cache.npz'
PARTS=[
 {'path':'data/datasets/development-movements/overhead-press-dvids-692514-front.mp4','start_ms':4000,'end_ms':10000,'action_id':'barbell_overhead_press',
  'source_url':'https://www.dvidshub.net/video/692514/military-press','credit':'CPT Matthew Holfinger / U.S. Marine Corps Training and Education Command',
  'license':'PUBLIC DOMAIN; subject to DVIDS use restrictions','license_url':'https://www.dvidshub.net/about/copyright','modification':'existing front-view crop; excerpt, resize and concatenate'},
 {'path':'data/datasets/development-movements/barbell-row-dvids-639914-side.mp4','start_ms':4000,'end_ms':10000,'action_id':None,'phase':'standing_preparation_background',
  'source_url':'https://www.dvidshub.net/video/639914/barbell-bent-over-row','credit':'CPT Matthew Holfinger / U.S. Marine Corps Training and Education Command',
  'license':'PUBLIC DOMAIN; subject to DVIDS use restrictions','license_url':'https://www.dvidshub.net/about/copyright','modification':'existing side-view crop; excerpt, resize and concatenate'},
 {'path':'data/datasets/development-movements/barbell-row-dvids-639914-side.mp4','start_ms':16000,'end_ms':23500,'action_id':'barbell_bent_over_row','phase':'active_row_frame_reviewed',
  'source_url':'https://www.dvidshub.net/video/639914/barbell-bent-over-row','credit':'CPT Matthew Holfinger / U.S. Marine Corps Training and Education Command',
  'license':'PUBLIC DOMAIN; subject to DVIDS use restrictions','license_url':'https://www.dvidshub.net/about/copyright','modification':'existing side-view crop; active excerpt, resize and concatenate'},
 {'path':'data/datasets/mydeadlift/Gerakan Benar/GB_S01_R07.mp4','start_ms':0,'end_ms':4733,'action_id':'conventional_deadlift',
  'source_url':'https://data.mendeley.com/datasets/w5prmmxyt9/1','credit':'Ivan Conanta and Gloria Virginia, MyDeadlift v1, DOI 10.17632/w5prmmxyt9.1',
  'license':'CC BY 4.0','license_url':'https://creativecommons.org/licenses/by/4.0/','modification':'resize and concatenate; retain full recorded repetition'},
 {'path':'data/datasets/mydeadlift/Gerakan Benar/GB_S01_R08.mp4','start_ms':0,'end_ms':5433,'action_id':'conventional_deadlift',
  'source_url':'https://data.mendeley.com/datasets/w5prmmxyt9/1','credit':'Ivan Conanta and Gloria Virginia, MyDeadlift v1, DOI 10.17632/w5prmmxyt9.1',
  'license':'CC BY 4.0','license_url':'https://creativecommons.org/licenses/by/4.0/','modification':'resize and concatenate; retain full recorded repetition'},
]
SOURCE_PHASE_REVIEW={
 'status':'reviewed_active_and_preparation_ranges','reviewer':'Codex frame review; not independent specific-error truth',
 'source_path':'data/datasets/development-movements/barbell-row-dvids-639914-side.mp4',
 'preparation_source_range_ms':[4000,10000],'active_row_source_range_ms':[16000,23500],
 'finding':'原准备区间继续作为背景负例，并加入实际划船区间；不能只替换失败样本。',
 'original_model_failure_retained':True,'boundary_tolerance_ms':500,
}


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(rebuild=False):
    parts=[];offset=0
    for raw in PARTS:
        path=project_path(raw['path']);info=probe_video(path)
        if info['duration_ms']<raw['end_ms']:raise ValueError('原素材时间范围不足，未生成替代内容')
        ticks=math.ceil((raw['end_ms']-raw['start_ms'])*15/1000)
        parts.append({**raw,'sha256':digest(path),'composite_start_ms':round(offset*1000/15),'composite_end_ms':round((offset+ticks)*1000/15)})
        offset+=ticks
    recipe={'scope':'development_seen_sources_regression_not_independent_accuracy','fps':15,'width':640,'height':480,'parts':parts,
        'contains_different_people_and_edit_cuts':True,'no_generated_interpolation_or_repeated_frames':True,
        'specific_error_truth':False,'not_continuous_recording_of_one_person':True,
        'notice':'Research/engineering regression only; source clips edited and combined. No government or author endorsement.'}
    signature=hashlib.sha256(json.dumps(recipe,sort_keys=True).encode()).hexdigest()
    if VIDEO.exists():
        saved=json.loads(RECIPE.read_text('utf8'))
        if saved['recipe_signature']==signature and saved['sha256']==digest(VIDEO):return saved
        if not rebuild:raise RuntimeError('阶段规格已修正，显式--rebuild-source才重建本任务同名素材')
    temporary=VIDEO.with_name('upload-real-regression.part.mp4')
    if temporary.exists():raise RuntimeError('存在待核对的生成中间文件，未覆盖')
    owned=False
    try:
        with temporary.open('xb') as output:
            owned=True
            with av.open(output,'w',format='mp4') as container:
                stream=container.add_stream('libx264',rate=15);stream.width=640;stream.height=480;stream.pix_fmt='yuv420p';stream.options={'crf':'20'}
                offset=0
                for raw in PARTS:
                    path=project_path(raw['path']);info=probe_video(path);previous=-1
                    for stamp,rgb in video_frames(path,info,start_ms=raw['start_ms'],end_ms=raw['end_ms']):
                        tick=(stamp-raw['start_ms'])*15//1000
                        if tick<=previous:continue
                        previous=tick;h,w=rgb.shape[:2];scale=min(1,640/w,480/h)
                        image=av.VideoFrame.from_ndarray(rgb,format='rgb24').reformat(width=max(2,int(w*scale)//2*2),height=max(2,int(h*scale)//2*2),format='rgb24').to_ndarray()
                        canvas=np.full((480,640,3),240,np.uint8);ih,iw=image.shape[:2];left,top=(640-iw)//2,(480-ih)//2;canvas[top:top+ih,left:left+iw]=image
                        frame=av.VideoFrame.from_ndarray(canvas,format='rgb24');frame.pts=offset+tick;frame.time_base=Fraction(1,15)
                        for packet in stream.encode(frame):container.mux(packet)
                    offset+=math.ceil((raw['end_ms']-raw['start_ms'])*15/1000)
                for packet in stream.encode():container.mux(packet)
        temporary.replace(VIDEO)
    finally:
        if owned and temporary.exists():temporary.unlink()
    saved={**recipe,'recipe_signature':signature,'sha256':digest(VIDEO),'bytes':VIDEO.stat().st_size}
    RECIPE.write_text(json.dumps(saved,ensure_ascii=False,indent=2)+'\n',encoding='utf8');return saved


def motion_validation(recipe,segments):
    regions=[]
    for part in recipe['parts']:
        if regions and regions[-1]['expected_action_id']==part['action_id'] and regions[-1]['end_ms']==part['composite_start_ms']:regions[-1]['end_ms']=part['composite_end_ms']
        else:regions.append({'start_ms':part['composite_start_ms'],'end_ms':part['composite_end_ms'],'expected_action_id':part['action_id']})
    for region in regions:
        start,end=region['start_ms']+500,region['end_ms']-500;denominator=end-start
        matched=sum(max(0,min(end,s['end_ms'])-max(start,s['start_ms'])) for s in segments if s['effective_action_id']==region['expected_action_id'])
        region.update(scored_start_ms=start,scored_end_ms=end,matched_ms=matched,evaluated_ms=denominator,matched_ratio=matched/denominator,
            passed=matched==denominator if region['expected_action_id'] is None else matched/denominator>=.8)
    return {'scope':'reviewed_source_regression_not_independent_accuracy','boundary_tolerance_ms':500,'regions':regions,'passed':all(r['passed'] for r in regions)}


def capture(scan):
    rows=scan.rows
    np.savez_compressed(CACHE,timestamps=np.asarray([r.stamp for r in rows],np.int64),points=np.asarray([r.points for r in rows],np.float32),
        world=np.asarray([r.world for r in rows],np.float32),sizes=np.asarray([(r.width,r.height) for r in rows],np.int32),
        people=np.asarray([r.people for r in rows],np.int16),bounds=np.asarray([r.bounds for r in rows],np.float32),
        identity_accepted=np.asarray([r.identity_accepted for r in rows],bool),pose_models=np.asarray([r.model for r in rows]),
        decisions_json=np.asarray(json.dumps(scan.decisions,ensure_ascii=False)),gaps_json=np.asarray(json.dumps(scan.unassessable)),
        end_ms=np.asarray(scan.end_ms),model_version=np.asarray(scan.model_version or ''))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--reuse-inference',action='store_true');parser.add_argument('--rebuild-source',action='store_true');args=parser.parse_args()
    if args.reuse_inference and args.rebuild_source:raise ValueError('重建素材不能同时复用旧输入推理')
    recipe=prepare(args.rebuild_source);print('Prepared real edited regression',recipe['bytes'],'bytes',flush=True)
    info,reason=resources()
    if info is None:raise RuntimeError('已发布识别模型不可用：'+str(reason))
    previous=json.loads(REPORT.read_text('utf8')) if REPORT.exists() else None
    if args.reuse_inference and (not previous or previous['source']['sha256']!=digest(VIDEO) or previous['numeric_cache_sha256']!=digest(CACHE) or previous['recognition_model_version']!=info['version']):
        raise RuntimeError('真实推理缓存身份不一致，未按旧缓存伪造本次识别')
    started=time.monotonic()
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp/tests',prefix='real-upload-') as folder:
        app=create_app({'DATABASE_PATH':str(Path(folder)/'app.sqlite3')});password=secrets.token_urlsafe(24)
        initialize_admin(app.extensions['database'],'Real_Upload',password);client=app.test_client();manager=app.extensions['video_analyses'];inputs=app.extensions['media_inputs']
        original=manager._scan
        captured={}
        def scanned(run,automatic,*extra,**kwargs):
            if args.reuse_inference and run.kind=='analysis':
                with np.load(CACHE,allow_pickle=False) as a:
                    rows=[NumericFrame(int(stamp),int(size[0]),int(size[1]),points.copy(),world.copy(),int(people),tuple(map(float,bounds)),str(model),bool(identity))
                        for stamp,size,points,world,people,bounds,model,identity in zip(a['timestamps'],a['sizes'],a['points'],a['world'],a['people'],a['bounds'],a['pose_models'],a['identity_accepted'])]
                    return OfflineScan(rows,json.loads(str(a['decisions_json'])),json.loads(str(a['gaps_json'])),int(a['end_ms']),str(a['model_version']),0)
            result=original(run,automatic,*extra,**kwargs)
            captured[run.kind]={'pose_calls':result.pose_calls,'first_source_ms':result.rows[0].stamp,'last_source_ms':result.rows[-1].stamp,'video_classification_windows':len(result.decisions)}
            if run.kind=='analysis':capture(result)
            return result
        manager._scan=scanned
        def post(path,data):
            token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
            return client.post(path,json=data,headers={'X-CSRFToken':token,'Idempotency-Key':__import__('uuid').uuid4().hex})
        try:
            response=post('/api/v1/auth/login',{'username':'Real_Upload','password':password});assert response.status_code==200
            token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
            with VIDEO.open('rb') as stream:response=client.post('/api/v1/inputs',data={'file':(stream,VIDEO.name)},headers={'X-CSRFToken':token,'Idempotency-Key':str(__import__('uuid').uuid4())})
            assert response.status_code==202,response.json;iid=response.json['data']['input_id'];inputs.threads[iid].join()
            assert inputs.snapshot(client.get('/api/v1/auth/me').json['data']['id'],iid)['status']=='ready'
            response=post('/api/v1/video-analyses',{'input_id':iid,'mode':'auto'});assert response.status_code==202,response.json
            pid=response.json['data']['analysis_id'];run=manager.runs[pid];last=None;last_print=time.monotonic()
            while run.thread.is_alive():
                run.thread.join(1);data=manager.store.snapshot(run.user_id,None,pid);state=(data['phase'],data['processed_ms'],data['status'])
                if state!=last or time.monotonic()-last_print>=30:
                    print('Progress',state,'elapsed',round(time.monotonic()-started,1),flush=True);last=state;last_print=time.monotonic()
            data=manager.snapshot(run.user_id,run.auth_id,pid);revision_check=None
            automatic_validation=motion_validation(recipe,data['segments'])
            automatic_segments=[{k:segment[k] for k in ('start_ms','end_ms','effective_action_id','kind','recognition_model_version')} for segment in data['segments']]
            row_segment=next((x for x in data['segments'] if x['effective_action_id']=='barbell_bent_over_row' and x['current_revision']),None)
            if args.reuse_inference and row_segment:
                prior_sid=row_segment['current_revision']['session_id']
                neighbors={x['current_revision']['session_id']:app.extensions['session_store'].snapshot(run.user_id,x['current_revision']['session_id'])['state_version']
                    for x in data['segments'] if x['id']!=row_segment['id'] and x['current_revision']}
                token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
                with VIDEO.open('rb') as stream:upload=client.post('/api/v1/inputs',data={'file':(stream,VIDEO.name)},headers={'X-CSRFToken':token,'Idempotency-Key':str(__import__('uuid').uuid4())})
                assert upload.status_code==202,upload.json;new_input=upload.json['data']['input_id'];inputs.threads[new_input].join()
                response=post('/api/v1/video-segments/'+row_segment['id']+'/revisions',{'input_id':new_input,'action_id':'barbell_bent_over_row','expected_version':row_segment['state_version']})
                assert response.status_code==202,response.json;rid=response.json['data']['revision_id'];manager.runs[rid].thread.join()
                revised=manager.revision(run.user_id,run.auth_id,rid);old=app.extensions['session_store'].snapshot(run.user_id,prior_sid)
                revision_check={'status':revised['status'],'is_current':revised['is_current'],'old_included_in_statistics':old['included_in_statistics'],
                    'old_superseded_by':old['superseded_by'],'current_session_id':revised['session_id'],
                    'neighbors_unchanged':all(app.extensions['session_store'].snapshot(run.user_id,sid)['state_version']==version for sid,version in neighbors.items()),
                    'requested_source_range':[row_segment['start_ms'],row_segment['end_ms']],'actual_scan':captured.get('revision'),
                    'temporary_input_copy_removed':not inputs._path(new_input).exists()}
                data=manager.snapshot(run.user_id,run.auth_id,pid)
            records=[]
            for segment in data['segments']:
                revision=segment['current_revision']
                if revision:records.append(app.extensions['session_store'].snapshot(run.user_id,revision['session_id'],True))
            expected={p['action_id'] for p in PARTS if p['action_id']};actual={p['effective_action_id'] for p in data['segments'] if p['effective_action_id']}
            movement=automatic_validation
            report={'scope':'real_video_engineering_regression_not_independent_accuracy','source':recipe,'status':data['status'],
                'parent':data,'records':records,'expected_actions':sorted(expected),'observed_actions':sorted(actual),
                'automatic_segments_before_manual_revision':automatic_segments,'classification_scored_before_manual_revision':True,
                'expected_action_names_present':expected<=actual,'all_expected_actions_observed':expected<=actual and movement['passed'],
                'source_phase_review':SOURCE_PHASE_REVIEW,'real_movement_validation_passed':movement['passed'],'movement_validation':movement,
                'recognition_model_version':info['version'],'posture_version':policy_version(),'specific_error_ground_truth':False,'independent_specific_error_accuracy':None,
                'inference_reused_for_parent':args.reuse_inference,'new_scan_calls':captured,'real_single_segment_revision':revision_check,
                'prior_segmentation':[{k:x[k] for k in ('start_ms','end_ms','effective_action_id')} for x in previous['parent']['segments']] if previous and args.reuse_inference else None,
                'elapsed_seconds':round(time.monotonic()-started,2),'original_upload_copy_removed':not inputs._path(iid).exists(),
                'numeric_cache_sha256':digest(CACHE) if CACHE.exists() else None,'paid_ai_calls':0}
            REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
            print(json.dumps({k:report[k] for k in ('status','observed_actions','all_expected_actions_observed','elapsed_seconds','original_upload_copy_removed')},ensure_ascii=False),flush=True)
        finally:
            manager.stop();inputs.stop();app.extensions['database'].dispose()


if __name__=='__main__':main()
