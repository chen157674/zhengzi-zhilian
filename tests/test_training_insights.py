"""工程统计去重/单位与本人范围，合成存储例不作为真实纠错准确率。"""
import unittest
from sqlalchemy import update
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.database import json_text
from fitness_app.training_insights import record_statistics,record_evidence
import test_formal_records as shared


class InsightsTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()
    def create(self,items,view='side',hidden=False):
        sid=self.fx.running();data=self.fx.store.snapshot(self.fx.user,sid)
        rule=get_action('bodyweight_squat')['issues'][0]
        with self.fx.db.transaction() as c:
            for start,end,status in items:
                finding={'code':rule['code'],'status':status,'side':'none','start_ms':start,'end_ms':end,
                    'rule_version':rule['rule_version'],'evidence_level':'reference','message':'工程夹具',
                    'body_parts':rule['body_parts'],'suggestion':rule['suggestion'],
                    'evidence':{'view':view,'value':.3,'threshold':rule['threshold']}}
                c.execute(s.posture_observations.insert().values(session_id=sid,set_id=data['groups'][0]['id'],
                    code=rule['code'],side='none',start_ms=start,end_ms=end,rule_version=rule['rule_version'],data_json=json_text(finding)))
            if hidden:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==sid).values(config_json=json_text({**data['config'],'included_in_statistics':False})))
        self.fx.finish(sid);return sid
    def get(self,client=None,path='/api/v1/account/training-insights'):return (client or self.fx.client).get(path)

    def test_history_is_all_matched_records_not_raw_frame_error_counts(self):
        self.create([(0,1000,'issue'),(500,1500,'issue'),(1500,2000,'not_observed')])
        self.create([(0,1000,'not_observed')]);self.create([])
        self.create([(0,1000,'issue')],hidden=True)
        r=self.get();self.assertEqual(r.status_code,200,r.json);data=r.json['data'];self.assertEqual(data['records_total'],3)
        cohort=next(c for c in data['rule_cohorts'] if c['code']=='squat.range')
        self.assertEqual(cohort['records_with_issue'],1);self.assertEqual(cohort['assessed_records'],2)
        self.assertEqual(cohort['unassessable_records'],1);self.assertEqual(cohort['ratio'],.5)
        self.assertIsNone(data['independent_specific_error_accuracy']);self.assertTrue(data['no_evidence_is_not_normal'])

    def test_unknown_is_not_zero_error_and_hidden_demo_and_other_users_are_excluded(self):
        sid=self.create([]);demo=self.create([(0,1000,'issue')])
        with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==demo).values(source='demo'))
        data=self.get().json['data'];self.assertEqual(data['records_total'],1)
        self.assertIsNone(data['rule_cohorts'][0]['ratio']);self.assertEqual(data['rule_cohorts'][0]['unassessable_records'],1)
        other=self.fx.app.test_client();self.fx.login(other,'other_user')
        self.assertEqual(self.get(other).json['data']['records_total'],0)
        r=self.fx.post(other,'/api/v1/records/comparison',{'record_ids':[sid,demo]});self.assertEqual(r.status_code,404,r.json)

    def test_continuous_windows_use_union_and_do_not_become_repetition_counts(self):
        sid=self.create([(0,1000,'issue'),(500,1500,'issue'),(1500,2000,'not_observed'),(2000,2500,'unassessable')])
        data=self.fx.store.snapshot(self.fx.user,sid,True);stats=record_statistics(data)
        self.assertEqual(len(stats),1);self.assertEqual(stats[0]['unit'],'ms')
        self.assertEqual((stats[0]['issue'],stats[0]['evaluated'],stats[0]['unknown']),(1500,2000,500))
        evidence=record_evidence(data)
        self.assertTrue(all(e['record_id']==sid for e in evidence));self.assertTrue(all(e['id'].startswith('e:') for e in evidence))

    def test_comparison_requires_same_view_version_unit_and_current_records(self):
        first=self.create([(0,1000,'issue'),(1000,2000,'not_observed')]);second=self.create([(0,2000,'not_observed')])
        r=self.fx.post(self.fx.client,'/api/v1/records/comparison',{'record_ids':[first,second]})
        self.assertEqual(r.status_code,200,r.json);data=r.json['data'];self.assertTrue(data['comparable'])
        self.assertEqual(data['comparisons'][0]['ratio_change'],-.5)
        third=self.create([(0,2000,'not_observed')],view='front')
        r=self.fx.post(self.fx.client,'/api/v1/records/comparison',{'record_ids':[first,third]})
        self.assertFalse(r.json['data']['comparable']);self.assertEqual(r.json['data']['comparisons'],[])
        r=self.fx.post(self.fx.client,'/api/v1/records/comparison',{'record_ids':[first,second],'expected_versions':{first:True,second:1}})
        self.assertEqual(r.status_code,422,r.json)

    def test_detail_limit_does_not_truncate_aggregate_scope(self):
        for _ in range(3):self.create([(0,1000,'not_observed')])
        store=self.fx.app.extensions['training_insights'];data=store.history(self.fx.user,self.fx.auth,detail_limit=1)
        self.assertEqual(data['records_total'],3);self.assertEqual(data['details_returned'],1);self.assertTrue(data['details_limited'])
        self.assertEqual(data['rule_cohorts'][0]['assessed_records'],3)
        r=self.get(path='/api/v1/account/training-insights?date_from=bad-date');self.assertEqual(r.status_code,422,r.json)
        r=self.get(path='/api/v1/account/training-insights?user_id='+self.fx.other);self.assertEqual(r.status_code,400,r.json)


if __name__=='__main__':unittest.main()
