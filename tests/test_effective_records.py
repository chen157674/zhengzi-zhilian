"""旧修订可追溯但不重复参与列表、本人概览和判项统计。"""
import unittest
from sqlalchemy import update
from fitness_app import db_schema as s
from fitness_app.database import json_text
import test_formal_records as shared


class EffectiveRecordTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()

    def test_hidden_revision_is_readable_but_excluded_from_all_statistics(self):
        old=self.fx.running();self.fx.store.append_event(self.fx.user,old,self.fx.event());self.fx.finish(old)
        current=self.fx.running();self.fx.store.append_event(self.fx.user,current,self.fx.event());self.fx.store.append_event(self.fx.user,current,self.fx.event(2));self.fx.finish(current)
        before=self.fx.store.snapshot(self.fx.user,old,True)
        with self.fx.db.transaction() as c:
            config={**before['config'],'included_in_statistics':False,'superseded_by':current}
            c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==old).values(config_json=json_text(config),state_version=s.analysis_sessions.c.state_version+1))
        listing=self.fx.client.get('/api/v1/records').json['data']
        self.assertEqual(listing['total'],1);self.assertEqual(listing['records'][0]['session_id'],current)
        account=self.fx.client.get('/api/v1/account/overview').json['data']
        self.assertEqual(account['total_sessions'],1);self.assertEqual(account['reference_reps'],2)
        overview=self.fx.client.get('/api/v1/account/training-overview?action_id=bodyweight_squat&code=squat.range').json['data']
        self.assertEqual(overview['record_count'],1)
        historical=self.fx.store.snapshot(self.fx.user,old,True)
        self.assertFalse(historical['included_in_statistics']);self.assertEqual(historical['superseded_by'],current)
        self.assertEqual(historical['events'],before['events']);self.assertEqual(historical['total_count'],1)

    def test_public_session_create_cannot_set_server_visibility_metadata(self):
        from fitness_app.catalogue import get_action
        data={'action_id':'bodyweight_squat','capability_version':get_action('bodyweight_squat')['capability_version'],
              'source':'camera','mode':'free','target':None,'included_in_statistics':False}
        result=self.fx.post(self.fx.client,'/api/v1/sessions',data)
        self.assertEqual(result.status_code,400,result.json)


if __name__=='__main__':unittest.main()
