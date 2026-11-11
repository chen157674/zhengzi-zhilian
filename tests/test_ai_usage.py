"""AI额度并发预留、幂等、取消/费用不确定与重启恢复；不调用真实供应商。"""
from concurrent.futures import ThreadPoolExecutor
import unittest
from sqlalchemy import select
from fitness_app import db_schema as s
from fitness_app.auth import APIError
from fitness_app.database import ConflictError
from fitness_app.ai_usage import AIUsageStore
import test_formal_records as shared

KEY='sk-accounting-only-fixture-12345678'
MESSAGES=[{'role':'system','content':'返回JSON对象'},{'role':'user','content':'解释记录中已有提示'}]


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.settings=self.fx.app.extensions['ai_settings']
        self.settings.environment_key=None;self.store=AIUsageStore(self.settings)
        admin=self.fx.app.test_client();self.fx.login(admin,'Record_Admin')
        with admin.session_transaction() as state:self.admin_auth=state['_user_id']
        with self.fx.db.transaction(False) as c:self.admin_uid=c.scalar(select(s.auth_sessions.c.user_id).where(s.auth_sessions.c.id==self.admin_auth))
        self.other=self.fx.app.test_client();self.fx.login(self.other,'other_user')
        with self.other.session_transaction() as state:self.other_auth=state['_user_id']
    def tearDown(self):self.fx.tearDown()
    def policy(self,budget='1.00',daily=5):
        with self.fx.db.transaction(False) as c:version=self.settings.policy(c)['version']
        return self.settings.admin_write(self.admin_uid,self.admin_auth,{'expected_version':version,'api_key':KEY,'budget_yuan':budget,
                    'per_user_daily_requests':daily,'enabled':True},s.new_id())
    def reserve(self,uid=None,auth=None,rid=None,digest='a'*64):
        return self.store.reserve(uid or self.fx.user,auth or self.fx.auth,rid or s.new_id(),digest,'review',{'scope':'test'},MESSAGES,0)
    def current_policy(self):
        with self.fx.db.transaction(False) as c:return self.settings.policy(c)

    def test_request_id_is_idempotent_and_settlement_cannot_double_charge(self):
        self.policy();rid=s.new_id();row,created=self.reserve(rid=rid);self.assertTrue(created)
        before=self.current_policy()['reserved_micro_cny'];self.assertGreater(before,0)
        _,created=self.reserve(rid=rid);self.assertFalse(created);self.assertEqual(self.current_policy()['reserved_micro_cny'],before)
        with self.assertRaises(ConflictError):self.reserve(rid=rid,digest='b'*64)
        work=self.store.begin(self.fx.user,rid);self.assertEqual(work['key'],KEY)
        response=self.store.finish(self.fx.user,rid,{'summary':'协议替身'},usage={'prompt_tokens':10,'completion_tokens':4},cost=52)
        self.assertEqual(response['status'],'succeeded');self.assertEqual(self.current_policy()['reserved_micro_cny'],0)
        self.store.finish(self.fx.user,rid,{'summary':'重复'},cost=52)
        self.assertEqual(self.current_policy()['spent_micro_cny'],52)
        with self.assertRaises(APIError) as got:self.store.get(self.fx.other,self.other_auth,rid)
        self.assertEqual(got.exception.status,404)

    def test_two_users_cannot_overspend_same_system_budget(self):
        self.policy('.02')
        def submit(uid,auth):
            try:return self.reserve(uid,auth)[0]
            except APIError as error:return error.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda args:submit(*args),[(self.fx.user,self.fx.auth),(self.fx.other,self.other_auth)]))
        self.assertEqual(sum(isinstance(r,dict) for r in results),1,results)
        self.assertIn('AI_SYSTEM_BUDGET_EXHAUSTED',results)
        policy=self.current_policy();self.assertLessEqual(policy['reserved_micro_cny']+policy['spent_micro_cny'],policy['budget_micro_cny'])

    def test_queued_cancel_releases_budget_but_running_cancel_accounts_usage(self):
        self.policy();row,_=self.reserve();rid=row['request_id']
        self.store.cancel(self.fx.user,self.fx.auth,rid,row['state_version']);self.assertEqual(self.current_policy()['reserved_micro_cny'],0)
        self.assertIsNone(self.store.begin(self.fx.user,rid))
        row,_=self.reserve();rid=row['request_id'];self.store.begin(self.fx.user,rid)
        now=self.store.get(self.fx.user,self.fx.auth,rid)
        cancelled=self.store.cancel(self.fx.user,self.fx.auth,rid,now['state_version']);self.assertEqual(cancelled['status'],'cancelling')
        response=self.store.finish(self.fx.user,rid,{'summary':'不能显示'},usage={'prompt_tokens':1,'completion_tokens':1},cost=10)
        self.assertEqual(response['status'],'cancelled');self.assertIsNone(response['result']);self.assertEqual(self.current_policy()['spent_micro_cny'],10)

    def test_unknown_upstream_charge_keeps_reservation_until_admin_reconciliation(self):
        self.policy();row,_=self.reserve();rid=row['request_id'];self.store.begin(self.fx.user,rid)
        response=self.store.finish(self.fx.user,rid,error='AI_NETWORK_UNCERTAIN',uncertain=True)
        self.assertEqual(response['status'],'uncertain');self.assertIsNone(response['estimated_micro_cny'])
        self.assertGreater(self.current_policy()['reserved_micro_cny'],0)
        with self.assertRaises(APIError):self.store.reconcile(self.fx.user,self.fx.auth,rid,response['state_version'],0,'本人不是管理员')
        self.store.reconcile(self.admin_uid,self.admin_auth,rid,response['state_version'],12,'核对上游记录后的管理员估算')
        self.assertEqual(self.current_policy()['reserved_micro_cny'],0);self.assertEqual(self.current_policy()['spent_micro_cny'],12)
        with self.assertRaises(ConflictError):self.store.reconcile(self.admin_uid,self.admin_auth,rid,response['state_version'],0,'不能重复结算')

    def test_daily_quota_counts_sent_requests_and_settings_changes_stop_before_network(self):
        self.policy(daily=1);row,_=self.reserve();rid=row['request_id']
        self.store.begin(self.fx.user,rid);self.store.finish(self.fx.user,rid,error='AI_KEY_INVALID')
        with self.assertRaises(APIError) as got:self.reserve()
        self.assertEqual(got.exception.code,'AI_DAILY_QUOTA_EXHAUSTED')
        row,_=self.reserve(self.fx.other,self.other_auth);rid=row['request_id']
        self.settings.write(self.fx.other,self.other_auth,{'expected_version':0,'source':'personal'},s.new_id())
        with self.assertRaises(APIError):self.store.begin(self.fx.other,rid)
        self.store.finish(self.fx.other,rid,error='AI_CONTEXT_CHANGED')
        self.assertEqual(self.current_policy()['reserved_micro_cny'],0)

    def test_restart_does_not_resend_running_requests_or_keep_unused_queued_budget(self):
        self.policy();queued,_=self.reserve();running,_=self.reserve(self.fx.other,self.other_auth)
        self.store.begin(self.fx.other,running['request_id']);self.store.recover()
        self.assertEqual(self.store.get(self.fx.user,self.fx.auth,queued['request_id'])['status'],'cancelled')
        row=self.store.get(self.fx.other,self.other_auth,running['request_id']);self.assertEqual(row['status'],'uncertain')
        self.assertEqual(self.current_policy()['reserved_micro_cny'],row['reserved_micro_cny'])

    def test_unlimited_policy_preserves_accounting_and_coach_resume(self):
        self.policy('0',daily=0)
        for _ in range(2):
            row,_=self.reserve();rid=row['request_id']
            self.store.begin(self.fx.user,rid)
            self.store.finish(self.fx.user,rid,{'summary':'测试'},cost=300000)
        self.assertEqual(self.current_policy()['spent_micro_cny'],600000)
        self.assertEqual(self.current_policy()['reserved_micro_cny'],0)
        with self.fx.db.transaction(False) as c:
            snapshot=self.settings.snapshot(c,self.fx.user)
        self.assertTrue(snapshot['available']);self.assertIsNone(snapshot['system_daily_remaining'])
        rid=s.new_id()
        self.store.reserve(self.fx.user,self.fx.auth,rid,'c'*64,'coach',{'scope':'test'},MESSAGES,0,coach=True)
        self.store.begin(self.fx.user,rid)
        self.store.finish(self.fx.user,rid,{'summary':'测试'},cost=10)
        resumed=self.store.resume_coach(self.fx.user,self.fx.auth,rid)
        self.assertEqual(resumed['status'],'queued')
        self.store.cancel(self.fx.user,self.fx.auth,rid,resumed['state_version'])
        self.assertEqual(self.current_policy()['reserved_micro_cny'],0)
        self.assertEqual(self.current_policy()['spent_micro_cny'],600010)

    def test_free_chat_content_is_not_persisted_as_permanent_conversation(self):
        self.policy();rid=s.new_id()
        self.store.reserve(self.fx.user,self.fx.auth,rid,'a'*64,'chat',{'scope':'test'},MESSAGES,0)
        self.store.begin(self.fx.user,rid)
        data=self.store.finish(self.fx.user,rid,{'summary':'本次聊天正文仅驻留本次会话'},usage={'prompt_tokens':1,'completion_tokens':1},cost=10)
        self.assertIsNone(data['result']);self.assertEqual(data['result_retention'],'current_conversation_memory')
        with self.fx.db.transaction(False) as c:self.assertIsNone(c.scalar(select(s.ai_requests.c.result_json).where(s.ai_requests.c.id==rid)))


if __name__=='__main__':unittest.main()
