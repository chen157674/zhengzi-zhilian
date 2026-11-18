"""通过真实HTTP路由与后台任务验证AI闭环；供应商替身不冒充真实付费验证。"""
import json,queue,threading,time,unittest
from unittest.mock import patch
from sqlalchemy import select,update,func
from fitness_app import db_schema as s
from fitness_app.deepseek_client import ProviderError
import test_training_insights as fixtures
import test_ai_context as context_fixtures


class Provider:
    def __init__(self):self.calls=0;self.entered=threading.Event();self.release=threading.Event();self.release.set();self.error=None;self.forged=False
    def generate(self,key,model,messages,max_output_tokens):
        self.calls+=1;self.entered.set()
        if not self.release.wait(4):raise RuntimeError('fixture timeout')
        if self.error:raise self.error
        if getattr(self,'empty',False):return {'result':{'summary':''},'usage':{'prompt_tokens':30,'completion_tokens':10},'estimated_micro_cny':140}
        facts=json.loads(messages[-1]['content'])['facts'];evidence=facts['evidence']
        explanations=[]
        if evidence:explanations=[{'evidence_id':'forged' if self.forged else evidence[0]['id'],
            'explanation':'依据已保存的参考提示核对动作。','adjustment':'先稳定身体，再按提示缓慢调整。'}]
        return {'result':{'summary':'先关注记录中的姿态提示。','explanations':explanations,
            'next_focus':[],'limitations':['缺少可评估证据时无法确认整体动作。']},
            'usage':{'prompt_tokens':30,'completion_tokens':10},'estimated_micro_cny':140}


