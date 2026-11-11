import unittest
from fitness_app.research_metrics import evaluate


class MetricTests(unittest.TestCase):
    def rows(self,pairs):return [dict(sample_key=str(index),truth=truth,prediction=pred,subject_group='S01',view='side',reason='occluded' if pred is None else None) for index,(truth,pred) in enumerate(pairs)]

    def test_abstentions_reduce_main_recall_f1_and_coverage(self):
        result=evaluate(self.rows([(1,1),(1,0),(1,None),(0,0),(0,1),(0,None)]));main=result['main']
        self.assertEqual(main['confusion']['counts'],[[1,1,1],[1,1,1]])
        self.assertAlmostEqual(main['positive_precision'],.5);self.assertAlmostEqual(main['positive_recall'],1/3)
        self.assertAlmostEqual(main['macro_f1'],.4);self.assertAlmostEqual(main['coverage'],2/3)
        self.assertAlmostEqual(result['conditional']['macro_f1'],.5);self.assertFalse(result['acceptance_passed'])
        self.assertEqual(main['rejection_reasons'],{'occluded':2})

    def test_no_predictions_no_truth_and_duplicate_denominator(self):
        result=evaluate(self.rows([(1,None),(0,None)]));self.assertIsNone(result['main']['positive_precision']);self.assertEqual(result['main']['positive_recall'],0)
        self.assertEqual(result['main']['macro_f1'],0);self.assertIsNone(result['conditional'])
        self.assertIsNone(evaluate(self.rows([(None,1)]))['main']);self.assertIsNone(evaluate([])['main'])
        row=self.rows([(1,1)])[0]
        with self.assertRaisesRegex(ValueError,'重复'):evaluate([row,row])


if __name__=='__main__':unittest.main()
