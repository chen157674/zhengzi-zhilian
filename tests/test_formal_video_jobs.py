"""10-B/C 的任务、取消和持久化集成，只消费隔离媒体与受控事件。"""
import io
import threading
import time
import unittest
from unittest.mock import patch
from sqlalchemy import select,update

import test_formal_records as shared
from test_formal_media import sample_mp4
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.database import StorageError
from fitness_app.auth import APIError


class SyntheticPose:
    def __init__(self,**unused):pass
    def detect(self,rgb,stamp):
        points=[dict(x=.5,y=.2,z=0,visibility=.95,presence=.95) for unused in range(33)]
        for index,xy in {11:(.4,.3),12:(.6,.3),13:(.35,.4),14:(.65,.4),15:(.3,.5),16:(.7,.5),23:(.4,.6),24:(.6,.6),25:(.4,.75),26:(.6,.75),27:(.4,.9),28:(.6,.9)}.items():points[index].update(x=xy[0],y=xy[1])
        return {'poses':[points],'model':'D-POSE-video-business'}
    def close(self):pass


class Events:
    def reset(self,reason):pass
    def process(self,frame,context):
        stamp=frame['captured_ms']
        return {'events':[dict(side='none',start_ms=stamp-100,end_ms=stamp,algorithm_version='D-EVENT-video',feature_schema_version='D-POSE-video',assessments=[])]} if stamp>0 else {}


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.client=self.fx.client;self.manager=self.fx.app.extensions['video_jobs'];self.media=self.fx.app.extensions['media_inputs']
        self.manager.pose_factory=SyntheticPose;self.manager.engine_provider=lambda action:Events();self.gates=[]

    def tearDown(self):
        for gate in self.gates:gate.set()
        self.manager.stop();self.media.stop();self.fx.tearDown()

    def video(self):
        self.payload=sample_mp4();info=self.media.upload(self.fx.user,self.fx.auth,io.BytesIO(self.payload),s.new_id());self.input_id=info['input_id'];self.media.threads[self.input_id].join(5)
        self.config=dict(action_id='bodyweight_squat',capability_version=get_action('bodyweight_squat')['capability_version'],source='video',mode='free',target=None,input_id=self.input_id)
        self.session_key=s.new_id();response=self.fx.post(self.client,'/api/v1/sessions',self.config,self.session_key);self.assertEqual(response.status_code,201,response.json)
        return response.json['data']['session_id']

    def start(self,sid,key=None):
        response=self.fx.post(self.client,'/api/v1/jobs/video',dict(session_id=sid,expected_version=0),key)
        self.assertEqual(response.status_code,202,response.json);return response.json['data']['job_id']

    def wait(self,jid):
        self.manager.runs[jid].thread.join(6);self.assertFalse(self.manager.runs[jid].thread.is_alive())
        response=self.client.get('/api/v1/jobs/'+jid);self.assertEqual(response.status_code,200,response.json);return response.json['data']

    def blocked_pose(self):
        entered=threading.Event();release=threading.Event();self.gates.append(release)
        class Blocking(SyntheticPose):
            def detect(self,rgb,stamp):
                if stamp==200:entered.set();release.wait(5)
                return super().detect(rgb,stamp)
        self.manager.pose_factory=Blocking;return entered,release

    def test_completion_replay_source_match_without_reanalysis(self):
        sid=self.video();key=s.new_id();jid=self.start(sid,key);data=self.wait(jid)
        self.assertEqual((data['status'],data['processed_ms'],data['processed_frames']),('completed',300,4));self.assertEqual(data['session']['total_count'],3)
        self.assertEqual(data['session']['active_ms'],300);self.assertTrue(data['stopped']);self.assertEqual(data['save_state'],'clean');self.assertFalse(self.media._path(self.input_id).exists())
        self.assertEqual(self.start(sid,key),jid)
        replay=self.fx.post(self.client,'/api/v1/sessions',self.config,self.session_key);self.assertEqual(replay.status_code,201,replay.json);self.assertEqual(replay.json['data']['session_id'],sid)
        token=self.client.get('/api/v1/auth/csrf').json['data']['csrf_token'];path='/api/v1/records/'+sid+'/source-match'
        for payload,status in [(self.payload,200),(self.payload+b'changed',422)]:
            response=self.client.post(path,data={'file':(io.BytesIO(payload),'same-name.mp4')},headers={'X-CSRFToken':token});self.assertEqual(response.status_code,status,response.json)
        self.assertEqual(self.manager.runs[jid].frames,4);self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['total_count'],3)
        cancel=self.fx.post(self.client,'/api/v1/jobs/'+jid+'/cancel',{'expected_version':0});self.assertEqual(cancel.status_code,202);self.assertEqual(cancel.json['data']['status'],'completed')

    def test_cancel_blocks_inflight_result_and_retains_confirmed_partial(self):
        sid=self.video();entered,release=self.blocked_pose();jid=self.start(sid);self.assertTrue(entered.wait(5))
        before=self.client.get('/api/v1/jobs/'+jid).json['data'];began=time.monotonic()
        cancel=self.fx.post(self.client,'/api/v1/jobs/'+jid+'/cancel',{'expected_version':before['state_version']})
        self.assertEqual(cancel.status_code,202,cancel.json);self.assertLess(time.monotonic()-began,2);self.assertFalse(cancel.json['data']['stopped'])
        self.assertTrue(self.fx.app.extensions['vision_slot'].owns(self.fx.user,jid,'video'))
        release.set();data=self.wait(jid);self.assertEqual(data['status'],'cancelled');self.assertEqual(data['session']['total_count'],1);self.assertEqual(data['processed_ms'],100)
        self.assertEqual(data['session']['status'],'ended_early');self.assertFalse(self.fx.app.extensions['vision_slot'].owns(self.fx.user,jid,'video'))

    def test_new_page_owner_scope_and_revoked_auth_blocks_late_results(self):
        sid=self.video();entered,release=self.blocked_pose();jid=self.start(sid);self.assertTrue(entered.wait(5))
        second=self.fx.app.test_client();self.fx.login(second,'Record_User');self.assertEqual(second.get('/api/v1/jobs/'+jid).status_code,200)
        other=self.fx.app.test_client();self.fx.login(other,'Other_User');self.assertEqual(other.get('/api/v1/jobs/'+jid).status_code,404)
        with self.fx.db.transaction() as c:c.execute(update(s.auth_sessions).where(s.auth_sessions.c.id==self.fx.auth).values(revoked_at_ms=self.fx.now[0]))
        self.manager.revoke(self.fx.user,self.fx.auth);release.set();self.manager.runs[jid].thread.join(5)
        data=second.get('/api/v1/jobs/'+jid).json['data'];self.assertEqual(data['status'],'interrupted');self.assertEqual(data['session']['total_count'],1)
        self.assertEqual(data['session']['end_reason'],'auth_revoked');self.assertFalse(self.media._path(self.input_id).exists())

    def test_save_failure_stops_worker_then_retries_only_original_batch(self):
        sid=self.video();entered,release=self.blocked_pose();jid=self.start(sid);self.assertTrue(entered.wait(5))
        def fault(point):
            if point=='before_commit':raise StorageError('D-FAULT')
        self.fx.store.fault=fault;release.set();data=self.wait(jid)
        self.assertTrue(data['stopped']);self.assertEqual(data['save_state'],'save_failed');self.assertEqual(data['session']['total_count'],1)
        self.assertFalse(self.media._path(self.input_id).exists());self.fx.store.fault=None
        response=self.fx.post(self.client,'/api/v1/sessions/'+sid+'/commands',{'command':'retry_save','expected_version':data['session']['state_version']})
        self.assertEqual(response.status_code,200,response.json);result=response.json['data'];self.assertEqual(result['total_count'],2);self.assertEqual(result['status'],'interrupted')
        self.assertEqual(result['save_state'],'clean');self.assertEqual(self.manager.runs[jid].frames,3);self.assertEqual(self.manager.runs[jid].processed_ms,200)

    def test_real_pose_executor_on_existing_demo_excerpt(self):
        import av
        from fractions import Fraction
        from fitness_app.paths import ROOT
        from fitness_app.pose import PoseService
        clip=io.BytesIO()
        with av.open(str(ROOT/'web/demo.mp4')) as original,av.open(clip,'w',format='mp4') as output:
            target=output.add_stream('libx264',rate=10);target.width=640;target.height=360;target.pix_fmt='yuv420p'
            for index,frame in enumerate(original.decode(video=0)):
                if index>=6:break
                frame=frame.reformat(width=640,height=360,format='yuv420p');frame.pts=index;frame.time_base=Fraction(1,10)
                for packet in target.encode(frame):output.mux(packet)
            for packet in target.encode():output.mux(packet)
        info=self.media.upload(self.fx.user,self.fx.auth,io.BytesIO(clip.getvalue()),s.new_id());input_id=info['input_id'];self.media.threads[input_id].join(5)
        config=dict(action_id='bodyweight_squat',capability_version=get_action('bodyweight_squat')['capability_version'],source='video',mode='free',target=None,input_id=input_id)
        sid=self.fx.post(self.client,'/api/v1/sessions',config).json['data']['session_id']
        self.manager.pose_factory=PoseService;self.manager.engine_provider=None;self.manager.allow_observer=True
        jid=self.start(sid);data=self.wait(jid)
        self.assertEqual(data['status'],'completed');self.assertEqual(data['processed_frames'],6);self.assertEqual(data['processed_ms'],500)
        self.assertFalse(data['counting_available']);self.assertFalse(data['skeleton_available']);self.assertEqual(data['session']['last_event_seq'],0)

    def test_input_expiry_and_restart_clear_only_registered_copies(self):
        sid=self.video();path=self.media._path(self.input_id);self.assertTrue(path.exists())
        original=self.media.root.parent/'retained-original.mp4';original.write_bytes(self.payload)
        try:
            self.fx.now[0]+=31*60*1000;self.media.expire();self.assertFalse(path.exists());self.assertTrue(original.exists())
            self.fx.store.recover_interrupted();self.media.recover()
            self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['status'],'ready');self.assertEqual(self.client.get('/api/v1/records').json['data']['total'],0)
        finally:original.unlink()

    def test_late_decode_failure_and_cleanup_retry_preserve_partial_result(self):
        sid=self.video();real_reader=self.manager.frames
        def broken(path,info):
            iterator=real_reader(path,info)
            try:
                yield next(iterator);yield next(iterator)
                raise APIError('UNDECODABLE_MEDIA','D-FAULT late decode',422)
            finally:iterator.close()
        self.manager.frames=broken
        with patch.object(self.media,'_remove_file',side_effect=PermissionError('D-FAULT locked temporary copy')):
            jid=self.start(sid);data=self.wait(jid)
            self.assertEqual(data['status'],'failed');self.assertEqual(data['error_code'],'UNDECODABLE_MEDIA');self.assertEqual(data['session']['total_count'],1)
            self.assertEqual(data['processed_ms'],100);self.assertTrue(data['cleanup_pending']);self.assertTrue(self.media._path(self.input_id).exists())
        response=self.fx.post(self.client,'/api/v1/sessions/'+sid+'/commands',{'command':'retry_save','expected_version':data['session']['state_version']})
        self.assertEqual(response.status_code,200,response.json);self.assertFalse(self.media._path(self.input_id).exists())
        self.assertFalse(self.manager.snapshot(self.fx.user,jid)['cleanup_pending']);self.assertEqual(response.json['data']['total_count'],1)

    def test_unknown_total_and_consumed_input_cleanup_guard(self):
        sid=self.video();entered,release=self.blocked_pose()
        # 缺总量的读取器语义，不制造巨大／损坏媒体；实际帧仍来自同一小片。
        with self.fx.db.transaction() as c:c.execute(update(s.media_inputs).where(s.media_inputs.c.id==self.input_id).values(duration_ms=None))
        jid=self.start(sid);self.assertTrue(entered.wait(5));data=self.manager.snapshot(self.fx.user,jid)
        self.assertIsNone(data['total_ms']);self.assertFalse(self.media.cleanup(self.fx.user,self.input_id));self.assertTrue(self.media._path(self.input_id).exists())
        release.set();self.assertEqual(self.wait(jid)['status'],'completed')


if __name__=='__main__':unittest.main()
