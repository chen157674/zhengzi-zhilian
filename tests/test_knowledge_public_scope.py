"""独立知识任务：隐私、工具边界、真实检索来源与无匹配零模型。"""
import json,time,unittest
from unittest.mock import patch
from sqlalchemy import select,func
import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.auth import APIError
from fitness_app.knowledge_store import KnowledgeStore

class KnowledgeProvider:
    def __init__(self):self.calls=[];self.forbidden=False
    def tool_completion(self,key,model,messages,tools,**kwargs):
        self.calls.append({'messages':json.loads(json.dumps(messages)),'tools':tools})
        if self.forbidden:
            call={'id':'forbidden','name':'get_training_evidence','arguments':{'record_id':s.new_id(),'expected_version':0}}
            return {'message':{'role':'assistant','content':None,'tool_calls':[]},'calls':[call],'usage':{},'estimated_micro_cny':1}
        content=next(m['content'] for m in messages if '服务器本次实际检索结果' in m.get('content',''))
        rows=json.loads(content.split('：',1)[1]);identifier=rows[0]['id']
        answer={'summary':rows[0]['text'],'knowledge_ids':[identifier],'evidence_ids':[],'focus_evidence_id':None,'question':None}
        return {'message':{'role':'assistant','content':json.dumps(answer,ensure_ascii=False)},'calls':[],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20},'estimated_micro_cny':100}

class KnowledgePublicScopeTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.jobs=self.fx.app.extensions['coach_tasks'];self.provider=KnowledgeProvider()
        store=KnowledgeStore(index_dir='.tmp/nonexistent-knowledge-index');self.fx.app.extensions['knowledge_store']=store
        settings=self.fx.app.extensions['ai_settings'];settings.environment_key=None
        settings.write(self.fx.user,self.fx.auth,{'expected_version':0,'source':'personal','personal_key':'sk-isolated-knowledge-fixture-123456'},s.new_id())
        self.fx.app.extensions['coach_provider_factory']=lambda:self.provider
    def tearDown(self):
        self.jobs.stop()
        if self.jobs.worker:self.jobs.worker.join(2)
        self.fx.tearDown()
    def payload(self):return {'text':'推胸回程怎样控制','mode':'knowledge','profile_usage':'none','knowledge_scope':{'domain':'movement','action_id':'machine_chest_press'},'consent':True,'history_consent':False}
    def wait(self,rid):
        for _ in range(150):
            row=self.jobs.snapshot(self.fx.user,self.fx.auth,rid)
            if row['status'] not in {'queued','running'}:return row
            time.sleep(.01)
        self.fail('task timeout')
    def test_private_profile_not_read_or_injected_and_real_citations_returned(self):
        info=self.fx.app.extensions['training_info']
        info.write(self.fx.user,self.fx.auth,{'expected_version':0,'weight_kg':73.41927},s.new_id())
        rid=s.new_id()
        with patch.object(info,'get_profile',side_effect=AssertionError('公共任务不可读私人档案')):
            self.jobs.create(self.fx.user,self.fx.auth,rid,self.payload());result=self.wait(rid)
        self.assertEqual(result['status'],'ready',result);self.assertEqual(result['tool_calls'],1)
        self.assertTrue(result['result']['sources']);self.assertEqual(result['mode'],'knowledge')
        sent=json.dumps(self.provider.calls,ensure_ascii=False);self.assertNotIn('73.41927',sent)
        self.assertEqual([t['function']['name'] for t in self.provider.calls[0]['tools']],['search_knowledge'])
        info.write(self.fx.user,self.fx.auth,{'expected_version':1,'weight_kg':74},s.new_id())
        self.assertEqual(self.wait(rid)['status'],'ready')
    def test_no_match_does_not_reserve_ledger_or_call_provider(self):
        payload=self.payload();payload['text']='推胸的火星发动机';payload['knowledge_scope']['domain']='nutrition';payload['knowledge_scope'].pop('action_id')
        rid=s.new_id();result=self.jobs.create(self.fx.user,self.fx.auth,rid,payload)
        self.assertEqual(result['status'],'no_match');self.assertEqual(self.provider.calls,[])
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.ai_requests)),0)
        self.assertEqual(self.jobs.cancel(self.fx.user,self.fx.auth,rid)['status'],'cancelled')
    def test_private_context_and_tools_rejected_even_if_model_requests_them(self):
        for extra in [{'profile_usage':'saved'},{'context':{'context_kind':'record','context_id':s.new_id()}},{'history_consent':True},{'inputs':{'weight':70,'weight_unit':'kg'}}]:
            with self.assertRaises(APIError):self.jobs.create(self.fx.user,self.fx.auth,s.new_id(),{**self.payload(),**extra})
        self.provider.forbidden=True;rid=s.new_id();self.jobs.create(self.fx.user,self.fx.auth,rid,self.payload())
        self.assertEqual(self.wait(rid)['status'],'failed')
    def test_nutrition_profile_delete_does_not_invalidate_public_knowledge(self):
        rid=s.new_id();self.jobs.create(self.fx.user,self.fx.auth,rid,self.payload());self.assertEqual(self.wait(rid)['status'],'ready')
        self.jobs.invalidate_owner(self.fx.user,mode='nutrition')
        self.assertEqual(self.wait(rid)['status'],'ready')

    def test_logout_hook_forgets_public_answer_and_retrieval_body(self):
        rid=s.new_id();self.jobs.create(self.fx.user,self.fx.auth,rid,self.payload());self.assertEqual(self.wait(rid)['status'],'ready')
        response=self.fx.post(self.fx.client,'/api/v1/auth/logout',{})
        self.assertEqual(response.status_code,200,response.json)
        self.assertIsNone(self.jobs.tasks[rid]['result']);self.assertEqual(self.jobs.tasks[rid]['knowledge'],{});self.assertEqual(self.jobs.tasks[rid]['messages'],[])

    def test_withdrawn_reference_invalidates_answer_without_returning_old_body(self):
        rid=s.new_id();self.jobs.create(self.fx.user,self.fx.auth,rid,self.payload());result=self.wait(rid)
        self.assertEqual(result['status'],'ready',result)
        identifier=result['result']['sources'][0]['chunk_id'];self.fx.app.extensions['knowledge_store'].by_id[identifier]['status']='withdrawn'
        changed=self.wait(rid);self.assertEqual(changed['status'],'source_invalid');self.assertIsNone(changed['result'])

if __name__=='__main__':unittest.main()
