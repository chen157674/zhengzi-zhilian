"""复盘页面所需只读预览/历史/版本绑定；供应商为工程替身，无真实付费。"""
import unittest
from sqlalchemy import select,func,update
from fitness_app import db_schema as s
import test_ai_jobs as shared


class ReportWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.f=shared.AIJobsTests();self.f.setUp();self.fx=self.f.fx
    def tearDown(self):self.f.tearDown()
    def preview(self,**changes):
        body={'kind':'review','record_ids':[self.f.sid],'history_scope':'related',**changes}
        response=self.f.post('/api/v1/ai/report-options',body)
        self.assertEqual(response.status_code,200,response.json);return body,response.json['data']
    def generate(self,body,options,key=None):
        return self.f.post('/api/v1/ai/reports',{**body,'expected_versions':options['expected_versions'],
            'expected_context':options['expected_context'],'consent':True},key)

    def test_preview_exposes_actual_scope_without_generation_or_key(self):
        body,data=self.preview();self.assertTrue(data['no_generation_performed'])
        self.assertEqual(data['read_scope']['selected_records'],1);self.assertEqual(data['history']['records_total'],1)
        self.assertEqual(data['record_ids'],[self.f.sid]);self.assertEqual(data['provider']['source'],'personal')
        self.assertNotIn('personal_key',str(data));self.assertEqual(self.f.provider.calls,0)
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.ai_requests)),0)
        self.assertEqual(self.f.post('/api/v1/ai/report-options',{'kind':[]}).status_code,422)
        self.assertEqual(self.f.post('/api/v1/ai/report-options',{'user_id':self.fx.other}).status_code,400)

    def test_changed_profile_or_selected_record_requires_preview_again_before_charge(self):
        body,data=self.preview()
        self.fx.app.extensions['training_info'].write(self.fx.user,self.fx.auth,{'experience':'beginner','expected_version':0},s.new_id())
        self.assertEqual(self.generate(body,data).status_code,409);self.assertEqual(self.f.provider.calls,0)
        body,data=self.preview()
        with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==self.f.sid).values(state_version=s.analysis_sessions.c.state_version+1))
        self.assertEqual(self.generate(body,data).status_code,409);self.assertEqual(self.f.provider.calls,0)

    def test_history_is_paginated_owner_only_and_does_not_return_bodies_or_chat(self):
        body,data=self.preview();reply=self.generate(body,data);rid=reply.json['data']['request_id'];self.f.wait(rid)
        conversation=self.f.conversation();chat=self.f.send(conversation).json['data']['request_id'];self.f.wait(chat)
        r=self.fx.client.get('/api/v1/ai/reports?page=1&page_size=1');self.assertEqual(r.status_code,200,r.json)
        data=r.json['data'];self.assertEqual(data['total'],1);row=data['reports'][0];self.assertEqual(row['request_id'],rid)
        self.assertTrue(row['body_saved']);self.assertNotIn('result',row);self.assertNotIn('context',row)
        other=self.fx.app.test_client();self.fx.login(other,'other_user');self.assertEqual(other.get('/api/v1/ai/reports').json['data']['total'],0)
        self.assertEqual(self.fx.client.get('/api/v1/ai/reports?page_size=21').status_code,422)
        self.assertEqual(self.fx.client.get('/api/v1/ai/reports?user_id='+self.fx.other).status_code,400)

    def test_deleted_body_and_stale_evidence_are_distinct_and_usage_retained(self):
        body,options=self.preview();rid=self.generate(body,options).json['data']['request_id'];row=self.f.wait(rid)
        self.assertFalse(row['report_body_deleted']);self.assertIsNotNone(row['result'])
        with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==self.f.sid).values(state_version=s.analysis_sessions.c.state_version+1))
        stale=self.fx.client.get('/api/v1/ai/reports/'+rid).json['data'];self.assertFalse(stale['context_current']);self.assertIsNone(stale['result']);self.assertFalse(stale['report_body_deleted'])
        token=self.fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        deleted=self.fx.client.delete('/api/v1/ai/reports/'+rid,json={'expected_version':row['state_version']},headers={'X-CSRFToken':token})
        self.assertEqual(deleted.status_code,200,deleted.json)
        saved=self.fx.client.get('/api/v1/ai/reports/'+rid).json['data'];self.assertTrue(saved['report_body_deleted']);self.assertEqual(saved['estimated_micro_cny'],140)
        self.assertFalse(self.fx.client.get('/api/v1/ai/reports').json['data']['reports'][0]['body_saved'])

    def test_comparison_preview_preserves_supported_pairs_and_uncomparable_case(self):
        second=self.f.fixture.create([(0,1000,'not_observed')]);body,data=self.preview(kind='comparison',record_ids=[self.f.sid,second],history_scope='none')
        self.assertTrue(data['comparison']['comparable']);self.assertEqual(data['comparison']['comparisons'][0]['unit'],'ms')
        third=self.f.fixture.create([(0,1000,'not_observed')],view='front');_,bad=self.preview(kind='comparison',record_ids=[self.f.sid,third],history_scope='none')
        self.assertFalse(bad['comparison']['comparable']);self.assertEqual(bad['comparison']['comparisons'],[]);self.assertTrue(bad['comparison']['reason'])
        self.assertEqual(self.f.provider.calls,0)

    def test_replaying_original_request_does_not_regenerate_after_evidence_change(self):
        body,data=self.preview();rid=s.new_id();reply=self.generate(body,data,rid);self.assertEqual(reply.status_code,202,reply.json);self.f.wait(rid)
        with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==self.f.sid).values(state_version=s.analysis_sessions.c.state_version+1))
        reply=self.generate(body,data,rid);self.assertEqual(reply.status_code,202,reply.json);self.assertEqual(self.f.provider.calls,1)
        hidden=self.f.fixture.create([(0,1000,'issue')],hidden=True)
        r=self.f.post('/api/v1/ai/report-options',{'kind':'review','record_ids':[hidden]});self.assertEqual(r.status_code,409,r.json)

    def test_single_record_review_without_history_is_available_to_existing_pdf_options(self):
        body,options=self.preview(history_scope='none');rid=self.generate(body,options).json['data']['request_id'];self.assertEqual(self.f.wait(rid)['status'],'succeeded')
        response=self.fx.client.get('/api/v1/reports/export-options?record_ids='+self.f.sid)
        self.assertEqual(response.status_code,200,response.json);self.assertIn(rid,[v['id'] for v in response.json['data']['ai_reports']])

    def test_period_review_can_export_only_when_all_actual_historical_sources_selected(self):
        second=self.f.fixture.create([(0,1000,'not_observed')]);body,options=self.preview(record_ids=[],history_scope='all')
        rid=self.generate(body,options).json['data']['request_id'];self.assertEqual(self.f.wait(rid)['status'],'succeeded')
        one=self.fx.client.get('/api/v1/reports/export-options?record_ids='+self.f.sid).json['data'];self.assertNotIn(rid,[v['id'] for v in one['ai_reports']])
        both=self.fx.client.get('/api/v1/reports/export-options?record_ids='+self.f.sid+','+second).json['data'];self.assertIn(rid,[v['id'] for v in both['ai_reports']])
        from fitness_app.export_snapshot import normalize
        payload=normalize({'record_ids':[self.f.sid,second],'expected_versions':both['expected_versions'],'include_ai_report':True,'ai_report_ids':[rid]})
        content,_,_=self.fx.app.extensions['report_exports'].builder.freeze(self.fx.user,self.fx.auth,payload)
        self.assertEqual([v['id'] for v in content['ai_reports']],[rid]);self.assertEqual(self.f.provider.calls,1)


if __name__=='__main__':unittest.main()
