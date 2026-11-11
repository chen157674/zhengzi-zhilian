"""直接检查实际视频处理器的空间分辨率；不加载模型，也不用文本分数证明准确性。"""
import unittest
import numpy as np
from fitness_app.paths import configure_environment
configure_environment()
from fitness_app.video_vlm import VideoActionVLM,CONFIG


class VideoVLMInputTests(unittest.TestCase):
    def test_actual_processor_preserves_per_frame_pixel_budget(self):
        from transformers import AutoProcessor
        model=VideoActionVLM();model.processor=AutoProcessor.from_pretrained(model.directory,local_files_only=True)
        frames=[np.full((720,1280,3),120+i,np.uint8) for i in range(8)]
        inputs=model.prepare_inputs(frames,[i*400 for i in range(8)])
        temporal,height,width=map(int,inputs['video_grid_thw'][0].cpu())
        patch=model.processor.video_processor.patch_size
        self.assertEqual(temporal,4)
        self.assertGreaterEqual(height*width*patch*patch,CONFIG['maximum_pixels']*.5)
        self.assertLessEqual(height*width*patch*patch,CONFIG['maximum_pixels'])
        self.assertGreaterEqual(height*patch,224)
        self.assertGreaterEqual(width*patch,384)


if __name__=='__main__':unittest.main()
