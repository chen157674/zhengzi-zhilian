"""发布范围、目录和旧引用失效；不请求模型或供应商。"""
import unittest
from unittest.mock import patch
import test_formal_records as shared
from fitness_app.knowledge_store import KnowledgeStore

class KnowledgePublicationTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.store=KnowledgeStore(index_dir='.tmp/nonexistent-knowledge-index')
        self.fx.app.extensions['knowledge_store']=self.store
    def tearDown(self):self.fx.tearDown()
    def test_withdrawal_excludes_new_search_and_never_returns_body(self):
        row=self.store.rows[0];row['status']='withdrawn'
        self.assertNotIn(row['id'],[r['id'] for r in self.store.search(row['title'],'movement',row['action_id'])['matches']])
        item=self.store.published(row['id']);self.assertEqual(item['status'],'withdrawn');self.assertNotIn('text',item)
    def test_version_unavailable_does_not_substitute_current_body(self):
        item=self.store.published(self.store.rows[0]['id'],'old-version')
        self.assertEqual(item['status'],'version_unavailable');self.assertNotIn('text',item)
    def test_catalog_counts_only_published_and_examples_match_current_coverage(self):
        self.store.rows[0]['status']='withdrawn';catalog=self.store.catalog()
        self.assertEqual(sum(x['available_count'] for x in catalog['domains']),49)
        self.assertTrue(all(x['query']!=self.store.rows[0]['title'] for x in catalog['example_queries']))
        result=self.store.retrieval('推胸手腕','movement','machine_chest_press')
        self.assertTrue(result['retrieval_id']);self.assertGreater(result['expires_at_ms'],0)
        self.assertTrue(all(x['source_status']=='published' and x['support_scope']['domain']=='movement' for x in result['matches']))
    def test_cold_semantic_encoder_does_not_block_keyword_query(self):
        import threading,time
        self.store.vectors=object();entered=threading.Event();release=threading.Event()
        def blocked(texts,query=False):entered.set();release.wait(3);self.store.encoder=('ready',);return []
        self.store.encode=blocked
        started=time.monotonic();result=self.store.search('推胸手腕','movement','machine_chest_press')
        self.assertLess(time.monotonic()-started,.5);self.assertTrue(result['matches']);self.assertEqual(result['retrieval_mode'],'keyword_warming_up')
        self.assertTrue(entered.wait(1));started=time.monotonic();again=self.store.retrieval('推胸回程','movement','machine_chest_press');self.assertLess(time.monotonic()-started,.5);self.assertTrue(again['matches']);release.set()

    def test_guide_owned_auth_and_limited_action(self):
        client=self.fx.client
        self.assertEqual(self.fx.app.test_client().get('/api/v1/knowledge/catalog').status_code,401)
        result=client.get('/api/v1/knowledge/catalog');self.assertEqual(result.status_code,200);self.assertEqual(result.headers['Cache-Control'],'no-store')
        response=client.get('/api/v1/actions/standard_push_up/guide');self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['data']['knowledge']['coverage'],'limited')
        self.assertEqual(client.get('/api/v1/actions/not-real/guide').status_code,404)

if __name__=='__main__':unittest.main()
