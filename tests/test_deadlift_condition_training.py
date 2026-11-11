"""GB/PB监督范围、人物边界及拒判计分；不是圆背准确率验收。"""
import unittest
from types import SimpleNamespace
import numpy as np
from scripts.train_deadlift_condition import cases_for,condition_metrics,decision,release_gate,window_observability


class DeadliftConditionTrainingTests(unittest.TestCase):
    def sample(self,**changes):
        return SimpleNamespace(**({'dataset_id':'w5prmmxyt9','sha256':'source','partition':'development','annotation_status':'consistent_source_label','quality_use':'source_GB_PB_only',
            'label':'PB','path':'mydeadlift/example.mp4','subject_group':'S01','validation_fold':0}|changes))
    def entry(self,**changes):
        return {'id':'input','source_sha256':'source','path':'data/datasets/mydeadlift/example.mp4','action_id':'conventional_deadlift','group':'w5prmmxyt9:S01','crop':None}|changes

    def test_label_is_from_index_and_filename_or_action_output_cannot_replace_it(self):
        cases=cases_for([self.entry()],[self.sample()]);self.assertEqual(cases[0]['source_label'],'PB')
        self.assertNotIn('extension',cases[0]['source_label'])
        with self.assertRaises(ValueError):cases_for([self.entry(action_id='barbell_bent_over_row')],[self.sample()])

    def test_test_or_conflicting_sources_and_wrong_subject_or_crop_refuse(self):
        for change in ({'partition':'test'},{'annotation_status':'excluded_conflicting_source_labels'},{'quality_use':'coarse_good_bad_only'},{'validation_fold':3}):
            with self.assertRaises(ValueError):cases_for([self.entry()],[self.sample(**change)])
        for change in ({'group':'w5prmmxyt9:S02'},{'crop':[0,0,.5,.5]},{'source_sha256':'other'}):
            with self.assertRaises(ValueError):cases_for([self.entry(**change)],[self.sample()])
        with self.assertRaises(ValueError):cases_for([self.entry(),self.entry(id='duplicate')],[self.sample()])

    def test_rejections_count_as_misses_and_good_class_does_not_imply_complete_correctness(self):
        rows=[{'source_label':'PB','accepted':None},{'source_label':'PB','accepted':'PB'},{'source_label':'GB','accepted':'PB'}]
        result=condition_metrics(rows);self.assertEqual(result['clips'],3);self.assertEqual(result['correct'],1)
        self.assertEqual(result['per_condition']['PB']['recall_including_rejections'],.5);self.assertEqual(result['per_condition']['PB']['precision'],.5)
        self.assertEqual(result['per_condition']['GB']['recall_including_rejections'],0)

    def test_probability_gate_rejects_uncertainty_and_invalid_values(self):
        self.assertIsNone(decision(np.asarray([.6,.4]),['GB','PB'])[1]);self.assertEqual(decision(np.asarray([.1,.9]),['GB','PB'])[1],'PB')
        for value in (np.asarray([float('nan'),.3]),np.asarray([1.1,-.1]),np.asarray([.2,.2])):
            with self.assertRaises(ValueError):decision(value,['GB','PB'])

    def test_aggregate_score_cannot_hide_a_failed_person_fold_or_enable_runtime(self):
        good=condition_metrics([{'source_label':v,'accepted':v} for v in ('GB','PB')]);bad=condition_metrics([{'source_label':'GB','accepted':'GB'},{'source_label':'PB','accepted':None}])
        gate=release_gate(good,[{'fold':0,'metrics':bad}]);self.assertFalse(gate['development_thresholds_passed']);self.assertFalse(gate['runtime_enabled'])
        passing=release_gate(good,[{'fold':0,'metrics':good}]);self.assertTrue(passing['development_thresholds_passed']);self.assertFalse(passing['release_gate_passed'])

    def test_unknown_conditions_cannot_silently_enter_metrics(self):
        for row in ({'source_label':'other','accepted':'PB'},{'source_label':'GB','accepted':'unknown'}):
            with self.assertRaises(ValueError):condition_metrics([row])

    def test_window_stage_is_label_independent_and_cannot_fill_hidden_or_changed_cache(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        times=np.arange(8)*200+1000;points=np.full((8,33,5),np.nan)
        for offset in (0,1):
            for joint,xy in ((11,(.2,.3)),(23,(.5,.5)),(25,(.5,.8))):points[:,joint+offset]=[*xy,0.,.9,.9]
        pose={'timestamps':times,'points':points,'people':np.ones(8),'sizes':np.tile([640,480],(8,1))}
        case={**self.entry(),'cache':'data/verification/round3/condition-toy-semantic.npz','source_label':'PB','source_subject':'S01'}
        identities=[{'id':case['id'],'semantic_cache_sha256':'cache','pose_cache_sha256':'cache'}]
        def load(path,**kwargs):return nullcontext(pose if str(path).endswith('full-world-features.npz') else {'timestamps':times})
        with patch('scripts.train_deadlift_condition.sha',return_value='cache'),patch('scripts.train_deadlift_condition.np.load',side_effect=load):
            first=window_observability([case],identities)['rows'][0]
            other=window_observability([{**case,'source_label':'GB'}],identities)['rows'][0]
            self.assertEqual(first['state'],'hinge_stage_visible');self.assertEqual(first['hinge_frames'],other['hinge_frames'])
            points[:,:,3:]=0.;hidden=window_observability([case],identities)['rows'][0]
            self.assertEqual(hidden['state'],'insufficient_joint_coverage');self.assertEqual(hidden['matched_joint_frames'],0)
            with self.assertRaises(ValueError):window_observability([case],[{**identities[0],'pose_cache_sha256':'changed'}])


if __name__=='__main__':unittest.main()
