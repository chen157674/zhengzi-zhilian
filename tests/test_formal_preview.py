from io import BytesIO
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch,Mock
from PIL import Image
from fitness_app import create_app
from fitness_app.catalogue import get_action
from fitness_app.auth import APIError,initialize_admin
from fitness_app.database import ConflictError
from fitness_app.paths import ROOT
from fitness_app.preview import PreviewManager
from fitness_app.vision_slot import VisionSlot
from fitness_app.frame_pipeline import decode_jpeg,jpeg_dimensions,framing
from fitness_app.db_schema import new_id,now_ms


def jpeg(width=32,height=24,format='JPEG',exif=None):
    stream=BytesIO();kwargs={'exif':exif} if exif is not None else {}
    Image.new('RGB',(width,height),(200,30,50)).save(stream,format=format,**kwargs);return stream.getvalue()


def pose():return {'poses':[[dict(x=.5,y=.5,z=0,visibility=.9,presence=.9) for unused in range(33)]]}


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.time=[0.0];self.now=now_ms();self.slot=VisionSlot()
        self.manager=PreviewManager(self.slot,monotonic=lambda:self.time[0],clock=lambda:self.now)
        self.start_patch=patch.object(self.manager,'start');self.start_patch.start()
        self.uid,self.auth=new_id(),new_id();self.payload=dict(action_id='bodyweight_squat',capability_version=get_action('bodyweight_squat')['capability_version'],page_id=new_id())
        self.created=self.manager.create(self.uid,self.auth,self.now+60000,self.payload,new_id());self.pid=self.created['preview_id'];self.token=self.created['preview_token']
        self.frame=jpeg()

    def tearDown(self):self.manager.stop();self.start_patch.stop()

    def metadata(self,seq):return dict(stream_epoch=self.created['stream_epoch'],frame_seq=seq,captured_ms=seq*100,width=32,height=24)

    def send(self,seq):return self.manager.submit(self.uid,self.pid,self.token,self.metadata(seq),self.frame)

    def test_release_closes_async_recognizer_before_discarding_reference(self):
        recognizer=Mock();self.manager.active['recognizer']=recognizer
        self.manager.release(self.uid,self.pid,{'preview_token':self.token,'stream_epoch':self.created['stream_epoch']},new_id())
        recognizer.close.assert_called_once_with()
        self.assertIsNone(self.manager.history[self.pid]['recognizer'])

    def test_jpeg_actual_format_dimensions_orientation_and_rgb(self):
        self.assertEqual(jpeg_dimensions(self.frame),(32,24));rgb=decode_jpeg(self.frame)
        self.assertEqual(rgb.shape,(24,32,3));self.assertGreater(int(rgb[0,0,0]),int(rgb[0,0,2]))
        for bad in [b'not-jpeg',jpeg(format='PNG'),jpeg(1921,1)]:
            with self.assertRaises(APIError):jpeg_dimensions(bad)
        exif=Image.Exif();exif[274]=6
        with self.assertRaises(APIError):jpeg_dimensions(jpeg(exif=exif))
        meta=self.metadata(0);meta['width']=31
        with self.assertRaises(APIError):self.manager.submit(self.uid,self.pid,self.token,meta,self.frame)

    def test_near_side_visibility_bilateral_requirements_and_ambiguity(self):
        data=pose();data['poses'][0][12]['visibility']=0
        self.assertEqual(framing('bodyweight_squat',data)['code'],'VISIBLE')
        self.assertEqual(framing('standing_dumbbell_curl',data)['code'],'VISIBLE')
        self.assertEqual(framing('high_knees',data)['code'],'MISSING_PARTS')
        data['poses'][0][11]['x']=float('nan')
        result=framing('bodyweight_squat',data);self.assertFalse(result['usable']);self.assertEqual(result['landmarks'][11],{'valid':False})
        self.assertEqual(framing('bodyweight_squat',{'poses':[]})['code'],'NO_PERSON')
        result=framing('bodyweight_squat',{'poses':[pose()['poses'][0],pose()['poses'][0]]})
        self.assertEqual(result['code'],'TARGET_AMBIGUOUS');self.assertFalse(result['landmarks'])

    def test_bounded_latest_queue_duplicate_stale_and_rate(self):
        self.send(0);packet=self.manager._next();self.send(1)
        self.assertTrue(self.send(2)['replaced_pending']);self.assertEqual(self.manager.snapshot(self.uid,self.pid)['queue']['pending'],1)
        self.assertTrue(self.send(2)['duplicate']);self.assertEqual(self.manager.snapshot(self.uid,self.pid)['queue']['accepted'],3)
        with self.assertRaises(ConflictError):self.send(1)
        altered=self.metadata(2);altered['captured_ms']+=1
        with self.assertRaises(ConflictError):self.manager.submit(self.uid,self.pid,self.token,altered,self.frame)
        for seq in range(3,30):self.send(seq)
        with self.assertRaises(APIError) as failure:self.send(30)
        self.assertEqual(failure.exception.code,'FRAME_RATE_LIMIT')
        self.manager._publish(*packet,pose(),1)
        self.assertEqual(self.manager._next()[1]['frame_seq'],29)
        # 模拟处理完成，避免在本测试的关闭后保留占用。
        self.manager.processing_id=None

    def test_lease_get_no_renew_auth_expiry_and_foreign_isolation(self):
        for moment in [5,10,14]:self.time[0]=moment;self.manager.snapshot(self.uid,self.pid)
        self.time[0]=15
        self.assertEqual(self.manager.snapshot(self.uid,self.pid)['status'],'expired');self.assertFalse(self.slot.owns(self.uid,self.pid,'preview'))
        with self.assertRaises(APIError):self.send(0)
        with self.assertRaises(APIError) as foreign:self.manager.snapshot(new_id(),self.pid)
        self.assertEqual(foreign.exception.status,404)
        self.created=self.manager.create(self.uid,self.auth,self.now+100,self.payload,new_id());self.pid=self.created['preview_id'];self.now+=101
        self.assertEqual(self.manager.snapshot(self.uid,self.pid)['status'],'expired')

    def test_stale_inflight_result_fenced_and_slot_not_released_early(self):
        self.send(0);item,packet=self.manager._next()
        payload={'preview_token':self.token,'stream_epoch':self.created['stream_epoch']};key=new_id()
        result=self.manager.release(self.uid,self.pid,payload,key)
        self.assertEqual(self.manager.release(self.uid,self.pid,payload,key),result)
        with self.assertRaises(APIError):self.slot.claim(new_id(),new_id(),'video')
        self.manager._publish(item,packet,pose(),1)
        self.assertIsNone(self.manager.snapshot(self.uid,self.pid)['result']);self.slot.claim(self.uid,'next','video');self.slot.release(self.uid,'next','video')

    def test_stability_gap_missing_clears_points_and_only_input_renews(self):
        for seq in range(7):
            self.send(seq);item,packet=self.manager._next();self.manager._publish(item,packet,pose(),1)
        self.assertTrue(self.manager.snapshot(self.uid,self.pid)['result']['framing']['ready_for_tracking'])
        self.time[0]=10;meta=self.metadata(7);meta['captured_ms']=1500
        self.manager.submit(self.uid,self.pid,self.token,meta,self.frame);item,packet=self.manager._next();self.manager._publish(item,packet,pose(),1)
        self.assertFalse(self.manager.snapshot(self.uid,self.pid)['result']['framing']['ready_for_tracking'])
        self.time[0]=24;self.assertEqual(self.manager.snapshot(self.uid,self.pid)['status'],'active')
        self.time[0]=25;self.assertEqual(self.manager.snapshot(self.uid,self.pid)['status'],'expired')

    def test_one_upload_claim_and_idempotent_create_payload(self):
        self.manager.release(self.uid,self.pid,{'preview_token':self.token,'stream_epoch':self.created['stream_epoch']},new_id())
        key=new_id();one=self.manager.create(self.uid,self.auth,self.now+60000,self.payload,key)
        self.assertEqual(self.manager.create(self.uid,self.auth,self.now+60000,self.payload,key),one)
        with self.assertRaises(ConflictError):self.manager.create(self.uid,self.auth,self.now+60000,{**self.payload,'page_id':new_id()},key)
        release=self.manager.begin_upload(self.uid,one['preview_id'],one['preview_token'])
        with self.assertRaises(APIError) as concurrent:self.manager.begin_upload(self.uid,one['preview_id'],one['preview_token'])
        self.assertEqual(concurrent.exception.status,429);release();self.manager.begin_upload(self.uid,one['preview_id'],one['preview_token'])()
        self.manager.revoke(self.uid,self.auth);self.assertEqual(self.manager.snapshot(self.uid,one['preview_id'])['status'],'released')

    def test_foreign_mutation_and_model_failure_release(self):
        foreign=new_id()
        with self.assertRaises(APIError) as failure:self.manager.submit(foreign,self.pid,self.token,self.metadata(0),self.frame)
        self.assertEqual(failure.exception.status,404)
        with self.assertRaises(APIError) as failure:self.manager.release(foreign,self.pid,{'preview_token':self.token,'stream_epoch':self.created['stream_epoch']},new_id())
        self.assertEqual(failure.exception.status,404);self.assertEqual(self.manager.snapshot(self.uid,self.pid)['status'],'active')
        class FailedPose:
            def __init__(self,**kwargs):raise RuntimeError('controlled model initialization failure')
        self.manager.pose_factory=FailedPose;self.send(0);self.start_patch.stop();self.manager.start()
        for unused in range(100):
            value=self.manager.snapshot(self.uid,self.pid)
            if value['status']=='failed':break
            time.sleep(.01)
        self.assertEqual(value['status'],'failed');self.assertEqual(value['reason'],'POSE_UNAVAILABLE')
        self.assertFalse(self.slot.owns(self.uid,self.pid,'preview'));self.assertIsNone(value['result'])


