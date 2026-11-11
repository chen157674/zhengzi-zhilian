"""旧库到当前schema的保留/原子验收；所有数据明确为工程夹具。"""
from contextlib import closing
import hashlib,io,json,os,sqlite3,unittest
from unittest.mock import patch
from sqlalchemy import select,update
from fitness_app import db_schema as s
from fitness_app.database import Database,StorageError
import test_formal_records as shared
from legacy_schema_support import legacy_shape,rows_snapshot
from fitness_app.app import create_app
from fitness_app.profile_assets import AVATAR_IDS

class LegacyUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.runtime=patch.dict(os.environ,{'FITNESS_RUNTIME_DIR':str(self.fx.db.path.parent/'runtime')});self.runtime.start()
    def tearDown(self):self.runtime.stop();self.fx.tearDown()
    def historical_record(self):
        sid=self.fx.running()
        for index in range(1,4):self.fx.store.append_event(self.fx.user,sid,self.fx.event(index))
        self.fx.finish(sid);return sid
    def test_target_validation_failure_does_not_commit_new_version_or_tables(self):
        sid=self.historical_record();legacy_shape(self.fx.db,7);original=s.report_exports.create
        def incomplete(connection,*args,**kwargs):
            original(connection,*args,**kwargs);connection.exec_driver_sql('DROP INDEX ix_exports_owner_created')
        with patch.object(s.report_exports,'create',side_effect=incomplete):
            with self.assertRaises(StorageError):self.fx.db.initialize(allow_round5_migration=True)
        with closing(sqlite3.connect(self.fx.db.path)) as c:
            self.assertEqual(c.execute('SELECT schema_version FROM app_meta').fetchone()[0],7)
            self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE name='report_exports'").fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM action_events WHERE session_id=?',(sid,)).fetchone()[0],3)

    def test_schema_one_through_eight_preserve_existing_business_rows_and_reopen(self):
        versions=tuple(int(v) for v in os.environ.get('FITNESS_MIGRATION_VERSIONS','1,2,3,4,5,6,7,8').split(','))
        if not versions or any(v not in range(1,9) for v in versions):raise ValueError('旧版本验证范围无效')
        for version in versions:
            with self.subTest(version=version):
                fx=shared.RecordTests();fx.setUp();opened=None
                try:
                    sid=fx.running()
                    for index in range(1,4):fx.store.append_event(fx.user,sid,fx.event(index))
                    if version>=2:
                        from fitness_app.catalogue import get_action
                        rule=get_action('bodyweight_squat')['issues'][0]
                        fx.store.append_posture(fx.user,sid,1,[{'code':rule['code'],'side':'none','status':'unassessable','start_ms':0,'end_ms':3000,'rule_version':rule['rule_version'],'evidence_level':'reference','body_parts':rule['body_parts'],'message':'工程旧库观察','suggestion':rule['suggestion'],'evidence':{'view':'unknown'},'reason':'fixture_scope'}],fx.auth,3000)
                    fx.finish(sid);avatar_hash=None;avatar_profile=None
                    if version>=3:
                        self.assertEqual(fx.client.get('/api/v1/account/profile').status_code,200)
                        with fx.db.transaction() as c:c.execute(update(s.user_profiles).where(s.user_profiles.c.user_id==fx.user).values(nickname='迁移保留昵称',avatar_id=AVATAR_IDS[-1],version=7))
                    if version>=4:
                        from test_avatar_upload import picture
                        csrf=fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
                        response=fx.client.post('/api/v1/account/avatar',data={'expected_version':'7','file':(io.BytesIO(picture()),'fixture.png')},headers={'X-CSRFToken':csrf})
                        self.assertEqual(response.status_code,200,response.json);avatar_profile=response.json['data']
                        with fx.client.get(avatar_profile['avatar_url']) as image:avatar_hash=hashlib.sha256(image.data).hexdigest()
                    if version>=6:
                        info=fx.app.extensions['training_info'];info.write(fx.user,fx.auth,{'expected_version':0,'experience':'beginner','available_minutes':35},s.new_id())
                        info.write(fx.user,fx.auth,{'expected_version':0,'load_value':5,'load_unit':'plate_index','load_basis':'device_setting','difficulty':'hard','note':'旧库自填档位，不是公斤'},s.new_id(),sid=sid)
                    if version>=7:
                        fx.app.extensions['ai_settings'].write(fx.user,fx.auth,{'expected_version':0,'source':'personal','personal_key':'sk-isolated-migration-fixture-123456','history_reference':False},s.new_id())
                        with fx.db.transaction() as c:c.execute(s.ai_requests.insert().values(id=s.new_id(),user_id=fx.user,auth_id=fx.auth,kind='review',source='personal',status='succeeded',model='deepseek-flash',request_digest='a'*64,context_json=json.dumps({'version':'legacy-fixture','record_ids':[sid]}),result_json=json.dumps({'summary':'工程旧版保存正文，不冒充当前建议'}),reserved_micro_cny=0,estimated_micro_cny=140,settings_version=1,policy_version=0,max_output_tokens=1800,pricing_version='fixture-legacy',created_at_ms=fx.now[0]))
                    parent=self.video_parent(fx) if version==8 else None
                    legacy_shape(fx.db,version);before=rows_snapshot(fx.db)
                    self.assertEqual(fx.db.inspect_identity(allow_legacy=True)['schema_version'],version)
                    fx.db.initialize(allow_round5_migration=True);self.assertEqual(fx.db.inspect_identity()['schema_version'],s.SCHEMA_VERSION);self.assertEqual(rows_snapshot(fx.db,before),before)
                    complete=rows_snapshot(fx.db);fx.db.initialize(allow_round5_migration=True);self.assertEqual(rows_snapshot(fx.db),complete)
                    opened=create_app({'TESTING':True,'DATABASE_PATH':str(fx.db.path),'SECRET_KEY':'test-only','CLOCK':lambda:fx.now[0]});client=opened.test_client();self.assertEqual(fx.login(client,'Record_User').status_code,200)
                    record=client.get('/api/v1/records/'+sid);self.assertEqual(record.status_code,200,record.json);self.assertEqual(record.json['data']['total_count'],3)
                    profile=client.get('/api/v1/account/profile').json['data'];self.assertIn(profile['avatar_id'],AVATAR_IDS)
                    if version>=3:self.assertEqual(profile['nickname'],'迁移保留昵称')
                    if version>=4:
                        self.assertEqual(profile['avatar_source'],'uploaded');self.assertEqual(profile['profile_version'],avatar_profile['profile_version'])
                        with client.get(profile['avatar_url']) as image:self.assertEqual(hashlib.sha256(image.data).hexdigest(),avatar_hash)
                    if version>=6:
                        self.assertEqual(client.get('/api/v1/me/training-profile').json['data']['available_minutes'],35)
                        note=client.get('/api/v1/records/'+sid+'/training-note').json['data'];self.assertEqual((note['load_value'],note['load_unit'],note['load_basis']),(5,'plate_index','device_setting'))
                    if version>=7:
                        settings=opened.extensions['ai_settings'];self.assertFalse(settings.get(fx.user,fx.auth)['history_reference'])
                        with opened.extensions['database'].transaction(False) as c:_,_,key,source=settings.active(c,fx.user)
                        self.assertEqual(source,'personal');self.assertEqual(key,'sk-isolated-migration-fixture-123456')
                    if parent:
                        value=client.get('/api/v1/video-analyses/'+parent).json['data'];self.assertEqual(value['segments'][0]['current_revision']['status'],'completed')
                        scope=client.get('/api/v1/video-analyses/'+parent+'/delete-options');self.assertEqual(scope.status_code,200,scope.json);self.assertEqual(scope.json['data']['record_count'],1)
                finally:
                    if opened:
                        opened.extensions['ai_jobs'].stop();opened.extensions['report_exports'].stop();opened.extensions['database'].dispose()
                    fx.tearDown()

    def video_parent(self,fx):
        iid,pid,segment,rid=[s.new_id() for _ in range(4)];sha='a'*64;policy=fx.store.processing_policy
        with fx.db.transaction() as c:
            c.execute(s.media_inputs.insert().values(id=iid,user_id=fx.user,status='consumed',sha256=sha,bytes=100,duration_ms=4000,encoding_json=json.dumps({'width':64,'height':48}),expires_at_ms=fx.now[0]+30000))
            c.execute(s.video_analyses.insert().values(id=pid,user_id=fx.user,owner_auth_session_id=fx.auth,input_id=iid,source_sha256=sha,source_bytes=100,duration_ms=4000,source_width=64,source_height=48,status='running',phase='posture',processed_ms=4000,config_json=json.dumps({'input_policy':policy,'coaching_view':'auto'})))
            c.execute(s.video_segments.insert().values(id=segment,user_id=fx.user,analysis_id=pid,segment_index=1,start_ms=0,end_ms=4000,action_id='bodyweight_squat',kind='action',metadata_json='{}'))
            c.execute(s.video_revisions.insert().values(id=rid,user_id=fx.user,segment_id=segment,input_id=iid,owner_auth_session_id=fx.auth,revision_index=1,origin='auto',action_id='bodyweight_squat',config_json=json.dumps({'parent_pass':True,'input_policy':policy,'coaching_view':'auto','source_start_ms':0,'source_end_ms':4000})))
        store=fx.app.extensions['video_analyses'].store;child=store.child(fx.user,fx.auth,rid);sid=child['session_id']
        fx.store.set_state(fx.user,sid,'running',s.new_id(),auth_id=fx.auth);fx.store.set_state(fx.user,sid,'completed',s.new_id(),auth_id=fx.auth)
        store.promote(fx.user,fx.auth,rid);store.finish(fx.user,pid,'completed')
        with fx.db.transaction() as c:c.execute(update(s.media_inputs).where(s.media_inputs.c.id==iid).values(status='cleaned'))
        return pid

    def test_maintenance_target_validation_is_inside_rollback_boundary(self):
        sid=self.historical_record();before=rows_snapshot(self.fx.db)
        def invalid(c):
            c.exec_driver_sql('DROP INDEX ix_video_revision_current');c.execute(update(s.users).where(s.users.c.id==self.fx.user).values(username_display='must_not_commit'))
        with self.assertRaises(StorageError):self.fx.db.maintenance_change(invalid)
        self.assertEqual(rows_snapshot(self.fx.db),before);self.assertEqual(self.fx.db.inspect_identity()['schema_version'],s.SCHEMA_VERSION)
        with self.assertRaises(StorageError):self.fx.db.maintenance_change(lambda c:None,target_version=s.SCHEMA_VERSION+1)
        self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['total_count'],3)
        self.fx.db.maintenance_change(lambda c:c.execute(update(s.users).where(s.users.c.id==self.fx.user).values(username_display='Record_User_维护')))
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(s.users.c.username_display).where(s.users.c.id==self.fx.user)),'Record_User_维护')

    def test_schema_three_retired_avatar_fallback_keeps_nickname_and_history(self):
        sid=self.historical_record();self.fx.client.get('/api/v1/account/profile')
        with self.fx.db.transaction() as c:c.execute(update(s.user_profiles).where(s.user_profiles.c.user_id==self.fx.user).values(nickname='旧版保留昵称',avatar_id='avatar-24',version=5))
        legacy_shape(self.fx.db,3);self.fx.db.initialize(allow_round5_migration=True)
        with self.fx.db.transaction(False) as c:profile=c.execute(select(s.user_profiles).where(s.user_profiles.c.user_id==self.fx.user)).mappings().one()
        self.assertEqual(profile['nickname'],'旧版保留昵称');self.assertEqual(profile['avatar_id'],AVATAR_IDS[0]);self.assertEqual(profile['version'],6);self.assertIsNone(profile['avatar_key'])
        self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['total_count'],3)

    def test_existing_foreign_key_violation_does_not_get_a_current_version_marker(self):
        sid=self.historical_record();legacy_shape(self.fx.db,7)
        with closing(sqlite3.connect(self.fx.db.path)) as c,c:
            c.execute('UPDATE analysis_sessions SET owner_auth_session_id=? WHERE id=?',(s.new_id(),sid))
        before=rows_snapshot(self.fx.db)
        with self.assertRaises(StorageError):self.fx.db.initialize(allow_round5_migration=True)
        with closing(sqlite3.connect(self.fx.db.path)) as c:self.assertEqual(c.execute('SELECT schema_version FROM app_meta').fetchone()[0],7)
        self.assertEqual(rows_snapshot(self.fx.db),before)

    def test_invalid_new_target_removes_only_its_new_empty_file(self):
        from fitness_app import database as module
        sid=self.historical_record();before=rows_snapshot(self.fx.db);path=self.fx.db.path.with_name('new-target.sqlite3');original=module.create_schema
        def invalid(c):original(c);c.exec_driver_sql('DROP INDEX ix_exports_owner_created')
        target=Database(path)
        try:
            with patch.object(module,'create_schema',side_effect=invalid):
                with self.assertRaises(StorageError):target.initialize(allow_round5_migration=True)
            self.assertFalse(path.exists());self.assertEqual(rows_snapshot(self.fx.db),before)
            self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['total_count'],3)
        finally:target.dispose()

if __name__=='__main__':unittest.main()
