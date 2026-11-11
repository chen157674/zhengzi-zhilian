"""单次过顶候选的入口与强确认；不将模拟分数当实际动作效果。"""
import unittest
from fitness_app.overhead_action_review import eligible,accepted_pulldown


class OverheadFastTests(unittest.TestCase):
    def test_only_strong_pulldown_can_shortcut(self):
        valid={'action_id':'lat_pulldown','score':.98,'margin':.9}
        self.assertTrue(accepted_pulldown(valid))
        for changes in ({'action_id':None},{'action_id':'dumbbell_shoulder_press'},
            {'score':.89},{'margin':.49},{'score':True},{'score':float('nan')},{'margin':float('inf')}):
            with self.subTest(changes=changes):self.assertFalse(accepted_pulldown({**valid,**changes}))

    def test_geometry_only_routes_and_cannot_force_action(self):
        self.assertTrue(eligible({'wrist_above_shoulder_max_torso':.8,'upper_arm_range_deg':70.,'elbow_range_deg':60.,'elbow_median_deg':100.}))
        self.assertFalse(eligible({'wrist_above_shoulder_max_torso':.1,'upper_arm_range_deg':70.}))
        self.assertFalse(eligible({}))
        self.assertFalse(eligible({'wrist_above_shoulder_max_torso':.8,'upper_arm_range_deg':100.,'elbow_range_deg':14.,'elbow_median_deg':168.}))


if __name__=='__main__':unittest.main()
