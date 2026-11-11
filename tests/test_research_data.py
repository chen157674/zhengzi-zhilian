"""11-A：固定清单、人物关联与来源粒度，不重解码全量视频。"""
import csv
import copy
import unittest
from collections import Counter
from fitness_app.research_data import DATA_ROOT,load_dataset,plan_rows,split_snapshot


class ResearchDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples=load_dataset()
        with (DATA_ROOT/'index.csv').open(encoding='utf-8-sig',newline='') as stream:cls.rows=list(csv.DictReader(stream))

    def test_fixed_real_counts_folds_and_quarantine(self):
        self.assertEqual(len(self.samples),565)
        accepted=[sample for sample in self.samples if sample.partition!='excluded'];self.assertEqual(len(accepted),555)
        counts=Counter((sample.action_id,sample.partition) for sample in accepted)
        for action,expected in [('bodyweight_squat',(96,24,22)),('standard_push_up',(92,22,24)),('standing_dumbbell_curl',(92,24,22))]:
            self.assertEqual(tuple(counts[action,part] for part in ('train','validation','test')),expected)
        deadlift=[sample for sample in accepted if sample.action_id=='conventional_deadlift']
        self.assertEqual(Counter(sample.partition for sample in deadlift),{'development':97,'test':40})
        self.assertEqual({sample.subject_group for sample in deadlift if sample.partition=='test'},{'S04','S07'})
        self.assertEqual({sample.label for sample in deadlift if sample.validation_fold==1},{'GB','PB'})
        self.assertTrue(all(sample.label is None for sample in self.samples if sample.partition=='excluded'))
        self.assertEqual(split_snapshot(self.samples)['split_sha256'],'ac2d0119d172f722cf4c9c174d24bf4bb7ce6119e70ae1b0dfb954675fc96db6')

    def test_conflicts_duplicate_sources_and_unsafe_paths_refused(self):
        original=next(row for row in self.rows if row['source_labels']=='good')
        wrong=copy.deepcopy(original);wrong['source_labels']='good|bad'
        with self.assertRaisesRegex(ValueError,'冲突标签'):plan_rows([wrong])
        with self.assertRaisesRegex(ValueError,'重复 sample key'):plan_rows([original,copy.deepcopy(original)])
        for path in ('../original.mp4','C:/private.mp4'):
            wrong={**original,'path':path}
            with self.assertRaises(ValueError):plan_rows([wrong])
        wrong={**original,'subject_group':'subject_017','source_subjects':'subject_003'}
        with self.assertRaisesRegex(ValueError,'原始人物'):plan_rows([wrong])
        wrong={**original,'source_split':'test'}
        self.assertEqual(plan_rows([wrong])[0].partition,plan_rows([original])[0].partition,'来源 split 不覆盖固定分组')


if __name__=='__main__':unittest.main()
