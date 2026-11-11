import unittest
import numpy as np
from fitness_app.upper_body_roi import upper_body_roi,crop_images


class UpperBodyROITest(unittest.TestCase):
    def arrays(self):
        a=np.zeros((8,33,5),np.float32);a[:,:,3:]=1;a[:,:,:2]=(.5,.5)
        for joint,xy in {0:(.5,.2),11:(.4,.35),12:(.6,.35),13:(.3,.4),14:(.7,.4),15:(.25,.3),16:(.75,.3),23:(.4,.65),24:(.6,.65)}.items():a[:,joint,:2]=xy
        return {'timestamps':np.arange(8)*100,'points':a,'people':np.ones(8,int)}

    def test_one_constant_context_crop_contains_all_visible_arm_extremes(self):
        arrays=self.arrays();arrays['points'][3,15,0]=.15;value=upper_body_roi(arrays,arrays['timestamps'])
        self.assertIsNone(value['fallback']);l,t,r,b=value['crop'];self.assertLess(l,.15);self.assertGreater(r,.75);self.assertLess(t,.2);self.assertGreater(b,.65)
        frames=[np.zeros((100,200,3),np.uint8) for _ in range(8)];out=crop_images(frames,value['crop']);self.assertEqual(len(out),8);self.assertEqual(len({v.shape for v in out}),1)

    def test_occlusion_multiple_people_or_unaligned_pose_falls_back(self):
        for mode in ('occlusion','multiple','stale'):
            arrays=self.arrays()
            if mode=='occlusion':arrays['points'][:3,15,3]=.5
            elif mode=='multiple':arrays['people'][:3]=2
            else:arrays['timestamps']+=10000
            with self.subTest(mode=mode):self.assertIsNone(upper_body_roi(arrays,np.arange(8)*100)['crop'])

    def test_any_missing_frame_retains_full_picture_and_no_joint_is_invented(self):
        arrays=self.arrays();arrays['points'][:2,15,0]=np.nan;value=upper_body_roi(arrays,arrays['timestamps'])
        self.assertIsNone(value['crop']);self.assertEqual(value['valid_samples'],6)
        arrays['points'][2,16,1]=1.1;self.assertIsNone(upper_body_roi(arrays,arrays['timestamps'])['crop'])

    def test_near_full_image_retains_identical_pixels_and_bad_crop_is_rejected(self):
        arrays=self.arrays();arrays['points'][:,15,:2]=(.01,.01);arrays['points'][:,16,:2]=(.99,.99)
        self.assertEqual(upper_body_roi(arrays,arrays['timestamps'])['fallback'],'near_full_frame')
        frames=[np.zeros((20,30,3),np.uint8)];self.assertIs(crop_images(frames,None),frames)
        with self.assertRaises(ValueError):crop_images(frames,[0,0,float('nan'),1])


if __name__=='__main__':unittest.main()
