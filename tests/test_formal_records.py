from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from sqlalchemy import select,update,func

from fitness_app import create_app
from fitness_app.catalogue import get_action
from fitness_app.auth import initialize_admin,password_hash
from fitness_app.database import StorageError,ConflictError
from fitness_app.persistence import PersistenceCoordinator
from fitness_app.auth import APIError
from fitness_app.paths import ROOT
from fitness_app import db_schema as s

PASSWORD='record-fixture-password-123'


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.base=ROOT/'.tmp/tests/records';self.base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=self.base)
        self.now=[s.now_ms()];self.mono=[0.0]
        self.app=create_app({'TESTING':True,'DATABASE_PATH':str(Path(self.tmp.name)/'app.sqlite3'),'SECRET_KEY':'test-only',
            'CLOCK':lambda:self.now[0],'MONOTONIC_CLOCK':lambda:self.mono[0],**getattr(self,'config_overrides',{})})
        self.db=self.app.extensions['database'];initialize_admin(self.db,'Record_Admin',PASSWORD)
        self.user,self.other=s.new_id(),s.new_id()
        hashed=password_hash(PASSWORD)
        with self.db.transaction() as c:
            c.execute(s.users.insert(),[dict(id=self.user,username_display='Record_User',username_key='record_user',password_hash=hashed),
                dict(id=self.other,username_display='Other_User',username_key='other_user',password_hash=hashed)])
        self.client=self.app.test_client();self.login(self.client,'Record_User')
        with self.client.session_transaction() as state:self.auth=state['_user_id']
        self.store=self.app.extensions['session_store'];self.writer=self.app.extensions['persistence']

    def tearDown(self):
        self.db.dispose();self.tmp.cleanup()
        if self.base.exists() and not any(self.base.iterdir()):self.base.rmdir()

    def post(self,client,path,data,key=None):
        token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        return client.post(path,json=data,headers={'X-CSRFToken':token,'Idempotency-Key':key or s.new_id()})

    def login(self,client,name):return self.post(client,'/api/v1/auth/login',{'username':name,'password':PASSWORD})

    def create(self,action='bodyweight_squat'):
        result=self.post(self.client,'/api/v1/sessions',{'action_id':action,'capability_version':get_action(action)['capability_version'],'source':'camera','mode':'free','target':None})
        self.assertEqual(result.status_code,201,result.json)
        return result.json['data']['session_id']

    def running(self,action='bodyweight_squat'):
        sid=self.create(action);self.store.set_state(self.user,sid,'running',s.new_id());return sid

    def event(self,seq=1):
        return dict(event_seq=seq,set_index=1,side='none',start_ms=(seq-1)*1000,end_ms=seq*1000,
            algorithm_version='logic-test',feature_schema_version='logic-test',assessments=[],active_ms=seq*1000)

    def finish(self,sid):return self.store.set_state(self.user,sid,'ended_early',s.new_id(),reason='test_end')

    def test_create_real_snapshot_not_started_and_private_owner(self):
        key=s.new_id();data={'action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],'source':'camera','mode':'free','target':None}
        first=self.post(self.client,'/api/v1/sessions',data,key);again=self.post(self.client,'/api/v1/sessions',data,key)
        self.assertEqual(first.status_code,201);self.assertEqual(first.json['data']['session_id'],again.json['data']['session_id'])
        self.assertEqual(self.client.get('/api/v1/records').json['data']['total'],0)
        bad=dict(data,actual_count=100);self.assertEqual(self.post(self.client,'/api/v1/sessions',bad).status_code,400)
        wrong=dict(data,capability_version='wrong');self.assertEqual(self.post(self.client,'/api/v1/sessions',wrong).status_code,422)
        other=self.app.test_client();self.login(other,'Record_Admin')
        sid=first.json['data']['session_id'];self.assertEqual(other.get('/api/v1/sessions/'+sid).status_code,404)
        self.assertEqual(other.get('/api/v1/records/'+sid).status_code,404)

    def test_events_idempotent_versions_and_no_unverified_quality(self):
        sid=self.running();first=self.store.append_event(self.user,sid,self.event());again=self.store.append_event(self.user,sid,self.event())
        self.assertEqual(first['total_count'],1);self.assertEqual(again['state_version'],first['state_version'])
        altered=self.event();altered['end_ms']=900
        with self.assertRaises(ConflictError):self.store.append_event(self.user,sid,altered)
        second=self.store.append_event(self.user,sid,self.event(2));self.assertEqual(second['total_count'],2)
        self.assertEqual(second['evaluable_count'],0);self.assertEqual(second['unassessable_count'],2)
        unsafe=self.event(3);unsafe['assessments']=[dict(code='squat.range',status='issue',reason='test',start_ms=2000,end_ms=3000)]
        with self.assertRaises(APIError):self.store.append_event(self.user,sid,unsafe)
        self.assertEqual(self.store.snapshot(self.user,sid)['total_count'],2)

    def test_intervals_union_reasons_and_quality_independent(self):
        sid=self.running('forearm_plank')
        def hold(a,b):return dict(kind='hold',start_ms=a,end_ms=b,algorithm_version='test')
        first=self.store.append_intervals(self.user,sid,1,[hold(0,1000)])
        again=self.store.append_intervals(self.user,sid,1,[hold(0,1000)]);self.assertEqual(again['state_version'],first['state_version'])
        self.store.append_intervals(self.user,sid,1,[hold(500,2000)])
        for a,b,reason in [(2000,3000,'遮挡'),(2500,3500,'目标不确定')]:
            self.store.append_intervals(self.user,sid,1,[dict(kind='unassessable',start_ms=a,end_ms=b,algorithm_version='test',reason=reason)])
        self.store.append_intervals(self.user,sid,1,[dict(kind='unassessable',item_code='plank.hip_low',start_ms=0,end_ms=500,algorithm_version='test',reason='髋位不可见')])
        detail=self.store.snapshot(self.user,sid,True);self.assertEqual(detail['hold_ms'],2000)
        self.assertEqual(len(detail['intervals']),5)
        with self.assertRaises(APIError):self.store.append_intervals(self.user,sid,1,[dict(kind='unassessable',start_ms=1000,end_ms=2500,algorithm_version='test',reason='冲突支撑')])
        self.assertEqual(self.store.snapshot(self.user,sid)['hold_ms'],2000)

    def test_failure_before_commit_and_after_commit_confirmation(self):
        sid=self.running();calls=[]
        def before(point):
            if point=='before_commit':raise StorageError('injected write failure')
        self.store.fault=before;self.writer.freeze_hooks.append(lambda **kwargs:calls.append(kwargs))
        failed=self.writer.submit(self.user,sid,self.auth,'event',self.event())
        self.assertEqual(failed['save_state'],'save_failed');self.assertEqual(failed['total_count'],0)
        self.assertEqual(len(calls),1)
        self.store.fault=None;restored=self.writer.retry(self.user,sid,self.auth)
        self.assertEqual(restored['total_count'],1);self.assertTrue(restored['analysis_frozen'])
        frozen=self.writer.submit(self.user,sid,self.auth,'event',self.event(2));self.assertEqual(frozen['total_count'],1)
        self.writer.release(self.user,sid)
        def after(point):
            if point=='after_commit':raise StorageError('injected response uncertainty')
        self.store.fault=after
        confirmed=self.writer.submit(self.user,sid,self.auth,'event',self.event(2))
        self.assertEqual(confirmed['save_state'],'clean');self.assertEqual(confirmed['total_count'],2)
        self.assertFalse(self.writer.unhealthy)

    def test_automatic_retry_schedule_and_one_pending_batch(self):
        sid=self.running();attempts=[]
        def failed(point):
            if point=='before_commit':attempts.append(self.mono[0]);raise StorageError('readable but unwritable')
        self.store.fault=failed;self.writer.submit(self.user,sid,self.auth,'event',self.event())
        self.writer.submit(self.user,sid,self.auth,'event',self.event(2))
        for value in (.49,.5,1.49,1.5,3.49,3.5,20):self.mono[0]=value;self.writer.tick()
        self.assertEqual(attempts,[0,.5,1.5,3.5])
        self.assertEqual(self.writer.snapshot(self.user,sid)['pending']['event_seq'],1)
        self.store.fault=None;self.writer.retry(self.user,sid,self.auth)
        self.assertEqual(self.store.snapshot(self.user,sid)['total_count'],1)

    def test_discard_cached_when_storage_unreadable_and_reconcile(self):
        sid=self.running();self.writer.submit(self.user,sid,self.auth,'event',self.event())
        def fail(point):
            if point=='before_commit':raise StorageError('write failed')
        self.store.fault=fail
        data=self.writer.submit(self.user,sid,self.auth,'event',self.event(2));version=data['state_version'];key=s.new_id()
        with patch.object(self.store,'snapshot',side_effect=StorageError('read also failed')):
            result=self.writer.discard(self.user,sid,self.auth,version,key)
            self.assertTrue(result['unsaved_discarded']);self.assertFalse(result['terminal_persisted'])
            self.assertFalse(self.writer._pending)
            with self.assertRaises(StorageError):self.writer.snapshot(self.other,sid)
        self.store.fault=None;self.mono[0]=11;self.writer.tick()
        current=self.store.snapshot(self.user,sid);self.assertEqual(current['status'],'interrupted');self.assertEqual(current['total_count'],1)
        self.assertEqual(self.writer.discard(self.user,sid,self.auth,version,key)['unsaved_discarded'],True)

    def test_http_history_date_isolation_and_atomic_delete_replay(self):
        self.now[0]=int(datetime(2026,1,1,15,59,tzinfo=timezone.utc).timestamp()*1000)
        one=self.running();self.store.append_event(self.user,one,self.event());self.finish(one)
        self.now[0]+=120000;two=self.running();self.store.append_event(self.user,two,self.event());self.finish(two)
        active=self.running()
        result=self.client.get('/api/v1/records?date_from=2026-01-02&date_to=2026-01-02').json['data']
        self.assertEqual({item['session_id'] for item in result['records']},{two,active})
        detail=self.client.get('/api/v1/records/'+one).json['data'];self.assertEqual(len(detail['events']),1)
        other=self.app.test_client();self.login(other,'Other_User')
        self.assertEqual(other.get('/api/v1/records/'+one).status_code,404)
        versions={one:detail['state_version'],active:self.store.snapshot(self.user,active)['state_version']}
        bad=self.post(self.client,'/api/v1/records/delete',{'ids':[one,active],'expected_versions':versions})
        self.assertEqual(bad.status_code,409);self.assertEqual(self.client.get('/api/v1/records/'+one).status_code,200)
        key=s.new_id();body={'ids':[one],'expected_versions':{one:detail['state_version']}}
        deleted=self.post(self.client,'/api/v1/records/delete',body,key)
        self.assertEqual(deleted.status_code,200);self.assertEqual(self.post(self.client,'/api/v1/records/delete',body,key).json['data'],deleted.json['data'])
        self.assertEqual(self.client.get('/api/v1/records/'+one).status_code,404)

    def test_restart_only_started_records_marked_interrupted(self):
        ready=self.create();running=self.running();self.store.append_event(self.user,running,self.event())
        complete=self.running();self.finish(complete)
        self.store.recover_interrupted()
        self.assertEqual(self.store.snapshot(self.user,ready)['status'],'ready')
        self.assertEqual(self.store.snapshot(self.user,running)['status'],'interrupted')
        self.assertEqual(self.store.snapshot(self.user,running)['total_count'],1)
        self.assertEqual(self.store.snapshot(self.user,complete)['status'],'ended_early')

    def test_recovery_http_receipt_before_version_and_payload_conflict(self):
        sid=self.running();self.writer.submit(self.user,sid,self.auth,'event',self.event())
        def fail(point):
            if point=='before_commit':raise StorageError('fixture fault')
        self.store.fault=fail;pending=self.writer.submit(self.user,sid,self.auth,'event',self.event(2))
        key=s.new_id();body={'command':'retry_save','expected_version':pending['state_version']}
        self.store.fault=None
        first=self.post(self.client,f'/api/v1/sessions/{sid}/commands',body,key)
        self.assertEqual(first.status_code,200);self.assertEqual(first.json['data']['total_count'],2)
        repeated=self.post(self.client,f'/api/v1/sessions/{sid}/commands',body,key)
        self.assertEqual(repeated.json['data'],first.json['data'])
        altered=dict(body,expected_version=first.json['data']['state_version'])
        conflict=self.post(self.client,f'/api/v1/sessions/{sid}/commands',altered,key)
        self.assertEqual(conflict.status_code,409);self.assertEqual(conflict.json['error']['code'],'IDEMPOTENCY_CONFLICT')
        self.writer.release(self.user,sid);self.store.fault=fail
        pending=self.writer.submit(self.user,sid,self.auth,'event',self.event(3));key=s.new_id()
        body={'command':'discard_unsaved','expected_version':pending['state_version'],'last_committed_version':pending['state_version'],'confirm':True}
        self.store.fault=None;first=self.post(self.client,f'/api/v1/sessions/{sid}/commands',body,key)
        self.assertTrue(first.json['data']['unsaved_discarded'])
        self.assertEqual(self.post(self.client,f'/api/v1/sessions/{sid}/commands',body,key).json['data'],first.json['data'])
        self.assertEqual(self.store.snapshot(self.user,sid)['total_count'],2)

    def test_search_filters_and_start_date_not_creation_date(self):
        self.now[0]=int(datetime(2026,1,1,15,59,tzinfo=timezone.utc).timestamp()*1000)
        ready=self.create('barbell_overhead_press')
        self.now[0]+=120000;self.store.set_state(self.user,ready,'running',s.new_id());self.finish(ready)
        one=self.running();self.finish(one)
        result=self.client.get('/api/v1/records?search=肩推&source=camera&date_from=2026-01-02&date_to=2026-01-02').json['data']
        self.assertEqual([item['session_id'] for item in result['records']],[ready])
        self.assertEqual(self.client.get('/api/v1/records?search=%25_').json['data']['total'],0)
        self.assertEqual(self.client.get('/api/v1/records?date_from=2026-01-01&date_to=2026-01-01').json['data']['total'],0)
        self.assertEqual(self.client.get('/api/v1/records?search='+'x'*65).status_code,422)

    def test_group_lifecycle_follows_saved_events_and_intervals(self):
        for action in ['bodyweight_squat','forearm_plank']:
            sid=self.running(action)
            if action=='forearm_plank':self.store.append_intervals(self.user,sid,1,[dict(kind='hold',start_ms=0,end_ms=1000,algorithm_version='test')])
            else:self.store.append_event(self.user,sid,self.event())
            self.assertEqual(self.store.snapshot(self.user,sid)['groups'][0]['status'],'active')
            with self.db.transaction() as c:c.execute(update(s.session_sets).where(s.session_sets.c.session_id==sid).values(status='completed'))
            with self.assertRaises(APIError):
                if action=='forearm_plank':self.store.append_intervals(self.user,sid,1,[dict(kind='hold',start_ms=1000,end_ms=2000,algorithm_version='test')])
                else:self.store.append_event(self.user,sid,self.event(2))
            with self.db.transaction() as c:c.execute(update(s.session_sets).where(s.session_sets.c.session_id==sid).values(status='active'))
            self.finish(sid)
            data=self.store.snapshot(self.user,sid)
            self.assertEqual(data['groups'][0]['status'],'ended_early')
            self.assertEqual(data['hold_ms'] if action=='forearm_plank' else data['total_count'],1000 if action=='forearm_plank' else 1)


if __name__=='__main__':unittest.main()
