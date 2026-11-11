"""跨组入口边界，几何证据只能触发复核，不能直接分类。"""
import unittest
from unittest.mock import patch,Mock
from fitness_app.pull_press_review import route,apply_review


class PullPressRoutingTests(unittest.TestCase):
    def test_overhead_evidence_routes_only_accepted_chest_candidates(self):
        facts={'wrist_above_shoulder_max_torso':.8,'upper_arm_to_trunk_median_deg':120.,'upper_arm_range_deg':80.}
        for action,expected in [('machine_chest_press',True),('reverse_pec_deck_fly',True),
            ('lat_pulldown',False),('dumbbell_shoulder_press',False),(None,False)]:
            self.assertEqual(route({'action_id':action},facts),expected)
        self.assertFalse(route({'action_id':'machine_chest_press'},{**facts,'wrist_above_shoulder_max_torso':.1}))
        self.assertTrue(route({'action_id':'reverse_pec_deck_fly'},{**facts,'upper_arm_to_trunk_median_deg':80.}))
        self.assertFalse(route({'action_id':'reverse_pec_deck_fly'},{**facts,'upper_arm_to_trunk_median_deg':80.,'upper_arm_range_deg':10.}))

    def test_missing_nonfinite_and_boolean_geometry_cannot_trigger_review(self):
        for facts in ({},{'wrist_above_shoulder_max_torso':float('nan'),'upper_arm_to_trunk_median_deg':120.},
            {'wrist_above_shoulder_max_torso':True,'upper_arm_to_trunk_median_deg':120.},
            {'wrist_above_shoulder_max_torso':.8,'upper_arm_to_trunk_median_deg':'120'}):
            self.assertFalse(route({'action_id':'machine_chest_press'},facts))


    def test_geometry_only_routes_but_low_confidence_and_unaccepted_review_never_changes_action(self):
        base={'action_id':'machine_chest_press','score':.99}
        facts={'wrist_above_shoulder_max_torso':.8,'upper_arm_to_trunk_median_deg':120.}
        review={'action_id':'lat_pulldown','score':.99,'margin':.9,'accepted':True}
        result=apply_review(base,review,facts)
        self.assertEqual(result['action_id'],'lat_pulldown')
        self.assertEqual(result['cross_review']['baseline_action_id'],'machine_chest_press')
        self.assertEqual(base['action_id'],'machine_chest_press')
        for changes in ({'score':.5},{'margin':.1},{'accepted':False},{'action_id':'not-a-class'},{'score':float('nan')}):
            self.assertEqual(apply_review(base,{**review,**changes},facts),base)
        self.assertEqual(apply_review(base,review,{}),base)
        unknown=apply_review(base,{**review,'action_id':None},facts)
        self.assertIsNone(unknown['action_id']);self.assertEqual(unknown['reason'],'cross_group_video_uncertain')


    def test_production_predictor_invokes_review_only_when_policy_and_geometry_match(self):
        from fitness_app.semantic_model import SemanticPredictor
        info={'version':'fixture','block':'full','minimum_score':.7,'minimum_margin':.2,'pull_press_review':{'version':'fixture'}}
        with patch('fitness_app.semantic_model.resources',return_value=(info,None)):
            model=SemanticPredictor()
        base={'action_id':'machine_chest_press','score':.99}
        facts={'wrist_above_shoulder_max_torso':.8,'upper_arm_to_trunk_median_deg':120.}
        model.pull_press_reviewer=Mock()
        model.pull_press_reviewer.review.return_value={'action_id':'lat_pulldown','accepted':True,'score':.98,'margin':.9}
        changed=model._cross_review(base,[],[],facts)
        self.assertEqual(changed['action_id'],'lat_pulldown')
        model.pull_press_reviewer.review.assert_called_once()
        model.pull_press_reviewer.review.reset_mock()
        self.assertEqual(model._cross_review(base,[],[],{}),base)
        model.pull_press_reviewer.review.assert_not_called()
        model.info.pop('pull_press_review')
        self.assertEqual(model._cross_review(base,[],[],facts),base)
        model.pull_press_reviewer.review.assert_not_called()


if __name__=='__main__':unittest.main()
