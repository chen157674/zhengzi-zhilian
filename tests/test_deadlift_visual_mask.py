"""目标掩膜对应与拒判；不把源类别当作单帧分割真值。"""
import unittest
import numpy as np
from fitness_app.deadlift_visual_mask import matched_target,foreground_image,MaskUnassessable


def pose(shift=0):
    p=np.zeros((33,5));p[:,3:]=1
    for i,x,y in ((11,.4,.25),(12,.6,.25),(23,.4,.75),(24,.6,.75)):p[i,:2]=[x+shift,y]
    return p


class ForegroundTests(unittest.TestCase):
    def test_target_association_uses_geometry_and_not_candidate_order(self):
        expected=np.array([[50,25],[50,75]])
        self.assertEqual(matched_target([pose(.5),pose()],expected,100,100),1)
        self.assertEqual(matched_target([pose(),pose(.5)],expected,100,100),0)

    def test_unclear_or_missing_target_refuses(self):
        expected=np.array([[50,25],[50,75]])
        for candidates in ([],[pose(.4)],[pose(),pose(.02)]):
            with self.assertRaises(MaskUnassessable):matched_target(candidates,expected,100,100)

    def test_mask_preserves_foreground_without_mutating_source(self):
        rgb=np.full((100,100,3),200,np.uint8);original=rgb.copy();mask=np.zeros((100,100));mask[10:90,30:70]=1
        result=foreground_image(rgb,mask,np.array([[50,25],[50,75]]))
        np.testing.assert_array_equal(rgb,original);self.assertTrue((result[50,50]==200).all());self.assertTrue((result[0,0]==127).all())

    def test_invalid_area_probabilities_or_torso_outside_mask_refuses(self):
        rgb=np.zeros((100,100,3),np.uint8);expected=np.array([[50,25],[50,75]])
        masks=[np.ones((100,100)),np.zeros((100,100)),np.full((100,100),np.nan),np.full((50,50),.5)]
        off=np.zeros((100,100));off[:,0:20]=1;masks.append(off)
        for mask in masks:
            with self.assertRaises(MaskUnassessable):foreground_image(rgb,mask,expected)


if __name__=='__main__':unittest.main()
