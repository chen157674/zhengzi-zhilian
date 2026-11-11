"""骨架与自动/固定/上传采集的增量集成，不重复旧识别矩阵。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fitness_app import db_schema as s
from fitness_app.automatic_recording import AutomaticRecorder
from fitness_app.posture_rules import rules_for
from fitness_app.skeleton_store import SkeletonStore
from fitness_app.sessions import LiveSession
import test_skeleton_store as skeleton_fixture
import test_video_analyses as video_fixture


class CameraCaptureTests(unittest.TestCase):
    def setUp(self):
        self.fixture=skeleton_fixture.SkeletonStoreTests();self.fixture.setUp();self.fx=self.fixture.fx;self.store=self.fixture.store
    def tearDown(self):self.fixture.tearDown()
    def make(self):return self.fixture.make()
    def finding(self,start,end):
        rule=rules_for('barbell_overhead_press')[0]
        return {**{k:rule[k] for k in ('code','rule_version','body_parts','suggestion')},'status':'not_observed','side':'none','start_ms':start,'end_ms':end,'message':'工程观察','reason':'','evidence_level':'reference','evidence':{'view':'front'}}
    def record(self,enabled=True):
        from fitness_app.automatic_analysis import AutomaticAnalysis
        recorder=AutomaticRecorder(self.fx.store,self.fx.user,self.fx.auth,s.new_id(),save_skeleton=enabled)
        finding=self.finding
        class Engine:
            counting_available=False
            def __init__(self,action):pass
            def reset(self,reason):pass
            def process(self,frame,context):return {'posture_findings':[finding(0,frame['captured_ms'])]} if frame['frame_seq']==24 else {}
        analysis=AutomaticAnalysis(Engine,recorder=recorder)
        for i in range(25):
            f=skeleton_fixture.frame(i*100,i)
            analysis.process(f,{'available':True,'state':'recognized','action_id':'barbell_overhead_press','model_version':'fixture'})
        sid=recorder.current['record_id'];recorder.close('preview_closed');return sid,recorder
    def test_auto_capture_keeps_precommit_frames_and_optional_failure_does_not_fail_summary(self):
        sid,recorder=self.record();value=self.store.read(self.fx.user,sid);self.assertEqual(value['frame_count'],25);self.assertEqual(value['visible_frame_count'],25);self.assertEqual(value['frames'][0]['t'],0);self.assertEqual(value['status'],'complete')
        off,_=self.record(False);self.assertEqual(self.store.summary(self.fx.user,off)['status'],'not_saved')
        with patch.object(SkeletonStore,'append',side_effect=OSError('optional skeleton fault')):
            failed,recorder=self.record();self.assertEqual(recorder.last_skeleton['status'],'save_failed');self.assertEqual(self.fx.store.snapshot(self.fx.user,failed)['status'],'ended_early')
    def test_fixed_training_has_monotonic_record_time_and_closes_with_session(self):
        sid=self.make();self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());data=self.fx.writer.snapshot(self.fx.user,sid)
        manager=self.fx.app.extensions['session_manager'];rt=LiveSession(self.fx.user,self.fx.auth,self.fx.now[0]+60000,data)
        class Engine:
            def process(self,frame,context):return {}
            def reset(self,reason):pass
        rt.adapter=Engine();rt.session_origin_mono=0
        for i in range(22):self.fx.mono[0]=i*.1;manager._consume(rt,skeleton_fixture.frame(10000+i*100,i))
        self.fx.finish(sid);rt.data=self.fx.writer.snapshot(self.fx.user,sid);manager._drop(rt)
        value=self.store.read(self.fx.user,sid);self.assertEqual(value['status'],'complete');self.assertGreater(value['frame_count'],20)
        times=[v['t'] for v in value['frames']];self.assertEqual(times,sorted(set(times)));self.assertEqual(value['frames'][0]['source_ms'],10000)
    def test_ai_receives_counts_only_and_deleting_skeleton_invalidates_its_summary(self):
        sid,_=self.record();builder=self.fx.app.extensions['ai_jobs'].builder
        bundle=builder.build(self.fx.user,self.fx.auth,{'context_kind':'record','context_id':sid,'history_scope':'none','text':'复盘这次训练'})
        summary=bundle['facts']['selected_records'][0]['skeleton_summary'];self.assertEqual(summary['visible_frame_count'],25);self.assertNotIn('joints',str(summary));self.assertEqual(bundle['read_scope']['skeleton_summary_records'],1)
        self.store.remove(self.fx.user,self.fx.auth,sid,summary['state_version'],s.new_id());self.assertFalse(builder.current(self.fx.user,self.fx.auth,bundle['reference']))

    def test_append_only_skeleton_growth_does_not_cancel_frozen_ai_statistics(self):
        sid=self.make();self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());self.store.append(self.fx.user,sid,0,self.fixture.batch(),0)
        builder=self.fx.app.extensions['ai_jobs'].builder
        bundle=builder.build(self.fx.user,self.fx.auth,{'context_kind':'record','context_id':sid,'history_scope':'none','text':'解释已保存的观察范围'})
        self.store.append(self.fx.user,sid,1,self.fixture.batch(20),0)
        self.assertTrue(builder.current(self.fx.user,self.fx.auth,bundle['reference']));self.assertEqual(bundle['facts']['selected_records'][0]['skeleton_summary']['frame_count'],20)
        version=self.store.summary(self.fx.user,sid)['state_version'];self.store.remove(self.fx.user,self.fx.auth,sid,version,s.new_id());self.assertFalse(builder.current(self.fx.user,self.fx.auth,bundle['reference']))


class UploadedCaptureTests(unittest.TestCase):
    def setUp(self):self.fixture=video_fixture.VideoAnalysesTests();self.fixture.setUp()
    def tearDown(self):self.fixture.tearDown()
    def test_auto_video_saves_segment_tracks_and_manual_revision_requires_fresh_optin(self):
        f=self.fixture;pid=f.start(f.upload(),save_skeleton=True);data=f.wait(pid);self.assertEqual(data['status'],'completed',data)
        store=f.fx.app.extensions['skeleton_store']
        for segment in data['segments']:
            sid=segment['current_revision']['session_id'];saved=store.read(f.fx.user,sid);self.assertEqual(saved['status'],'complete');self.assertGreater(saved['frame_count'],0);self.assertEqual(saved['metadata']['source_offset_ms'],segment['start_ms']);self.assertEqual(saved['metadata']['source_sha256'],data['source_sha256'])
        segment=data['segments'][0];rid=f.revise(data,segment,'dumbbell_shoulder_press',f.upload());result=f.wait(rid,True)
        self.assertEqual(result['status'],'completed',result);self.assertEqual(store.summary(f.fx.user,result['session_id'])['status'],'not_saved')


if __name__=='__main__':unittest.main()
