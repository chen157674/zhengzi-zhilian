"""只检查新时序特征及人物隔离，不重跑旧训练。"""
from types import SimpleNamespace
import unittest
import numpy as np
from fitness_app.deadlift_temporal import trajectory_features,fit_fold,require_development


class TemporalTests(unittest.TestCase):
    def test_temporal_order_is_retained_with_same_unordered_statistics(self):
        stamps=np.arange(12)*100
        rows=np.column_stack((np.linspace(60,175,12),np.linspace(90,170,12),np.linspace(80,10,12)))
        forward,reason,summary=trajectory_features(stamps,rows)
        backward,_,_=trajectory_features(stamps,rows[::-1])
        self.assertIsNone(reason);self.assertEqual(summary['dimension'],189)
        self.assertFalse(np.allclose(forward,backward))

    def test_long_gaps_and_nonmonotonic_time_are_not_silently_interpolated(self):
        times=np.arange(20)*100;values=np.ones((20,3))*90;values[7:10]=np.nan
        self.assertEqual(trajectory_features(times,values)[1],'ANGLE_EVIDENCE_GAP')
        with self.assertRaises(ValueError):trajectory_features(times[::-1],values)
        self.assertEqual(trajectory_features([],np.empty((0,3)))[1],'INSUFFICIENT_ANGLE_COVERAGE')

    def test_test_partition_and_shared_subjects_never_fit(self):
        def sample(key,group,label='GB',partition='development'):
            return SimpleNamespace(key=key,subject_group=group,label=label,partition=partition,action_id='conventional_deadlift')
        with self.assertRaises(ValueError):require_development([sample('heldout','test',partition='test')])
        with self.assertRaises(ValueError):fit_fold([sample('a','person')],[sample('b','person')],{})
        train=[sample('a','one'),sample('b','two','PB')];validation=[sample('c','three')]
        features={'a':{'features':[0.,0.]},'b':{'features':[2.,2.]},'c':{'features':[1000.,1000.]}}
        _,record=fit_fold(train,validation,features)
        self.assertEqual(record['scaler_mean'],[1.,1.])
        self.assertEqual(record['actual_fit_keys'],['a','b'])


if __name__=='__main__':unittest.main()
