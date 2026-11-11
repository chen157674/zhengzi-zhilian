"""显式D-POSE比较参数仅验计算／阶段；不作生物力学标准或独立准确率。"""
import copy
from dataclasses import replace
import unittest

from fitness_app.quality_candidates import CandidateRule,SCHEMA,SPECS,evaluate_cycle
from test_action_counting import geometry as arm_geometry
from test_reverse_lunge import geometry as lunge_geometry
from test_glute_bridge import geometry as bridge_geometry
from test_sit_up import geometry as sit_geometry

LEVELS=[0]*5+[.2,.4,.6,.8,1,1,1,1,.8,.6,.4,.2]+[0]*5
THRESHOLDS={'bodyweight_squat':0.,'standard_push_up':.75,'reverse_lunge':120.,'bodyweight_glute_bridge':140.,'bent_knee_sit_up':65.,'standing_dumbbell_curl':75.}


def fixture(action,shallow=False):
    frames=[]
    for i,level in enumerate(LEVELS):
        if action in {'bodyweight_squat','standard_push_up','standing_dumbbell_curl'}:
            low=110 if action=='bodyweight_squat' else 120
            f=arm_geometry(action,170-((170-low) if shallow else 90)*level,seq=i)
        elif action=='reverse_lunge':f=lunge_geometry(level*(.4 if shallow else 1),seq=i)
        elif action=='bodyweight_glute_bridge':f=bridge_geometry(level*(.25 if shallow else 1),seq=i)
        else:f=sit_geometry(level*(55 if shallow else 80),seq=i)
        f['captured_ms']=i*100;frames.append(f)
    side='left' if action in {'reverse_lunge','standing_dumbbell_curl'} else 'none'
    event={'side':side,'start_ms':0,'end_ms':frames[-1]['captured_ms'],'assessments':[]}
    rule=CandidateRule(SPECS[action][0],SCHEMA,THRESHOLDS[action],.01 if action=='bodyweight_glute_bridge' else 6.,100,250,'D-POSE-range-test','Known synthetic geometry; no training or clinical standard')
    context={'measurement_side':'left','view':'oblique' if action in {'reverse_lunge','standing_dumbbell_curl'} else 'side','variant_confirmed':True,'rule':rule}
    return frames,event,context


