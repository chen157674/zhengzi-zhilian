"""实际8帧对齐及无标签像素输入，不启动视觉模型。"""
import unittest
from unittest.mock import patch
import numpy as np
from scripts.temporal_expert_features import normalized,exact_images,recovering_images,sequential_exact_images,decoder_only_upgrade


class TemporalFeatureTests(unittest.TestCase):
    def test_normalization_requires_real_finite_nonzero_expected_dimension(self):
        value=np.arange(512,dtype=np.float32);self.assertAlmostEqual(float(np.linalg.norm(normalized(value))),1.,places=6)
        for bad in (np.zeros(512),np.full(512,np.nan),np.ones(511)):
            with self.assertRaises(ValueError):normalized(bad)
    def test_neighboring_frame_is_not_substituted_for_frozen_actual_time(self):
        rgb=np.zeros((40,60,3),np.uint8)
        with patch('scripts.temporal_expert_features.decode',return_value=[(1033,rgb)]):
            with self.assertRaises(ValueError):exact_images({'crop':None},[1000])
    def test_crop_uses_original_pixels_and_class_labels_do_not_change_input(self):
        rgb=np.arange(40*60*3,dtype=np.uint8).reshape(40,60,3)
        with patch('scripts.temporal_expert_features.decode',return_value=[(1000,rgb)]):
            first=exact_images({'crop':[.25,0,.75,1],'action_id':'dumbbell_bench_press'},[1000])[0]
            second=exact_images({'crop':[.25,0,.75,1],'action_id':'unknown'},[1000])[0]
        np.testing.assert_array_equal(first,rgb[:,15:45]);np.testing.assert_array_equal(first,second)

    def test_seek_failure_uses_exact_sequential_frames_and_missing_frame_still_refuses(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        rgb=np.arange(40*60*3,dtype=np.uint8).reshape(40,60,3);frame=SimpleNamespace(time=1.)
        row={'id':'source','path':'data/datasets/development-movements/commons-gantner-dumbbell-bench.webm','crop':[.25,0,.75,1]}
        video=SimpleNamespace(decode=lambda **kwargs:iter([frame]))
        with patch('scripts.temporal_expert_features.exact_images',side_effect=ValueError('wrong keyframe')),patch('scripts.temporal_expert_features.av.open',return_value=nullcontext(video)),patch('scripts.temporal_expert_features.display_rgb',return_value=rgb):
            images,recovered=recovering_images(row,[1000]);self.assertTrue(recovered);np.testing.assert_array_equal(images[0],rgb[:,15:45])
        frame.time=1.033
        with patch('scripts.temporal_expert_features.av.open',return_value=nullcontext(video)),patch('scripts.temporal_expert_features.display_rgb',return_value=rgb):
            with self.assertRaises(ValueError):sequential_exact_images(row,[1000])

    def test_only_adding_decoder_recovery_is_compatible_not_changed_pixels_or_model(self):
        original={'model':'fixed','normalizer':'fixed','crop':'fixed'};added={'sequential_exact_sha256':'new','recovery_sha256':'new'}
        self.assertTrue(decoder_only_upgrade(original,{**original,**added}))
        for changed in ({'model':'other'},{'normalizer':'other'},{'crop':'other'}):self.assertFalse(decoder_only_upgrade(original,{**original,**added,**changed}))
        self.assertFalse(decoder_only_upgrade({**original,**added},{**original,**added}))


if __name__=='__main__':unittest.main()
