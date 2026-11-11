"""已确认画面变式不被原错误标题覆盖；不联网或伪造训练真值。"""
import unittest
from scripts.prepare_curl_sources import verified_variant,duplicate_reference

class CurlSourceTests(unittest.TestCase):
    def test_exact_review_overrides_publisher_mislabel_and_preserves_origin(self):
        row={'id':'dvids-679657','sha256':'9952c49a6b042ec78557e4a9c63f607e19f7e606f823b2e3862d00251120b3c9','action_id':'standing_dumbbell_curl','title':'Dumbbell Bicep Curl'}
        fixed=verified_variant(row);self.assertEqual(fixed['action_id'],'unknown');self.assertEqual(fixed['publisher_action_id'],row['action_id']);self.assertEqual(fixed['title'],row['title']);self.assertEqual(row['action_id'],'standing_dumbbell_curl')
        self.assertEqual(verified_variant(fixed),fixed)
    def test_changed_original_cannot_inherit_old_visual_review(self):
        with self.assertRaises(ValueError):verified_variant({'id':'dvids-679657','sha256':'changed','action_id':'standing_dumbbell_curl'})
    def test_unreviewed_new_source_does_not_receive_fabricated_variant(self):
        row={'id':'dvids-636923','sha256':'new','action_id':'standing_dumbbell_curl'};self.assertEqual(verified_variant(row),row)
    def test_same_media_reuses_one_asset_and_is_not_an_extra_training_record(self):
        from fitness_app.action_label_policy import scope
        old={'id':'canonical','sha256':'same','path':'data/original.mp4'}
        source={'id':'new','sha256':'same','path':'data/duplicate.mp4','action_id':'standing_dumbbell_curl','partition':'train'}
        fixed=duplicate_reference(source,[old]);self.assertEqual(fixed['path'],old['path']);self.assertEqual(fixed['duplicate_of'],old['id']);self.assertTrue(fixed['exclude_from_training'])
        with self.assertRaises(ValueError):scope(fixed)

if __name__=='__main__':unittest.main()
