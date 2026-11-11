"""易混淆类别复核的控制边界；不将模拟分数当真实动作识别成绩。"""
import unittest
from fitness_app.video_refinement import choose_family,combine


class VideoRefinementTests(unittest.TestCase):
    def primary(self,action,score=.9):return {'action_id':action,'candidate_action_id':action,'top_action_id':action,'score':score,'margin':.7,'model_version':'test'}
    def review(self,action):return {'action_id':action,'top_action_id':action,'score':.95,'margin':.9}

    def test_grip_subtypes_review_even_when_coarse_curl_is_confident(self):
        self.assertEqual(choose_family(self.primary('standing_dumbbell_curl'),{'top_action_id':'standing_dumbbell_curl','score':.9}),'curl')
        self.assertIsNone(choose_family(self.primary('bodyweight_squat'),{'top_action_id':'bodyweight_squat','score':.9}))

    def test_equipment_review_cannot_override_strong_background(self):
        primary=self.primary(None)
        result=combine(primary,self.review('machine_chest_press'),'chest')
        self.assertIsNone(result['action_id']);self.assertEqual(result['model_version'],'test')

    def test_review_cannot_publish_a_class_outside_requested_family(self):
        primary=self.primary('standing_dumbbell_curl')
        result=combine(primary,self.review('machine_chest_press'),'curl')
        self.assertEqual(result['action_id'],primary['action_id'])

    def test_grip_review_updates_class_without_changing_pipeline_version(self):
        result=combine(self.primary('standing_dumbbell_curl'),self.review('hammer_curl'),'curl')
        self.assertEqual(result['action_id'],'hammer_curl');self.assertEqual(result['top_action_id'],'hammer_curl');self.assertEqual(result['model_version'],'test')

    def test_weak_review_cannot_replace_trained_classifier(self):
        review=self.review('hammer_curl');review.update(score=.6,margin=.1)
        result=combine(self.primary('standing_dumbbell_curl'),review,'curl')
        self.assertEqual(result['action_id'],'standing_dumbbell_curl')


if __name__=='__main__':unittest.main()
