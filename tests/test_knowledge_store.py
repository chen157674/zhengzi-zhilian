"""领域和来源过滤，不以小集结果替代真实效果验收。"""
import unittest
from fitness_app.knowledge_store import KnowledgeStore,entries

class KnowledgeTests(unittest.TestCase):
    def setUp(self):self.store=KnowledgeStore(index_dir='.tmp/nonexistent-knowledge-index')
    def test_entries_have_real_sources_and_unique_hashes(self):
        rows=entries();self.assertEqual(len(rows),170);self.assertEqual(len({x['id'] for x in rows}),170)
        self.assertTrue(all(x['source']['reuse'] and x['locator'] and len(x['sha256'])==64 for x in rows))
        self.assertEqual(sum(x['domain']=='movement' for x in rows),60)
    def test_domain_action_and_abstention_boundaries(self):
        value=self.store.search('推胸手腕','movement','machine_chest_press')
        self.assertTrue(value['matches']);self.assertTrue(all(x['action_id']=='machine_chest_press' for x in value['matches']))
        self.assertEqual(self.store.search('深蹲幅度','movement','machine_chest_press')['matches'],[])
        self.assertEqual(self.store.search('肾病蛋白处方','nutrition')['matches'],[])
        self.assertEqual(self.store.search('火星发动机','nutrition')['matches'],[])
    def test_runtime_hash_check_excludes_readme_but_never_accepts_changed_weights(self):
        import tempfile,hashlib
        from pathlib import Path
        from fitness_app.paths import ROOT
        from fitness_app.knowledge_store import MODEL_RUNTIME_FILES,verify_encoder_files
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp/tests') as folder:
            root=Path(folder);hashes={}
            for name in MODEL_RUNTIME_FILES:
                value=('isolated-'+name).encode();(root/name).write_bytes(value);hashes[name]=hashlib.sha256(value).hexdigest()
            hashes['README.md']='0'*64;(root/'README.md').write_text('改变说明不改变权重',encoding='utf-8')
            verify_encoder_files(root,hashes)
            (root/'model.safetensors').write_bytes(b'changed')
            with self.assertRaises(ValueError):verify_encoder_files(root,hashes)

    def test_all_topics_searches_public_sources_and_honors_withdrawal(self):
        for query in ['推胸回程控制','蛋白质来源','增肌恢复']:
            result=self.store.search(query,'all');self.assertTrue(result['matches'],query)
            self.assertTrue(all(x['domain'] in {'movement','nutrition','hypertrophy'} for x in result['matches']))
        for row in self.store.by_id.values():
            if row['domain']=='nutrition':row['status']='withdrawn'
        self.assertTrue(all(x['domain']!='nutrition' for x in self.store.search('蛋白质来源','all')['matches']))
        self.assertEqual(self.store.search('肾病蛋白处方','all')['matches'],[])
        with self.assertRaises(ValueError):self.store.search('深蹲','all','bodyweight_squat')

    def test_all_catalogue_actions_have_specific_editorial_instruction_and_match(self):
        from fitness_app.catalogue import definitions
        actions=definitions()
        for action in actions:
            rows=self.store.search(action['name']+'怎么练','all')['matches']
            self.assertTrue(rows,action['name'])
            self.assertEqual(rows[0]['action_id'],action['action_id'],action['name'])
        lessons=[r for r in self.store.rows if r['source_type']=='project_tutorial']
        self.assertEqual(len(lessons),30)
        for row in lessons:
            self.assertTrue(row['id'].startswith('k:lesson:'))
            self.assertIsNone(row['source']['url'])
            self.assertIn('本项目原创',row['source']['reuse'])
        self.assertEqual(self.store.search('深蹲幅度','movement','machine_chest_press')['matches'],[])

    def test_common_beginner_short_names_match_the_expected_variants(self):
        queries={'俯卧撑怎么做':'standard_push_up','平板支撑怎么做':'forearm_plank','臀桥怎么做':'bodyweight_glute_bridge','高抬腿怎么做':'high_knees','锤式弯举怎么做':'hammer_curl','高位下拉怎么做':'lat_pulldown','坐姿划船怎么做':'seated_cable_row','面拉怎么做':'cable_face_pull'}
        for text,action in queries.items():self.assertEqual(self.store.search(text,'all')['matches'][0]['action_id'],action,text)

    def test_food_guide_topics_match_and_remain_nutrition_only(self):
        for query in ['食堂午餐怎么搭配','西兰花可以换什么','早餐牛奶鸡蛋怎么搭','训练后蛋白质食物怎么选']:
            result=self.store.search(query,'nutrition');self.assertTrue(result['matches'],query)
            self.assertTrue(all(row['domain']=='nutrition' for row in result['matches']))
        row=self.store.by_id['k:food:vegetables'];row['status']='withdrawn'
        self.assertNotIn(row['id'],[x['id'] for x in self.store.search('西兰花蔬菜怎么换','nutrition')['matches']])

    def test_researched_nutrition_sources_scope_and_withdrawal(self):
        rows=[r for r in self.store.rows if r['id'].startswith('k:nutrition:')]
        self.assertEqual(len(rows),84);self.assertEqual(len({r['source_id'] for r in rows}),14)
        self.assertTrue(all(r['applicability'] and r['locator'] and r['source']['checked_on'] in {'2026-11-11','2026-11-12'} for r in rows))
        for query,expected in [('一份就是一包吗','serving_package'),('肌酸冲击期必须吗','creatine_loading'),('乳糖不耐受还能喝奶吗','lactose_not_all'),('咖啡氮泵能量饮料一起喝','caffeine_total')]:
            matches=self.store.search(query,'nutrition')['matches'];self.assertIn('k:nutrition:'+expected,[r['id'] for r in matches],query)
        self.store.by_id['k:nutrition:creatine_loading']['status']='withdrawn'
        self.assertNotIn('k:nutrition:creatine_loading',[r['id'] for r in self.store.search('肌酸冲击期必须吗','nutrition')['matches']])
        self.assertEqual(self.store.search('肾病蛋白处方','nutrition')['matches'],[])

    def test_verified_bibliography_has_journals_and_web_sources(self):
        refs=self.store.references();self.assertEqual(len(refs),14)
        self.assertEqual(sum(r['reference_type']=='journal' for r in refs),4)
        self.assertTrue(all(r['citation'] and r['reference_id'] for r in refs))
        article=next(r for r in refs if r['source_id']=='R_PROTEIN');self.assertEqual(article['year'],2017);self.assertEqual(article['article_number'],'20')
        for row in self.store.by_id.values():
            if row['source_id']=='R_CREATINE':row['status']='withdrawn'
        self.assertNotIn('R_CREATINE',[r['source_id'] for r in self.store.references()])

    def test_invalid_fields_rejected(self):
        for query,domain,action in [('x',[],None),('x','movement',[]),('x','movement','unknown'),('', 'nutrition',None)]:
            with self.assertRaises(ValueError):self.store.search(query,domain,action)

if __name__=='__main__':unittest.main()
