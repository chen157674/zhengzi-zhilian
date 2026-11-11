"""08 新的预览移交和会话控制集成；不复制底层帧／凭据完整矩阵。"""
import unittest
from unittest.mock import patch
from pathlib import Path
from io import BytesIO
import json
import tempfile
import time
import copy

from sqlalchemy import select,func
from fitness_app import create_app
from fitness_app.catalogue import get_action
from fitness_app.auth import initialize_admin,password_hash
from fitness_app.paths import ROOT
from fitness_app import db_schema as s

from fitness_app.auth import APIError
from fitness_app.database import StorageError
from fitness_app.db_schema import new_id,now_ms
from fitness_app.preview import PreviewManager
from fitness_app.vision_slot import VisionSlot
from test_formal_preview import jpeg,pose


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.slot=VisionSlot();self.manager=PreviewManager(self.slot,monotonic=lambda:1,clock=lambda:now_ms())
        self.patcher=patch.object(self.manager,'start');self.patcher.start()
        self.uid,self.auth,self.page,self.sid=new_id(),new_id(),new_id(),new_id()
        self.created=self.manager.create(self.uid,self.auth,now_ms()+60000,{'action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],'page_id':self.page},new_id())
        self.pid=self.created['preview_id'];self.jpeg=jpeg()
        for seq in range(7):
            self.send(seq);item,packet=self.manager._next();self.manager._publish(item,packet,pose(),1)

    def tearDown(self):self.manager.stop();self.patcher.stop()
    def send(self,seq):return self.manager.submit(self.uid,self.pid,self.created['preview_token'],{'stream_epoch':self.created['stream_epoch'],'frame_seq':seq,'captured_ms':seq*100,'width':32,'height':24},self.jpeg)
    def adopt(self,commit=lambda:{'state_version':1,'status':'countdown','save_state':'clean'}):
        return self.manager.adopt(self.uid,self.auth,self.page,self.pid,self.created['preview_token'],self.sid,'bodyweight_squat',commit)

    def test_atomic_handoff_old_packets_and_public_endpoint_fenced(self):
        self.send(7);old,packet=self.manager._next();data,handle=self.adopt()
        self.assertEqual(data['status'],'countdown');self.assertTrue(self.slot.owns(self.uid,self.sid,'session'))
        self.assertEqual(self.manager.snapshot(self.uid,self.pid)['status'],'transferred')
        with self.assertRaises(APIError):self.send(8)
        with self.assertRaises(APIError):self.manager.snapshot(self.uid,handle['preview_id'])
        # 移交后立即停止时，旧模型尚在处理；不能提前让别的任务抢槽。
        self.manager.close_session(self.uid,self.sid)
        with self.assertRaises(APIError):self.slot.claim(new_id(),new_id(),'video')
        self.manager._publish(old,packet,pose(),1)
        self.assertFalse(self.slot.owns(self.uid,self.sid,'session'))
        self.assertIsNone(self.manager.snapshot(self.uid,handle['preview_id'],internal=True)['result'])

    def test_failed_business_commit_does_not_transfer_or_change_old_token(self):
        def fail():raise StorageError('controlled start commit failure')
        with self.assertRaises(StorageError):self.adopt(fail)
        self.assertTrue(self.slot.owns(self.uid,self.pid,'preview'));self.send(7)
        with self.assertRaises(APIError):self.manager.adopt(self.uid,self.auth,new_id(),self.pid,self.created['preview_token'],self.sid,'bodyweight_squat',lambda:{})

    def test_new_stream_has_separate_sequence_and_clock(self):
        unused,handle=self.adopt()
        self.manager.release(self.uid,self.pid,{'preview_token':self.created['preview_token'],'stream_epoch':self.created['stream_epoch']},new_id())
        self.assertTrue(self.slot.owns(self.uid,self.sid,'session'))
        metadata={'stream_epoch':handle['stream_epoch'],'frame_seq':0,'captured_ms':0,'width':32,'height':24}
        self.assertTrue(self.manager.submit(self.uid,handle['preview_id'],handle['preview_token'],metadata,self.jpeg,internal=True)['accepted'])
        item,packet=self.manager._next();self.manager._publish(item,packet,pose(),1)
        self.assertEqual(self.manager.snapshot(self.uid,handle['preview_id'],internal=True)['result']['captured_ms'],0)


def realistic_pose():
    result=pose();points=result['poses'][0]
    for index,xy in {11:(.4,.3),12:(.6,.3),23:(.4,.6),24:(.6,.6),25:(.4,.75),26:(.6,.75),27:(.4,.9),28:(.6,.9)}.items():
        points[index].update(x=xy[0],y=xy[1])
    return result


class SessionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.base=ROOT/'.tmp/tests/sessions-http';self.base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=self.base);self.wall=[now_ms()];self.mono=[0.0];self.scene=[realistic_pose()]
        scene=self.scene
        class FakePose:
            def __init__(self,**kwargs):pass
            def detect(self,rgb,timestamp):return copy.deepcopy(scene[0])
            def close(self):pass
        self.app=create_app({'TESTING':True,'DATABASE_PATH':str(Path(self.tmp.name)/'app.sqlite3'),'SECRET_KEY':'session-test-only',
            'CLOCK':lambda:self.wall[0],'MONOTONIC_CLOCK':lambda:self.mono[0],'PREVIEW_POSE_FACTORY':FakePose,'ALLOW_POSE_ONLY_SESSIONS':True,'SESSION_ENGINE_PROVIDER':lambda action:None})
        self.db=self.app.extensions['database'];initialize_admin(self.db,'Session_Admin','session-fixture-password-123')
        self.uid,self.other=new_id(),new_id();hashed=password_hash('session-fixture-password-123')
        with self.db.transaction() as c:
            c.execute(s.users.insert(),[dict(id=self.uid,username_display='Session_User',username_key='session_user',password_hash=hashed),
                dict(id=self.other,username_display='Session_Other',username_key='session_other',password_hash=hashed)])
        self.client=self.app.test_client();self.login(self.client,'Session_User');self.page=new_id();self.jpeg=jpeg()
        self.manager=self.app.extensions['session_manager'];self.previews=self.app.extensions['preview_manager'];self.store=self.app.extensions['session_store'];self.writer=self.app.extensions['persistence']
        result=self.post(self.client,'/api/v1/sessions',dict(action_id='bodyweight_squat',capability_version=get_action('bodyweight_squat')['capability_version'],source='camera',mode='free',target=None))
        self.assertEqual(result.status_code,201,result.json);self.sid=result.json['data']['session_id'];self.control=None;self.seq=0

    def tearDown(self):
        self.store.fault=None
        for sid,item in list(self.writer._pending.items()):self.writer.retry(item['user_id'],sid,item['auth_id'])
        for sid,item in list(self.writer._discarded.items()):self.writer.retry(item['user_id'],sid,item['auth_id'])
        self.manager.tick();self.manager.stop();self.previews.stop();self.db.dispose();self.tmp.cleanup()
        if self.base.exists() and not any(self.base.iterdir()):self.base.rmdir()

    def post(self,client,path,payload,key=None):
        token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        return client.post(path,json=payload,headers={'X-CSRFToken':token,'Idempotency-Key':key or new_id()})
    def login(self,client,name):return self.post(client,'/api/v1/auth/login',{'username':name,'password':'session-fixture-password-123'})
    def snapshot(self):return self.client.get('/api/v1/sessions/'+self.sid).json['data']
    def claim(self,page=None,takeover=False,key=None):
        result=self.post(self.client,f'/api/v1/sessions/{self.sid}/control',{'page_id':page or self.page,'takeover':takeover,'expected_version':self.snapshot()['state_version']},key)
        self.assertEqual(result.status_code,200,result.json);self.control=result.json['data'];return result
    def prepare(self):
        result=self.post(self.client,'/api/v1/previews',{'action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],'page_id':self.page})
        self.assertEqual(result.status_code,201,result.json);created=result.json['data']
        for seq in range(7):
            data={'stream_epoch':created['stream_epoch'],'frame_seq':seq,'captured_ms':seq*100,'width':32,'height':24}
            accepted=self.multipart(f"/api/v1/previews/{created['preview_id']}/frames",data,'X-Preview-Token',created['preview_token'])
            self.assertEqual(accepted.status_code,202,accepted.json)
            self.wait_frame(created['preview_id'],seq)
        return created
    def multipart(self,path,metadata,header,token):
        csrf=self.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        return self.client.post(path,data={'metadata':json.dumps(metadata),'frame':(BytesIO(self.jpeg),'frame.jpg','image/jpeg')},headers={'X-CSRFToken':csrf,header:token})
    def wait_frame(self,pid,seq,internal=False):
        for unused in range(1000):
            value=self.previews.snapshot(self.uid,pid,internal=internal)
            if value['result'] and value['result']['frame_seq']==seq:return
            time.sleep(.005)
        self.fail('controlled frame worker did not finish')
    def command(self,name,extra=None,key=None):
        snapshot=self.snapshot();control=snapshot['control']
        payload={'command':name,'expected_version':snapshot['state_version'],'control_token':self.control['control_token'],
            'controller_epoch':control['controller_epoch'],'stream_epoch':control['stream_epoch'],**(extra or {})}
        return self.post(self.client,f'/api/v1/sessions/{self.sid}/commands',payload,key)
    def start(self):
        self.claim();prepared=self.prepare()
        result=self.command('start',{'preview_id':prepared['preview_id'],'preview_token':prepared['preview_token']})
        self.assertEqual(result.status_code,200,result.json);self.assertEqual(result.json['data']['status'],'countdown');self.seq=0
    def frame(self):
        control=self.snapshot()['control'];seq=self.seq;self.seq+=1
        metadata={'controller_epoch':control['controller_epoch'],'stream_epoch':control['stream_epoch'],'frame_seq':seq,'captured_ms':seq*100,'width':32,'height':24}
        result=self.multipart(f'/api/v1/sessions/{self.sid}/frames',metadata,'X-Control-Token',self.control['control_token'])
        self.assertEqual(result.status_code,202,result.json)
        handle=self.manager.live[self.sid].stream;self.wait_frame(handle['preview_id'],seq,True);self.manager.tick()
    def running(self):
        self.start()
        for unused in range(7):self.frame()
        self.mono[0]+=3.1;self.frame();self.assertEqual(self.snapshot()['status'],'running')

    def test_countdown_cancel_zero_results_and_repeat_receipt(self):
        self.start();snapshot=self.snapshot();key=new_id();control=snapshot['control']
        payload={'command':'cancel_countdown','expected_version':snapshot['state_version'],'control_token':self.control['control_token'],
            'controller_epoch':control['controller_epoch'],'stream_epoch':control['stream_epoch']}
        first=self.post(self.client,f'/api/v1/sessions/{self.sid}/commands',payload,key)
        self.assertEqual(first.status_code,200,first.json);self.assertEqual(first.json['data']['status'],'ready')
        repeated=self.post(self.client,f'/api/v1/sessions/{self.sid}/commands',payload,key)
        self.assertEqual(repeated.json['data'],first.json['data']);self.assertIsNone(self.snapshot()['started_at_ms'])
        self.assertEqual(self.client.get('/api/v1/records').json['data']['total'],0)

    def test_pause_lease_expiry_retains_pause_and_explicit_resume(self):
        self.running();self.frame();self.frame();active=self.snapshot()['live_active_ms']
        paused=self.command('pause');self.assertEqual(paused.status_code,200,paused.json);self.assertEqual(paused.json['data']['status'],'paused')
        self.mono[0]=20;self.manager.tick();self.assertEqual(self.snapshot()['status'],'paused');self.assertEqual(self.snapshot()['active_ms'],active)
        self.claim();prepared=self.prepare()
        resumed=self.command('resume',{'preview_id':prepared['preview_id'],'preview_token':prepared['preview_token']})
        self.assertEqual(resumed.status_code,200,resumed.json);self.assertEqual(resumed.json['data']['status'],'countdown')
        self.assertEqual(resumed.json['data']['total_count'],0)

    def test_unconfirmed_loss_interrupts_and_late_frames_cannot_revive(self):
        self.running();old=dict(self.control);metadata=self.snapshot()['control'];self.mono[0]=20;self.manager.tick()
        self.assertEqual(self.snapshot()['status'],'interrupted');self.assertEqual(self.snapshot()['end_reason'],'control_lost')
        result=self.multipart(f'/api/v1/sessions/{self.sid}/frames',{'controller_epoch':metadata['controller_epoch'],'stream_epoch':metadata['stream_epoch'],'frame_seq':100,'captured_ms':10000,'width':32,'height':24},'X-Control-Token',old['control_token'])
        self.assertEqual(result.status_code,409);self.assertEqual(self.snapshot()['status'],'interrupted')

    def test_takeover_stops_old_stream_read_only_does_not_claim_and_secret_once(self):
        self.running();old=dict(self.control);snapshot=self.snapshot();key=new_id();new_page=new_id()
        read=self.client.get(f'/api/v1/sessions/{self.sid}/updates').json['data'];self.assertNotIn('control_token',json.dumps(read));self.assertEqual(read['control'],snapshot['control'])
        payload={'page_id':new_page,'takeover':True,'expected_version':snapshot['state_version']}
        response=self.post(self.client,f'/api/v1/sessions/{self.sid}/control',payload,key)
        self.assertEqual(response.status_code,200,response.json);self.assertTrue(response.json['data']['credential_available']);self.assertEqual(self.snapshot()['status'],'paused')
        repeated=self.post(self.client,f'/api/v1/sessions/{self.sid}/control',payload,key)
        self.assertFalse(repeated.json['data']['credential_available']);self.assertNotIn('control_token',repeated.json['data'])
        old_frame=self.multipart(f'/api/v1/sessions/{self.sid}/frames',{},'X-Control-Token',old['control_token']);self.assertEqual(old_frame.status_code,409)
        payload['page_id']=new_id();self.assertEqual(self.post(self.client,f'/api/v1/sessions/{self.sid}/control',payload,key).status_code,409)

    def test_logout_revokes_live_flow_and_other_user_cannot_control(self):
        self.running();result=self.post(self.client,'/api/v1/auth/logout',{});self.assertEqual(result.status_code,200)
        saved=self.store.snapshot(self.uid,self.sid);self.assertEqual(saved['status'],'interrupted');self.assertEqual(saved['end_reason'],'auth_revoked')
        other=self.app.test_client();self.login(other,'Session_Other')
        self.assertEqual(other.get('/api/v1/sessions/'+self.sid).status_code,404)
        self.assertEqual(self.post(other,f'/api/v1/sessions/{self.sid}/control',{'page_id':new_id(),'takeover':True,'expected_version':saved['state_version']}).status_code,404)

    def test_event_fault_freezes_real_stream_then_retry_stays_paused(self):
        class FixtureCounter:
            def reset(self,reason):pass
            def process(self,frame,context):
                end=frame['captured_ms'];return {'events':[{'side':'none','start_ms':max(0,end-100),'end_ms':end,'algorithm_version':'D-EVENT','feature_schema_version':'D-EVENT','assessments':[]}]}
        self.manager.engine_provider=lambda action:FixtureCounter();self.running()
        def fault(point):
            if point=='before_commit':raise StorageError('controlled event write failure')
        self.store.fault=fault;self.frame();data=self.snapshot()
        self.assertEqual(data['save_state'],'save_failed');self.assertEqual(data['total_count'],0);self.assertTrue(data['analysis_frozen'])
        self.assertIsNone(self.manager.live[self.sid].stream);self.assertEqual(len(self.writer._pending),1)
        self.store.fault=None
        recovered=self.post(self.client,f'/api/v1/sessions/{self.sid}/commands',{'command':'retry_save','expected_version':data['state_version']})
        self.assertEqual(recovered.status_code,200,recovered.json);self.assertEqual(recovered.json['data']['total_count'],1);self.assertEqual(recovered.json['data']['status'],'paused')
        self.mono[0]+=4;self.manager.tick();self.assertEqual(self.snapshot()['total_count'],1);self.assertEqual(self.snapshot()['status'],'paused')

    def test_unreadable_database_original_page_can_stop_then_discard_only(self):
        self.running();snapshot=self.snapshot();control=snapshot['control']
        csrf=self.client.get('/api/v1/auth/csrf').json['data']['csrf_token'];end_key=new_id()
        payload={'command':'end','expected_version':snapshot['state_version'],'confirm':True,'control_token':self.control['control_token'],
            'controller_epoch':control['controller_epoch'],'stream_epoch':control['stream_epoch']}
        with patch.object(self.db,'transaction',side_effect=StorageError('controlled database unreadable')):
            response=self.client.post(f'/api/v1/sessions/{self.sid}/commands',json=payload,headers={'X-CSRFToken':csrf,'Idempotency-Key':end_key})
            self.assertEqual(response.status_code,200,response.json);result=response.json['data']
            self.assertTrue(result['stopped']);self.assertFalse(result['terminal_persisted']);self.assertNotIn('total_count',result)
            self.assertIsNone(self.manager.live[self.sid].stream);self.assertEqual(len(self.writer._pending),1)
            repeated=self.client.post(f'/api/v1/sessions/{self.sid}/commands',json=payload,headers={'X-CSRFToken':csrf,'Idempotency-Key':end_key})
            self.assertEqual(repeated.json['data'],result);self.assertEqual(len(self.writer._pending),1)
            self.assertEqual(self.client.get('/api/v1/sessions/'+self.sid).status_code,503)
            forbidden={**payload,'command':'resume'}
            self.assertEqual(self.client.post(f'/api/v1/sessions/{self.sid}/commands',json=forbidden,headers={'X-CSRFToken':csrf,'Idempotency-Key':new_id()}).status_code,409)
            discard={**payload,'command':'discard_unsaved','expected_version':result['state_version'],'last_committed_version':result['state_version']}
            response=self.client.post(f'/api/v1/sessions/{self.sid}/commands',json=discard,headers={'X-CSRFToken':csrf,'Idempotency-Key':new_id()})
            self.assertEqual(response.status_code,200,response.json);self.assertTrue(response.json['data']['unsaved_discarded']);self.assertFalse(response.json['data']['terminal_persisted'])
            self.assertFalse(self.writer._pending)
        with self.client.session_transaction() as session:auth=session['_user_id']
        self.writer.retry(self.uid,self.sid,auth);saved=self.store.snapshot(self.uid,self.sid)
        self.assertEqual(saved['status'],'interrupted');self.assertEqual(saved['total_count'],0)

    def test_unreadable_database_cannot_use_other_signed_login_for_cleanup(self):
        self.running();snapshot=self.snapshot();other=self.app.test_client();self.login(other,'Session_Other')
        csrf=other.get('/api/v1/auth/csrf').json['data']['csrf_token']
        payload={'command':'end','confirm':True,'expected_version':snapshot['state_version'],'control_token':self.control['control_token'],
            'controller_epoch':snapshot['control']['controller_epoch'],'stream_epoch':snapshot['control']['stream_epoch']}
        with patch.object(self.db,'transaction',side_effect=StorageError('controlled database unreadable')):
            response=other.post(f'/api/v1/sessions/{self.sid}/commands',json=payload,headers={'X-CSRFToken':csrf,'Idempotency-Key':new_id()})
            self.assertEqual(response.status_code,409,response.json)
            self.assertIsNotNone(self.manager.live[self.sid].stream)

    def test_new_guard_other_user_frames_and_discard_do_not_touch_owner(self):
        self.running();other=self.app.test_client();self.login(other,'Session_Other')
        csrf=other.get('/api/v1/auth/csrf').json['data']['csrf_token'];control=self.snapshot()['control']
        frame=other.post(f'/api/v1/sessions/{self.sid}/frames',data={'metadata':'{}','frame':(BytesIO(self.jpeg),'frame.jpg','image/jpeg')},headers={'X-CSRFToken':csrf,'X-Control-Token':self.control['control_token']})
        self.assertEqual(frame.status_code,404)
        discarded=self.post(other,f'/api/v1/sessions/{self.sid}/commands',{'command':'discard_unsaved','confirm':True,'expected_version':0,'last_committed_version':0,
            'control_token':self.control['control_token'],'controller_epoch':control['controller_epoch'],'stream_epoch':control['stream_epoch']})
        self.assertEqual(discarded.status_code,404);self.assertEqual(self.snapshot()['status'],'running');self.assertIsNotNone(self.manager.live[self.sid].stream)

    def test_new_guard_second_create_blocked_original_key_replays(self):
        self.running();config=dict(action_id='bodyweight_squat',capability_version=get_action('bodyweight_squat')['capability_version'],source='camera',mode='free',target=None)
        blocked=self.post(self.client,'/api/v1/sessions',config);self.assertEqual(blocked.status_code,409);self.assertEqual(blocked.json['error']['fields']['session_id'],self.sid)
        with self.db.transaction(False) as c:key=c.scalar(select(s.command_receipts.c.command_id).where(s.command_receipts.c.actor_user_id==self.uid,s.command_receipts.c.resource_type=='session.create'))
        repeated=self.post(self.client,'/api/v1/sessions',config,key);self.assertEqual(repeated.status_code,201,repeated.json);self.assertEqual(repeated.json['data']['session_id'],self.sid)
        with self.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.analysis_sessions)),1)

    def test_new_guard_deleted_terminal_forgets_frozen_runtime(self):
        self.running();rt=self.manager.live[self.sid];self.manager._freeze(rt)
        with self.client.session_transaction() as state:auth=state['_user_id']
        self.manager.recovered(self.uid,auth,self.sid)
        result=self.command('end',{'confirm':True});self.assertEqual(result.status_code,200,result.json);self.assertEqual(result.json['data']['status'],'ended_early')
        deleted=self.post(self.client,'/api/v1/records/delete',{'ids':[self.sid],'expected_versions':{self.sid:result.json['data']['state_version']}})
        self.assertEqual(deleted.status_code,200,deleted.json);self.assertNotIn(self.sid,self.manager.live);self.manager.tick()

    def test_new_lifecycle_waiting_stable_reentry_and_paused_auth_expiry(self):
        self.running();before=self.snapshot()['live_active_ms'];self.scene[0]={'poses':[]};self.mono[0]+=1.1;self.frame()
        self.assertEqual(self.snapshot()['status'],'waiting');self.assertEqual(self.snapshot()['live_active_ms'],before)
        self.scene[0]=realistic_pose()
        for unused in range(7):self.frame()
        self.assertEqual(self.snapshot()['status'],'countdown');self.mono[0]+=3.1;self.frame();self.assertEqual(self.snapshot()['status'],'running')
        result=self.command('pause');self.assertEqual(result.status_code,200)
        self.mono[0]=30;self.manager.tick();self.assertEqual(self.store.snapshot(self.uid,self.sid)['status'],'paused')
        self.wall[0]+=12*3600*1000+1;self.manager.tick()
        saved=self.store.snapshot(self.uid,self.sid);self.assertEqual(saved['status'],'interrupted');self.assertEqual(saved['end_reason'],'auth_expired')

    def test_new_lifecycle_model_error_preserves_failure_reason(self):
        self.running()
        with self.previews.lock:self.previews._close('failed','POSE_UNAVAILABLE')
        self.manager.tick();data=self.snapshot()
        self.assertEqual(data['status'],'failed');self.assertEqual(data['end_reason'],'POSE_UNAVAILABLE');self.assertIsNone(self.manager.live[self.sid].stream)


if __name__=='__main__':unittest.main()
