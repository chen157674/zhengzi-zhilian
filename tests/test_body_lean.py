"""侧倾几何/独立方向/时间窗与撤回；合成测试不等同独立质量验收。"""
import copy
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


if __name__=='__main__':unittest.main()
