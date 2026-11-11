"""周事实分母、资料变化和有界解释，不请求真实AI。"""
import datetime as dt,json,time,unittest
from sqlalchemy import select,func
import test_formal_records as shared
from test_training_plans import draft
from fitness_app import db_schema as s
from fitness_app.auth import APIError
class ReviewProvider:
    def tool_completion(self,key,model,messages,tools,**kwargs):
        facts=json.loads(messages[-1]['content'])['facts']
        answer={'summary':'本周训练应结合已保存观察理解，继续关注可控制的动作。','focus':['先查看具体记录，再选择下一次关注点。'],'evidence_refs':[x['record_id'] for x in facts['records'][:1]],'knowledge_refs':[]}
        return {'message':{'role':'assistant','content':json.dumps(answer)},'calls':[],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20},'estimated_micro_cny':100}
class TrainingReviewTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp();self.service=self.fx.app.extensions['training_reviews']
    def tearDown(self):self.fx.app.extensions['training_jobs'].stop();self.fx.tearDown()
    def test_no_plan_has_actual_records_but_not_fake_execution_count(self):
        sid=self.fx.running();self.fx.finish(sid);facts=self.service.facts(self.fx.user,self.fx.auth,None)
        self.assertIsNone(facts['plan_progress']);self.assertEqual(facts['actual_training']['analysis_record_count'],1);self.assertEqual(facts['actual_training']['execution_session_count'],0)
        self.assertEqual(facts['records'][0]['record_id'],sid)
    def test_job_only_current_facts_and_source_changes_invalidate(self):
        sid=self.fx.running();self.fx.finish(sid);facts=self.service.facts(self.fx.user,self.fx.auth,None)
        settings=self.fx.app.extensions['ai_settings'];settings.environment_key=None;settings.write(self.fx.user,self.fx.auth,{'expected_version':0,'source':'personal','personal_key':'sk-isolated-review-job-fixture-123456'},s.new_id());self.fx.app.extensions['training_provider_factory']=ReviewProvider
        job=self.fx.app.extensions['training_jobs'];jid=s.new_id();data={'week_start':facts['week_start'],'data_version':facts['data_version'],'consent':True,'history_consent':True}
        job.create(self.fx.user,self.fx.auth,jid,data,'review')
        for _ in range(200):
            result=job.get(self.fx.user,self.fx.auth,jid,'review')
            if result['status'] not in {'queued','running'}:break
            time.sleep(.01)
        self.assertEqual(result['status'],'ready',result);self.assertEqual(result['result']['data_version'],facts['data_version'])
        record=self.fx.store.snapshot(self.fx.user,sid);self.fx.store.delete_records(self.fx.user,self.fx.auth,[sid],{sid:record['state_version']},s.new_id())
        self.assertEqual(job.get(self.fx.user,self.fx.auth,jid,'review')['status'],'source_invalid')
    def test_committed_change_during_aggregation_retries_without_holding_a_read_lock(self):
        from unittest.mock import patch
        from sqlalchemy import update
        sid=self.fx.running();self.fx.finish(sid)
        original=self.service._facts;calls=[]
        def changed(*args):
            facts=original(*args);calls.append(facts)
            if len(calls)==1:
                with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==sid).values(state_version=s.analysis_sessions.c.state_version+1))
            return facts
        with patch.object(self.service,'_facts',side_effect=changed):facts=self.service.facts(self.fx.user,self.fx.auth,None)
        self.assertEqual(len(calls),2);self.assertNotEqual(calls[0]['data_version'],facts['data_version'])
        self.assertEqual(facts['records'][0]['state_version'],self.fx.store.snapshot(self.fx.user,sid)['state_version'])

    def test_record_deleted_mid_aggregation_retries_current_facts(self):
        from unittest.mock import patch
        sid=self.fx.running();self.fx.finish(sid);original=self.service._facts;calls=[]
        def deleted(*args):
            calls.append(1)
            if len(calls)==1:
                version=self.fx.store.snapshot(self.fx.user,sid)['state_version']
                self.fx.store.delete_records(self.fx.user,self.fx.auth,[sid],{sid:version},s.new_id())
                raise APIError('NOT_FOUND','聚合中来源已删除',404)
            return original(*args)
        with patch.object(self.service,'_facts',side_effect=deleted):facts=self.service.facts(self.fx.user,self.fx.auth,None)
        self.assertEqual(len(calls),2);self.assertEqual(facts['records'],[])

    def test_per_side_counts_are_not_added_as_total_repetitions(self):
        sid=self.fx.running('standing_dumbbell_curl')
        for seq,side in enumerate(['left','right','left'],1):
            event=self.fx.event(seq);event['side']=side;self.fx.store.append_event(self.fx.user,sid,event,self.fx.auth)
        self.fx.finish(sid);facts=self.service.facts(self.fx.user,self.fx.auth,None)
        value=facts['actual_training']['per_action'][0]
        self.assertEqual(value['reference_reps'],0);self.assertEqual(value['reference_per_side_reps'],1);self.assertEqual(value['reference_hold_ms'],0)

    def test_trend_groups_keep_rule_view_side_and_units_separate(self):
        from unittest.mock import patch
        ids=[self.fx.running() for _ in range(3)]
        for sid in ids:self.fx.finish(sid)
        def stats(row):
            index=ids.index(row['session_id'])
            return [{'code':'squat.range','side':'none','rule_version':'rule-a' if index<2 else 'rule-b','view':'side','unit':'cycles','issue':index,'evaluated':3,'unknown':1,'ratio':index/3}]
        with patch('fitness_app.training_reviews.record_statistics',side_effect=stats):facts=self.service.facts(self.fx.user,self.fx.auth,None)
        self.assertEqual(len(facts['trend_groups']),2)
        groups={x['rule_version']:x for x in facts['trend_groups']}
        self.assertEqual(len(groups['rule-a']['records']),2);self.assertEqual(groups['rule-a']['totals']['evaluated'],6)
        self.assertEqual(groups['rule-a']['totals']['issue'],1);self.assertEqual(groups['rule-b']['totals']['issue'],2)

    def test_extra_numbers_and_forged_refs_rejected(self):
        facts=self.service.facts(self.fx.user,self.fx.auth,None);inputs={'facts':facts,'data_version':facts['data_version']}
        for data in [{'summary':'进步100%','focus':[],'evidence_refs':[],'knowledge_refs':[]},{'summary':'测试解释','focus':[],'evidence_refs':[s.new_id()],'knowledge_refs':[]}]:
            with self.assertRaises(APIError):self.service.validate(inputs,data)
        self.assertEqual(self.fx.client.get('/training/review').status_code,200)
        self.assertEqual(self.fx.client.get('/api/v1/training/reviews/week').status_code,200)
if __name__=='__main__':unittest.main()
