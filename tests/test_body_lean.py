"""侧倾几何/独立方向/时间窗与撤回；合成测试不等同独立质量验收。"""
import copy
import gzip
import hashlib
import os
import json
import time
import unittest
from unittest.mock import patch
import numpy as np
from fitness_app.body_lean import VERSION,scene_axis,measure,SceneAxisTracker
from fitness_app.pretrained_input import body_features
from fitness_app.pretrained_quality import PretrainedObserver
from test_pretrained_correction import body
from fitness_app.paths import ROOT
from fitness_app.posture_rules import policy_version


def reference(angle=0,size=1000):
    return {'status':'observed','basis':VERSION,'image_up_degrees':angle,'width':size,'height':size}


class BodyLeanTests(unittest.TestCase):
    def test_whole_body_lean_not_cancelled_by_tilted_pelvis(self):
        pose=body(rotation=12)
        self.assertAlmostEqual(body_features(pose,1000,1000)['value'],0,places=5)
        measured=measure(pose,1000,1000,reference())
        self.assertTrue(measured['candidate']);self.assertAlmostEqual(measured['value'],12,places=5)

    def test_camera_roll_is_removed_without_removing_real_lean(self):
        straight=measure(body(rotation=15),1000,1000,reference(15))
        tilted=measure(body(rotation=27),1000,1000,reference(15))
        self.assertFalse(straight['candidate']);self.assertAlmostEqual(straight['value'],0,places=5)
        self.assertTrue(tilted['candidate']);self.assertAlmostEqual(tilted['value'],12,places=5)

    def test_raised_single_shoulder_is_not_whole_body_lean(self):
        pose=body();pose['keypoints'][5]['y']-=.06
        self.assertFalse(measure(pose,1000,1000,reference())['candidate'])

    def test_two_models_can_confirm_lean_with_level_shoulders_but_not_disagreement(self):
        from fitness_app.rtmpose_input import quality_landmarks
        pose=body(shift=.08);source=quality_landmarks(pose)
        result=measure(pose,1000,1000,reference(),source)
        self.assertTrue(result['cross_model_agreement']);self.assertTrue(result['candidate'])
        self.assertAlmostEqual(result['shoulder_degrees'],0,places=5)
        disagree=measure(pose,1000,1000,reference(),quality_landmarks(body(shift=-.08)))
        self.assertFalse(disagree['candidate']);self.assertFalse(disagree['cross_model_agreement'])

    def test_missing_wrong_geometry_and_side_view_are_refused(self):
        pose=body();pose['keypoints'][11]['score']=.1
        self.assertEqual(measure(pose,1000,1000,reference())['status'],'unassessable')
        self.assertEqual(measure(body(),1000,1000,reference(size=800))['status'],'unassessable')
        pose=body();pose['keypoints'][5]['x']=.47;pose['keypoints'][6]['x']=.53
        self.assertEqual(measure(pose,1000,1000,reference())['reason'],'unsupported_view')

    def test_scene_axis_requires_image_evidence_and_recovers_camera_roll(self):
        import cv2
        image=np.full((1000,1000,3),255,np.uint8)
        self.assertEqual(scene_axis(image,body())['status'],'unassessable')
        for x in (50,140,850,950):cv2.line(image,(x,20),(x,980),(0,0,0),3)
        axis=scene_axis(image,body());self.assertEqual(axis['status'],'observed',axis)
        self.assertAlmostEqual(axis['image_up_degrees'],0,delta=1.)
        rotated=cv2.warpAffine(image,cv2.getRotationMatrix2D((500,500),-12,1),(1000,1000),borderValue=(255,255,255))
        axis=scene_axis(rotated,body(rotation=12))
        self.assertEqual(axis['status'],'observed',axis);self.assertAlmostEqual(axis['image_up_degrees'],12,delta=1.5)

    def test_perspective_verticals_provide_local_axis(self):
        import cv2
        image=np.full((1000,1000,3),255,np.uint8);vp=(500,-1400)
        for bottom in (30,130,240,780,880,980):
            top=vp[0]+(bottom-vp[0])*(20-vp[1])/(980-vp[1])
            cv2.line(image,(round(top),20),(bottom,980),(0,0,0),3)
        axis=scene_axis(image,body());self.assertEqual(axis['status'],'observed',axis)
        self.assertEqual(axis['model'],'perspective');self.assertAlmostEqual(axis['image_up_degrees'],0,delta=1.5)

    def test_narrow_real_background_can_be_used_without_wide_padding_or_invented_axis(self):
        import cv2
        image=np.full((1000,2800,3),220,np.uint8);pose=body()
        for p in pose['keypoints']:p['x']=(900+p['x']*1000)/2800
        for x in (1120,1190,1610,1680):cv2.line(image,(x,20),(x,980),(20,20,20),4)
        axis=scene_axis(image,pose)
        self.assertEqual(axis['status'],'observed',axis)
        self.assertIn('analysis_region_pixels',axis)
        self.assertEqual(axis['model'],'parallel')
        self.assertEqual((axis['width'],axis['height']),(2800,1000))
        self.assertAlmostEqual(axis['image_up_degrees'],0,delta=1.5)
        self.assertFalse(measure(pose,2800,1000,axis)['candidate'])
        self.assertEqual(scene_axis(np.full_like(image,220),pose)['status'],'unassessable')

    def test_lat_pulldown_has_continuous_side_lean_and_rejects_identity_loss(self):
        observer=PretrainedObserver('lat_pulldown','fixture')
        feature=measure(body(rotation=12),1000,1000,reference())
        for t in range(0,1400,100):
            frame={'captured_ms':t,'framing':{'person_count':1},
                'pretrained':{'status':'observed','timestamp_ms':t,'body':body(rotation=12),'body_features':{'scene_lean':feature}}}
            item=observer.update(frame)[0]
        self.assertEqual(item['code'],'lat_pulldown.pretrained_upper_body_balance')
        self.assertEqual(item['status'],'issue')
        frame['captured_ms']=1400;frame['pretrained']['timestamp_ms']=1400;frame['identity_accepted']=False
        self.assertEqual(observer.update(frame)[0]['status'],'unassessable')
        frame['captured_ms']=1500;frame['pretrained']['timestamp_ms']=1500;frame['identity_accepted']=True
        self.assertEqual(observer.update(frame)[0]['status'],'unassessable')

    def test_two_to_ten_fps_real_time_still_requires_actual_observations_and_duration(self):
        feature=measure(body(rotation=12),1000,1000,reference())
        for interval in (100,200,250,280,333,400,500):
            observer=PretrainedObserver('lat_pulldown','fixture')
            for t in range(0,2501,interval):
                item=observer.update({'captured_ms':t,'pretrained':{'status':'observed','timestamp_ms':t,'body':body(rotation=12),
                    'body_features':{'scene_lean':feature}}})[0]
                if t<900:self.assertNotEqual(item['status'],'issue')
            self.assertEqual(item['status'],'issue',interval)
            late=observer.update({'captured_ms':t+700,'pretrained':{'status':'observed','timestamp_ms':t+700,
                'body':body(rotation=12),'body_features':{'scene_lean':feature}}})[0]
            self.assertNotEqual(late['status'],'issue')

    def test_putting_down_one_hand_withdraws_lat_lean_and_does_not_treat_getting_up_as_a_rep(self):
        observer=PretrainedObserver('lat_pulldown','fixture');pose=body(rotation=12)
        feature=measure(pose,1000,1000,reference())
        for t in range(0,1400,100):
            frame={'captured_ms':t,'pretrained':{'status':'observed','timestamp_ms':t,'body':copy.deepcopy(pose),
                'body_features':{'scene_lean':feature}}}
            item=observer.update(frame)[0]
        self.assertEqual(item['status'],'issue')
        frame['captured_ms']=1400;frame['pretrained']['timestamp_ms']=1400
        frame['pretrained']['body']['keypoints'][9]['y']=.7
        item=observer.update(frame)[0]
        self.assertEqual(item['status'],'unassessable')
        self.assertEqual(item['reason'],'exercise_not_engaged')
    def test_continuity_noise_missing_reference_and_gap(self):
        observer=PretrainedObserver('machine_chest_press','fixture')
        def frame(t,value=9,side='left'):
            feature={'status':'observed','value':value,'candidate':value>=6,'side':side,'direction_agreed':True,
                     'trunk_degrees':value,'shoulder_degrees':value}
            return {'captured_ms':t,'pretrained':{'status':'observed','timestamp_ms':t,
                'body_features':{'status':'observed','value':0,'scene_lean':feature},'hands':[]}}
        def lean(result):return next(i for i in result if i['code'].endswith('upper_body_balance'))
        for t in range(0,1300,100):item=lean(observer.update(frame(t,5 if t==400 else 9)))
        self.assertEqual(item['status'],'issue');self.assertEqual(item['side'],'left')
        lost=frame(1300);lost['pretrained']['body_features']['scene_lean']={'status':'unassessable','reason':'scene_reference_unavailable'}
        self.assertEqual(lean(observer.update(lost))['status'],'unassessable')
        self.assertEqual(lean(observer.update(frame(2000)))['status'],'unassessable')
        for t in range(2100,3300,100):item=lean(observer.update(frame(t,side='left' if t%200 else 'right')))
        self.assertNotEqual(item['status'],'issue')

    def test_reference_tracks_real_background_and_expires_without_recalibration(self):
        import cv2
        image=np.full((1000,1000,3),255,np.uint8)
        for x in (50,140,850,950):cv2.line(image,(x,20),(x,980),(0,0,0),3)
        random=np.random.default_rng(44)
        for _ in range(180):
            x,y=random.integers(10,990,2)
            if x<200 or x>800:cv2.circle(image,(int(x),int(y)),3,(70,70,70),-1)
        tracker=SceneAxisTracker();first=tracker.update(image,body(),0)
        self.assertEqual(first['status'],'observed',first)
        with patch('fitness_app.body_lean.scene_axis',return_value={'status':'unassessable','reason':'scene_reference_unavailable'}):
            tracked=tracker.update(image,body(),100)
            self.assertEqual(tracked['status'],'observed',tracked)
            self.assertEqual(tracked['direction_source'],'tracked_background')
            for stamp in range(200,3200,100):last=tracker.update(image,body(),stamp)
            self.assertEqual(last['status'],'unassessable')

    def test_background_loss_and_old_timestamp_never_reuse_an_axis(self):
        import cv2
        image=np.full((1000,1000,3),255,np.uint8)
        for x in (50,140,850,950):cv2.line(image,(x,20),(x,980),(0,0,0),3)
        tracker=SceneAxisTracker();self.assertEqual(tracker.update(image,body(),100)['status'],'observed')
        with patch('fitness_app.body_lean.scene_axis',return_value={'status':'unassessable','reason':'scene_reference_unavailable'}):
            self.assertEqual(tracker.update(np.full_like(image,255),body(),200)['status'],'unassessable')
            self.assertEqual(tracker.update(image,body(),150)['status'],'unassessable')


