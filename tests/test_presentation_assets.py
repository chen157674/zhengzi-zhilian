import copy
import unittest
from unittest.mock import patch

from fitness_app.catalogue import definitions
from fitness_app.presentation_assets import cover_catalogue,_read,MANIFEST


class CoverAssetsTests(unittest.TestCase):
    def test_twelve_assets_are_present_without_changing_capability_snapshots(self):
        before=copy.deepcopy(definitions());covers=cover_catalogue()
        self.assertEqual(set(covers),{a['action_id'] for a in before})
        self.assertTrue(all(c['width']==1536 and c['height']==1024 and c['url'].startswith('/static/covers/') for c in covers.values()))
        self.assertEqual(definitions(),before)

    def test_missing_tampered_or_unrelated_asset_falls_back_independently(self):
        stat=MANIFEST.stat();data=copy.deepcopy(_read(stat.st_mtime_ns,stat.st_size))
        data['covers']['bodyweight_squat']['sha256']='0'*64
        data['covers']['standard_push_up']['path']='data/models/manifest.json'
        data['covers']['not_real']={**data['covers']['forearm_plank'],'path':'fitness_app/static/covers/not_real.png'}
        with patch('fitness_app.presentation_assets._read',return_value=data):result=cover_catalogue()
        self.assertNotIn('bodyweight_squat',result);self.assertNotIn('standard_push_up',result);self.assertNotIn('not_real',result)
        self.assertEqual(len(result),10)


if __name__=='__main__':unittest.main()
