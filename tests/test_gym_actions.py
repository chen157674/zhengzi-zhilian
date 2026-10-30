"""新增动作契约、拒判和反馈保存测试；合成轨迹不作为真实识别准确率。"""
import copy,math,unittest
from unittest.mock import patch
import numpy as np
from fitness_app.gym_catalogue import ACTION_IDS,BY_ID
from fitness_app.catalogue import definitions,get_action
from fitness_app.frame_pipeline import REQUIRED
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.posture_rules import rules_for
from fitness_app.basic_recognition import BasicRecognizer
from fitness_app.gym_recognition import candidate,resources
from fitness_app.skeleton import skeleton_snapshot
from fitness_app.automatic_analysis import AutomaticAnalysis
from fitness_app.automatic_recording import AutomaticRecorder


def raise_sequence(asymmetric=False):
    frames=[]
    for i in range(42):
        p=[{'x':.5,'y':.5,'z':0.,'visibility':1.,'presence':1.,'valid':True} for _ in range(33)]
        for s,direction in ((0,-1),(1,1)):
            shoulder=(.35 if s==0 else .65,.35)
            theta=math.radians(45+20*math.sin(i*math.pi/10)+(35 if asymmetric and s==0 else 0))
            for idx,length in ((13,.12),(15,.24)):
                p[idx+s].update(x=shoulder[0]+direction*length*math.sin(theta),y=shoulder[1]+length*math.cos(theta))
            p[11+s].update(x=shoulder[0],y=shoulder[1]);p[23+s].update(x=shoulder[0],y=.65)
        frames.append({'frame_seq':i,'captured_ms':i*100,'width':800,'height':800,
                       'framing':{'person_count':1,'usable':True,'code':'VISIBLE','landmarks':p}})
    return frames


