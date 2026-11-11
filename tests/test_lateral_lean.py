"""侧倾与摆幅分离、相机旋转、缺证据及正式引擎撤回；不是准确率验证。"""
import copy
import math
import unittest
import numpy as np
from test_gym_actions import raise_sequence
from fitness_app.scene_reference import VERSION, estimate_vertical, lateral_measurement, evaluate_lateral
from fitness_app.posture_engine import create_analysis_engine


def sequence(shift=.08):
    rows = raise_sequence()
    for f in rows:
        f['posture_view']='front'
        f['framing']['scene_reference']={'status':'observed','basis':VERSION,'image_up_degrees':0.,'width':800,'height':800}
        for i in (11,12,13,14,15,16):f['framing']['landmarks'][i]['x']+=shift
    return rows


class LateralTests(unittest.TestCase):
    def test_unvalidated_candidate_is_not_enabled_in_production(self):
        engine=create_analysis_engine('machine_chest_press')
        self.assertFalse(any(r['code'].endswith('trunk_lateral_lean') for r in engine.rules))

    def test_constant_lean_is_detected_while_straight_is_clear(self):
        for shift,status in ((.08,'issue'),(0.,'not_observed')):
            result=evaluate_lateral(sequence(shift),12.,['front'])
            self.assertEqual(result['status'],status,result)
        self.assertEqual(evaluate_lateral(sequence()[:5],12.,['front'])['status'],'unassessable')

    def test_camera_rotation_is_removed_and_side_preserved(self):
        f=sequence()[-1]; original,_=lateral_measurement(f)
        angle=math.radians(15)
        for p in f['framing']['landmarks']:
            x,y=p['x']-.5,p['y']-.5
            p['x']=.5+math.cos(angle)*x-math.sin(angle)*y
            p['y']=.5+math.sin(angle)*x+math.cos(angle)*y
        f['framing']['scene_reference']['image_up_degrees']=15.
        rotated,reason=lateral_measurement(f)
        self.assertIsNone(reason)
        self.assertAlmostEqual(original['angle'],rotated['angle'])
        self.assertEqual(original['side'],rotated['side'])

    def test_missing_axis_occlusion_and_recovery_never_report_normal(self):
        for field in ('axis','shoulder'):
            rows=sequence()
            if field=='axis':rows[-1]['framing'].pop('scene_reference')
            else:rows[-1]['framing']['landmarks'][11]['valid']=False
            self.assertEqual(evaluate_lateral(rows,12.,['front'])['status'],'unassessable')
        rows=sequence();rows[-7]['framing']['landmarks'][11]['valid']=False
        self.assertEqual(evaluate_lateral(rows,12.,['front'])['status'],'unassessable')

    def test_background_reference_needs_long_distributed_lines(self):
        import cv2
        image=np.full((800,800,3),255,np.uint8)
        landmarks=sequence(0)[0]['framing']['landmarks']
        self.assertEqual(estimate_vertical(image,landmarks)['status'],'unavailable')
        for x in (40,130,690,770):cv2.line(image,(x,20),(x,780),(0,0,0),3)
        ref=estimate_vertical(image,landmarks)
        self.assertEqual(ref['status'],'observed',ref)
        self.assertAlmostEqual(ref['image_up_degrees'],0.,places=1)

    def test_formal_engine_persists_side_and_withdraws_when_axis_missing(self):
        engine=create_analysis_engine('machine_chest_press',include_candidates=True)
        self.assertTrue(all(rule['rule_version']==engine.version for rule in engine.rules))
        for f in sequence():out=engine.process(copy.deepcopy(f),{'coaching_view':'front','stream_epoch':'lean'})
        item=next(x for x in out['observation']['posture']['items'] if x['code'].endswith('trunk_lateral_lean'))
        self.assertEqual(item['status'],'issue',item)
        self.assertIn(item['side'],('left','right'))
        self.assertIn('reference_degrees',item['evidence'])
        f=copy.deepcopy(f);f['frame_seq']+=1;f['captured_ms']+=100;f['framing'].pop('scene_reference')
        out=engine.process(f,{'coaching_view':'front','stream_epoch':'lean'})
        item=next(x for x in out['observation']['posture']['items'] if x['code'].endswith('trunk_lateral_lean'))
        self.assertEqual(item['status'],'unassessable')


if __name__=='__main__':unittest.main()
