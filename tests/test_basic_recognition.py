"""用户授权的基础发布策略，不把基础启用写成完整验收通过。"""
import unittest
import copy
from unittest.mock import patch
import numpy as np
from fitness_app.basic_recognition import BasicRecognizer
from fitness_app.automatic_recognition import ACTIONS
from test_automatic_analysis import frame
import test_formal_records as records


class Prediction:
    classes_=np.asarray(ACTIONS)
    def __init__(self,action):self.action=action
    def predict_proba(self,x):
        return np.asarray([[.96 if a==self.action else .04/(len(ACTIONS)-1) for a in ACTIONS] for _ in x])


def recognizer(action='conventional_deadlift',pose='conventional_deadlift'):
    value=BasicRecognizer();value.available=True;value.reason=None
    value.gym=value.gym_head=None  # 旧九类融合契约；新增视觉路径有自己的特征与真实输入测试。
    value.model=Prediction(action);value.pose_model=Prediction(pose)
    return value


def run_frames(value,start,end):
    for i in range(start,end):result=value.process(frame(i),np.ones(512) if i%5==0 else None)
    return result


class BasicRecognitionTests(unittest.TestCase):
    def test_default_enabled_status_does_not_load_image_engine_or_claim_full_validation(self):
        with patch('joblib.load') as load:
            value=BasicRecognizer().snapshot()
            self.assertTrue(value['available']);self.assertEqual(value['release_mode'],'basic_reference')
            self.assertEqual(len(value['supported_actions']),33)
            self.assertFalse(set(value['trained_actions'])&set(value['untrained_actions']))
            self.assertEqual(set(value['trained_actions'])|set(value['untrained_actions']),set(value['target_actions']))
            self.assertEqual(len(value['rule_actions']),12)
            self.assertFalse(value['full_validation_passed']);load.assert_not_called()

    def test_confirm_conflict_and_pose_fallback_without_images(self):
        value=recognizer();result=run_frames(value,0,31)
        self.assertEqual(result['action_id'],'conventional_deadlift')
        value.model.action='barbell_overhead_press'
        result=run_frames(value,31,36)
        self.assertIsNone(result['action_id']);self.assertIn(result['reason'],{'models_disagree','visual_motion_conflict'})
        value.model.action=value.pose_model.action='conventional_deadlift'
        self.assertEqual(run_frames(value,36,51)['action_id'],'conventional_deadlift')
        from test_action_counting import sequence
        full=sequence('conventional_deadlift')
        for i in range(51,100):
            f=copy.deepcopy(full[(i-51)%len(full)]);f.update(frame_seq=i,captured_ms=i*100)
            result=value.process(f)
        self.assertEqual(result['action_id'],'conventional_deadlift');self.assertEqual(result['decision_basis'],'temporal_pose_rules')
        self.assertIsNone(value.process(frame(20),np.ones(512))['action_id'])

    def test_stationary_and_multiple_people_do_not_receive_action_labels(self):
        value=recognizer()
        for i in range(31):
            f=frame(0);f.update(frame_seq=i,captured_ms=i*100)
            result=value.process(f,np.ones(512) if i%5==0 else None)
        self.assertIsNone(result['action_id']);self.assertEqual(result['reason'],'stationary')
        result=run_frames(value,31,65)
        self.assertEqual(result['action_id'],'conventional_deadlift')
        f=frame(65);f['framing']['person_count']=2
        result=value.process(f,np.ones(512))
        self.assertIsNone(result['action_id']);self.assertEqual(len(value.images),0)

    def test_missing_assets_keep_manual_fallback_and_http_reports_basic_scope(self):
        with patch('fitness_app.basic_recognition.POLICY') as path:
            path.read_text.side_effect=FileNotFoundError()
            value=BasicRecognizer().snapshot()
            self.assertFalse(value['available']);self.assertTrue(value['manual_fallback_available'])
        fx=records.RecordTests();fx.setUp()
        try:
            reply=fx.client.get('/api/v1/recognition')
            self.assertEqual(reply.status_code,200)
            data=reply.json['data'];self.assertTrue(data['automatic_analysis_available'])
            self.assertTrue(data['full_twelve_action_support']);self.assertFalse(data['full_twelve_action_validation_passed'])
            self.assertTrue(data['automatic_training_records_available'])
        finally:
            for key in ('video_jobs','session_manager','preview_manager','media_inputs'):fx.app.extensions[key].stop()
            fx.tearDown()


if __name__=='__main__':unittest.main()
