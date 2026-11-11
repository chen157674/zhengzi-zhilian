"""手机竖屏帧实际JPEG/元数据路径；不调用物理相机或重跑视觉准确率。"""
from io import BytesIO
import unittest
from unittest.mock import patch
from PIL import Image
from fitness_app.frame_pipeline import jpeg_dimensions,decode_jpeg
from fitness_app.auth import APIError
from fitness_app.preview import PreviewManager
from fitness_app.vision_slot import VisionSlot
from fitness_app.catalogue import get_action
from fitness_app.db_schema import new_id,now_ms


class MobileFrameTests(unittest.TestCase):
    def jpeg(self,width,height):
        stream=BytesIO();Image.new('RGB',(width,height),(90,105,115)).save(stream,format='JPEG');return stream.getvalue()
    def test_portrait_and_landscape_decode_keep_orientation_and_size(self):
        for width,height in [(720,1280),(1280,720),(1080,1920),(1920,1080)]:
            with self.subTest(width=width,height=height):
                data=self.jpeg(width,height);self.assertEqual(jpeg_dimensions(data),(width,height))
                self.assertEqual(decode_jpeg(data).shape,(height,width,3))
    def test_rotated_sizes_do_not_relax_long_edge_or_short_edge_limits(self):
        for width,height in [(1081,1920),(1920,1081),(720,1921),(1921,720)]:
            with self.subTest(width=width,height=height),self.assertRaises(APIError) as got:jpeg_dimensions(self.jpeg(width,height))
            self.assertEqual(got.exception.code,'FRAME_DIMENSIONS')

    def test_phone_portrait_metadata_reaches_preview_queue_without_model_or_camera(self):
        manager=PreviewManager(VisionSlot());uid,auth=new_id(),new_id()
        try:
            with patch.object(manager,'start'):
                item=manager.create(uid,auth,now_ms()+60000,{'action_id':'bodyweight_squat',
                    'capability_version':get_action('bodyweight_squat')['capability_version'],'page_id':new_id()},new_id())
                result=manager.submit(uid,item['preview_id'],item['preview_token'],{'stream_epoch':item['stream_epoch'],
                    'frame_seq':1,'captured_ms':100,'width':720,'height':1280},self.jpeg(720,1280))
                self.assertEqual(result['frame_seq'],1)
        finally:manager.stop()


if __name__=='__main__':unittest.main()
