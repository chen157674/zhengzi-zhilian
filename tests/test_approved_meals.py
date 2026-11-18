"""新餐盘和餐食编辑：真实隔离库，验证权限、回执、冲突及旧分析失效。"""
import unittest
import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.auth import APIError
from fitness_app.meal_recommendations import VERSION, validate_suggestion


class ApprovedMealsTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp()
        self.store=self.fx.app.extensions['diet_journal']
        self.day=self.store.today().isoformat()

    def tearDown(self):
        self.fx.tearDown()

    def payload(self):
        return {'consumed_date':self.day,'meal_type':'lunch',
                'foods':[{'name':'米饭','group':'staple','amount':None}],'note':'午饭'}

    def patch(self,identifier,payload,key=None,client=None):
        client=client or self.fx.client
        token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        return client.patch('/api/v1/nutrition/journal/'+identifier,json=payload,
                            headers={'X-CSRFToken':token,'Idempotency-Key':key or s.new_id()})

    def test_published_options_and_bounds(self):
        path='/api/v1/nutrition/meal-options'
        self.assertEqual(self.fx.app.test_client().get(path).status_code,401)
        result=self.fx.client.get(path+'?meal_type=lunch&scene=canteen')
        self.assertEqual(result.status_code,200,result.json)
        data=result.json['data'];self.assertEqual(data['content_version'],VERSION)
        self.assertTrue(all(set(p['foods'])<=set(x['id'] for x in data['foods']) for p in data['plates']))
        self.assertEqual(self.fx.client.get(path+'?scene=unsupported').status_code,422)
        self.assertEqual(self.fx.client.get(path+'?private_weight=70').status_code,400)

    def test_suggestion_slots_version_and_explicit_restrictions(self):
        payload={'content_version':VERSION,'meal_type':'lunch','scene':'home',
                 'slots':{'staple':'rice','protein':'tofu','vegetable':'leafy-greens'}}
        self.assertEqual(validate_suggestion(payload),payload)
        with self.assertRaises(APIError):validate_suggestion(payload,['豆腐'])
        with self.assertRaises(APIError):validate_suggestion({**payload,'content_version':'old'})
        with self.assertRaises(APIError):validate_suggestion({**payload,'slots':{**payload['slots'],'staple':'tofu'}})
        with self.assertRaises(APIError):validate_suggestion({**payload,'slots':{**payload['slots'],'protein':'invented'}})

    def test_note_detail_receipt_and_replayed_edit(self):
        key=s.new_id();response=self.fx.post(self.fx.client,'/api/v1/nutrition/journal',self.payload(),key)
        self.assertEqual(response.status_code,201,response.json);row=response.json['data']
        self.assertEqual(row['note'],'午饭')
        receipt=self.fx.client.get('/api/v1/nutrition/journal/commands/'+key).json['data']
        self.assertEqual(receipt,{'status':'succeeded','record_id':row['id'],'deleted':False})
        data={'expected_version':1,'note':'已核对','foods':[{'name':'红薯','group':'staple','amount':'一个'}]}
        edit_key=s.new_id()
        for _ in range(2):
            response=self.patch(row['id'],data,edit_key);self.assertEqual(response.status_code,200,response.json)
            self.assertEqual(response.json['data']['version'],2)
        current=self.fx.client.get('/api/v1/nutrition/journal/'+row['id']).json['data']
        self.assertEqual(current['note'],'已核对');self.assertEqual(current['foods'][0]['name'],'红薯')
        self.assertEqual(self.patch(row['id'],{'expected_version':1,'note':'旧页面'}).status_code,409)
        self.assertEqual(self.fx.client.get('/api/v1/nutrition/journal/'+row['id']).json['data']['note'],'已核对')

    def test_owner_csrf_and_extra_fields(self):
        row=self.store.save(self.fx.user,self.fx.auth,self.payload(),s.new_id())
        other=self.fx.app.test_client();self.fx.login(other,'Other_User')
        self.assertEqual(other.get('/api/v1/nutrition/journal/'+row['id']).status_code,404)
        self.assertEqual(self.patch(row['id'],{'expected_version':1,'note':'other'},client=other).status_code,404)
        self.assertEqual(self.fx.client.patch('/api/v1/nutrition/journal/'+row['id'],json={'expected_version':1}).status_code,403)
        self.assertEqual(self.patch(row['id'],{'expected_version':1,'owner':self.fx.other}).status_code,400)
        self.assertEqual(self.patch(row['id'],{'expected_version':1,'note':'x'*501}).status_code,422)

    def test_manual_edit_invalidates_old_analysis_and_late_ai_cannot_overwrite(self):
        root=s.new_id();analysis={'foods':[{'name':'米饭','group':'staple','amount':'一碗'}],
                                'next_meal':'下一餐配豆腐和青菜','swaps':[]}
        saved=self.store.remember(self.fx.user,self.fx.auth,s.new_id(),'午餐吃了一碗米饭',
                                  analysis=analysis,analysis_kind='meal',root_id=root)
        row=self.store.get(self.fx.user,self.fx.auth,saved['journal_id'])
        edit=self.store.edit(self.fx.user,self.fx.auth,row['id'],{
            'expected_version':row['version'],'foods':[{'name':'红薯','group':'staple','amount':None}],'note':'本人更正'},s.new_id())
        self.assertTrue(edit['analysis_stale']);self.assertEqual(edit['analysis']['result'],analysis)
        self.store.remember(self.fx.user,self.fx.auth,s.new_id(),'午餐吃了一碗米饭',
                            analysis=analysis,analysis_kind='meal',root_id=root)
        current=self.store.get(self.fx.user,self.fx.auth,row['id'])
        self.assertEqual(current['foods'][0]['name'],'红薯');self.assertEqual(current['note'],'本人更正')

    def test_workout_note_is_versioned_idempotent_and_survives_commands_and_sets(self):
        from test_training_plans import draft
        plans=self.fx.app.extensions['training_plans'];workouts=self.fx.app.extensions['training_workouts']
        plan=plans.create(self.fx.user,self.fx.auth,draft(plans),s.new_id())
        active=plans.command(self.fx.user,self.fx.auth,plan['id'],'activate',{'expected_version':0,'confirmed':True},s.new_id())
        session=active['sessions'][0]
        workout=workouts.create(self.fx.user,self.fx.auth,{'plan_session_id':session['id'],'session_expected_version':session['schedule_version']},s.new_id())
        target=workout['target_snapshot'];key=s.new_id()
        for _ in range(2):
            workout=workouts.save_note(self.fx.user,self.fx.auth,workout['id'],{'expected_version':0,'note':'今天使用轻重量'},key)
            self.assertEqual(workout['state_version'],1)
        workout=workouts.command(self.fx.user,self.fx.auth,workout['id'],{'expected_version':1,'type':'start'},s.new_id())
        first=workout['items'][0]
        workout=workouts.set(self.fx.user,self.fx.auth,workout['id'],first['item_id'],1,{'expected_version':2,'actual_reps':8,'actual_duration_ms':None,'load':None,'status':'done','source':'manual'},s.new_id())
        self.assertEqual(workout['note'],'今天使用轻重量');self.assertEqual(workout['target_snapshot'],target)
        workout=workouts.command(self.fx.user,self.fx.auth,workout['id'],{'expected_version':workout['state_version'],'type':'end_early','confirmed':True},s.new_id())
        self.assertEqual(workout['note'],'今天使用轻重量')

    def test_workout_note_endpoint_auth_validation_and_conflict(self):
        from test_training_plans import draft
        plans=self.fx.app.extensions['training_plans'];workouts=self.fx.app.extensions['training_workouts']
        plan=plans.create(self.fx.user,self.fx.auth,draft(plans),s.new_id())
        active=plans.command(self.fx.user,self.fx.auth,plan['id'],'activate',{'expected_version':0,'confirmed':True},s.new_id())
        session=active['sessions'][0];workout=workouts.create(self.fx.user,self.fx.auth,{'plan_session_id':session['id'],'session_expected_version':session['schedule_version']},s.new_id())
        path='/api/v1/training/workouts/'+workout['id']+'/note'
        self.assertEqual(self.fx.client.put(path,json={'expected_version':0,'note':'备注'}).status_code,403)
        token=self.fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        headers={'X-CSRFToken':token,'Idempotency-Key':s.new_id()}
        self.assertEqual(self.fx.client.put(path,json={'expected_version':0,'note':'x'*501},headers=headers).status_code,422)
        response=self.fx.client.put(path,json={'expected_version':0,'note':'备注'},headers=headers)
        self.assertEqual(response.status_code,200,response.json)
        headers['Idempotency-Key']=s.new_id()
        self.assertEqual(self.fx.client.put(path,json={'expected_version':0,'note':'覆盖'},headers=headers).status_code,409)


if __name__=='__main__':
    unittest.main()