class AIJobsTests(unittest.TestCase):
    def test_personal_food_statement_is_saved_and_read_on_next_chat(self):
        conversation=self.conversation();sent=[];original=self.provider.generate
        def generate(key,model,messages,max_output_tokens):
            sent.append(json.loads(json.dumps(messages)))
            return original(key,model,messages,max_output_tokens)
        self.provider.generate=generate
        response=self.post('/api/v1/ai/conversations/'+conversation['conversation_id']+'/messages',
            {'text':'我喜欢吃西兰花。今天午餐吃了米饭和鸡蛋。','expected_context_revision':conversation['context_revision'],'consent':True})
        row=self.wait(response.json['data']['request_id']);self.assertEqual(row['status'],'succeeded',row)
        self.assertTrue(row['result']['memory_saved']['saved'])
        store=self.fx.app.extensions['diet_journal'];today=store.today().isoformat()
        records=store.list(self.fx.user,self.fx.auth,today,today)['records']
        self.assertEqual(len(records),1);self.assertEqual({x['name'] for x in records[0]['foods']},{'米饭','鸡蛋'})
        next_chat=self.conversation()
        response=self.post('/api/v1/ai/conversations/'+next_chat['conversation_id']+'/messages',
            {'text':'帮我搭配下一餐','expected_context_revision':next_chat['context_revision'],'consent':True})
        self.assertEqual(self.wait(response.json['data']['request_id'])['status'],'succeeded')
        context=json.dumps(sent[-1],ensure_ascii=False)
        self.assertIn('personal_statements',context);self.assertIn('西兰花',context);self.assertIn('recent_foods',context)

    def test_invalid_form_reply_uses_registered_issue_without_publishing_invalid_answer(self):
        conversation=self.conversation();self.provider.forged=True
        response=self.post('/api/v1/ai/conversations/'+conversation['conversation_id']+'/messages',
            {'text':'动作错误了嘛','expected_context_revision':conversation['context_revision'],'consent':True})
        self.assertEqual(response.status_code,202,response.json)
        result=self.wait(response.json['data']['request_id'])
        self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(result['result']['response_basis'],'open_conversation')
        self.assertEqual(result['result']['explanations'],[])
        self.assertNotIn('forged',json.dumps(result['result']))
        self.assertEqual(self.provider.calls,1)

    def test_general_question_reaches_open_chat_without_topic_rejection(self):
        conversation=self.conversation()
        response=self.post('/api/v1/ai/conversations/'+conversation['conversation_id']+'/messages',
            {'text':'帮我解方程','expected_context_revision':conversation['context_revision'],'consent':True})
        self.assertEqual(response.status_code,202,response.json)
        row=self.wait(response.json['data']['request_id']);self.assertEqual(row['status'],'succeeded',row)
        self.assertEqual(self.provider.calls,1)
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.ai_requests)),1)

    def test_streaming_text_is_scoped_and_invalid_final_result_is_withdrawn(self):
        class StreamingProvider(Provider):
            def generate_stream(inner,key,model,messages,max_output_tokens,on_summary):
                on_summary('先稳定身体，再参考已记录的提示调整。')
                return inner.generate(key,model,messages,max_output_tokens)
        provider=StreamingProvider();provider.release.clear();provider.empty=True
        self.provider=provider
        self.fx.app.extensions['ai_provider_factory']=lambda:provider
        conversation=self.conversation();response=self.send(conversation);rid=response.json['data']['request_id']
        self.assertTrue(provider.entered.wait(2))
        snapshot=self.manager.snapshot(self.fx.user,self.fx.auth,rid)
        self.assertEqual(snapshot['status'],'running');self.assertTrue(snapshot['stream_text'].startswith('先稳定身体'))
        self.assertIsNone(snapshot['result']);self.assertTrue(snapshot['context_current'])
        other=self.fx.app.test_client();self.fx.login(other,'Record_User')
        with other.session_transaction() as session:other_auth=session['_user_id']
        self.assertNotIn('stream_text',self.manager.snapshot(self.fx.user,other_auth,rid))
        provider.release.set();snapshot=self.wait(rid)
        self.assertEqual(snapshot['status'],'failed');self.assertNotIn('stream_text',snapshot);self.assertIsNone(snapshot['result'])

    def setUp(self):
        self.fixture=fixtures.InsightsTests();self.fixture.setUp();self.fx=self.fixture.fx
        self.manager=self.fx.app.extensions['ai_jobs'];self.provider=Provider()
        self.fx.app.extensions['ai_provider_factory']=lambda:self.provider
        self.fx.app.extensions['ai_settings'].write(self.fx.user,self.fx.auth,
            {'expected_version':0,'source':'personal','personal_key':'sk-isolated-job-fixture-12345678'},s.new_id())
        self.sid=self.fixture.create([(0,1000,'issue')])
    def tearDown(self):
        self.provider.release.set();self.manager.stop()
        for worker in self.manager.workers:worker.join(5)
        self.fixture.tearDown()
    def post(self,path,data,key=None):return self.fx.post(self.fx.client,path,data,key)
    def wait(self,rid):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            row=self.fx.client.get('/api/v1/ai/requests/'+rid).json['data']
            if row['status'] in {'succeeded','failed','cancelled','uncertain'}:return row
            time.sleep(.015)
        self.fail('后台AI任务没有结束')
    def conversation(self):
        response=self.post('/api/v1/ai/conversations',{'context_kind':'record','context_id':self.sid})
        self.assertEqual(response.status_code,201,response.json);return response.json['data']
    def send(self,conversation,key=None):
        return self.post('/api/v1/ai/conversations/'+conversation['conversation_id']+'/messages',
            {'text':'这次应该怎么调整？','expected_context_revision':conversation['context_revision'],'consent':True},key)

    def test_queue_failure_does_not_hold_the_last_result_cache_slot(self):
        conversation=self.conversation()
        for _ in range(255):self.manager.answers[s.new_id()]={'uid':self.fx.user,'auth':self.fx.auth,'result':{'summary':'保留的隔离聊天结果'},'touched':time.monotonic()}
        first=s.new_id()
        with patch.object(self.manager.jobs,'put_nowait',side_effect=queue.Full):response=self.send(conversation,first)
        self.assertEqual(response.status_code,202,response.json);self.assertEqual(response.json['data']['status'],'failed');self.assertEqual(self.provider.calls,0)
        second=s.new_id();response=self.send(conversation,second)
        self.assertEqual(response.status_code,202,response.json)
        row=self.wait(second);self.assertEqual(row['status'],'succeeded',row);self.assertIsNotNone(row['result']);self.assertEqual(self.provider.calls,1)
        first_row=self.manager.snapshot(self.fx.user,self.fx.auth,first)
        self.assertEqual(first_row['error_code'],'AI_BUSY');self.assertEqual(first_row['estimated_micro_cny'],0);self.assertFalse(first in self.manager.answers,'队列失败请求不应占用结果缓存')
        self.send(conversation,second);self.assertEqual(self.provider.calls,1)

    def test_saved_report_uses_persistent_body_without_holding_chat_cache_capacity(self):
        response=self.post('/api/v1/ai/reports',{'context_kind':'record','context_id':self.sid,'kind':'review','consent':True})
        self.assertEqual(response.status_code,202,response.json);rid=response.json['data']['request_id'];row=self.wait(rid)
        self.assertEqual(row['status'],'succeeded',row);self.assertFalse(rid in self.manager.answers,'持久复盘不应占用聊天正文缓存')
        with self.fx.db.transaction(False) as c:
            stored=c.execute(select(s.ai_requests.c.result_json,s.ai_requests.c.estimated_micro_cny).where(s.ai_requests.c.id==rid)).mappings().one()
            self.assertIsNotNone(stored['result_json']);self.assertEqual(stored['estimated_micro_cny'],140)
        result=self.fx.client.get('/api/v1/ai/reports/'+rid).json['data'];self.assertIsNotNone(result['result']);self.assertEqual(self.provider.calls,1)

    def test_failed_and_uncertain_cache_is_reclaimed_while_successful_chat_remains(self):
        conversation=self.conversation();failed_ids=[]
        for mode in ('invalid','uncertain'):
            self.provider.empty=mode=='invalid';self.provider.error=ProviderError('AI_NETWORK_UNCERTAIN','隔离超时',uncertain_charge=True) if mode=='uncertain' else None
            response=self.send(conversation);self.assertEqual(response.status_code,202,response.json);rid=response.json['data']['request_id'];row=self.wait(rid);failed_ids.append(rid)
            self.assertEqual(row['status'],'uncertain' if mode=='uncertain' else 'failed');self.assertFalse(rid in self.manager.answers,'无可展示正文的终态不应占用结果缓存')
            self.assertIsNone(row['estimated_micro_cny']) if mode=='uncertain' else self.assertEqual(row['estimated_micro_cny'],140)
        self.provider.error=None;self.provider.forged=False;self.provider.empty=False;response=self.send(conversation);rid=response.json['data']['request_id'];row=self.wait(rid)
        self.assertEqual(row['status'],'succeeded',row);self.assertIn(rid,self.manager.answers);self.assertIsNotNone(row['result'])
        for old in failed_ids:self.assertIn(self.manager.snapshot(self.fx.user,self.fx.auth,old)['status'],{'failed','uncertain'})
        self.assertEqual(self.provider.calls,3)

    def test_queued_cancellation_reclaims_cache_without_provider_or_charge(self):
        conversation=self.conversation();rid=s.new_id()
        with patch.object(self.manager,'_start_workers'):response=self.send(conversation,rid)
        self.assertEqual(response.status_code,202,response.json);self.assertIn(rid,self.manager.answers)
        queued=self.fx.client.get('/api/v1/ai/requests/'+rid).json['data']
        self.assertEqual(queued['status'],'queued');self.assertIn(rid,self.manager.answers)
        response=self.post('/api/v1/ai/requests/'+rid+'/cancel',{'expected_version':queued['state_version']})
        self.assertEqual(response.status_code,202,response.json);row=self.wait(rid)
        self.assertEqual(row['status'],'cancelled');self.assertEqual(row['estimated_micro_cny'],0);self.assertFalse(rid in self.manager.answers)
        self.manager._start_workers();deadline=time.monotonic()+3
        while self.manager.jobs.unfinished_tasks and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(self.manager.jobs.unfinished_tasks,0);self.assertEqual(self.provider.calls,0)

    def test_chat_is_owner_bound_idempotent_and_not_a_permanent_transcript(self):
        conversation=self.conversation();self.assertEqual(self.provider.calls,0)
        key=s.new_id();response=self.send(conversation,key);self.assertEqual(response.status_code,202,response.json)
        row=self.wait(key);self.assertEqual(row['status'],'succeeded',row);self.assertEqual(row['result']['generated_by'],'deepseek')
        response=self.send(conversation,key);self.assertEqual(response.status_code,202,response.json);self.assertEqual(self.provider.calls,1)
        other=self.fx.app.test_client();self.fx.login(other,'other_user')
        self.assertEqual(other.get('/api/v1/ai/requests/'+key).status_code,404)
        self.assertEqual(other.get('/api/v1/ai/conversations/'+conversation['conversation_id']).status_code,404)
        with self.fx.db.transaction(False) as c:
            raw=c.execute(select(s.ai_requests).where(s.ai_requests.c.id==key)).mappings().one()
            self.assertIsNone(raw['result_json']);self.assertNotIn('这次应该',raw['context_json'])
        self.assertEqual(len(self.manager.conversation(self.fx.user,self.fx.auth,conversation['conversation_id'])['messages']),2)
        self.manager.answers.clear();self.assertTrue(self.manager.snapshot(self.fx.user,self.fx.auth,key)['conversation_content_expired'])
        self.send(conversation,key);self.assertEqual(self.provider.calls,1)

    def test_consent_stale_context_and_forged_evidence_cannot_produce_success(self):
        c=self.conversation();url='/api/v1/ai/conversations/'+c['conversation_id']+'/messages'
        self.assertEqual(self.post(url,{'text':'解释','expected_context_revision':c['context_revision']}).status_code,422)
        self.assertEqual(self.provider.calls,0)
        self.fx.app.extensions['training_info'].write(self.fx.user,self.fx.auth,{'experience':'beginner','expected_version':0},s.new_id())
        self.assertEqual(self.send(c).status_code,409);self.assertEqual(self.provider.calls,0)
        self.provider.forged=True;response=self.send(self.conversation());row=self.wait(response.json['data']['request_id'])
        self.assertEqual(row['status'],'succeeded');self.assertEqual(row['result']['explanations'],[])
        self.assertNotIn('forged',json.dumps(row['result']))
        self.assertEqual(row['estimated_micro_cny'],140)

    def test_cancel_running_hides_answer_but_accounts_actual_usage(self):
        self.provider.release.clear();response=self.send(self.conversation());rid=response.json['data']['request_id']
        self.assertTrue(self.provider.entered.wait(3));row=self.fx.client.get('/api/v1/ai/requests/'+rid).json['data']
        r=self.post('/api/v1/ai/requests/'+rid+'/cancel',{'expected_version':row['state_version']});self.assertEqual(r.status_code,202,r.json)
        self.provider.release.set();row=self.wait(rid)
        self.assertEqual(row['status'],'cancelled');self.assertIsNone(row['result']);self.assertEqual(row['estimated_micro_cny'],140)

    def test_record_change_while_generating_hides_stale_result_and_retains_cost(self):
        self.provider.release.clear();r=self.send(self.conversation());rid=r.json['data']['request_id']
        self.assertTrue(self.provider.entered.wait(3))
        with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==self.sid).values(state_version=s.analysis_sessions.c.state_version+1))
        self.provider.release.set();row=self.wait(rid)
        self.assertEqual(row['status'],'failed');self.assertFalse(row['context_current']);self.assertIsNone(row['result']);self.assertEqual(row['estimated_micro_cny'],140)

    def test_report_is_persistent_read_only_and_can_delete_content_without_deleting_usage(self):
        r=self.post('/api/v1/ai/reports',{'context_kind':'record','context_id':self.sid,'kind':'review','consent':True})
        self.assertEqual(r.status_code,202,r.json);rid=r.json['data']['request_id'];row=self.wait(rid)
        self.assertEqual(row['status'],'succeeded',row);self.manager.answers.clear()
        result=self.fx.client.get('/api/v1/ai/reports/'+rid);self.assertIsNotNone(result.json['data']['result'])
        token=self.fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        r=self.fx.client.delete('/api/v1/ai/reports/'+rid,json={'expected_version':row['state_version']},headers={'X-CSRFToken':token})
        self.assertEqual(r.status_code,200,r.json)
        row=self.fx.client.get('/api/v1/ai/reports/'+rid).json['data'];self.assertIsNone(row['result']);self.assertEqual(row['estimated_micro_cny'],140)

    def test_provider_timeout_is_uncertain_and_is_not_resent(self):
        self.provider.error=ProviderError('AI_NETWORK_UNCERTAIN','隔离超时替身',uncertain_charge=True)
        c=self.conversation();rid=s.new_id();self.send(c,rid);row=self.wait(rid)
        self.assertEqual(row['status'],'uncertain');self.assertIsNone(row['result']);self.assertIsNone(row['estimated_micro_cny'])
        self.send(c,rid);self.assertEqual(self.provider.calls,1)

    def test_key_storage_lost_while_queued_does_not_call_provider_or_claim_a_charge(self):
        c=self.conversation();rid=s.new_id()
        with patch.object(self.manager,'_start_workers'):self.assertEqual(self.send(c,rid).status_code,202)
        self.fx.app.extensions['ai_settings'].vault.path.unlink()
        self.manager._start_workers();row=self.wait(rid)
        self.assertEqual(row['status'],'failed');self.assertEqual(row['error_code'],'AI_KEY_STORAGE_UNAVAILABLE')
        self.assertEqual(row['estimated_micro_cny'],0);self.assertEqual(self.provider.calls,0)

    def test_pausing_while_live_answer_is_generating_keeps_frozen_answer_and_marks_old_segment(self):
        pid,sid,snap=context_fixtures.live_snapshot_fixture(self.fx)
        self.provider.release.clear()
        with patch.object(self.fx.app.extensions['preview_manager'],'snapshot',return_value=snap):
            response=self.post('/api/v1/ai/conversations',{'context_kind':'live','context_id':pid,'history_scope':'none'})
            self.assertEqual(response.status_code,201,response.json);conversation=response.json['data']
            reply=self.send(conversation);rid=reply.json['data']['request_id'];self.assertTrue(self.provider.entered.wait(3))
            snap.update(status='released',result=None);self.fx.finish(sid);self.provider.release.set();row=self.wait(rid)
            self.assertEqual(row['status'],'succeeded',row);self.assertTrue(row['context_current']);self.assertIsNotNone(row['result'])
            self.assertFalse(row['live_context']['matches_current_segment']);self.assertTrue(row['live_context']['snapshot_only'])
            self.assertEqual(row['estimated_micro_cny'],140);self.assertEqual(self.provider.calls,1)

    def test_deleting_live_source_while_generating_discards_answer_but_preserves_usage(self):
        pid,sid,snap=context_fixtures.live_snapshot_fixture(self.fx)
        self.provider.release.clear()
        with patch.object(self.fx.app.extensions['preview_manager'],'snapshot',return_value=snap):
            response=self.post('/api/v1/ai/conversations',{'context_kind':'live','context_id':pid,'history_scope':'none'})
            reply=self.send(response.json['data']);rid=reply.json['data']['request_id'];self.assertTrue(self.provider.entered.wait(3))
            self.fx.finish(sid);latest=self.fx.store.snapshot(self.fx.user,sid)
            self.fx.store.delete_records(self.fx.user,self.fx.auth,[sid],{sid:latest['state_version']},s.new_id())
            self.provider.release.set();row=self.wait(rid)
            self.assertEqual(row['status'],'failed',row);self.assertFalse(row['context_current']);self.assertIsNone(row['result'])
            self.assertEqual(row['estimated_micro_cny'],140);self.assertEqual(self.provider.calls,1)


if __name__=='__main__':unittest.main()
