"""分段时间/迟滞/未知覆盖测试；合成窗口不是动作准确率证据。"""
import unittest
from fitness_app.video_segmentation import segment_decisions


def window(start,end,action,model='fixture-v1'):return dict(start_ms=start,end_ms=end,action_id=action,model_version=model)


class SegmentationTests(unittest.TestCase):
    def partition(self,segments,end):
        self.assertEqual(segments[0]['start_ms'],0);self.assertEqual(segments[-1]['end_ms'],end)
        for left,right in zip(segments,segments[1:]):self.assertEqual(left['end_ms'],right['start_ms'])
        self.assertTrue(all(s['end_ms']>s['start_ms'] for s in segments))

    def test_short_static_plank_is_an_action_not_rest(self):
        data=segment_decisions([window(0,2800,'forearm_plank')],3000)
        self.assertEqual(len(data),1);self.assertEqual(data[0]['action_id'],'forearm_plank');self.partition(data,3000)

    def test_action_switch_requires_two_windows_and_uses_first_candidate_midpoint(self):
        data=segment_decisions([window(0,2800,'barbell_overhead_press'),window(1600,4400,'seated_cable_row'),
            window(3200,6000,'barbell_overhead_press'),window(4800,7600,'conventional_deadlift'),window(6400,9200,'conventional_deadlift')],9600)
        self.assertEqual([s['action_id'] for s in data],['barbell_overhead_press','conventional_deadlift'])
        self.assertEqual(data[1]['start_ms'],6200);self.partition(data,9600)

    def test_long_unknown_gap_separates_repeated_same_action_short_gap_only_affects_coverage(self):
        data=segment_decisions([window(0,2800,'hammer_curl'),window(1600,4400,'hammer_curl'),window(3200,6000,'hammer_curl')],6500,
            [{'start_ms':1600,'end_ms':1700,'reason':'missing_parts'},{'start_ms':3000,'end_ms':4100,'reason':'target_unavailable'}])
        self.assertEqual([s['action_id'] for s in data],['hammer_curl',None,'hammer_curl'])
        self.assertEqual(data[0]['unassessable_ranges'],[{'start_ms':1600,'end_ms':1700,'reason':'missing_parts'}]);self.partition(data,6500)

    def test_unrecognized_beginning_and_long_tail_are_not_silently_filled(self):
        data=segment_decisions([window(0,2800,None),window(1600,4400,'cable_face_pull')],8000)
        self.assertEqual([s['action_id'] for s in data],[None,'cable_face_pull',None]);self.assertEqual(data[1]['start_ms'],1600)
        self.assertEqual(data[-1]['reason'],'tail_without_recognition');self.partition(data,8000)

    def test_overlapping_invalid_spans_merge_without_double_partitioning(self):
        data=segment_decisions([window(0,2800,'barbell_bench_press')],4000,
            [{'start_ms':1000,'end_ms':2000,'reason':'source_gap'},{'start_ms':1500,'end_ms':2500,'reason':'target_changed'}])
        self.assertEqual([(x['start_ms'],x['end_ms']) for x in data],[(0,1000),(1000,2500),(2500,4000)]);self.partition(data,4000)
        unknown=segment_decisions([],500);self.assertEqual(unknown[0]['kind'],'unknown');self.assertIsNone(unknown[0]['action_id'])

    def test_malformed_ranges_unknown_classes_and_mixed_models_are_rejected(self):
        cases=[[window(100,100,'hammer_curl')],[window(0,2800,'not_a_class')],
            [window(0,2800,'hammer_curl'),window(1000,2600,'hammer_curl')],
            [window(0,2800,'hammer_curl'),window(1600,4400,'hammer_curl','other-version')]]
        for values in cases:
            with self.subTest(values=values),self.assertRaises(ValueError):segment_decisions(values,5000)

    def test_transient_unknown_does_not_fragment_action_but_withholds_posture_evidence(self):
        values=segment_decisions([window(0,2800,'cable_face_pull'),window(1600,4400,None),window(3200,6000,'cable_face_pull')],6200)
        self.assertEqual(len(values),1);self.assertEqual(values[0]['action_id'],'cable_face_pull')
        self.assertEqual(values[0]['recognition_unassessable_ranges'],[{'start_ms':1600,'end_ms':4400,'reason':'action_unconfirmed'}])

    def test_identity_change_always_splits_and_conflicting_window_suppresses_old_cue(self):
        data=segment_decisions([window(0,2800,'hammer_curl'),window(1600,4400,'standing_dumbbell_curl'),window(3200,6000,'hammer_curl')],6200,
            [{'start_ms':3000,'end_ms':3100,'reason':'target_changed'}])
        self.assertEqual([s['action_id'] for s in data],['hammer_curl',None,'hammer_curl'])
        self.assertTrue(data[0]['recognition_unassessable_ranges']);self.partition(data,6200)

    def test_nonoverlapping_new_exercise_windows_do_not_drop_their_initial_motion(self):
        data=segment_decisions([window(0,2800,'barbell_overhead_press'),window(1200,4400,'barbell_overhead_press'),
            window(6067,8868,'barbell_bent_over_row'),window(7267,10468,'barbell_bent_over_row')],12000)
        self.assertEqual(data[1]['start_ms'],6067);self.assertEqual(data[0]['end_ms'],6067);self.partition(data,12000)


if __name__=='__main__':unittest.main()
