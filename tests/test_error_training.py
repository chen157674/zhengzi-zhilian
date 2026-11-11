"""训练划分／归一化／错误召回口径；不镜像模型内部拟合实现。"""
import unittest
import numpy as np
from fitness_app.error_training import features,fingerprints,blocked_split,metrics,trunk_features

class ErrorTrainingTests(unittest.TestCase):
    def test_validation_and_fit_are_disjoint_and_boundaries_are_purged(self):
        labels=np.repeat(['C','H','L'],1500);train,val=blocked_split(labels)
        self.assertFalse(set(train)&set(val));self.assertEqual(set(labels[train]),{'C','H','L'})
        for offset in range(-16,17):self.assertFalse(set(train)&set((val+offset).tolist()))

    def test_hip_centered_features_are_translation_and_uniform_scale_invariant(self):
        names=['left_shoulder','right_shoulder','left_hip','right_hip']
        columns=[name+'_'+key for name in names for key in ('x','y','z','v')]
        values=np.array([[.3,.3,-.1,.9,.6,.3,.1,.8,.35,.7,-.05,.9,.55,.7,.05,.8]])
        changed=values.copy()
        for i,c in enumerate(columns):
            if c.endswith('_x'):changed[:,i]=(changed[:,i]-.5)*1.2+.5+.05
            elif c.endswith('_y'):changed[:,i]=(changed[:,i]-.5)*1.2+.5-.03
            elif c.endswith('_z'):changed[:,i]*=1.2
        np.testing.assert_allclose(features(values,columns),features(changed,columns),atol=1e-12)

    def test_trunk_candidate_does_not_learn_visibility_or_absolute_position(self):
        columns=[name+'_'+key for name in ('left_shoulder','right_shoulder','left_hip','right_hip') for key in ('x','y','z','v')]
        values=np.array([[.3,.3,-.1,.9,.6,.3,.1,.8,.35,.7,-.05,.9,.55,.7,.05,.8]])
        changed=values.copy()
        for i,column in enumerate(columns):
            if column.endswith('_v'):changed[:,i]=.01
            elif column.endswith('_z'):changed[:,i]=50
            elif column.endswith('_x'):changed[:,i]+=.1
        np.testing.assert_allclose(trunk_features(values,columns),trunk_features(changed,columns),atol=1e-12)

    def test_equal_coordinates_with_conflicting_labels_share_identity(self):
        hashes=fingerprints([[1,2,3],[1,2,3],[1,2,4]])
        self.assertEqual(hashes[0],hashes[1]);self.assertNotEqual(hashes[0],hashes[2])

    def test_error_detection_does_not_require_correct_error_subtype(self):
        result=metrics(['C','H','L','L'],['H','L','L','C'])
        self.assertEqual(result['accuracy'],.25)
        self.assertAlmostEqual(result['error_detection']['recall'],2/3)
        self.assertEqual(result['error_detection']['fp'],1)
        self.assertEqual(result['error_detection']['fn'],1)

if __name__=='__main__':unittest.main()
