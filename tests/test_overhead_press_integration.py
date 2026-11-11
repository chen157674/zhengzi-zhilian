"""肩推候选接服务端目标／保存；正例为D-POSE，实际视频仅作裁切反例。"""
from pathlib import Path
import sys
import unittest

import test_formal_records as shared
from test_overhead_press import settings,sequence
from fitness_app.overhead_press import OverheadPressCounter
from fitness_app.catalogue import get_action
from fitness_app.frame_pipeline import framing
from fitness_app.paths import ROOT
from fitness_app import db_schema as s

sys.path.insert(0,str(ROOT/'scripts'))
from prepare_movement_input import cached_frames


class PressConnections(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.manager=self.fx.app.extensions['session_manager'];self.video=self.fx.app.extensions['video_jobs'];self.media=self.fx.app.extensions['media_inputs']

    def tearDown(self):
        self.video.stop();self.manager.stop();self.fx.app.extensions['preview_manager'].stop();self.media.stop();self.fx.tearDown()

    def create(self,source='camera',input_id=None):
        config={'action_id':'barbell_overhead_press','capability_version':get_action('barbell_overhead_press')['capability_version'],'source':source,
            'mode':'target' if source=='camera' else 'free','target':{'sets':1,'value':2,'unit':'reps','rest_seconds':0} if source=='camera' else None}
        if input_id:config['input_id']=input_id
        response=self.fx.post(self.fx.client,'/api/v1/sessions',config);self.assertEqual(response.status_code,201,response.json)
        return response.json['data']['session_id']

    def test_known_full_cycles_complete_target_with_empty_quality_assessments(self):
        sid=self.create();self.manager.claim(self.fx.user,self.fx.auth,self.fx.now[0]+12*3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=self.manager.live[sid];rt.data=self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());rt.adapter=OverheadPressCounter(settings());rt.session_origin_mono=0
        frames=sequence();offset=len(frames);frames.extend({**f,'frame_seq':f['frame_seq']+offset,'captured_ms':f['captured_ms']+offset*150} for f in sequence())
        for frame in frames:
            self.fx.mono[0]=(frame['captured_ms']+1)/1000;q=framing('barbell_overhead_press',{'poses':[frame['framing']['landmarks']]});q['ready_for_tracking']=q['usable']
            self.manager._consume(rt,{**frame,'framing':q,'model_version':'D-POSE-known-geometry'})
        data=self.fx.store.snapshot(self.fx.user,sid,True)
        self.assertEqual(data['status'],'completed');self.assertEqual(data['total_count'],2);self.assertEqual((data['left_count'],data['right_count']),(0,0))
        self.assertEqual(data['evaluable_count'],0);self.assertTrue(all(e['assessments']==[] for e in data['events']))
        self.assertFalse(get_action('barbell_overhead_press')['counting_available'])

    def test_cropped_real_video_does_not_persist_a_guessed_full_cycle(self):
        source=ROOT/'data/datasets/development-movements/overhead-press-dvids-692514-side.mp4'
        frames={f['captured_ms']:f for f in cached_frames('dvids-692514-side',apply_bounds=False)}
        class CachedPose:
            def __init__(inner,**unused):pass
            def detect(inner,rgb,stamp):return {'poses':[frames[stamp]['framing']['landmarks']],'model':'mediapipe-full-float16-1'}
            def close(inner):pass
        self.video.pose_factory=CachedPose;self.video.engine_provider=lambda action:OverheadPressCounter(settings())
        with source.open('rb') as stream:uploaded=self.media.upload(self.fx.user,self.fx.auth,stream,s.new_id())
        iid=uploaded['input_id'];self.media.threads[iid].join(10);self.assertEqual(self.media.snapshot(self.fx.user,iid)['status'],'ready')
        sid=self.create('video',iid);response=self.fx.post(self.fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0});self.assertEqual(response.status_code,202,response.json)
        jid=response.json['data']['job_id'];self.video.runs[jid].thread.join(30);self.assertFalse(self.video.runs[jid].thread.is_alive())
        data=self.video.snapshot(self.fx.user,jid);self.assertEqual(data['status'],'completed',data);self.assertEqual(data['save_state'],'clean');self.assertEqual(data['session']['total_count'],0)
        self.assertEqual(self.fx.store.snapshot(self.fx.user,sid,True)['events'],[]);self.assertFalse(self.media._path(iid).exists())


if __name__=='__main__':unittest.main()
