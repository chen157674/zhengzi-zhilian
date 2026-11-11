"""慢动作候选保留真实时间；不以抽帧/契约检查当准确率成绩。"""
import unittest
from unittest.mock import Mock
from pathlib import Path
import sys
import numpy as np
from fitness_app.paths import ROOT
from fitness_app.compact_video import CompactVideoVLM
sys.path.insert(0,str(ROOT/'scripts'))
from evaluate_compact_video import frames


class CompactVideoTests(unittest.TestCase):
    def test_long_video_sampling_keeps_actual_seven_second_context(self):
        path='data/datasets/development-movements/seven-subject_016_shoulder_good_diagonal.mp4'
        if not (ROOT/path).exists():self.skipTest('需已有公开数据原片')
        images,times=frames({'path':path,'start_ms':3000,'end_ms':27000,'crop':None})
        self.assertEqual(len(images),8);self.assertGreater(times[-1]-times[0],7500)
        self.assertTrue(all(b>a for a,b in zip(times,times[1:])))
        self.assertTrue(all(r.dtype==np.uint8 and r.ndim==3 for r in images))
        self.assertGreaterEqual(times[0],11000);self.assertLess(times[-1],19200)

    def test_overlong_or_reordered_context_rejects_before_gpu_load(self):
        model=CompactVideoVLM.__new__(CompactVideoVLM);model.load=Mock()
        images=[np.zeros((16,16,3),np.uint8) for _ in range(8)]
        for times in (np.arange(8)*1400,[0,100,200,200,400,500,600,700]):
            with self.assertRaises(ValueError):model.predict(images,times)
        model.load.assert_not_called()


if __name__=='__main__':unittest.main()
