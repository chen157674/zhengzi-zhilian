"""当前遮挡必须撤回纠错；合成可见性边界不代表独立纠错准确率。"""
import copy
import unittest

from fitness_app.coaching import coaching_snapshot
from fitness_app.gym_quality import evaluate
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.skeleton import skeleton_snapshot
from test_gym_actions import raise_sequence


class PostureOcclusionTests(unittest.TestCase):
    def test_current_missing_wrist_is_neither_wrong_nor_normal(self):
        for asymmetric in (False, True):
            with self.subTest(asymmetric=asymmetric):
                frames = raise_sequence(asymmetric)
                code = 'dumbbell_lateral_raise.arm_asymmetry'
                before = evaluate(code, frames, 'front', .25)
                self.assertEqual(before['status'], 'issue' if asymmetric else 'not_observed')
                frames[-1]['framing']['landmarks'][15]['valid'] = False
                self.assertEqual(evaluate(code, frames, 'front', .25)['status'], 'unassessable')

    def test_recovery_cannot_reuse_sustained_deviation_before_occlusion(self):
        frames = raise_sequence(True)
        frames[25]['framing']['landmarks'][15]['valid'] = False
        code = 'dumbbell_lateral_raise.arm_asymmetry'
        # 恢复后不足1200ms，不得借用遮挡前那段已确认的偏差。
        self.assertEqual(evaluate(code, frames[:36], 'front', .25)['status'], 'unassessable')
        self.assertEqual(evaluate(code, frames, 'front', .25)['status'], 'issue')

    def test_current_trunk_side_must_be_visible_but_one_side_can_suffice(self):
        frames = raise_sequence()
        code = 'cable_triceps_pushdown.upper_arm_swing'
        self.assertEqual(evaluate(code, frames, 'side', 25.)['status'], 'issue')
        frames[-1]['framing']['landmarks'][23]['valid'] = False
        self.assertEqual(evaluate(code, frames, 'side', 25.)['status'], 'issue')
        frames[-1]['framing']['landmarks'][24]['valid'] = False
        self.assertEqual(evaluate(code, frames, 'side', 25.)['status'], 'unassessable')

    def test_current_occlusion_retracts_guidance_and_error_highlight(self):
        action = 'dumbbell_lateral_raise'
        engine = create_analysis_engine(action)
        frames = raise_sequence(True)
        context = {'coaching_view': 'front', 'stream_epoch': 'occlusion'}
        for frame in frames[:-1]:
            output = engine.process(frame, context)
        feedback = [x for x in output['posture_findings'] if x['status'] == 'issue']
        self.assertTrue(feedback)
        current = copy.deepcopy(frames[-1])
        current['framing']['landmarks'][15]['valid'] = False
        output = engine.process(current, context)
        current['analysis'] = output['observation']
        session = {'status': 'running', 'action_id': action, 'supported_items': engine.rules}
        cue = coaching_snapshot(session, current, feedback, feedback_age_ms=100)
        self.assertIsNone(cue['primary_cue'])
        self.assertEqual(cue['state'], 'evidence_insufficient')
        self.assertFalse(skeleton_snapshot(current, cue).get('highlight'))


if __name__ == '__main__':
    unittest.main()
