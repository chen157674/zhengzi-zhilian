import copy
import unittest
from fitness_app.research_features import frame_angles,aggregate_angles


def geometry():
    points=[dict(x=.5,y=.5,z=0,visibility=1,presence=1) for unused in range(33)]
    for offset in (0,1):
        for index,xy in {11:(.5,.25),13:(.5,.375),15:(.5,.5),23:(.5,.5),25:(.75,.5),27:(.75,.75)}.items():points[index+offset].update(x=xy[0],y=xy[1])
    return {'width':200,'height':100,'poses':[points],'identity_accepted':True}


class FeatureTests(unittest.TestCase):
    def test_geometry_pixel_aspect_columns_and_no_metadata_answer(self):
        frame=geometry();row=frame_angles('bodyweight_squat',frame);self.assertEqual(row['left'],(90.,90.,0.))
        result=aggregate_angles('bodyweight_squat',[row]*3);self.assertEqual(len(result['features']),15);self.assertEqual(result['features'][:5],[90.,0.,90.,90.,90.])
        changed={**frame,'path':'bad/S07.mp4','label':'bad','person':'test'};self.assertEqual(frame_angles('bodyweight_squat',changed),row)
        curl=frame_angles('standing_dumbbell_curl',frame);self.assertEqual(curl['both'],(180.,180.,0.,0.,0.));self.assertEqual(len(aggregate_angles('standing_dumbbell_curl',[curl]*3)['features']),25)
        frame['poses'][0][25].update(x=.625,y=.75)
        # 实际宽高还原后两个分量均为 25；直接在归一化平面算会得不同夹角。
        self.assertAlmostEqual(frame_angles('bodyweight_squat',frame)['left'][1],135.)

    def test_missing_side_fixed_coverage_and_degenerate_vectors(self):
        frame=geometry();good=frame_angles('bodyweight_squat',frame);bad=copy.deepcopy(frame);bad['poses'][0][11]['presence']=0
        right_only=frame_angles('bodyweight_squat',bad)
        result=aggregate_angles('bodyweight_squat',[good,right_only,good]);self.assertEqual(result['side'],'right');self.assertEqual(result['coverage'],1.)
        missing={'left':None,'right':None}
        self.assertIsNotNone(aggregate_angles('bodyweight_squat',[good]*7+[missing]*3)['features'])
        self.assertEqual(aggregate_angles('bodyweight_squat',[good]*6+[missing]*4)['rejection_reason'],'INSUFFICIENT_VALID_COVERAGE')
        self.assertEqual(aggregate_angles('bodyweight_squat',[good]*2)['rejection_reason'],'INSUFFICIENT_VALID_OBSERVATIONS')
        self.assertIsNone(frame_angles('standing_dumbbell_curl',bad)['both'])
        frame['poses'][0][23].update(x=.5,y=.25);self.assertIsNone(frame_angles('bodyweight_squat',frame)['left'])
        frame['poses'].append(copy.deepcopy(frame['poses'][0]));self.assertEqual(frame_angles('bodyweight_squat',frame),missing)


if __name__=='__main__':unittest.main()
