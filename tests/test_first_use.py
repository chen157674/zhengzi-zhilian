"""首次使用表单、仅本机初始化和已有账户保护。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from unittest.mock import patch
from sqlalchemy import select, func
from fitness_app import create_app, db_schema as s
from fitness_app.paths import ROOT
from werkzeug.security import check_password_hash

PASSWORD = 'isolated-first-use-123'

class FirstUseTests(unittest.TestCase):
    def setUp(self):
        self.base = ROOT / '.tmp/tests/first-use'
        self.base.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=self.base)
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'first-use-test',
                              'DATABASE_PATH': str(Path(self.tmp.name) / 'fresh.sqlite3')})
        self.db = self.app.extensions['database']
        self.client = self.app.test_client()

    def tearDown(self):
        self.db.dispose()
        self.tmp.cleanup()
        if self.base.exists() and not any(self.base.iterdir()): self.base.rmdir()

    def setup_account(self, client=None, **kwargs):
        client = client or self.client
        token = client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        data = {'email': 'review_user', 'password': PASSWORD, 'password_confirmation': PASSWORD}
        data.update(kwargs.pop('data', {}))
        return client.post('/api/v1/auth/setup', json=data,
                           headers={'X-CSRFToken': token}, **kwargs)

    def test_fresh_page_setup_login_and_protected_page(self):
        login_page = self.client.get('/login').get_data(as_text=True)
        self.assertIn('data-kind="login"', login_page)
        self.assertIn('>登录</button>', login_page)
        self.assertIn('href="/register"', login_page)
        self.assertNotIn('开始体验', login_page)
        register_page = self.client.get('/register').get_data(as_text=True)
        self.assertIn('data-kind="register"', register_page)
        self.assertIn('data-first-registration="true"', register_page)
        self.assertIn('>创建账号</button>', register_page)
        self.assertIn('去登录', register_page)
        self.assertNotIn('--init-admin', register_page)
        self.assertEqual(self.client.get('/setup').location, '/register')
        self.assertEqual(self.setup_account().status_code, 201)
        with self.db.transaction(False) as c:
            user = c.execute(select(s.users)).mappings().one()
            self.assertEqual(user['role'], 'admin')
            self.assertNotEqual(user['password_hash'], PASSWORD)
            self.assertTrue(check_password_hash(user['password_hash'], PASSWORD))
        token = self.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        login = self.client.post('/api/v1/auth/login', json={'email': 'review_user', 'password': PASSWORD},
                                 headers={'X-CSRFToken': token})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 200)
        self.assertEqual(self.client.get('/records').status_code, 200)

    def test_csrf_validation_and_remote_access_leave_no_account(self):
        data = {'email': 'review_user', 'password': PASSWORD, 'password_confirmation': PASSWORD}
        self.assertEqual(self.client.post('/api/v1/auth/setup', json=data).status_code, 403)
        self.assertEqual(self.setup_account(data={'password_confirmation': 'different'}).status_code, 422)
        self.assertEqual(self.setup_account(environ_overrides={'REMOTE_ADDR': '192.0.2.5'}).status_code, 403)
        with patch.dict(self.app.config, {'MOBILE_PUBLIC_ORIGIN': 'https://test.invalid'}):
            self.assertEqual(self.setup_account().status_code, 403)
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.users)), 0)

    def test_existing_account_cannot_be_overwritten_and_setup_disappears(self):
        self.assertEqual(self.setup_account().status_code, 201)
        self.assertEqual(self.setup_account(data={'email': 'other_user'}).status_code, 409)
        self.assertEqual(self.client.get('/setup').status_code, 302)
        self.assertIn('data-kind="login"', self.client.get('/login').get_data(as_text=True))
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.users)), 1)
            self.assertEqual(c.scalar(select(s.users.c.username_display)), 'review_user')

    def test_two_first_use_requests_create_only_one_account(self):
        def run(name):
            with self.app.test_client() as client:
                return self.setup_account(client, data={'email': name}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(run, ['first_user', 'second_user']))
        self.assertEqual(sorted(statuses), [201, 409])
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.users)), 1)

if __name__ == '__main__': unittest.main()
