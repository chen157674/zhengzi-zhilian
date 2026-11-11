"""冻结探针的输入身份与缓存标签边界；不重复运行已通过视觉推理。"""
import json,unittest
from fitness_app.paths import ROOT
from scripts.probe_new_actions import validate_plan,validate_cached_row


class ProbeSourcePlanTests(unittest.TestCase):
    def entry(self):
        return {'id':'commons-window-a','source_sha256':'a'*64,'action_id':'dumbbell_bench_press','start_ms':1000,'end_ms':4200,'crop':None,
            'partition':'train','group':'commons-author:fixture','path':'data/datasets/development-movements/commons-gantner-dumbbell-bench.webm'}
    def test_original_plan_stays_valid_without_running_models(self):
        validate_plan(json.loads((ROOT/'data/verification/round3/recognition-probe-inputs.json').read_text(encoding='utf-8')))
    def test_path_ids_windows_crops_and_source_flags_reject_before_inference(self):
        entry=self.entry();validate_plan({'entries':[entry]})
        for change in ({'id':'../other'},{'source_sha256':'not-a-hash'},{'source_sha256':None},{'start_ms':True},{'end_ms':1200},{'crop':[0,0,float('nan'),1]},
                       {'crop':[.8,0,.2,1]},{'evaluation_only':True},{'exclude_from_training':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_plan({'entries':[{**entry,**change}]})
        with self.assertRaises(ValueError):validate_plan({'entries':[entry,entry]})
    def test_cached_prediction_cannot_silently_keep_changed_label_partition_or_creator(self):
        entry=self.entry();row={k:entry[k] for k in ('id','source_sha256','partition','group')}|{'expected':entry['action_id']}
        validate_cached_row(entry,row)
        for change in ({'expected':'incline_dumbbell_press'},{'partition':'diagnostic'},{'group':'different-author'},{'source_sha256':'b'*64}):
            with self.assertRaises(ValueError):validate_cached_row(entry,{**row,**change})


if __name__=='__main__':unittest.main()
