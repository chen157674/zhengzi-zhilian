import copy
import json
import unittest
from unittest.mock import patch

import test_formal_records as shared
from fitness_app.presentation_assets import demo_resource,MANIFEST,_read


class DemoShowcaseTests(unittest.TestCase):
    def test_published_video_analysis_identity_and_result_conclusion(self):
        fx=shared.RecordTests();fx.setUp()
        try:
            demos=fx.client.get('/api/v1/demos').json['data']['demos']
            for demo in demos:
                if not demo['saved_result_url']:continue
                with self.subTest(demo=demo['demo_id']):
                    response=fx.client.get(demo['analysis_track_url'])
                    track=response.json;response.close()
                    self.assertEqual(track['source_sha256'],demo['source_sha256'])
                    self.assertEqual(track['action_id'],demo['action_id'])
                    page=fx.client.get(demo['saved_result_url'])
                    self.assertEqual(page.status_code,200)
                    self.assertIn('本次分析结论',page.text)
                    self.assertIn('分析完成 · 按时间回看动作反馈',page.text)
                    self.assertNotIn('已保存结果 · 按保存版本回看检查项',page.text)
                    self.assertNotIn('复核',page.text)
                    self.assertNotIn('倍速',page.text)
                    if 'data-review-seek' in page.text:
                        self.assertIn('片段来源',page.text)
                        self.assertIn('用户标记',page.text)
                    if demo['demo_id']=='user-1688':
                        self.assertIn('身体向左侧持续倾斜',page.text)
                        self.assertRegex(page.text,r'data-initial-ms="[1-9]\d*"')
            self.assertEqual(fx.store.list_records(fx.user)['total'],0)
        finally:
            fx.app.extensions['video_jobs'].stop();fx.app.extensions['session_manager'].stop()
            fx.app.extensions['preview_manager'].stop();fx.app.extensions['media_inputs'].stop();fx.tearDown()

    def test_registered_public_input_and_trace_identity_and_bad_resource_rejection(self):
        item=demo_resource('barbell-row');self.assertIsNotNone(item);self.assertEqual(item['frames'],855)
        self.assertIsNone(demo_resource('private-input'))
        stat=MANIFEST.stat();bad=copy.deepcopy(_read(stat.st_mtime_ns,stat.st_size));bad['demos']['barbell-row']['video']['path']='data/app.sqlite3'
        with patch('fitness_app.presentation_assets._read',return_value=bad):self.assertIsNone(demo_resource('barbell-row'))
        bad=copy.deepcopy(_read(stat.st_mtime_ns,stat.st_size));bad['demos']['barbell-row']['track']['sha256']='0'*64
        with patch('fitness_app.presentation_assets._read',return_value=bad):self.assertIsNone(demo_resource('barbell-row'))

    def test_anonymous_showcase_range_read_and_track_do_not_create_training(self):
        fx=shared.RecordTests();fx.setUp()
        try:
            client=fx.app.test_client();item=demo_resource('barbell-row')
            response=client.get(item['video_url'],headers={'Range':'bytes=0-99'})
            self.assertEqual(response.status_code,206);self.assertEqual(len(response.data),100);self.assertIn('bytes 0-99/',response.headers['Content-Range']);response.close()
            response=client.get(item['track_url']);track=response.json;response.close()
            self.assertEqual(track['source_sha256'],item['video']['sha256']);self.assertEqual(len(track['frames']),855);self.assertNotIn('request_id',track)
            self.assertEqual(len(track['events']),3);self.assertEqual(client.get('/static/demo/private/video').status_code,404)
            self.assertEqual(client.get('/static/demo/barbell-row/private').status_code,404)
            self.assertIn('id="showcase-feedback"',client.get('/').text)
            self.assertIn('request_id',client.get('/api/v1/health').json)
            self.assertEqual(fx.store.list_records(fx.user)['total'],0)
        finally:
            fx.app.extensions['video_jobs'].stop();fx.app.extensions['session_manager'].stop();fx.app.extensions['preview_manager'].stop();fx.app.extensions['media_inputs'].stop();fx.tearDown()


if __name__=='__main__':unittest.main()
