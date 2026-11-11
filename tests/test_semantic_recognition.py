"""验证异步队列、隐私释放与目标隔离，不将模拟窗口当准确率证据。"""
import threading
import time
import unittest
from unittest.mock import patch,Mock
import numpy as np
from fitness_app.semantic_recognition import SemanticRecognizer,create_recognizer


class Provider:
    def __init__(self):self.entered=threading.Event();self.release=threading.Event();self.done=threading.Event();self.calls=0
    def predict(self,images,times,arrays):
        self.calls+=1;self.entered.set()
        if not self.release.wait(3):raise RuntimeError('测试未释放工作线程')
        self.done.set()
        return {'action_id':'bodyweight_squat','score':.9,'reason':None,'decision_basis':'test'}


class SemanticRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.provider=Provider();self.model=SemanticRecognizer(lambda:self.provider,info={'version':'test'})
        self.rgb=np.zeros((24,32,3),np.uint8)
    def tearDown(self):
        self.model.close();self.provider.release.set()
        if self.model.thread:self.model.thread.join(timeout=4)

    def frame(self,stamp,people=1):
        points=[{'x':.5,'y':.5,'z':0.,'visibility':1.,'presence':1.,'valid':True} for _ in range(33)]
        for i,x,y in ((11,.4,.3),(12,.6,.3),(23,.42,.65),(24,.58,.65)):points[i].update(x=x,y=y)
        return {'captured_ms':stamp,'frame_seq':stamp//100,'width':32,'height':24,'framing':{'landmarks':points,'person_count':people,'code':'VISIBLE'}}
    def process(self,stamp,people=1):
        return self.model.process(self.frame(stamp,people),self.model.extract(self.rgb,stamp))
    def start_job(self):
        for stamp in range(0,3600,100):self.process(stamp)
        self.assertTrue(self.provider.entered.wait(1))

    def test_preparation_loads_without_frames_and_closed_late_warm_cannot_be_ready(self):
        entered=threading.Event();release=threading.Event()
        class Warming:
            def warm(self):entered.set();release.wait(2)
        model=SemanticRecognizer(lambda:Warming(),info={'version':'test'})
        try:
            self.assertEqual(model.preparation()['state'],'loading')
            model.prepare();self.assertTrue(entered.wait(1))
            self.assertEqual(len(model.images),0);self.assertIsNone(model.job)
            model.close();release.set();model.thread.join(1)
            self.assertFalse(model.model_ready)
            self.assertEqual(model.preparation()['state'],'unavailable')
        finally:model.close();release.set()

    def test_preparation_becomes_ready_without_publishing_an_action(self):
        model=SemanticRecognizer(lambda:object(),info={'version':'test'})
        try:
            model.prepare()
            for _ in range(100):
                if model.preparation()['state']=='ready':break
                time.sleep(.005)
            self.assertEqual(model.preparation()['state'],'ready')
            self.assertIsNone(model.snapshot()['action_id']);self.assertIsNone(model.completed)
        finally:model.close();model.thread.join(1)

    def test_slow_gpu_does_not_block_frame_processing_or_make_unbounded_queue(self):
        self.start_job();began=time.perf_counter()
        for stamp in range(3600,8000,100):self.process(stamp)
        self.assertLess(time.perf_counter()-began,.5)
        self.assertLessEqual(len(self.model.images),24);self.assertLessEqual(len(self.model.rows),100)
        self.assertIsNotNone(self.model.job);self.assertEqual(self.provider.calls,1)

    def test_slow_worker_keeps_only_latest_source_window_in_pending_slot(self):
        self.start_job()
        for stamp in range(3600,5200,100):self.process(stamp)
        self.assertTrue(self.model.inference_active)
        self.assertIsNotNone(self.model.job)
        self.assertLessEqual(5100-self.model.job[1],200)
        self.assertEqual(self.provider.calls,1)
        self.assertLessEqual(len(self.model.images),24)

    def test_multiple_people_discard_inflight_result_and_rgb(self):
        self.start_job();result=self.process(3600,2)
        self.assertIsNone(result['action_id']);self.assertEqual(len(self.model.images),0)
        self.provider.release.set();self.assertTrue(self.provider.done.wait(1))
        self.model.close();self.model.thread.join(1)
        self.assertIsNone(self.model.completed);self.assertIsNone(self.model.job)

    def test_closing_clears_buffers_and_late_prediction_cannot_reopen(self):
        self.start_job();self.model.close();self.provider.release.set();self.model.thread.join(1)
        self.assertFalse(self.model.snapshot()['available']);self.assertIsNone(self.model.completed)
        self.assertEqual(len(self.model.images),0);self.assertEqual(len(self.model.rows),0)
        self.assertIsNone(self.model.extract(self.rgb,4000))

    def test_stale_input_cannot_keep_recognized_result_or_enqueue(self):
        self.process(100);self.model.pending=self.model.confirmed='bodyweight_squat'
        result=self.process(100)
        self.assertIsNone(result['action_id']);self.assertIsNone(result['candidate_action_id'])
        self.assertEqual(self.provider.calls,0);self.assertIsNone(self.model.job)

    def test_confirmation_expires_without_fresh_video_prediction(self):
        self.process(0)
        self.model.completed=(self.model.generation,0,{'action_id':'bodyweight_squat','score':.9,'reason':None})
        for stamp in range(100,1000,100):result=self.process(stamp)
        self.assertEqual(result['action_id'],'bodyweight_squat')
        for stamp in range(1000,5200,100):result=self.process(stamp)
        self.assertIsNone(result['action_id']);self.assertEqual(result['reason'],'waiting_for_video')

    def test_unreleased_candidate_keeps_existing_backend(self):
        fallback=Mock();sentinel=object();fallback.return_value=sentinel
        with patch('fitness_app.semantic_recognition.resources',return_value=(None,'semantic_model_not_released')):
            self.assertIs(create_recognizer(fallback),sentinel)
        fallback.assert_called_once_with()

    def test_released_model_factory_is_lazy_without_starting_gpu_thread(self):
        with patch('fitness_app.semantic_recognition.resources',return_value=({'version':'test','release_gate_passed':True},None)):
            result=create_recognizer()
        try:self.assertIsNone(result.thread);self.assertTrue(result.available)
        finally:result.close()

    def test_resolution_change_discards_old_rgb_window(self):
        for stamp in range(0,1000,100):self.process(stamp)
        old_generation=self.model.generation
        frame=self.frame(1000);frame.update(width=64,height=48)
        image=np.zeros((48,64,3),np.uint8)
        self.model.process(frame,self.model.extract(image,1000))
        self.assertGreater(self.model.generation,old_generation);self.assertEqual(len(self.model.images),1)
        self.assertEqual(self.model.images[0][1].shape,(48,64,3));self.assertEqual(self.provider.calls,0)

    def test_action_switch_needs_distinct_video_windows_not_repeated_old_prediction(self):
        self.process(0);self.model.completed=(self.model.generation,0,{'action_id':'barbell_bent_over_row','score':.9})
        for stamp in range(100,1000,100):result=self.process(stamp)
        self.assertEqual(result['action_id'],'barbell_bent_over_row')
        self.model.completed=(self.model.generation,1000,{'action_id':'conventional_deadlift','score':.9})
        for stamp in range(1000,2000,100):result=self.process(stamp)
        self.assertIsNone(result['action_id']);self.assertEqual(self.model.pending_windows,1)
        self.model.completed=(self.model.generation,2000,{'action_id':'conventional_deadlift','score':.9})
        self.assertEqual(self.process(2000)['action_id'],'conventional_deadlift')

    def test_low_frame_rate_still_queues_eight_distinct_frames_in_supported_span(self):
        for stamp in range(0,3600,400):self.process(stamp)
        self.assertTrue(self.provider.entered.wait(1))
        self.assertEqual(self.provider.calls,1)


if __name__=='__main__':unittest.main()
