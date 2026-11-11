from dataclasses import replace
import unittest
import numpy as np
from fitness_app.research_data import Sample
from fitness_app.research_training import fit_method,predict_method,METHODS,choose


class FitTests(unittest.TestCase):
    def sample(self,index,label,partition='train'):
        return Sample('test',str(index)*64,'unused.mp4',1,'bodyweight_squat',label,str(index),(str(index),),('side',),partition,None,'consistent_source_label','coarse_good_bad_only')

    def test_scaler_fit_only_training_and_rejections_stay_in_evaluation(self):
        samples=[self.sample(1,'good'),self.sample(2,'bad'),self.sample(3,'bad','validation'),self.sample(4,'good','validation')]
        features={sample.key:{'features':[value,value+1]} for sample,value in zip(samples,[0.,2.,1000.,2000.])}
        features[samples[3].key]={'features':None,'rejection_reason':'INSUFFICIENT_VALID_COVERAGE'}
        model,record=fit_method(METHODS[1],samples[:2],features)
        self.assertTrue(np.allclose(record['scaler_mean'],[1.,2.]));self.assertEqual(record['actual_fit_keys'],[sample.key for sample in samples[:2]])
        rows=predict_method(model,samples[2:],features);self.assertEqual(len(rows),2);self.assertIsNone(rows[1]['prediction']);self.assertEqual(rows[1]['truth'],0)
        with self.assertRaisesRegex(ValueError,'测试'):fit_method(METHODS[1],[replace(samples[0],partition='test'),samples[1]],features)

    def test_failed_fit_cannot_be_selected_as_primary(self):
        self.assertIsNone(choose([{'status':'failed','metrics':{'main':{'macro_f1':0}}}]))

    def test_unmet_development_threshold_keeps_reference_without_primary(self):
        candidate={'status':'computed','method':{'id':'svm-c0.1','family':'svm'},'median_prediction_ms':1,
            'metrics':{'main':{'macro_f1':.8,'coverage':1.,'positive_recall':.75,'classes':[{'coverage':1.},{'coverage':1.}]}}}
        self.assertIsNone(choose([candidate]))
        reference=choose([candidate],require_threshold=False)
        self.assertEqual(reference['method_id'],'svm-c0.1');self.assertFalse(reference['development_coverage_recall_met'])


if __name__=='__main__':unittest.main()
