from pathlib import Path
import tempfile
import unittest
from sqlalchemy import select,func
from fitness_app import create_app
from fitness_app.auth import initialize_admin
from fitness_app import db_schema as s
from fitness_app.paths import ROOT


class PageTests(unittest.TestCase):
    def setUp(self):
        self.base=ROOT/'.tmp/tests/pages';self.base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=self.base)
        self.app=create_app({'TESTING':True,'DATABASE_PATH':str(Path(self.tmp.name)/'app.sqlite3'),'SECRET_KEY':'test-only'})
        self.db=self.app.extensions['database'];initialize_admin(self.db,'Test_Admin','test-password-123')
        self.client=self.app.test_client()

    def tearDown(self):
        self.db.dispose();self.tmp.cleanup()
        if self.base.exists() and not any(self.base.iterdir()):self.base.rmdir()

    def login(self):
        token=self.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        return self.client.post('/api/v1/auth/login',json={'username':'Test_Admin','password':'test-password-123'},headers={'X-CSRFToken':token})

    def test_public_pages_and_private_redirect(self):
        for path in ('/','/login','/register'):
            response=self.client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertEqual(self.client.get('/actions').status_code,302)
        self.assertIn('/login?returnTo=',self.client.get('/records').location)

    def test_catalogue_and_real_navigation(self):
        self.login();payload=self.client.get('/api/v1/actions').json['data']['actions']
        self.assertEqual(len(payload),12);self.assertEqual(len({item['action_id'] for item in payload}),12)
        self.assertEqual({item['action_id'] for item in payload if item['counting_available']},{'bodyweight_squat','standard_push_up','standing_dumbbell_curl','conventional_deadlift'})
        self.assertEqual({item['equipment'] for item in payload},{'徒手','哑铃','杠铃'})
        page=self.client.get('/actions');self.assertEqual(page.status_code,200)
        self.assertIn('下一步：设置分析',page.get_data(as_text=True))
        item=self.client.get('/prepare?action=standing_dumbbell_curl')
        self.assertEqual(item.status_code,200);self.assertIn('站姿哑铃弯举',item.get_data(as_text=True))
        with self.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.analysis_sessions)),0)

    def test_forced_page_guard_and_template_escaping(self):
        response=self.client.get('/login?username=%22%3E%3Cscript%3Ebad%3C/script%3E&returnTo=https://other.example')
        html=response.get_data(as_text=True)
        self.assertNotIn('<script>bad',html);self.assertIn('data-return-to="/"',html)
        self.login()
        with self.db.transaction() as c:
            c.execute(s.users.update().where(s.users.c.username_key=='test_admin').values(must_change_password=1))
        response=self.client.get('/records')
        self.assertEqual(response.status_code,302);self.assertIn('/account/password',response.location)


if __name__=='__main__':unittest.main()
