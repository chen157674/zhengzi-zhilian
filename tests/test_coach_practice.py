"""练习状态独立于真实复测，同对象版本/幂等/分页/权限。"""
import unittest
from sqlalchemy import select,func
from test_coach_retests import CoachRetestTests
from fitness_app import db_schema as s
from fitness_app.auth import APIError

class CoachPracticeTests(unittest.TestCase):
    def setUp(self):
        self.fixture=CoachRetestTests();self.fixture.setUp();self.fx=self.fixture.fx;self.service=self.fx.app.extensions['coach_practice']
        sid=self.fixture.record();self.origin=self.fx.app.extensions['retests'].options(self.fx.user,sid,self.fx.auth)['options'][0]['origin']
    def tearDown(self):self.fixture.tearDown()
    def save(self,practice=None):
        tid=s.new_id();data={'origin':self.origin,'confirmed':True}
        if practice is not None:data['practice']=practice
        self.fx.app.extensions['coach_retests'].save(self.fx.user,self.fx.auth,tid,data);return tid
    def test_state_commands_do_not_mark_improvement_and_are_idempotent(self):
        tid=self.save({'steps':[{'text':'按原提示稳定躯干','citation_ids':[]}],'knowledge_refs':[]})
        payload={'expected_version':0,'type':'start_practice'};key=s.new_id()
        first=self.service.mutate(self.fx.user,self.fx.auth,tid,payload,key,True)
        self.assertEqual(first['practice']['status'],'practicing')
        self.assertEqual(self.service.mutate(self.fx.user,self.fx.auth,tid,payload,key,True)['practice']['state_version'],1)
        second=self.service.mutate(self.fx.user,self.fx.auth,tid,{'expected_version':1,'type':'finish_practice'},s.new_id(),True)
        self.assertEqual(second['practice']['status'],'awaiting_retest');self.assertNotIn('comparison',second)
        self.assertIn('start_practice',second['allowed_actions'])
    def test_invalid_reference_rejected_before_any_parent_write(self):
        tid=s.new_id()
        with self.assertRaises(APIError):self.fx.app.extensions['coach_retests'].save(self.fx.user,self.fx.auth,tid,{'origin':self.origin,'confirmed':True,'practice':{'steps':[{'text':'错误引用','citation_ids':['fake']}],'knowledge_refs':[]}})
        with self.fx.db.transaction(False) as c:self.assertIsNone(c.execute(select(s.coach_saved_retests).where(s.coach_saved_retests.c.id==tid)).first())
    def test_comparison_returns_real_same_item_counts_and_media_limits(self):
        tid=self.save();missing=self.service.comparison(self.fx.user,self.fx.auth,tid)
        self.assertEqual(missing['comparability']['status'],'awaiting_target')
        target=self.fixture.record('not_observed')
        self.fx.app.extensions['coach_retests'].bind(self.fx.user,self.fx.auth,tid,{'target_record_id':target,'expected_version':0,'same_trainee':True,'comparable_conditions':True})
        result=self.service.comparison(self.fx.user,self.fx.auth,tid)
        self.assertEqual(result['comparability']['status'],'comparable',result)
        self.assertEqual(result['metrics']['source']['issue'],3000)
        self.assertEqual(result['metrics']['target']['issue'],0)
        self.assertEqual(result['media']['source']['availability'],'unavailable')
        self.assertIsNone(result['metrics']['change'])
        self.assertEqual(self.fx.client.get('/coach/tasks/'+tid+'/compare').status_code,200)
        self.assertEqual(self.fx.client.get('/coach/tasks').status_code,200)

    def test_save_rechecks_origin_inside_write_transaction(self):
        from unittest.mock import patch
        from sqlalchemy import update
        tid=s.new_id();retests=self.fx.app.extensions['retests'];original=retests.resolve
        def changed(*args):
            value=original(*args)
            with self.fx.db.transaction() as c:c.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id==self.origin['session_id']).values(state_version=s.analysis_sessions.c.state_version+1))
            return value
        with patch.object(retests,'resolve',side_effect=changed):
            with self.assertRaises(Exception):self.fx.app.extensions['coach_retests'].save(self.fx.user,self.fx.auth,tid,{'origin':self.origin,'confirmed':True})
        with self.fx.db.transaction(False) as c:self.assertIsNone(c.execute(select(s.coach_saved_retests).where(s.coach_saved_retests.c.id==tid)).first())

    def test_target_preview_version_must_still_match_at_confirmation(self):
        tid=self.save();target=self.fixture.record('not_observed');record=self.fx.store.snapshot(self.fx.user,target)
        with self.assertRaises(Exception):self.fx.app.extensions['coach_retests'].bind(self.fx.user,self.fx.auth,tid,{'target_record_id':target,'target_expected_version':record['state_version']+1,'expected_version':0,'same_trainee':True,'comparable_conditions':True})
        self.assertIsNone(self.fx.app.extensions['coach_retests'].get(self.fx.user,self.fx.auth,tid)['target_record_id'])
        result=self.fx.app.extensions['coach_retests'].bind(self.fx.user,self.fx.auth,tid,{'target_record_id':target,'target_expected_version':record['state_version'],'expected_version':0,'same_trainee':True,'comparable_conditions':True})
        self.assertEqual(result['target_record_id'],target)

    def test_target_replacement_requires_current_task_version_and_conditions(self):
        tid=self.save();first=self.fixture.record('not_observed');second=self.fixture.record('issue')
        service=self.fx.app.extensions['coach_retests'];service.bind(self.fx.user,self.fx.auth,tid,{'target_record_id':first,'expected_version':0,'same_trainee':True,'comparable_conditions':True})
        with self.assertRaises(Exception):service.bind(self.fx.user,self.fx.auth,tid,{'target_record_id':second,'expected_version':0,'same_trainee':True,'comparable_conditions':True})
        result=service.bind(self.fx.user,self.fx.auth,tid,{'target_record_id':second,'expected_version':1,'same_trainee':True,'comparable_conditions':True})
        self.assertEqual(result['target_record_id'],second);self.assertEqual(result['state_version'],2)

    def test_linked_workout_is_rechecked_at_practice_commit(self):
        from unittest.mock import patch
        from sqlalchemy import update
        from test_training_plans import draft
        plans=self.fx.app.extensions['training_plans'];workouts=self.fx.app.extensions['training_workouts']
        value=draft(plans);value['sessions'][0]['items'][0]['action_id']='machine_chest_press'
        plan=plans.create(self.fx.user,self.fx.auth,value,s.new_id());plan=plans.command(self.fx.user,self.fx.auth,plan['id'],'activate',{'expected_version':0,'confirmed':True},s.new_id())
        workout=workouts.create(self.fx.user,self.fx.auth,{'plan_session_id':plan['sessions'][0]['id'],'session_expected_version':0},s.new_id());tid=self.save();original=self.service.clean
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            with self.fx.db.transaction() as c:c.execute(update(s.training_workouts).where(s.training_workouts.c.id==workout['id']).values(status='voided'))
            return result
        with patch.object(self.service,'clean',side_effect=changed):
            with self.assertRaises(Exception):self.service.mutate(self.fx.user,self.fx.auth,tid,{'expected_version':0,'linked_workout_id':workout['id']},s.new_id())
        self.assertIsNone((self.service.snapshot(self.fx.user,self.fx.auth,tid) or {}).get('linked_workout_id'))

    def test_withdrawn_practice_citation_retracts_step_and_requires_edit(self):
        knowledge=self.fx.app.extensions['knowledge_store'];row=next(x for x in knowledge.rows if x['action_id']=='machine_chest_press')
        tid=self.save({'steps':[{'text':'依据该方法练习','citation_ids':[row['id']]}],'knowledge_refs':[{'id':row['id'],'version':row['version']}]})
        knowledge.by_id[row['id']]['status']='withdrawn'
        task=self.fx.app.extensions['coach_retests'].get(self.fx.user,self.fx.auth,tid)
        self.assertEqual(task['practice']['steps'],[]);self.assertEqual(task['practice']['knowledge_status'],'source_invalid');self.assertEqual(task['allowed_actions'],[])
        with self.assertRaises(APIError):self.service.mutate(self.fx.user,self.fx.auth,tid,{'expected_version':0,'type':'start_practice'},s.new_id(),True)
        edited=self.service.mutate(self.fx.user,self.fx.auth,tid,{'expected_version':0,'steps':[{'text':'本人确认的练习记录','citation_ids':[]}],'knowledge_refs':[]},s.new_id())
        self.assertEqual(edited['practice']['knowledge_status'],'valid');self.assertIn('start_practice',edited['allowed_actions'])

    def test_pagination_signed_scope_legacy_compatibility_and_owner(self):
        for _ in range(3):self.save()
        first=self.service.list(self.fx.user,self.fx.auth,{'view':'practice','limit':'2'})
        self.assertEqual(len(first['items']),2);self.assertTrue(first['has_more'])
        second=self.service.list(self.fx.user,self.fx.auth,{'view':'practice','limit':'2','cursor':first['next_cursor']})
        self.assertEqual(len(second['items']),1);self.assertFalse(second['has_more'])
        self.assertIn('tasks',self.fx.app.extensions['coach_retests'].list(self.fx.user,self.fx.auth))
        with self.assertRaises(APIError):self.service.list(self.fx.user,self.fx.auth,{'view':'practice','status':'practicing','cursor':first['next_cursor']})
        with self.assertRaises(Exception):self.service.mutate(self.fx.other,self.fx.auth,first['items'][0]['task_id'],{'expected_version':0,'type':'start_practice'},s.new_id(),True)

if __name__=='__main__':unittest.main()
