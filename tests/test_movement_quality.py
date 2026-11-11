"""16专属D-POSE检查；全部阈值仅作已知几何比较，不进入正式训练配置。"""
import copy
from dataclasses import replace
import unittest

from fitness_app.movement_quality import PlankHipRule,DynamicRangeRule,evaluate_plank,evaluate_dynamic
from test_plank_hold import geometry as plank_frame
from test_jumping_jack import geometry as jack_frame
from test_high_knees import sequence as knee_frames

LEVELS=[0]*5+[.2,.4,.6,.8,1,1,1,1,.8,.6,.4,.2]+[0]*5


def plank(frames,support=None,rule=None,up=(0,-1),view='side'):
    return evaluate_plank(frames,[{'start_ms':0,'end_ms':1000}] if support is None else support,measurement_side='left',view=view,
        orientation_up=up,orientation_confirmed=True,rule=rule or PlankHipRule(.1,.1,300,250,'D-POSE-hip','Known geometry only'))


def jack(arm=1,leg=1):
    frames=[]
    for i,v in enumerate(LEVELS):
        f=jack_frame(v*arm,v*leg,seq=i,step_ms=50);p=f['framing']['landmarks']
        for side in (0,1):
            p[25+side].update(x=(p[23+side]['x']+p[27+side]['x'])/2,y=(p[23+side]['y']+p[27+side]['y'])/2)
        frames.append(f)
    return frames


def dynamic(code,frames,side='none',threshold=None):
    limits={'jumping_jack.arm_range':120.,'jumping_jack.leg_range':1.1,'high_knees.height':-.1}
    rule=DynamicRangeRule(code,limits[code] if threshold is None else threshold,6. if code.endswith('arm_range') else .025,50,200,'D-POSE-dynamic','Known geometry only')
    event={'side':side,'start_ms':0,'end_ms':frames[-1]['captured_ms'],'assessments':[]}
    return evaluate_dynamic(event,frames,frames[0],view='front',variant_confirmed=True,orientation_up=(0,-1),orientation_confirmed=True,rule=rule)


