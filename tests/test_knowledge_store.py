"""领域和来源过滤，不以小集结果替代真实效果验收。"""
import unittest
from fitness_app.knowledge_store import KnowledgeStore,entries

class KnowledgeTests(unittest.TestCase):
    def setUp(self):self.store=KnowledgeStore(index_dir='.tmp/nonexistent-knowledge-index')
    def test_entries_have_real_sources_and_unique_hashes(self):
        rows=entries();self.assertEqual(len(rows),50);self.assertEqual(len({x['id'] for x in rows}),50)
        self.assertTrue(all(x['source']['reuse'] and x['locator'] and len(x['sha256'])==64 for x in rows))
        self.assertEqual(sum(x['domain']=='movement' for x in rows),30)
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

    def test_invalid_fields_rejected(self):
        for query,domain,action in [('x',[],None),('x','movement',[]),('x','movement','unknown'),('', 'nutrition',None)]:
            with self.assertRaises(ValueError):self.store.search(query,domain,action)

if __name__=='__main__':unittest.main()
