"""17标签语义、观测阶段与同帧髋膝证据；D-POSE阈值不属于正式标准。"""
import copy
from dataclasses import replace
import math
import unittest

from fitness_app.barbell_quality import observe_press_alignment,observe_row_torso,DeadliftExtensionRule,evaluate_deadlift_extension


def base(seq=0):
    return {'frame_seq':seq,'captured_ms':seq*100,'width':1000,'height':1000,'framing':{'person_count':1,
        'landmarks':[dict(x=.5,y=.5,z=0.,visibility=.99,presence=.99,valid=True) for _ in range(33)]}}


def press(outward=0):
    f=base();p=f['framing']['landmarks']
    for s,x,sign in ((0,.4,-1),(1,.6,1)):
        for i,px,py in ((11,x,.4),(13,x+sign*outward,.55),(15,x,.35)):p[i+s].update(x=px,y=py)
    return f


def lift(hip_angle,knee_angle,seq=0):
    f=base(seq);p=f['framing']['landmarks'];a,b=math.radians(hip_angle),math.radians(knee_angle)
    for s in (0,1):
        coords={23:(.55,.5),25:(.55,.7),27:(.55+.2*math.sin(b),.7-.2*math.cos(b)),11:(.55+.25*math.sin(a),.5+.25*math.cos(a))}
        for i,(x,y) in coords.items():p[i+s].update(x=x+s*.02,y=y)
    return f


LEVELS=[0]*5+[.2,.4,.6,.8,1,1,1,1,.8,.6,.4,.2]+[0]*5
def cycle(hip_top=175,knee_top=175):return [lift(80+(hip_top-80)*x,100+(knee_top-100)*x,i) for i,x in enumerate(LEVELS)]
def rule():return DeadliftExtensionRule(160.,160.,6.,100,250,'D-POSE-extension','Known geometry only; no GB/PB mapping')
def evaluate(frames,event=None,r=None):
    if event is None:event={'side':'none','start_ms':0,'end_ms':frames[-1]['captured_ms'],'assessments':[]}
    return evaluate_deadlift_extension(event,frames,view='side',conventional_barbell_confirmed=True,measurement_side='left',rule=r or rule())


class BarbellQualityTests(unittest.TestCase):
    def test_press_observes_alignment_and_direction_not_extension(self):
        ctx=dict(view='front',standing_barbell_confirmed=True,rack_phase_confirmed=True,orientation_up=(0,-1))
        for offset in (0,.1,-.05):
            result=observe_press_alignment(press(offset),**ctx)
            self.assertEqual(result['status'],'unassessable');self.assertFalse(result['publishable'])
            for arm in result['arms'].values():
                self.assertAlmostEqual(arm['elbow_outside_wrist_forearm_ratio'],offset/math.hypot(offset,.2))
                self.assertGreater(arm['elbow_below_wrist_forearm_ratio'],0)
            self.assertNotIn('elbow_extension_degrees',result)

    def test_press_wrong_view_variant_phase_and_missing_arm_refuse(self):
        ctx=dict(view='front',standing_barbell_confirmed=True,rack_phase_confirmed=True,orientation_up=(0,-1));f=press()
        for patch in ({'view':'side'},{'standing_barbell_confirmed':False},{'rack_phase_confirmed':False}):
            self.assertNotIn('arms',observe_press_alignment(f,**{**ctx,**patch}))
        f['framing']['landmarks'][16]['valid']=False
        self.assertNotIn('arms',observe_press_alignment(f,**ctx))

    def test_row_torso_angle_is_static_geometry_not_swing_or_lumbar(self):
        for angle in (30,60,100):
            f=base();p=f['framing']['landmarks'];p[23].update(x=.6,y=.65);p[11].update(x=.6-.3*math.sin(math.radians(angle)),y=.65-.3*math.cos(math.radians(angle)))
            result=observe_row_torso(f,view='side',barbell_row_confirmed=True,row_phase_confirmed=True,measurement_side='left',orientation_up=(0,-1))
            self.assertAlmostEqual(result['torso_tilt_from_up_degrees'],angle);self.assertEqual(result['status'],'unassessable')
            self.assertNotIn('lumbar_curvature',result);self.assertNotIn('trunk_swing',result)

    def test_row_standing_transition_missing_parts_and_unknown_view_refuse(self):
        ctx=dict(view='side',barbell_row_confirmed=True,row_phase_confirmed=True,measurement_side='left',orientation_up=(0,-1));f=lift(120,160)
        for patch in ({'view':'front'},{'row_phase_confirmed':False},{'barbell_row_confirmed':False}):
            self.assertNotIn('torso_tilt_from_up_degrees',observe_row_torso(f,**{**ctx,**patch}))
        f['framing']['landmarks'][23]['valid']=False
        self.assertNotIn('torso_tilt_from_up_degrees',observe_row_torso(f,**ctx))

    def test_deadlift_top_hip_and_knee_and_missing_threshold(self):
        result=evaluate(cycle());self.assertEqual(result['status'],'not_observed');self.assertAlmostEqual(result['hip_image_degrees'],175.);self.assertAlmostEqual(result['knee_image_degrees'],175.)
        self.assertEqual(evaluate(cycle(hip_top=140))['status'],'issue');self.assertEqual(evaluate(cycle(knee_top=140))['status'],'issue')
        no_labels=evaluate(cycle(),r=replace(rule(),minimum_hip_degrees=None,minimum_knee_degrees=None))
        self.assertEqual(no_labels['reason'],'quality_threshold_unavailable');self.assertFalse(no_labels['publishable'])

    def test_deadlift_non_simultaneous_maxima_cannot_be_combined_as_normal(self):
        frames=cycle()
        for i in range(9,13):frames[i]=lift(175 if i<11 else 140,140 if i<11 else 175,i)
        result=evaluate(frames);self.assertEqual(result['status'],'issue',result)

    def test_deadlift_half_cycle_or_top_missing_not_normal(self):
        frames=cycle();ctx=dict(view='side',conventional_barbell_confirmed=True,measurement_side='left',rule=rule())
        self.assertIsNone(evaluate_deadlift_extension(None,frames,**ctx))
        frames[10]['framing']['landmarks'][23]['valid']=False
        self.assertEqual(evaluate(frames)['reason'],'top_or_cycle_parts_unobservable')
        self.assertEqual(evaluate(cycle()[:13])['reason'],'top_not_bracketed_by_complete_cycle')

    def test_deadlift_wrong_variant_front_view_or_gap_refuse(self):
        frames=cycle();event={'side':'none','start_ms':0,'end_ms':frames[-1]['captured_ms']};ctx=dict(view='side',conventional_barbell_confirmed=True,measurement_side='left',rule=rule())
        for patch in ({'view':'front'},{'conventional_barbell_confirmed':False}):
            self.assertEqual(evaluate_deadlift_extension(event,frames,**{**ctx,**patch})['status'],'unassessable')
        self.assertEqual(evaluate(frames[:8]+frames[12:])['reason'],'cycle_evidence_gap')


if __name__=='__main__':unittest.main()