class PreviewHTTPTests(unittest.TestCase):
    def test_declared_oversize_rejected_before_form_or_auth_read(self):
        app=create_app({'TESTING':True});client=app.test_client()
        response=client.post('/api/v1/previews/'+new_id()+'/frames',data=b'x',content_type='multipart/form-data; boundary=small',environ_overrides={'CONTENT_LENGTH':str(2*1024*1024+64*1024+1)})
        self.assertEqual(response.status_code,413);self.assertEqual(response.json['error']['code'],'FRAME_TOO_LARGE')
        from fitness_app.frame_pipeline import MAX_JPEG_BYTES
        with self.assertRaises(APIError) as failure:jpeg_dimensions(bytes(MAX_JPEG_BYTES+1))
        self.assertEqual(failure.exception.status,413)

    def test_loading_preview_get_does_not_extend_initialization_lease(self):
        from fitness_app.semantic_recognition import SemanticRecognizer
        model=SemanticRecognizer(lambda:object(),info={'version':'fixture'})
        clock=[0.];manager=PreviewManager(VisionSlot(),monotonic=lambda:clock[0],recognizer_factory=lambda:model)
        uid,auth=new_id(),new_id()
        try:
            with patch.object(manager,'start'):
                created=manager.create(uid,auth,now_ms()+600000,{'page_id':new_id()},new_id())
            self.assertEqual(manager.snapshot(uid,created['preview_id'])['model_preparation']['state'],'loading')
            self.assertIsNone(model.thread)
            for stamp in (20.,100.,179.):
                clock[0]=stamp
                self.assertEqual(manager.snapshot(uid,created['preview_id'])['status'],'active')
            clock[0]=180.
            self.assertEqual(manager.snapshot(uid,created['preview_id'])['status'],'expired')
            self.assertTrue(model.closed)
        finally:manager.stop()

    def test_preview_creation_prepares_engine_before_first_source_frame(self):
        ready=threading.Event();closed=threading.Event()
        class PreparedPose:
            def __init__(self,**kwargs):ready.set()
            def detect(self,rgb,stamp):return pose()
            def close(self):closed.set()
        manager=PreviewManager(VisionSlot(),pose_factory=PreparedPose)
        uid,auth=new_id(),new_id()
        try:
            created=manager.create(uid,auth,now_ms()+60000,
                {'action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],
                 'page_id':new_id()},new_id())
            self.assertTrue(ready.is_set())
            self.assertTrue(manager.active['engine_ready'].is_set())
            self.assertEqual(manager.active['accepted'],0)
            manager.release(uid,created['preview_id'],{'preview_token':created['preview_token'],
                'stream_epoch':created['stream_epoch']},new_id())
        finally:manager.stop()
        self.assertTrue(closed.is_set())

    def test_http_frame_new_pipeline_and_no_training_rows(self):
        class FakePose:
            def __init__(self,**kwargs):pass
            def detect(self,rgb,stamp):return pose()
            def close(self):pass
        base=ROOT/'.tmp/tests/preview-http';base.mkdir(parents=True,exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=base) as folder:
                app=create_app({'TESTING':True,'DATABASE_PATH':str(Path(folder)/'app.sqlite3'),'PREVIEW_POSE_FACTORY':FakePose})
                db=app.extensions['database'];manager=app.extensions['preview_manager']
                try:
                    initialize_admin(db,'Preview_Admin','preview-fixture-password-123');client=app.test_client()
                    token=client.get('/api/v1/auth/csrf').json['data']['csrf_token'];headers={'X-CSRFToken':token}
                    client.post('/api/v1/auth/login',json={'username':'Preview_Admin','password':'preview-fixture-password-123'},headers=headers)
                    headers={'X-CSRFToken':client.get('/api/v1/auth/csrf').json['data']['csrf_token']}
                    created=client.post('/api/v1/previews',json={'action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],'page_id':new_id()},headers={**headers,'Idempotency-Key':new_id()})
                    self.assertEqual(created.status_code,201);data=created.json['data'];pid=data['preview_id']
                    metadata=dict(stream_epoch=data['stream_epoch'],frame_seq=0,captured_ms=0,width=32,height=24)
                    import json
                    result=client.post(f'/api/v1/previews/{pid}/frames',data={'metadata':json.dumps(metadata),'frame':(BytesIO(jpeg()),'frame.jpg','image/jpeg')},headers={**headers,'X-Preview-Token':data['preview_token']})
                    self.assertEqual(result.status_code,202,result.json)
                    for unused in range(100):
                        value=client.get('/api/v1/previews/'+pid).json['data']
                        if value['result']:break
                        time.sleep(.01)
                    self.assertEqual(value['result']['frame_seq'],0);self.assertNotIn('preview_token',value)
                    self.assertEqual(client.get('/api/v1/records').json['data']['total'],0)
                    from sqlalchemy import select,func
                    from fitness_app.db_schema import analysis_sessions
                    with db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(analysis_sessions)),0)
                    # 请求体限制先于 multipart/CSRF 解析，使用实际小体积格式失败而不造巨型图片。
                    wrong=client.post(f'/api/v1/previews/{pid}/frames',data=b'bad',content_type='image/jpeg',headers={**headers,'X-Preview-Token':data['preview_token']})
                    self.assertEqual(wrong.status_code,400)
                    deleted=client.delete('/api/v1/previews/'+pid,json={'preview_token':data['preview_token'],'stream_epoch':data['stream_epoch']},headers={**headers,'Idempotency-Key':new_id()})
                    self.assertEqual(deleted.status_code,200)
                finally:manager.stop();db.dispose()
        finally:
            if base.exists() and not any(base.iterdir()):base.rmdir()


if __name__=='__main__':unittest.main()
