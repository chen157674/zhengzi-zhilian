"""视觉条件候选的动作／周期／像素边界，不以合成图计算健身准确率。"""
import unittest
from unittest.mock import patch
import numpy as np
from fitness_app.visual_posture import VisualBackConditionCandidate,validate_video


class VisualPostureTests(unittest.TestCase):
    def test_other_actions_partial_cycles_and_ambiguous_targets_never_load_weights(self):
        model=VisualBackConditionCandidate()
        with patch.object(model,'load',side_effect=AssertionError('不能加载候选权重')):
            for changes,reason in (
                ({'action_id':'lat_pulldown'},'unsupported_exercise'),
                ({'phase_complete':False},'awaiting_complete_cycle'),
                ({'single_person':False},'target_not_unique'),
                ({'torso_visible':False},'torso_not_visible')):
                args={'action_id':'conventional_deadlift','phase_complete':True,**changes}
                with self.subTest(reason=reason):
                    result=model.observe([],[],**args)
                    self.assertEqual(result['reason'],reason);self.assertFalse(result['production_enabled'])

    def test_old_timestamps_missing_frames_and_geometry_changes_cannot_form_a_video(self):
        frames=[np.zeros((64,96,3),np.uint8) for _ in range(8)];times=[i*300 for i in range(8)]
        validate_video(frames,times)
        with self.assertRaises(ValueError):validate_video(frames,times[:-1])
        with self.assertRaises(ValueError):validate_video(frames,[0]*8)
        with self.assertRaises(ValueError):validate_video(frames,[i*3000 for i in range(8)])
        with self.assertRaises(ValueError):validate_video(frames[:-1]+[np.zeros((96,64,3),np.uint8)],times)
        with self.assertRaises(ValueError):validate_video([image.astype(np.float32) for image in frames],times)
        model=VisualBackConditionCandidate()
        with patch.object(model,'load',side_effect=AssertionError('不能加载候选权重')):
            result=model.observe(frames,times[:-1],action_id='conventional_deadlift',phase_complete=True)
            self.assertEqual(result['reason'],'invalid_video_window')

    def test_low_confidence_condition_is_not_published_as_correct_movement(self):
        model=VisualBackConditionCandidate();frames=[np.zeros((64,96,3),np.uint8) for _ in range(8)]
        with patch.object(model,'prepare',return_value={}),patch.object(model,'predict_prepared',
            return_value={'top':'GB','accepted':None,'score':.7,'production_enabled':False}):
            result=model.observe(frames,[i*300 for i in range(8)],action_id='conventional_deadlift',phase_complete=True)
        self.assertEqual(result['status'],'unassessable')
        self.assertEqual(result['reason'],'visual_condition_uncertain')
        self.assertEqual(result['temporal_location'],'whole_completed_rep_not_frame_label')
        self.assertNotIn('overall_correctness',result)


if __name__=='__main__':unittest.main()
