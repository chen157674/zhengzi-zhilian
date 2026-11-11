"""两到十fps的持续偏差观察，不改变动作分类器的八帧要求。"""
import copy,unittest
from fitness_app.gym_quality import evaluate
from fitness_app.gym_motion import measurements
from fitness_app.posture_engine import create_analysis_engine
from test_gym_actions import raise_sequence
from test_curl_correction import sequence

class GymCadenceTests(unittest.TestCase):
    def test_arm_height_detects_at_two_to_ten_fps_and_missing_current_withdraws(self):
        for step in (1,2,3,4,5):
            for bad in (False,True):
                frames=raise_sequence(bad)[::step]
                result=evaluate('dumbbell_lateral_raise.arm_asymmetry',frames[-max(5,25//step):],'front',.25)
                self.assertEqual(result['status'],'issue' if bad else 'not_observed',(step,result))
                self.assertLessEqual(result['maximum_observation_gap_ms'],500)
                frames[-1]['framing']['landmarks'][15]['valid']=False
                self.assertEqual(evaluate('dumbbell_lateral_raise.arm_asymmetry',frames,'front',.25)['status'],'unassessable')

    def test_sparse_upper_arm_swing_and_classifier_minimum_remain_separate(self):
        frames=sequence(arm_swing=True)[::5]
        current=frames[-5:]
        self.assertIsNone(measurements(current))
        result=evaluate('curl.upper_arm_swing',current,'side',25.,require_continuous=True)
        self.assertEqual(result['status'],'issue');self.assertEqual(result['observed_frames'],5)
        broken=copy.deepcopy(current);broken[-1]['captured_ms']+=600
        self.assertEqual(evaluate('curl.upper_arm_swing',broken,'side',25.,require_continuous=True)['status'],'unassessable')

    def test_deliberate_alternating_variant_rejects_bilateral_height(self):
        engine=create_analysis_engine('dumbbell_lateral_raise')
        for f in raise_sequence(True):result=engine.process(f,{'coaching_view':'front','movement_pattern':'alternating'})
        item=next(i for i in result['observation']['posture']['items'] if i['code'].endswith('arm_asymmetry'))
        self.assertEqual(item['status'],'unassessable');self.assertEqual(item['reason'],'simultaneous_variant_required')

if __name__=='__main__':unittest.main()
