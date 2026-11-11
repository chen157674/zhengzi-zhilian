"""13 新管理员任务、归属和取消边界；结果复用 11 的真实冻结预测。"""
import threading
import unittest
from unittest.mock import patch
from sqlalchemy import select,update,func

import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.auth import password_hash
from fitness_app.database import StorageError,json_text


@unittest.skip('2026-10-20 用户取消产品算法实验；旧Web任务用例保留作历史，移除行为由test_account_backend验证')
class ExperimentJobTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.user_client=self.fx.client
        self.client=self.fx.app.test_client();self.fx.login(self.client,'Record_Admin')
        with self.client.session_transaction() as session:self.auth=session['_user_id']
        with self.fx.db.transaction() as c:
            self.uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key=='record_admin'))
            c.execute(s.users.insert().values(username_display='Other_Admin',username_key='other_admin',role='admin',password_hash=password_hash(shared.PASSWORD)))
        self.other=self.fx.app.test_client();self.fx.login(self.other,'Other_Admin')
        self.manager=self.fx.app.extensions['experiment_manager'];self.gates=[]
        options=self.client.get('/api/v1/admin/experiment-options').json['data'];task=options['tasks'][0]
        self.payload={'task_id':task['task_id'],'dataset_version':task['dataset_version'],'split_hash':task['split_hash'],'method_ids':['majority','svm-c0.1']}

    def tearDown(self):
        for gate in self.gates:gate.set()
        self.manager.stop();self.fx.tearDown()

    def create(self,key=None,payload=None):
        response=self.fx.post(self.client,'/api/v1/admin/experiments',payload or self.payload,key)
        self.assertEqual(response.status_code,202,response.json);return response.json['data']

    def wait(self,eid):
        self.manager.runs[eid].thread.join(5);self.assertFalse(self.manager.runs[eid].thread.is_alive())
        response=self.client.get('/api/v1/admin/experiments/'+eid);self.assertEqual(response.status_code,200,response.json);return response.json['data']

    def barrier(self,method='svm-c0.1'):
        entered=threading.Event();release=threading.Event();self.gates.append(release)
        def hook(method_id,stop):
            if method_id==method:entered.set();release.wait(5)
        self.manager.method_hook=hook;return entered,release

    def test_admin_create_replay_real_results_and_explicit_new_run(self):
        self.assertEqual(self.user_client.get('/api/v1/admin/experiment-options').status_code,403)
        self.assertEqual(self.fx.post(self.user_client,'/api/v1/admin/experiments',self.payload).status_code,403)
        key=s.new_id();created=self.create(key);eid=created['experiment_id'];done=self.wait(eid)
        self.assertEqual(done['status'],'completed');self.assertEqual(done['result_completeness'],'full');self.assertTrue(done['stopped'])
        majority=done['results']['methods'][0];self.assertTrue(majority['reused_prediction_cache']);self.assertIsNone(majority['metrics']['main']['positive_precision']);self.assertEqual(majority['metrics']['main']['positive_recall'],0)
        self.assertEqual(self.create(key)['experiment_id'],eid)
        changed={**self.payload,'method_ids':['majority','rf-depth4']};self.assertEqual(self.fx.post(self.client,'/api/v1/admin/experiments',changed,key).status_code,409)
        second=self.create();self.assertNotEqual(second['experiment_id'],eid);self.wait(second['experiment_id'])
        listed=self.client.get('/api/v1/admin/experiments?page=1&page_size=1').json['data'];self.assertEqual(listed['total'],2);self.assertEqual(len(listed['experiments']),1)
        self.assertEqual(self.client.get('/api/v1/admin/experiments?page=0').status_code,422)
        self.assertEqual(self.user_client.get('/api/v1/records').json['data']['total'],0)

    def test_global_slot_owner_scope_cancel_and_late_result_fence(self):
        entered,release=self.barrier();created=self.create();eid,jid=created['experiment_id'],created['job_id'];self.assertTrue(entered.wait(5))
        before=self.client.get('/api/v1/jobs/'+jid).json['data'];self.assertEqual(before['results']['processed_methods'],1)
        self.assertEqual(self.fx.post(self.client,'/api/v1/admin/experiments',self.payload).status_code,409)
        self.assertEqual(self.other.get('/api/v1/admin/experiments/'+eid).status_code,404)
        self.assertEqual(self.other.get('/api/v1/jobs/'+jid).status_code,404)
        self.assertEqual(self.fx.post(self.other,f'/api/v1/jobs/{jid}/cancel',{'expected_version':before['state_version']}).status_code,404)
        cancelled=self.fx.post(self.client,f'/api/v1/jobs/{jid}/cancel',{'expected_version':before['state_version']});self.assertEqual(cancelled.status_code,202,cancelled.json)
        self.assertEqual(cancelled.json['data']['status'],'cancelling');self.assertFalse(cancelled.json['data']['stopped'])
        release.set();done=self.wait(eid);self.assertEqual(done['status'],'cancelled');self.assertEqual(done['result_completeness'],'partial')
        self.assertEqual([row['status'] for row in done['results']['methods']],['completed','queued']);self.assertIsNone(done['results']['methods'][1]['metrics'])
        self.assertFalse(self.fx.app.extensions['vision_slot'].owns(self.uid,jid,'experiment'))

    def test_partial_method_failure_does_not_replace_or_erase_success(self):
        def fail(method,stop):
            if method=='svm-c0.1':raise ValueError('D-FAULT method failed')
        self.manager.method_hook=fail;created=self.create();done=self.wait(created['experiment_id'])
        self.assertEqual(done['status'],'failed');self.assertEqual(done['result_completeness'],'partial')
        methods=done['results']['methods'];self.assertEqual([row['method_id'] for row in methods],self.payload['method_ids'])
        self.assertEqual(methods[0]['status'],'completed');self.assertEqual(methods[1]['status'],'failed');self.assertIsNone(methods[1]['metrics'])

    def test_original_auth_revocation_stops_commit_and_same_admin_can_reenter(self):
        entered,release=self.barrier();created=self.create();eid=created['experiment_id'];self.assertTrue(entered.wait(5))
        fresh=self.fx.app.test_client();self.fx.login(fresh,'Record_Admin');self.assertEqual(fresh.get('/api/v1/admin/experiments/'+eid).status_code,200)
        response=self.fx.post(self.client,'/api/v1/auth/logout',{});self.assertEqual(response.status_code,200)
        release.set();self.manager.runs[eid].thread.join(5)
        done=fresh.get('/api/v1/admin/experiments/'+eid).json['data'];self.assertEqual(done['status'],'interrupted');self.assertEqual(done['results']['processed_methods'],1)
        self.assertEqual(done['results']['methods'][1]['status'],'queued')

    def test_storage_failure_keeps_only_confirmed_method_and_reports_failure(self):
        original=self.manager._persist
        def fail_second(rt,status,rows,error=None,actor=True):
            if status=='running' and len(rows)>1 and rows[1]['status']=='completed':raise StorageError('D-FAULT result write')
            return original(rt,status,rows,error,actor)
        with patch.object(self.manager,'_persist',side_effect=fail_second):
            created=self.create();done=self.wait(created['experiment_id'])
        self.assertEqual(done['status'],'failed');self.assertTrue(done['terminal_persisted']);self.assertEqual(done['result_completeness'],'partial')
        rows=done['results']['methods'];self.assertEqual(rows[0]['status'],'completed');self.assertEqual(rows[1]['error_code'],'RESULT_NOT_SAVED');self.assertIsNone(rows[1]['metrics'])

    def test_restart_marks_persisted_partial_interrupted_without_rerun(self):
        prepared=self.manager.sources.snapshot(self.payload);eid,jid=s.new_id(),s.new_id()
        first=prepared['methods']['majority'];rows=[{'method_id':'majority','status':'completed','metrics':first['metrics'],'predictions':first['predictions']},
            {'method_id':'svm-c0.1','status':'queued','metrics':None,'predictions':[]}]
        results,completeness=self.manager._result(rows)
        with self.fx.db.transaction() as c:
            c.execute(s.experiments.insert().values(id=eid,owner_id=self.uid,owner_auth_session_id=self.auth,config_json=json_text(prepared['config']),
                status='running',result_completeness=completeness,results_json=json_text(results),truth_status='source_clip_labels_only'))
            c.execute(s.jobs.insert().values(id=jid,owner_id=self.uid,kind='experiment',experiment_id=eid,status='running'))
        self.manager.recover();done=self.client.get('/api/v1/admin/experiments/'+eid).json['data']
        self.assertEqual(done['status'],'interrupted');self.assertEqual(done['results']['processed_methods'],1);self.assertTrue(done['stopped'])
        self.assertNotIn(eid,self.manager.runs)


if __name__=='__main__':unittest.main()
