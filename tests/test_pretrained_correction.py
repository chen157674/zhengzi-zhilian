"""预训练候选的拒判、时间边界和新API权限；不以工程检查冒充模型准确率。"""
import copy
import io
import math
import os
import pickle
import unittest
from unittest.mock import patch
import numpy as np
from fitness_app.pretrained_input import body_features,PretrainedFrameModel
from fitness_app.pretrained_quality import PretrainedObserver
from fitness_app.exercise_feedback import _NumbersOnlyUnpickler
from fitness_app.posture_rules import policy_version,rules_for
import test_formal_records as fixtures


def body(shift=0,rotation=0):
    xy={5:(.35+shift,.3),6:(.65+shift,.3),11:(.4,.6),12:(.6,.6),
        7:(.25,.4),8:(.75,.4),9:(.3,.2),10:(.7,.2)}
    points=[];theta=math.radians(rotation);matrix=np.array([[math.cos(theta),-math.sin(theta)],[math.sin(theta),math.cos(theta)]])
    for i in range(17):
        p=matrix@(np.array(xy.get(i,(.5,.2)))-.5)+.5
        points.append({'x':float(p[0]),'y':float(p[1]),'score':.9,'coordinate_valid':True})
    return {'keypoints':points}


class PretrainedTests(unittest.TestCase):
    def test_body_reference_is_rotation_invariant_and_neutral_is_not_lean(self):
        upright=body_features(body(),1000,1000);lean=body_features(body(.08),1000,1000)
        rotated=body_features(body(.08,15),1000,1000)
        self.assertAlmostEqual(upright['value'],0,places=5)
        self.assertGreater(lean['value'],8);self.assertAlmostEqual(lean['value'],rotated['value'],places=5)

    def test_missing_core_point_refuses_body_reference(self):
        pose=body();pose['keypoints'][11]['score']=.1
        self.assertEqual(body_features(pose,1000,1000)['status'],'unassessable')

    def test_continuous_confirmation_withdrawal_and_gap(self):
        observer=PretrainedObserver('machine_chest_press','fixture')
        def frame(t,value=12):
            return {'captured_ms':t,'pretrained':{'status':'observed','timestamp_ms':t,
                'body_features':{'status':'observed','value':value,'elbow_angles':{'left':90+(t%300)/10},
                    'scene_lean':{'status':'observed','value':value,'candidate':value>=6,'side':'left','direction_agreed':True,
                                  'trunk_degrees':value,'shoulder_degrees':value}},
                'hands':[{'body_wrist_index':9,'observation_with_body_wrist':{'assessed_fingers':4,'extended_fingers':3}}]}}
        for t in range(0,700,100):result=observer.update(frame(t))
        self.assertFalse(any(r['status']=='issue' for r in result))
        for t in range(700,1100,100):result=observer.update(frame(t))
        self.assertTrue(all(r['status']=='issue' for r in result))
        result=observer.update({'captured_ms':1100})
        self.assertTrue(all(r['status']=='unassessable' for r in result))
        result=observer.update(frame(2000))
        self.assertFalse(any(r['status']=='issue' for r in result))

    def test_one_hidden_hand_cannot_be_reported_as_bilateral_no_pattern(self):
        observer=PretrainedObserver('machine_chest_press','fixture')
        data={'status':'observed','timestamp_ms':100,'body_features':{'status':'observed','value':0},
              'hands':[{'body_wrist_index':9,'observation_with_body_wrist':{'assessed_fingers':4,'extended_fingers':0}}]}
        item=next(i for i in observer.update({'captured_ms':100,'pretrained':data}) if i['code'].endswith('finger_extension'))
        self.assertEqual(item['status'],'unassessable');self.assertEqual(item['reason'],'pretrained_one_hand_occluded')
        data['timestamp_ms']=200;data['hands'].append({'body_wrist_index':10,'observation_with_body_wrist':{'assessed_fingers':4,'extended_fingers':0}})
        item=next(i for i in observer.update({'captured_ms':200,'pretrained':data}) if i['code'].endswith('finger_extension'))
        self.assertEqual(item['status'],'not_observed')

    def test_alternating_hands_do_not_accumulate_one_continuous_issue(self):
        observer=PretrainedObserver('machine_chest_press','fixture')
        for t in range(0,1300,100):
            data={'status':'observed','timestamp_ms':t,'body_features':{'status':'observed','value':0,
                  'elbow_angles':{'left':90+(t%300)/10,'right':90+(t%300)/10}},
                  'hands':[{'body_wrist_index':9 if t%200 else 10,
                            'observation_with_body_wrist':{'assessed_fingers':4,'extended_fingers':3}}]}
            result=observer.update({'captured_ms':t,'pretrained':data})
            self.assertFalse(any(i['status']=='issue' for i in result))

    def test_pretrained_error_survives_up_and_down_phase_transition(self):
        class Model:
            def observe(self,points):return {'status':'candidate','label':points,'confidence':.95}
        observer=PretrainedObserver('barbell_bench_press','fixture');observer.model=Model()
        for t in range(0,1200,100):
            data={'status':'observed','timestamp_ms':t,'source_landmarks':'b_arms_spread_up' if t%200 else 'b_arms_spread_down'}
            result=observer.update({'captured_ms':t,'pretrained':data})
        self.assertEqual(result[0]['status'],'issue')

    def test_old_pretrained_timestamp_is_rejected(self):
        result=PretrainedObserver('machine_chest_press','fixture').update(
            {'captured_ms':500,'pretrained':{'status':'observed','timestamp_ms':100}})
        self.assertTrue(all(r['status']=='unassessable' for r in result))

    def test_pickle_cannot_invoke_arbitrary_globals(self):
        with self.assertRaises(pickle.UnpicklingError):_NumbersOnlyUnpickler(io.BytesIO(pickle.dumps(eval))).load()

    def test_input_observation_does_not_change_counting_pose_or_world(self):
        model=object.__new__(PretrainedFrameModel)
        class Body:
            resource={'sha256':'body'}
            def detect(self,rgb,bbox):return body()
        class Hands:
            class Part:
                resource={'sha256':'hand'}
                def detect(self,rgb,bbox):return {'keypoints':[]}
            pose=Part();detector=Part()
            def detect(self,rgb):return {'hands':[],'reason':'hands_not_detected'}
        model.body=Body();model.hands=Hands()
        original={'poses':[[{'x':.2,'y':.3,'z':.4,'visibility':.9,'presence':.9} for _ in range(33)]],
                  'world_poses':[[{'x':1,'y':2,'z':3} for _ in range(33)]]}
        before=copy.deepcopy(original)
        result=model.observe(np.zeros((200,200,3),np.uint8),original,100)
        self.assertEqual(original,before);self.assertFalse(result['counting_input_modified'])

    def test_default_rule_set_and_opt_in_are_separate(self):
        try:
            with patch.dict(os.environ,{'FITNESS_PRETRAINED_CORRECTION':'0'}):
                policy_version.cache_clear();base=policy_version()
                self.assertFalse(any('.pretrained_' in row['code'] for row in rules_for('machine_chest_press')))
            with patch.dict(os.environ,{'FITNESS_PRETRAINED_CORRECTION':'1'}):
                policy_version.cache_clear();version=policy_version()
                self.assertNotEqual(base,version)
                self.assertEqual(sum('.pretrained_' in row['code'] for row in rules_for('machine_chest_press')),2)
        finally:policy_version.cache_clear()


