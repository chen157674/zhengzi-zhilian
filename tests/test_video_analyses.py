"""真实API→后台管线→分段/修订/统计闭环，分类和姿态用明确替身，不冒充准确率。"""
import io,json,threading,time,unittest
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import select,func
import numpy as np
from fitness_app import db_schema as s
from fitness_app.database import StorageError
from fitness_app.posture_rules import rules_for
from fitness_app.pose_input import input_policy_snapshot
from fitness_app.records import effective_records
import test_formal_records as fixtures
import test_offline_video as offline
import test_formal_media as mediafixtures


class SnapshotConsistencyTests(unittest.TestCase):
    def test_terminal_mutation_cannot_interleave_database_and_worker_snapshot(self):
        from fitness_app.video_analyses import VideoAnalyses
        manager=object.__new__(VideoAnalyses);manager.lock=threading.RLock()
        pid=s.new_id();run=SimpleNamespace(stopped=False,finalization_pending=False)
        manager.runs={pid:run};manager.writer=SimpleNamespace(unhealthy=False)
        read=threading.Event();attempt=threading.Event();finished=threading.Event()
        def finalize():
            read.wait(2);attempt.set()
            with manager.lock:run.stopped=True;finished.set()
        def database_snapshot(*args):
            read.set();self.assertTrue(attempt.wait(2))
            self.assertFalse(finished.wait(.1),'收尾不应插入数据库和线程状态的两次读取之间')
            return {'status':'running','segments':[],'cleanup_pending':False,'source_available':True}
        manager.store=SimpleNamespace(snapshot=database_snapshot)
        worker=threading.Thread(target=finalize);worker.start()
        try:
            result=manager.snapshot('user','auth',pid)
            self.assertEqual(result['status'],'running');self.assertTrue(result['processing'])
        finally:read.set();worker.join(2)
        self.assertTrue(finished.is_set())


class Engine:
    def __init__(self,action,control):self.action=action;self.control=control;self.previous=None;self.counting_available=False
    def reset(self,reason):self.previous=None
    def process(self,frame,context):
        if self.control.get('fail'):raise RuntimeError('injected engine failure')
        stamp=frame['captured_ms']
        if self.previous is None:self.previous=stamp
        if stamp-self.previous<400:return {}
        rule=rules_for(self.action)[0];start=self.previous;self.previous=stamp
        if self.control.get('before_finding'):self.control['before_finding']()
        return {'posture_findings':[{'code':rule['code'],'side':'none','status':'issue','start_ms':start,'end_ms':stamp,
            'rule_version':rule['rule_version'],'evidence_level':'reference','body_parts':rule['body_parts'],'message':'合成存储夹具',
            'suggestion':rule['suggestion'],'evidence':{'view':'side','value':.3,'threshold':rule['threshold']}}]}


class Predictor:
    def __init__(self):self.calls=0
    def predict(self,images,timestamps,arrays):
        self.calls+=1
        return {'action_id':'barbell_overhead_press' if timestamps[-1]<6000 else 'conventional_deadlift','model_version':'fixture-v1','score':.95}


class VideoAnalysesTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.RecordTests();self.fx.setUp();self.manager=self.fx.app.extensions['video_analyses'];self.inputs=self.fx.app.extensions['media_inputs']
        self.poses=[];self.reads=[];self.predictor=Predictor();self.control={};self.block=None
        def pose_factory(**kw):
            pose=offline.Pose();original=pose.detect
            def detect(rgb,stamp):
                if self.block:self.block[0].set();self.block[1].wait(5)
                return original(rgb,stamp)
            pose.detect=detect;self.poses.append(pose);return pose
        self.manager.pose_factory=pose_factory;self.fx.store.processing_policy=input_policy_snapshot(pose_factory)
        self.manager.predictor_provider=lambda:self.predictor;self.manager.frames=self.frames
        self.manager.engine_provider=lambda action:Engine(action,self.control)
    def tearDown(self):
        if self.block:self.block[1].set()
        self.manager.stop();self.inputs.stop();self.fx.tearDown()
    def frames(self,path,info,*,start_ms=0,end_ms=None):
        self.reads.append((start_ms,end_ms))
        for stamp in range(start_ms,end_ms or info['duration_ms'],40):yield stamp,np.full((48,64,3),180,np.uint8)
    def upload(self,content=None):
        payload=content or mediafixtures.sample_mp4(frames=100)
        token=self.fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        response=self.fx.client.post('/api/v1/inputs',data={'file':(io.BytesIO(payload),'training.mp4')},headers={'X-CSRFToken':token,'Idempotency-Key':s.new_id()})
        self.assertEqual(response.status_code,202,response.json);iid=response.json['data']['input_id']
        self.inputs.threads[iid].join(5);self.assertEqual(self.inputs.snapshot(self.fx.user,iid)['status'],'ready');return iid
    def post(self,path,data,key=None,client=None):return self.fx.post(client or self.fx.client,path,data,key)
    def start(self,iid,key=None,**values):
        response=self.post('/api/v1/video-analyses',{'input_id':iid,**values},key)
        self.assertEqual(response.status_code,202,response.json);return response.json['data']['analysis_id']
    def wait(self,identity,revision=False):
        prefix='/api/v1/video-revisions/' if revision else '/api/v1/video-analyses/'
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            response=self.fx.client.get(prefix+identity);self.assertEqual(response.status_code,200,response.json);data=response.json['data']
            run=self.manager.runs.get(identity)
            if data['status'] in {'completed','cancelled','failed','interrupted'} and run and run.stopped:
                run.thread.join(2);return data
            time.sleep(.02)
        self.fail('任务未在隔离时限内结束')
    def count(self):
        with self.fx.db.transaction(False) as c:return c.scalar(select(func.count()).select_from(s.analysis_sessions).where(s.analysis_sessions.c.user_id==self.fx.user,effective_records()))
    def revise(self,parent,segment,action,iid,key=None):
        response=self.post('/api/v1/video-segments/'+segment['id']+'/revisions',{'input_id':iid,'action_id':action,'expected_version':segment['state_version']},key)
        self.assertEqual(response.status_code,202,response.json);return response.json['data']['revision_id']

    def test_auto_real_upload_metadata_saves_multiple_segments_and_only_current_results(self):
        iid=self.upload();key=s.new_id();pid=self.start(iid,key);data=self.wait(pid)
        self.assertEqual(data['status'],'completed',data);self.assertEqual(data['result_completeness'],'full');self.assertGreaterEqual(len(data['segments']),2)
        self.assertEqual({x['effective_action_id'] for x in data['segments']},{'barbell_overhead_press','conventional_deadlift'})
        self.assertEqual(self.count(),len(data['segments']));self.assertEqual(len(self.poses),1);self.assertEqual(self.poses[0].calls,250)
        self.assertFalse(self.inputs._path(iid).exists());self.assertFalse(data['source_available'])
        self.assertEqual(self.start(iid,key),pid);self.assertEqual(len(self.poses),1)
        for segment in data['segments']:
            session=self.fx.store.snapshot(self.fx.user,segment['current_revision']['session_id'],True)
            self.assertTrue(session['included_in_statistics']);self.assertEqual(session['source'],'video');self.assertEqual(session['config']['exercise_basis'],'automatically_recognized')
            self.assertEqual(session['config']['source_start_ms'],segment['start_ms'])

    def test_manual_parent_and_single_segment_revision_do_not_call_classifier_or_touch_neighbor(self):
        iid=self.upload();pid=self.start(iid,mode='manual',action_id='hammer_curl');data=self.wait(pid)
        self.assertEqual(data['status'],'completed',data);self.assertEqual(self.predictor.calls,0);self.assertEqual(data['segments'][0]['current_revision']['origin'],'manual')
        old=data['segments'][0]['current_revision']['session_id'];segment=data['segments'][0];iid=self.upload();rid=self.revise(data,segment,'standing_dumbbell_curl',iid)
        result=self.wait(rid,True);self.assertEqual(result['status'],'completed',result);self.assertTrue(result['is_current']);self.assertEqual(self.count(),1)
        self.assertEqual(self.reads[-1],(segment['start_ms'],segment['end_ms']));self.assertEqual(self.predictor.calls,0)
        self.assertFalse(self.fx.store.snapshot(self.fx.user,old)['included_in_statistics'])

    def test_only_target_range_is_reanalysed_with_new_pose_pass_and_neighbor_unchanged(self):
        parent=self.wait(self.start(self.upload()));first=parent['segments'][0];neighbor=parent['segments'][1]['current_revision']['session_id']
        before=self.fx.store.snapshot(self.fx.user,neighbor)['state_version'];old_calls=self.predictor.calls
        rid=self.revise(parent,first,'dumbbell_shoulder_press',self.upload());result=self.wait(rid,True)
        self.assertEqual(result['status'],'completed',result);self.assertEqual(self.predictor.calls,old_calls)
        self.assertEqual(self.reads[-1],(first['start_ms'],first['end_ms']))
        self.assertEqual(self.fx.store.snapshot(self.fx.user,neighbor)['state_version'],before);self.assertEqual(self.count(),len(parent['segments']))

    def test_cancel_inflight_pose_does_not_publish_late_result(self):
        self.block=(threading.Event(),threading.Event());pid=self.start(self.upload());self.assertTrue(self.block[0].wait(4))
        before=self.fx.client.get('/api/v1/video-analyses/'+pid).json['data'];r=self.post('/api/v1/video-analyses/'+pid+'/cancel',{'expected_version':before['state_version']})
        self.assertEqual(r.status_code,202,r.json);self.block[1].set();data=self.wait(pid)
        self.assertEqual(data['status'],'cancelled',data);self.assertEqual(self.count(),0);self.assertTrue(self.poses[0].closed)

    def test_failed_revision_keeps_old_current_and_source_cleanup_failure_can_be_retried(self):
        parent=self.wait(self.start(self.upload(),mode='manual',action_id='hammer_curl'));segment=parent['segments'][0];old=segment['current_revision']['session_id']
        self.control['fail']=True;iid=self.upload()
        with patch.object(self.inputs,'cleanup',side_effect=OSError('fixture cleanup unavailable')):
            rid=self.revise(parent,segment,'standing_dumbbell_curl',iid);result=self.wait(rid,True)
        self.assertEqual(result['status'],'failed');self.assertTrue(result['cleanup_pending']);self.assertEqual(self.count(),1)
        self.assertEqual(self.fx.client.get('/api/v1/video-analyses/'+parent['id']).json['data']['segments'][0]['current_revision']['session_id'],old)
        response=self.post('/api/v1/video-revisions/'+rid+'/cleanup',{});self.assertEqual(response.status_code,200,response.json);self.assertFalse(self.inputs._path(iid).exists())

    def test_owner_and_source_mismatch_do_not_consume_other_sources_or_run_models(self):
        parent=self.wait(self.start(self.upload(),mode='manual',action_id='hammer_curl'));segment=parent['segments'][0]
        other=self.fx.app.test_client();self.fx.login(other,'other_user')
        self.assertEqual(other.get('/api/v1/video-analyses/'+parent['id']).status_code,404)
        r=self.post('/api/v1/video-segments/'+segment['id']+'/revisions',{'input_id':parent['input_id'],'action_id':'hammer_curl','expected_version':segment['state_version']},client=other)
        self.assertEqual(r.status_code,404,r.json)
        different=self.upload(mediafixtures.sample_mp4(frames=90));before=len(self.poses)
        r=self.post('/api/v1/video-segments/'+segment['id']+'/revisions',{'input_id':different,'action_id':'hammer_curl','expected_version':segment['state_version']})
        self.assertEqual(r.status_code,422,r.json);self.assertEqual(r.json['error']['code'],'SOURCE_MISMATCH');self.assertEqual(len(self.poses),before)
        self.assertEqual(self.inputs.snapshot(self.fx.user,different)['status'],'ready')

    def test_storage_failure_is_frozen_and_recovery_saves_only_original_batch(self):
        enabled=[False]
        def fault(point):
            if enabled[0] and point=='before_commit':raise StorageError('injected storage failure')
        self.fx.store.fault=fault;self.control['before_finding']=lambda:enabled.__setitem__(0,True)
        pending_sid=None
        try:
            pid=self.start(self.upload(),mode='manual',action_id='hammer_curl');data=self.wait(pid)
            self.assertEqual(data['status'],'failed');self.assertEqual(data['error_code'],'SAVE_OR_STORAGE_FAILED')
            revision=data['segments'][0]['revisions'][0];pending_sid=revision['session_id'];self.assertIsNotNone(pending_sid)
            self.assertEqual(self.fx.writer.snapshot(self.fx.user,pending_sid)['save_state'],'save_failed');self.assertEqual(self.count(),0)
            before=len(self.poses);enabled[0]=False;self.control.clear()
            snapshot=self.fx.writer.snapshot(self.fx.user,pending_sid)
            response=self.post('/api/v1/sessions/'+pending_sid+'/commands',{'command':'retry_save','expected_version':snapshot['state_version']})
            self.assertEqual(response.status_code,200,response.json);self.assertEqual(response.json['data']['save_state'],'clean')
            self.assertEqual(len(self.poses),before);self.assertEqual(self.count(),0)
        finally:
            self.fx.store.fault=None
            if pending_sid and self.fx.writer.unhealthy:
                data=self.fx.writer.snapshot(self.fx.user,pending_sid)
                self.fx.writer.discard(self.fx.user,pending_sid,self.fx.auth,data['state_version'],s.new_id())

    def test_logout_during_pose_discards_late_work_and_preserves_owner_read_after_relogin(self):
        self.block=(threading.Event(),threading.Event());pid=self.start(self.upload());self.assertTrue(self.block[0].wait(4))
        response=self.post('/api/v1/auth/logout',{});self.assertEqual(response.status_code,200,response.json)
        self.block[1].set();run=self.manager.runs[pid];run.thread.join(6);self.assertFalse(run.thread.is_alive())
        data=self.manager.store.snapshot(self.fx.user,None,pid);self.assertEqual(data['status'],'interrupted');self.assertEqual(self.count(),0)
        self.fx.login(self.fx.client,'Record_User');self.assertEqual(self.fx.client.get('/api/v1/video-analyses/'+pid).status_code,200)

    def test_terminal_state_write_failure_is_explicit_and_retried_without_new_inference(self):
        iid=self.upload()
        with patch.object(self.manager.store,'finish',side_effect=StorageError('terminal metadata fixture')):
            pid=self.start(iid,mode='manual',action_id='hammer_curl');data=self.wait(pid)
        self.assertEqual(data['status'],'failed');self.assertFalse(data['terminal_persisted']);self.assertTrue(data['finalization_pending']);self.assertFalse(data['can_reanalyse'])
        before=len(self.poses)
        response=self.post('/api/v1/video-analyses/'+pid+'/retry-finalize',{'expected_version':data['state_version']})
        self.assertEqual(response.status_code,200,response.json);data=response.json['data'];self.assertEqual(data['status'],'completed');self.assertTrue(data['terminal_persisted'])
        self.assertFalse(data['finalization_pending']);self.assertEqual(len(self.poses),before);self.assertEqual(self.count(),1)

    def test_failed_revision_terminal_write_is_recoverable_without_touching_current(self):
        parent=self.wait(self.start(self.upload(),mode='manual',action_id='hammer_curl'));segment=parent['segments'][0];old=segment['current_revision']['session_id']
        self.control['fail']=True;iid=self.upload()
        with patch.object(self.manager.store,'fail_revision',side_effect=StorageError('revision metadata fixture')):
            rid=self.revise(parent,segment,'standing_dumbbell_curl',iid);data=self.wait(rid,True)
        self.assertEqual(data['status'],'failed');self.assertTrue(data['finalization_pending']);self.assertFalse(data['terminal_persisted'])
        current=self.fx.client.get('/api/v1/video-analyses/'+parent['id']).json['data'];self.assertFalse(current['can_reanalyse'])
        self.assertEqual(current['segments'][0]['revisions'][-1]['status'],'failed');before=len(self.poses)
        response=self.post('/api/v1/video-revisions/'+rid+'/retry-finalize',{'expected_version':data['state_version']})
        self.assertEqual(response.status_code,200,response.json);self.assertTrue(response.json['data']['terminal_persisted'])
        self.assertFalse(response.json['data']['finalization_pending']);self.assertEqual(len(self.poses),before);self.assertEqual(self.count(),1)
        self.assertEqual(self.fx.client.get('/api/v1/video-analyses/'+parent['id']).json['data']['segments'][0]['current_revision']['session_id'],old)


if __name__=='__main__':unittest.main()
