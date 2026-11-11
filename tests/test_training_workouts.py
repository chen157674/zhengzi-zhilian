"""逐组计量和执行命令，不依赖姿态模型或AI。"""
import unittest
import test_formal_records as shared
from test_training_plans import draft
from fitness_app import db_schema as s
from fitness_app.auth import APIError

class TrainingWorkoutTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.plans=self.fx.app.extensions['training_plans'];self.workouts=self.fx.app.extensions['training_workouts']
        p=self.plans.create(self.fx.user,self.fx.auth,draft(self.plans),s.new_id());self.plan=self.plans.command(self.fx.user,self.fx.auth,p['id'],'activate',{'expected_version':0,'confirmed':True},s.new_id())
        self.session=self.plan['sessions'][0];self.workout=self.workouts.create(self.fx.user,self.fx.auth,{'plan_session_id':self.session['id'],'session_expected_version':0},s.new_id())
    def tearDown(self):self.fx.tearDown()
    def command(self,type,**kw):
        self.workout=self.workouts.command(self.fx.user,self.fx.auth,self.workout['id'],{'expected_version':self.workout['state_version'],'type':type,**kw},s.new_id());return self.workout
    def set(self,index,value,status='done'):
        item=self.workout['items'][0];payload={'expected_version':self.workout['state_version'],'actual_reps':value,'status':status,'source':'manual'}
        self.workout=self.workouts.set(self.fx.user,self.fx.auth,self.workout['id'],item['item_id'],index,payload,s.new_id());return self.workout
    def test_ready_partial_skip_complete_reopen_and_revoke(self):
        self.assertEqual(self.workout['status'],'ready');self.assertEqual(self.workout['completion']['done_sets'],0)
        repeat=self.workouts.create(self.fx.user,self.fx.auth,{'plan_session_id':self.session['id'],'session_expected_version':0},s.new_id());self.assertEqual(repeat['id'],self.workout['id'])
        self.command('start');self.set(1,6);self.assertTrue(self.workout['updated_set']['partial'])
        with self.assertRaises(APIError):self.command('complete',confirmed=True)
        self.set(1,10);self.set(2,None,'skipped');self.assertEqual(self.workout['completion']['done_sets'],1)
        self.set(2,10);self.command('complete',confirmed=True);self.assertEqual(self.workout['status'],'completed')
        progress=self.fx.app.extensions['training_progress'].progress(self.fx.user,self.fx.auth);self.assertEqual(progress['completed_sessions'],1)
        self.command('reopen');item=self.workout['items'][0]
        self.workout=self.workouts.set(self.fx.user,self.fx.auth,self.workout['id'],item['item_id'],2,{'expected_version':self.workout['state_version']},s.new_id(),True)
        self.assertEqual(self.workout['completion']['done_sets'],1);self.command('end_early');self.assertEqual(self.workout['status'],'ended_early')
    def test_immutable_target_and_idempotent_group_no_clock_reset(self):
        self.command('start');item=self.workout['items'][0];key=s.new_id();payload={'expected_version':1,'actual_reps':10,'status':'done','source':'manual'}
        first=self.workouts.set(self.fx.user,self.fx.auth,self.workout['id'],item['item_id'],1,payload,key)
        self.fx.now[0]+=5000;again=self.workouts.set(self.fx.user,self.fx.auth,self.workout['id'],item['item_id'],1,payload,key)
        self.assertEqual(first['updated_set']['saved_at_ms'],again['updated_set']['saved_at_ms']);self.assertEqual(again['state_version'],2)
        self.assertEqual(again['target_snapshot']['items'][0]['target_reps'],10)
    def test_paused_plan_stops_new_execution_but_existing_workout_can_end(self):
        self.command('start');plan=self.plans.get(self.fx.user,self.fx.auth,self.plan['id'])
        self.plans.command(self.fx.user,self.fx.auth,plan['id'],'pause',{'expected_version':plan['state_version']},s.new_id())
        self.command('pause')
        with self.assertRaises(APIError):self.command('resume')
        self.command('end_early');self.assertEqual(self.workout['status'],'ended_early')

    def test_stale_group_writes_and_double_click_do_not_add_extra_sets(self):
        self.command('start');item=self.workout['items'][0];body={'expected_version':1,'actual_reps':10,'status':'done','source':'manual'};key=s.new_id()
        first=self.workouts.set(self.fx.user,self.fx.auth,self.workout['id'],item['item_id'],1,body,key)
        again=self.workouts.set(self.fx.user,self.fx.auth,self.workout['id'],item['item_id'],1,body,key);self.assertEqual(first['state_version'],again['state_version'])
        with self.assertRaises(Exception):self.workouts.set(self.fx.user,self.fx.auth,self.workout['id'],item['item_id'],2,body,s.new_id())
        self.assertEqual(self.workouts.get(self.fx.user,self.fx.auth,self.workout['id'])['completion']['done_sets'],1)

    def test_ready_workout_rescheduled_cannot_start_on_wrong_day(self):
        import datetime as dt
        progress=self.fx.app.extensions['training_progress'];target=(self.plans.today()+dt.timedelta(days=5)).isoformat()
        progress.schedule(self.fx.user,self.fx.auth,self.plan['id'],self.session['id'],'reschedule',{'expected_version':0,'plan_expected_version':self.plan['state_version'],'target_date':target},s.new_id())
        self.workout=self.workouts.get(self.fx.user,self.fx.auth,self.workout['id']);self.assertEqual(self.workout['target_snapshot']['date'],target)
        with self.assertRaises(APIError):self.command('start')

    def test_ready_workout_can_skip_restore_without_inventing_execution(self):
        progress=self.fx.app.extensions['training_progress']
        skipped=progress.schedule(self.fx.user,self.fx.auth,self.plan['id'],self.session['id'],'skip',{'expected_version':0},s.new_id())
        self.assertEqual(progress.today(self.fx.user,self.fx.auth)['next_action'],'skipped')
        with self.assertRaises(APIError):self.command('start')
        restored=progress.schedule(self.fx.user,self.fx.auth,self.plan['id'],self.session['id'],'restore',{'expected_version':skipped['schedule_version']},s.new_id())
        self.assertEqual(restored['status'],'planned');self.assertIsNone(self.workouts.get(self.fx.user,self.fx.auth,self.workout['id'])['actual_started_at_ms'])
        self.command('start');self.assertEqual(self.workout['status'],'running')

    def test_owner_invalid_values_and_unstarted_end_rejected(self):
        with self.assertRaises(Exception):self.workouts.get(self.fx.other,self.fx.auth,self.workout['id'])
        with self.assertRaises(APIError):self.command('complete',confirmed=True)
        self.command('start')
        with self.assertRaises(APIError):self.set(1,True)
        with self.assertRaises(APIError):self.set(3,10)
        with self.assertRaises(APIError):self.set(1,0)
        self.assertEqual(self.workouts.get(self.fx.user,self.fx.auth,self.workout['id'])['state_version'],1)

if __name__=='__main__':unittest.main()