class ReferenceFixture:
    def compare(self,a,b,action,reference_action):return {'status':'candidate','correctness':None,'action_id':action,'angles':[]}


class ExerciseFixture:
    def __init__(self,action):self.action=action
    def observe(self,points):return {'status':'candidate','action_id':self.action,'label':'s_correct_up','correctness':None}


class PretrainedApiTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.RecordTests();self.fx.config_overrides={'PRETRAINED_CORRECTION_ENABLED':True,
            'PRETRAINED_REFERENCE_FACTORY':ReferenceFixture,'PRETRAINED_EXERCISE_FACTORY':ExerciseFixture};self.fx.setUp()

    def tearDown(self):self.fx.tearDown()

    def test_authentication_and_csrf_are_enforced(self):
        url='/api/v1/correction-models/reference-compare'
        self.assertEqual(self.fx.app.test_client().get('/api/v1/correction-models').status_code,401)
        self.assertEqual(self.fx.client.post(url,json={}).status_code,403)
        self.assertEqual(self.fx.client.get('/api/v1/correction-models').status_code,200)

    def test_reference_bounds_and_action_mismatch(self):
        data={'action_id':'barbell_bench_press','reference_action_id':'barbell_bench_press',
              'reference_angles':[[90.]*23]*4,'observed_angles':[[100.]*23]*4}
        url='/api/v1/correction-models/reference-compare'
        good=self.fx.post(self.fx.client,url,data);self.assertEqual(good.status_code,200,good.json)
        self.assertIsNone(good.json['data']['correctness'])
        mismatch=self.fx.post(self.fx.client,url,{**data,'reference_action_id':'machine_chest_press'})
        self.assertEqual(mismatch.json['data']['reason'],'reference_action_mismatch')
        bad=self.fx.post(self.fx.client,url,{**data,'observed_angles':[[1.]*22]})
        self.assertEqual(bad.status_code,422)
        too_many=self.fx.post(self.fx.client,url,{**data,'observed_angles':[[1.]*23]*301})
        self.assertEqual(too_many.status_code,422)

    def test_pose_classifier_does_not_accept_other_action_or_non_finite(self):
        url='/api/v1/correction-models/pose-check'
        points=[{'x':.2,'y':.3,'z':.1,'visibility':1.} for _ in range(33)]
        good=self.fx.post(self.fx.client,url,{'action_id':'bodyweight_squat','landmarks':points})
        self.assertEqual(good.status_code,200,good.json)
        unsupported=self.fx.post(self.fx.client,url,{'action_id':'machine_chest_press','landmarks':points})
        self.assertEqual(unsupported.status_code,422)
        points[0]['x']=float('inf')
        self.assertEqual(self.fx.post(self.fx.client,url,{'action_id':'bodyweight_squat','landmarks':points}).status_code,422)
        self.assertEqual(self.fx.post(self.fx.client,url,{'action_id':[], 'landmarks':points}).status_code,422)

    def test_invalid_reference_action_type_is_rejected(self):
        response=self.fx.post(self.fx.client,'/api/v1/correction-models/reference-compare',{'action_id':[],
            'reference_action_id':'bodyweight_squat','reference_angles':[[1.]*23],'observed_angles':[[1.]*23]})
        self.assertEqual(response.status_code,422,response.json)

    def test_disabled_application_has_no_model_routes(self):
        from fitness_app import create_app
        disabled=create_app({'TESTING':True,'DATABASE_PATH':str(self.fx.db.path),'SECRET_KEY':'test-only','PRETRAINED_CORRECTION_ENABLED':False})
        try:self.assertFalse(any('/correction-models' in str(rule) for rule in disabled.url_map.iter_rules()))
        finally:disabled.extensions['database'].dispose()


if __name__=='__main__':unittest.main()
