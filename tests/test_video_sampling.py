"""完整画面预处理的实际内容保留检查，防止竖屏裁掉头顶器械和脚部。"""
import unittest
import numpy as np
from fitness_app.video_sampling import fit_pixels

CONFIG={'image_mean':[.485,.456,.406],'image_std':[.229,.224,.225]}


class VideoSamplingTests(unittest.TestCase):
    def colors(self,value):return value.transpose(1,2,0)*np.array(CONFIG['image_std'])+np.array(CONFIG['image_mean'])
    def test_portrait_top_and_bottom_content_survives(self):
        image=np.full((300,100,3),128,np.uint8);image[:20]=[255,0,0];image[-20:]=[0,0,255]
        result=self.colors(fit_pixels([image],CONFIG)[0])
        self.assertGreater(((result[:,:,0]>.9)&(result[:,:,2]<.1)).sum(),500)
        self.assertGreater(((result[:,:,2]>.9)&(result[:,:,0]<.1)).sum(),500)

    def test_landscape_equipment_edges_survive_and_input_is_unchanged(self):
        image=np.full((100,300,3),128,np.uint8);image[:,:20]=[255,0,0];image[:,-20:]=[0,0,255];original=image.copy()
        result=self.colors(fit_pixels([image],CONFIG)[0])
        self.assertTrue((result[:,:15,0]>.9).any());self.assertTrue((result[:,-15:,2]>.9).any())
        np.testing.assert_array_equal(image,original)

    def test_augmentation_is_shared_across_frames_and_keeps_sequence_order(self):
        frames=[]
        for i in range(8):
            frame=np.full((100,300,3),80+i*15,np.uint8);frame[:,:20]=[255,0,0];frames.append(frame)
        original=[p.copy() for p in frames]
        result=fit_pixels(frames,CONFIG,{'flip':True,'brightness':1.,'contrast':1.,'scale':.95})
        self.assertTrue(all(self.colors(frame)[:,190:,0].max()>.9 for frame in result))
        self.assertTrue(np.all(np.diff(result[:,0,112,112])>0))
        for first,last in zip(original,frames):np.testing.assert_array_equal(first,last)


if __name__=='__main__':unittest.main()
