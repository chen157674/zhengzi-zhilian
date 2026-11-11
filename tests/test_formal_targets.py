"""09 的目标事务与休息业务；已知 D-EVENT／D-CLOCK，不用于识别准确率。"""
import hashlib
import json
import unittest
from sqlalchemy import select,update,func
import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.database import StorageError,json_text
from fitness_app.auth import APIError
from fitness_app.frame_pipeline import framing
from test_formal_sessions import realistic_pose


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.h=shared.RecordTests();self.h.setUp()
        self.store=self.h.store;self.db=self.h.db;self.uid=self.h.user;self.auth=self.h.auth
        self.manager=self.h.app.extensions['session_manager'];self.manager.allow_observer=True
        self.control=None;self.sid=None

    def tearDown(self):
        self.store.fault=None
        for sid,item in list(self.h.writer._pending.items()):self.h.writer.retry(item['user_id'],sid,item['auth_id'])
        self.manager.stop();self.h.app.extensions['preview_manager'].stop();self.h.tearDown()

    def create(self,action='bodyweight_squat',unit='reps',sets=2,value=1,rest=60,running=True):
        config=dict(action_id=action,capability_version=get_action(action)['capability_version'],source='camera',mode='target',target={'sets':sets,'value':value,'unit':unit,'rest_seconds':rest})
        result=self.h.post(self.h.client,'/api/v1/sessions',config);self.assertEqual(result.status_code,201,result.json)
        sid=result.json['data']['session_id']
        if running:self.store.set_state(self.uid,sid,'running',s.new_id())
        return sid

    def event(self,seq,side='none',index=1):
        return dict(event_seq=seq,set_index=index,side=side,start_ms=(seq-1)*1000,end_ms=seq*1000,algorithm_version='D-EVENT',feature_schema_version='D-EVENT',assessments=[],active_ms=seq*1000)

    def rest_session(self,seconds=60):
        self.sid=self.create(rest=seconds,running=False)
        self.control=self.manager.claim(self.uid,self.auth,self.h.now[0]+12*3600*1000,self.sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        self.store.set_state(self.uid,self.sid,'running',s.new_id())
        rt=self.manager.live[self.sid];rt.data=self.store.append_event(self.uid,self.sid,self.event(1));rt.active_ms=rt.data['active_ms']
        self.manager.previews.reserve_session(self.uid,self.sid);self.manager._target_committed(rt)
        return rt

    def command(self,name,key=None,body=None):
        data=self.h.writer.snapshot(self.uid,self.sid);control=self.manager.controls.snapshot(self.uid,self.sid)
        payload=body or {'command':name,'expected_version':data['state_version'],'control_token':self.control['control_token'],
            'controller_epoch':control['controller_epoch'],'stream_epoch':control['stream_epoch']}
        return self.h.post(self.h.client,f'/api/v1/sessions/{self.sid}/commands',payload,key)

    def advance(self,seconds):
        while seconds>0:
            control=self.manager.controls.snapshot(self.uid,self.sid)
            self.manager.heartbeat(self.uid,self.auth,self.sid,{'control_token':self.control['control_token'],'controller_epoch':control['controller_epoch'],'stream_epoch':control['stream_epoch']})
            step=min(seconds,10);self.h.mono[0]+=step;seconds-=step;self.manager.tick()

    def test_form_fields_bounds_units_and_free_mode(self):
        baseline={'action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],'source':'camera','mode':'target','target':{'sets':1,'value':1,'unit':'reps','rest_seconds':0}}
        for field,bad in [('sets',0),('sets',101),('value',1001),('value',True),('value','2'),('value',1.5),('rest_seconds',-1),('rest_seconds',3601)]:
            data={**baseline,'target':{**baseline['target'],field:bad}};response=self.h.post(self.h.client,'/api/v1/sessions',data)
            self.assertEqual(response.status_code,422);self.assertIn('target.'+field,response.json['error']['fields'])
        missing={**baseline,'target':{'sets':1}};response=self.h.post(self.h.client,'/api/v1/sessions',missing)
        self.assertEqual(response.status_code,422);self.assertIn('target.value',response.json['error']['fields'])
        wrong={**baseline,'target':{**baseline['target'],'unit':'seconds'}};self.assertEqual(self.h.post(self.h.client,'/api/v1/sessions',wrong).status_code,422)
        self.create(sets=100,value=1000,rest=3600,running=False)
        self.create('forearm_plank','seconds',sets=1,value=3600,rest=0,running=False)
        free={**baseline,'mode':'free'};self.assertEqual(self.h.post(self.h.client,'/api/v1/sessions',free).status_code,422)
        free['target']=None;response=self.h.post(self.h.client,'/api/v1/sessions',free);self.assertEqual(response.status_code,201)
        self.assertIsNone(response.json['data']['config']['target']);self.assertIsNone(response.json['data']['groups'][0]['target_value'])

    def test_per_side_and_total_goal_once_without_side_compensation(self):
        curl=self.create('standing_dumbbell_curl','per_side_reps',sets=1,value=2)
        for seq,side in enumerate(['left','left','right','left'],1):data=self.store.append_event(self.uid,curl,self.event(seq,side))
        self.assertEqual(data['status'],'running');self.assertEqual((data['left_count'],data['right_count']),(3,1))
        last=self.event(5,'right');done=self.store.append_event(self.uid,curl,last)
        self.assertEqual(done['status'],'completed');self.assertEqual(done['completed_sets'],1);self.assertEqual(done['total_count'],5)
        self.assertEqual(self.store.append_event(self.uid,curl,last)['state_version'],done['state_version'])
        knees=self.create('high_knees','reps',sets=1,value=3)
        for seq,side in enumerate(['left','right','left'],1):data=self.store.append_event(self.uid,knees,self.event(seq,side))
        self.assertEqual(data['status'],'completed');self.assertEqual(data['total_count'],3);self.assertEqual((data['left_count'],data['right_count']),(2,1))

    def test_hold_pause_gap_not_counted_and_quality_issue_does_not_remove_count(self):
        sid=self.create('forearm_plank','seconds',sets=1,value=2)
        self.store.append_intervals(self.uid,sid,1,[{'kind':'hold','start_ms':0,'end_ms':1000,'algorithm_version':'D-EVENT'}])
        self.store.set_state(self.uid,sid,'paused',s.new_id());self.h.now[0]+=5000;self.store.set_state(self.uid,sid,'running',s.new_id())
        batch=[{'kind':'hold','start_ms':6000,'end_ms':7000,'algorithm_version':'D-EVENT'}]
        data=self.store.append_intervals(self.uid,sid,1,batch);self.assertEqual(data['hold_ms'],2000);self.assertEqual(data['status'],'completed')
        self.assertEqual(self.store.append_intervals(self.uid,sid,1,batch)['hold_ms'],2000)
        squat=self.create(sets=1)
        # 此专用 fixture 的判项只是已知 D-EVENT，不升级正式目录或声称真实质量验证。
        with self.db.transaction() as c:
            definition=json.loads(c.scalar(select(s.action_capabilities.c.definition_json).where(s.action_capabilities.c.action_id=='bodyweight_squat',s.action_capabilities.c.capability_version==get_action('bodyweight_squat')['capability_version'])))
            definition['capability_version']='target-fixture';definition['issues'][0]['status']='verified';encoded=json_text(definition)
            c.execute(s.action_capabilities.insert().values(action_id='bodyweight_squat',capability_version='target-fixture',definition_json=encoded,definition_hash=hashlib.sha256(encoded.encode()).hexdigest()))
            c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==squat).values(capability_version='target-fixture'))
        event=self.event(1);event['assessments']=[{'code':'squat.range','status':'issue','reason':'D-EVENT prescribed label','start_ms':0,'end_ms':1000}]
        data=self.store.append_event(self.uid,squat,event);self.assertEqual(data['total_count'],1);self.assertEqual(data['status'],'completed')

    def test_goal_transition_same_transaction_and_future_group_rejected(self):
        sid=self.create(sets=2,value=1,rest=0)
        with self.assertRaises(APIError):self.store.append_event(self.uid,sid,self.event(1,index=2))
        def fail(point):
            if point=='before_commit':raise StorageError('controlled goal transaction fault')
        self.store.fault=fail
        with self.assertRaises(StorageError):self.store.append_event(self.uid,sid,self.event(1))
        data=self.store.snapshot(self.uid,sid);self.assertEqual(data['total_count'],0);self.assertEqual(data['completed_sets'],0);self.assertEqual(data['groups'][0]['status'],'pending')
        self.store.fault=None;data=self.store.append_event(self.uid,sid,self.event(1));self.assertEqual(data['status'],'waiting');self.assertEqual(data['completed_sets'],1);self.assertEqual(data['rest_remaining_ms'],0)
        self.assertEqual(self.store.append_event(self.uid,sid,self.event(1))['completed_sets'],1)
        self.store.set_state(self.uid,sid,'running',s.new_id());data=self.store.append_event(self.uid,sid,self.event(2,index=2))
        self.assertEqual(data['status'],'completed');self.assertEqual(data['completed_sets'],2);self.assertEqual(data['total_count'],2)
        with self.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.analysis_sessions)),1)

    def test_rest_fixed_extension_pause_resume_and_limit(self):
        rt=self.rest_session();key=s.new_id();snapshot=self.store.snapshot(self.uid,self.sid);control=self.manager.controls.snapshot(self.uid,self.sid)
        body={'command':'extend_rest','expected_version':snapshot['state_version'],'control_token':self.control['control_token'],'controller_epoch':control['controller_epoch'],'stream_epoch':control['stream_epoch']}
        first=self.command('extend_rest',key,body);self.assertEqual(first.status_code,200,first.json);self.assertEqual(first.json['data']['live_rest_remaining_ms'],90000)
        self.assertEqual(self.command('extend_rest',key,body).json['data']['live_rest_remaining_ms'],90000)
        self.assertEqual(self.command('extend_rest').json['data']['live_rest_remaining_ms'],120000)
        self.advance(11);paused=self.command('pause');self.assertEqual(paused.status_code,200,paused.json);self.assertEqual(paused.json['data']['rest_remaining_ms'],109000)
        self.advance(20);self.assertEqual(self.store.snapshot(self.uid,self.sid)['rest_remaining_ms'],109000)
        resumed=self.command('resume');self.assertEqual(resumed.status_code,200,resumed.json);self.assertEqual(resumed.json['data']['status'],'resting');self.assertEqual(resumed.json['data']['live_rest_remaining_ms'],109000)
        self.advance(109);self.assertEqual(self.store.snapshot(self.uid,self.sid)['status'],'waiting')
        # 结束当前任务后，另测上限，沿用同一业务层而不重测认证。
        self.command('end',body={'command':'end','confirm':True,'expected_version':self.store.snapshot(self.uid,self.sid)['state_version'],'control_token':self.control['control_token'],**{key:value for key,value in self.manager.controls.snapshot(self.uid,self.sid).items() if key in {'controller_epoch','stream_epoch'}}})
        self.rest_session(3590);before=self.store.snapshot(self.uid,self.sid);rejected=self.command('extend_rest')
        self.assertEqual(rejected.status_code,422);self.assertEqual(self.store.snapshot(self.uid,self.sid)['state_version'],before['state_version'])
        self.assertEqual(self.store.snapshot(self.uid,self.sid)['rest_remaining_ms'],3590000)

    def test_failed_rest_extension_freezes_proposed_remaining_until_explicit_resume(self):
        rt=self.rest_session()
        def fail(point):
            if point=='before_commit':raise StorageError('controlled rest write fault')
        self.store.fault=fail;response=self.command('extend_rest');self.assertEqual(response.status_code,200,response.json);self.assertEqual(response.json['data']['save_state'],'save_failed')
        self.advance(100);self.store.fault=None
        data=self.store.snapshot(self.uid,self.sid);response=self.h.post(self.h.client,f'/api/v1/sessions/{self.sid}/commands',{'command':'retry_save','expected_version':data['state_version']})
        self.assertEqual(response.status_code,200,response.json);self.assertEqual(response.json['data']['status'],'paused');self.assertEqual(response.json['data']['resume_from'],'resting');self.assertEqual(response.json['data']['rest_remaining_ms'],90000)
        resumed=self.command('resume');self.assertEqual(resumed.status_code,200,resumed.json);self.assertEqual(resumed.json['data']['live_rest_remaining_ms'],90000)

    def test_stream_restart_keeps_hold_intervals_on_one_session_timeline(self):
        sid=self.create('forearm_plank','seconds',sets=1,value=2)
        rt=self.manager._runtime(self.uid,sid,self.auth,self.h.now[0]+12*3600*1000)
        class HoldFixture:
            previous=None
            def reset(self,reason):self.previous=None
            def process(self,frame,context):
                timestamp=frame['captured_ms'];previous=self.previous;self.previous=timestamp
                return {} if previous is None else {'intervals':[{'kind':'hold','start_ms':previous,'end_ms':timestamp,'algorithm_version':'D-EVENT'}]}
        rt.adapter=HoldFixture();quality=framing('forearm_plank',realistic_pose())
        def send(raw,server_time):
            self.h.mono[0]=server_time
            self.manager._consume(rt,{'captured_ms':raw,'width':32,'height':24,'framing':quality})
        for raw in (0,500,1000):send(raw,raw/1000)
        self.assertEqual(rt.data['hold_ms'],1000)
        self.store.set_state(self.uid,sid,'paused',s.new_id());self.manager._drop(rt)
        self.h.mono[0]=6;self.store.set_state(self.uid,sid,'running',s.new_id());rt.data=self.store.snapshot(self.uid,sid)
        for raw in (0,500,1000):send(raw,6+raw/1000)
        detail=self.store.snapshot(self.uid,sid,True);self.assertEqual(detail['status'],'completed');self.assertEqual(detail['hold_ms'],2000)
        self.assertEqual(len(detail['intervals']),2);self.assertGreaterEqual(detail['intervals'][1]['start_ms'],6000)


if __name__=='__main__':unittest.main()
