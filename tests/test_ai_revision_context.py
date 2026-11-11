"""AI冻结快照需区分正常追加与视频发布/替代；只使用隔离存储。"""
import copy
import unittest
from unittest.mock import patch

from fitness_app.ai_context import AIContextBuilder
from fitness_app import db_schema as s
import test_video_analysis_store as video_fixture
import test_ai_jobs as job_fixture
from fitness_app.video_analysis_store import VideoAnalysisStore


class AIRevisionContextTests(unittest.TestCase):
    def setUp(self):
        self.fixture = video_fixture.VideoAnalysisStoreTests()
        self.fixture.setUp()
        self.fx = self.fixture.fx
        self.builder = AIContextBuilder(self.fx.app)

    def tearDown(self):
        self.fixture.tearDown()

    def bundle(self, sid):
        return self.builder.build(self.fx.user, self.fx.auth, {
            'context_kind': 'record', 'context_id': sid,
            'history_scope': 'none', 'text': '这一段应该怎样调整？'})

    def current(self, reference):
        return self.builder.current(self.fx.user, self.fx.auth, reference)

    def test_running_video_context_is_invalidated_when_result_becomes_current(self):
        parent = self.fixture.parent(('conventional_deadlift',))
        revision = self.fixture.revision(parent)
        sid = self.fixture.store.child(self.fx.user, self.fx.auth, revision)['session_id']
        self.fx.store.set_state(self.fx.user, sid, 'running', s.new_id())
        reference = self.bundle(sid)['reference']
        self.assertTrue(self.current(reference))
        self.fx.store.set_state(self.fx.user, sid, 'completed', s.new_id())
        # 结束本身允许解释提问时的冻结快照；发布资格变化须重新读取。
        self.assertTrue(self.current(reference))
        self.fixture.store.promote(self.fx.user, self.fx.auth, revision)
        self.assertFalse(self.current(reference))
        refreshed = self.bundle(sid)
        self.assertTrue(refreshed['facts']['selected_records'][0]['included_in_statistics'])
        self.assertTrue(self.current(refreshed['reference']))

    def test_replaced_running_snapshot_cannot_masquerade_as_current_same_action(self):
        parent = self.fixture.parent(('conventional_deadlift',))
        first = self.fixture.revision(parent)
        old_sid = self.fixture.store.child(self.fx.user, self.fx.auth, first)['session_id']
        self.fx.store.set_state(self.fx.user, old_sid, 'running', s.new_id())
        reference = self.bundle(old_sid)['reference']
        self.fx.store.set_state(self.fx.user, old_sid, 'completed', s.new_id())
        self.fixture.store.promote(self.fx.user, self.fx.auth, first)
        self.fixture.store.finish(self.fx.user, parent['id'], 'completed')
        second = self.fixture.revision(parent, automatic=False)
        new_sid = self.fixture.complete(second)
        self.assertNotEqual(old_sid, new_sid)
        self.assertFalse(self.current(reference))
        historical = self.bundle(old_sid)
        self.assertEqual(historical['facts']['selected_records'][0]['superseded_by'], new_sid)
        # 主动打开的旧记录仍可解释，但须明确是历史修订。
        self.assertTrue(self.current(historical['reference']))

    def test_failed_or_cancelled_revision_preserves_current_record_reference(self):
        parent = self.fixture.parent(('conventional_deadlift',))
        sid = self.fixture.complete(self.fixture.revision(parent))
        self.fixture.store.finish(self.fx.user, parent['id'], 'completed')
        reference = self.bundle(sid)['reference']
        for status in ('failed', 'cancelled'):
            revision = self.fixture.revision(parent, automatic=False)
            self.fixture.store.fail_revision(self.fx.user, revision, status, 'fixture-stop')
            self.assertTrue(self.current(reference))

    def test_ordinary_active_record_append_does_not_invalidate_frozen_context(self):
        sid = self.fx.running()
        bundle = self.bundle(sid)
        self.fx.store.append_event(self.fx.user, sid, self.fx.event(), self.fx.auth)
        self.assertTrue(self.current(bundle['reference']))
        self.assertEqual(bundle['facts']['selected_records'][0]['completion']['reference_count'], 0)

    def test_legacy_reference_without_revision_binding_is_not_current(self):
        sid = self.fx.running()
        reference = copy.deepcopy(self.bundle(sid)['reference'])
        reference['version'] = 'training-evidence-context-1'
        for dependency in reference['dependencies']:
            dependency.pop('record_binding', None)
        self.assertFalse(self.current(reference))


class AIRevisionRequestTests(unittest.TestCase):
    def setUp(self):
        self.fixture = job_fixture.AIJobsTests()
        self.fixture.setUp()
        self.fx = self.fixture.fx
        self.video = video_fixture.VideoAnalysisStoreTests()
        self.video.fx = self.fx
        self.video.uid, self.video.auth = self.fx.user, self.fx.auth
        self.video.store = VideoAnalysisStore(self.fx.store, self.fx.app.extensions['media_inputs'])
        parent = self.video.parent(('conventional_deadlift',))
        self.revision = self.video.revision(parent)
        self.sid = self.video.store.child(self.fx.user, self.fx.auth, self.revision)['session_id']
        self.fx.store.set_state(self.fx.user, self.sid, 'running', s.new_id())
        response = self.fixture.post('/api/v1/ai/conversations', {
            'context_kind': 'record', 'context_id': self.sid, 'history_scope': 'none'})
        self.assertEqual(response.status_code, 201, response.json)
        self.conversation = response.json['data']

    def tearDown(self):
        self.fixture.tearDown()

    def publish(self):
        self.fx.store.set_state(self.fx.user, self.sid, 'completed', s.new_id())
        self.video.store.promote(self.fx.user, self.fx.auth, self.revision)

    def test_queued_request_does_not_call_provider_after_publication_changes(self):
        manager = self.fixture.manager
        with patch.object(manager, '_start_workers'):
            response = self.fixture.send(self.conversation)
        self.assertEqual(response.status_code, 202, response.json)
        rid = response.json['data']['request_id']
        self.publish()
        work = manager.jobs.get_nowait()
        try:
            manager._execute(*work)
        finally:
            manager.jobs.task_done()
        result = manager.snapshot(self.fx.user, self.fx.auth, rid)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'AI_CONTEXT_CHANGED')
        self.assertEqual(result['estimated_micro_cny'], 0)
        self.assertEqual(self.fixture.provider.calls, 0)
        self.assertIsNone(result['result'])
        self.assertFalse(result['context_current'])

    def test_publication_during_generation_hides_old_answer_but_keeps_usage(self):
        generate = self.fixture.provider.generate

        def publishing_provider(*args):
            self.publish()
            return generate(*args)

        with patch.object(self.fixture.provider, 'generate', side_effect=publishing_provider):
            response = self.fixture.send(self.conversation)
            self.assertEqual(response.status_code, 202, response.json)
            result = self.fixture.wait(response.json['data']['request_id'])
        self.assertEqual(self.fixture.provider.calls, 1)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'AI_CONTEXT_CHANGED')
        self.assertEqual(result['estimated_micro_cny'], 140)
        self.assertEqual(result['usage'], {'prompt_tokens': 30, 'completion_tokens': 10})
        self.assertIsNone(result['result'])
        self.assertFalse(result['context_current'])
        current = self.fixture.manager.conversation(self.fx.user, self.fx.auth, self.conversation['conversation_id'])
        self.assertFalse(current['context_current'])
        self.assertEqual(current['messages'], [])


if __name__ == '__main__':
    unittest.main()
