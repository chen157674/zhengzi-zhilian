"""十二类按连续关节运动区分；工程几何不替代真实视频准确率。"""
import copy
import unittest
from fitness_app.motion_recognition import motion_candidate
from fitness_app.basic_recognition import BasicRecognizer
from fitness_app.automatic_recognition import TARGET_ACTIONS
from fitness_app.automatic_analysis import AutomaticAnalysis
from test_action_counting import sequence
import test_glute_bridge as bridge
import test_sit_up as situp
import test_high_knees as knees
import test_plank_hold as plank
import test_overhead_press as press
import test_barbell_row as row
import test_reverse_lunge as lunge
import test_jumping_jack as jack


def sequences():
    result={a:sequence(a) for a in ('bodyweight_squat','standard_push_up','standing_dumbbell_curl','conventional_deadlift')}
    result.update(bodyweight_glute_bridge=bridge.sequence(),bent_knee_sit_up=situp.sequence(),
        high_knees=knees.sequence()+knees.sequence('right',24),forearm_plank=[plank.geometry(i) for i in range(25)],
        barbell_overhead_press=press.sequence(),barbell_bent_over_row=row.sequence(),
        reverse_lunge=lunge.sequence(),jumping_jack=jack.sequence(step_ms=100))
    return result


class TwelveMotionTests(unittest.TestCase):
    def test_forward_step_is_not_labeled_as_reverse_lunge(self):
        self.assertNotEqual(motion_candidate(lunge.sequence(forward=True))['action_id'],'reverse_lunge')
        self.assertEqual(motion_candidate(lunge.sequence())['action_id'],'reverse_lunge')

    def test_twelve_unique_patterns_and_mirrored_translated_scaled_frames(self):
        values=sequences();self.assertEqual(set(values),set(TARGET_ACTIONS))
        for expected,frames in values.items():
            with self.subTest(action=expected):
                self.assertEqual(motion_candidate(frames)['action_id'],expected)
                transformed=copy.deepcopy(frames)
                for f in transformed:
                    f['width']=f['height']=800
                    for p in f['framing']['landmarks']:
                        p['x']=.05+.9*(1-p['x']);p['y']=.03+.9*p['y']
                self.assertEqual(motion_candidate(transformed)['action_id'],expected)

    def test_all_twelve_reach_real_analysis_without_a_selected_action_or_fake_prediction(self):
        for expected,frames in sequences().items():
            with self.subTest(action=expected):
                recognizer=BasicRecognizer();router=AutomaticAnalysis();observed=set();routed=set()
                # 重复同一完整轨迹以覆盖稳定确认；分类器从未接收expected或动作ID。
                stream=[];offset=0
                for _ in range(3):
                    for f in frames:
                        item=copy.deepcopy(f);item.update(frame_seq=len(stream),captured_ms=offset+f['captured_ms'])
                        item['framing'].update(usable=True,code='VISIBLE');stream.append(item)
                    offset=stream[-1]['captured_ms']+100
                for f in stream:
                    recognition=recognizer.process(f)
                    if recognition['action_id']:observed.add(recognition['action_id'])
                    result=router.process(f,recognition)
                    if result['automatic_analysis']['state']=='analyzing':routed.add(result['automatic_analysis']['action_id'])
                self.assertEqual(observed,{expected});self.assertEqual(routed,{expected})

    def test_rest_missing_joints_multipeople_and_gap_are_not_forced_into_twelve_classes(self):
        frames=[copy.deepcopy(sequences()['bodyweight_squat'][0]) for _ in range(25)]
        for i,f in enumerate(frames):f.update(frame_seq=i,captured_ms=i*100)
        self.assertIsNone(motion_candidate(frames)['action_id'])
        for modify in ('missing','people','gap'):
            values=copy.deepcopy(bridge.sequence())
            for f in values:
                if modify=='missing':
                    for p in f['framing']['landmarks']:p['valid']=False
                elif modify=='people':f['framing']['person_count']=2
                else:f['captured_ms']*=10
            self.assertIsNone(motion_candidate(values)['action_id'])


if __name__=='__main__':unittest.main()
