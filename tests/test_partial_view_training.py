"""新局部视野训练不会挪用评估人群或缩小原分母；不重新拟合模型。"""
import json,unittest
from fitness_app.paths import ROOT
from scripts.train_partial_view_fusion import merge_cases


class PartialViewTrainingTests(unittest.TestCase):
    def test_frozen_scaler_matches_library_float32_contract_without_mutating_source(self):
        import numpy as np
        from sklearn.preprocessing import StandardScaler
        from scripts.partial_action_training import standardize_frozen
        rng=np.random.default_rng(20261024);x=(rng.normal(size=(17,32))*123.456+21.789).astype(np.float32);before=x.copy();scaler=StandardScaler().fit(x[:12])
        np.testing.assert_array_equal(standardize_frozen(x,scaler.mean_,scaler.scale_),scaler.transform(x));np.testing.assert_array_equal(x,before)
        for mean,scale in ((scaler.mean_[:-1],scaler.scale_),(scaler.mean_,np.zeros(32))):
            with self.assertRaises(ValueError):standardize_frozen(x,mean,scale)
    def inputs(self):
        out=ROOT/'data/verification/round3';old=ROOT/'data/verification/step-22'
        read=lambda p:json.loads(p.read_text(encoding='utf-8'))
        return [read(out/'partial-label-fusion-training.json'),read(old/'semantic-video-features.json'),read(out/'recognition-probe-inputs.json'),
                read(out/'new-source-action-probe.json'),read(out/'partial-view-training-inputs.json'),read(out/'partial-view-action-probe.json')]
    def test_all_original_evaluation_inputs_and_old_training_kept(self):
        rows,_=merge_cases(*self.inputs())
        self.assertEqual(sum(r['partition']=='train' for r in rows),630)
        self.assertEqual(sum(r['partition']=='validation' for r in rows),195)
        self.assertEqual(sum(r['partition']=='diagnostic' for r in rows),9)
        self.assertTrue(all(r['group']=='wger-author:Goulart' for r in rows if r['partition']=='diagnostic'))
    def test_extra_rows_cannot_become_new_diagnostics_or_leak_existing_evaluation_creator(self):
        for change in ({'partition':'diagnostic'},{'group':'wger-author:Goulart'}):
            inputs=self.inputs();inputs[4]['entries'][0].update(change)
            observed=next(r for r in inputs[5]['rows'] if r['id']==inputs[4]['entries'][0]['id']);observed.update(change)
            with self.assertRaises(ValueError):merge_cases(*inputs)
    def test_cached_source_label_drift_cannot_change_training_comparison(self):
        inputs=self.inputs();inputs[4]['entries'][0]['action_id']='dumbbell_chest_fly'
        with self.assertRaises(ValueError):merge_cases(*inputs)
        inputs=self.inputs();inputs[0]['training'][0]['group']='different-source'
        with self.assertRaises(ValueError):merge_cases(*inputs)


if __name__=='__main__':unittest.main()
