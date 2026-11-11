"""局部手点恢复到原图、缺失语义和时序边界；不伪造握法标签。"""
import unittest
from types import SimpleNamespace as NS
import numpy as np
from fitness_app.hand_pose import HandPoseService


class HandTests(unittest.TestCase):
    def service(self):
        service=object.__new__(HandPoseService)
        service.mp=NS(ImageFormat=NS(SRGB='RGB'),Image=lambda **kwargs:kwargs)
        service.video=False;service.last_stamp=-1
        service.resource={'model':'fixture','sha256':'fixture'}
        result=NS(hand_landmarks=[[NS(x=.5,y=.5,z=.1) for _ in range(21)]],
                  handedness=[[NS(score=.9,category_name='Left')]])
        service.landmarker=NS(detect=lambda image:result,detect_for_video=lambda image,stamp:result)
        return service

    def test_crop_maps_to_original_and_does_not_claim_finger_visibility(self):
        service=self.service()
        result=service.detect(np.zeros((100,200,3),np.uint8),0,(20,10,100,90))
        hand=result['hands'][0];point=hand['landmarks'][0]
        self.assertAlmostEqual(point['x'],.3);self.assertAlmostEqual(point['y'],.5)
        self.assertFalse(hand['finger_visibility_verified'])
        self.assertEqual(result['grip_assessment'],'not_implemented')

    def test_invalid_crop_and_video_time_are_rejected(self):
        service=self.service();rgb=np.zeros((100,200,3),np.uint8)
        with self.assertRaises(ValueError):service.detect(rgb,0,(-1,0,100,100))
        with self.assertRaises(ValueError):service.detect(rgb.astype(np.float32))
        service.video=True;service.detect(rgb,100)
        with self.assertRaises(ValueError):service.detect(rgb,100)

    def test_no_hands_is_unknown_not_correct(self):
        service=self.service();service.landmarker=NS(detect=lambda image:NS(hand_landmarks=[],handedness=[]))
        result=service.detect(np.zeros((100,200,3),np.uint8))
        self.assertEqual(result['hands'],[])
        self.assertTrue(result['no_hand_does_not_mean_correct_grip'])


if __name__=='__main__':unittest.main()
