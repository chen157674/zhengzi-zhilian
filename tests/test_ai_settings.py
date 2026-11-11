"""密钥不回传、费用来源不切换、管理员预算与schema6升级；无真实API调用。"""
from contextlib import closing
import json,sqlite3,unittest
from sqlalchemy import select,update
from fitness_app import db_schema as s
from fitness_app.database import StorageError
from fitness_app.deepseek_client import ProviderError
import test_formal_records as shared

KEY='sk-isolated-protocol-fixture-12345678'


class StubProvider:
    def __init__(self):self.calls=[];self.error=None
    def test(self,key):
        self.calls.append(key)
        if self.error:raise self.error
        return {'connection_verified':True,'generation_verified':False,'available_models':['deepseek-flash']}


class AISettingsTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.store=self.fx.app.extensions['ai_settings']
        self.store.environment_key=None
        self.provider=StubProvider();self.fx.app.extensions['ai_provider_factory']=lambda:self.provider
    def tearDown(self):self.fx.tearDown()
    def write(self,path,data,method='PATCH',client=None,key=None):
        client=client or self.fx.client;csrf=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        return client.open(path,method=method,json=data,headers={'X-CSRFToken':csrf,'Idempotency-Key':key or s.new_id()})
    def get(self,client=None):return (client or self.fx.client).get('/api/v1/me/ai-settings').json['data']
    def admin(self):
        client=self.fx.app.test_client();self.fx.login(client,'Record_Admin');return client
    def save(self,**values):return self.write('/api/v1/me/ai-settings',{'expected_version':self.get()['version'],**values})

    def test_key_is_encrypted_owner_bound_and_never_returned_or_receipted(self):
        other=self.fx.app.test_client();self.fx.login(other,'other_user')
        r=self.save(personal_key=KEY,source='personal');self.assertEqual(r.status_code,200,r.json)
        self.assertNotIn(KEY,json.dumps(r.json));self.assertEqual(r.json['data']['personal_key_mask'],'••••5678')
        self.assertFalse(self.get(other)['personal_key_configured'])
        with self.fx.db.transaction(False) as c:
            row=dict(c.execute(select(s.ai_settings).where(s.ai_settings.c.user_id==self.fx.user)).mappings().one())
            receipts=list(c.execute(select(s.command_receipts.c.result_json)).scalars())
        self.assertNotIn(KEY,json.dumps(row));self.assertNotIn(KEY,str(receipts));self.assertNotIn(KEY.encode(),self.fx.db.path.read_bytes())
        self.assertEqual(self.store.vault.open('user:'+self.fx.user,row['key_ciphertext']),KEY)
        with self.assertRaises(StorageError):self.store.vault.open('user:'+self.fx.other,row['key_ciphertext'])
        self.assertTrue(self.store.vault.path.is_relative_to(self.fx.db.path.parent))

    def test_save_blank_keeps_key_remove_does_not_fallback_and_retry_is_idempotent(self):
        key=s.new_id();payload={'expected_version':0,'source':'personal','personal_key':KEY}
        r=self.write('/api/v1/me/ai-settings',payload,key=key);self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(self.write('/api/v1/me/ai-settings',payload,key=key).json['data']['version'],1)
        self.assertEqual(self.save(personal_key='',history_reference=False).status_code,200)
        self.assertTrue(self.get()['personal_key_configured']);self.assertFalse(self.get()['history_reference'])
        self.assertEqual(self.provider.calls,[])
        r=self.write('/api/v1/me/ai-key',{'expected_version':2},'DELETE');self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['data']['source'],'personal');self.assertFalse(r.json['data']['available'])
        self.assertEqual(r.json['data']['reason'],'AI_PERSONAL_KEY_MISSING')

    def test_candidate_test_never_saves_and_auth_test_is_not_generation(self):
        self.assertEqual(self.save(personal_key=KEY,source='personal').status_code,200)
        self.provider.error=ProviderError('AI_KEY_INVALID','AI密钥无效')
        r=self.write('/api/v1/me/ai-settings/test',{'source':'personal','personal_key':'sk-invalid-candidate-87654321'},'POST')
        self.assertEqual(r.status_code,422,r.json);self.assertEqual(self.get()['version'],1)
        self.provider.error=None
        r=self.write('/api/v1/me/ai-settings/test',{},'POST');self.assertEqual(r.status_code,200,r.json)
        self.assertFalse(r.json['data']['generation_verified']);self.assertFalse(r.json['data']['candidate_only'])
        self.assertIsNotNone(self.get()['personal_key_verified_at_ms'])
        self.assertEqual(self.provider.calls[-1],KEY)

    def test_bad_input_versions_and_unknown_model_are_rejected(self):
        for values in [{'source':[]},{'source':'random'},{'history_reference':1},{'model':'other-provider'},
                       {'personal_key':'short'},{'personal_key':False},{'personal_key':KEY+'\r\nInjected: leak'}]:
            r=self.save(**values);self.assertEqual(r.status_code,422,r.json)
        self.assertEqual(self.get()['version'],0)
        self.save(source='personal')
        r=self.write('/api/v1/me/ai-settings',{'expected_version':0,'history_reference':False})
        self.assertEqual(r.status_code,409,r.json)
        self.assertEqual(self.get()['source'],'personal');self.assertFalse(self.get()['available'])

    def test_system_policy_needs_admin_key_budget_and_daily_limit(self):
        path='/api/v1/admin/ai-policy';self.assertEqual(self.fx.client.get(path).status_code,403)
        admin=self.admin();initial=admin.get(path).json['data'];self.assertEqual(initial['budget_micro_cny'],0)
        r=self.write(path,{'expected_version':0,'enabled':True,'budget_yuan':'1.00','per_user_daily_requests':5},client=admin)
        self.assertEqual(r.status_code,422,r.json)
        payload={'expected_version':0,'enabled':True,'budget_yuan':'1.25','per_user_daily_requests':5,'api_key':KEY}
        r=self.write(path,payload,client=admin);self.assertEqual(r.status_code,200,r.json)
        self.assertNotIn(KEY,json.dumps(r.json));self.assertEqual(r.json['data']['budget_micro_cny'],1250000)
        mine=self.get();self.assertTrue(mine['system_available']);self.assertEqual(mine['system_daily_remaining'],5)
        self.assertNotIn('spent_micro_cny',mine);self.assertNotIn('system_key_mask',mine)
        self.assertEqual(self.save(source='personal').json['data']['source'],'personal')
        self.assertFalse(self.get()['available'])

    def test_missing_master_key_does_not_overwrite_existing_ciphertext(self):
        self.save(personal_key=KEY,source='personal')
        with self.fx.db.transaction(False) as c:before=c.scalar(select(s.ai_settings.c.key_ciphertext).where(s.ai_settings.c.user_id==self.fx.user))
        self.store.vault.path.unlink()
        r=self.write('/api/v1/me/ai-settings/test',{},'POST');self.assertEqual(r.status_code,503,r.json)
        self.assertEqual(self.provider.calls,[])
        with self.fx.db.transaction(False) as c:self.assertEqual(before,c.scalar(select(s.ai_settings.c.key_ciphertext).where(s.ai_settings.c.user_id==self.fx.user)))
        other=self.fx.app.test_client();self.fx.login(other,'other_user')
        response=self.write('/api/v1/me/ai-settings',{'expected_version':0,'personal_key':KEY,'source':'personal'},client=other)
        self.assertEqual(response.status_code,503,response.json);self.assertFalse(self.store.vault.path.exists())

    def test_connection_tests_are_bounded_and_separate_from_generation(self):
        for _ in range(5):
            r=self.write('/api/v1/me/ai-settings/test',{'source':'personal','personal_key':KEY},'POST')
            self.assertEqual(r.status_code,200,r.json)
        r=self.write('/api/v1/me/ai-settings/test',{'source':'personal','personal_key':KEY},'POST')
        self.assertEqual(r.status_code,429,r.json);self.assertEqual(len(self.provider.calls),5)
        self.fx.now[0]+=60001
        self.assertEqual(self.write('/api/v1/me/ai-settings/test',{'personal_key':KEY},'POST').status_code,200)

    def test_unlimited_policy_is_available_and_can_restore_finite_limits(self):
        admin=self.admin();path='/api/v1/admin/ai-policy'
        result=self.write(path,{'expected_version':0,'enabled':True,'api_key':KEY,
            'budget_yuan':'0','per_user_daily_requests':0},client=admin)
        self.assertEqual(result.status_code,200,result.json)
        self.assertTrue(self.get()['system_available'])
        self.assertIsNone(self.get()['system_daily_remaining'])
        result=self.write(path,{'expected_version':1,'budget_yuan':'1','per_user_daily_requests':2},client=admin)
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(self.get()['system_daily_remaining'],2)

    def test_schema6_upgrade_preserves_training_profile(self):
        self.fx.app.extensions['training_info'].write(self.fx.user,self.fx.auth,{'expected_version':0,'experience':'beginner'},s.new_id())
        self.fx.db.dispose()
        with closing(sqlite3.connect(self.fx.db.path)) as c:
            for table in reversed(s.metadata.sorted_tables):
                if s.TABLE_VERSIONS.get(table.name,1)>6:c.execute('DROP TABLE "'+table.name+'"')
            c.execute('UPDATE app_meta SET schema_version=6');c.commit()
        self.fx.db.initialize(allow_round5_migration=True)
        self.assertEqual(self.fx.db.inspect_identity()['schema_version'],s.SCHEMA_VERSION)
        data=self.fx.app.extensions['training_info'].get_profile(self.fx.user,self.fx.auth)
        self.assertEqual(data['experience'],'beginner');self.assertEqual(data['version'],1)
        self.assertFalse(self.get()['available'])


if __name__=='__main__':unittest.main()
