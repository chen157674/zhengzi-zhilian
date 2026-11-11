from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import io
import os

from sqlalchemy import select,update,func
from werkzeug.security import check_password_hash

from fitness_app import create_app
from fitness_app.auth import initialize_admin,recover_admin,password_hash,safe_return_to,AUTH_TTL_MS
from fitness_app.database import ConflictError,StorageError,maintenance_lock
from fitness_app.paths import ROOT,runtime_path
from fitness_app import db_schema as s

PASSWORD="test-only-password-123"
NEW_PASSWORD="new-test-password-456"


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.base=ROOT/'.tmp/tests/auth'
        self.base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=self.base)
        self.now=[s.now_ms()]
        self.app=create_app({'TESTING':True,'DATABASE_PATH':str(Path(self.temp.name)/'app.sqlite3'),
                             'SECRET_KEY':'isolated-auth-test','CLOCK':lambda:self.now[0]})
        self.db=self.app.extensions['database']
        self.admin=initialize_admin(self.db,'Admin_Test',PASSWORD)
        self.client=self.app.test_client()

    def tearDown(self):
        self.db.dispose()
        self.temp.cleanup()
        if self.base.exists() and not any(self.base.iterdir()): self.base.rmdir()

    def post(self,path,data=None,client=None):
        client=client or self.client
        csrf=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        return client.post('/api/v1/auth/'+path,json=data or {},headers={'X-CSRFToken':csrf})

    def login(self,username='Admin_Test',password=PASSWORD,client=None):
        return self.post('login',{'username':username,'password':password},client)

    def register(self,name='User_Test@qq.com',password=PASSWORD):
        return self.post('register',{'email':name,'password':password,'password_confirmation':password})

    def test_registration_validation_normalization_and_hash(self):
        self.assertEqual(self.register().status_code,201)
        self.assertEqual(self.register('user_test@qq.com').status_code,409)
        for name in ('a','x'*255,'bad name','../bad','user@example.com','a@qq.com.evil.com','a@sub.qq.com','a..b@qq.com','a@ｑｑ.com'):
            with self.subTest(name=name): self.assertEqual(self.register(name).status_code,422)
        for password in ('x'*11,'x'*129): self.assertEqual(self.register('newuser@qq.com',password).status_code,422)
        response=self.post('register',{'username':'badrole','password':PASSWORD,'password_confirmation':PASSWORD,'role':'admin'})
        self.assertEqual(response.status_code,400)
        with self.db.transaction(False) as c:
            row=c.execute(select(s.users).where(s.users.c.username_key=='user_test@qq.com')).mappings().one()
            self.assertEqual(row['role'],'user'); self.assertNotEqual(row['password_hash'],PASSWORD)
            self.assertTrue(check_password_hash(row['password_hash'],PASSWORD))
            self.assertEqual(c.scalar(select(func.count()).select_from(s.users)),2)

    def test_common_email_policy_and_long_login(self):
        for domain in ('qq.com', '163.com', 'gmail.com', '126.com', 'outlook.com', 'icloud.com'):
            address='mailbox@'+domain
            with self.subTest(domain=domain):
                self.assertEqual(self.register(address).status_code,201)
                response=self.post('login',{'email':address.upper(),'password':PASSWORD})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json['data']['email'],address)
        long_address='a'*64+'@gmail.com'
        self.assertEqual(self.register('  '+long_address.upper()+'  ').status_code,201)
        self.assertEqual(self.post('login',{'email':long_address,'password':PASSWORD}).status_code,200)
        self.assertEqual(self.register(long_address.upper()).status_code,409)
        self.assertEqual(self.register('a'*65+'@qq.com').status_code,422)
        self.login()
        found=self.client.get('/api/v1/admin/users',query_string={'search':long_address}).json['data']['users']
        self.assertEqual([row['username'] for row in found],[long_address])

    def test_registration_cannot_bypass_email_policy_or_identity(self):
        before=None
        with self.db.transaction(False) as c:
            before=c.scalar(select(func.count()).select_from(s.users))
        for data in ({'username':'legacy_name'}, {'username':'a@unknown.example'},
                     {'email':'a@qq.com.evil.com'}, {'email':'a@qq.com','username':'b@qq.com'}):
            response=self.post('register',{**data,'password':PASSWORD,'password_confirmation':PASSWORD})
            self.assertIn(response.status_code,(400,422))
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.users)),before)
        response=self.post('register',{'username':'compat@qq.com','password':PASSWORD,'password_confirmation':PASSWORD})
        self.assertEqual(response.status_code,201)
        self.assertEqual(self.post('login',{'email':'Admin_Test','password':PASSWORD}).status_code,200)
        self.assertEqual(self.post('login',{'email':'missing@qq.com','username':'Admin_Test','password':PASSWORD}).status_code,400)

    def test_login_csrf_host_role_and_public_fields(self):
        self.assertEqual(self.client.post('/api/v1/auth/login',json={'username':'Admin_Test','password':PASSWORD}).status_code,403)
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code,401)
        self.assertEqual(self.login(password='incorrect').status_code,401)
        response=self.login(username='ADMIN_TEST')
        self.assertEqual(response.status_code,200); self.assertIn('request_id',response.json)
        self.assertNotIn(PASSWORD,response.get_data(as_text=True))
        self.assertNotIn('password',response.json['data']); self.assertNotIn('password_hash',response.json['data'])
        self.assertNotIn('token',response.json['data'])
        self.assertIn('HttpOnly',response.headers['Set-Cookie']); self.assertIn('SameSite=Strict',response.headers['Set-Cookie'])
        self.assertEqual(self.client.get('/api/v1/auth/me').json['data']['role'],'admin')
        self.register(); self.login('User_Test@qq.com')
        self.assertEqual(self.client.get('/api/v1/admin/users').status_code,403)
        self.assertEqual(self.client.get('/api/v1/auth/me',base_url='http://untrusted.example').status_code,400)

    def test_disabled_and_unknown_fail_uniformly(self):
        self.register()
        with self.db.transaction() as c: c.execute(update(s.users).where(s.users.c.username_key=='user_test@qq.com').values(enabled=0))
        disabled=self.login('User_Test@qq.com'); unknown=self.login('missing_user')
        self.assertEqual(disabled.status_code,401); self.assertEqual(unknown.status_code,401)
        self.assertEqual(disabled.json['error'],unknown.json['error'])

    def test_logout_expiry_and_revoke_hook(self):
        called=[]; self.app.extensions['auth_revoke_hooks'].append(lambda **kwargs:called.append(kwargs))
        self.assertEqual(self.login().status_code,200)
        self.assertEqual(self.post('logout').status_code,200)
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code,401)
        self.assertEqual(called[0]['user_id'],self.admin['id']); self.assertIsNotNone(called[0]['auth_session_id'])
        self.assertEqual(self.post('logout').status_code,200)
        self.login(); self.now[0]+=AUTH_TTL_MS+1
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code,401)

    def test_password_change_revokes_all_old_sessions(self):
        another=self.app.test_client(); self.login(); self.login(client=another)
        bad=self.post('password',{'current_password':'incorrect','new_password':NEW_PASSWORD,'password_confirmation':NEW_PASSWORD})
        self.assertEqual(bad.status_code,422)
        changed=self.post('password',{'current_password':PASSWORD,'new_password':NEW_PASSWORD,'password_confirmation':NEW_PASSWORD})
        self.assertEqual(changed.status_code,200)
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code,401)
        self.assertEqual(another.get('/api/v1/auth/me').status_code,401)
        self.assertEqual(self.login(password=PASSWORD).status_code,401)
        self.assertEqual(self.login(password=NEW_PASSWORD).status_code,200)

    def test_temporary_credentials_and_forced_change(self):
        temporary='temporary-test-12345'
        with self.db.transaction() as c:
            c.execute(update(s.users).where(s.users.c.id==self.admin['id']).values(must_change_password=1,
                temporary_password_hash=password_hash(temporary),temporary_expires_at_ms=self.now[0]+1800000,auth_version=1))
        self.assertEqual(self.login().status_code,401)
        self.assertEqual(self.login(password=temporary).status_code,200)
        self.assertTrue(self.client.get('/api/v1/auth/me').json['data']['must_change_password'])
        self.assertEqual(self.client.get('/api/v1/admin/users').json['error']['code'],'PASSWORD_CHANGE_REQUIRED')
        self.assertEqual(self.post('password',{'current_password':temporary,'new_password':NEW_PASSWORD,'password_confirmation':NEW_PASSWORD}).status_code,200)
        self.assertEqual(self.login(password=temporary).status_code,401)
        self.assertEqual(self.login(password=NEW_PASSWORD).status_code,200)
        with self.db.transaction() as c:
            c.execute(update(s.users).where(s.users.c.id==self.admin['id']).values(must_change_password=1,
                temporary_password_hash=password_hash(temporary),temporary_expires_at_ms=self.now[0]-1,auth_version=3))
        self.assertEqual(self.login(password=temporary).status_code,401)
        self.assertEqual(self.login(password=NEW_PASSWORD).status_code,401)

    def test_initialization_recovery_and_maintenance_boundary(self):
        with self.assertRaises(ConflictError): initialize_admin(self.db,'Another_Admin',PASSWORD)
        self.register(); self.login()
        with self.assertRaises(ConflictError): recover_admin(self.db,'User_Test@qq.com',NEW_PASSWORD)
        with maintenance_lock(runtime_path()/'service.lock',exact=True):
            with self.assertRaises(StorageError): recover_admin(self.db,'Admin_Test',NEW_PASSWORD)
        recovered=recover_admin(self.db,'Admin_Test',NEW_PASSWORD)
        self.assertEqual(recovered['id'],self.admin['id'])
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code,401)
        self.assertEqual(self.login(password=NEW_PASSWORD).status_code,200)
        with self.db.transaction() as c:
            c.execute(update(s.users).where(s.users.c.id==self.admin['id']).values(enabled=0))
        with self.assertRaises(ConflictError): recover_admin(self.db,'Admin_Test',PASSWORD)
        with self.db.transaction(False) as c:
            rows=c.execute(select(s.admin_operations)).mappings().all()
        self.assertEqual(len(rows),2)
        self.assertNotIn(PASSWORD,str(rows)); self.assertNotIn(NEW_PASSWORD,str(rows))

    def test_return_paths_and_password_spaces(self):
        for target in ('//other.example','https://other.example','/\\other','/%2fother','javascript:alert(1)','https://['):
            self.assertEqual(safe_return_to(target),'/')
        self.assertEqual(safe_return_to('/records?source=video'),'/records?source=video')
        spaced='  test password  '
        self.assertEqual(self.register('space_user@163.com',spaced).status_code,201)
        self.assertEqual(self.login('space_user@163.com',spaced).status_code,200)
        self.assertEqual(self.login('space_user@163.com',spaced.strip()).status_code,401)

    def test_registration_requires_local_initialization(self):
        app=create_app({'TESTING':True,'DATABASE_PATH':str(Path(self.temp.name)/'fresh.sqlite3'),'SECRET_KEY':'test-only'})
        client=app.test_client()
        try:
            self.assertFalse(client.get('/api/v1/health').json['data']['initialized'])
            token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
            response=client.post('/api/v1/auth/register',json={'username':'early_user@gmail.com','password':PASSWORD,'password_confirmation':PASSWORD},headers={'X-CSRFToken':token})
            self.assertEqual(response.status_code,409)
            self.assertEqual(response.json['error']['code'],'INITIALIZATION_REQUIRED')
            with app.extensions['database'].transaction(False) as c:
                self.assertEqual(c.scalar(select(func.count()).select_from(s.users)),0)
        finally: app.extensions['database'].dispose()

    def test_cli_recovery_hidden_prompt_and_cancel(self):
        from fitness_app.runtime import main
        output=io.StringIO()
        values={'FITNESS_DATABASE_PATH':str(self.db.path),'FITNESS_RUNTIME_DIR':str(Path(self.temp.name)/'runtime')}
        with patch.dict(os.environ,values),patch('sys.argv',['fitness_app','--recover-admin']),patch('builtins.input',side_effect=['Admin_Test','NO']),patch('getpass.getpass',side_effect=[NEW_PASSWORD,NEW_PASSWORD]),patch('sys.stdout',output):
            main()
        self.assertEqual(self.login().status_code,200)
        with patch.dict(os.environ,values),patch('sys.argv',['fitness_app','--recover-admin']),patch('builtins.input',side_effect=['Admin_Test','YES']),patch('getpass.getpass',side_effect=[NEW_PASSWORD,NEW_PASSWORD]),patch('sys.stdout',output):
            main()
        self.assertEqual(self.login(password=NEW_PASSWORD).status_code,200)
        self.assertNotIn(PASSWORD,output.getvalue()); self.assertNotIn(NEW_PASSWORD,output.getvalue())


if __name__=='__main__': unittest.main()
