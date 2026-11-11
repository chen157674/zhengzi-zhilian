import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from fitness_app.research_data import Sample
from fitness_app.research_extract import extract
from fitness_app.paths import ROOT


class TestGate(unittest.TestCase):
    def test_test_partition_rejected_before_any_video_or_model_access(self):
        sample=Sample('test','a'*64,'must-not-open.mp4',1,'bodyweight_squat','good','subject_003',('subject_003',),('side',),'test',None,'consistent_source_label','coarse_good_bad_only')
        with tempfile.TemporaryDirectory(prefix='research-gate-',dir=ROOT/'.tmp/tests') as directory,patch('fitness_app.research_extract.OUTPUT',Path(directory)),patch('fitness_app.research_extract.PoseService',side_effect=AssertionError('不应访问模型')):
            with self.assertRaisesRegex(ValueError,'未冻结'):extract(sample,{})
            with self.assertRaisesRegex(ValueError,'冻结记录'):extract(sample,{},allow_test=True)


if __name__=='__main__':unittest.main()
