"""候选预处理的新坐标边界；复用真实双人IMAGE结果，不重新运行模型。"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from fitness_app.pose_input import ContentPoseService
from fitness_app.frame_pipeline import framing
from fitness_app.paths import ROOT


def points():
    return [{'x':.5,'y':.5,'z':.2,'visibility':.99,'presence':.99} for _ in range(33)]


class PoseInputTests(unittest.TestCase):
    def test_video_crop_jitter_uses_fixed_anchor_and_raw_visibility_bounds(self):
        engines=[]
        class TrackingPose:
            def __init__(self, **kwargs):self.closed=False;self.shapes=[];engines.append(self)
            def detect(self,rgb,stamp):
                self.shapes.append(rgb.shape)
                return {'poses':[points(),points()],'model':'fixture-model'}
            def close(self):self.closed=True
        image=np.full((240,320,3),150,dtype=np.uint8)
        tops=[12,13,12,14,15,16]
        with patch('fitness_app.pose_input.detect_content_bounds',side_effect=[[0,t/240,1,227/240] for t in tops]):
            with ContentPoseService(video=True,pose_factory=TrackingPose) as engine:
                results=[engine.detect(image,i*33) for i in range(len(tops))]
        self.assertEqual([r['input_transform']['tracker_reset'] for r in results],[False,False,False,False,True,False])
        self.assertEqual([r['input_transform']['crop_pixels'][1] for r in results],[12,12,12,12,15,15])
        self.assertEqual(len(engines),2);self.assertTrue(all(e.closed for e in engines))
        self.assertEqual(results[1]['content_bounds'][1],13/240)
        self.assertEqual(results[1]['input_transform']['detected_crop_pixels'][1],13)
        self.assertEqual(len(results[1]['poses']),2,'不能借裁切稳定选择或丢弃其他人')
        self.assertAlmostEqual(results[1]['poses'][0][0]['y'],(12+.5*215)/240)

    def test_resolution_change_resets_and_image_mode_does_not_stabilize(self):
        class TrackingPose:
            def __init__(self,**kwargs):pass
            def detect(self,rgb,stamp):return {'poses':[points()],'model':'fixture-model'}
            def close(self):pass
        with patch('fitness_app.pose_input.detect_content_bounds',return_value=[0,0,1,1]):
            with ContentPoseService(video=True,pose_factory=TrackingPose) as engine:
                engine.detect(np.ones((100,160,3),dtype=np.uint8),0)
                self.assertTrue(engine.detect(np.ones((101,160,3),dtype=np.uint8),33)['input_transform']['tracker_reset'])
        with patch('fitness_app.pose_input.detect_content_bounds',side_effect=[[0,.10,1,.9],[0,.11,1,.9]]):
            with ContentPoseService(video=False,pose_factory=TrackingPose) as engine:
                engine.detect(np.ones((100,160,3),dtype=np.uint8))
                result=engine.detect(np.ones((100,160,3),dtype=np.uint8))
                self.assertEqual(result['input_transform']['crop_pixels'][1],11)
                self.assertFalse(result['input_transform']['crop_stabilized'])

    def test_geometry_change_restarts_tracking_but_keeps_source_clock(self):
        engines=[]
        class TrackingPose:
            def __init__(self, **kwargs):
                self.stamps=[];self.closed=False;engines.append(self)
            def detect(self,rgb,stamp):
                self.stamps.append(stamp)
                return {'poses':[points()],'model':'fixture-model'}
            def close(self):self.closed=True
        image=np.full((100,160,3),150,dtype=np.uint8)
        padded=image.copy();padded[:10]=0;padded[-10:]=0
        with ContentPoseService(video=True,pose_factory=TrackingPose) as engine:
            self.assertFalse(engine.detect(image,0)['input_transform']['tracker_reset'])
            self.assertFalse(engine.detect(image,60)['input_transform']['tracker_reset'])
            self.assertTrue(engine.detect(padded,120)['input_transform']['tracker_reset'])
            self.assertEqual(len(engines),2);self.assertTrue(engines[0].closed)
            self.assertEqual(engines[0].stamps,[0,60]);self.assertEqual(engines[1].stamps,[120])
            with self.assertRaisesRegex(ValueError,'递增'):engine.detect(image,120)
            self.assertEqual(len(engines),2,'旧时间戳不能通过切换裁切范围绕过验证')
        self.assertTrue(engines[1].closed)
        engine.close()  # 重复收尾不重复关闭底层资源。

    def factory(self, expected, poses):
        test=self
        class CachedPose:
            def __init__(self, **kwargs):
                self.closed=False
            def detect(self, rgb, timestamp):
                test.assertTrue(np.array_equal(rgb, expected))
                test.assertTrue(rgb.flags.c_contiguous)
                return {'poses':poses,'model':'fixture-model','timestamp_ms':timestamp}
            def close(self):
                self.closed=True
        return CachedPose

    def test_padding_maps_xy_and_z_without_mutating_raw_pose(self):
        inner=np.full((80,128,3),150,dtype=np.uint8)
        image=np.pad(inner,((10,10),(16,16),(0,0)))
        raw=[points()];raw[0][0]['x']=.25;original=copy.deepcopy(raw)
        with ContentPoseService(video=True,pose_factory=self.factory(inner,raw)) as engine:
            result=engine.detect(image,123)
            self.assertEqual(result['input_transform']['crop_pixels'],[16,10,144,90])
            self.assertAlmostEqual(result['poses'][0][0]['x'],.3)
            self.assertAlmostEqual(result['poses'][0][0]['y'],.5)
            self.assertAlmostEqual(result['poses'][0][0]['z'],.16)
            self.assertEqual(raw,original)
            underlying=engine.pose
        self.assertTrue(underlying.closed)

    def test_uncertain_padding_and_extrapolated_points_are_not_hidden(self):
        image=np.zeros((100,160,3),dtype=np.uint8);image[20:80,65:95]=150
        raw=[points()]
        with ContentPoseService(pose_factory=self.factory(image,raw)) as engine:
            result=engine.detect(image)
            self.assertEqual(result['content_bounds'],[0.,0.,1.,1.])
            self.assertEqual(result['poses'],raw)
        inner=np.full((80,128,3),150,dtype=np.uint8);padded=np.pad(inner,((10,10),(16,16),(0,0)))
        raw[0][27]['x']=-.1
        with ContentPoseService(pose_factory=self.factory(inner,raw)) as engine:
            result=engine.detect(padded)
            self.assertLess(result['poses'][0][27]['x'],result['content_bounds'][0])
            self.assertEqual(framing('jumping_jack',result)['code'],'MISSING_PARTS')

    def test_large_combined_padding_is_not_assumed_to_be_confirmed(self):
        inner=np.full((360,640,3),150,dtype=np.uint8)
        image=np.pad(inner,((180,180),(320,320),(0,0)))
        # 原规则要求内侧对比覆盖足够宽；双轴各25%时不擅自裁选中间区域。
        with ContentPoseService(pose_factory=self.factory(image,[points()])) as engine:
            self.assertEqual(engine.detect(image)['content_bounds'],[0.,0.,1.,1.])

    def test_retained_real_two_person_candidates_survive_remapping(self):
        report=json.loads((ROOT/'data/verification/step-19/pose-ambiguity-comparison.json').read_text(encoding='utf-8'))
        case=next(r for r in report['runs'] if r['identity']['protocol']==2)
        sheet=Image.open(ROOT/'data/verification/step-14/sit-up-source.png').convert('RGB')
        for row in case['observations']:
            with self.subTest(frame=row['source_review_time']):
                box=case['identity']['crops'][row['source_review_time']]
                inner=np.ascontiguousarray(np.asarray(sheet.crop(box).resize((640,360),Image.Resampling.BICUBIC)))
                padded=np.pad(inner,((45,45),(80,80),(0,0)))
                with ContentPoseService(pose_factory=self.factory(inner,row['poses'])) as engine:
                    result=engine.detect(padded)
                    self.assertEqual(len(result['poses']),2)
                    self.assertEqual(framing('jumping_jack',result)['code'],'TARGET_AMBIGUOUS')


if __name__=='__main__':
    unittest.main()
