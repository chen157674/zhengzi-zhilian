"""11 的冻结／首测协议；全为小型 D-TRUTH 数值夹具，不读取真实测试视频。"""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fitness_app.paths import ROOT
from fitness_app.research_data import Sample,split_snapshot
from fitness_app.research_extract import atomic_json
from fitness_app.research_features import CONFIG_SHA256
from fitness_app.research_training import development_comparison
from fitness_app.research_freeze import freeze,independent_test


class FreezeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        directory=self.stack.enter_context(tempfile.TemporaryDirectory(prefix='research-freeze-',dir=ROOT/'.tmp/tests'));self.root=Path(directory)
        for module in ('research_training','research_freeze','research_extract'):
            self.stack.enter_context(patch('fitness_app.'+module+'.OUTPUT',self.root))
        self.stack.enter_context(patch('fitness_app.research_data.DATA_ROOT',self.root))
        for module in ('research_training','research_freeze'):self.stack.enter_context(patch('fitness_app.'+module+'.SUPPORTED',{'bodyweight_squat'}))
        self.config={'D-POSE':'no-video-read'};self.stack.enter_context(patch('fitness_app.research_freeze.pose_config',return_value=self.config))
        self.samples=[];self.features={};extractions={}
        for index in range(8):
            label='bad' if index%2 else 'good';part='train' if index<4 else 'validation' if index<6 else 'test'
            raw=f'D-TRUTH {index}'.encode();sha=hashlib.sha256(raw).hexdigest();name=f'source-{index}.fixture';(self.root/name).write_bytes(raw)
            group='fit-'+str(index//2) if part=='train' else part
            sample=Sample('D-TRUTH',sha,name,len(raw),'bodyweight_squat',label,group,(group,),('side',),part,None,'consistent_source_label','coarse_good_bad_only');self.samples.append(sample)
            value=2*(index%2)+.01*index;self.features[sample.key]={'features':[value]*15}
            if part!='test':
                feature_key=f'feature-{index}';pose_key=f'pose-{index}'
                data={'status':'completed','sample_key':sample.key,'feature_key':feature_key,'pose_key':pose_key,'config_sha256':CONFIG_SHA256,
                    'features':[value]*15,'source':{'source_sha256':sha,'bytes':len(raw),'modified_ns':(self.root/name).stat().st_mtime_ns}}
                atomic_json(self.root/'features'/f'{feature_key}.json',data);extractions[sample.key]={'feature_key':feature_key,'pose_key':pose_key}
        self.stack.enter_context(patch('fitness_app.research_freeze.load_dataset',return_value=self.samples))
        self.baseline={'frozen':False,'test_used':False,'pose_config':self.config,'feature_config_sha256':CONFIG_SHA256,
            'split_sha256':split_snapshot(self.samples)['split_sha256'],'extractions':extractions}
        atomic_json(self.root/'baseline.json',self.baseline)
        development_comparison(self.samples,self.baseline)

    def test_changed_features_cannot_freeze_stale_selection(self):
        path=self.root/'features/feature-0.json';data=json.loads(path.read_text(encoding='utf-8'));data['features'][0]=500
        atomic_json(path,data)
        with self.assertRaisesRegex(ValueError,'特征内容发生变化'):freeze()
        self.assertFalse((self.root/'freeze.json').exists())

    def test_preflight_before_test_exposure_and_saved_result_not_retested(self):
        frozen=freeze();artifact=next(item for item in frozen['actions'][0]['methods'] if item['status']=='ready');model=ROOT/artifact['path'];original=model.read_bytes()
        model.write_bytes(original+b'D-FAULT')
        with patch('fitness_app.research_freeze.extract') as extractor:
            with self.assertRaisesRegex(ValueError,'尚未读取独立测试'):independent_test()
            extractor.assert_not_called()
        self.assertFalse(json.loads((self.root/'baseline.json').read_text(encoding='utf-8'))['test_used'])
        model.write_bytes(original)
        seen=[]
        def features(sample,config,allow_test):
            self.assertTrue(json.loads((self.root/'baseline.json').read_text(encoding='utf-8'))['test_used']);self.assertTrue(allow_test)
            self.assertEqual(config,self.config);seen.append(sample.key)
            return {**self.features[sample.key],'status':'completed','cache_hit':False}
        with patch('fitness_app.research_freeze.extract',side_effect=features):
            result=independent_test();second=independent_test()
        self.assertEqual(result,second);self.assertEqual(len(seen),2);self.assertEqual(result['phase'],'independent_test')
        self.assertTrue(all(item['metrics']['main']['samples']==2 for item in result['actions'][0]['methods'] if item['status']=='computed'))


if __name__=='__main__':unittest.main()
