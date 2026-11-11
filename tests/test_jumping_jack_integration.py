"""14-E 新联合周期接目标／离线保存；真实姿态缓存复用，不重复 Full 提取。"""
import json
from pathlib import Path
import sys
import unittest

import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.paths import ROOT
from fitness_app.catalogue import get_action
from fitness_app.counting_resources import create_counter
from fitness_app.frame_pipeline import framing

sys.path.insert(0,str(ROOT/'scripts'))
from prepare_movement_input import cached_frames


class JackConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames=cached_frames('commons-jumping-jacks-segment')
        cls.diagnosis=json.loads((ROOT/'data/verification/step-14/jumping-jack-development.json').read_text(encoding='utf-8'))
        cls.source=ROOT/'data/datasets/development-movements/jumping-jacks-commons-segment.mp4'

    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.manager=self.fx.app.extensions['session_manager'];self.video=self.fx.app.extensions['video_jobs'];self.media=self.fx.app.extensions['media_inputs']

    def tearDown(self):
        self.video.stop();self.manager.stop();self.fx.app.extensions['preview_manager'].stop();self.media.stop();self.fx.tearDown()

    def create(self,source='camera',input_id=None):
        config={'action_id':'jumping_jack','capability_version':get_action('jumping_jack')['capability_version'],'source':source,
            'mode':'target' if source=='camera' else 'free','target':{'sets':1,'value':3,'unit':'reps','rest_seconds':0} if source=='camera' else None}
        if input_id:config['input_id']=input_id
        response=self.fx.post(self.fx.client,'/api/v1/sessions',config);self.assertEqual(response.status_code,201,response.json)
        return response.json['data']['session_id']

    @staticmethod
    def pose_result(frame):
        count=frame['framing']['person_count']
        return {'poses':[frame['framing']['landmarks']] if count==1 else [[] for unused in range(count)],'model':'mediapipe-full-float16-1'}

    def test_real_cached_joint_cycles_complete_target_and_persist_without_quality(self):
        sid=self.create();self.manager.claim(self.fx.user,self.fx.auth,self.fx.now[0]+12*3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=self.manager.live[sid];rt.data=self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());rt.adapter=create_counter('jumping_jack');rt.session_origin_mono=0
        for frame in self.frames:
            self.fx.mono[0]=(frame['captured_ms']+1)/1000;raw=self.pose_result(frame);quality=framing('jumping_jack',raw);quality['ready_for_tracking']=quality['usable']
            self.manager._consume(rt,{**frame,'framing':quality,'model_version':raw['model']})
        result=self.fx.store.snapshot(self.fx.user,sid,True)
        self.assertEqual(result['status'],'completed');self.assertEqual(result['total_count'],3);self.assertGreater(result['active_ms'],0)
        self.assertEqual(result['evaluable_count'],0);self.assertEqual(result['hold_ms'],0)
        self.assertEqual([e['side'] for e in result['events']],['none']*3)

    def test_h264_video_decode_real_cached_poses_produce_three_saved_cycles(self):
        stat=self.source.stat()
        with self.source.open('rb') as stream:uploaded=self.media.upload(self.fx.user,self.fx.auth,stream,s.new_id())
        iid=uploaded['input_id'];self.media.threads[iid].join(10);self.assertEqual(self.media.snapshot(self.fx.user,iid)['status'],'ready')
        poses={frame['captured_ms']:frame for frame in self.frames}
        class CachedPose:
            def __init__(inner,**unused):pass
            def detect(inner,rgb,stamp):
                frame=poses[stamp];self.assertEqual(rgb.shape[:2],(frame['height'],frame['width']));return self.pose_result(frame)
            def close(inner):pass
        self.video.pose_factory=CachedPose;sid=self.create('video',iid)
        response=self.fx.post(self.fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0});self.assertEqual(response.status_code,202,response.json)
        jid=response.json['data']['job_id'];self.video.runs[jid].thread.join(20);self.assertFalse(self.video.runs[jid].thread.is_alive())
        result=self.video.snapshot(self.fx.user,jid);self.assertEqual(result['status'],'completed',result);self.assertEqual(result['save_state'],'clean')
        self.assertEqual(result['session']['total_count'],len(self.diagnosis['events']));self.assertEqual(result['session']['total_count'],3)
        self.assertFalse(self.media._path(iid).exists());self.assertEqual(self.source.stat().st_mtime_ns,stat.st_mtime_ns)


if __name__=='__main__':unittest.main()
