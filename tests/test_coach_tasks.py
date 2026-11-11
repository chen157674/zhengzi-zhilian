"""工具任务、账本、澄清、引用与临时资料；供应商协议替身。"""
import json,time,unittest
from sqlalchemy import select,func
import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.coach_contracts import CoachLoop,CoachLimit,validate,SPECS

KEY='sk-coach-isolated-protocol-fixture-123456'
class Provider:
    def __init__(self):self.calls=[];self.question=False
    def tool_completion(self,key,model,messages,tools,max_output_tokens=1200,allow_tools=True):
        self.calls.append(json.loads(json.dumps(messages)));call_id='call-'+str(len(self.calls))
        if messages[-1]['role']=='user':
            call={'id':call_id,'type':'function','function':{'name':'estimate_nutrition','arguments':json.dumps({'input_version':0,'inputs':{'subject':'self','age_years':25,'weight':70,'weight_unit':'kg','exercise_status':'regular','formula_sex':'male','activity_category':'light'}})}}
            message={'role':'assistant','content':None,'tool_calls':[call]};calls=[{'id':call_id,'name':'estimate_nutrition','arguments':json.loads(call['function']['arguments'])}]
        else:
            message={'role':'assistant','content':json.dumps({'summary':'已按本次自述计算参考范围，可展开查看依据。','knowledge_ids':[],'evidence_ids':[],'focus_evidence_id':None,'question':None})};calls=[]
        return {'message':message,'calls':calls,'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120},'estimated_micro_cny':360}

class CoachTaskTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.jobs=self.fx.app.extensions['coach_tasks'];self.provider=Provider()
        self.fx.app.extensions['coach_provider_factory']=lambda:self.provider
        settings=self.fx.app.extensions['ai_settings'];settings.environment_key=None
        settings.write(self.fx.user,self.fx.auth,{'expected_version':0,'source':'personal','personal_key':KEY},s.new_id())
    def tearDown(self):
        self.jobs.stop()
        if self.jobs.worker:self.jobs.worker.join(2)
        self.fx.tearDown()
    def wait(self,rid):
        for _ in range(100):
            row=self.jobs.snapshot(self.fx.user,self.fx.auth,rid)
            if row['status'] not in {'queued','running'}:return row
            time.sleep(.01)
        self.fail('task timeout')
    def test_protocol_uses_real_ids_and_single_ephemeral_ledger(self):
        rid=s.new_id();data={'consent':True,'text':'我25岁，70公斤，男，规律健身，整体轻活动，估算摄入。'}
        self.jobs.create(self.fx.user,self.fx.auth,rid,data);row=self.wait(rid)
        self.assertEqual(row['status'],'ready',row);self.assertEqual(row['model_requests'],2)
        self.assertEqual(row['result']['nutrition']['items'][0]['display_lower'],95)
        self.assertEqual(self.provider.calls[1][-1]['tool_call_id'],'call-1')
        self.assertEqual(self.provider.calls[1][-2]['tool_calls'][0]['id'],'call-1')
        with self.fx.db.transaction(False) as c:
            records=c.execute(select(s.ai_requests)).mappings().all()
        self.assertEqual(len(records),1);self.assertEqual(records[0]['estimated_micro_cny'],720)
        self.assertIsNone(records[0]['result_json']);self.assertNotIn('70',records[0]['context_json'])
        self.assertNotIn(data['text'].encode(),self.fx.db.path.read_bytes())
        self.assertEqual(self.jobs.create(self.fx.user,self.fx.auth,rid,data)['status'],'ready')
        self.assertEqual(len(self.provider.calls),2)
        with self.assertRaises(Exception):self.jobs.snapshot(self.fx.other,self.fx.auth,rid)
    def test_saved_profile_is_read_by_general_coach_and_clear_revokes_context(self):
        self.fx.app.extensions['knowledge_store'].vectors=None
        info=self.fx.app.extensions['training_info']
        info.write(self.fx.user,self.fx.auth,{'expected_version':0,'height_cm':182.5,'weight_kg':78.25,'age_years':27},s.new_id())
        def provider(key,model,messages,tools,**kwargs):
            self.provider.calls.append(json.loads(json.dumps(messages)))
            if messages[-1]['role']=='user':
                args={'query':'深蹲','domain':'movement','action_id':'bodyweight_squat'}
                tool={'id':'profile-kb','type':'function','function':{'name':'search_knowledge','arguments':json.dumps(args)}}
                return {'message':{'role':'assistant','content':None,'tool_calls':[tool]},
                        'calls':[{'id':'profile-kb','name':'search_knowledge','arguments':args}],
                        'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120},'estimated_micro_cny':360}
            matches=json.loads(messages[-1]['content'])['matches']
            content=json.dumps({'summary':'可以结合你的健身档案，从动作质量和规律训练开始。','knowledge_ids':[matches[0]['id']],'evidence_ids':[],'focus_evidence_id':None,'question':None})
            return {'message':{'role':'assistant','content':content},'calls':[],
                    'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120},'estimated_micro_cny':360}
        self.provider.tool_completion=provider
        rid=s.new_id();self.jobs.create(self.fx.user,self.fx.auth,rid,{'consent':True,'text':'结合我的健身档案，训练先注意什么？'})
        self.assertEqual(self.wait(rid)['status'],'ready')
        sent=json.dumps(self.provider.calls,ensure_ascii=False)
        self.assertIn('182.5',sent);self.assertIn('78.25',sent)
        with self.fx.db.transaction(False) as c:
            ledger=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==rid)).mappings().one()
            self.assertNotIn('78.25',ledger['context_json'])
        info.write(self.fx.user,self.fx.auth,{'expected_version':1},s.new_id(),clear=True)
        self.assertEqual(self.jobs.snapshot(self.fx.user,self.fx.auth,rid)['status'],'source_invalid')
        self.assertEqual(self.jobs.tasks[rid]['messages'],[])

    def test_method_sources_cannot_become_training_evidence_or_model_numbers(self):
        from fitness_app.nutrition_estimates import estimate
        value={'nutrition':estimate({'adult':True,'weight':70,'weight_unit':'kg','exercise_status':'regular'}),
               'knowledge':{},'evidence':{},'asked':False,'turns':2,'uid':self.fx.user,'auth':self.fx.auth}
        for item in self.fx.app.extensions['knowledge_store'].rows:
            if item['source_id']=='S09':value['knowledge'][item['id']]=item
        content={'summary':'建议吃9999克，错误数字不可发布。','knowledge_ids':['S09'],'evidence_ids':['S09','S10'],'focus_evidence_id':None,'question':None}
        result=self.jobs._validate_result(value,json.dumps(content))
        self.assertNotIn('9999',result['summary']);self.assertEqual(result['evidence'],[])
        self.assertEqual(result['nutrition']['items'][0]['display_upper'],140)
        content['evidence_ids']=['e:forged']
        with self.assertRaises(Exception):self.jobs._validate_result(value,json.dumps(content))

    def test_consent_required_and_cancel_forgets_body(self):
        with self.assertRaises(Exception):self.jobs.create(self.fx.user,self.fx.auth,s.new_id(),{'text':'估算蛋白质'})
        rid=s.new_id();self.jobs.create(self.fx.user,self.fx.auth,rid,{'text':'我25岁，70公斤健身，算蛋白质','consent':True});self.wait(rid)
        self.jobs.cancel(self.fx.user,self.fx.auth,rid)
        self.assertIsNone(self.jobs.tasks[rid]['result']);self.assertEqual(self.jobs.tasks[rid]['inputs'],{})
    def test_one_question_resumes_same_parent_and_preserves_total_cost(self):
        original=self.provider.tool_completion
        count=[0]
        def provider(*args,**kwargs):
            count[0]+=1
            if count[0]==1:
                content={'summary':'需要确认本次体重单位。','knowledge_ids':[],'evidence_ids':[],'focus_evidence_id':None,'question':'你的体重单位是公斤吗？'}
                return {'message':{'role':'assistant','content':json.dumps(content)},'calls':[],
                        'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120},'estimated_micro_cny':360}
            return original(*args,**kwargs)
        self.provider.tool_completion=provider;rid=s.new_id()
        self.jobs.create(self.fx.user,self.fx.auth,rid,{'text':'我25岁，70公斤，男，规律健身，整体轻活动，算蛋白质','consent':True})
        first=self.wait(rid);self.assertEqual(first['status'],'awaiting_answer',first)
        self.jobs.answer(self.fx.user,self.fx.auth,rid,{'answer':'是公斤'});second=self.wait(rid)
        self.assertEqual(second['status'],'ready',second);self.assertEqual(second['model_requests'],3)
        with self.fx.db.transaction(False) as c:rows=c.execute(select(s.ai_requests)).mappings().all()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['estimated_micro_cny'],1080)
        self.assertEqual(json.loads(rows[0]['usage_json'])['total_tokens'],360)
        with self.assertRaises(Exception):self.jobs.answer(self.fx.user,self.fx.auth,rid,{'answer':'再问一次'})

    def test_full_clarification_queue_keeps_original_answerable_state(self):
        from unittest.mock import patch
        from fitness_app.auth import APIError
        rid=s.new_id()
        def clarify(*args,**kwargs):
            answer={'summary':'需要确认一个条件','knowledge_ids':[],'evidence_ids':[],'focus_evidence_id':None,'question':'你已成年吗？'}
            return {'message':{'role':'assistant','content':json.dumps(answer)},'calls':[],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20},'estimated_micro_cny':1}
        self.provider.tool_completion=clarify
        self.jobs.create(self.fx.user,self.fx.auth,rid,{'text':'我想了解健身的蛋白质参考','consent':True})
        self.assertEqual(self.wait(rid)['status'],'awaiting_answer')
        with patch.object(self.jobs.queue,'full',return_value=True):
            with self.assertRaises(APIError) as caught:self.jobs.answer(self.fx.user,self.fx.auth,rid,{'answer':'成年'})
        self.assertEqual(caught.exception.code,'AI_BUSY');self.assertEqual(self.wait(rid)['status'],'awaiting_answer')
        with self.fx.db.transaction(False) as c:
            row=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==rid)).mappings().one()
            self.assertEqual(row['status'],'succeeded');self.assertEqual(row['reserved_micro_cny'],0)

    def test_loop_limit_returns_explicit_partial_without_unvalidated_answer(self):
        from unittest.mock import patch
        rid=s.new_id()
        with patch('fitness_app.coach_tasks.CoachLoop.run',return_value={'status':'partial','events':[],'model_requests':3,'tool_calls':8,'estimated_micro_cny':0}):
            self.jobs.create(self.fx.user,self.fx.auth,rid,{'text':'深蹲时如何控制动作','consent':True})
            result=self.wait(rid)
        self.assertEqual(result['status'],'partial');self.assertIsNone(result['result'])
        self.assertEqual(self.jobs.tasks[rid]['messages'],[]);self.assertEqual(self.jobs.tasks[rid]['inputs'],{})
        with self.fx.db.transaction(False) as c:
            row=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==rid)).mappings().one()
            self.assertIn(row['status'],{'completed','succeeded'});self.assertIsNone(row['result_json'])

    def test_profile_explicit_owner_version_and_delete(self):
        store=self.fx.app.extensions['nutrition_profiles'];inputs={'subject':'self','age_years':25,'weight':'73.41927','weight_unit':'kg'}
        self.assertEqual(store.get(self.fx.user,self.fx.auth)['inputs'],{})
        saved=store.write(self.fx.user,self.fx.auth,{'expected_version':0,'inputs':inputs})
        self.assertEqual(saved['version'],1);self.assertEqual(store.write(self.fx.user,self.fx.auth,{'expected_version':0,'inputs':inputs})['version'],1)
        other=self.fx.app.test_client();self.fx.login(other,'Other_User')
        with other.session_transaction() as state:other_auth=state['_user_id']
        self.assertEqual(store.get(self.fx.other,other_auth)['inputs'],{})
        cleared=store.write(self.fx.user,self.fx.auth,{'expected_version':1},True);self.assertEqual(cleared['inputs'],{})
        self.assertNotIn('weight',json.dumps(cleared))

if __name__=='__main__':unittest.main()