class GymTests(unittest.TestCase):
    def test_catalogue_pipeline_and_frozen_counter_resources(self):
        actions=definitions();self.assertEqual(len(actions),33);self.assertEqual(len({a['action_id'] for a in actions}),33)
        for action in ACTION_IDS:
            self.assertIn(action,REQUIRED);self.assertTrue(rules_for(action));engine=create_analysis_engine(action)
            self.assertIsNotNone(engine);self.assertFalse(engine.counting_available)
            for frame in raise_sequence()[:2]:engine.process(frame,{'stream_epoch':'test','coaching_view':'auto'})
        self.assertTrue(get_action('bodyweight_squat')['counting_available'])
        self.assertTrue(get_action('barbell_bent_over_row')['counting_available'])
        r=BasicRecognizer().snapshot();self.assertEqual(set(r['zero_shot_actions']),set(ACTION_IDS));self.assertEqual(len(r['base_trained_actions']),9)

    def test_lateral_raise_feedback_normal_asymmetry_and_highlight(self):
        for asym,status in ((False,'not_observed'),(True,'issue')):
            engine=create_analysis_engine('dumbbell_lateral_raise')
            for f in raise_sequence(asym):out=engine.process(f,{'coaching_view':'front','stream_epoch':'a'})
            item=next(i for i in out['observation']['posture']['items'] if i['code'].endswith('arm_asymmetry'))
            self.assertEqual(item['status'],status);self.assertEqual(out['events'],[])
            if asym:
                self.assertTrue(item['suggestion']);skel=skeleton_snapshot(f,{'state':'needs_adjustment','primary_cue':{**item,'cue_id':'test'}})
                self.assertEqual(skel['highlight']['joint_indices'],[13,14,15,16])

    def test_missing_joints_and_bad_view_never_report_normal(self):
        for mode in ('missing','multi','view'):
            engine=create_analysis_engine('dumbbell_lateral_raise')
            frames=raise_sequence(True)
            for frame in frames:
                if mode=='missing':frame['framing']['landmarks'][15]['valid']=False
                if mode=='multi':frame['framing']['person_count']=2
                out=engine.process(frame,{'coaching_view':'side' if mode=='view' else 'front','stream_epoch':'a'})
            item=next(i for i in out['observation']['posture']['items'] if i['code'].endswith('arm_asymmetry'))
            self.assertEqual(item['status'],'unassessable')

    def test_sway_and_upper_arm_feedback_respect_motion_and_projection(self):
        from fitness_app.gym_quality import evaluate
        from test_barbell_row import sequence
        stable=sequence()
        self.assertEqual(evaluate('seated_cable_row.trunk_sway',stable,'side',18.)['status'],'not_observed')
        swaying=copy.deepcopy(stable)
        for i,f in enumerate(swaying):
            for s in (0,1):f['framing']['landmarks'][11+s]['x']+=.18*math.sin(i*math.pi/len(swaying))
        self.assertEqual(evaluate('seated_cable_row.trunk_sway',swaying,'side',18.)['status'],'issue')
        moving=raise_sequence()
        self.assertEqual(evaluate('cable_triceps_pushdown.upper_arm_swing',moving,'side',25.)['status'],'issue')
        for f in moving:
            for p in f['framing']['landmarks']:p['valid']=False
        self.assertEqual(evaluate('cable_triceps_pushdown.upper_arm_swing',moving,'side',25.)['status'],'unassessable')

    def test_visual_motion_gate_stationary_stale_and_ambiguous(self):
        resource,_=resources();frames=raise_sequence();stamp=frames[-1]['captured_ms']
        index=resource['labels'].index('dumbbell_lateral_raise');feature=resource['features'][index]
        images=[(stamp-1000,feature),(stamp-500,feature),(stamp,feature)]
        self.assertEqual(candidate(resource,images,frames,stamp)['candidate_action_id'],'dumbbell_lateral_raise')
        rest=[{**copy.deepcopy(frames[0]),'frame_seq':i,'captured_ms':i*100} for i in range(42)]
        self.assertIsNone(candidate(resource,images,rest,stamp)['candidate_action_id'])
        self.assertIsNone(candidate(resource,images,frames,stamp+1000)['candidate_action_id'])
        modified={**resource,'features':resource['features'].copy()}
        modified['features'][resource['labels'].index('dumbbell_front_raise')]=feature
        self.assertIsNone(candidate(modified,images,frames,stamp)['candidate_action_id'])

    def test_automatic_extension_uses_features_and_clears_on_gap(self):
        r=BasicRecognizer();resource,_=resources();vector=resource['features'][resource['labels'].index('dumbbell_lateral_raise')]
        r.gym_head=None  # 本项单独验证零样本路径，训练分类头在真实素材回放验证。
        frames=raise_sequence()
        for f in frames:result=r.process(f,vector if f['frame_seq']%5==0 else None)
        self.assertEqual(result['action_id'],'dumbbell_lateral_raise');self.assertIsNone(result['score'])
        f=copy.deepcopy(frames[-1]);f.update(frame_seq=100,captured_ms=10000)
        self.assertIsNone(r.process(f)['action_id'])

    def test_uncertain_closed_set_does_not_block_a_supported_new_class(self):
        resource,_=resources();vector=resource['features'][resource['labels'].index('dumbbell_lateral_raise')]
        frames=raise_sequence();stamp=frames[-1]['captured_ms'];images=[(stamp-1000,vector),(stamp-500,vector),(stamp,vector)]
        class Head:
            classes_=np.array(['pec_deck_fly','standard_push_up','bodyweight_squat'])
            def predict_proba(self,values):return np.tile([.45,.4,.15],(len(values),1))
        head={'sha256':'fixture','new_actions':['pec_deck_fly'],'actions':Head.classes_.tolist(),'config':{'minimum_score':.7,'minimum_margin':.2}}
        with patch('fitness_app.gym_recognition.load_head',return_value=Head()):
            result=candidate(resource,images,frames,stamp,head)
        self.assertEqual(result['candidate_action_id'],'dumbbell_lateral_raise')

    def test_fly_classification_does_not_invent_hidden_wrists_for_quality(self):
        from fitness_app.gym_recognition import motion_evidence
        from fitness_app.gym_motion import motion_gate
        from fitness_app.gym_quality import evaluate
        frames=raise_sequence()
        for f in frames:
            for i in (15,16):f['framing']['landmarks'][i]['valid']=False
        self.assertTrue(motion_gate('reverse_pec_deck_fly',motion_evidence('reverse_pec_deck_fly',frames)))
        self.assertEqual(evaluate('reverse_pec_deck_fly.arm_asymmetry',frames,'front',.25)['status'],'unassessable')


