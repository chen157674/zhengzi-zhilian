"""下肢显示必须唯一关联原主体，不跨人、不跨帧、不修改原判项。"""
import copy
import unittest
from fitness_app.locked_lower_pose import matching_target
from fitness_app.skeleton import skeleton_snapshot
from fitness_app.frame_pipeline import framing


def source_points():
    points = [{'x': .5, 'y': .5, 'z': 0., 'visibility': .95, 'presence': .95} for _ in range(33)]
    for i, xy in {11:(.4,.3),12:(.6,.3),23:(.43,.6),24:(.57,.6)}.items():
        points[i].update(x=xy[0], y=xy[1])
    return points


def body(source, offset=0., score=.95):
    points = [{'x': .5+offset, 'y': .5, 'score': score, 'coordinate_valid': True} for _ in range(17)]
    for a, b in [(5,11),(6,12),(11,23),(12,24)]:
        points[a].update(x=source[b]['x']+offset,y=source[b]['y'])
    for i,xy in {13:(.43,.75),14:(.57,.75),15:(.43,.93),16:(.57,.93)}.items():
        points[i].update(x=xy[0]+offset,y=xy[1])
    return {'keypoints': points, 'model_sha256': 'fixture'}


class LockedLowerTests(unittest.TestCase):
    def test_higher_score_bystander_does_not_win(self):
        source=source_points();target=body(source,score=.8);other=body(source,offset=.3,score=1.)
        selected,reason=matching_target([other,target],source,1000,1000)
        self.assertIs(selected,target);self.assertIsNone(reason)
        self.assertEqual(matching_target([other],source,1000,1000)[1],'target_mismatch')

    def test_two_overlapping_matches_are_ambiguous(self):
        source=source_points()
        self.assertEqual(matching_target([body(source),body(source,.01)],source,1000,1000)[1],'target_ambiguous')

    def test_lost_source_anchor_and_side_swap_reject(self):
        source=source_points();target=body(source)
        source[23]['visibility']=.2
        self.assertEqual(matching_target([target],source,1000,1000)[1],'source_anchor_unavailable')
        source=source_points();target=body(source)
        target['keypoints'][5],target['keypoints'][6]=target['keypoints'][6],target['keypoints'][5]
        self.assertEqual(matching_target([target],source,1000,1000)[1],'target_mismatch')

    def test_same_frame_supplement_not_stale_and_primary_data_preserved(self):
        source=source_points()
        pose={'poses':[source]}
        q=framing('bodyweight_squat',pose)
        primary=body(source)
        primary['keypoints'][14]['score']=.2
        frame={'captured_ms':1000,'frame_seq':1,'width':1000,'height':1000,'framing':q,
               'pretrained':{'status':'observed','timestamp_ms':1000,'body':primary,
                            'source_landmarks':source,'lower_display':{
                            'status':'observed','timestamp_ms':1000,'body':body(source),
                            'target_basis':'unique_same_frame_anatomical_shoulders_and_hips'}}}
        before=copy.deepcopy(frame)
        value=skeleton_snapshot(frame)
        self.assertTrue({25,26,27,28}<={p['index'] for p in value['joints']})
        self.assertIn('locked-yolo-lower-v1',value['model_version'])
        self.assertEqual(frame,before)
        frame['pretrained']['lower_display']['timestamp_ms']=999
        self.assertFalse({26,28}&{p['index'] for p in skeleton_snapshot(frame)['joints']})
        frame=copy.deepcopy(before)
        frame['pretrained']['lower_display']['body']=body(source,.3)
        self.assertFalse({26,28}&{p['index'] for p in skeleton_snapshot(frame)['joints']})
        frame=before;frame['framing']['person_count']=2
        self.assertEqual(skeleton_snapshot(frame)['reason'],'target_ambiguous')

if __name__=='__main__':unittest.main()
