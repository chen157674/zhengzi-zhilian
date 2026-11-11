"""旧健身域输出复用需保持原片、窗口、裁剪、作者和实际取帧，不启动GPU。"""
import unittest
from fitness_app.domain_video_adviser import reusable_prediction,DomainVideoAdviser


class DomainAdviserTests(unittest.TestCase):
    def test_unreleased_checkpoint_cannot_be_loaded_by_default_runtime_constructor(self):
        for manifest in ({'runtime_enabled':False},{'runtime_enabled':True,'development_gate_passed':False,'release_gate_passed':True},
                         {'runtime_enabled':True,'development_gate_passed':True,'release_gate_passed':False}):
            with self.subTest(manifest=manifest),self.assertRaises(RuntimeError):DomainVideoAdviser({},manifest)
    def fixtures(self):
        entry={'id':'source-window','source_sha256':'source','start_ms':0,'end_ms':5000,'crop':None,'group':'creator','partition':'validation'}
        old={**entry};prediction={'id':entry['id'],'source_group':'creator','top':'dumbbell_bench_press','score':.9,'margin':.7}
        return entry,old,prediction,list(range(1000,1800,100))
    def test_unchanged_source_and_frame_contract_can_reuse(self):
        row,old,p,t=self.fixtures();self.assertTrue(reusable_prediction(row,old,p,t,t))
    def test_source_crop_time_creator_and_old_training_output_cannot_be_reused(self):
        row,old,p,t=self.fixtures()
        for change in ({'source_sha256':'other'},{'crop':[0,0,.5,1]},{'start_ms':1},{'group':'other'},{'partition':'train'}):
            with self.subTest(change=change):self.assertFalse(reusable_prediction(row,{**old,**change},p,t,t))
        self.assertFalse(reusable_prediction(row,old,p,[v+33 for v in t],t))
    def test_bad_confidence_or_wrong_cached_owner_cannot_be_reused(self):
        row,old,p,t=self.fixtures()
        for change in ({'score':float('nan')},{'margin':True},{'source_group':'other'},{'id':'other'}):
            with self.subTest(change=change):self.assertFalse(reusable_prediction(row,old,{**p,**change},t,t))


if __name__=='__main__':unittest.main()
