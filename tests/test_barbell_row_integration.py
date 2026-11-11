"""14-H 实际姿态至目标、真实解码至保存；只核新增动作连接。"""
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
from fitness_app.additional_counting_resources import read_manifest,current_manifest

sys.path.insert(0,str(ROOT/'scripts'))
from diagnose_barbell_row import prepare_frames
from prepare_movement_input import cached_frames


class RowConnections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames,_,hit=prepare_frames()
        if not hit:raise AssertionError('已有图像范围证据应复用')
        cls.raw=cached_frames('dvids-639914-side',apply_bounds=False)
        cls.source=ROOT/'data/datasets/development-movements/barbell-row-dvids-639914-side.mp4'

    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.manager=self.fx.app.extensions['session_manager'];self.video=self.fx.app.extensions['video_jobs'];self.media=self.fx.app.extensions['media_inputs']

    def tearDown(self):
        self.video.stop();self.manager.stop();self.fx.app.extensions['preview_manager'].stop();self.media.stop();self.fx.tearDown()

    def create(self,source='camera',input_id=None):
        config={'action_id':'barbell_bent_over_row','capability_version':get_action('barbell_bent_over_row')['capability_version'],'source':source,
            'mode':'target' if source=='camera' else 'free','target':{'sets':1,'value':3,'unit':'reps','rest_seconds':0} if source=='camera' else None}
        if input_id:config['input_id']=input_id
        response=self.fx.post(self.fx.client,'/api/v1/sessions',config);self.assertEqual(response.status_code,201,response.json)
        return response.json['data']['session_id']

    def test_real_cached_cycles_complete_target_and_persist_without_quality(self):
        sid=self.create();self.manager.claim(self.fx.user,self.fx.auth,self.fx.now[0]+12*3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=self.manager.live[sid];rt.data=self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());rt.adapter=create_counter('barbell_bent_over_row');rt.session_origin_mono=0
        for frame in self.frames:
            self.fx.mono[0]=(frame['captured_ms']+1)/1000;q={**frame['framing'],'ready_for_tracking':frame['framing']['usable']}
            self.manager._consume(rt,{**frame,'framing':q,'model_version':'mediapipe-full-float16-1'})
        data=self.fx.store.snapshot(self.fx.user,sid,True)
        self.assertEqual(data['status'],'completed');self.assertEqual(data['total_count'],3);self.assertEqual(data['evaluable_count'],0)
        self.assertEqual([e['side'] for e in data['events']],['none']*3);self.assertTrue(all(e['assessments']==[] for e in data['events']))

    def test_real_h264_decode_raw_cache_and_saved_cycles(self):
        frames={f['captured_ms']:f for f in self.raw}
        class CachedPose:
            def __init__(inner,**unused):pass
            def detect(inner,rgb,stamp):
                frame=frames[stamp];count=frame['framing']['person_count']
                return {'poses':[frame['framing']['landmarks']] if count==1 else [[] for _ in range(count)],'model':'mediapipe-full-float16-1'}
            def close(inner):pass
        self.video.pose_factory=CachedPose
        with self.source.open('rb') as stream:uploaded=self.media.upload(self.fx.user,self.fx.auth,stream,s.new_id())
        iid=uploaded['input_id'];self.media.threads[iid].join(10);self.assertEqual(self.media.snapshot(self.fx.user,iid)['status'],'ready')
        sid=self.create('video',iid);response=self.fx.post(self.fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0});self.assertEqual(response.status_code,202,response.json)
        jid=response.json['data']['job_id'];self.video.runs[jid].thread.join(30);self.assertFalse(self.video.runs[jid].thread.is_alive())
        data=self.video.snapshot(self.fx.user,jid);self.assertEqual(data['status'],'completed',data);self.assertEqual(data['save_state'],'clean');self.assertEqual(data['session']['total_count'],3)
        self.assertFalse(self.media._path(iid).exists())

    def test_new_resource_and_existing_capabilities_keep_their_identity(self):
        self.assertEqual(read_counting_manifest()['capability_version'],'count-1-800d0110077c')
        for action,version in [('reverse_lunge','count-14-3ea429e6a708'),('forearm_plank','count-14-6746cc77338d'),('jumping_jack','count-14-59297c196045')]:
            self.assertEqual(current_manifest(action)['capability_version'],version)
        self.assertEqual(get_action('barbell_bent_over_row')['capability_version'],'count-14-051a0c302c36')
        self.assertFalse(get_action('barbell_overhead_press')['counting_available'])
        raw=json.loads((ROOT/'data/models/barbell-row.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(prefix='row-resource-',dir=ROOT/'.tmp/tests') as directory:
            p=Path(directory)/'resource.json'
            for field,value in [('parameters_sha256','0'*64),('input_validation_sha256','0'*64),('implementation',{})]:
                p.write_text(json.dumps({**raw,field:value}),encoding='utf-8')
                with self.subTest(field=field),self.assertRaises(ValueError):read_manifest('barbell_bent_over_row',p)


if __name__=='__main__':unittest.main()
