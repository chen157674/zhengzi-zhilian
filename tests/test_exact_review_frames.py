"""MPEG-4索引seek跳帧回归：确切时间必须对应真实原帧，不能用邻帧替代。"""
import json
import unittest
from unittest.mock import patch
import av
import numpy as np
from fitness_app.paths import ROOT
from scripts.evaluate_pull_press_review import images


class ExactReviewFrameTests(unittest.TestCase):
    def test_index_seek_skipped_frame_is_read_by_sequential_decode(self):
        entries=json.loads((ROOT/'data/verification/step-22/semantic-video-features.json').read_text(encoding='utf-8'))['entries']
        entry=next(v for v in entries if v['id']=='seven-5839a9417b0b72bf-3087-27780')
        path=ROOT/entry['path'];times=[13867,14333,14733,15200,15667,16067,16533,17000]
        actual=images(path,times);expected={}
        with av.open(str(path)) as container:
            for frame in container.decode(video=0):
                stamp=round(float(frame.time)*1000)
                if stamp in times:expected[stamp]=frame.to_ndarray(format='rgb24')
                if stamp>times[-1]:break
        self.assertEqual(len(actual),8)
        for stamp,pixels in zip(times,actual):np.testing.assert_array_equal(pixels,expected[stamp])
        with self.assertRaises(ValueError):images(path,[16533,16533])
        with self.assertRaises(ValueError):images(path,[16533,16067])

    def test_native_replay_only_converts_selected_real_frames_and_drops_late_source(self):
        from scripts.verify_live_lat_correction import native_capture_frames
        from fitness_app.media import probe_video,video_frames,display_rgb
        item=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))['demos']['user-1688']
        path=ROOT/item['video']['path'];info=probe_video(path)
        expected={}
        for stamp,rgb in video_frames(path,info):
            if stamp in (0,200,400,600,1100):expected[stamp]=rgb
            if stamp>=1100:break
        timing={'origin':None,'dropped':0};iterator=native_capture_frames(path,info,timing)
        try:
            with patch('fitness_app.media.display_rgb',wraps=display_rgb) as convert:
                for stamp in (0,200,400,600):
                    actual,pixels=next(iterator);self.assertEqual(actual,stamp)
                    np.testing.assert_array_equal(pixels,expected[stamp])
                self.assertEqual(convert.call_count,4)
                timing['origin']=0
                with patch('scripts.verify_live_lat_correction.time.monotonic',return_value=1.3):
                    actual,pixels=next(iterator)
                self.assertEqual(actual,1100)
                np.testing.assert_array_equal(pixels,expected[1100])
                self.assertEqual(convert.call_count,5)
                self.assertGreater(timing['dropped'],0)
        finally:iterator.close()


if __name__=='__main__':unittest.main()
