"""结构化云端观察绑定真实抽样，不测试合成健身准确率。"""
import copy
import unittest
from fitness_app.ai_context import validate_answer,messages_for


def bundle():
    return {'visual_form_review':True,'text':'这个动作有问题吗？','read_scope':{},
        'facts':{'visual_input':{'frame_count':3,'frames':[{'source_ms':v,'sha256':str(i)*64}
            for i,v in enumerate((2200,2600,3000),1)]},'evidence':[],'knowledge':[],
            'selected_records':[],'history':None,'comparison':None,'live':None}}


def answer():
    return {'summary':'画面中躯干向一侧偏斜。','explanations':[],'next_focus':[],'limitations':[],
        'visual_findings':[{'code':'side_lean','status':'observed','frame_indices':[1,3],
            'observation':'肩部中点相对髋部中点向一侧偏移。'}]}


class VisualReviewTests(unittest.TestCase):
    def test_source_times_come_only_from_actual_input_and_keep_cloud_provenance(self):
        raw=answer();original=copy.deepcopy(raw);result=validate_answer(raw,bundle())
        self.assertEqual(raw,original)
        item=result['visual_review']['findings'][0]
        self.assertEqual(item['sample_span_ms'],[2200,3000])
        self.assertEqual(item['samples'][1]['sha256'],'3'*64)
        self.assertEqual(item['time_scope'],'sampled_frames_only_not_continuous_error_interval')
        self.assertFalse(item['local_detector_confirmed'])
        self.assertEqual(item['side'],'none');self.assertTrue(item['suggestion'])
        self.assertIsNone(result['visual_review']['whole_video_correctness'])

    def test_invalid_frames_numbers_sides_codes_or_fake_time_cannot_be_accepted(self):
        changes=[{'frame_indices':[0]},{'frame_indices':[4]},{'frame_indices':[True]},
            {'frame_indices':[2,1]},{'frame_indices':[1,1]},{'frame_indices':[]},
            {'code':'muscle_force'},{'status':'correct'},
            {'source_ms':2400},{'observation':'身体向左侧倾斜。'},
            {'observation':'躯干偏斜20度。'},{'observation':'保证安全，可以增加负重。'}]
        for change in changes:
            raw=answer();raw['visual_findings'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):validate_answer(raw,bundle())
        raw=answer();raw['visual_findings']*=2
        with self.assertRaises(ValueError):validate_answer(raw,bundle())

    def test_missing_structured_reply_and_unassessable_are_never_normal(self):
        raw=answer();raw.pop('visual_findings')
        review=validate_answer(raw,bundle())['visual_review']
        self.assertEqual(review['state'],'unassessable');self.assertEqual(review['findings'],[])
        raw=answer();raw['visual_findings'][0]['status']='unassessable'
        raw['visual_findings'][0]['observation']='肩髋被遮挡，无法判断。'
        item=validate_answer(raw,bundle())['visual_review']['findings'][0]
        self.assertEqual(item['suggestion'],'');self.assertEqual(item['status'],'unassessable')

    def test_no_images_or_scene_only_cannot_create_form_findings(self):
        for changes in ({'visual_form_review':False},{'facts':{**bundle()['facts'],'visual_input':None}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                validate_answer(answer(),{**bundle(),**changes})
        request=bundle();request['reference']={}
        system=messages_for(request)[0]['content']
        self.assertIn('visual_findings',system);self.assertIn('frame_indices',system)


if __name__=='__main__':unittest.main()
