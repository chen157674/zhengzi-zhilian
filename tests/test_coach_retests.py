"""保存单项草稿与跨来源比较，不把工程夹具当真实改善。"""
import unittest
from sqlalchemy import update
import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.posture_rules import rules_for
from fitness_app.database import json_text

class CoachRetestTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp();self.service=self.fx.app.extensions['coach_retests']
    def tearDown(self):self.fx.tearDown()
    def record(self,status='issue',source='camera'):
        sid=self.fx.running('machine_chest_press');rule=rules_for('machine_chest_press')[0]
        item={k:rule[k] for k in ('code','rule_version','body_parts','suggestion')}
        item.update(status=status,side='none',start_ms=0,end_ms=3000,message='合成推胸观察',reason='',evidence_level='reference',evidence={'view':'side','value':.4})
        self.fx.store.append_posture(self.fx.user,sid,1,[item],self.fx.auth,3000)
        self.fx.finish(sid)
        if source=='video':
            with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==sid).values(source='video',status='completed'))
        return sid
    def test_saved_card_cross_source_and_source_invalidation(self):
        for a,b in [('camera','camera'),('camera','video'),('video','camera'),('video','video')]:
            origin=self.record(source=a);option=self.fx.app.extensions['retests'].options(self.fx.user,origin,self.fx.auth)['options'][0]
            tid=s.new_id();data={'origin':option['origin'],'confirmed':True}
            saved=self.service.save(self.fx.user,self.fx.auth,tid,data);self.assertEqual(saved['status'],'ready')
            self.assertEqual(self.service.save(self.fx.user,self.fx.auth,tid,data)['task_id'],tid)
            target=self.record('not_observed',b)
            result=self.service.bind(self.fx.user,self.fx.auth,tid,{'target_record_id':target,'expected_version':0,'same_trainee':True,'comparable_conditions':True})
            self.assertEqual(result['comparison']['state'],'not_reobserved',result)
            with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==target).values(state_version=s.analysis_sessions.c.state_version+1))
            self.assertEqual(self.service.get(self.fx.user,self.fx.auth,tid)['status'],'source_invalid')
    def test_confirmation_and_cross_owner_are_required(self):
        sid=self.record();origin=self.fx.app.extensions['retests'].options(self.fx.user,sid,self.fx.auth)['options'][0]['origin'];tid=s.new_id()
        with self.assertRaises(Exception):self.service.save(self.fx.user,self.fx.auth,tid,{'origin':origin})
        self.service.save(self.fx.user,self.fx.auth,tid,{'origin':origin,'confirmed':True})
        with self.assertRaises(Exception):self.service.get(self.fx.other,self.fx.auth,tid)
        with self.assertRaises(Exception):self.service.bind(self.fx.user,self.fx.auth,tid,{'target_record_id':sid,'expected_version':0,'same_trainee':True,'comparable_conditions':True})

if __name__=='__main__':unittest.main()