class _BodyLeanFixture(unittest.TestCase):
    def setUp(self):
        import test_formal_records as fixtures
        self.env=patch.dict(os.environ,{'FITNESS_PRETRAINED_CORRECTION':'0','FITNESS_BODY_LEAN':'1'});self.env.start();policy_version.cache_clear()
        self.fx=fixtures.RecordTests();self.fx.setUp()

    def tearDown(self):
        try:
            for hook in self.fx.app.extensions.get('stop_hooks',[]):hook()
            self.fx.tearDown()
        finally:self.env.stop();policy_version.cache_clear()


class BodyLeanBackendTests(_BodyLeanFixture):
    def test_real_cached_pair_survives_live_clock_offset_at_five_fps(self):
        from fitness_app import db_schema as s
        from fitness_app.frame_pipeline import analysis_framing
        from fitness_app.posture_engine import create_analysis_engine
        from fitness_app.presentation_assets import demo_resource
        from fitness_app.research_extract import atomic_json
        fx=self.fx;manager=fx.app.extensions['session_manager'];reviews=[]
        for number in ('1688','1689'):
            cache=ROOT/'data/experiments/open-source-correction'/('user-'+number+'-native-inputs.json.gz')
            with gzip.open(cache,'rb') as stream:raw=stream.read(64*1024*1024+1)
            self.assertLessEqual(len(raw),64*1024*1024);saved=json.loads(raw)
            self.assertEqual(saved['owner'],'fitness-native-input-cache')
            self.assertEqual(saved['video_sha256'],demo_resource('user-'+number)['video']['sha256'])
            sid=fx.create('lat_pulldown')
            manager.claim(fx.user,fx.auth,fx.now[0]+3600000,sid,s.new_id(),
                {'page_id':s.new_id(),'takeover':False,'expected_version':0})
            rt=manager.live[sid];rt.data=fx.store.set_state(fx.user,sid,'running',s.new_id())
            rt.session_origin_mono=0;rt.stream_time_offset_ms=7000;rt.adapter=create_analysis_engine('lat_pulldown')
            last=-200;observations=0
            for row in saved['rows']:
                stamp=row['t']
                if stamp-last<200:continue
                last=stamp;observations+=1;output=row['pose_result']
                frame={'captured_ms':stamp,'frame_seq':observations,'width':row['width'],'height':row['height'],
                    'framing':analysis_framing('lat_pulldown',output),'pretrained':output.get('pretrained')}
                fx.mono[0]=7+stamp/1000;manager._consume(rt,frame)
            record=fx.store.snapshot(fx.user,sid,True)
            issues=[v for v in record['posture_observations'] if v['status']=='issue']
            code='lat_pulldown.pretrained_upper_body_balance'
            self.assertEqual({v['code'] for v in issues},{code} if number=='1688' else set())
            self.assertTrue(all(v['start_ms']>=7000 for v in issues))
            reviews.append({'demo_id':'user-'+number,'source_sha256':saved['video_sha256'],
                'numeric_cache_sha256':hashlib.sha256(cache.read_bytes()).hexdigest(),'sampled_observations':observations,
                'sampling':'first_real_observation_at_least_200ms_after_previous_no_interpolation',
                'session_clock_offset_ms':7000,'policy_version':record['posture_policy_version'],
                'issues':[{k:v[k] for k in ('code','start_ms','end_ms','side','message')} for v in issues]})
            rt.data=fx.store.set_state(fx.user,sid,'ended_early',s.new_id(),reason='test_complete')
            manager._drop(rt)
        target=ROOT/'data/verification/round3/lat-pulldown-upload-review.json'
        document=json.loads(target.read_text(encoding='utf-8'))
        document['realtime_replay_results']={'scope':'real_cached_native_observations_through_live_session_not_new_camera_capture',
            'new_model_inferences':0,'private_media_uploaded':False,'videos':reviews}
        atomic_json(target,document)

    def test_live_session_offset_preserves_detected_issue_in_record_and_withdraws_stale_frame(self):
        from fitness_app import db_schema as s
        from fitness_app.posture_engine import create_analysis_engine
        from fitness_app.rtmpose_input import quality_landmarks
        fx=self.fx;manager=fx.app.extensions['session_manager'];sid=fx.create('lat_pulldown')
        manager.claim(fx.user,fx.auth,fx.now[0]+3600000,sid,s.new_id(),
            {'page_id':s.new_id(),'takeover':False,'expected_version':0})
        rt=manager.live[sid];rt.data=fx.store.set_state(fx.user,sid,'running',s.new_id())
        rt.session_origin_mono=0;rt.stream_time_offset_ms=7000;rt.adapter=create_analysis_engine('lat_pulldown')
        pose=body(rotation=12);feature=measure(pose,1000,1000,reference())
        for t in range(0,1400,100):
            f={'captured_ms':t,'frame_seq':t//100,'width':1000,'height':1000,
                'framing':{'usable':True,'ready_for_tracking':True,'code':'VISIBLE','person_count':1,
                    'landmarks':quality_landmarks(pose)},
                'pretrained':{'status':'observed','timestamp_ms':t,'body':pose,
                    'body_features':{'status':'observed','value':0,'scene_lean':feature}}}
            fx.mono[0]=7+t/1000;manager._consume(rt,f)
        result=manager.decorate_snapshot(fx.user,rt.data)
        code='lat_pulldown.pretrained_upper_body_balance'
        self.assertEqual(result['save_state'],'clean')
        self.assertEqual(result['coaching']['state'],'needs_adjustment')
        self.assertEqual(result['coaching']['primary_cue']['code'],code)
        issues=[v for v in fx.store.snapshot(fx.user,sid,True)['posture_observations']
            if v['code']==code and v['status']=='issue']
        self.assertTrue(issues)
        self.assertTrue(all(v['start_ms']>=7000 and v['end_ms']<=8300 for v in issues))
        f=copy.deepcopy(f);f['captured_ms']=1400;f['frame_seq']=14;fx.mono[0]=8.4
        manager._consume(rt,f)
        self.assertIsNone(manager.decorate_snapshot(fx.user,rt.data)['coaching']['primary_cue'])

    def test_shifted_session_clock_uses_same_source_frame_and_keeps_session_issue_times(self):
        from fitness_app.posture_engine import create_analysis_engine
        from fitness_app.rtmpose_input import quality_landmarks
        pose=body(rotation=12);feature=measure(pose,1000,1000,reference())
        engine=create_analysis_engine('lat_pulldown')
        for t in range(0,1400,100):
            frame={'captured_ms':t+7000,'source_captured_ms':t,'frame_seq':t//100,'width':1000,'height':1000,
                'framing':{'usable':True,'code':'VISIBLE','person_count':1,'landmarks':quality_landmarks(pose)},
                'pretrained':{'status':'observed','timestamp_ms':t,'body':pose,
                    'body_features':{'status':'observed','value':0,'scene_lean':feature}}}
            result=engine.process(frame,{'stream_epoch':'shifted-session-clock','coaching_view':'front'})
        items=result['observation']['posture']['items']
        issue=next(v for v in items if v['code']=='lat_pulldown.pretrained_upper_body_balance')
        self.assertEqual(issue['status'],'issue')
        self.assertGreaterEqual(issue['start_ms'],7000);self.assertEqual(issue['end_ms'],8300)
        frame['captured_ms']=8400;frame['source_captured_ms']=1400;frame['frame_seq']=14
        # 时间看起来属于当前会话也不能使上一张源图变成同帧证据。
        frame['pretrained']['timestamp_ms']=8400
        result=engine.process(frame,{'stream_epoch':'shifted-session-clock','coaching_view':'front'})
        issue=next(v for v in result['observation']['posture']['items'] if v['code']=='lat_pulldown.pretrained_upper_body_balance')
        self.assertEqual(issue['status'],'unassessable')
        self.assertEqual(issue['reason'],'pretrained_input_unavailable')

    def test_body_only_mode_reaches_saved_cue_highlight_and_immediate_withdrawal(self):
        from fitness_app.posture_engine import create_analysis_engine
        from fitness_app.rtmpose_input import quality_landmarks
        from fitness_app.coaching import coaching_snapshot,committed_issues
        from fitness_app.skeleton import skeleton_snapshot
        engine=create_analysis_engine('machine_chest_press');sid=self.fx.running('machine_chest_press')
        codes=[r['code'] for r in engine.rules if '.pretrained_' in r['code']]
        self.assertEqual(codes,['machine_chest_press.pretrained_upper_body_balance'])
        pose=body(rotation=12);feature=measure(pose,1000,1000,reference())
        for t in range(0,1400,100):
            frame={'captured_ms':t,'frame_seq':t//100,'width':1000,'height':1000,
                   'framing':{'usable':True,'code':'VISIBLE','person_count':1,'landmarks':quality_landmarks(pose)},
                   'pretrained':{'status':'observed','timestamp_ms':t,'body_features':{'status':'observed','value':0,'scene_lean':feature}}}
            result=engine.process(frame,{'stream_epoch':'body-lean-store','coaching_view':'front'})
            if result.get('posture_findings'):
                saved=self.fx.writer.submit(self.fx.user,sid,self.fx.auth,'posture',{'set_index':1,'findings':result['posture_findings'],'active_ms':t})
                self.assertEqual(saved['save_state'],'clean')
        feedback=committed_issues(result);self.assertTrue(feedback)
        frame['analysis']=result['observation'];session={'status':'running','action_id':'machine_chest_press','supported_items':engine.rules}
        cue=coaching_snapshot(session,frame,feedback)
        self.assertEqual(cue['state'],'needs_adjustment');self.assertIn('侧持续倾斜',cue['primary_cue']['message'])
        self.assertTrue(skeleton_snapshot(frame,cue)['highlight'])
        self.assertTrue(any(i['status']=='issue' and i['code']==codes[0] for i in self.fx.store.snapshot(self.fx.user,sid,True)['posture_observations']))
        frame=copy.deepcopy(frame);frame['captured_ms']+=100;frame['frame_seq']+=1
        frame['pretrained']['timestamp_ms']=frame['captured_ms'];frame['pretrained']['body_features']['scene_lean']={'status':'unassessable','reason':'scene_reference_unavailable'}
        frame['analysis']=engine.process(frame,{'stream_epoch':'body-lean-store','coaching_view':'front'})['observation']
        cue=coaching_snapshot(session,frame,feedback,100)
        self.assertIsNone(cue['primary_cue']);self.assertFalse(skeleton_snapshot(frame,cue)['highlight'])
        self.assertEqual(self.fx.client.get('/api/v1/correction-models').status_code,404)


