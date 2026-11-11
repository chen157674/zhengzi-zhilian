"""个人中心、研究入口移除及追加式迁移的隔离验证。"""
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch
from sqlalchemy import select,func,update
from fitness_app import db_schema as s
from fitness_app.database import Database,StorageError
from fitness_app.profile_assets import AVATAR_IDS,AVATARS
import test_formal_records as shared


class AccountTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()
    def profile(self,client=None):return (client or self.fx.client).get('/api/v1/account/profile').json['data']

    def overview(self):
        response=self.fx.client.get('/api/v1/account/overview');self.assertEqual(response.status_code,200,response.json)
        return response.json['data']

    def test_empty_overview_has_unavailable_completion_instead_of_inferred_zero(self):
        data=self.overview();summary=data['completion_summary']
        self.assertEqual(data['total_sessions'],0);self.assertEqual(summary['unavailable_sessions'],0)
        for key in ('reps','hold'):
            self.assertEqual(summary[key]['status'],'not_available');self.assertIsNone(summary[key]['value'])
            self.assertEqual(summary[key]['session_count'],0)

    def test_posture_only_overview_keeps_historical_counting_unavailable(self):
        from fitness_app.catalogue import get_action,seed_catalogue
        fx=self.fx;sid=fx.running('barbell_overhead_press');fx.finish(sid)
        # 后续目录新增可计数版本，也不能重解释原记录的能力快照。
        future=get_action('barbell_overhead_press');future={**future,'capability_version':future['capability_version']+'-D-FUTURE','counting_available':True}
        with patch('fitness_app.catalogue.definitions',return_value=[future]):
            seed_catalogue(fx.db);self.assertTrue(get_action('barbell_overhead_press')['counting_available'])
            data=self.overview()
        summary=data['completion_summary']
        self.assertEqual((data['total_sessions'],summary['unavailable_sessions']),(1,1))
        self.assertEqual(data['reference_reps'],0);self.assertIsNone(summary['reps']['value']);self.assertIsNone(summary['hold']['value'])
        self.assertFalse(data['recent_records'][0]['counting_available'])
        self.assertEqual(fx.store.snapshot(fx.user,sid)['capability_version'],data['recent_records'][0]['capability_version'])

    def test_supported_zero_is_distinct_from_unavailable_hold(self):
        fx=self.fx;sid=fx.running();fx.finish(sid);data=self.overview()
        self.assertEqual(data['completion_summary']['reps'],{'status':'available','value':0,'unit':'reps','session_count':1})
        self.assertIsNone(data['completion_summary']['hold']['value']);self.assertEqual(data['completion_summary']['unavailable_sessions'],0)

    def test_mixed_units_and_multiple_sets_do_not_duplicate_available_sessions(self):
        from fitness_app.catalogue import get_action
        fx=self.fx;action='standing_dumbbell_curl'
        response=fx.post(fx.client,'/api/v1/sessions',{'action_id':action,'capability_version':get_action(action)['capability_version'],
                    'source':'camera','mode':'target','target':{'sets':2,'value':1,'unit':'per_side_reps','rest_seconds':0}})
        self.assertEqual(response.status_code,201,response.json);sid=response.json['data']['session_id']
        fx.store.set_state(fx.user,sid,'running',s.new_id())
        for seq,side in enumerate(('left','right','left','right'),1):
            if seq==3:fx.store.set_state(fx.user,sid,'running',s.new_id())
            fx.store.append_event(fx.user,sid,{**fx.event(seq),'side':side,'set_index':1 if seq<=2 else 2})
        plank=fx.running('forearm_plank')
        fx.store.append_intervals(fx.user,plank,1,[{'kind':'hold','start_ms':0,'end_ms':1200,'algorithm_version':'D-OVERVIEW'}]);fx.finish(plank)
        press=fx.running('barbell_overhead_press');fx.finish(press)
        data=self.overview();summary=data['completion_summary']
        self.assertEqual(data['total_sessions'],3)
        self.assertEqual(summary['reps'],{'status':'available','value':4,'unit':'reps','session_count':1})
        self.assertEqual(summary['hold'],{'status':'available','value':1200,'unit':'ms','session_count':1})
        self.assertEqual(summary['unavailable_sessions'],1)
        self.assertEqual((data['reference_reps'],data['reference_hold_ms']),(4,1200))

    def test_registration_random_profile_persists_and_avatars_are_local(self):
        fx=self.fx
        response=fx.post(fx.client,'/api/v1/auth/register',{'username':'new_fitness_user@qq.com','password':shared.PASSWORD,'password_confirmation':shared.PASSWORD})
        self.assertEqual(response.status_code,201,response.json)
        data=response.json['data'];self.assertIn(data['avatar_id'],AVATAR_IDS);self.assertNotEqual(data['nickname'],'new_fitness_user@qq.com')
        fx.login(fx.client,'new_fitness_user@qq.com');first=self.profile();self.assertEqual(first,self.profile())
        self.assertEqual(first['nickname'],data['nickname']);self.assertEqual(first['avatar_id'],data['avatar_id'])
        self.assertEqual(fx.client.get('/api/v1/account/avatars').status_code,404)
        self.assertEqual(first['avatar_source'],'builtin');self.assertEqual(len(AVATARS),6)
        for item in AVATARS:
            with fx.client.get(item['url']) as response:
                self.assertEqual(response.status_code,200);self.assertTrue(response.data.startswith(b'\x89PNG'))

    def test_profile_updates_are_versioned_private_and_do_not_change_login(self):
        fx=self.fx;mine=self.profile();other=fx.app.test_client();fx.login(other,'Other_User');theirs=self.profile(other)
        response=fx.post(fx.client,'/api/v1/account/profile',{'nickname':'新的昵称','expected_version':mine['profile_version']})
        self.assertEqual(response.status_code,200,response.json);updated=response.json['data']
        self.assertEqual(updated['profile_version'],mine['profile_version']+1);self.assertEqual(updated['username'],'Record_User')
        self.assertEqual(fx.client.get('/api/v1/auth/me').json['data']['nickname'],'新的昵称')
        self.assertEqual(theirs,self.profile(other))
        stale=fx.post(fx.client,'/api/v1/account/profile',{'nickname':'旧标签页','expected_version':mine['profile_version']})
        self.assertEqual(stale.status_code,409)
        same=fx.post(other,'/api/v1/account/profile',{'nickname':'新的昵称','expected_version':theirs['profile_version']})
        self.assertEqual(same.status_code,200,'展示昵称允许重名，账号仍独立')

    def test_invalid_fields_external_avatar_and_missing_auth_are_rejected(self):
        fx=self.fx;mine=self.profile();version=mine['profile_version']
        for data,status in [({'nickname':'a'},422),({'nickname':'坏\x00名字'},422),({'nickname':'<script>'},422),
                            ({'avatar_id':'https://example.com/a.svg'},400),({'avatar_id':'../auth.py'},400),
                            ({'avatar_id':[]},400),({'user_id':fx.other},400),({'role':'admin'},400)]:
            response=fx.post(fx.client,'/api/v1/account/profile',{'expected_version':version,**data})
            self.assertEqual(response.status_code,status,response.json)
        self.assertEqual(mine,self.profile())
        self.assertEqual(fx.app.test_client().get('/api/v1/account/profile').status_code,401)

    def test_overview_includes_only_own_records_and_excludes_demo(self):
        fx=self.fx;sid=fx.running();fx.store.append_event(fx.user,sid,fx.event());fx.finish(sid)
        # 另一用户的实际记录不应计入本人概览。
        other=fx.app.test_client();fx.login(other,'Other_User')
        data=fx.post(other,'/api/v1/sessions',{'action_id':'bodyweight_squat','capability_version':self.fx.store.snapshot(fx.user,sid)['capability_version'],
                     'source':'camera','mode':'free','target':None}).json['data']
        oid=data['session_id'];fx.store.set_state(fx.other,oid,'running',s.new_id())
        fx.store.append_event(fx.other,oid,fx.event());fx.store.set_state(fx.other,oid,'ended_early',s.new_id())
        demo=fx.running();fx.store.append_event(fx.user,demo,fx.event());fx.finish(demo)
        with fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==demo).values(source='demo'))
        response=fx.client.get('/api/v1/account/overview');self.assertEqual(response.status_code,200,response.json)
        overview=response.json['data'];self.assertEqual(overview['total_sessions'],1);self.assertEqual(overview['reference_reps'],1)
        self.assertEqual(overview['completion_summary']['reps']['session_count'],1)
        self.assertEqual(overview['completion_summary']['reps']['value'],1)
        self.assertEqual([r['session_id'] for r in overview['recent_records']],[sid])

    def test_revoke_other_logins_keeps_current_and_never_returns_tokens(self):
        fx=self.fx;second=fx.app.test_client();fx.login(second,'Record_User');other=fx.app.test_client();fx.login(other,'Other_User')
        response=fx.client.get('/api/v1/account/sessions');text=response.get_data(as_text=True)
        self.assertEqual(sum(x['current'] for x in response.json['data']['sessions']),1)
        self.assertNotIn('token',text);self.assertNotIn('password',text)
        bad=fx.post(fx.client,'/api/v1/account/sessions/revoke-others',{'current_password':'wrong'});self.assertEqual(bad.status_code,422)
        self.assertEqual(second.get('/api/v1/auth/me').status_code,200)
        response=fx.post(fx.client,'/api/v1/account/sessions/revoke-others',{'current_password':shared.PASSWORD})
        self.assertEqual(response.json['data']['revoked_count'],1)
        self.assertEqual(second.get('/api/v1/auth/me').status_code,401)
        self.assertEqual(fx.client.get('/api/v1/auth/me').status_code,200);self.assertEqual(other.get('/api/v1/auth/me').status_code,200)

    def test_experiment_product_routes_removed_and_historical_jobs_not_dispatched(self):
        fx=self.fx;admin=fx.app.test_client();fx.login(admin,'Record_Admin')
        for path in ('/admin/experiments','/api/v1/admin/experiments','/api/v1/admin/experiment-options'):
            self.assertEqual(admin.get(path).status_code,404,path)
        self.assertNotIn('experiment_manager',fx.app.extensions)
        self.assertNotIn('算法实验',admin.get('/admin/users').get_data(as_text=True))
        eid,jid=s.new_id(),s.new_id()
        with fx.db.transaction() as c:
            c.execute(s.experiments.insert().values(id=eid,owner_id=fx.user,owner_auth_session_id=fx.auth,config_json='{}',truth_status='historical',status='completed'))
            c.execute(s.jobs.insert().values(id=jid,owner_id=fx.user,kind='experiment',experiment_id=eid,status='completed'))
        self.assertEqual(fx.client.get('/api/v1/jobs/'+jid).status_code,404)
        self.assertEqual(fx.post(fx.client,'/api/v1/jobs/'+jid+'/cancel',{'expected_version':0}).status_code,404)
        with fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.experiments)),1)

    def legacy_two(self):
        self.fx.db.dispose()
        with closing(sqlite3.connect(str(self.fx.db.path))) as c,c:
            c.execute('DROP INDEX IF EXISTS ix_receipts_resource_saved')
            for table in reversed(s.metadata.sorted_tables):
                if s.TABLE_VERSIONS.get(table.name,1)>2:c.execute('DROP TABLE "'+table.name+'"')
            c.execute('UPDATE app_meta SET schema_version=2')

    def test_schema_two_profiles_migrate_once_preserving_password_and_records(self):
        fx=self.fx;sid=fx.running();fx.store.append_event(fx.user,sid,fx.event());self.legacy_two()
        database=Database(fx.db.path)
        try:
            self.assertEqual(database.initialize(allow_round5_migration=True)['schema_version'],s.SCHEMA_VERSION)
            with database.transaction(False) as c:
                first=dict(c.execute(select(s.user_profiles).where(s.user_profiles.c.user_id==fx.user)).mappings().one())
            database.initialize(allow_round5_migration=True)
            with database.transaction(False) as c:self.assertEqual(first,dict(c.execute(select(s.user_profiles).where(s.user_profiles.c.user_id==fx.user)).mappings().one()))
            self.assertEqual(fx.store.snapshot(fx.user,sid)['total_count'],1)
            fx.login(fx.client,'Record_User');self.assertEqual(self.profile()['nickname'],first['nickname'])
        finally:database.dispose()

    def test_schema_two_migration_failure_rolls_back_without_clearing_data(self):
        fx=self.fx;self.legacy_two();database=Database(fx.db.path);original=s.user_profiles.create
        def fail(c):original(c);raise RuntimeError('D-FAULT profile upgrade')
        try:
            with patch.object(s.user_profiles,'create',side_effect=fail):
                with self.assertRaises(StorageError):database.initialize(allow_round5_migration=True)
            with closing(sqlite3.connect(str(fx.db.path))) as c:
                self.assertEqual(c.execute('SELECT schema_version FROM app_meta').fetchone()[0],2)
                self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE name='user_profiles'").fetchone()[0],0)
                self.assertEqual(c.execute('SELECT count(*) FROM users').fetchone()[0],3)
        finally:database.dispose()


if __name__=='__main__':unittest.main()
