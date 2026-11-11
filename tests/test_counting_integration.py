"""12 新连接：真实开发姿态缓存→四动作计数→原会话／视频／目标保存链。

不重复运行姿态模型；视频执行器仍解码原研究媒体，测试内部登记为研究输入，
不将 HEVC 研究输入当作公共上传接口支持范围。期待事件是接线基准，不是准确率真值。
"""
import json
from pathlib import Path
import sys
import unittest
import threading
import time
from unittest.mock import patch

import av
import test_formal_records as shared
from fitness_app import db_schema as s
from fitness_app.paths import ROOT
from fitness_app.database import json_text
from fitness_app.catalogue import get_action
from fitness_app.counting_resources import create_counter,read_counting_manifest
from fitness_app.frame_pipeline import framing
from fitness_app.research_data import load_dataset
from fitness_app.research_extract import probe_research_video

sys.path.insert(0,str(ROOT/'scripts'))
from diagnose_cycles import cached_frames

CASES={'bodyweight_squat':'seven-exercises/squat/good/subject_007_squat_good_side.mp4',
    'standard_push_up':'seven-exercises/push_up/good/subject_002_push_up_good_side.mp4',
    'standing_dumbbell_curl':'seven-exercises/bicep_curl/good/subject_001_bicep_curl_good_front.mp4',
    'conventional_deadlift':'mydeadlift/Gerakan Benar/GB_S02_R29.mp4'}


class CountingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        samples={sample.path:sample for sample in load_dataset()};cls.samples={action:samples[path] for action,path in CASES.items()}
        baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'))
        cls.frames={action:cached_frames(sample,baseline) for action,sample in cls.samples.items()}
        diagnosis=json.loads((ROOT/'data/verification/step-12/development-cycles.json').read_text(encoding='utf-8'))
        cls.expected={action:next(case['events'] for case in diagnosis['actions'][action]['cases'] if case['source_path']==path) for action,path in CASES.items()}

    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.manager=self.fx.app.extensions['session_manager'];self.video=self.fx.app.extensions['video_jobs'];self.media=self.fx.app.extensions['media_inputs']
        self.provider=lambda action:create_counter(action,allow_development=True)
        self.manager.engine_provider=self.provider;self.video.engine_provider=self.provider

    def tearDown(self):
        self.video.stop();self.manager.stop();self.fx.app.extensions['preview_manager'].stop();self.media.stop();self.fx.tearDown()

    def create(self,action,source='camera',target=None,input_id=None):
        config={'action_id':action,'capability_version':get_action(action)['capability_version'],'source':source,'mode':'target' if target else 'free','target':target}
        if input_id:config['input_id']=input_id
        response=self.fx.post(self.fx.client,'/api/v1/sessions',config);self.assertEqual(response.status_code,201,response.json)
        return response.json['data']['session_id']

    def runtime(self,sid,action):
        control=self.manager.claim(self.fx.user,self.fx.auth,self.fx.now[0]+12*3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        data=self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());rt=self.manager.live[sid];rt.data=data;rt.adapter=self.provider(action);rt.session_origin_mono=0
        return rt,control

    @staticmethod
    def pose_result(frame):
        count=frame['framing']['person_count']
        return {'poses':[frame['framing']['landmarks']] if count==1 else [[] for unused in range(count)],'model':'mediapipe-full-float16-1'}

    def consume(self,rt,action,frames):
        for frame in frames:
            raw=self.pose_result(frame);quality=framing(action,raw);quality['ready_for_tracking']=quality['usable']
            self.manager._consume(rt,{**frame,'framing':quality,'model_version':raw['model']})

    def test_realtime_cached_squat_and_curl_target_use_real_events(self):
        action='bodyweight_squat';sid=self.create(action);rt,control=self.runtime(sid,action)
        self.consume(rt,action,self.frames[action]);data=self.fx.store.snapshot(self.fx.user,sid,True)
        self.assertEqual(data['total_count'],len(self.expected[action]));self.assertTrue(all(event['algorithm_version'].startswith('cycles-four-actions-') for event in data['events']))
        self.assertIn('analysis',rt.latest);rt.data=self.fx.store.set_state(self.fx.user,sid,'ended_early',s.new_id())
        action='standing_dumbbell_curl';sid=self.create(action,target={'sets':1,'value':1,'unit':'per_side_reps','rest_seconds':0});rt,control=self.runtime(sid,action)
        self.consume(rt,action,self.frames[action]);data=self.fx.store.snapshot(self.fx.user,sid)
        self.assertEqual(data['status'],'completed');self.assertEqual((data['left_count'],data['right_count']),(1,1));self.assertEqual(data['total_count'],2)

    def test_pause_resets_candidate_before_return_and_old_capability_cannot_start(self):
        action='bodyweight_squat';sid=self.create(action);rt,control=self.runtime(sid,action)
        frames=self.frames[action];cut=next(index for index,frame in enumerate(frames) if frame['captured_ms']>=self.expected[action][0]['start_ms']+400)
        self.consume(rt,action,frames[:cut]);before=self.fx.store.snapshot(self.fx.user,sid)['total_count'];rt.adapter.reset('flow_stopped')
        # 只给这一未完成周期的返回部分，不把暂停前半次接回来。
        stop=self.expected[action][0]['end_ms']
        self.consume(rt,action,[frame for frame in frames[cut:] if frame['captured_ms']<=stop]);self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['total_count'],before)
        rt.data=self.fx.store.set_state(self.fx.user,sid,'ended_early',s.new_id())
        sid=self.create(action);control=self.manager.claim(self.fx.user,self.fx.auth,self.fx.now[0]+12*3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        current=self.manager.controls.snapshot(self.fx.user,sid)
        with patch('fitness_app.catalogue.get_action',return_value={'capability_version':'changed'}):
            response=self.fx.post(self.fx.client,f'/api/v1/sessions/{sid}/commands',{'command':'start','expected_version':self.fx.writer.snapshot(self.fx.user,sid)['state_version'],'control_token':control['control_token'],'controller_epoch':current['controller_epoch'],'stream_epoch':current['stream_epoch']})
        self.assertEqual(response.status_code,409,response.json);self.assertEqual(response.json['error']['code'],'STALE_CAPABILITY')

    def test_four_actions_video_executor_persists_cached_pose_events(self):
        for action,sample in self.samples.items():
            with self.subTest(action=action):
                frames=self.frames[action];poses={frame['captured_ms']:frame for frame in frames}
                class CachedPose:
                    def __init__(self,**unused):pass
                    def detect(inner,rgb,stamp):
                        frame=poses[stamp];self.assertEqual(rgb.shape[:2],(frame['height'],frame['width']));return self.pose_result(frame)
                    def close(inner):pass
                self.video.pose_factory=CachedPose
                iid=s.new_id();path=self.media._path(iid);path.parent.mkdir(parents=True,exist_ok=False)
                path.hardlink_to(sample.absolute_path);original_stat=sample.absolute_path.stat()
                info=probe_research_video(path)
                with av.open(str(path)) as container:
                    stream=container.streams.video[0];duration=round(float(stream.duration*stream.time_base)*1000)
                with self.fx.db.transaction() as c:
                    c.execute(s.media_inputs.insert().values(id=iid,user_id=self.fx.user,status='ready',sha256=sample.sha256,bytes=sample.bytes,duration_ms=duration,
                        encoding_json=json_text({**info,'validation':'internal_research_cached_pose_fixture'}),temporary_relative_path=path.relative_to(ROOT).as_posix(),expires_at_ms=self.fx.now[0]+3600000))
                sid=self.create(action,source='video',input_id=iid)
                response=self.fx.post(self.fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0});self.assertEqual(response.status_code,202,response.json)
                jid=response.json['data']['job_id'];self.video.runs[jid].thread.join(45);self.assertFalse(self.video.runs[jid].thread.is_alive())
                result=self.video.snapshot(self.fx.user,jid);self.assertEqual(result['status'],'completed',result);self.assertEqual(result['save_state'],'clean')
                self.assertEqual(result['session']['total_count'],len(self.expected[action]))
                self.assertFalse(path.exists());self.assertTrue(sample.absolute_path.exists());self.assertEqual(sample.absolute_path.stat().st_mtime_ns,original_stat.st_mtime_ns)

    def test_countdown_tick_primes_first_runtime_cycle(self):
        from test_action_counting import geometry
        action='bodyweight_squat';sid=self.create(action)
        self.manager.claim(self.fx.user,self.fx.auth,self.fx.now[0]+12*3600000,sid,s.new_id(),{'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=self.manager.live[sid];rt.data=self.fx.store.set_state(self.fx.user,sid,'countdown',s.new_id());rt.adapter=self.provider(action);rt.countdown_until=3.
        for index in range(20):
            self.fx.mono[0]=index*.15;self.consume(rt,action,[geometry(action,170,seq=index)])
        before=self.fx.store.snapshot(self.fx.user,sid);self.assertEqual(before['total_count'],0);self.assertIsNone(before['started_at_ms'])
        self.fx.mono[0]=3.;rt.stream={'preview_id':s.new_id(),'stream_epoch':'bound-runtime'}
        with patch.object(self.manager.previews,'snapshot',return_value={'status':'active','result':None}):self.manager.tick()
        rt.stream=None;self.assertEqual(rt.data['status'],'running')
        for index,value in enumerate([140,110,80,80,80,110,140,170,170,170,170]):
            self.fx.mono[0]=3.+index*.15;self.consume(rt,action,[geometry(action,value,seq=20+index)])
        saved=self.fx.store.snapshot(self.fx.user,sid,True);self.assertEqual(saved['total_count'],1)
        self.assertLess(saved['events'][0]['start_ms'],500)

    def test_visible_curl_side_passes_global_gate_without_inventing_other_side(self):
        from test_action_counting import sequence,geometry
        action='standing_dumbbell_curl';sid=self.create(action);rt,control=self.runtime(sid,action)
        frames=sequence(action);frames.append(geometry(action,170,seq=len(frames)))
        for frame in frames:
            for index in (12,14,16,24):frame['framing']['landmarks'][index]['presence']=0
        self.consume(rt,action,frames);saved=self.fx.store.snapshot(self.fx.user,sid)
        self.assertEqual((saved['left_count'],saved['right_count']),(1,0));self.assertEqual(saved['total_count'],1)

    def test_video_cancel_keeps_completed_real_cycle_not_pending_cycle(self):
        action='bodyweight_squat';sample=self.samples[action];frames=self.frames[action];poses={frame['captured_ms']:frame for frame in frames}
        cutoff=self.expected[action][1]['start_ms']+200;blocked_stamp=next(frame['captured_ms'] for frame in frames if frame['captured_ms']>=cutoff)
        entered=threading.Event();release=threading.Event()
        class BlockingCachedPose:
            def __init__(inner,**unused):pass
            def detect(inner,rgb,stamp):
                if stamp==blocked_stamp:entered.set();release.wait(10)
                return self.pose_result(poses[stamp])
            def close(inner):pass
        self.video.pose_factory=BlockingCachedPose
        iid=s.new_id();path=self.media._path(iid);path.parent.mkdir(parents=True,exist_ok=False);path.hardlink_to(sample.absolute_path)
        info=probe_research_video(path)
        with self.fx.db.transaction() as c:
            c.execute(s.media_inputs.insert().values(id=iid,user_id=self.fx.user,status='ready',sha256=sample.sha256,bytes=sample.bytes,
                duration_ms=frames[-1]['captured_ms'],encoding_json=json_text(info),temporary_relative_path=path.relative_to(ROOT).as_posix(),expires_at_ms=self.fx.now[0]+3600000))
        sid=self.create(action,source='video',input_id=iid);response=self.fx.post(self.fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0});self.assertEqual(response.status_code,202,response.json)
        jid=response.json['data']['job_id']
        try:
            self.assertTrue(entered.wait(10));before=self.video.snapshot(self.fx.user,jid);self.assertEqual(before['session']['total_count'],1)
            started=time.monotonic();response=self.fx.post(self.fx.client,f'/api/v1/jobs/{jid}/cancel',{'expected_version':before['state_version']})
            self.assertEqual(response.status_code,202,response.json);self.assertLess(time.monotonic()-started,2)
            self.assertFalse(response.json['data']['stopped'])
        finally:release.set()
        self.video.runs[jid].thread.join(10);after=self.video.snapshot(self.fx.user,jid)
        self.assertEqual(after['status'],'cancelled');self.assertTrue(after['stopped']);self.assertEqual(after['session']['total_count'],1)
        self.assertEqual(after['save_state'],'clean');self.assertFalse(path.exists());self.assertTrue(sample.absolute_path.exists())


if __name__=='__main__':unittest.main()
