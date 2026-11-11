"""连续视频任务的内存释放；不重复真实模型推理。"""
import weakref
import unittest
from unittest.mock import patch
from sqlalchemy import update
from fitness_app import db_schema as s
import test_formal_video_jobs as video


class VideoRetentionTests(unittest.TestCase):
    def setUp(self):self.fx=video.VideoTests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()

    def test_completed_job_releases_analyzer_and_preserves_counting_metadata(self):
        refs=[]
        class Buffered(video.Events):
            counting_available=True
            def __init__(self):self.frames=[];refs.append(weakref.ref(self))
            def process(self,frame,context):
                self.frames.append(frame)
                return super().process(frame,context)
        fx=self.fx;fx.manager.engine_provider=lambda action:Buffered()
        sid=fx.video();jid=fx.start(sid);done=fx.wait(jid)
        self.assertEqual(done['session']['total_count'],3);self.assertTrue(done['counting_available'])
        rt=fx.manager.runs[jid]
        self.assertIsNone(rt.adapter);self.assertIsNone(rt.latest_status_frame);self.assertIsNone(rt.latest_skeleton_frame)
        self.assertEqual(rt.latest_issues,[]);self.assertIsNone(refs[0](),'已停止任务不应再持有分析器或其逐帧缓存')

    def test_bounded_runs_keep_historical_http_and_idempotent_start(self):
        fx=self.fx;ids=[]
        with patch('fitness_app.video_jobs.MAX_RETAINED_RUNS',2):
            for unused in range(3):
                sid=fx.video();key=s.new_id();jid=fx.start(sid,key)
                self.assertEqual(fx.wait(jid)['status'],'completed');ids.append((sid,jid,key))
        sid,jid,key=ids[0]
        self.assertNotIn(jid,fx.manager.runs);self.assertEqual(len(fx.manager.runs),2)
        response=fx.client.get('/api/v1/jobs/'+jid);self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['data']['session']['total_count'],3)
        self.assertEqual(response.json['data']['status'],'completed');self.assertIsNone(response.json['data']['processed_frames'])
        self.assertEqual(fx.start(sid,key),jid,'旧幂等请求应读取原任务，不能重新推理')
        self.assertEqual(len(fx.manager.runs),2)

    def test_pruning_never_discards_recovery_state_or_live_thread(self):
        fx=self.fx;sid=fx.video();jid=fx.start(sid);fx.wait(jid);rt=fx.manager.runs[jid]
        with patch('fitness_app.video_jobs.MAX_RETAINED_RUNS',1),fx.manager.lock:
            rt.cleanup_error=True;fx.manager._prune_finished();self.assertIn(jid,fx.manager.runs)
            rt.cleanup_error=False
            with patch.object(fx.manager.writer,'snapshot',return_value={'save_state':'save_failed'}):
                fx.manager._prune_finished();self.assertIn(jid,fx.manager.runs)
            with patch.object(rt.thread,'is_alive',return_value=True):
                fx.manager._prune_finished();self.assertIn(jid,fx.manager.runs)
            with fx.fx.db.transaction() as c:c.execute(update(s.jobs).where(s.jobs.c.id==jid).values(status='running'))
            fx.manager._prune_finished();self.assertIn(jid,fx.manager.runs)
            with fx.fx.db.transaction() as c:c.execute(update(s.jobs).where(s.jobs.c.id==jid).values(status='completed'))
            fx.manager._prune_finished();self.assertNotIn(jid,fx.manager.runs)

    def test_inference_failure_also_releases_analyzer_and_keeps_partial_record(self):
        fx=self.fx
        class Failed(video.SyntheticPose):
            def detect(self,rgb,stamp):
                if stamp==200:raise ValueError('D-RETENTION inference failure')
                return super().detect(rgb,stamp)
        fx.manager.pose_factory=Failed
        sid=fx.video();jid=fx.start(sid);done=fx.wait(jid)
        self.assertEqual(done['status'],'failed');self.assertEqual(done['session']['total_count'],1)
        self.assertIsNone(fx.manager.runs[jid].adapter);self.assertIsNone(fx.manager.runs[jid].latest_skeleton_frame)
        self.assertFalse(fx.media._path(fx.input_id).exists())

    def test_decoder_close_failure_does_not_skip_model_release(self):
        fx=self.fx;closed=[]
        class Model(video.SyntheticPose):
            def close(self):closed.append('pose')
        original=fx.manager.frames
        class Decoder:
            def __init__(self,path,info):self.iterator=original(path,info)
            def __iter__(self):return self
            def __next__(self):return next(self.iterator)
            def close(self):
                self.iterator.close();closed.append('decoder')
                raise OSError('D-RETENTION decoder close')
        fx.manager.pose_factory=Model;fx.manager.frames=Decoder
        sid=fx.video();jid=fx.start(sid);done=fx.wait(jid)
        self.assertEqual(closed,['decoder','pose'])
        self.assertEqual(done['status'],'failed');self.assertEqual(done['error_code'],'VIDEO_RELEASE_FAILED')
        self.assertEqual(done['session']['total_count'],3)
        self.assertIsNone(fx.manager.runs[jid].adapter)


if __name__=='__main__':unittest.main()
