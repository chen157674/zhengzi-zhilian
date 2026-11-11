"""周分母守恒、跨周改期、跳过/暂停排除和零分母。"""
import datetime as dt,unittest
from sqlalchemy import update
import test_formal_records as shared
from test_training_plans import draft
from fitness_app import db_schema as s

class TrainingProgressTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.plans=self.fx.app.extensions['training_plans'];self.service=self.fx.app.extensions['training_progress']
        p=self.plans.create(self.fx.user,self.fx.auth,draft(self.plans),s.new_id())
        self.plan=self.plans.command(self.fx.user,self.fx.auth,p['id'],'activate',{'expected_version':0,'confirmed':True},s.new_id())
    def tearDown(self):self.fx.tearDown()
    def progress(self):return self.service.progress(self.fx.user,self.fx.auth,None,self.plan['id'])
    def test_initial_and_skip_restore_partition_sum(self):
        result=self.progress();expected=sum(self.service.monday()<=dt.date.fromisoformat(x['scheduled_date'])<=self.service.monday()+dt.timedelta(days=6) for x in self.plan['sessions'])
        self.assertEqual(result['scheduled_sessions'],expected);self.assertEqual(result['ratio'],0)
        sid=self.plan['sessions'][0]['id'];self.service.schedule(self.fx.user,self.fx.auth,self.plan['id'],sid,'skip',{'expected_version':0},s.new_id())
        result=self.progress();self.assertEqual(result['skipped_sessions'],1)
        self.assertEqual(sum(result[k] for k in ['completed_sessions','partial_sessions','skipped_sessions','missed_sessions','in_progress_sessions','remaining_sessions']),result['scheduled_sessions'])
        self.service.schedule(self.fx.user,self.fx.auth,self.plan['id'],sid,'restore',{'expected_version':1},s.new_id());self.assertEqual(self.progress()['skipped_sessions'],0)
    def test_reschedule_moves_denominator_and_reports_both_weeks(self):
        sid=self.plan['sessions'][0]['id'];target=self.service.monday()+dt.timedelta(days=7)
        if any(x['scheduled_date']==target.isoformat() for x in self.plan['sessions']):target+=dt.timedelta(days=3)
        response=self.service.schedule(self.fx.user,self.fx.auth,self.plan['id'],sid,'reschedule',{'expected_version':0,'plan_expected_version':1,'target_date':target.isoformat()},s.new_id())
        self.assertEqual(response['session']['id'],sid);self.assertTrue(response['affected_weeks']);self.assertTrue(any(x['event_type']=='reschedule' for x in self.progress()['changes']))
    def test_pause_excludes_then_resume_does_not_invent_training(self):
        self.plans.command(self.fx.user,self.fx.auth,self.plan['id'],'pause',{'expected_version':1},s.new_id())
        # 当日暂停后当日尚未结束；当前paused计划的全部未开始项均不得开练。
        today=self.service.today(self.fx.user,self.fx.auth);self.assertEqual(today['plan_status'],'paused');self.assertFalse(today['day']['can_start'])
        self.fx.now[0]+=2*86400000
        with self.fx.db.transaction() as c:c.execute(update(s.auth_sessions).where(s.auth_sessions.c.id==self.fx.auth).values(expires_at_ms=self.fx.now[0]+86400000))
        result=self.progress();self.assertTrue(result['excluded_sessions'])
        self.plans.command(self.fx.user,self.fx.auth,self.plan['id'],'resume',{'expected_version':2,'past_due_policy':'keep_missed'},s.new_id())
        result=self.progress();self.assertEqual(result['completed_sessions'],0);self.assertTrue(result['excluded_sessions'])
    def test_days_before_first_activation_are_not_scheduled_not_missed(self):
        week=self.service.week(self.fx.user,self.fx.auth,self.plan['id'])
        for day in week['days']:
            if dt.date.fromisoformat(day['date'])<self.plans.today():self.assertEqual(day['state'],'not_scheduled')

    def test_same_millisecond_pause_resume_events_follow_plan_versions(self):
        self.plans.command(self.fx.user,self.fx.auth,self.plan['id'],'pause',{'expected_version':1},s.new_id())
        self.plans.command(self.fx.user,self.fx.auth,self.plan['id'],'resume',{'expected_version':2,'past_due_policy':'keep_missed'},s.new_id())
        # 人为使UUID排序与提交顺序相反，不能把resume排在pause前。
        with self.fx.db.transaction() as c:
            c.execute(update(s.training_schedule_events).where(s.training_schedule_events.c.plan_id==self.plan['id'],s.training_schedule_events.c.event_type=='pause').values(id='ffffffff-ffff-4fff-8fff-ffffffffffff'))
            c.execute(update(s.training_schedule_events).where(s.training_schedule_events.c.plan_id==self.plan['id'],s.training_schedule_events.c.event_type=='resume').values(id='00000000-0000-4000-8000-000000000001'))
        self.assertFalse(self.progress()['excluded_sessions'])
        self.assertEqual(self.progress()['remaining_sessions'],self.progress()['scheduled_sessions'])

    def test_out_of_range_and_no_plan_ratio_null(self):
        week=(self.service.monday()+dt.timedelta(days=28)).isoformat();result=self.service.progress(self.fx.user,self.fx.auth,week,self.plan['id'])
        self.assertEqual(result['state'],'out_of_range');self.assertIsNone(result['ratio'])
        self.assertEqual(self.fx.client.get('/api/v1/training/today').status_code,200)

if __name__=='__main__':unittest.main()