class MovementQualityTests(unittest.TestCase):
    def test_plank_high_low_are_exclusive_and_do_not_change_hold(self):
        for y,issue in ((.25,'plank.hip_high'),(.58,'plank.hip_low'),(.487719298,None)):
            result=plank([plank_frame(i,hip_y=y) for i in range(11)])
            self.assertEqual(result['hold_ms'],1000);self.assertFalse(result['publishable'])
            for code,items in result['intervals'].items():
                self.assertEqual({x['status'] for x in items},{'issue' if code==issue else 'not_observed'})

    def test_plank_mirror_facing_and_rotated_up_keep_high_low_semantics(self):
        frames=[plank_frame(i,hip_y=.25) for i in range(11)];original=plank(frames)
        for transform,up in ((lambda x,y:(1-x,y),(0,-1)),(lambda x,y:(1-y,x),(1,0))):
            changed=copy.deepcopy(frames)
            for f in changed:
                for p in f['framing']['landmarks']:p['x'],p['y']=transform(p['x'],p['y'])
            result=plank(changed,up=up)
            self.assertEqual(result['intervals'],original['intervals'])
            self.assertAlmostEqual(result['observations'][5]['hip_signed_body_ratio'],original['observations'][5]['hip_signed_body_ratio'])

    def test_plank_missing_and_unknown_gap_do_not_bridge_quality(self):
        frames=[plank_frame(i,hip_y=.25) for i in range(11)];frames[5]['framing']['landmarks'][23]['valid']=False
        result=plank(frames);self.assertEqual(result['hold_ms'],1000)
        self.assertTrue(any(i['status']=='unassessable' and i['start_ms']<=500<=i['end_ms'] for i in result['intervals']['plank.hip_high']))
        gap=plank(frames[:3]+frames[8:])
        self.assertTrue(any(i['reason']=='quality_source_gap' for i in gap['intervals']['plank.hip_high']))

    def test_plank_no_support_duplicate_support_and_transient(self):
        frames=[plank_frame(i,hip_y=.25 if i in (4,5) else .487719298) for i in range(11)]
        self.assertEqual(plank(frames,support=[])['intervals']['plank.hip_high'],[])
        support=[{'start_ms':0,'end_ms':1000}]*2;result=plank(frames,support=support)
        self.assertEqual(result['hold_ms'],1000);self.assertFalse(any(i['status']=='issue' for i in result['intervals']['plank.hip_high']))
        self.assertIn('quality_duration_unconfirmed',{i['reason'] for i in result['intervals']['plank.hip_high']})

    def test_plank_degenerate_scale_wrong_view_and_missing_threshold(self):
        frames=[plank_frame(i) for i in range(11)]
        for f in frames:f['framing']['landmarks'][27].update(x=.35,y=.4)
        self.assertTrue(all(i['status']=='unassessable' for i in plank(frames)['intervals']['plank.hip_high']))
        frames=[plank_frame(i) for i in range(11)]
        self.assertEqual(plank(frames,view='front')['intervals']['plank.hip_high'][0]['reason'],'view_or_orientation_unconfirmed')
        result=plank(frames,rule=PlankHipRule(None,None,300,250,'observe','No quality labels'))
        self.assertEqual(result['intervals']['plank.hip_high'][0]['reason'],'quality_threshold_unavailable')

    def test_jack_arms_at_open_phase_and_wrist_loss(self):
        full=dynamic('jumping_jack.arm_range',jack());self.assertEqual(full['status'],'not_observed');self.assertAlmostEqual(full['value'],140.)
        self.assertEqual(dynamic('jumping_jack.arm_range',jack(arm=.5))['status'],'issue')
        frames=jack();frames[10]['framing']['landmarks'][15]['valid']=False
        self.assertEqual(dynamic('jumping_jack.arm_range',frames)['status'],'unassessable')
        self.assertEqual(dynamic('jumping_jack.leg_range',frames)['status'],'not_observed')

    def test_jack_leg_range_independent_of_arms_and_count(self):
        self.assertEqual(dynamic('jumping_jack.leg_range',jack())['status'],'not_observed')
        self.assertEqual(dynamic('jumping_jack.leg_range',jack(leg=.4))['status'],'issue')
        frames=jack();frames[10]['framing']['landmarks'][27]['valid']=False
        self.assertEqual(dynamic('jumping_jack.leg_range',frames)['status'],'unassessable')
        self.assertEqual(dynamic('jumping_jack.arm_range',frames)['status'],'not_observed')

    def test_high_knees_peak_uses_start_leg_scale_and_target_side(self):
        for side in ('left','right'):
            result=dynamic('high_knees.height',knee_frames(side),side)
            self.assertEqual(result['status'],'not_observed',result);self.assertAlmostEqual(result['value'],-.02/.42)
            self.assertEqual(dynamic('high_knees.height',knee_frames(side,amplitude=.25),side)['status'],'issue')
        self.assertEqual(dynamic('high_knees.height',knee_frames('right'),'left')['reason'],'extreme_not_bracketed_by_cycle')

    def test_high_knees_other_leg_missing_not_copied_and_side_swap_rejected(self):
        frames=knee_frames('left')
        for f in frames:f['framing']['landmarks'][26]['valid']=False;f['framing']['landmarks'][28]['valid']=False
        self.assertEqual(dynamic('high_knees.height',frames,'left')['status'],'not_observed')
        frames=knee_frames('left')
        for i in (11,23,25,27):frames[10]['framing']['landmarks'][i],frames[10]['framing']['landmarks'][i+1]=frames[10]['framing']['landmarks'][i+1],frames[10]['framing']['landmarks'][i]
        self.assertEqual(dynamic('high_knees.height',frames,'left')['status'],'unassessable')

    def test_dynamic_transition_unknown_direction_and_no_label_do_not_become_normal(self):
        frames=jack();rule=DynamicRangeRule('jumping_jack.arm_range',None,6.,50,200,'observe','No labels')
        ctx=dict(view='front',variant_confirmed=True,orientation_up=(0,-1),orientation_confirmed=True,rule=rule)
        self.assertIsNone(evaluate_dynamic(None,frames,frames[0],**ctx))
        event={'side':'none','start_ms':0,'end_ms':frames[-1]['captured_ms']}
        self.assertEqual(evaluate_dynamic(event,frames,frames[0],**ctx)['reason'],'quality_threshold_unavailable')
        self.assertEqual(evaluate_dynamic(event,frames,frames[0],**{**ctx,'orientation_confirmed':False})['reason'],'image_orientation_unknown')
        self.assertEqual(dynamic('jumping_jack.arm_range',[jack_frame(0,seq=i) for i in range(20)])['reason'],'extreme_not_bracketed_by_cycle')

    def test_missing_leg_reference_does_not_disable_observable_arms(self):
        frames=jack();event={'side':'none','start_ms':0,'end_ms':frames[-1]['captured_ms']}
        ctx=dict(view='front',variant_confirmed=True,orientation_up=(0,-1),orientation_confirmed=True)
        arm=DynamicRangeRule('jumping_jack.arm_range',120.,6.,50,200,'D-POSE','Known geometry')
        self.assertEqual(evaluate_dynamic(event,frames,None,**ctx,rule=arm)['status'],'not_observed')
        leg=DynamicRangeRule('jumping_jack.leg_range',1.1,.025,50,200,'D-POSE','Known geometry')
        self.assertEqual(evaluate_dynamic(event,frames,None,**ctx,rule=leg)['reason'],'start_reference_missing')

    def test_observable_small_cycles_keep_count_when_candidate_range_flags_issue(self):
        from test_jumping_jack import consume as count_jack
        from test_high_knees import consume as count_knee
        for code,frames,counter,threshold,band in (
            ('jumping_jack.leg_range',jack(leg=.4),count_jack,1.1,.025),
            ('high_knees.height',knee_frames('left',amplitude=.25),count_knee,-.1,.025)):
            events,_=counter(frames);self.assertEqual(len(events),1);before=copy.deepcopy(events)
            result=evaluate_dynamic(events[0],frames,frames[0],view='front',variant_confirmed=True,orientation_up=(0,-1),orientation_confirmed=True,
                rule=DynamicRangeRule(code,threshold,band,50,200,'D-POSE','Known geometry'))
            self.assertEqual(result['status'],'issue',result);self.assertEqual(events,before)


if __name__=='__main__':unittest.main()
