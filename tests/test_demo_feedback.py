"""演示必须区分有错、未发现偏差、证据不足，并且只高亮真实确认的问题。"""
import copy
import unittest

from fitness_app.demo_feedback import build_demo_feedback
from fitness_app.coaching import coaching_observation,coaching_snapshot,committed_issues
from fitness_app.posture_engine import create_analysis_engine
from test_gym_actions import raise_sequence


def result(asymmetric=False):
    action='dumbbell_lateral_raise';engine=create_analysis_engine(action)
    for frame in raise_sequence(asymmetric):
        output=engine.process(frame,{'coaching_view':'front','stream_epoch':'demo-feedback'})
    coaching=coaching_snapshot({'status':'running','action_id':action,'supported_items':engine.rules},
                               {**frame,'analysis':coaching_observation(output)},committed_issues(output))
    return frame,output,engine.rules,coaching


class DemoFeedbackTests(unittest.TestCase):
    def test_clear_is_scoped_to_available_checks_and_keeps_unsupported_item_visible(self):
        value=build_demo_feedback(*result())
        self.assertEqual(value['state'],'clear')
        self.assertIn('未发现错误',value['title'])
        self.assertEqual(value['assessed_count'],1)
        self.assertEqual(value['unknown_count'],1)
        unsupported=next(x for x in value['checks'] if x['code'].endswith('trunk_sway'))
        self.assertIn('侧面',unsupported['detail'])
        self.assertIsNone(value['overall_correctness'])
        self.assertIsNone(value['highlight'])

    def test_confirmed_issue_has_specific_problem_adjustment_and_highlight(self):
        value=build_demo_feedback(*result(True))
        self.assertEqual(value['state'],'issue')
        self.assertIn('手臂',value['title'])
        self.assertIn('放慢',value['instruction'])
        self.assertEqual(value['highlight']['joint_indices'],[13,14,15,16])
        self.assertIsNone(value['overall_correctness'])

    def test_missing_target_cannot_reuse_previous_normal_or_issue(self):
        for asymmetric in (False,True):
            frame,output,rules,coaching=result(asymmetric)
            for count in (0,2):
                with self.subTest(asymmetric=asymmetric,count=count):
                    changed=copy.deepcopy(frame);changed['framing'].update(person_count=count,usable=False)
                    value=build_demo_feedback(changed,output,rules,coaching)
                    self.assertEqual(value['state'],'unassessable')
                    self.assertEqual(value['assessed_count'],0)
                    self.assertIsNone(value['highlight'])

    def test_warmup_and_unconfirmed_issue_do_not_claim_correctness(self):
        engine=create_analysis_engine('dumbbell_lateral_raise');frame=raise_sequence()[0]
        output=engine.process(frame,{'coaching_view':'front','stream_epoch':'warmup'})
        value=build_demo_feedback(frame,output,engine.rules,{})
        self.assertIn(value['state'],{'observing','unassessable'})
        frame,output,rules,_=result(True)
        value=build_demo_feedback(frame,output,rules,{})
        self.assertEqual(value['state'],'observing')
        self.assertIsNone(value['highlight'])

    def test_missing_frame_assessments_are_neither_clear_nor_old_error(self):
        frame,_,rules,coaching=result(True)
        value=build_demo_feedback(frame,{'observation':{}},rules,coaching)
        self.assertNotEqual(value['state'],'clear')
        self.assertNotEqual(value['state'],'issue')
        self.assertIsNone(value['highlight'])


if __name__=='__main__':unittest.main()
