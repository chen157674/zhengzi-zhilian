"""独立知识页真实模板/鉴权/来源撤回，不访问AI。"""
import unittest
import test_formal_records as shared

class KnowledgePagesTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()
    def test_private_routes_and_templates(self):
        for route in ['/coach/knowledge','/coach/knowledge?view=actions','/actions/machine_chest_press']:
            self.assertEqual(self.fx.app.test_client().get(route).status_code,302)
            response=self.fx.client.get(route);self.assertEqual(response.status_code,200,response.get_data(as_text=True))
            html=response.get_data(as_text=True);self.assertIn('健身指南',html);self.assertEqual(html.count('id="ai-assistant"'),1)
    def test_first_lessons_are_in_html_without_waiting_for_a_catalogue_request(self):
        response=self.fx.client.get('/coach/knowledge')
        html=response.get_data(as_text=True)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertIn('id="learn-intro"',html)
        self.assertEqual(html.count('class="learn-next-lesson"'),2)
        directory=self.fx.client.get('/coach/knowledge?view=actions').get_data(as_text=True)
        self.assertEqual(directory.count('class="learn-lesson-card guide-action-card"'),6)
        self.assertIn('id="knowledge-catalog-data"',html)
        self.assertIn('/static/bundle/',html)
        self.assertNotIn('src="/static/knowledge_query.js"',html)
        self.assertNotIn('k:lesson:lat_pulldown',html)  # 启动数据是公开目录，不包含条目正文。
    def test_source_version_withdrawal_and_missing_do_not_leak_body(self):
        store=self.fx.app.extensions['knowledge_store'];row=store.rows[0];route='/coach/knowledge/'+row['id']
        response=self.fx.client.get(route);self.assertEqual(response.status_code,200);self.assertIn(row['text'],response.get_data(as_text=True))
        row['status']='withdrawn';response=self.fx.client.get(route);self.assertEqual(response.status_code,410);self.assertNotIn(row['text'],response.get_data(as_text=True))
        self.assertEqual(self.fx.client.get('/coach/knowledge/missing').status_code,404)
        self.assertEqual(self.fx.client.get('/actions/missing').status_code,404)

if __name__=='__main__':unittest.main()
