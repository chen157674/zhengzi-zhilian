import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fitness_app.paths import ROOT
from fitness_app.counting_resources import DEFAULT_CONFIG,read_counting_manifest,implementation_signature
from fitness_app.frame_pipeline import REQUIRED


class CountingResourceTests(unittest.TestCase):
    def test_unrelated_action_framing_is_not_a_four_action_dependency(self):
        expected='count-1-800d0110077c'
        self.assertEqual(read_counting_manifest()['capability_version'],expected)
        with patch.dict(REQUIRED,{'high_knees':[[11,12,23,24,25,27]]}):
            self.assertEqual(read_counting_manifest()['capability_version'],expected)
        with patch.dict(REQUIRED,{'bodyweight_squat':[[11,23]]}):
            with self.assertRaisesRegex(ValueError,'实现与冻结'):read_counting_manifest()

    def test_development_gate_and_ready_resource_identity(self):
        source=json.loads(DEFAULT_CONFIG.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(prefix='counting-resource-',dir=ROOT/'.tmp/tests') as directory:
            path=Path(directory)/'counting.json'
            def write(value):path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
            draft={**source,'status':'development'};write(draft)
            with self.assertRaisesRegex(ValueError,'尚未冻结'):read_counting_manifest(path)
            self.assertEqual(len(read_counting_manifest(path,allow_development=True)['actions']),4)
            ready={**source,'status':'ready','implementation':implementation_signature()};write(ready)
            self.assertTrue(read_counting_manifest(path)['capability_version'].startswith('count-1-'))
            wrong=copy.deepcopy(ready);wrong['actions']['bodyweight_squat']['cycle']['exit_delta']+=1;write(wrong)
            with self.assertRaisesRegex(ValueError,'参数摘要'):read_counting_manifest(path)
            wrong={**ready,'calibration_sha256':'0'*64};write(wrong)
            with self.assertRaisesRegex(ValueError,'诊断摘要'):read_counting_manifest(path)
            wrong={**ready,'implementation':{}};write(wrong)
            with self.assertRaisesRegex(ValueError,'实现与冻结'):read_counting_manifest(path)


if __name__=='__main__':unittest.main()