class QualityCandidateTests(unittest.TestCase):
    def check_action(self,action):
        frames,event,context=fixture(action);original=copy.deepcopy(event)
        result=evaluate_cycle(action,event,frames,**context)
        self.assertEqual(result['status'],'not_observed',result);self.assertFalse(result['publishable']);self.assertEqual(result['side'],event['side'])
        self.assertLess(result['start_ms'],result['end_ms']);self.assertTrue(0<result['start_ms']<result['end_ms']<event['end_ms'])
        shallow,event2,_=fixture(action,True);problem=evaluate_cycle(action,event2,shallow,**context)
        self.assertEqual(problem['status'],'issue',problem);self.assertEqual(event,original,'候选质量不能修改已确认计数事件')
        frames[10]['framing']['landmarks'][11]['valid']=False
        self.assertEqual(evaluate_cycle(action,event,frames,**context)['status'],'unassessable')
        self.assertEqual(evaluate_cycle(action,event,shallow,**{**context,'view':'front'})['status'],'unsupported')
        self.assertEqual(evaluate_cycle(action,event,shallow,**{**context,'variant_confirmed':False})['status'],'unsupported')
        self.assertIsNone(evaluate_cycle(action,None,shallow,**context),'正常过渡没有完整事件，不应判幅度不足')

    def test_squat_bottom_range(self):self.check_action('bodyweight_squat')
    def test_push_up_bottom_range(self):self.check_action('standard_push_up')
    def test_reverse_lunge_bound_to_rear_side(self):self.check_action('reverse_lunge')
    def test_bilateral_bridge_top_range(self):self.check_action('bodyweight_glute_bridge')
    def test_sit_up_top_range(self):self.check_action('bent_knee_sit_up')
    def test_curl_per_side_excursion(self):self.check_action('standing_dumbbell_curl')

    def test_missing_definition_cannot_default_to_normal(self):
        frames,event,ctx=fixture('bodyweight_squat');ctx['rule']=None
        result=evaluate_cycle('bodyweight_squat',event,frames,**ctx)
        self.assertEqual(result['status'],'unassessable');self.assertEqual(result['reason'],'label_definition_or_threshold_missing')

    def test_curl_side_mismatch_or_far_arm_loss_cannot_copy_labels(self):
        action='standing_dumbbell_curl';frames,event,ctx=fixture(action)
        for f in frames:f['framing']['landmarks'][16]['presence']=0
        self.assertEqual(evaluate_cycle(action,event,frames,**ctx)['status'],'not_observed')
        self.assertEqual(evaluate_cycle(action,{**event,'side':'right'},frames,**ctx)['reason'],'event_side_mismatch')
        self.assertEqual(evaluate_cycle(action,{**event,'side':'right'},frames,**{**ctx,'measurement_side':'right'})['status'],'unassessable')

    def test_unknown_gap_duplicate_and_still_pose_are_not_extreme_evidence(self):
        action='bodyweight_squat';frames,event,ctx=fixture(action)
        self.assertEqual(evaluate_cycle(action,event,frames[:8]+frames[12:],**ctx)['reason'],'cycle_evidence_gap')
        self.assertEqual(evaluate_cycle(action,event,frames[:8]+[frames[7]]+frames[8:],**ctx)['reason'],'non_monotonic_cycle_evidence')
        for i in range(len(frames)):
            points=copy.deepcopy(frames[0]['framing']['landmarks']);frames[i]['framing']['landmarks']=points
        self.assertEqual(evaluate_cycle(action,event,frames,**ctx)['reason'],'extreme_not_bracketed_by_cycle')

    def test_wrong_schema_or_action_parameters_are_rejected(self):
        frames,event,ctx=fixture('bodyweight_squat')
        with self.assertRaises(ValueError):replace(ctx['rule'],feature_schema_version='world-coordinates')
        with self.assertRaises(ValueError):replace(ctx['rule'],threshold=float('nan'))
        with self.assertRaises(ValueError):evaluate_cycle('bodyweight_squat',event,frames,**{**ctx,'rule':replace(ctx['rule'],code='curl.range')})

    def test_bridge_selects_highest_hip_not_the_straightest_transition(self):
        frames,event,ctx=fixture('bodyweight_glute_bridge')
        result=evaluate_cycle('bodyweight_glute_bridge',event,frames,**ctx)
        self.assertEqual((result['start_ms'],result['end_ms']),(900,1200))
        self.assertAlmostEqual(result['value'],155.7,delta=.2)

    def test_unlabelled_input_can_report_geometry_but_not_normality(self):
        frames,event,ctx=fixture('standing_dumbbell_curl')
        result=evaluate_cycle('standing_dumbbell_curl',event,frames,**{**ctx,'rule':replace(ctx['rule'],threshold=None)})
        self.assertAlmostEqual(result['value'],90.)
        self.assertEqual(result['status'],'unassessable');self.assertEqual(result['reason'],'quality_threshold_unavailable')
        self.assertFalse(result['publishable'])

    def test_curl_isolated_extension_spike_cannot_inflate_range(self):
        action='standing_dumbbell_curl';frames,event,ctx=fixture(action)
        for i,level in enumerate(LEVELS):
            frames[i]=arm_geometry(action,179 if i==2 else 160-30*level,seq=i);frames[i]['captured_ms']=i*100
        result=evaluate_cycle(action,event,frames,**{**ctx,'rule':replace(ctx['rule'],threshold=40.)})
        self.assertEqual(result['status'],'issue');self.assertAlmostEqual(result['value'],30.)


if __name__=='__main__':unittest.main()
