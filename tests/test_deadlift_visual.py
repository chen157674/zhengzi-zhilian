"""新视觉候选的裁片几何、人物划分、拒判分母与缓存身份；不重跑原分类实验。"""
import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from fitness_app import deadlift_visual as v
from fitness_app.paths import ROOT


def points():
    p=np.zeros((33,5));p[:,3:]=1
    p[11,:2]=(.4375,.25);p[12,:2]=(.5625,.25)
    p[23,:2]=(.4375,.75);p[24,:2]=(.5625,.75)
    return p


def sample(key,group,label='GB',partition='development'):
    return SimpleNamespace(key=key,subject_group=group,label=label,partition=partition,action_id='conventional_deadlift',views=('side',))


class VisualCandidateTests(unittest.TestCase):
    def test_canonical_axis_maps_shoulder_and_hip_and_preserves_scaled_view(self):
        p=points();matrix=v.torso_geometry(p,320,320)
        np.testing.assert_allclose(matrix@np.array([160,80,1]),[112,56],atol=1e-5)
        np.testing.assert_allclose(matrix@np.array([160,240,1]),[112,168],atol=1e-5)
        larger=v.torso_geometry(p,640,640)
        np.testing.assert_allclose(larger@np.array([320,160,1]),[112,56],atol=1e-5)
        np.testing.assert_allclose(larger@np.array([320,480,1]),[112,168],atol=1e-5)

    def test_missing_identity_parts_small_torso_padding_or_nonfinite_refuse(self):
        for count,identity in ((2,True),(1,False)):
            self.assertIsNone(v.torso_geometry(points(),320,320,count,identity))
        for field,value in ((3,.49),(4,.49),(0,float('nan')),(0,-.01)):
            p=points();p[11,field]=value;self.assertIsNone(v.torso_geometry(p,320,320))
        p=points();p[[23,24],1]=.26;self.assertIsNone(v.torso_geometry(p,320,320))
        p=points();p[[11,12,23,24],0]-=.4;self.assertIsNone(v.torso_geometry(p,320,320))

    def test_independent_input_is_rejected_before_any_extraction(self):
        with self.assertRaises(ValueError):v.extract_sample(sample('test','S04',partition='test'),Path('unused'),None)
        with self.assertRaises(ValueError):v.require_development([sample('x','S01',partition='excluded')])

    def test_fold_scaler_uses_only_training_subjects_and_rejects_overlap(self):
        train=[sample('a','S01','GB'),sample('b','S02','PB')];validation=[sample('c','S03','PB')]
        features={'a':{'features':np.array([1.,2.]),'reason':None},'b':{'features':np.array([3.,6.]),'reason':None},
                  'c':{'features':np.array([1000.,2000.]),'reason':None}}
        model,record=v.fit_fold(train,validation,features)
        np.testing.assert_allclose(model.named_steps['standardscaler'].mean_,[2,4])
        self.assertEqual(record['actual_fit_keys'],['a','b']);self.assertEqual(record['evaluation_keys'],['c'])
        with self.assertRaises(ValueError):v.fit_fold(train,[sample('d','S01')],features)

    def test_feature_rejection_and_fit_failure_remain_in_prediction_rows(self):
        samples=[sample('a','S01','GB'),sample('b','S02','PB')]
        features={'a':{'features':None,'reason':'INSUFFICIENT_TORSO_COVERAGE'},'b':{'features':np.array([1.,2.]),'reason':None}}
        rows=v.predictions(None,samples,features,'FIT_CLASS_UNAVAILABLE')
        self.assertEqual(len(rows),2);self.assertEqual([r['prediction'] for r in rows],[None,None])
        self.assertEqual(v.evaluate(rows)['main']['samples'],2);self.assertEqual(v.evaluate(rows)['main']['coverage'],0)

    def test_cache_reuses_exact_feature_signature_and_preserves_rejection(self):
        parent=ROOT/'.tmp/tests/visual-candidate';parent.mkdir(parents=True,exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=parent) as temporary:
                path=Path(temporary)/'features.npz'
                rows={'a':{'sample_key':'a','fingerprint':'hash-a','reason':None,'features':np.ones(2560,np.float32),'summary':{}},
                      'b':{'sample_key':'b','fingerprint':'hash-b','reason':'MISSING','features':None,'summary':{}}}
                with patch.object(v,'CACHE',path):
                    v.save_cache('exact',rows);loaded=v.read_cache('exact')
                    self.assertEqual(loaded['a']['fingerprint'],'hash-a');np.testing.assert_array_equal(loaded['a']['features'],rows['a']['features'])
                    self.assertIsNone(loaded['b']['features']);self.assertEqual(v.read_cache('changed'),{})
                    self.assertFalse(path.with_suffix('.npz.tmp').exists())
        finally:
            if parent.exists() and not any(parent.iterdir()):parent.rmdir()


if __name__=='__main__':unittest.main()
