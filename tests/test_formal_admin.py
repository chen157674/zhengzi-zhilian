from pathlib import Path
import json
import tempfile
import unittest
from flask import request
from sqlalchemy import select,update,func
from werkzeug.security import check_password_hash

from fitness_app import create_app
from fitness_app.catalogue import get_action
from fitness_app.auth import initialize_admin,password_hash
from fitness_app.paths import ROOT
from fitness_app import db_schema as s

PASSWORD='admin-test-password-123'


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.base=ROOT/'.tmp/tests/admin';self.base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=self.base)
        self.app=create_app({'TESTING':True,'DATABASE_PATH':str(Path(self.tmp.name)/'app.sqlite3'),'SECRET_KEY':'test-only'})
        self.db=self.app.extensions['database'];self.admin=initialize_admin(self.db,'Main_Admin',PASSWORD)
        self.target,self.other,self.second=[s.new_id() for _ in range(3)]
        hashed=password_hash(PASSWORD)
        with self.db.transaction() as c:
            c.execute(s.users.insert(),[
                {'id':self.target,'username_display':'Target_User','username_key':'target_user','password_hash':hashed,'role':'user'},
                {'id':self.other,'username_display':'Other_User','username_key':'other_user','password_hash':hashed,'role':'user'},
                {'id':self.second,'username_display':'Second_Admin','username_key':'second_admin','password_hash':hashed,'role':'admin'}])
        @self.app.before_request
        def test_race():
            if self.app.config.get('TEST_ROLE_RACE') and request.path.endswith('/disable'):
                self.app.config['TEST_ROLE_RACE']=False
                with self.db.transaction() as c:c.execute(update(s.users).where(s.users.c.id==self.admin['id']).values(role='user'))
        @self.app.after_request
        def test_response_drop(response):
            if self.app.config.get('TEST_DROP_RESET') and request.path.endswith('/password-reset') and response.status_code==200:
                self.app.config['TEST_DROP_RESET']=False
                response.set_data(json.dumps({'error':{'code':'INTERNAL_ERROR','message':'test response lost','fields':{},'retryable':False}}));response.status_code=503
            return response
        self.client=self.app.test_client();self.login(self.client,'Main_Admin')

    def tearDown(self):
        self.db.dispose();self.tmp.cleanup()
        if self.base.exists() and not any(self.base.iterdir()):self.base.rmdir()

    def post(self,client,path,body,key=None):
        csrf=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        headers={'X-CSRFToken':csrf}
        if key:headers['Idempotency-Key']=key
        return client.post(path,json=body,headers=headers)

    def login(self,client,username,password=PASSWORD):
        return self.post(client,'/api/v1/auth/login',{'username':username,'password':password})

    def mutate(self,target,operation,version=0,key=None,client=None):
        return self.post(client or self.client,f'/api/v1/admin/users/{target}/{operation}',{'expected_version':version,'confirm':True},key or s.new_id())

    def test_list_filter_pagination_and_private_fields(self):
        data=self.client.get('/api/v1/admin/users?page=999&page_size=2').json['data']
        self.assertEqual(data['total'],4);self.assertEqual(data['page'],2);self.assertEqual(len(data['users']),2)
        result=self.client.get('/api/v1/admin/users?search=Target_').json['data']
        self.assertEqual([u['id'] for u in result['users']],[self.target])
        self.assertEqual(self.client.get('/api/v1/admin/users?search=%25').json['data']['total'],0)
        for field in ('password_hash','temporary_password_hash','token','auth_version'):
            self.assertNotIn(field,json.dumps(result))
        own=next(u for u in self.client.get('/api/v1/admin/users').json['data']['users'] if u['id']==self.admin['id'])
        self.assertFalse(own['can_disable']);self.assertFalse(own['can_reset'])
        self.assertEqual(self.client.get('/api/v1/admin/users?page_size=0').status_code,422)

    def test_disable_replay_and_no_record_deletion(self):
        target_client=self.app.test_client();self.login(target_client,'Target_User')
        with self.db.transaction() as c:
            auth=c.execute(select(s.auth_sessions).where(s.auth_sessions.c.user_id==self.target)).mappings().first()
            record_id=s.new_id()
            c.execute(s.analysis_sessions.insert().values(id=record_id,user_id=self.target,owner_auth_session_id=auth['id'],action_id='bodyweight_squat',
                capability_version=get_action('bodyweight_squat')['capability_version'],source='camera',mode='free',config_json='{}',status='ended_early',started_at_ms=s.now_ms()))
        key=s.new_id();first=self.mutate(self.target,'disable',key=key);self.assertEqual(first.status_code,200)
        self.assertEqual(self.mutate(self.target,'disable',key=key).json['data'],first.json['data'])
        self.assertEqual(target_client.get('/api/v1/auth/me').status_code,401)
        self.assertEqual(self.login(target_client,'Target_User').status_code,401)
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.admin_operations).where(s.admin_operations.c.operation=='disable')),1)
            self.assertIsNotNone(c.scalar(select(s.analysis_sessions.c.id).where(s.analysis_sessions.c.id==record_id)))

    def test_reset_once_hash_receipt_and_old_session(self):
        target_client=self.app.test_client();self.login(target_client,'Target_User')
        key=s.new_id();first=self.mutate(self.target,'password-reset',key=key)
        self.assertEqual(first.status_code,200);data=first.json['data'];temporary=data['temporary_password']
        self.assertTrue(data['credential_available']);self.assertGreaterEqual(len(temporary),12)
        replay=self.mutate(self.target,'password-reset',key=key).json['data']
        self.assertFalse(replay['credential_available']);self.assertNotIn('temporary_password',replay)
        receipt=self.client.get('/api/v1/operations/'+key).json['data'];self.assertEqual(receipt['status'],'succeeded')
        self.assertNotIn(temporary,json.dumps(receipt))
        self.assertEqual(target_client.get('/api/v1/auth/me').status_code,401)
        self.assertEqual(self.login(target_client,'Target_User').status_code,401)
        self.assertTrue(self.login(target_client,'Target_User',temporary).json['data']['must_change_password'])
        with self.db.transaction(False) as c:
            row=c.execute(select(s.users).where(s.users.c.id==self.target)).mappings().one()
            self.assertTrue(check_password_hash(row['temporary_password_hash'],temporary));self.assertNotEqual(row['temporary_password_hash'],temporary)
            self.assertEqual(row['auth_version'],1)
            self.assertEqual(c.scalar(select(func.count()).select_from(s.admin_operations).where(s.admin_operations.c.operation=='reset')),1)

    def test_lost_reset_response_only_metadata_and_explicit_new_command(self):
        self.app.config['TEST_DROP_RESET']=True;key=s.new_id()
        self.assertEqual(self.mutate(self.target,'password-reset',key=key).status_code,503)
        self.assertEqual(self.client.get('/api/v1/operations/'+key).json['data']['status'],'succeeded')
        replay=self.mutate(self.target,'password-reset',key=key)
        self.assertEqual(replay.status_code,200);self.assertFalse(replay.json['data']['credential_available'])
        wrong=self.mutate(self.target,'password-reset',version=1,key=key)
        self.assertEqual(wrong.status_code,409);self.assertEqual(wrong.json['error']['code'],'IDEMPOTENCY_CONFLICT')
        explicit=self.mutate(self.target,'password-reset',version=1,key=s.new_id())
        self.assertEqual(explicit.status_code,200);self.assertTrue(explicit.json['data']['credential_available'])
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(s.users.c.auth_version).where(s.users.c.id==self.target)),2)

    def test_role_version_confirmation_self_and_disabled_boundaries(self):
        ordinary=self.app.test_client();self.login(ordinary,'Other_User')
        self.assertEqual(self.mutate(self.target,'disable',client=ordinary).status_code,403)
        self.assertEqual(self.mutate(self.admin['id'],'disable').status_code,409)
        self.assertEqual(self.mutate(self.admin['id'],'password-reset').status_code,409)
        self.assertEqual(self.mutate(self.target,'disable',version=99).status_code,409)
        self.assertEqual(self.post(self.client,f'/api/v1/admin/users/{self.target}/disable',{'expected_version':0,'confirm':False},s.new_id()).status_code,422)
        self.assertEqual(self.post(self.client,f'/api/v1/admin/users/{self.target}/disable',{'expected_version':True,'confirm':True},s.new_id()).status_code,422)
        self.assertEqual(self.mutate(self.second,'disable').status_code,200)
        self.assertEqual(self.mutate(self.second,'password-reset',version=1).status_code,409)
        own=self.client.get('/api/v1/admin/users?enabled=1').json['data']['users']
        self.assertEqual(len([u for u in own if u['role']=='admin']),1)

    def test_receipt_ownership_and_actor_revocation_race(self):
        key=s.new_id();self.mutate(self.target,'disable',key=key)
        ordinary=self.app.test_client();self.login(ordinary,'Other_User')
        self.assertEqual(ordinary.get('/api/v1/operations/'+key).status_code,404)
        self.app.config['TEST_ROLE_RACE']=True
        self.assertEqual(self.mutate(self.other,'disable').status_code,403)
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(s.users.c.enabled).where(s.users.c.id==self.other)),1)


if __name__=='__main__':unittest.main()
