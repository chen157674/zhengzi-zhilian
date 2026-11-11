"""手动计划、版本、事务启用、未来修订与所有人；无需AI。"""
import datetime as dt,unittest
from sqlalchemy import select,func
import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.auth import APIError
from fitness_app.database import ConflictError

def draft(service,title='本周训练'):
    today=service.today();start=today-dt.timedelta(days=today.weekday())
    return {'title':title,'goal':'general_fitness','start_date':start.isoformat(),'weeks':2,'timezone':'Asia/Shanghai',
            'constraints':{'experience':'beginner','training_weekdays':[1,3,5],'minutes_per_session':30,'equipment':['bodyweight']},
            'sessions':[{'client_id':'day-'+str(i),'date':(today+dt.timedelta(days=i)).isoformat(),'day_kind':'training','title':'全身训练','estimated_minutes':30,
            'items':[{'client_id':'item-'+str(i),'action_id':'bodyweight_squat','variant':None,'target_sets':2,'target_reps':10,'target_duration_ms':None,'rest_seconds':60,'load':None,'focus_task_id':None}]} for i in range(3)]}

class TrainingPlanTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp();self.service=self.fx.app.extensions['training_plans']
    def tearDown(self):self.fx.tearDown()
    def create(self,title='本周训练'):return self.service.create(self.fx.user,self.fx.auth,draft(self.service,title),s.new_id())
    def activate(self,p,**kw):return self.service.command(self.fx.user,self.fx.auth,p['id'],'activate',{'expected_version':p['state_version'],'confirmed':True,**kw},s.new_id())
    def test_draft_idempotency_activation_atomic_replacement_and_owner(self):
        key=s.new_id();payload=draft(self.service)
        first=self.service.create(self.fx.user,self.fx.auth,payload,key)
        self.assertEqual(first['id'],self.service.create(self.fx.user,self.fx.auth,payload,key)['id'])
        self.assertEqual(first['sessions'],[]);active=self.activate(first);self.assertEqual(len(active['sessions']),3);self.assertEqual(len(active['client_id_map']),6)
        with self.assertRaises(Exception):self.service.get(self.fx.other,self.fx.auth,first['id'])
        second=self.create('新计划')
        with self.assertRaises(APIError):self.activate(second)
        with self.assertRaises(APIError):self.activate(second,replace_plan_id=first['id'],replace_expected_version=0)
        self.assertEqual(self.service.current(self.fx.user,self.fx.auth)['plan']['id'],first['id'])
        replaced=self.activate(second,replace_plan_id=first['id'],replace_expected_version=active['state_version'])
        self.assertEqual(replaced['status'],'active');self.assertEqual(self.service.get(self.fx.user,self.fx.auth,first['id'])['status'],'archived')
    def test_revision_preserves_started_workout_target_and_new_dates(self):
        active=self.activate(self.create());session=active['sessions'][0];wid=s.new_id()
        with self.fx.db.transaction() as c:c.execute(s.training_workouts.insert().values(id=wid,user_id=self.fx.user,plan_session_id=session['id'],plan_revision_id=session['revision_id'],status='running',target_snapshot_json='{}',completion_json='{}'))
        value=draft(self.service);value['sessions'][0]['items'][0]['target_reps']=20
        updated=self.service.edit(self.fx.user,self.fx.auth,active['id'],{'expected_version':active['state_version'],'edited_revision':{'constraints':value['constraints'],'sessions':value['sessions']},'effective_date':self.service.today().isoformat()},s.new_id())
        old=next(x for x in updated['sessions'] if x['id']==session['id']);self.assertEqual(old['status'],'planned');self.assertEqual(old['items'][0]['target_reps'],10)
        self.assertEqual(updated['revision']['revision_number'],2)
    def test_invalid_types_and_units_rejected_and_delete_preserves_records(self):
        for field,value in [('weeks',True),('goal',[]),('timezone','UTC')]:
            data=draft(self.service);data[field]=value
            with self.assertRaises(APIError):self.service.create(self.fx.user,self.fx.auth,data,s.new_id())
        p=self.create();response=self.service.remove(self.fx.user,self.fx.auth,p['id'],{'expected_version':0,'scope':'draft_only','confirm_title':p['title']},s.new_id())
        self.assertTrue(response['records_preserved']);self.assertEqual(response['deleted_workout_count'],0)
    def test_known_version_conflict_never_overwrites_draft(self):
        p=self.create();self.service.edit(self.fx.user,self.fx.auth,p['id'],{'expected_version':0,'title':'另一页面的新标题'},s.new_id())
        with self.assertRaises(ConflictError):self.service.edit(self.fx.user,self.fx.auth,p['id'],{'expected_version':0,'title':'旧页面覆盖'},s.new_id())
        self.assertEqual(self.service.get(self.fx.user,self.fx.auth,p['id'])['title'],'另一页面的新标题')

    def test_concurrent_activation_keeps_one_current_plan(self):
        from concurrent.futures import ThreadPoolExecutor
        first=self.create('并发一');second=self.create('并发二')
        def activate(plan):
            try:return self.activate(plan)['status']
            except APIError as error:return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(activate,[first,second]))
        self.assertEqual(results.count('active'),1,results)
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.training_plans).where(s.training_plans.c.status=='active')),1)

    def test_revise_then_reschedule_can_use_date_of_cancelled_slot(self):
        active=self.activate(self.create());value=draft(self.service)
        edited=self.service.edit(self.fx.user,self.fx.auth,active['id'],{'expected_version':active['state_version'],'edited_revision':{'constraints':value['constraints'],'sessions':value['sessions']},'effective_date':self.service.today().isoformat()},s.new_id())
        current=[x for x in edited['sessions'] if x['status']!='cancelled'];one=current[0];two=current[1]
        progress=self.fx.app.extensions['training_progress'];future=(self.service.today()+dt.timedelta(days=5)).isoformat()
        progress.schedule(self.fx.user,self.fx.auth,edited['id'],two['id'],'reschedule',{'expected_version':0,'plan_expected_version':edited['state_version'],'target_date':future},s.new_id())
        latest=self.service.get(self.fx.user,self.fx.auth,edited['id'])
        result=progress.schedule(self.fx.user,self.fx.auth,edited['id'],one['id'],'reschedule',{'expected_version':0,'plan_expected_version':latest['state_version'],'target_date':two['scheduled_date']},s.new_id())
        self.assertEqual(result['session']['scheduled_date'],two['scheduled_date'])

    def test_route_csrf_versions_and_minimal_receipt(self):
        payload=draft(self.service);r=self.fx.post(self.fx.client,'/api/v1/training/plan-drafts',payload)
        self.assertEqual(r.status_code,201,r.json)
        self.assertEqual(self.fx.client.get('/api/v1/training/plans/current').json['data']['plan'],None)
        plan=r.json['data'];key=s.new_id();r=self.fx.post(self.fx.client,'/api/v1/training/plans/'+plan['id']+'/activate',{'expected_version':0,'confirmed':True},key)
        self.assertEqual(r.status_code,200,r.json)
        receipt=self.fx.client.get('/api/v1/training/commands/'+key).json['data'];self.assertEqual(receipt['resource_id'],plan['id']);self.assertNotIn('payload',receipt)

    def test_abandoned_unknown_command_is_atomic_and_rejects_late_original_write(self):
        key=s.new_id();path='/api/v1/training/commands/'+key+'/abandon'
        response=self.fx.post(self.fx.client,path,{'confirmed':True},key)
        self.assertEqual(response.status_code,200,response.json);self.assertEqual(response.json['data']['status'],'abandoned')
        self.assertEqual(self.fx.post(self.fx.client,path,{'confirmed':True},key).status_code,200)
        with self.assertRaises(ConflictError):self.service.create(self.fx.user,self.fx.auth,draft(self.service),key)
        other=self.fx.app.test_client();self.fx.login(other,'Record_Admin')
        self.assertEqual(self.fx.post(other,path,{'confirmed':True},key).status_code,404)
        original=s.new_id();plan=self.service.create(self.fx.user,self.fx.auth,draft(self.service),original)
        result=self.fx.post(self.fx.client,'/api/v1/training/commands/'+original+'/abandon',{'confirmed':True},original)
        self.assertEqual(result.json['data']['status'],'succeeded');self.assertEqual(result.json['data']['resource_id'],plan['id'])

if __name__=='__main__':unittest.main()
