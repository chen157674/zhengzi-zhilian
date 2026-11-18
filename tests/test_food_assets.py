"""食物图按名称匹配、公开知识边界与真实素材身份。"""
import json
import unittest
from fitness_app.food_assets import food_asset_for,published_food_assets,food_catalog
from fitness_app.diet_journal import identify_foods
from fitness_app.knowledge_store import KnowledgeStore
from fitness_app.nutrition_analysis import ANALYSIS_PROMPT
import test_formal_records as shared

class FoodAssetsTests(unittest.TestCase):
    def test_all_reviewed_local_assets_and_specific_names(self):
        published=published_food_assets();self.assertEqual(len(published['assets']),17)
        self.assertEqual(len({x['id'] for x in published['assets']}),17)
        for name,identifier in [('泡面','instant-noodles'),('方便面','instant-noodles'),('牛肉汉堡','hamburger'),('炸鸡汉堡','hamburger'),('牛肉','lean-beef'),('炸鸡','fried-chicken'),('薯条','french-fries'),('奶茶','milk-tea'),('无糖可乐','soda'),('米饭','rice'),('鸡蛋','eggs'),('清蒸鱼','fish'),('豆腐','tofu'),('鸡胸肉','chicken-breast')]:
            self.assertEqual(food_asset_for(name)['id'],identifier,name)
        self.assertIsNone(food_asset_for('素食汉堡','other'))
        self.assertIsNone(food_asset_for('牛肉干','other'))
        self.assertIsNone(food_asset_for('从未收录的菜','other'))
        self.assertIsNone(food_asset_for('从未收录的主食','staple'))
        self.assertTrue(all(x['url'].startswith('/static/food/') and '..' not in x['url'] for x in published['assets']))
    def test_composite_food_name_is_not_split_into_unspoken_beef(self):
        foods=identify_foods('牛肉汉堡、泡面、奶茶、薯条')
        self.assertEqual({x['name'] for x in foods},{'牛肉汉堡','泡面','奶茶','薯条'})
    def test_retrieval_has_specific_food_choices_and_beef_is_not_a_ban(self):
        store=KnowledgeStore(index_dir='.tmp/nonexistent-knowledge-index')
        for query,slug in [('泡面方便面当正餐','instant_noodles_meal'),('汉堡套餐怎么搭配','burger_meal_choice'),('牛肉牛排增肌怎么吃','lean_beef_variety'),('炸鸡下一餐怎么吃','fried_chicken_choice'),('薯条天天吃','fries_choice'),('奶茶甜度频次','milk_tea_choice')]:
            self.assertIn('k:nutrition:'+slug,[x['id'] for x in store.search(query,'nutrition')['matches']],query)
        beef=store.by_id['k:nutrition:lean_beef_variety']
        self.assertIn('瘦牛肉',beef['text']);self.assertIn('轮换',beef['text']);self.assertEqual(beef['source_id'],'R_MEAT')
        self.assertIn('瘦牛肉',ANALYSIS_PROMPT);self.assertIn('无糖',ANALYSIS_PROMPT)
        self.assertNotIn('不能吃牛肉',beef['text'])
    def test_asset_catalogue_is_authenticated_and_no_store(self):
        fx=shared.RecordTests();fx.setUp()
        try:
            self.assertEqual(fx.app.test_client().get('/api/v1/nutrition/food-assets').status_code,401)
            response=fx.client.get('/api/v1/nutrition/food-assets');self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['Cache-Control'],'no-store');self.assertEqual(len(response.json['data']['assets']),17)
            self.assertNotIn('prompt',json.dumps(response.json));self.assertNotIn('sha256',json.dumps(response.json))
        finally:fx.tearDown()

if __name__=='__main__':unittest.main()
