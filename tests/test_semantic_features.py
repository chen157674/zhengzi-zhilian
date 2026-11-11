"""验证语义/骨架融合的缺失与数值契约，不以合成输入充当动作准确率证据。"""
import unittest
from unittest.mock import patch
import numpy as np
from fitness_app.semantic_features import feature_blocks
from fitness_app.video_vlm import LABELS
from fitness_app.world_features import WorldFeatures
from fitness_app.recognition_observations import observations


class SemanticFeaturesTests(unittest.TestCase):
    def prediction(self):return {'scores':{a or 'unknown':.01 for a in LABELS},'pose_observations':{}}
    def test_missing_geometry_is_explicit_and_semantic_is_not_fabricated(self):
        blocks=feature_blocks(np.ones(2560,np.float32),self.prediction())
        self.assertEqual(blocks['full'].shape,(2736,));self.assertEqual(blocks['geometry'][-2],0.)
        self.assertTrue(np.isfinite(blocks['full']).all());self.assertAlmostEqual(float(np.linalg.norm(blocks['semantic'])),1.,places=5)
        with self.assertRaises(ValueError):feature_blocks(np.zeros(2560),self.prediction())
        with self.assertRaises(ValueError):feature_blocks(np.ones(2048),self.prediction())

    def test_bad_scores_are_rejected_instead_of_normalized(self):
        p=self.prediction();p['scores']['unknown']=float('nan')
        with self.assertRaises(ValueError):feature_blocks(np.ones(2560),p)

    def test_multiple_persons_do_not_load_or_fill_world_features(self):
        encoder=WorldFeatures();arrays={'timestamps':np.arange(32)*100,'people':np.full(32,2), 'world':np.ones((32,33,5),np.float32)}
        vector,_=encoder.extract(arrays,0,3100);self.assertIsNone(vector);self.assertIsNone(encoder.session)

    def test_missing_hips_do_not_erase_visible_elbow_motion(self):
        raw=np.full((32,33,5),np.nan,np.float32)
        for j in range(32):
            for i,x,y in ((11,.4,.3),(12,.6,.3),(13,.35,.45),(14,.65,.45),(15,.3+j*.004,.58),(16,.7-j*.004,.58)):
                raw[j,i]=[x,y,0,1,1]
        values,box=observations({'timestamps':np.arange(32)*100,'people':np.ones(32,int),'points':raw,'sizes':np.tile((640,480),(32,1))},0,3100)
        self.assertIn('elbow_range_deg',values);self.assertNotIn('trunk_tilt_from_vertical_deg',values)
        self.assertIsNotNone(box)


if __name__=='__main__':unittest.main()
