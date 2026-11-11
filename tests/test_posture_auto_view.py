"""自动机位稳定性及规则内视角变化；不把真实不支持的机位当作正常。"""
import copy
import unittest
from fitness_app.gym_quality import evaluate
from fitness_app.posture_engine import create_analysis_engine,estimate_view
from test_barbell_row import sequence
from test_gym_actions import raise_sequence


class AutoViewTests(unittest.TestCase):
    def test_supported_adjacent_views_are_not_a_declared_mismatch(self):
        frames=sequence()
        for i,f in enumerate(frames):f['posture_view']='side' if i%2 else 'oblique'
        value=evaluate('pec_deck_fly.trunk_sway',frames,'oblique',18.,allowed_views=['side','oblique'])
        self.assertEqual(value['status'],'not_observed')
        frames[-1]['posture_view']='front'
        self.assertEqual(evaluate('pec_deck_fly.trunk_sway',frames,'oblique',18.,allowed_views=['side','oblique'])['status'],'unassessable')

    def test_auto_boundary_jitter_is_smoothed_and_missing_current_points_still_rejected(self):
        frames=raise_sequence();engine=create_analysis_engine('pec_deck_fly');raw=[];resolved=[]
        for i,f in enumerate(frames):
            width=.3*(.76 if i%6==0 else .70)
            for joint in (11,23):f['framing']['landmarks'][joint]['x']=.5-width/2
            for joint in (12,24):f['framing']['landmarks'][joint]['x']=.5+width/2
            raw.append(estimate_view(f))
            out=engine.process(f,{'coaching_view':'auto','stream_epoch':'view'})
            resolved.append(out['observation']['posture']['view'])
        self.assertIn('front',raw);self.assertIn('oblique',raw)
        self.assertLess(resolved[6:].count('front'),raw[6:].count('front'))
        missing=copy.deepcopy(frames[-1]);missing['captured_ms']+=100;missing['frame_seq']+=1
        missing['framing']['landmarks'][11]['valid']=False
        self.assertEqual(engine.process(missing,{'coaching_view':'auto','stream_epoch':'view'})['observation']['posture']['view'],'unknown')

    def test_manual_front_side_conflict_is_preserved(self):
        engine=create_analysis_engine('pec_deck_fly')
        out=engine.process(raise_sequence()[0],{'coaching_view':'side','stream_epoch':'manual'})
        self.assertEqual(out['observation']['posture']['view_basis'],'view_mismatch')

    def test_pec_deck_near_front_bilateral_height_and_true_side_rejection(self):
        for asymmetric in (False,True):
            frames=raise_sequence(asymmetric)
            value=evaluate('pec_deck_fly.arm_asymmetry',frames,'oblique',.25,allowed_views=['front','oblique'])
            self.assertEqual(value['status'],'issue' if asymmetric else 'not_observed')
        frames=raise_sequence()
        for f in frames:
            for joint in (12,24):f['framing']['landmarks'][joint]['x']=f['framing']['landmarks'][joint-1]['x']+.05
        self.assertEqual(evaluate('pec_deck_fly.arm_asymmetry',frames,'oblique',.25,allowed_views=['front','oblique'])['status'],'unassessable')


if __name__=='__main__':unittest.main()
