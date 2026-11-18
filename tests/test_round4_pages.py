"""第四轮公共页面与素材边界；使用原有隔离数据库，不连接真实AI。"""
import unittest
from urllib.parse import quote
from unittest.mock import patch
from html.parser import HTMLParser
from flask import render_template

import test_formal_records as shared
from fitness_app.pages import ROUND4_ASSETS
from fitness_app.paths import ROOT


class Round4PagesTests(unittest.TestCase):
    def setUp(self):
        self.fx = shared.RecordTests()
        self.fx.setUp()

    def tearDown(self):
        self.fx.tearDown()

    def test_coach_requires_login_and_renders_one_shared_component(self):
        anonymous = self.fx.app.test_client()
        self.assertEqual(anonymous.get('/coach').status_code, 302)
        response = self.fx.client.get('/coach')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertEqual(html.count('id="ai-assistant"'), 1)
        self.assertEqual(html.count('id="ai-chat-form"'), 1)
        self.assertIn('data-mode="page"', html)
        self.assertNotIn('class="active" aria-current="page" data-nav><svg', html)
        self.assertIn('id="ai-send-frame"', html)
        self.assertNotIn('id="ai-send-frame" checked', html)
        self.assertNotIn('data-ai-record-id=', html)

    def test_existing_pages_keep_one_closed_assistant_and_settings_no_main_selection(self):
        for route in ['/realtime', '/video', '/records', '/account', '/settings']:
            with self.subTest(route=route):
                html = self.fx.client.get(route).get_data(as_text=True)
                self.assertEqual(html.count('id="ai-assistant"'), 1)
                self.assertIn('data-mode="panel" aria-labelledby="ai-assistant-title" hidden', html)
                self.assertIn('href="/coach"', html)
                if route in ['/settings', '/account']:
                    self.assertNotIn('aria-current="page" data-nav><svg', html)

    def test_public_auth_has_only_auth_navigation(self):
        anonymous = self.fx.app.test_client()
        for route in ['/login', '/register']:
            with self.subTest(route=route):
                html = anonymous.get(route).get_data(as_text=True)
                self.assertIn('class="auth-form-brand"', html)
                self.assertIn('class="auth-switch"', html)
                self.assertIn('name="email"', html)
                self.assertNotIn('username-help', html)
                self.assertNotIn('aria-label="主要导航"', html)
                self.assertNotIn('id="ai-assistant"', html)
                self.assertIn('alt="正姿智练"', html)

    def test_coach_context_is_selected_from_owned_records(self):
        sid = self.fx.running()
        self.fx.finish(sid)
        response = self.fx.client.get('/coach?record=' + sid)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-record-id="' + sid + '"', html)
        self.assertIn('id="coach-context-record"', html)
        self.assertIn('value="' + sid + '" selected', html)
        self.assertNotIn('context_label', self.fx.client.get('/api/v1/records/' + sid).json['data'])

    def test_coach_context_cannot_read_another_users_record(self):
        sid = self.fx.running()
        self.fx.finish(sid)
        other = self.fx.app.test_client()
        self.fx.login(other, 'Other_User')
        response = other.get('/coach?record=' + sid)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('data-record-id="' + sid + '"', response.get_data(as_text=True))

    def test_saved_video_first_html_does_not_show_upload_before_scripts(self):
        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.nodes = {}
            def handle_starttag(self, tag, attrs):
                data = dict(attrs)
                if data.get('id'):
                    self.nodes[data['id']] = data
        marker = '11111111-1111-4111-8111-111111111111'
        cases = [
            ('/video/analyses/' + marker, 'video_auto.html', {'analysis_id': marker}, 'video-auto-page'),
            ('/video/jobs/' + marker, 'video.html', {'job_id': marker}, 'video-page'),
            ('/video?demo=1', 'video.html', {'job_id': ''}, 'video-page'),
        ]
        for path, template, values, root_id in cases:
            with self.subTest(path=path), self.fx.app.test_request_context(path):
                html = render_template(template, actions=[], demos=[], demo=None, action=None, **values)
                parsed = Elements()
                parsed.feed(html)
                self.assertIn('hidden', parsed.nodes['video-choices'])
                self.assertNotIn('hidden', parsed.nodes['video-restore-state'])
                self.assertEqual(parsed.nodes[root_id]['data-stage'], 'loading')

    def test_public_asset_whitelist_mime_and_cache(self):
        anonymous = self.fx.app.test_client()
        for name, mime in ROUND4_ASSETS.items():
            with self.subTest(name=name):
                url = '/static/round4/' + quote(name)
                with anonymous.get(url) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.mimetype, mime)
                    self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                    expected = ROOT / 'fitness_app' / 'static' / 'brand' / name
                    if not expected.exists():
                        expected = ROOT / '第四轮优化' / '开发素材' / name
                    self.assertEqual(response.get_data(), expected.read_bytes())
                    etag = response.headers['ETag']
                with anonymous.get(url, headers={'If-None-Match': etag}) as cached:
                    self.assertEqual(cached.status_code, 304)
        for name in ['PRD.md', '../PRD.md', '../../PRD.md', '登录页.png']:
            self.assertEqual(anonymous.get('/static/round4/' + quote(name, safe='')).status_code, 404)

    def test_resolved_asset_must_stay_in_asset_directory(self):
        directory = ROOT / 'fitness_app' / 'static' / 'brand'
        outside_asset_directory = ROOT / 'PRD.md'
        with patch('fitness_app.pages.project_path',
                   side_effect=[directory, outside_asset_directory]):
            response = self.fx.app.test_client().get('/static/round4/' + quote('知形标志.svg'))
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
