"""成长看板与自动饮食记忆：真实隔离库、所有人/来源/删除/升级边界。"""
import datetime as dt
import unittest
from unittest.mock import patch
from sqlalchemy import select,func
import test_formal_records as shared
from legacy_schema_support import legacy_shape,rows_snapshot
from fitness_app import db_schema as s
from fitness_app.database import StorageError

class GrowthTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp()
        self.store=self.fx.app.extensions['diet_journal'];self.day=self.store.today().isoformat()
    def tearDown(self):self.fx.tearDown()
    def payload(self):
        return {'consumed_date':self.day,'meal_type':'lunch','foods':[{'name':'米饭','group':'staple','amount':'一碗'},{'name':'鸡蛋','group':'protein','amount':'两个'}]}
    def remember(self,text,**kwargs):return self.store.remember(self.fx.user,self.fx.auth,s.new_id(),text,**kwargs)
    def test_manual_save_dedup_and_private_delete(self):
        path='/api/v1/nutrition/journal';payload=self.payload();key=s.new_id()
        self.assertEqual(self.fx.app.test_client().get(path).status_code,401)
        self.assertEqual(self.fx.client.post(path,json=payload).status_code,403)
        first=self.fx.post(self.fx.client,path,payload,key);self.assertEqual(first.status_code,201,first.json)
        again=self.fx.post(self.fx.client,path,payload,key);self.assertEqual(first.json['data']['id'],again.json['data']['id'])
        self.assertEqual(first.headers['Cache-Control'],'no-store')
        other=self.fx.app.test_client();self.fx.login(other,'Other_User')
        token=other.get('/api/v1/auth/csrf').json['data']['csrf_token']
        identifier=first.json['data']['id']
        self.assertEqual(other.delete(path+'/'+identifier,json={'expected_version':1},headers={'X-CSRFToken':token,'Idempotency-Key':s.new_id()}).status_code,404)
        self.assertEqual(other.get(path).json['data']['records'],[])
        token=self.fx.client.get('/api/v1/auth/csrf').json['data']['csrf_token'];command=s.new_id()
        for _ in range(2):
            response=self.fx.client.delete(path+'/'+identifier,json={'expected_version':1},headers={'X-CSRFToken':token,'Idempotency-Key':command});self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.fx.client.get(path).json['data']['records'],[])
        with self.fx.db.transaction(False) as c:
            receipt=c.execute(select(s.command_receipts).where(s.command_receipts.c.command_id==key)).mappings().one()
            self.assertNotIn('米饭',receipt['result_json'])
    def test_auto_meal_analysis_followup_and_deleted_task_never_revives(self):
        root=s.new_id();analysis={'foods':[{'name':'米饭','group':'staple','amount':'一碗'}]}
        first=self.remember('午餐吃了一碗米饭',analysis=analysis,analysis_kind='meal',root_id=root)
        second=self.remember('午餐吃了一碗米饭，两个鸡蛋',analysis={'foods':analysis['foods']+[{'name':'鸡蛋','group':'protein','amount':'两个'}]},analysis_kind='meal',root_id=root)
        self.assertEqual(first['journal_id'],second['journal_id'])
        records=self.store.list(self.fx.user,self.fx.auth,self.day,self.day)['records'];self.assertEqual(len(records),1);self.assertEqual(len(records[0]['foods']),2)
        self.store.remove(self.fx.user,self.fx.auth,first['journal_id'],{'expected_version':records[0]['version']},s.new_id())
        self.remember('午餐吃了一碗米饭',analysis=analysis,analysis_kind='meal',root_id=root)
        self.assertEqual(self.store.list(self.fx.user,self.fx.auth,self.day,self.day)['records'],[])
    def test_explicit_preferences_and_body_recalled_and_editable(self):
        self.remember('我喜欢吃西兰花和鸡蛋。我不吃牛肉。我身高175厘米，我体重70公斤，我今年24岁，我想增肌。')
        memories=self.store.memories(self.fx.user,self.fx.auth)['memories']
        self.assertEqual({x['kind'] for x in memories},{'food_like','food_dislike','body','goal'})
        self.assertEqual(self.store.list(self.fx.user,self.fx.auth,self.day,self.day)['records'],[])
        item=next(x for x in memories if x['value']=='西兰花')
        self.store.edit_memory(self.fx.user,self.fx.auth,item['id'],{'expected_version':item['version'],'value':'菠菜'})
        self.remember('我喜欢吃西兰花。')
        self.assertIn('菠菜',str(self.store.context(self.fx.user,self.fx.auth)))
        self.store.edit_memory(self.fx.user,self.fx.auth,item['id'],{'expected_version':2},True)
        self.remember('我喜欢吃西兰花。')
        self.assertNotIn('菠菜',str(self.store.context(self.fx.user,self.fx.auth)))
    def test_no_other_hypothetical_future_or_recommendation_as_food(self):
        for text in ['我朋友今天吃了鸡蛋','假设我吃了米饭','我明天打算吃了鸡蛋','我今天没有吃鸡蛋','我想吃西兰花，应该怎么搭配？']:
            self.remember(text)
        self.assertEqual(self.store.list(self.fx.user,self.fx.auth,self.day,self.day)['records'],[])
        self.assertEqual(self.store.memories(self.fx.user,self.fx.auth)['memories'],[])
    def test_actual_foods_are_separate_from_likes_and_unknown_names_work(self):
        self.remember('我喜欢吃鸡肉，今天午餐吃了米饭和鸡蛋。')
        self.remember('我今天晚餐吃了意大利面。')
        rows=self.store.list(self.fx.user,self.fx.auth,self.day,self.day)['records']
        lunch=next(x for x in rows if x['meal_type']=='lunch')
        self.assertEqual({x['name'] for x in lunch['foods']},{'米饭','鸡蛋'})
        self.assertIn('意大利面',str(rows))
    def test_repeated_self_report_is_one_record_and_preference_change_is_consistent(self):
        first=self.remember('今天午餐吃了米饭和鸡蛋。')
        again=self.remember('今天午餐吃了米饭和鸡蛋。')
        self.assertEqual(first['journal_id'],again['journal_id'])
        self.assertEqual(len(self.store.list(self.fx.user,self.fx.auth,self.day,self.day)['records']),1)
        self.remember('我喜欢吃牛肉。');self.remember('我不吃牛肉。')
        rows=self.store.memories(self.fx.user,self.fx.auth)['memories']
        beef=[x for x in rows if x['value']=='牛肉'];self.assertEqual(len(beef),1);self.assertEqual(beef[0]['kind'],'food_dislike')
    def test_date_range_and_future_are_validated(self):
        payload=self.payload();payload['consumed_date']=(self.store.today()+dt.timedelta(days=1)).isoformat()
        self.assertEqual(self.fx.post(self.fx.client,'/api/v1/nutrition/journal',payload).status_code,422)
        self.assertEqual(self.fx.client.get('/api/v1/nutrition/journal?date_from=bad').status_code,422)
        self.assertEqual(self.fx.client.get('/api/v1/training/growth?days=999').status_code,422)
    def test_growth_uses_saved_food_days_and_does_not_create_ai_or_workouts(self):
        self.store.save(self.fx.user,self.fx.auth,self.payload(),s.new_id())
        self.remember('今天晚餐吃了西兰花和豆腐。')
        response=self.fx.client.get('/api/v1/training/growth?days=7')
        self.assertEqual(response.status_code,200,response.json);data=response.json['data']
        self.assertEqual(data['diet_days'],1);self.assertEqual(data['diet_records'],2);self.assertEqual(data['food_days']['vegetable'],1)
        self.assertEqual(len(data['daily']),7);self.assertEqual(data['analysis_count'],0)
        with self.fx.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.training_workouts)),0)
            self.assertEqual(c.scalar(select(func.count()).select_from(s.ai_requests)),0)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
    def test_schema13_migration_is_gated_preserves_rows_and_rolls_back(self):
        sid=self.fx.running();self.fx.finish(sid);legacy_shape(self.fx.db,13);before=rows_snapshot(self.fx.db)
        with self.assertRaises(StorageError):self.fx.db.initialize()
        with patch.object(s.personal_memories,'create',side_effect=RuntimeError('isolated migration failure')):
            with self.assertRaises(StorageError):self.fx.db.initialize(allow_round5_migration=True)
        self.assertEqual(self.fx.db.inspect_identity(allow_legacy=True)['schema_version'],13)
        self.assertEqual(self.fx.db.initialize(allow_round5_migration=True)['schema_version'],14)
        self.assertEqual(rows_snapshot(self.fx.db,before),before)
        self.assertEqual(self.fx.db.initialize()['schema_version'],14)

if __name__=='__main__':unittest.main()
