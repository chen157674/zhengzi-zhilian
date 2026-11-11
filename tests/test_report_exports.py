"""PDF导出真实HTTP/后台生命周期；渲染协议替身不冒充排版或真实AI质量。"""
import copy,json,threading,time,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
from sqlalchemy import select,update
from fitness_app import db_schema as s
from fitness_app.database import json_text,StorageError
from fitness_app.report_exports import ReportExports,TTL_MS
from fitness_app.pdf_report import ReportRenderCancelled
from fitness_app.export_snapshot import normalize
import test_pdf_report as fixtures
import test_ai_jobs as ai_fixture
from legacy_schema_support import legacy_shape


class Renderer:
    def __init__(self):self.calls=0;self.entered=threading.Event();self.release=threading.Event();self.release.set();self.snapshots=[]
    def __call__(self,snapshot,rid,created,cancelled):
        self.calls+=1;self.snapshots.append(snapshot);self.entered.set()
        if not self.release.wait(5):raise RuntimeError('controlled renderer timeout')
        if cancelled():raise ReportRenderCancelled('cancelled')
        return b'%PDF-1.7\ncontrolled-render-fixture\n%%EOF\n'


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PdfRenderTests();self.fixture.setUp();self.fx=self.fixture.fx;self.service=self.fx.app.extensions['report_exports'];self.renderer=Renderer();self.service.renderer=self.renderer;self.sid=self.fixture.record('user_selected')
    def tearDown(self):
        self.renderer.release.set();self.service.stop();ai=self.fx.app.extensions['ai_jobs'];ai.stop()
        for worker in ai.workers:worker.join(5)
        self.fx.tearDown()
    def payload(self,**kwargs):return {'record_ids':[self.sid],'expected_versions':{self.sid:self.fx.store.snapshot(self.fx.user,self.sid)['state_version']},**kwargs}
    def create(self,payload=None,key=None):
        response=self.fx.post(self.fx.client,'/api/v1/reports/exports',payload or self.payload(),key);self.assertEqual(response.status_code,202,response.json);return response.json['data']
    def wait(self,rid):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            row=self.fx.client.get('/api/v1/reports/exports/'+rid).json['data']
            if row['status'] in {'ready','failed','expired','invalidated','deleted'}:return row
            time.sleep(.015)
        self.fail('报告后台任务未结束')

    def test_new_layout_policy_creates_new_export_without_rewriting_history(self):
        first = self.create()
        self.assertEqual(self.wait(first['id'])['status'], 'ready')
        before = self.fx.client.get('/api/v1/reports/exports/' + first['id'] + '/download').data
        with patch('fitness_app.export_snapshot.VERSION', 'fixture-next-layout'):
            second = self.create()
            self.assertNotEqual(second['id'], first['id'])
            self.assertEqual(self.wait(second['id'])['status'], 'ready')
        old = self.fx.client.get('/api/v1/reports/exports/' + first['id'])
        self.assertEqual(old.json['data']['status'], 'ready')
        after = self.fx.client.get('/api/v1/reports/exports/' + first['id'] + '/download').data
        self.assertEqual(before, after)
        self.assertEqual(self.renderer.calls, 2)

    def test_same_request_and_same_snapshot_reuse_one_generation_and_download_does_not_regenerate(self):
        self.renderer.release.clear();payload=self.payload();key=s.new_id();first=self.create(payload,key);self.assertTrue(self.renderer.entered.wait(2))
        self.assertEqual(self.create(payload,key)['id'],first['id']);self.assertEqual(self.create(payload,s.new_id())['id'],first['id'])
        conflict=self.fx.post(self.fx.client,'/api/v1/reports/exports',{**payload,'include_user_notes':True},key);self.assertEqual(conflict.status_code,409)
        self.renderer.release.set();row=self.wait(first['id']);self.assertEqual(row['status'],'ready',row)
        a=self.fx.client.get('/api/v1/reports/exports/'+row['id']+'/download');b=self.fx.client.get('/api/v1/reports/exports/'+row['id']+'/download')
        self.assertEqual(a.status_code,200);self.assertEqual(a.data,b.data);self.assertIn('no-store',a.headers['Cache-Control']);self.assertIn('attachment',a.headers['Content-Disposition']);self.assertEqual(self.renderer.calls,1)
        self.assertTrue(a.headers['Content-Type'].startswith('application/pdf'))

    def test_owner_checks_default_content_and_notes_edit_then_clear(self):
        self.fx.app.extensions['training_info'].write(self.fx.user,self.fx.auth,{'expected_version':0,'note':'旧备注保留于快照','load_value':12.5,'load_unit':'kg','load_basis':'per_hand'},s.new_id(),sid=self.sid)
        first=self.wait(self.create()['id']);self.assertIsNone(self.renderer.snapshots[0]['records'][0]['training_note'])
        row=self.wait(self.create(self.payload(include_user_notes=True))['id']);rid=row['id'];other=self.fx.app.test_client();self.fx.login(other,'other_user')
        self.assertEqual(other.get('/api/v1/reports/exports/'+rid).status_code,404);self.assertEqual(other.get('/api/v1/reports/exports/'+rid+'/download').status_code,404)
        self.fx.app.extensions['training_info'].write(self.fx.user,self.fx.auth,{'expected_version':1,'note':'新的备注','load_value':12.5,'load_unit':'kg','load_basis':'per_hand'},s.new_id(),sid=self.sid)
        state=self.service.get(self.fx.user,self.fx.auth,rid);self.assertEqual(state['snapshot_state'],'stale');self.assertEqual(self.fx.client.get('/api/v1/reports/exports/'+rid+'/download').status_code,200)
        self.fx.app.extensions['training_info'].write(self.fx.user,self.fx.auth,{'expected_version':2,'note':None,'load_value':12.5,'load_unit':'kg','load_basis':'per_hand'},s.new_id(),sid=self.sid)
        self.assertEqual(self.fx.client.get('/api/v1/reports/exports/'+rid+'/download').status_code,410)
        with self.fx.db.transaction(False) as c:
            result=c.execute(select(s.report_exports).where(s.report_exports.c.id==rid)).mappings().one();self.assertIsNone(result['pdf_blob']);self.assertIsNone(result['snapshot_json'])
        self.assertEqual(self.service.get(self.fx.user,self.fx.auth,first['id'])['status'],'ready')

    def test_record_update_marks_old_snapshot_and_delete_invalidates_atomically(self):
        row=self.wait(self.create()['id']);record=self.fx.store.snapshot(self.fx.user,self.sid)
        with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==self.sid).values(config_json=json_text({**record['config'],'included_in_statistics':False}),state_version=record['state_version']+1))
        self.assertEqual(self.service.get(self.fx.user,self.fx.auth,row['id'])['snapshot_state'],'stale')
        self.assertEqual(self.fx.client.get('/api/v1/reports/exports/'+row['id']+'/download').headers['X-Report-Snapshot-State'],'stale')
        rejected=self.fx.post(self.fx.client,'/api/v1/reports/exports',self.payload());self.assertEqual(rejected.status_code,409)
        self.fx.store.delete_records(self.fx.user,self.fx.auth,[self.sid],{self.sid:record['state_version']+1},s.new_id())
        with self.fx.db.transaction(False) as c:
            saved=c.execute(select(s.report_exports).where(s.report_exports.c.id==row['id'])).mappings().one();self.assertEqual(saved['status'],'invalidated');self.assertIsNone(saved['pdf_blob']);self.assertIsNone(saved['snapshot_json'])

    def test_source_deleted_while_rendering_cannot_publish_late_pdf(self):
        self.renderer.release.clear();row=self.create();self.assertTrue(self.renderer.entered.wait(2));version=self.fx.store.snapshot(self.fx.user,self.sid)['state_version']
        self.fx.store.delete_records(self.fx.user,self.fx.auth,[self.sid],{self.sid:version},s.new_id());self.renderer.release.set()
        self.assertEqual(self.wait(row['id'])['status'],'invalidated');self.service.jobs.join();self.assertEqual(self.fx.client.get('/api/v1/reports/exports/'+row['id']+'/download').status_code,410)

    def test_expiry_and_manual_delete_remove_copies_but_keep_training_record(self):
        row=self.wait(self.create()['id']);token=self.fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token'];key=s.new_id()
        deleted=self.fx.client.delete('/api/v1/reports/exports/'+row['id'],json={'expected_version':row['state_version']},headers={'X-CSRFToken':token,'Idempotency-Key':key});self.assertEqual(deleted.status_code,200,deleted.json)
        self.assertEqual(self.fx.client.delete('/api/v1/reports/exports/'+row['id'],json={'expected_version':row['state_version']},headers={'X-CSRFToken':token,'Idempotency-Key':key}).status_code,200)
        fresh=self.wait(self.create()['id']);self.fx.now[0]+=TTL_MS;self.service.sweep()
        with self.fx.db.transaction(False) as c:
            expired=c.execute(select(s.report_exports).where(s.report_exports.c.id==fresh['id'])).mappings().one();self.assertEqual(expired['status'],'expired');self.assertIsNone(expired['pdf_blob']);self.assertIsNone(expired['snapshot_json'])
        self.fx.login(self.fx.client,'Record_User')
        with self.fx.client.session_transaction() as state:self.fx.auth=state['_user_id']
        self.assertEqual(self.fx.client.get('/api/v1/reports/exports/'+fresh['id']+'/download').status_code,410);self.assertEqual(self.fx.store.snapshot(self.fx.user,self.sid)['session_id'],self.sid)

    def test_restart_marks_unfinished_task_without_sending_it_again(self):
        with patch.object(self.service,'_start'):row=self.create()
        replacement=ReportExports(self.fx.app)
        try:replacement.recover();self.assertEqual(replacement.get(self.fx.user,self.fx.auth,row['id'])['status'],'failed');self.assertEqual(self.renderer.calls,0)
        finally:replacement.stop()

    def test_ai_report_is_explicit_scoped_and_not_regenerated_by_export(self):
        provider=ai_fixture.Provider();self.fx.app.extensions['ai_provider_factory']=lambda:provider
        self.fx.app.extensions['ai_settings'].write(self.fx.user,self.fx.auth,{'expected_version':0,'source':'personal','personal_key':'sk-controlled-report-fixture-123456789'},s.new_id())
        response=self.fx.post(self.fx.client,'/api/v1/ai/reports',{'kind':'review','context_kind':'record','context_id':self.sid,'history_scope':'none','consent':True});self.assertEqual(response.status_code,202,response.json);rid=response.json['data']['request_id']
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            ai=self.fx.client.get('/api/v1/ai/reports/'+rid).json['data']
            if ai['status'] in {'succeeded','failed'}:break
            time.sleep(.015)
        self.assertEqual(ai['status'],'succeeded',ai);available=self.service.builder.options(self.fx.user,self.fx.auth,[self.sid]);self.assertIn(rid,[v['id'] for v in available['ai_reports']])
        row=self.wait(self.create(self.payload(include_ai_report=True,ai_report_ids=[rid]))['id']);self.assertEqual(len(self.renderer.snapshots[-1]['ai_reports']),1);self.assertEqual(provider.calls,1)
        token=self.fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token'];deleted=self.fx.client.delete('/api/v1/ai/reports/'+rid,json={'expected_version':ai['state_version']},headers={'X-CSRFToken':token});self.assertEqual(deleted.status_code,200,deleted.json)
        self.assertEqual(self.fx.client.get('/api/v1/reports/exports/'+row['id']+'/download').status_code,410)

    def test_schema10_to_current_atomic_migration_keeps_records(self):
        legacy_shape(self.fx.db, 10)
        with patch.object(s.report_exports,'create',side_effect=RuntimeError('migration fixture')):
            with self.assertRaises(StorageError):self.fx.db.initialize(allow_round5_migration=True)
        with closing(sqlite3.connect(self.fx.db.path)) as c:self.assertEqual(c.execute('SELECT schema_version FROM app_meta').fetchone()[0],10)
        self.fx.db.initialize(allow_round5_migration=True);self.assertEqual(self.fx.db.inspect_identity()['schema_version'],s.SCHEMA_VERSION);self.assertEqual(self.fx.store.snapshot(self.fx.user,self.sid)['session_id'],self.sid)
        with self.fx.db.transaction() as c:c.exec_driver_sql('DROP INDEX ix_exports_owner_fingerprint')
        with self.assertRaises(StorageError):self.fx.db.inspect_identity()

    def test_invalid_inputs_and_incompatible_processing_do_not_claim_comparable_results(self):
        for extra in [{'include_user_notes':'true'},{'format':'html'},{'include_ai_report':True},{'unknown':True}]:
            response=self.fx.post(self.fx.client,'/api/v1/reports/exports',self.payload(**extra));self.assertIn(response.status_code,(400,422),response.json)
        second=self.fixture.record('automatically_recognized');data=self.fx.store.snapshot(self.fx.user,second)
        with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==second).values(config_json=json_text({**data['config'],'input_policy':{'different':'processing'}})))
        payload={'record_ids':[self.sid,second],'expected_versions':{sid:self.fx.store.snapshot(self.fx.user,sid)['state_version'] for sid in [self.sid,second]}}
        snapshot,_,_=self.service.builder.freeze(self.fx.user,self.fx.auth,normalize(payload));self.assertFalse(snapshot['comparison']['comparable']);self.assertEqual(self.renderer.calls,0)

    def test_partial_pdf_and_corruption_are_not_served_as_success(self):
        self.service.renderer=lambda *args:b'%PDF-1.7\nunfinished'
        row=self.wait(self.create()['id']);self.assertEqual(row['status'],'failed');self.assertFalse(row['download_available'])
        self.service.renderer=self.renderer;valid=self.wait(self.create()['id'])
        with self.fx.db.transaction() as c:c.execute(update(s.report_exports).where(s.report_exports.c.id==valid['id']).values(pdf_sha256='0'*64))
        self.assertEqual(self.fx.client.get('/api/v1/reports/exports/'+valid['id']+'/download').status_code,503)
        with self.fx.db.transaction(False) as c:self.assertIsNone(c.scalar(select(s.report_exports.c.pdf_blob).where(s.report_exports.c.id==valid['id'])))

    def test_logout_during_generation_does_not_publish_a_late_report(self):
        self.renderer.release.clear();row=self.create();self.assertTrue(self.renderer.entered.wait(2));logout=self.fx.post(self.fx.client,'/api/v1/auth/logout',{});self.assertEqual(logout.status_code,200,logout.json)
        self.renderer.release.set();self.service.jobs.join()
        with self.fx.db.transaction(False) as c:
            record=c.execute(select(s.report_exports).where(s.report_exports.c.id==row['id'])).mappings().one();self.assertEqual(record['status'],'failed');self.assertIsNone(record['pdf_blob'])

    def test_ai_history_outside_selected_records_is_not_silently_exported(self):
        other=self.fixture.record('automatically_recognized');provider=ai_fixture.Provider();self.fx.app.extensions['ai_provider_factory']=lambda:provider
        self.fx.app.extensions['ai_settings'].write(self.fx.user,self.fx.auth,{'expected_version':0,'source':'personal','personal_key':'sk-history-report-fixture-123456789'},s.new_id())
        response=self.fx.post(self.fx.client,'/api/v1/ai/reports',{'kind':'review','context_kind':'record','context_id':self.sid,'history_scope':'related','consent':True});self.assertEqual(response.status_code,202,response.json);rid=response.json['data']['request_id'];deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            state=self.fx.client.get('/api/v1/ai/reports/'+rid).json['data']
            if state['status'] in {'succeeded','failed'}:break
            time.sleep(.015)
        self.assertEqual(state['status'],'succeeded',state)
        rejected=self.fx.post(self.fx.client,'/api/v1/reports/exports',self.payload(include_ai_report=True,ai_report_ids=[rid]));self.assertEqual(rejected.status_code,409,rejected.json);self.assertEqual(self.renderer.calls,0)
        scope=self.service.builder.options(self.fx.user,self.fx.auth,[self.sid,other]);self.assertIn(rid,[v['id'] for v in scope['ai_reports']])

    def test_pending_export_reserves_capacity_and_completion_releases_unused_reservation(self):
        from fitness_app.pdf_report import MAX_PDF_BYTES
        with patch('fitness_app.report_exports.MAX_RETAINED_BYTES',MAX_PDF_BYTES+100):
            self.renderer.release.clear();first=self.create();self.assertTrue(self.renderer.entered.wait(2))
            blocked=self.fx.post(self.fx.client,'/api/v1/reports/exports',self.payload(include_user_notes=True));self.assertEqual(blocked.status_code,429,blocked.json)
            self.renderer.release.set();self.assertEqual(self.wait(first['id'])['status'],'ready')
            allowed=self.wait(self.create(self.payload(include_user_notes=True))['id']);self.assertEqual(allowed['status'],'ready');self.assertEqual(self.renderer.calls,2)


if __name__=='__main__':unittest.main()
