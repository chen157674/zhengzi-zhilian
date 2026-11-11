"""三条真实业务保存链的运动摘要增量集成；姿态/分类是合成夹具。"""
import unittest
from fitness_app import db_schema as s
from fitness_app.automatic_analysis import AutomaticAnalysis
from fitness_app.automatic_recording import AutomaticRecorder
from fitness_app.posture_rules import rules_for
from fitness_app.sessions import LiveSession
import test_formal_records as records
import test_movement_stability as metrics
import test_video_analyses as videos


class Engine:
    counting_available=False
    def __init__(self,action):self.action=action;self.previous=None
    def reset(self,reason):pass
    def process(self,frame,context):
        output=metrics.output();rule=rules_for(self.action)[0];output['observation']['posture']['items']=[{**rule,'status':'not_observed'}]
        stamp=frame['captured_ms']
        if self.previous is None:self.previous=stamp
        if stamp-self.previous>=1000:
            item={k:rule[k] for k in ('code','rule_version','body_parts','suggestion')}
            item.update(status='not_observed',side='none',start_ms=self.previous,end_ms=stamp,message='工程观察',reason='',evidence_level='reference',evidence={'view':'front'})
            output['posture_findings']=[item];self.previous=stamp
        return output


class CameraStabilityTests(unittest.TestCase):
    def setUp(self):self.fx=records.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.app.extensions['preview_manager'].stop();self.fx.tearDown()
    def test_auto_default_summary_is_saved_without_enabling_skeleton_retention(self):
        recorder=AutomaticRecorder(self.fx.store,self.fx.user,self.fx.auth,s.new_id());analysis=AutomaticAnalysis(Engine,recorder=recorder)
        for t in range(0,18001,100):analysis.process(metrics.frame(t,.02 if t<9000 else .20),{'available':True,'state':'recognized','action_id':'barbell_overhead_press','model_version':'synthetic'})
        sid=recorder.current['record_id'];recorder.close('preview_closed');result=self.fx.client.get('/api/v1/records/'+sid)
        self.assertEqual(result.status_code,200,result.json);data=result.json['data'];self.assertEqual(data['saved_skeleton']['status'],'not_saved')
        self.assertEqual(data['movement_stability']['status'],'complete');self.assertEqual(data['movement_stability']['window_count'],6)
        selected=next(v for v in data['movement_stability']['comparisons'] if v['metric']=='wrist_difference');self.assertEqual(selected['direction'],'increase')
    def test_fixed_training_keeps_group_and_monotonic_record_time(self):
        sid=self.fx.running('barbell_overhead_press');manager=self.fx.app.extensions['session_manager'];rt=LiveSession(self.fx.user,self.fx.auth,self.fx.now[0]+60000,self.fx.writer.snapshot(self.fx.user,sid));rt.adapter=Engine('barbell_overhead_press');rt.session_origin_mono=0
        for t in range(0,18001,100):
            self.fx.mono[0]=t/1000;frame=metrics.frame(t,.02 if t<9000 else .20);frame['captured_ms']=10000+t;manager._consume(rt,frame)
        self.fx.finish(sid);rt.data=self.fx.writer.snapshot(self.fx.user,sid);manager._drop(rt)
        value=self.fx.app.extensions['stability_store'].snapshot(self.fx.user,sid,True)
        self.assertEqual(value['status'],'complete');self.assertEqual(value['window_count'],6);self.assertTrue(all(w['group']==1 for w in value['windows']))
        self.assertTrue(all(w['start_ms']>=0 for w in value['windows']));self.assertEqual(self.fx.app.extensions['skeleton_store'].summary(self.fx.user,sid)['status'],'not_saved')


class VideoStabilityTests(unittest.TestCase):
    def setUp(self):self.fixture=videos.VideoAnalysesTests();self.fixture.setUp()
    def tearDown(self):self.fixture.tearDown()
    def test_uploaded_segments_keep_their_own_windows_and_do_not_count_missing_as_stable(self):
        f=self.fixture;pid=f.start(f.upload());data=f.wait(pid);self.assertEqual(data['status'],'completed',data)
        for segment in data['segments']:
            sid=segment['current_revision']['session_id'];value=f.fx.app.extensions['stability_store'].snapshot(f.fx.user,sid,True)
            self.assertGreater(value['window_count'],0);self.assertEqual(value['status'],'complete');self.assertNotEqual(value['assessment_state'],'compared')
            self.assertTrue(all(w['start_ms']>=0 and w['end_ms']<=segment['end_ms']-segment['start_ms'] for w in value['windows']))


if __name__=='__main__':unittest.main()
