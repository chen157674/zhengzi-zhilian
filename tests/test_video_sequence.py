"""连续特征的遮挡、时间与人物边界；不以合成数据证明真实动作准确率。"""
import unittest
import numpy as np
from fitness_app.video_sequence import pose_vector,window


def points():
    result=[{'x':.5,'y':.5,'z':0.,'visibility':0.,'presence':0.} for _ in range(33)]
    for index,x,y in ((11,.4,.3),(12,.6,.3),(13,.35,.45),(14,.65,.45),(15,.3,.6),(16,.7,.6),(23,.43,.65),(24,.57,.65)):
        result[index].update(x=x,y=y,visibility=1.,presence=1.)
    return result


class VideoSequenceTests(unittest.TestCase):
    def test_coordinate_translation_and_resize_do_not_change_normalized_pose(self):
        original=points();moved=[{**p,'x':p['x']+.05,'y':p['y']+.05} for p in original]
        first,ok=pose_vector(original,(640,480));second,ok2=pose_vector(moved,(1280,960))
        self.assertTrue(ok and ok2);np.testing.assert_allclose(first,second,atol=1e-6)

    def test_upper_body_classification_keeps_hips_missing_and_requires_real_shoulders(self):
        p=points()
        for i in (23,24):p[i]['visibility']=0.
        feature,ok=pose_vector(p,(640,480));self.assertTrue(ok)
        self.assertEqual(feature[-3:].tolist(),[0.,1.,0.]);self.assertEqual(feature[7*4:9*4].tolist(),[0.]*8)
        p[11]['visibility']=0.;self.assertFalse(pose_vector(p,(640,480))[1])
        self.assertFalse(pose_vector(points(),(640,480),person_count=2)[1])

    def arrays(self):
        p=np.array([[v[k] for k in ('x','y','z','visibility','presence')] for v in points()],np.float32)
        a={'timestamps':np.arange(40)*100,'points':np.repeat(p[None],40,axis=0),'sizes':np.tile((640,480),(40,1)),
           'people':np.ones(40,int),'features':np.full((40,512),np.nan,np.float32)}
        for i in range(0,40,5):a['features'][i]=i+1
        return a

    def test_window_preserves_time_order_and_never_uses_future_image(self):
        a=self.arrays();value,reason=window(a,3100);self.assertIsNone(reason)
        self.assertEqual(value[0].shape,(32,513));self.assertEqual(value[1].shape,(32,55))
        self.assertEqual(value[0][0,0],1);self.assertEqual(value[0][-1,0],31)
        a['features'][35]=999
        np.testing.assert_array_equal(window(a,3100)[0][0],value[0])

    def test_long_missing_image_and_multiple_people_are_rejected(self):
        a=self.arrays();a['features'][10:]=np.nan
        self.assertEqual(window(a,3100)[1],'sequence_image_missing')
        a=self.arrays();a['people'][:20]=2
        self.assertEqual(window(a,3100)[1],'sequence_target_unavailable')

    def test_non_integer_video_frame_rate_jitter_is_not_a_person_gap(self):
        a=self.arrays();a['timestamps']=np.round(np.arange(40)*1000/9.99).astype(int)
        self.assertIsNone(window(a,3101)[1])

    def test_visible_body_basis_is_invariant_to_camera_yaw(self):
        p=points();rotated=[];theta=.6
        matrix=np.array([[np.cos(theta),0,np.sin(theta)],[0,1,0],[-np.sin(theta),0,np.cos(theta)]])
        for point in p:
            xyz=np.array([(point['x']-.5)*640,(point['y']-.5)*480,point['z']*640])@matrix.T
            rotated.append({**point,'x':xyz[0]/640+.5,'y':xyz[1]/480+.5,'z':xyz[2]/640})
        original,ok=pose_vector(p,(640,480));changed,ok2=pose_vector(rotated,(640,480))
        self.assertTrue(ok and ok2);self.assertEqual(original[-1],1.)
        np.testing.assert_allclose(original,changed,atol=1e-6)


if __name__=='__main__':unittest.main()
