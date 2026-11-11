"""第三方粗标签轨迹评估的来源、映射和当前结果必须可复现。"""
import unittest

from scripts.evaluate_pose_trainer_quality import (
    EXPECTED_SOURCE,
    SOURCE_ROOT,
    evaluate_all,
    frames_from_sequence,
    source_bundle,
    verify_source,
)


class PoseTrainerQualityTests(unittest.TestCase):
    def test_fixed_source_and_openpose_presence_mapping(self):
        self.assertEqual(verify_source(), EXPECTED_SOURCE)
        self.assertEqual(source_bundle()['dataset_files'], 112)
        frames = frames_from_sequence(
            SOURCE_ROOT / 'poses_compressed/shoulderpress/shoulderpressgood1.npy')
        self.assertEqual(len(frames), 119)
        shoulder = frames[0]['framing']['landmarks'][12]
        self.assertTrue(shoulder['valid'])
        self.assertGreater(shoulder['source_confidence'], 0)
        self.assertEqual(shoulder['visibility'], 1.0)

    def test_current_coarse_label_results_are_not_specific_error_acceptance(self):
        report = evaluate_all()
        expected = {
            'front_raise': (28, 2, 0, 15, 11, 0),
            'shoulder_press': (36, 0, 0, 17, 14, 5),
            'bicep_curl': (16, 5, 0, 6, 5, 0),
        }
        for name, values in expected.items():
            with self.subTest(name=name):
                metrics = report['cases'][name]['metrics']
                actual = tuple(metrics[key] for key in ('sequences', 'tp', 'fp', 'tn', 'fn', 'unassessable'))
                self.assertEqual(actual, values)
                self.assertFalse(report['cases'][name]['specific_error_accuracy_eligible'])
        self.assertFalse(report['independent_specific_error_accuracy_passed'])
        self.assertFalse(report['release_candidate'])


if __name__ == '__main__':
    unittest.main()
