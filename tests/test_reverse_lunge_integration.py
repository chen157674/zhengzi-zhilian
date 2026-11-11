"""14-A 真实姿态缓存接相机／离线保存；只补新增动作，不重跑共用矩阵。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.paths import ROOT
from fitness_app.catalogue import get_action
from fitness_app.counting_resources import create_counter,read_counting_manifest
from fitness_app.additional_counting_resources import read_manifest
from fitness_app.frame_pipeline import framing

sys.path.insert(0,str(ROOT/'scripts'))
from prepare_movement_input import cached_frames


class ReverseLungeConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames=cached_frames('dvids-551833')
        cls.diagnosis=json.loads((ROOT/'data/verification/step-14/reverse-lunge-development.json').read_text(encoding='utf-8'))
        cls.source=ROOT/'data/datasets/development-movements/reverse-lunge-dvids-551833.mp4'

    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.manager=self.fx.app.extensions['session_manager'];self.video=self.fx.app.extensions['video_jobs'];self.media=self.fx.app.extensions['media_inputs']

    def tearDown(self):
        self.video.stop();self.manager.stop();self.fx.app.extensions['preview_manager'].stop();self.media.stop();self.fx.tearDown()

    def create(self,source='camera',input_id=None):
        config={'action_id':'reverse_lunge','capability_version':get_action('reverse_lunge')['capability_version'],'source':source,
            'mode':'target' if source=='camera' else 'free','target':{'sets':1,'value':2,'unit':'per_side_reps','rest_seconds':0} if source=='camera' else None}
        if input_id:config['input_id']=input_id
        response=self.fx.post(self.fx.client,'/api/v1/sessions',config);self.assertEqual(response.status_code,201,response.json)
        return response.json['data']['session_id']

    @staticmethod
    def pose_result(frame):
        count=frame['framing']['person_count']
        return {'poses':[frame['framing']['landmarks']] if count==1 else [[] for unused in range(count)],'model':'mediapipe-full-float16-1'}

    def test_camera_new_adapter_drives_per_side_target_and_saved_events(self):
        sid=self.create();self.manager.claim(self.fx.user,self.fx.auth,self.fx.now[0]+12*3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=self.manager.live[sid];rt.data=self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());rt.adapter=create_counter('reverse_lunge');rt.session_origin_mono=0
        for frame in self.frames:
            raw=self.pose_result(frame);quality=framing('reverse_lunge',raw);quality['ready_for_tracking']=quality['usable']
            self.manager._consume(rt,{**frame,'framing':quality,'model_version':raw['model']})
        result=self.fx.store.snapshot(self.fx.user,sid,True)
        self.assertEqual(result['status'],'completed');self.assertEqual((result['left_count'],result['right_count']),(2,2))
        self.assertEqual([e['side'] for e in result['events']],[e['side'] for e in self.diagnosis['events']])
        self.assertEqual(result['evaluable_count'],0)

    def test_video_real_h264_validation_decode_and_cached_pose_persistence(self):
        stat=self.source.stat()
        with self.source.open('rb') as stream:uploaded=self.media.upload(self.fx.user,self.fx.auth,stream,s.new_id())
        iid=uploaded['input_id'];self.media.threads[iid].join(10)
        self.assertEqual(self.media.snapshot(self.fx.user,iid)['status'],'ready')
        poses={frame['captured_ms']:frame for frame in self.frames}
        class CachedPose:
            def __init__(inner,**unused):pass
            def detect(inner,rgb,stamp):
                frame=poses[stamp];self.assertEqual(rgb.shape[:2],(frame['height'],frame['width']));return self.pose_result(frame)
            def close(inner):pass
        self.video.pose_factory=CachedPose;sid=self.create('video',iid)
        response=self.fx.post(self.fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0});self.assertEqual(response.status_code,202,response.json)
        jid=response.json['data']['job_id'];self.video.runs[jid].thread.join(30);self.assertFalse(self.video.runs[jid].thread.is_alive())
        result=self.video.snapshot(self.fx.user,jid);self.assertEqual(result['status'],'completed',result);self.assertEqual(result['save_state'],'clean')
        self.assertEqual((result['session']['left_count'],result['session']['right_count']),(2,2))
        self.assertFalse(self.media._path(iid).exists());self.assertEqual(self.source.stat().st_mtime_ns,stat.st_mtime_ns)

    def test_new_resource_guard_and_original_capability_unchanged(self):
        self.assertEqual(read_counting_manifest()['capability_version'],'count-1-800d0110077c')
        self.assertTrue(get_action('reverse_lunge')['counting_available']);self.assertFalse(get_action('bodyweight_glute_bridge')['counting_available'])
        raw=json.loads((ROOT/'data/models/reverse-lunge.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(prefix='lunge-resource-',dir=ROOT/'.tmp/tests') as directory:
            path=Path(directory)/'resource.json'
            for field,value in (('status','development'),('implementation',{}),('parameters_sha256','0'*64),('calibration_sha256','0'*64)):
                path.write_text(json.dumps({**raw,field:value}),encoding='utf-8')
                with self.subTest(field=field),self.assertRaises(ValueError):read_manifest('reverse_lunge',path)


if __name__=='__main__':unittest.main()
