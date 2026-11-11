"""排他动作标签与诊断隔离；不按候选输出改原标签或强行改非目标动作。"""
import unittest
from fitness_app.action_label_policy import scope,route


class LabelPolicyTests(unittest.TestCase):
    def test_generic_curl_is_family_evidence_and_does_not_prove_supination(self):
        value=scope({'action_id':'standing_dumbbell_curl','partition':'train'});self.assertIsNone(value['leaf_label']);self.assertEqual(set(value['allowed_actions']),{'standing_dumbbell_curl','hammer_curl'})
        specific=scope({'action_id':'standing_dumbbell_curl','partition':'train','fine_action_verified':True});self.assertEqual(specific['leaf_label'],'standing_dumbbell_curl')
    def test_evaluation_only_asset_cannot_enter_training(self):
        with self.assertRaises(ValueError):scope({'action_id':'hammer_curl','partition':'train','evaluation_only':True})
        self.assertEqual(scope({'action_id':'hammer_curl','partition':'diagnostic','evaluation_only':True})['leaf_label'],'hammer_curl')
    def test_non_curl_rejection_and_insufficient_subtype_are_not_forced(self):
        for action in ('cable_face_pull',None):
            primary={'action_id':action,'score':.9};self.assertEqual(route(primary,'hammer_curl',.99,.98),primary)
        primary={'action_id':'standing_dumbbell_curl','score':.9}
        self.assertEqual(route(primary,'hammer_curl',.6,.1),primary);self.assertEqual(route(primary,'hammer_curl',.95,.9)['action_id'],'hammer_curl');self.assertEqual(primary['action_id'],'standing_dumbbell_curl')


if __name__=='__main__':unittest.main()