class BodyLeanVideoTests(_BodyLeanFixture):
    # 重型原片验证单独选择执行，避免普通几何回归重复推理原视频。
    def test_real_lat_pair_runs_native_video_jobs_and_persists_only_detected_issues(self):
        from fitness_app.presentation_assets import demo_resource
        from fitness_app.catalogue import get_action
        from fitness_app.research_extract import atomic_json
        fx=self.fx;inputs=fx.app.extensions['media_inputs'];manager=fx.app.extensions['video_jobs'];reviews=[]
        captured=[]
        capture=os.environ.get('FITNESS_CAPTURE_NATIVE_INPUTS')=='1'
        if capture:
            from fitness_app.gym_pose import GymPoseService
            original=GymPoseService.detect
            def collect(model,rgb,stamp):
                output=original(model,rgb,stamp)
                captured.append({'t':stamp,'width':rgb.shape[1],'height':rgb.shape[0],'pose_result':copy.deepcopy(output)})
                return output
            instrument=patch.object(GymPoseService,'detect',collect);instrument.start()
            self.addCleanup(instrument.stop)
        for number in ('1688','1689'):
            captured.clear()
            response=fx.post(fx.client,'/api/v1/demo-inputs',{'demo_id':'user-'+number})
            self.assertEqual(response.status_code,202,response.json);iid=response.json['data']['input_id']
            inputs.threads[iid].join(15);self.assertEqual(inputs.snapshot(fx.user,iid)['status'],'ready')
            response=fx.post(fx.client,'/api/v1/sessions',{'input_id':iid,'action_id':'lat_pulldown',
                'capability_version':get_action('lat_pulldown')['capability_version'],'source':'demo','mode':'free',
                'target':None,'demo_id':'user-'+number,'coaching_view':'auto'})
            self.assertEqual(response.status_code,201,response.json);sid=response.json['data']['session_id']
            response=fx.post(fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0})
            self.assertEqual(response.status_code,202,response.json);jid=response.json['data']['job_id'];run=manager.runs[jid]
            run.thread.join(600);self.assertFalse(run.thread.is_alive(),'实际视觉任务未结束，不能当作完成')
            result=manager.snapshot(fx.user,jid);self.assertEqual(result['status'],'completed',result.get('error_code'))
            record=fx.store.snapshot(fx.user,sid,True)
            issues=[v for v in record['posture_observations'] if v['code']=='lat_pulldown.pretrained_upper_body_balance' and v['status']=='issue']
            backends=sorted({v['evidence']['body_execution_backend'] for v in record['posture_observations']
                if v['code']=='lat_pulldown.pretrained_upper_body_balance' and v.get('evidence',{}).get('body_execution_backend')})
            if os.environ.get('FITNESS_POSE_DEVICE')=='cuda':self.assertEqual(backends,['torch_cuda_fp32'])
            self.assertEqual(bool(issues),number=='1688',[(v['start_ms'],v['end_ms']) for v in issues])
            self.assertTrue(all(v['evidence'].get('basis')==VERSION for v in issues))
            self.assertFalse(inputs._path(iid).exists())
            reviews.append({'demo_id':'user-'+number,'source_sha256':demo_resource('user-'+number)['video']['sha256'],
                'status':result['status'],'policy_version':record['posture_policy_version'],
                'issues':[{k:v[k] for k in ('start_ms','end_ms','side','message')} for v in issues],
                'all_issue_codes':sorted({v['code'] for v in record['posture_observations'] if v['status']=='issue'}),
                'body_execution_backends':backends,
                'copy_cleaned':True,'personal_statistics_excluded':True})
            if capture:
                from fitness_app.database import json_text
                from fitness_app.paths import project_path
                cache=project_path('data/experiments/open-source-correction/user-'+number+'-native-inputs.json.gz')
                if cache.exists():
                    with gzip.open(cache,'rb') as stream:old=stream.read(64*1024*1024+1)
                    self.assertLessEqual(len(old),64*1024*1024)
                    old=json.loads(old)
                    self.assertEqual(old['owner'],'fitness-native-input-cache')
                    self.assertEqual(old['video_sha256'],reviews[-1]['source_sha256'])
                payload={'schema_version':1,'owner':'fitness-native-input-cache','demo_id':'user-'+number,
                    'video_sha256':reviews[-1]['source_sha256'],'private_media_uploaded':False,'rows':captured,
                    'runtime_flags':{key:os.environ.get(key)=='1' for key in
                        ('FITNESS_BODY_LEAN','FITNESS_MOVEMENT_GUIDANCE','FITNESS_PRETRAINED_CORRECTION')},
                    'input_code_sha256':{name:hashlib.sha256((ROOT/'fitness_app'/name).read_bytes()).hexdigest() for name in
                        ('pose.py','pose_input.py','content_bounds.py','gym_pose.py','pretrained_input.py','rtmpose_input.py','body_lean.py')}}
                encoded=gzip.compress(json_text(payload).encode('utf-8'),compresslevel=5,mtime=0)
                temporary=project_path(str(cache)+'.part');owned=False
                try:
                    with temporary.open('xb') as stream:owned=True;stream.write(encoded)
                    temporary.replace(cache)
                finally:
                    if owned and temporary.exists():temporary.unlink()
                reviews[-1]['numeric_cache']={'path':cache.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(encoded).hexdigest(),
                    'frames':len(captured),'bytes':len(encoded),'real_model_outputs_unmodified':True}
            print(json.dumps({'native_video':number,'detected_side_lean_ranges':len(issues),
                'all_issue_codes':reviews[-1]['all_issue_codes']},ensure_ascii=False),flush=True)
        self.assertEqual(fx.store.list_records(fx.user)['total'],0)
        target=ROOT/'data/verification/round3/lat-pulldown-upload-review.json'
        previous=json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
        configurations=previous.get('configuration_results',{})
        if previous.get('videos') and not configurations:
            old_key='body_and_guidance' if previous['videos'][0]['policy_version'].endswith('-guidance') else 'body_only'
            configurations[old_key]=previous['videos']
        guided=os.environ.get('FITNESS_MOVEMENT_GUIDANCE')=='1'
        configuration='body_and_guidance' if guided else 'body_only'
        if all(v['body_execution_backends']==['torch_cuda_fp32'] for v in reviews):configuration+='_torch_cuda'
        configurations[configuration]=reviews
        atomic_json(target,{**({'realtime_replay_results':previous['realtime_replay_results']} if previous.get('realtime_replay_results') else {}),
            'scope':'real_native_video_jobs_development_pair_not_independent_accuracy','paid_api_calls':0,
            'runtime_flags':{'FITNESS_BODY_LEAN':True,'FITNESS_MOVEMENT_GUIDANCE':guided,'FITNESS_PRETRAINED_CORRECTION':False,
                'FITNESS_POSE_DEVICE':os.environ.get('FITNESS_POSE_DEVICE','auto')},
            'videos':reviews,'configuration_results':configurations})

    def test_real_1694_demo_upload_runs_real_models_and_saves_lean(self):
        from fitness_app.presentation_assets import demo_resource
        from fitness_app.research_extract import atomic_json
        from fitness_app.catalogue import get_action
        fx=self.fx;inputs=fx.app.extensions['media_inputs'];manager=fx.app.extensions['video_jobs']
        response=fx.post(fx.client,'/api/v1/demo-inputs',{'demo_id':'user-1694'})
        self.assertEqual(response.status_code,202,response.json);iid=response.json['data']['input_id']
        inputs.threads[iid].join(15);self.assertEqual(inputs.snapshot(fx.user,iid)['status'],'ready')
        action=get_action('machine_chest_press')
        response=fx.post(fx.client,'/api/v1/sessions',{'input_id':iid,'action_id':action['action_id'],
            'capability_version':action['capability_version'],'source':'demo','mode':'free','target':None,'demo_id':'user-1694','coaching_view':'auto'})
        self.assertEqual(response.status_code,201,response.json);sid=response.json['data']['session_id']
        response=fx.post(fx.client,'/api/v1/jobs/video',{'session_id':sid,'expected_version':0})
        self.assertEqual(response.status_code,202,response.json);pid=response.json['data']['job_id'];run=manager.runs[pid]
        deadline=time.monotonic()+180
        while run.thread.is_alive() and time.monotonic()<deadline:run.thread.join(1)
        self.assertFalse(run.thread.is_alive(),'原片处理未结束，不能视为完成')
        result=manager.snapshot(fx.user,pid);self.assertEqual(result['status'],'completed',result.get('error_code'))
        record=fx.store.snapshot(fx.user,sid,True)
        code='machine_chest_press.pretrained_upper_body_balance'
        issues=[f for f in record['posture_observations'] if f['code']==code and f['status']=='issue']
        self.assertTrue(issues);self.assertTrue(all(f['side'] in ('left','right') for f in issues))
        self.assertTrue(all(f['evidence'].get('basis')==VERSION for f in issues))
        self.assertFalse(inputs._path(iid).exists());self.assertEqual(fx.store.list_records(fx.user)['total'],0)
        atomic_json(ROOT/'data/verification/round3/body-lean-upload-review.json',{
            'scope':'real_local_demo_upload_manual_action_not_independent_accuracy','source_id':'user-1694',
            'source_sha256':demo_resource('user-1694')['video']['sha256'],'status':result['status'],
            'policy_version':record['posture_policy_version'],'stored_lean_issue_ranges':[
                {key:f[key] for key in ('start_ms','end_ms','side','message')} for f in issues],
            'source_copy_cleaned':True,'excluded_from_personal_statistics':True,'paid_api_calls':0})


if __name__=='__main__':unittest.main()
