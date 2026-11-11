"""时序候选的缺失／范围／数值契约；这些合成输入不作为独立健身准确率。"""
import copy
import unittest
import numpy as np
from fitness_app.temporal_posture import TemporalKneeCandidate,temporal_features


def sequence():
    generator=np.random.default_rng(92)
    base=generator.uniform(.2,.8,(1,33,3)).astype(np.float32)
    drift=np.sin(np.linspace(0,2*np.pi,40)).astype(np.float32)[:,None,None]
    return base+drift*generator.normal(0,.01,(1,33,3)).astype(np.float32)


class TemporalCandidateTests(unittest.TestCase):
    def test_translation_and_uniform_scale_do_not_change_compatible_features(self):
        points=sequence()
        before=temporal_features(points)
        after=temporal_features(points*1.7+np.array([.2,-.3,.1],np.float32))
        self.assertEqual(before.shape,(100,222))
        self.assertTrue(np.allclose(before,after,rtol=2e-4,atol=2e-4))

    def test_missing_points_are_rejected_instead_of_interpolated_as_evidence(self):
        points=sequence();points[5,25,0]=np.nan
        with self.assertRaises(ValueError):temporal_features(points)
        model=TemporalKneeCandidate()
        result=model.observe(points,np.arange(40)*33,np.ones((40,33)),
            action_id='barbell_overhead_press',phase_complete=True)
        self.assertEqual(result['status'],'unassessable')
        self.assertIsNone(model.arrays)

    def test_scope_phase_visibility_and_sparse_sampling_are_checked_before_loading(self):
        model=TemporalKneeCandidate();points=sequence();scores=np.ones((40,33));times=np.arange(40)*33
        for changes,reason in [
            ({'action_id':'lat_pulldown'},'unsupported_exercise'),
            ({'phase_complete':False},'awaiting_complete_cycle'),
            ({'single_person':False},'target_not_unique'),
            ({'timestamps':np.arange(40)*150},'sampling_rate_not_validated'),
            ({'timestamps':np.arange(40)*600},'source_gap')]:
            args={'landmarks':points,'timestamps':times,'visibility':scores,
                'action_id':'barbell_overhead_press','phase_complete':True}
            args.update(changes)
            with self.subTest(reason=reason):
                result=model.observe(**args);self.assertEqual(result['reason'],reason);self.assertIsNone(model.arrays)
        hidden=scores.copy();hidden[:,25]=.1
        self.assertEqual(model.observe(points,times,hidden,action_id='barbell_overhead_press',phase_complete=True)['reason'],'critical_joints_not_visible')

    def test_changed_weight_identity_is_not_loaded(self):
        model=TemporalKneeCandidate();model.weight=copy.deepcopy(model.weight);model.weight['sha256']='0'*64
        with self.assertRaises(ValueError):model.load()
        self.assertIsNone(model.arrays)


if __name__=='__main__':unittest.main()
