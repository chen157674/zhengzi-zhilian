"""新离线同步窗口/单次姿态缓存与实际MP4范围读取；不评估合成动作准确率。"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from fitness_app.paths import ROOT
from fitness_app.offline_video import scan_video
from fitness_app.media import video_frames,probe_video
from fitness_app.auth import APIError
import test_formal_media as mediafixtures


class Pose:
    def __init__(self):self.calls=0;self.closed=False;self.multiple_at=None
    def detect(self,rgb,stamp):
        self.calls+=1
        points=[dict(x=.5,y=.6,z=0.,visibility=.99,presence=.99) for _ in range(33)]
        for index,x,y in [(11,.4,.2),(12,.6,.2),(23,.45,.5),(24,.55,.5),(25,.45,.7),(26,.55,.7),(27,.45,.9),(28,.55,.9)]:points[index].update(x=x,y=y)
        return {'poses':[points,points] if self.multiple_at and self.multiple_at[0]<=stamp<self.multiple_at[1] else [points],
                'world_poses':[points],'model':'synthetic-pose'}
    def close(self):self.closed=True


class Predictor:
    def __init__(self):self.calls=[];self.change_model=False
    def predict(self,images,timestamps,arrays):
        self.calls.append((len(images),list(map(int,timestamps)),arrays['points'].shape))
        assert len(images)==8 and np.all(np.diff(timestamps)>0) and len(arrays['timestamps'])>8
        return {'action_id':'hammer_curl','model_version':'changed' if self.change_model and len(self.calls)>1 else 'fixture-v1','score':.9,'decision_basis':'fixture'}


class OfflineVideoTests(unittest.TestCase):
    def setUp(self):self.pose=Pose();self.predictor=Predictor();self.frames_read=[]
    def frames(self,path,info,*,start_ms=0,end_ms=None):
        for stamp in range(start_ms,end_ms or info['duration_ms'],40):
            self.frames_read.append(stamp);yield stamp,np.full((48,64,3),180,np.uint8)
    def scan(self,**options):
        return scan_video('fixture-path',{'duration_ms':9000},pose_factory=lambda **kw:self.pose,
            predictor_provider=lambda:self.predictor,frames=self.frames,**options)
    def test_video_windows_are_synchronous_and_posture_reuses_numeric_cache(self):
        result=self.scan();self.assertEqual(result.pose_calls,225);self.assertGreater(len(result.decisions),1)
        calls=self.pose.calls
        for index,row in enumerate(result.rows):
            frame=row.frame('hammer_curl',index);self.assertEqual(frame['captured_ms'],row.stamp)
            self.assertEqual(frame['framing']['person_count'],1)
        self.assertEqual(self.pose.calls,calls);self.assertTrue(self.pose.closed);self.assertEqual(result.end_ms,9000)
        self.assertTrue(all(w['model_version']=='fixture-v1' for w in result.decisions));result.clear();self.assertEqual(result.rows,[])
    def test_manual_range_skips_video_classifier_and_reads_only_requested_part(self):
        result=self.scan(automatic=False,start_ms=2000,end_ms=4000)
        self.assertEqual(self.frames_read,list(range(2000,4000,40)));self.assertEqual(self.predictor.calls,[])
        self.assertEqual(result.end_ms,4000);self.assertEqual(result.rows[0].stamp,2000)
    def test_multiple_people_are_cached_as_unassessable_and_reset_recognition(self):
        self.pose.multiple_at=(3200,4200);result=self.scan()
        self.assertTrue(any(g['start_ms']==3200 and g['end_ms']==4200 for g in result.unassessable))
        multi=next(row for row in result.rows if row.stamp==3200).frame('hammer_curl',80)
        self.assertFalse(multi['identity_accepted']);self.assertEqual(multi['framing']['code'],'TARGET_AMBIGUOUS')
    def test_cancel_and_changed_model_close_resources_without_returning_success(self):
        with self.assertRaises(APIError) as got:self.scan(cancelled=lambda:self.pose.calls>=12)
        self.assertEqual(got.exception.code,'ANALYSIS_CANCELLED');self.assertTrue(self.pose.closed)
        self.pose=Pose();self.predictor.change_model=True
        with self.assertRaises(APIError) as got:self.scan()
        self.assertEqual(got.exception.code,'MODEL_CHANGED');self.assertTrue(self.pose.closed)
    def test_actual_h264_range_is_half_open_and_seek_does_not_emit_neighbor_frames(self):
        base=ROOT/'.tmp/tests'
        with TemporaryDirectory(dir=base,prefix='video-range-') as directory:
            path=Path(directory)/'input.mp4';path.write_bytes(mediafixtures.sample_mp4(frames=24));info=probe_video(path)
            rows=list(video_frames(path,info,start_ms=800,end_ms=1400))
            self.assertEqual([stamp for stamp,rgb in rows],[800,900,1000,1100,1200,1300])
            self.assertTrue(all(rgb.shape==(48,64,3) for _,rgb in rows))
            with self.assertRaises(APIError):list(video_frames(path,info,start_ms=1400,end_ms=800))


if __name__=='__main__':unittest.main()
