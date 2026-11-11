"""标量运动摘要的可比性、覆盖和抗异常；不作为真实动作精度证明。"""
import copy,math,unittest
from fitness_app.movement_stability import Collector,report


def frame(stamp,asymmetry=.02,still=False,scale=1.):
    phase=0 if still else stamp/1500*2*math.pi
    points=[{'x':.5,'y':.5,'z':0.,'visibility':.99,'presence':.99,'valid':True} for _ in range(33)]
    for i,x,y in [(11,.4,.3),(12,.6,.3),(13,.3,.35+.10*math.cos(phase)),(14,.7,.35+.10*math.cos(phase)),
        (15,.25,.2+.15*math.sin(phase)),(16,.75,.2+.15*math.sin(phase)+asymmetry*.3),
        (23,.45,.6),(24,.55,.6),(25,.45,.8),(26,.55,.8),(27,.45,.95),(28,.55,.95)]:
        points[i].update(x=.5+(x-.5)*scale,y=.5+(y-.5)*scale)
    return {'captured_ms':stamp,'frame_seq':stamp//50,'width':640,'height':480,'framing':{'usable':True,'person_count':1,'landmarks':points}}


def output(view='front',basis='estimated'):
    return {'observation':{'posture':{'view':view,'view_basis':basis,'items':[{'status':'not_observed'}]}}}


def windows(transform=None):
    collector=Collector('dumbbell_shoulder_press');values=[]
    for stamp in range(0,18001,100):
        f=frame(stamp,.02 if stamp<9000 else .20);o=output();group=1;epoch='same'
        if transform:f,o,group,epoch=transform(stamp,f,o,group,epoch)
        values+=collector.push(f,o,group,epoch)
    values+=collector.finish();return values


class StabilityMetricsTests(unittest.TestCase):
    def test_equal_length_observations_report_bilateral_change_without_fatigue_or_score(self):
        values=windows();result=next(r for r in report(values) if r['metric']=='wrist_difference')
        self.assertEqual(len(values),6);self.assertEqual(result['state'],'compared');self.assertEqual(result['direction'],'increase');self.assertEqual(result['before']['samples'],3)
        self.assertNotIn('疲劳',result['message']);self.assertNotIn('score',result)
    def test_static_preparation_is_not_claimed_as_stable_exercise(self):
        def change(t,f,o,g,e):return frame(t,still=True),o,g,e
        values=windows(change);self.assertTrue(all(m['status']=='unassessable' for v in values for m in v['metrics']))
        self.assertTrue(all(v['state']=='insufficient_evidence' for v in report(values)))
    def test_missing_person_and_unobserved_time_lower_coverage(self):
        def change(t,f,o,g,e):
            if 9000<=t<15000:f['framing']['person_count']=2
            return f,o,g,e
        result=next(r for r in report(windows(change)) if r['metric']=='wrist_difference');self.assertEqual(result['state'],'insufficient_evidence')
    def test_view_source_or_scale_change_prevents_comparison(self):
        for mode in ('view','basis','scale'):
            def change(t,f,o,g,e):
                if t>=9000:
                    if mode=='view':o=output('oblique')
                    elif mode=='basis':o=output(basis='user_setting')
                    else:f=frame(t,scale=1.4)
                return f,o,g,e
            result=next(r for r in report(windows(change)) if r['metric']=='trunk_range' and r['side']=='left')
            self.assertEqual(result['state'],'not_comparable',mode)
    def test_groups_and_new_streams_do_not_pool_small_samples(self):
        for mode in ('group','stream'):
            def change(t,f,o,g,e):return f,o,2 if mode=='group' and t>=9000 else g,'new' if mode=='stream' and t>=9000 else e
            self.assertFalse(any(r['state']=='compared' for r in report(windows(change))))
    def test_single_outlier_does_not_make_a_trend_but_raw_range_remains(self):
        values=windows()
        for i,w in enumerate(values):
            for m in w['metrics']:
                if m['metric']=='wrist_difference':m['value']=99. if i==0 else .05
        result=next(r for r in report(values) if r['metric']=='wrist_difference');self.assertEqual(result['direction'],'no_clear_change');self.assertEqual(result['before']['maximum'],99.)
    def test_complete_cycles_and_reference_intervals_remain_distinct(self):
        collector=Collector('standard_push_up');values=[]
        for t in range(0,6001,50):
            o=output('side')
            if t and t%1000==0:o['events']=[{'start_ms':t-1000,'end_ms':t,'side':'none'}]
            values+=collector.push(frame(t),o)
        self.assertEqual(len(values),6);self.assertTrue(all(v['kind']=='cycle' for v in values));self.assertTrue(any(r['state']=='compared' for r in report(values)))
        for i in range(3):values[i]['kind']='reference_interval'
        self.assertFalse(any(r['state']=='compared' for r in report(values)))
    def test_tail_and_sparse_sampling_do_not_create_valid_windows(self):
        collector=Collector('dumbbell_shoulder_press');values=[]
        for t in range(0,4001,600):values+=collector.push(frame(t),output())
        values+=collector.finish();self.assertTrue(all(m['status']!='observed' for v in values for m in v['metrics']))

    def test_alternating_sides_are_compared_separately_without_treating_other_side_as_missing(self):
        collector=Collector('standing_dumbbell_curl');values=[]
        for t in range(0,12001,50):
            o=output('side')
            if t and t%1000==0:o['events']=[{'start_ms':t-1000,'end_ms':t,'side':'left' if t//1000%2 else 'right'}]
            values+=collector.push(frame(t),o)
        compared=[r for r in report(values) if r['state']=='compared'];self.assertEqual({r['side'] for r in compared},{'left','right'})

    def test_plank_support_must_be_evaluable_for_coverage(self):
        collector=Collector('forearm_plank');values=[]
        for t in range(0,3001,100):
            o=output('side')
            if t<2700:o['observation']['posture']['items']=[{'status':'unassessable'}]
            values+=collector.push(frame(t,still=True),o)
        self.assertTrue(all(m['status']=='unassessable' for w in values for m in w['metrics']))

    def test_aspect_or_pose_model_switch_is_not_hidden_by_equal_scale_statistics(self):
        for change in ('aspect','model'):
            values=windows()
            for w in values[3:]:
                if change=='aspect':w['aspect_ratio']=.75
                else:w['pose_models']=['changed']
            if change=='model':
                for w in values[:3]:w['pose_models']=['original']
            self.assertFalse(any(v['state']=='compared' for v in report(values)))

    def test_reference_unknown_intervals_are_not_dropped_and_wrong_range_view_is_rejected(self):
        collector=Collector('high_knees');values=[]
        for t in range(0,3001,100):
            o=output('side')
            if t and t%1000==0:o['posture_findings']=[{'start_ms':t-1000,'end_ms':t,'side':'left','status':'unassessable'}]
            values+=collector.push(frame(t),o)
        self.assertEqual(len(values),3);self.assertTrue(all(m['reason']=='unsupported_view' for w in values for m in w['metrics']))


if __name__=='__main__':unittest.main()