class GymPersistenceTests(unittest.TestCase):
    def test_new_action_feedback_persists_without_fake_reps(self):
        import test_formal_records as shared
        fx=shared.RecordTests();fx.setUp()
        try:
            recorder=AutomaticRecorder(fx.store,fx.user,fx.auth,'00000000-0000-4000-8000-000000000022')
            analysis=AutomaticAnalysis(recorder=recorder)
            for f in raise_sequence(True):
                result=analysis.process(f,{'action_id':'dumbbell_lateral_raise','state':'recognized','available':True,
                                          'model_version':'manual-selection','decision_basis':'user_selected'})
            self.assertFalse(recorder.failed,getattr(recorder,'error_detail',''))
            self.assertEqual(result['coaching']['state'],'needs_adjustment');recorder.close('ended')
            self.assertTrue(recorder.record_ids)
            data=fx.store.snapshot(fx.user,recorder.record_ids[0],include_details=True)
            self.assertEqual(data['action_id'],'dumbbell_lateral_raise')
            self.assertTrue(any(x['status']=='issue' for x in data['posture_observations']))
            self.assertFalse(data['events'])
            response=fx.client.get('/api/v1/actions');self.assertEqual(response.status_code,200)
            page=fx.client.get('/actions').get_data(as_text=True);self.assertIn('33 个动作',page);self.assertIn('蝴蝶机夹胸',page)
            self.assertIn('data-filter="绳索"',page)
            overview=fx.client.get('/api/v1/account/training-overview?action_id=dumbbell_lateral_raise&code=dumbbell_lateral_raise.arm_asymmetry').json['data']
            self.assertEqual(overview['issue_record_count'],1);self.assertIsNone(overview['ratio'])
        finally:fx.tearDown()


class GymPoseTests(unittest.TestCase):
    def test_joint_visibility_change_is_not_a_different_person(self):
        from fitness_app.sessions import IdentityContinuity
        f=raise_sequence()[0]
        # 很长的侧面躯干与较窄肩线：髋遮挡前后不是同一个尺度定义。
        for i in (11,12):f['framing']['landmarks'][i].update(x=.45+(i-11)*.1,y=.2)
        for i in (23,24):f['framing']['landmarks'][i].update(x=.45+(i-23)*.1,y=.8)
        tracker=IdentityContinuity();self.assertTrue(tracker.accept(f))
        cropped=copy.deepcopy(f)
        for i in (23,24):cropped['framing']['landmarks'][i]['valid']=False
        self.assertTrue(tracker.accept(cropped));self.assertTrue(tracker.accept(f))
        jumped=copy.deepcopy(cropped)
        for i in (11,12):jumped['framing']['landmarks'][i]['x']+=.4
        self.assertFalse(tracker.accept(jumped))
        changed=copy.deepcopy(f)
        self.assertTrue(tracker.accept(cropped))
        for i in (11,12):changed['framing']['landmarks'][i]['valid']=False
        self.assertFalse(tracker.accept(changed))

    def test_fallback_maps_only_original_pixels_and_keeps_multiple_candidates(self):
        from fitness_app.gym_pose import GymPoseService
        from fitness_app.pose_input import input_policy_snapshot
        class FakePose:
            instances=[]
            def __init__(self,**kwargs):self.closed=False;self.calls=0;self.video=kwargs['video'];self.instances.append(self)
            def detect(self,rgb,stamp):
                self.calls+=1
                if self is self.instances[0]:return {'poses':[],'model':'fake'}
                point={'x':.5,'y':.5,'z':.1,'presence':1.,'visibility':1.}
                pose=[dict(point) for _ in range(33)];pose[27]['y']=.9
                return {'poses':[pose,copy.deepcopy(pose)],'model':'fake'}
            def close(self):self.closed=True
        rgb=np.full((100,160,3),150,dtype=np.uint8)
        with GymPoseService(video=True,pose_factory=FakePose) as service:
            result=service.detect(rgb,100)
            self.assertEqual(len(result['poses']),2)
            self.assertAlmostEqual(result['poses'][0][11]['y'],.75)
            self.assertEqual(result['poses'][0][27]['visibility'],0)
            self.assertGreater(result['poses'][0][27]['y'],1)
            self.assertRaises(ValueError,service.detect,rgb,100)
        self.assertTrue(all(p.closed for p in FakePose.instances))
        self.assertNotEqual(input_policy_snapshot(GymPoseService)['version'],'test-override')

    def test_successful_original_detection_does_not_run_fallback(self):
        from fitness_app.gym_pose import GymPoseService
        class Present:
            instances=[]
            def __init__(self,**kwargs):self.instances.append(self)
            def detect(self,rgb,stamp):return {'poses':[[]],'model':'fake'}
            def close(self):pass
        with GymPoseService(video=True,pose_factory=Present) as service:service.detect(np.full((100,160,3),150,dtype=np.uint8),100)
        self.assertEqual(len(Present.instances),1)


if __name__=='__main__':unittest.main()
