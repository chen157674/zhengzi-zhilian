"""实际图像留边与高置信点的边界；不将这组规则当作通用遮挡识别。"""
import unittest
import copy
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch
import numpy as np

from fitness_app.content_bounds import detect_content_bounds,validate_content_bounds
from fitness_app.frame_pipeline import framing,REQUIRED
from fitness_app.paths import ROOT
from fitness_app.counting_resources import read_input_validation


def points():
    result=[{'x':.5,'y':.5,'z':0.,'visibility':.99,'presence':.99} for unused in range(33)]
    for index in (27,28,29,30,31,32):result[index]['y']=.95
    return result


class ContentBoundsTests(unittest.TestCase):
    def test_evidence_cache_is_scoped_to_the_actual_case_dependencies(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from audit_content_bounds import case_current
        report=json.loads((ROOT/'data/verification/step-14/content-bounds-validation.json').read_text(encoding='utf-8'))
        key=next(k for k,v in report['cases'].items() if v['action_id']=='bodyweight_squat');case=report['cases'][key]
        config=json.loads((ROOT/'data/models/cycle-counting.json').read_text(encoding='utf-8'))['actions']['bodyweight_squat']
        self.assertTrue(key.startswith('data/datasets/'));self.assertTrue(case_current(case,key,'bodyweight_squat',case['source_sha256'],config))
        with patch.dict(REQUIRED,{'high_knees':[[11,12,23,24,25,27]]}):self.assertTrue(case_current(case,key,'bodyweight_squat',case['source_sha256'],config))
        changed=copy.deepcopy(config);changed['cycle']['minimum_excursion']+=1
        self.assertFalse(case_current(case,key,'bodyweight_squat',case['source_sha256'],changed))
        with patch.dict(REQUIRED,{'bodyweight_squat':[[11,23]]}):self.assertFalse(case_current(case,key,'bodyweight_squat',case['source_sha256'],config))

    def test_evidence_cannot_use_framing_only_or_zero_output_as_counter_validation(self):
        path=ROOT/'data/verification/step-14/content-bounds-validation.json';report=json.loads(path.read_text(encoding='utf-8'))
        document=json.loads((ROOT/'data/models/cycle-counting.json').read_text(encoding='utf-8'));original=Path.read_bytes
        for missing_counter in (True,False):
            altered=copy.deepcopy(report)
            if missing_counter:altered['actions']['bodyweight_squat']['counter_cases']=0
            else:altered['actions']['bodyweight_squat'].update(events=0,hold_ms=0)
            raw=json.dumps(altered).encode();doc={**document,'input_validation_sha256':hashlib.sha256(raw).hexdigest()}
            with patch.object(Path,'read_bytes',lambda p:raw if p==path else original(p)),self.assertRaisesRegex(ValueError,'实际动作完成量'):
                read_input_validation(doc,'four_actions',report['resource_implementations']['four_actions'])

    def test_opposed_letterbox_and_pillarbox_preserve_original_coordinate_space(self):
        image=np.full((100,160,3),150,dtype=np.uint8);image[:10]=16;image[-10:]=16
        self.assertEqual(detect_content_bounds(image),[0.,.1,1.,.9])
        image[:, :16]=16;image[:, -16:]=16
        self.assertEqual(detect_content_bounds(image),[.1,.1,.9,.9])

    def test_high_confidence_in_padding_is_not_a_valid_body_part(self):
        raw=points();old=framing('barbell_overhead_press',{'poses':[raw]});self.assertTrue(old['usable'])
        current=framing('barbell_overhead_press',{'poses':[raw],'content_bounds':[0.,.1,1.,.9]})
        self.assertEqual(current['code'],'MISSING_PARTS');self.assertFalse(current['landmarks'][31]['valid'])
        self.assertNotIn('x',current['landmarks'][31]);self.assertEqual(raw[31]['visibility'],.99)
        for i in (27,28,29,30,31,32):raw[i]['y']=.8
        self.assertTrue(framing('barbell_overhead_press',{'poses':[raw],'content_bounds':[0.,.1,1.,.9]})['usable'])

    def test_dark_background_single_shadow_and_full_solid_frame_are_not_trimmed(self):
        dark=np.zeros((100,160,3),dtype=np.uint8);dark[20:80,65:95]=150
        self.assertEqual(detect_content_bounds(dark),[0.,0.,1.,1.])
        single=np.full((100,160,3),150,dtype=np.uint8);single[:10]=16
        self.assertEqual(detect_content_bounds(single),[0.,0.,1.,1.])
        self.assertEqual(detect_content_bounds(np.full((100,160,3),16,dtype=np.uint8)),[0.,0.,1.,1.])
        asymmetric=np.full((100,160,3),150,dtype=np.uint8);asymmetric[:10]=16;asymmetric[-20:]=16
        self.assertEqual(detect_content_bounds(asymmetric),[0.,0.,1.,1.])

    def test_compression_noise_does_not_turn_padding_into_visible_image(self):
        rng=np.random.default_rng(2);image=np.full((100,160,3),150,dtype=np.uint8)
        image[:10]=rng.integers(11,22,size=(10,160,3),dtype=np.uint8);image[-10:]=rng.integers(11,22,size=(10,160,3),dtype=np.uint8)
        self.assertEqual(detect_content_bounds(image),[0.,.1,1.,.9])

    def test_malformed_bounds_fail_and_full_image_remains_backward_compatible(self):
        for value in ([0,0,1,0],[0,0,2,1],[0,0,float('nan'),1],None):
            with self.subTest(value=value),self.assertRaises(ValueError):validate_content_bounds(value)
        raw=points();default=framing('bodyweight_squat',{'poses':[raw]});explicit=framing('bodyweight_squat',{'poses':[raw],'content_bounds':[0.,0.,1.,1.]})
        self.assertEqual(default,explicit)


if __name__=='__main__':unittest.main()
