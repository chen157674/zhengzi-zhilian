import unittest
import numpy as np
from fitness_app.temporal_training import temporal_centres,make_views
from scripts.partial_action_training import source_weights


class TemporalTrainingTests(unittest.TestCase):
    def row(self,key='one',span=9000,digest='original-one'):
        start=1000;centre=start+span/2
        return {'id':key,'partition':'train','start_ms':start,'end_ms':start+span,'actual_ms':[round(t) for t in np.linspace(centre-min(3200,span)/2,centre+min(3200,span)/2-100,8)],
                'source_sha256':digest,'group':'publisher','action_id':'cable_face_pull','label_scope':{'allowed_actions':['cable_face_pull']}}
    def resolve(self,row,desired):return [round(t) for t in desired]
    def test_short_video_is_not_padded_and_long_views_stay_inside_original_interval(self):
        self.assertEqual(temporal_centres(1000,3000),(2000.,));self.assertEqual(temporal_centres(1000,10000),(2600,5500.,8400))
        for bounds in ((0,1000),(-1,5000),(5000,1000)):
            with self.assertRaises(ValueError):temporal_centres(*bounds)
        views=make_views([self.row()],self.resolve);self.assertEqual(len(views),3)
        self.assertEqual([v['view_kind'] for v in views],['early','middle','late']);self.assertEqual(views[1]['actual_ms'],self.row()['actual_ms'])
        self.assertTrue(all(v['control_actual_ms']==self.row()['actual_ms'] for v in views));self.assertTrue(all(v['label_scope']==self.row()['label_scope'] for v in views))
    def test_new_views_do_not_reward_same_original_video_more_than_other_sources(self):
        rows=[self.row('long',9000,'a'),self.row('short',2000,'b')];views=make_views(rows,self.resolve);weights=source_weights(views)
        first=sum(w for v,w in zip(views,weights) if v['source_sha256']=='a');second=sum(w for v,w in zip(views,weights) if v['source_sha256']=='b')
        self.assertAlmostEqual(first,second);self.assertEqual(len({v['base_id'] for v in views}),2);self.assertAlmostEqual(weights.sum(),len(views))
    def test_validation_or_frozen_input_is_rejected_before_reading_any_frames(self):
        def forbidden(*args):self.fail('受限来源不应读原帧')
        for change in ({'partition':'validation'},{'evaluation_only':True},{'exclude_from_training':True},{'model_inference_allowed_before_freeze':False}):
            with self.subTest(change=change),self.assertRaises(ValueError):make_views([self.row()|change],forbidden)
    def test_duplicates_future_jump_or_synthetic_repeated_frames_are_rejected(self):
        row=self.row()
        for resolver in (lambda r,d:[round(v)+500 for v in d],lambda r,d:[round(d[0])]*8):
            with self.assertRaises(ValueError):make_views([row],resolver)
        with self.assertRaises(ValueError):make_views([row,row],self.resolve)


if __name__=='__main__':unittest.main()
