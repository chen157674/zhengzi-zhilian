"""有界AI计划：显式资料、合法目标/来源、临时草稿与人工保存。"""
import json,time,unittest
from sqlalchemy import select,func
import test_formal_records as shared
from test_training_plans import draft
from fitness_app import db_schema as s
from fitness_app.knowledge_store import KnowledgeStore
from fitness_app.auth import APIError
from unittest.mock import patch

class PlanProvider:
    def __init__(self):self.calls=[];self.invalid=False
    def tool_completion(self,key,model,messages,tools,**kwargs):
        self.calls.append(messages);data=json.loads(messages[-1]['content']);source=next(x for x in data['sources'] if x['action_id']=='bodyweight_squat')
        content={'title':'AI测试草稿','weekly_sessions':[{'weekday':day,'title':'徒手训练','estimated_minutes':30,'items':[{'action_id':'bodyweight_squat','target_sets':2,'target_reps':10,'target_duration_ms':None,'rest_seconds':60}]} for day in data['conditions']['constraints']['training_weekdays']],'knowledge_ids':[source['id']]}
        if self.invalid:content['weekly_sessions'][0]['items'][0]['action_id']='forged_action'
        return {'message':{'role':'assistant','content':json.dumps(content)},'calls':[],'usage':{'prompt_tokens':20,'completion_tokens':20,'total_tokens':40},'estimated_micro_cny':100}
class TrainingPlanJobTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.jobs=self.fx.app.extensions['training_jobs'];self.provider=PlanProvider();self.fx.app.extensions['training_provider_factory']=lambda:self.provider
        self.fx.app.extensions['knowledge_store']=KnowledgeStore(index_dir='.tmp/nonexistent-knowledge-index')
        settings=self.fx.app.extensions['ai_settings'];settings.environment_key=None;settings.write(self.fx.user,self.fx.auth,{'expected_version':0,'source':'personal','personal_key':'sk-isolated-training-plan-fixture-12345'},s.new_id())
        d=draft(self.fx.app.extensions['training_plans']);self.data={k:d[k] for k in ('goal','start_date','weeks','constraints')};self.data.update(use_profile=False,consent=True,adult_confirmed=True)
    def tearDown(self):self.jobs.stop();self.fx.tearDown()
    def wait(self,jid):
        for _ in range(200):
            value=self.jobs.get(self.fx.user,self.fx.auth,jid,'plan')
            if value['status'] not in {'queued','running'}:return value
            time.sleep(.01)
        self.fail('timeout')
    def test_ready_is_only_ephemeral_then_save_and_activate_are_separate(self):
        jid=s.new_id();self.jobs.create(self.fx.user,self.fx.auth,jid,self.data,'plan');result=self.wait(jid)
        self.assertEqual(result['status'],'ready',result)
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.training_plans)),0)
        saved=self.jobs.save_plan(self.fx.user,self.fx.auth,{'job_id':jid,'result_version':1,'edited_draft':result['validated_draft']},s.new_id())
        self.assertEqual(saved['status'],'draft');self.assertEqual(saved['revision']['source'],'ai_draft');self.assertIsNone(self.fx.app.extensions['training_plans'].current(self.fx.user,self.fx.auth)['plan'])
    def test_invalid_action_and_missing_adult_scope_rejected(self):
        bad={**self.data,'adult_confirmed':False}
        with self.assertRaises(APIError):self.jobs.create(self.fx.user,self.fx.auth,s.new_id(),bad,'plan')
        self.provider.invalid=True;jid=s.new_id();self.jobs.create(self.fx.user,self.fx.auth,jid,self.data,'plan');self.assertEqual(self.wait(jid)['status'],'failed')
    def test_cancel_late_provider_and_expiry_do_not_publish_result(self):
        import threading
        entered=threading.Event();release=threading.Event();original=self.provider.tool_completion
        def blocked(*args,**kwargs):entered.set();release.wait(5);return original(*args,**kwargs)
        self.provider.tool_completion=blocked;jid=s.new_id();self.jobs.create(self.fx.user,self.fx.auth,jid,self.data,'plan');self.assertTrue(entered.wait(3))
        self.jobs.cancel(self.fx.user,self.fx.auth,jid,'plan');release.set()
        worker=self.jobs.jobs[jid]['thread'];worker.join(5);self.assertFalse(worker.is_alive())
        self.assertEqual(self.jobs.get(self.fx.user,self.fx.auth,jid,'plan')['status'],'cancelled');self.assertIsNone(self.jobs.get(self.fx.user,self.fx.auth,jid,'plan')['result'])
        with self.fx.db.transaction(False) as c:
            row=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==jid)).mappings().one()
            self.assertEqual(row['status'],'cancelled');self.assertEqual(row['estimated_micro_cny'],100);self.assertIsNone(row['result_json'])
        self.jobs.jobs[jid]['expires']=time.monotonic()-1
        with self.assertRaises(APIError):self.jobs.get(self.fx.user,self.fx.auth,jid,'plan')

    def test_private_profile_not_sent_and_explicit_adoption_version_checked(self):
        info=self.fx.app.extensions['training_info'];info.write(self.fx.user,self.fx.auth,{'expected_version':0,'weight_kg':73.41,'height_cm':180.5},s.new_id())
        jid=s.new_id()
        with patch.object(info,'get_profile',side_effect=AssertionError('未采用不可读取')):
            self.jobs.create(self.fx.user,self.fx.auth,jid,self.data,'plan');self.assertEqual(self.wait(jid)['status'],'ready')
        self.assertNotIn('73.41',json.dumps(self.provider.calls,ensure_ascii=False))
        bad={**self.data,'use_profile':True,'profile_expected_version':0}
        with self.assertRaises(Exception):self.jobs.create(self.fx.user,self.fx.auth,s.new_id(),bad,'plan')

    def test_logout_hook_clears_ephemeral_result_and_inputs(self):
        jid=s.new_id();self.jobs.create(self.fx.user,self.fx.auth,jid,self.data,'plan');self.assertEqual(self.wait(jid)['status'],'ready')
        response=self.fx.post(self.fx.client,'/api/v1/auth/logout',{})
        self.assertEqual(response.status_code,200,response.json)
        self.assertIsNone(self.jobs.jobs[jid]['result']);self.assertEqual(self.jobs.jobs[jid]['inputs'],{});self.assertEqual(self.jobs.jobs[jid]['sources'],[])

    def test_queued_expiry_settles_ledger_without_provider_call(self):
        jid=s.new_id()
        with patch('fitness_app.training_jobs.threading.Thread.start'):
            self.jobs.create(self.fx.user,self.fx.auth,jid,self.data,'plan')
        self.jobs.jobs[jid]['expires']=time.monotonic()-1;self.jobs.prune()
        self.assertNotIn(jid,self.jobs.jobs);self.assertEqual(self.provider.calls,[])
        with self.fx.db.transaction(False) as c:
            row=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==jid)).mappings().one()
            self.assertEqual(row['status'],'cancelled');self.assertEqual(row['reserved_micro_cny'],0);self.assertIsNotNone(row['ended_at_ms'])
        self.jobs.workers.clear()

    def test_duplicate_parent_and_source_withdrawal_invalidate_result(self):
        jid=s.new_id();self.jobs.create(self.fx.user,self.fx.auth,jid,self.data,'plan');result=self.wait(jid)
        self.assertEqual(self.jobs.create(self.fx.user,self.fx.auth,jid,self.data,'plan')['id'],jid);self.assertEqual(len(self.provider.calls),1)
        chunk=result['result']['knowledge_sources'][0]['id'];self.fx.app.extensions['knowledge_store'].by_id[chunk]['status']='withdrawn'
        self.assertEqual(self.jobs.get(self.fx.user,self.fx.auth,jid,'plan')['status'],'source_invalid')

if __name__=='__main__':unittest.main()
