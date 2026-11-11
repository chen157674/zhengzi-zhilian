"""14-D 保持适配→源区间／摘要／时长目标的新连接，复用已提取真实姿态。"""
import json
from pathlib import Path
import sys
import unittest

import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.paths import ROOT
from fitness_app.catalogue import get_action
from fitness_app.counting_resources import create_counter,read_counting_manifest
from fitness_app.additional_counting_resources import read_manifest
from fitness_app.frame_pipeline import framing
from fitness_app.database import StorageError
from fitness_app.auth import APIError

sys.path.insert(0,str(ROOT/'scripts'))
from prepare_movement_input import cached_frames
from test_plank_hold import geometry


class PlankConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames=cached_frames('dvids-640237-side')
        cls.diagnosis=json.loads((ROOT/'data/verification/step-14/plank-hold-development.json').read_text(encoding='utf-8'))
        cls.source=ROOT/'data/datasets/development-movements/plank-dvids-640237-side.mp4'

    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.manager=self.fx.app.extensions['session_manager'];self.video=self.fx.app.extensions['video_jobs'];self.media=self.fx.app.extensions['media_inputs']

    def tearDown(self):
        self.fx.store.fault=None;self.video.stop();self.manager.stop();self.fx.app.extensions['preview_manager'].stop();self.media.stop();self.fx.tearDown()

    def create(self,source='camera',input_id=None,target=None):
        config={'action_id':'forearm_plank','capability_version':get_action('forearm_plank')['capability_version'],'source':source,'mode':'target' if target else 'free','target':target}
        if input_id:config['input_id']=input_id
        response=self.fx.post(self.fx.client,'/api/v1/sessions',config);self.assertEqual(response.status_code,201,response.json)
        return response.json['data']['session_id']

    def running(self,sid):
        control=self.manager.claim(self.fx.user,self.fx.auth,self.fx.now[0]+12*3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=self.manager.live[sid];rt.data=self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());rt.adapter=create_counter('forearm_plank');rt.session_origin_mono=0
        return rt,control

    @staticmethod
    def pose_result(frame):
        count=frame['framing']['person_count']
        return {'poses':[frame['framing']['landmarks']] if count==1 else [[] for unused in range(count)],'model':'mediapipe-full-float16-1'}

    def consume(self,rt,frames):
        for frame in frames:
            # 会话时间轴首次帧至少为 1ms；模拟时钟使用同一原点，避免夹具凭空少1ms。
            self.fx.mono[0]=(frame['captured_ms']+1)/1000
            raw=self.pose_result(frame);quality=framing('forearm_plank',raw);quality['ready_for_tracking']=quality['usable']
            self.manager._consume(rt,{**frame,'framing':quality,'model_version':raw['model']})

    def test_camera_real_pose_hold_completes_seconds_target_with_atomic_active_time(self):
        sid=self.create(target={'sets':1,'value':5,'unit':'seconds','rest_seconds':0});rt,_=self.running(sid)
        self.consume(rt,self.frames);result=self.fx.store.snapshot(self.fx.user,sid,True)
        self.assertEqual(result['status'],'completed');self.assertGreaterEqual(result['hold_ms'],5000);self.assertLess(result['hold_ms'],5100)
        self.assertGreaterEqual(result['active_ms'],result['hold_ms']);self.assertEqual(result['total_count'],0);self.assertEqual(result['events'],[])
        self.assertEqual(len(result['intervals']),1);self.assertEqual(result['groups'][0]['hold_ms'],result['hold_ms'])

    def test_pause_command_and_new_source_confirmation_exclude_five_seconds(self):
        sid=self.create();rt,control=self.running(sid);self.consume(rt,[geometry(i) for i in range(11)])
        before=self.fx.store.snapshot(self.fx.user,sid);self.assertEqual(before['hold_ms'],1000)
        current=self.manager.controls.snapshot(self.fx.user,sid)
        response=self.fx.post(self.fx.client,f'/api/v1/sessions/{sid}/commands',{'command':'pause','expected_version':before['state_version'],
            'control_token':control['control_token'],'controller_epoch':current['controller_epoch'],'stream_epoch':current['stream_epoch']})
        self.assertEqual(response.status_code,200,response.json);self.assertEqual(response.json['data']['status'],'paused')
        # 已有 08 证明恢复控制／取景；本用例只补新保持适配在暂停后的源区间接续。
        rt.data=self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());self.consume(rt,[geometry(i) for i in range(60,71)])
        after=self.fx.store.snapshot(self.fx.user,sid,True);self.assertEqual(after['hold_ms'],2000);self.assertEqual(after['active_ms'],2000)
        self.assertEqual(len(after['intervals']),2)
        self.assertEqual(after['intervals'][1]['start_ms']-after['intervals'][0]['end_ms'],5000)

    def test_video_h264_decode_and_hold_persistence_uses_source_time(self):
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
        jid=response.json['data']['job_id'];self.video.runs[jid].thread.join(30);self.assertFalse(self.video.runs[jid].thread.is_alive())
        result=self.video.snapshot(self.fx.user,jid);self.assertEqual(result['status'],'completed',result);self.assertEqual(result['save_state'],'clean')
        self.assertEqual(result['session']['hold_ms'],self.diagnosis['hold_ms']);self.assertGreaterEqual(result['session']['active_ms'],result['session']['hold_ms'])
        self.assertFalse(self.media._path(iid).exists());self.assertEqual(self.source.stat().st_mtime_ns,stat.st_mtime_ns)

    def test_interval_active_time_receipt_rollback_and_repeat_keep_atomic_summary(self):
        sid=self.create();self.fx.store.set_state(self.fx.user,sid,'running',s.new_id())
        batch={'set_index':1,'intervals':[{'kind':'hold','start_ms':0,'end_ms':1000,'algorithm_version':'D-POSE-hold-atomic'}],'active_ms':1200}
        def fail(point):
            if point=='before_commit':raise StorageError('injected interval transaction failure')
        self.fx.store.fault=fail;failed=self.fx.writer.submit(self.fx.user,sid,self.fx.auth,'intervals',batch)
        self.assertEqual(failed['save_state'],'save_failed');saved=self.fx.store.snapshot(self.fx.user,sid)
        self.assertEqual((saved['hold_ms'],saved['active_ms']),(0,0))
        self.fx.store.fault=None;restored=self.fx.writer.retry(self.fx.user,sid,self.fx.auth);self.assertEqual((restored['hold_ms'],restored['active_ms']),(1000,1200))
        repeated=self.fx.writer.submit(self.fx.user,sid,self.fx.auth,'intervals',batch);self.assertEqual(repeated['state_version'],restored['state_version'])
        self.assertTrue(self.fx.store.confirmed(self.fx.user,sid,'intervals',batch))
        self.fx.writer.release(self.fx.user,sid)
        def after(point):
            if point=='after_commit':raise StorageError('injected interval response uncertainty')
        self.fx.store.fault=after;next_batch={'set_index':1,'intervals':[{'kind':'hold','start_ms':1000,'end_ms':1500,'algorithm_version':'D-POSE-hold-atomic'}],'active_ms':1800}
        result=self.fx.writer.submit(self.fx.user,sid,self.fx.auth,'intervals',next_batch)
        self.assertEqual(result['save_state'],'clean');self.assertEqual((result['hold_ms'],result['active_ms']),(1500,1800));self.fx.store.fault=None
        with self.assertRaises(APIError):self.fx.store.append_intervals(self.fx.user,sid,1,batch['intervals'],active_ms=-1)
        # 既有不带 active_ms 的内部调用仍保持原回执语义，不能改写历史命令。
        legacy=[{'kind':'hold','start_ms':1500,'end_ms':1600,'algorithm_version':'D-POSE-hold-atomic'}]
        first=self.fx.store.append_intervals(self.fx.user,sid,1,legacy);again=self.fx.store.append_intervals(self.fx.user,sid,1,legacy)
        self.assertEqual(first['state_version'],again['state_version']);self.assertEqual(again['active_ms'],1800)
        self.assertEqual(read_counting_manifest()['capability_version'],'count-1-800d0110077c')
        self.assertEqual(read_manifest('reverse_lunge')['capability_version'],'count-14-3ea429e6a708')


if __name__=='__main__':unittest.main()
